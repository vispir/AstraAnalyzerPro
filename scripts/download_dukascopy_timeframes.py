"""
Download Dukascopy symbol data into cached timeframes.

Usage:
    python scripts/download_dukascopy_timeframes.py --symbol XAUUSD --start 2020-01-01 --end 2024-12-31 --timeframes M1 M15 H4
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable

import pandas as pd
import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from astra_v2 import config
from astra_v2.data.dukascopy import (
    _parse_bi5_candles,
    validate_data,
    dukascopy_ohlcv_path,
    dukascopy_m1_chunks_dir,
    dukascopy_resolve_read_path,
)


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


_RESAMPLE_RULES = {
    "M1": "1min",
    "M5": "5min",
    "M15": "15min",
    "H1": "1h",
    "H4": "4h",
}

_SUPPORTED_SYMBOLS = {"XAUUSD", "BTCUSD", "XAGUSD", "EURUSD"}


def _bi5_url(symbol: str, dt: datetime) -> str:
    return (
        f"https://datafeed.dukascopy.com/datafeed/{symbol.upper()}/{dt.year:04d}/"
        f"{(dt.month - 1):02d}/{dt.day:02d}/{dt.hour:02d}h_ticks.bi5"
    )


def _fetch_hour_ticks(symbol: str, dt: datetime) -> pd.DataFrame | None:
    session = requests.Session()
    session.headers["User-Agent"] = "Mozilla/5.0"
    try:
        resp = session.get(_bi5_url(symbol, dt), timeout=15)
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        ticks = _parse_bi5_candles(resp.content, dt)
        if not ticks:
            return None
        df = pd.DataFrame(ticks).set_index("timestamp")
        df.index = pd.to_datetime(df.index, utc=True)
        return df.sort_index()
    except Exception as exc:
        logger.debug(f"Tick fetch failed for {dt.isoformat()}: {exc}")
        return None
    finally:
        session.close()


def _download_ticks(symbol: str, start: str, end: str) -> pd.DataFrame:
    start_dt = datetime.fromisoformat(start).replace(tzinfo=timezone.utc)
    end_dt = datetime.fromisoformat(end).replace(tzinfo=timezone.utc)
    hours: list[datetime] = []
    current = start_dt
    while current < end_dt:
        weekday = current.weekday()
        if not (weekday == 5 or (weekday == 6 and current.hour < 21)):
            hours.append(current)
        current += timedelta(hours=1)

    logger.info(f"Downloading Dukascopy {symbol} ticks for {len(hours):,} trading hours")
    frames_by_hour: dict[datetime, pd.DataFrame] = {}
    completed = 0
    with ThreadPoolExecutor(max_workers=16) as pool:
        future_map = {pool.submit(_fetch_hour_ticks, symbol, dt): dt for dt in hours}
        for future in as_completed(future_map):
            completed += 1
            if completed % 500 == 0:
                logger.info(f"  {completed:,}/{len(hours):,} hours")
            df = future.result()
            if df is not None and not df.empty:
                frames_by_hour[future_map[future]] = df

    if not frames_by_hour:
        raise RuntimeError("No Dukascopy ticks downloaded")

    ordered_frames = [frames_by_hour[dt] for dt in hours if dt in frames_by_hour]
    result = pd.concat(ordered_frames)
    result = result[~result.index.duplicated(keep="first")]
    logger.info(f"Downloaded {len(result):,} ticks")
    return result


def _iter_chunks(start: str, end: str, chunk_months: int) -> Iterable[tuple[str, str]]:
    start_dt = datetime.fromisoformat(start).replace(tzinfo=timezone.utc)
    end_dt = datetime.fromisoformat(end).replace(tzinfo=timezone.utc)
    current = start_dt
    while current < end_dt:
        year = current.year
        month = current.month + chunk_months
        year += (month - 1) // 12
        month = ((month - 1) % 12) + 1
        next_dt = current.replace(year=year, month=month)
        if next_dt > end_dt:
            next_dt = end_dt
        yield current.date().isoformat(), next_dt.date().isoformat()
        current = next_dt


def _resample_ohlcv(ticks: pd.DataFrame, timeframe: str) -> pd.DataFrame:
    rule = _RESAMPLE_RULES[timeframe]
    ohlcv = ticks["price"].resample(rule).ohlc()
    ohlcv["volume"] = ticks["volume"].resample(rule).sum()
    ohlcv = ohlcv.dropna()
    return validate_data(ohlcv)


def _resample_from_bars(source: pd.DataFrame, timeframe: str) -> pd.DataFrame:
    rule = _RESAMPLE_RULES[timeframe]
    ohlcv = pd.DataFrame({
        "open": source["open"].resample(rule).first(),
        "high": source["high"].resample(rule).max(),
        "low": source["low"].resample(rule).min(),
        "close": source["close"].resample(rule).last(),
        "volume": source["volume"].resample(rule).sum(),
    })
    ohlcv = ohlcv[["open", "high", "low", "close", "volume"]].dropna()
    return validate_data(ohlcv)


def _build_m1_cache(
    symbol: str,
    cache_dir: Path,
    start: str,
    end: str,
    force_refresh: bool,
    chunk_months: int,
) -> pd.DataFrame:
    chunk_dir = dukascopy_m1_chunks_dir(cache_dir, symbol)
    chunk_dir.mkdir(parents=True, exist_ok=True)

    chunk_frames: list[pd.DataFrame] = []
    sym_l = symbol.lower()
    for chunk_start, chunk_end in _iter_chunks(start, end, chunk_months):
        chunk_name = f"{sym_l}_m1_{chunk_start}_{chunk_end}.parquet"
        chunk_path = chunk_dir / chunk_name
        legacy_sym = cache_dir / "_m1_chunks" / sym_l / chunk_name
        legacy_flat = cache_dir / "_m1_chunks" / chunk_name
        if chunk_path.exists() and not force_refresh:
            logger.info(f"Loading cached M1 chunk: {chunk_path.name}")
            m1_df = pd.read_parquet(chunk_path)
        elif legacy_sym.exists() and not force_refresh:
            logger.info(f"Loading cached M1 chunk (legacy): {legacy_sym.name}")
            m1_df = pd.read_parquet(legacy_sym)
        elif legacy_flat.exists() and not force_refresh:
            logger.info(f"Loading cached M1 chunk (legacy): {legacy_flat.name}")
            m1_df = pd.read_parquet(legacy_flat)
        else:
            logger.info(f"Building M1 chunk {chunk_start} -> {chunk_end}")
            ticks = _download_ticks(symbol, chunk_start, chunk_end)
            m1_df = _resample_ohlcv(ticks, "M1")
            m1_df.to_parquet(chunk_path)
            logger.info(f"Saved {len(m1_df):,} M1 bars to {chunk_path}")
        chunk_frames.append(m1_df)

    result = pd.concat(chunk_frames).sort_index()
    result = result[~result.index.duplicated(keep="first")]
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Download Dukascopy symbol timeframes")
    parser.add_argument("--symbol", default="XAUUSD", help="Instrument symbol (XAUUSD/BTCUSD/XAGUSD/EURUSD)")
    parser.add_argument("--start", default="2020-01-01", help="Start date")
    parser.add_argument("--end", default="2024-12-31", help="End date")
    parser.add_argument("--timeframes", nargs="+", default=["M1", "H4"], help="Requested timeframes")
    parser.add_argument("--force-refresh", action="store_true", help="Overwrite existing cache files")
    parser.add_argument("--chunk-months", type=int, default=12, help="Chunk size in months for M1 downloads")
    args = parser.parse_args()
    symbol = args.symbol.upper()

    if symbol not in _SUPPORTED_SYMBOLS:
        raise ValueError(f"Unsupported symbol '{symbol}'. Valid options: {', '.join(sorted(_SUPPORTED_SYMBOLS))}")

    cache_dir = Path(config.DUKASCOPY_CACHE_DIR)
    cache_dir.mkdir(parents=True, exist_ok=True)

    requested = [tf.upper() for tf in args.timeframes]
    unsupported = [tf for tf in requested if tf not in _RESAMPLE_RULES]
    if unsupported:
        raise ValueError(f"Unsupported timeframes: {', '.join(unsupported)}")

    missing = []
    for tf in requested:
        existing = dukascopy_resolve_read_path(cache_dir, symbol, tf, args.start, args.end)
        if args.force_refresh or existing is None:
            missing.append(tf)
        else:
            logger.info(f"Cache already exists: {existing}")

    if not missing:
        logger.info("All requested timeframe caches already exist")
        return

    m1_df = None
    if "M1" in missing or "H4" in missing:
        m1_path = dukascopy_ohlcv_path(cache_dir, symbol, "M1", args.start, args.end)
        m1_existing = dukascopy_resolve_read_path(cache_dir, symbol, "M1", args.start, args.end)
        if m1_existing and not args.force_refresh:
            logger.info(f"Loading cached M1: {m1_existing}")
            m1_df = pd.read_parquet(m1_existing)
        else:
            m1_df = _build_m1_cache(
                symbol=symbol,
                cache_dir=cache_dir,
                start=args.start,
                end=args.end,
                force_refresh=args.force_refresh,
                chunk_months=args.chunk_months,
            )
            m1_path.parent.mkdir(parents=True, exist_ok=True)
            m1_df.to_parquet(m1_path)
            logger.info(f"Saved {len(m1_df):,} M1 bars to {m1_path}")
        if "M1" in missing:
            logger.info(f"M1 cache ready: {m1_path}")

    for tf in missing:
        if tf == "M1":
            continue
        if tf == "H4":
            m15_existing = dukascopy_resolve_read_path(
                cache_dir, symbol, "M15", args.start, args.end
            )
            if m15_existing:
                logger.info(f"Building H4 from cached M15: {m15_existing}")
                source = pd.read_parquet(m15_existing)
            elif m1_df is not None:
                source = m1_df
            else:
                raise RuntimeError("Cannot build H4: neither M15 nor M1 cache is available")
            ohlcv = _resample_from_bars(source, tf)
        else:
            if m1_df is None:
                raise RuntimeError(f"Cannot build {tf}: M1 cache is not available")
            ohlcv = _resample_from_bars(m1_df, tf)
        out = dukascopy_ohlcv_path(cache_dir, symbol, tf, args.start, args.end)
        out.parent.mkdir(parents=True, exist_ok=True)
        ohlcv.to_parquet(out)
        logger.info(f"Saved {len(ohlcv):,} {tf} bars to {out}")


if __name__ == "__main__":
    main()
