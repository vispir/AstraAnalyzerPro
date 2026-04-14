"""
FRED API client — bulk fetch macro series, cache as parquet.

Fetches daily data for the full backtest range in ONE call per series.
This avoids FRED's 120 req/min rate limit when running backtests.

Series used:
  DGS10  — 10-Year Treasury Constant Maturity Rate
  T10YIE — 10-Year Breakeven Inflation Rate (TIPS proxy)
  DTWEXBGS — Broad Dollar Index (alternative to DXY)

TIPS spread = DGS10 - T10YIE
  Falling TIPS spread → real yields falling → bullish gold
  Rising TIPS spread → real yields rising → bearish gold
"""

import logging
import os
import requests
import pandas as pd
from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional

from astra_v2 import config

logger = logging.getLogger(__name__)

FRED_API_BASE = "https://api.stlouisfed.org/fred/series/observations"

SERIES = {
    "DGS10": "10Y Treasury yield",
    "T10YIE": "10Y breakeven inflation (TIPS)",
    "DTWEXBGS": "Broad dollar index",
    "DCOILWTICO": "WTI crude (risk proxy)",
}


def _fetch_series(series_id: str, start: str, end: str) -> pd.Series:
    """Fetch a single FRED series. Returns daily pd.Series."""
    params = {
        "series_id": series_id,
        "observation_start": start,
        "observation_end": end,
        "api_key": config.FRED_API_KEY,
        "file_type": "json",
        "frequency": "d",
    }
    resp = requests.get(FRED_API_BASE, params=params, timeout=30)
    resp.raise_for_status()
    data = resp.json()

    rows = []
    for obs in data.get("observations", []):
        if obs["value"] == ".":
            continue  # FRED uses "." for missing values
        rows.append({"date": obs["date"], "value": float(obs["value"])})

    if not rows:
        logger.warning(f"No data returned for FRED series {series_id}")
        return pd.Series(dtype=float, name=series_id)

    s = pd.DataFrame(rows).set_index("date")["value"]
    s.index = pd.to_datetime(s.index)
    s.name = series_id
    return s


def fetch_all(
    start: str = None,
    end: str = None,
    cache_path: str = None,
    force_refresh: bool = False,
) -> pd.DataFrame:
    """
    Fetch all macro series from FRED and cache as parquet.

    Returns DataFrame with daily index and columns:
      DGS10, T10YIE, DTWEXBGS, DCOILWTICO, tips_spread

    tips_spread = DGS10 - T10YIE (key gold signal)
    """
    start = start or config.BACKTEST_START
    end = end or config.BACKTEST_HOLDOUT_END  # fetch everything at once
    cache_path = Path(cache_path or config.FRED_CACHE_PATH)
    cache_path.parent.mkdir(parents=True, exist_ok=True)

    if cache_path.exists() and not force_refresh:
        df = pd.read_parquet(cache_path)
        logger.info(f"Loaded FRED cache: {cache_path} ({len(df)} days)")
        return df

    if not config.FRED_API_KEY:
        raise EnvironmentError("FRED_API_KEY not set. Get a free key at https://fred.stlouisfed.org/docs/api/api_key.html")

    logger.info(f"Fetching FRED macro data {start} → {end}")
    series_data = {}
    for series_id in SERIES:
        logger.info(f"  Fetching {series_id} ({SERIES[series_id]})")
        try:
            series_data[series_id] = _fetch_series(series_id, start, end)
        except Exception as e:
            logger.warning(f"  Failed to fetch {series_id}: {e}")
            series_data[series_id] = pd.Series(dtype=float, name=series_id)

    df = pd.DataFrame(series_data)
    df.index = pd.to_datetime(df.index)
    df = df.sort_index()

    # Forward-fill FRED gaps (holidays, weekends) — macro data doesn't change daily
    df = df.ffill().bfill()

    # Derived signal
    if "DGS10" in df.columns and "T10YIE" in df.columns:
        df["tips_spread"] = df["DGS10"] - df["T10YIE"]

    df.to_parquet(cache_path)
    logger.info(f"Saved FRED cache: {cache_path} ({len(df)} days)")
    return df


def get_for_date(dt: datetime, df: pd.DataFrame = None) -> dict:
    """
    Get macro values for a specific date. Uses cached DataFrame.
    Falls back to last available date if exact date not found (handles weekends/holidays).

    Returns dict: {tips_spread, dgs10, t10yie, dxy_broad, ...}
    """
    if df is None:
        df = fetch_all()

    target = pd.Timestamp(dt.date())

    # Find closest available date (look back up to 7 days)
    for days_back in range(8):
        check = target - timedelta(days=days_back)
        if check in df.index:
            row = df.loc[check]
            return {
                "date": check,
                "tips_spread": float(row.get("tips_spread", 0.0)),
                "dgs10": float(row.get("DGS10", 0.0)),
                "t10yie": float(row.get("T10YIE", 0.0)),
                "dxy_broad": float(row.get("DTWEXBGS", 0.0)),
                "wti": float(row.get("DCOILWTICO", 0.0)),
            }

    logger.warning(f"No FRED data found within 7 days of {dt.date()}")
    return {
        "date": target,
        "tips_spread": 0.0,
        "dgs10": 0.0,
        "t10yie": 0.0,
        "dxy_broad": 0.0,
        "wti": 0.0,
    }
