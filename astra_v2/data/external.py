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
CFTC_COT_URL = "https://www.cftc.gov/dea/newcot/deaHistTff.txt"
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


def fetch_cot_gold() -> pd.DataFrame:
    """
    Download CFTC COT report for gold futures (COMEX 088691).
    Returns DataFrame with weekly index and columns:
      net_noncommercial — non-commercial net long (large speculators)

    Note: COT is released every Friday for the prior Tuesday.
    Data is 3-10 days stale by definition. Use as directional regime indicator only.
    """
    logger.info("Fetching CFTC COT data for gold futures")
    try:
        resp = requests.get(CFTC_COT_URL, timeout=30)
        resp.raise_for_status()

        df = pd.read_csv(
            StringIO(resp.text),
            low_memory=False,
        )

        # Filter to gold futures
        gold = df[df["CFTC_Commodity_Code"].astype(str).str.strip() == CFTC_COT_GOLD_CODE].copy()
        if gold.empty:
            # Try alternate column name
            gold = df[df.get("Commodity_Code", pd.Series()).astype(str).str.strip() == CFTC_COT_GOLD_CODE].copy()

        if gold.empty:
            logger.warning("Could not find gold in COT report (code 088691)")
            return pd.DataFrame()

        gold["date"] = pd.to_datetime(gold["Report_Date_as_MM_DD_YYYY"], format="%m/%d/%Y", errors="coerce")
        gold = gold.dropna(subset=["date"]).set_index("date").sort_index()

        # Net non-commercial = long - short (speculator positioning)
        long_col = "NonComm_Positions_Long_All"
        short_col = "NonComm_Positions_Short_All"
        if long_col in gold.columns and short_col in gold.columns:
            gold["net_noncommercial"] = gold[long_col] - gold[short_col]

        return gold[["net_noncommercial"]].dropna()

    except Exception as e:
        logger.warning(f"COT fetch failed: {e}")
        return pd.DataFrame()


def get_cot_for_date(dt: datetime, cot_df: pd.DataFrame = None) -> dict:
    """
    Get COT net positioning for the nearest prior Tuesday report.
    COT is always stale by 3-10 days — that's expected.
    """
    if cot_df is None or cot_df.empty:
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
