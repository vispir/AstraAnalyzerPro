"""
Dukascopy M15 XAU/USD historical data downloader and validator.

Data flow:
  HTTP request → decompress → parse OHLCV → validate gaps → cache as parquet

Notes:
  - Dukascopy tick/OHLCV data is in UTC server time
  - Pre-2015 data has quality issues — default start is 2018
  - Weekend gaps (Fri close → Mon open) are normal, do not fill
  - Gaps > 4 hours on weekdays are flagged as suspect
"""

import struct
import logging
import requests
import pandas as pd
import numpy as np
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from astra_v2 import config

logger = logging.getLogger(__name__)


def dukascopy_pair_dir(symbol: str) -> str:
    """Folder label for a pair, e.g. XAUUSD."""
    return symbol.strip().upper()


def dukascopy_ohlcv_path(
    cache_dir: Path,
    symbol: str,
    timeframe: str,
    start: str,
    end: str,
) -> Path:
    """
    Path to merged OHLCV parquet:
      <cache_dir>/m1|m15|h4/<PAIR>/ <pair>_<tf>_<start>_<end>.parquet
    """
    sym = dukascopy_pair_dir(symbol)
    sym_l = sym.lower()
    tf = timeframe.strip().upper()
    if tf not in ("M1", "M15", "H4"):
        raise ValueError(f"Unsupported timeframe for cache layout: {timeframe}")
    key = tf.lower()
    return Path(cache_dir) / key / sym / f"{sym_l}_{key}_{start}_{end}.parquet"


def dukascopy_m1_chunks_dir(cache_dir: Path, symbol: str) -> Path:
    """Per-pair M1 chunk downloads: <cache_dir>/m1/<PAIR>/chunks/"""
    return Path(cache_dir) / "m1" / dukascopy_pair_dir(symbol) / "chunks"


def _dukascopy_legacy_flat_path(
    cache_dir: Path, symbol: str, timeframe: str, start: str, end: str
) -> Path:
    """Pre-relayout flat file at cache root (backward compatibility)."""
    sym_l = dukascopy_pair_dir(symbol).lower()
    tf = timeframe.strip().upper().lower()
    return Path(cache_dir) / f"{sym_l}_{tf}_{start}_{end}.parquet"


def dukascopy_resolve_read_path(
    cache_dir: Path | str,
    symbol: str,
    timeframe: str,
    start: str,
    end: str,
) -> Optional[Path]:
    """Return existing parquet path (new layout preferred) or None."""
    base = Path(cache_dir)
    new = dukascopy_ohlcv_path(base, symbol, timeframe, start, end)
    if new.exists():
        return new
    legacy = _dukascopy_legacy_flat_path(base, symbol, timeframe, start, end)
    if legacy.exists():
        return legacy
    return None


DUKASCOPY_BASE_URL = "https://datafeed.dukascopy.com/datafeed/XAUUSD"
# Dukascopy stores M15 OHLCV in BI5 format: one file per hour
# Each BI5 file contains 4 candles (4 × 15min = 1 hour)


def _bi5_url(dt: datetime) -> str:
    """Build Dukascopy BI5 URL for a given UTC hour."""
    return (
        f"{DUKASCOPY_BASE_URL}/{dt.year:04d}/"
        f"{(dt.month - 1):02d}/{dt.day:02d}/"
        f"{dt.hour:02d}h_ticks.bi5"
    )


def _parse_bi5_candles(raw: bytes, hour_dt: datetime) -> list[dict]:
    """
    Parse Dukascopy BI5 binary format into OHLCV dicts.
    Each record is 32 bytes: timestamp(ms), ask_open, ask_high, ask_low,
    ask_close, bid_open, bid_high, bid_low, bid_close (all int32, price * 100000)
    We use mid = (ask + bid) / 2 for OHLCV.
    """
    import lzma
    try:
        data = lzma.decompress(raw)
    except Exception:
        return []

    record_size = 20  # timestamp(4) + ask(4) + bid(4) + volume_ask(4) + volume_bid(4)
    candles = []
    for i in range(0, len(data) - record_size + 1, record_size):
        chunk = data[i:i + record_size]
        if len(chunk) < record_size:
            break
        ts_ms, ask, bid, vol_ask, vol_bid = struct.unpack(">IIIff", chunk)
        ts = hour_dt + timedelta(milliseconds=ts_ms)
        mid = (ask + bid) / 2 / 1000.0  # XAU/USD: stored in 0.001 USD units
        candles.append({
            "timestamp": ts,
            "price": mid,
            "volume": vol_ask + vol_bid,
        })
    return candles


