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

import os
import io
import struct
import logging
import requests
import pandas as pd
import numpy as np
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from astra_v2 import config

logger = logging.getLogger(__name__)

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
        mid = (ask + bid) / 2 / 100000.0
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
    cache_file = cache_dir / f"xauusd_m15_{start}_{end}.parquet"

    if cache_file.exists() and not force_refresh:
        logger.info(f"Loading Dukascopy cache: {cache_file}")
        return pd.read_parquet(cache_file)

    logger.info(f"Downloading Dukascopy M15 XAU/USD {start} → {end}")
    start_dt = datetime.fromisoformat(start).replace(tzinfo=timezone.utc)
    end_dt = datetime.fromisoformat(end).replace(tzinfo=timezone.utc)

    frames = []
    session = requests.Session()
    session.headers["User-Agent"] = "Mozilla/5.0"

    current = start_dt
    total_hours = int((end_dt - start_dt).total_seconds() / 3600)
    fetched = 0

    while current < end_dt:
        # Skip weekends (Sat 21:00 UTC → Sun 21:00 UTC is roughly closed)
        weekday = current.weekday()  # 0=Mon, 5=Sat, 6=Sun
        if weekday == 5 or (weekday == 6 and current.hour < 21):
            current += timedelta(hours=1)
            continue

        df = _fetch_hour_candles(current, session)
        if df is not None and len(df) > 0:
            frames.append(df)

        fetched += 1
        if fetched % 100 == 0:
            pct = fetched / total_hours * 100
            logger.info(f"  {pct:.0f}% ({current.date()})")

        current += timedelta(hours=1)

    if not frames:
        raise RuntimeError("No data downloaded from Dukascopy")

    result = pd.concat(frames).sort_index()
    result = result[~result.index.duplicated(keep="first")]
    result = validate_data(result)

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
