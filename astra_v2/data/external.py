"""
External market data: DXY, VIX, TNX via yfinance + COT via CFTC.

All functions return simple dicts for the given date.
For backtesting: fetch bulk data once, then query by date.
For live: fetch recent data and use the most recent cached snapshot.
"""

from __future__ import annotations

import io
import logging
import zipfile
from datetime import date as date_type
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import pandas as pd
import requests
import yfinance as yf

from astra_v2 import config

logger = logging.getLogger(__name__)

# CFTC COT report - gold futures (COMEX), disaggregated yearly ZIP files.
CFTC_COT_BASE_URL = "https://www.cftc.gov/files/dea/history/fut_disagg_txt_{year}.zip"


def _read_cached_dataframe(cache_path: Path) -> pd.DataFrame:
    if not cache_path.exists():
        return pd.DataFrame()
    df = pd.read_parquet(cache_path)
    logger.info(f"Loaded cache: {cache_path} ({len(df)} rows)")
    return df


def _write_cached_dataframe(df: pd.DataFrame, cache_path: Path) -> None:
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(cache_path)
    logger.info(f"Saved cache: {cache_path} ({len(df)} rows)")


def _slice_by_date(df: pd.DataFrame, start: str, end: str) -> pd.DataFrame:
    if df.empty:
        return df
    start_ts = pd.Timestamp(start)
    end_ts = pd.Timestamp(end)
    return df[(df.index >= start_ts) & (df.index < end_ts)].sort_index()


def _cache_covers_range(df: pd.DataFrame, start: str, end: str) -> bool:
    if df.empty:
        return False
    start_ts = pd.Timestamp(start)
    end_ts = pd.Timestamp(end) - timedelta(days=1)
    earliest_ok = df.index.min() <= (start_ts + timedelta(days=5))
    latest_ok = df.index.max() >= (end_ts - timedelta(days=5))
    return earliest_ok and latest_ok


def fetch_yfinance_bulk(
    start: str,
    end: str,
    symbols: list[str] = None,
    cache_path: str = None,
    force_refresh: bool = False,
    cache_only: bool = False,
) -> pd.DataFrame:
    """
    Load yfinance daily data for the given symbols and date range.

    Default behavior is cache-first. Missing coverage is refreshed from yfinance
    unless cache_only=True.
    """
    symbols = symbols or [config.YFINANCE_DXY, config.YFINANCE_VIX, config.YFINANCE_TNX]
    cache = Path(cache_path or config.YFINANCE_CACHE_PATH)
    cached = _read_cached_dataframe(cache) if cache.exists() else pd.DataFrame()

    if not force_refresh and _cache_covers_range(cached, start, end):
        return _slice_by_date(cached, start, end)

    if cache_only:
        if cached.empty:
            logger.warning("yfinance cache unavailable and cache_only=True - running without yfinance signal")
        else:
            logger.warning("yfinance cache does not fully cover requested range and cache_only=True")
        return _slice_by_date(cached, start, end)

    logger.info(f"Downloading yfinance: {symbols} {start}->{end}")
    fresh = yf.download(symbols, start=start, end=end, auto_adjust=True, progress=False)
    if fresh.empty:
        logger.warning("yfinance download returned no rows")
        return _slice_by_date(cached, start, end)

    fresh.index = pd.to_datetime(fresh.index)
    combined = fresh if cached.empty else pd.concat([cached, fresh]).sort_index()
    combined = combined[~combined.index.duplicated(keep="last")]
    _write_cached_dataframe(combined, cache)
    return _slice_by_date(combined, start, end)


def get_yfinance_for_date(dt: datetime, df: pd.DataFrame = None) -> dict:
    """
    Get DXY, VIX, TNX for a specific date from a bulk DataFrame.
    Falls back up to 5 trading days if the exact date is missing.
    """
    if df is None:
        start = (dt - timedelta(days=10)).strftime("%Y-%m-%d")
        end = (dt + timedelta(days=1)).strftime("%Y-%m-%d")
        df = fetch_yfinance_bulk(start, end)

    target = pd.Timestamp(dt.date())
    result = {"dxy": None, "vix": None, "tnx": None}

    for days_back in range(6):
        check = target - timedelta(days=days_back)
        if check not in df.index:
            continue

        row = df.loc[check]

        def _get(symbol: str) -> Optional[float]:
            try:
                if hasattr(row, "index") and ("Close", symbol) in row.index:
                    val = row[("Close", symbol)]
                else:
                    val = row.get(symbol)
                return float(val) if val is not None and not pd.isna(val) else None
            except Exception:
                return None

        result["dxy"] = _get(config.YFINANCE_DXY)
        result["vix"] = _get(config.YFINANCE_VIX)
        result["tnx"] = _get(config.YFINANCE_TNX)
        result["date"] = check
        break

    return result


