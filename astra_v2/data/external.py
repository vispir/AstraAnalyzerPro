"""
External market data: DXY, VIX, TNX via yfinance + COT via CFTC.

All functions return simple dicts for the given date.
For backtesting: fetch bulk data once, then query by date.
For live: fetch last N days and use most recent.
"""

import logging
import requests
import pandas as pd
import yfinance as yf
from datetime import datetime, timedelta, date
from typing import Optional
from io import StringIO

from astra_v2 import config

logger = logging.getLogger(__name__)

# CFTC COT report — gold futures (COMEX), commodity code 088691
# Disaggregated format, yearly ZIP files (CFTC migrated from .txt to .zip per year)
CFTC_COT_BASE_URL = "https://www.cftc.gov/files/dea/history/fut_disagg_txt_{year}.zip"
CFTC_COT_GOLD_CODE = "088691"


def fetch_yfinance_bulk(
    start: str,
    end: str,
    symbols: list[str] = None,
) -> pd.DataFrame:
    """
    Bulk download yfinance data for the given symbols and date range.
    Returns DataFrame with MultiIndex columns (symbol, field).

    Symbols default to DXY, VIX, TNX.
    """
    symbols = symbols or [config.YFINANCE_DXY, config.YFINANCE_VIX, config.YFINANCE_TNX]
    logger.info(f"Downloading yfinance: {symbols} {start}→{end}")
    df = yf.download(symbols, start=start, end=end, auto_adjust=True, progress=False)
    return df


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
                val = row[("Close", symbol)] if ("Close", symbol) in row.index else row.get(symbol)
                return float(val) if val is not None and not pd.isna(val) else None
            except Exception:
                return None

        result["dxy"] = _get(config.YFINANCE_DXY)
        result["vix"] = _get(config.YFINANCE_VIX)
        result["tnx"] = _get(config.YFINANCE_TNX)
        result["date"] = check
        break

    return result


def fetch_cot_gold(start_year: int = 2019) -> pd.DataFrame:
    """
    Download CFTC COT report for gold futures (COMEX 088691).
    Downloads yearly ZIP files (Disaggregated format) and concatenates.
    Returns DataFrame with weekly index and column: net_noncommercial

    Note: COT is released every Friday for the prior Tuesday.
    Data is 3-10 days stale by definition. Use as directional regime indicator only.
    """
    import zipfile
    import io
    from datetime import date as date_type

    logger.info("Fetching CFTC COT data for gold futures")
    current_year = date_type.today().year
    frames = []

    for year in range(start_year, current_year + 1):
        url = CFTC_COT_BASE_URL.format(year=year)
        try:
            resp = requests.get(url, timeout=60)
            resp.raise_for_status()

            with zipfile.ZipFile(io.BytesIO(resp.content)) as z:
                # ZIP contains a single .txt file
                txt_name = [n for n in z.namelist() if n.endswith(".txt")][0]
                with z.open(txt_name) as f:
                    df = pd.read_csv(f, low_memory=False)

            # Filter to COMEX gold futures by market name (most reliable across format versions)
            name_col = "Market_and_Exchange_Names"
            if name_col not in df.columns:
                continue
            gold = df[
                df[name_col].str.contains("GOLD", case=False, na=False) &
                df[name_col].str.contains("COMMODITY EXCHANGE", case=False, na=False)
            ].copy()
            if gold.empty:
                continue

            # Parse date — prefer ISO format column
            if "Report_Date_as_YYYY-MM-DD" in gold.columns:
                gold["date"] = pd.to_datetime(gold["Report_Date_as_YYYY-MM-DD"], errors="coerce")
            else:
                date_col = next((c for c in gold.columns if "Report_Date" in c), None)
                if date_col is None:
                    continue
                gold["date"] = pd.to_datetime(gold[date_col], errors="coerce")
            gold = gold.dropna(subset=["date"]).set_index("date")

            # Managed Money net = long - short (Disaggregated format)
            long_col = next((c for c in gold.columns if "M_Money" in c and "Long" in c and "All" in c), None)
            short_col = next((c for c in gold.columns if "M_Money" in c and "Short" in c and "All" in c), None)
            if long_col and short_col:
                gold["net_noncommercial"] = pd.to_numeric(gold[long_col], errors="coerce") - pd.to_numeric(gold[short_col], errors="coerce")
                frames.append(gold[["net_noncommercial"]])
                logger.debug(f"COT {year}: {len(gold)} rows")

        except Exception as e:
            logger.warning(f"COT fetch failed for {year}: {e}")

    if not frames:
        logger.warning("No COT data fetched — running without COT signal")
        return pd.DataFrame()

    result = pd.concat(frames).sort_index()
    result = result[~result.index.duplicated(keep="last")]
    logger.info(f"COT data loaded: {len(result)} weekly reports ({result.index[0].date()} → {result.index[-1].date()})")
    return result.dropna()


def get_cot_for_date(dt: datetime, cot_df: pd.DataFrame = None) -> dict:
    """
    Get COT net positioning for the nearest prior Tuesday report.
    COT is always stale by 3-10 days — that's expected.
    """
    if cot_df is None:
        # Only fetch if not provided at all (live mode).
        # If caller passed an empty DataFrame, COT is unavailable — don't retry.
        cot_df = fetch_cot_gold()

    if cot_df.empty:
        return {"cot_net": 0, "cot_date": None}

    target = pd.Timestamp(dt.date())

    # Find most recent COT report on or before target date
    prior = cot_df[cot_df.index <= target]
    if prior.empty:
        return {"cot_net": 0, "cot_date": None}

    latest = prior.iloc[-1]
    return {
        "cot_net": int(latest.get("net_noncommercial", 0)),
        "cot_date": prior.index[-1].date(),
    }