def _fetch_hour_candles(dt: datetime, session: requests.Session) -> Optional[pd.DataFrame]:
    """Download one hour of tick data and resample to M15."""
    url = _bi5_url(dt)
    try:
        resp = session.get(url, timeout=15)
        if resp.status_code == 404:
            return None  # no data for this hour (weekend, holiday)
        resp.raise_for_status()
        ticks = _parse_bi5_candles(resp.content, dt)
        if not ticks:
            return None
        df = pd.DataFrame(ticks).set_index("timestamp")
        df.index = pd.to_datetime(df.index, utc=True)
        ohlcv = df["price"].resample("15min").ohlc()
        ohlcv["volume"] = df["volume"].resample("15min").sum()
        ohlcv = ohlcv.dropna()
        return ohlcv
    except Exception as e:
        logger.debug(f"Dukascopy fetch failed for {url}: {e}")
        return None


def download(
    start: str = "2020-01-01",
    end: str = "2023-12-31",
    cache_dir: str = None,
    force_refresh: bool = False,
) -> pd.DataFrame:
    """
    Download Dukascopy M15 XAU/USD data for the given date range.
    Caches result as parquet. Subsequent calls load from cache.

    Returns DataFrame with columns: open, high, low, close, volume
    Index: UTC DatetimeIndex at 15-min intervals (trading hours only)
    """
    cache_dir = Path(cache_dir or config.DUKASCOPY_CACHE_DIR)
    cache_dir.mkdir(parents=True, exist_ok=True)
    resolved = dukascopy_resolve_read_path(cache_dir, "XAUUSD", "M15", start, end)
    if resolved and not force_refresh:
        logger.info(f"Loading Dukascopy cache: {resolved}")
        return pd.read_parquet(resolved)
    cache_file = dukascopy_ohlcv_path(cache_dir, "XAUUSD", "M15", start, end)

    logger.info(f"Downloading Dukascopy M15 XAU/USD {start} → {end} (parallel, 16 workers)")
    start_dt = datetime.fromisoformat(start).replace(tzinfo=timezone.utc)
    end_dt = datetime.fromisoformat(end).replace(tzinfo=timezone.utc)

    # Build list of hours to fetch (skip weekends)
    hours: list[datetime] = []
    current = start_dt
    while current < end_dt:
        weekday = current.weekday()  # 0=Mon, 5=Sat, 6=Sun
        if not (weekday == 5 or (weekday == 6 and current.hour < 21)):
            hours.append(current)
        current += timedelta(hours=1)

    total = len(hours)
    logger.info(f"  {total:,} trading hours to fetch")

    frames: list[pd.DataFrame] = []
    completed = 0

    def _fetch_with_session(dt: datetime) -> Optional[pd.DataFrame]:
        # Each thread gets its own session (requests.Session is not thread-safe)
        s = requests.Session()
        s.headers["User-Agent"] = "Mozilla/5.0"
        return _fetch_hour_candles(dt, s)

    with ThreadPoolExecutor(max_workers=16) as pool:
        future_to_dt = {pool.submit(_fetch_with_session, dt): dt for dt in hours}
        for future in as_completed(future_to_dt):
            completed += 1
            if completed % 500 == 0:
                pct = completed / total * 100
                dt = future_to_dt[future]
                logger.info(f"  {pct:.0f}% ({dt.date()})")
            try:
                df = future.result()
                if df is not None and len(df) > 0:
                    frames.append(df)
            except Exception as e:
                logger.debug(f"Worker error: {e}")

    if not frames:
        raise RuntimeError("No data downloaded from Dukascopy")

    result = pd.concat(frames).sort_index()
    result = result[~result.index.duplicated(keep="first")]
    result = validate_data(result)

    cache_file.parent.mkdir(parents=True, exist_ok=True)
    result.to_parquet(cache_file)
    logger.info(f"Saved {len(result)} M15 bars to {cache_file}")
    return result