def fetch_cot_gold(
    start_year: int = 2019,
    cache_path: str = None,
    force_refresh: bool = False,
    cache_only: bool = False,
) -> pd.DataFrame:
    """
    Load CFTC COT report for gold futures.

    Default behavior is cache-first. Network is used only when refreshing or
    when the cache is absent and cache_only=False.
    """
    cache = Path(cache_path or config.COT_CACHE_PATH)
    if cache.exists() and not force_refresh:
        return _read_cached_dataframe(cache)

    if cache_only:
        logger.warning("COT cache unavailable and cache_only=True - running without COT signal")
        return pd.DataFrame()

    logger.info("Fetching CFTC COT data for gold futures")
    current_year = date_type.today().year
    frames = []

    for year in range(start_year, current_year + 1):
        url = CFTC_COT_BASE_URL.format(year=year)
        try:
            resp = requests.get(url, timeout=60)
            resp.raise_for_status()

            with zipfile.ZipFile(io.BytesIO(resp.content)) as archive:
                txt_name = next(name for name in archive.namelist() if name.endswith(".txt"))
                with archive.open(txt_name) as file_handle:
                    df = pd.read_csv(file_handle, low_memory=False)

            name_col = "Market_and_Exchange_Names"
            if name_col not in df.columns:
                continue

            gold = df[
                df[name_col].str.contains("GOLD", case=False, na=False)
                & df[name_col].str.contains("COMMODITY EXCHANGE", case=False, na=False)
            ].copy()
            if gold.empty:
                continue

            if "Report_Date_as_YYYY-MM-DD" in gold.columns:
                gold["date"] = pd.to_datetime(gold["Report_Date_as_YYYY-MM-DD"], errors="coerce")
            else:
                date_col = next((col for col in gold.columns if "Report_Date" in col), None)
                if date_col is None:
                    continue
                gold["date"] = pd.to_datetime(gold[date_col], errors="coerce")

            gold = gold.dropna(subset=["date"]).set_index("date")
            long_col = next((col for col in gold.columns if "M_Money" in col and "Long" in col and "All" in col), None)
            short_col = next((col for col in gold.columns if "M_Money" in col and "Short" in col and "All" in col), None)
            if long_col and short_col:
                gold["net_noncommercial"] = (
                    pd.to_numeric(gold[long_col], errors="coerce")
                    - pd.to_numeric(gold[short_col], errors="coerce")
                )
                frames.append(gold[["net_noncommercial"]])
                logger.debug(f"COT {year}: {len(gold)} rows")
        except Exception as exc:
            logger.warning(f"COT fetch failed for {year}: {exc}")

    if not frames:
        logger.warning("No COT data fetched - running without COT signal")
        return pd.DataFrame()

    result = pd.concat(frames).sort_index()
    result = result[~result.index.duplicated(keep="last")].dropna()
    _write_cached_dataframe(result, cache)
    logger.info(
        f"COT data loaded: {len(result)} weekly reports ({result.index[0].date()} -> {result.index[-1].date()})"
    )
    return result


def get_cot_for_date(dt: datetime, cot_df: pd.DataFrame = None) -> dict:
    """
    Get COT net positioning for the nearest prior Tuesday report.
    COT is always stale by 3-10 days - that's expected.
    """
    if cot_df is None:
        cot_df = fetch_cot_gold()

    if cot_df.empty:
        return {"cot_net": 0, "cot_date": None}

    target = pd.Timestamp(dt.date())
    prior = cot_df[cot_df.index <= target]
    if prior.empty:
        return {"cot_net": 0, "cot_date": None}

    latest = prior.iloc[-1]
    return {
        "cot_net": int(latest.get("net_noncommercial", 0)),
        "cot_date": prior.index[-1].date(),
    }