def validate_data(df: pd.DataFrame) -> pd.DataFrame:
    """
    Validate and clean M15 OHLCV data.
    - Remove bars with zero volume
    - Flag gaps > 4h on weekdays
    - Remove obvious bad ticks (price > 2x or < 0.5x neighbours)

    Returns cleaned DataFrame. Logs warnings for suspicious gaps.
    """
    original_len = len(df)

    # Drop zero-volume bars
    df = df[df["volume"] > 0].copy()

    # Drop bars with OHLC that make no sense
    df = df[(df["low"] > 0) & (df["high"] >= df["low"])].copy()

    # Detect price spikes: close > 2x or < 0.5x of rolling median
    rolling_med = df["close"].rolling(48, center=True, min_periods=12).median()
    spike_mask = (df["close"] > rolling_med * 2.0) | (df["close"] < rolling_med * 0.5)
    if spike_mask.sum() > 0:
        logger.warning(f"Removing {spike_mask.sum()} price spike bars")
        df = df[~spike_mask].copy()

    # Log weekday gaps > 4h
    df.index = pd.to_datetime(df.index, utc=True)
    time_diff = df.index.to_series().diff()
    big_gaps = time_diff[
        (time_diff > pd.Timedelta("4h")) &
        (df.index.dayofweek < 5)  # weekdays only
    ]
    for ts, gap in big_gaps.items():
        logger.warning(f"Data gap: {gap} at {ts}")

    dropped = original_len - len(df)
    if dropped > 0:
        logger.info(f"Validation removed {dropped} bars ({dropped/original_len*100:.1f}%)")

    return df


def load(
    start: str = None,
    end: str = None,
    cache_dir: str = None,
) -> pd.DataFrame:
    """
    Load cached Dukascopy data. If not cached, download first.
    Optionally slice to [start, end].
    """
    df = download(
        start=start or config.BACKTEST_START,
        end=end or config.BACKTEST_END,
        cache_dir=cache_dir,
    )
    if start:
        df = df[df.index >= pd.Timestamp(start, tz="UTC")]
    if end:
        df = df[df.index <= pd.Timestamp(end, tz="UTC")]
    return df


def load_timeframe(
    timeframe: str,
    start: str = None,
    end: str = None,
    cache_dir: str = None,
    symbol: str = "XAUUSD",
) -> pd.DataFrame:
    """
    Load a cached Dukascopy timeframe parquet.
    Layout: <DUKASCOPY_CACHE_DIR>/m1|m15|h4/<PAIR>/ <pair>_<tf>_<start>_<end>.parquet
    Falls back to legacy flat filename at cache root if present.
    """
    cache_dir = Path(cache_dir or config.DUKASCOPY_CACHE_DIR)
    s = start or config.BACKTEST_START
    e = end or config.BACKTEST_END
    file_path = dukascopy_resolve_read_path(cache_dir, symbol, timeframe, s, e)
    if file_path is None:
        expected = dukascopy_ohlcv_path(cache_dir, symbol, timeframe, s, e)
        raise FileNotFoundError(
            f"Dukascopy cache not found for {symbol} timeframe '{timeframe}': {expected}"
        )
    df = pd.read_parquet(file_path)
    df.index = pd.to_datetime(df.index, utc=True)
    df = df.sort_index()
    if start:
        df = df[df.index >= pd.Timestamp(start, tz="UTC")]
    if end:
        df = df[df.index <= pd.Timestamp(end, tz="UTC")]
    return df
