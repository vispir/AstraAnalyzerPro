"""
Shared macro feature computation — used by both macro_engine.py (live LLM)
and backtest/llm_proxy.py (deterministic rules).

DRY: one formula for TIPS spread, DXY change, etc.
If this diverges between live and backtest, results won't be comparable.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional
import pandas as pd

logger = logging.getLogger(__name__)


@dataclass
class MacroFeatures:
    date: str
    tips_spread: float       # DGS10 - T10YIE; falling = bullish gold
    dgs10: float             # 10Y nominal yield
    t10yie: float            # 10Y breakeven inflation
    dxy_broad: float         # broad dollar index
    dxy_1m_change: float     # % change over last 22 trading days
    vix: float               # CBOE VIX
    tnx: float               # 10Y Treasury yield (yfinance, slightly diff from FRED)
    cot_net: Optional[int]   # non-commercial net contracts (3-10 days stale)


def compute_macro_features(
    as_of: datetime,
    fred_df: Optional[pd.DataFrame] = None,
    yfinance_df: Optional[pd.DataFrame] = None,
    cot_df: Optional[pd.DataFrame] = None,
) -> MacroFeatures:
    """
    Compute MacroFeatures for a given datetime.

    For live use: fetches data if DataFrames not provided.
    For backtest: pass pre-loaded DataFrames to avoid API calls.
    """
    from astra_v2.data.fred_client import fetch_all as fetch_fred, get_for_date
    from astra_v2.data.external import (
        fetch_yfinance_bulk, get_yfinance_for_date,
        fetch_cot_gold, get_cot_for_date,
    )

    # Load data if not provided (live mode)
    if fred_df is None:
        fred_df = fetch_fred()
    if yfinance_df is None:
        start = (as_of - timedelta(days=60)).strftime("%Y-%m-%d")
        end = (as_of + timedelta(days=1)).strftime("%Y-%m-%d")
        yfinance_df = fetch_yfinance_bulk(start, end)
    if cot_df is None:
        cot_df = fetch_cot_gold()

    # Get values for this date
    fred = get_for_date(as_of, fred_df)
    yf = get_yfinance_for_date(as_of, yfinance_df)
    cot = get_cot_for_date(as_of, cot_df)

    # DXY 1-month change: compare to ~22 trading days ago
    dxy_1m_change = 0.0
    dxy_broad = fred.get("dxy_broad") or 100.0
    if yfinance_df is not None and not yfinance_df.empty:
        from astra_v2 import config
        target = pd.Timestamp(as_of.date(), tz="UTC") if as_of.tzinfo else pd.Timestamp(as_of.date())
        # Find DXY 22 days prior
        try:
            dxy_col = [c for c in yfinance_df.columns if "NYB" in str(c) or "DX" in str(c)]
            if dxy_col:
                dxy_series = yfinance_df[dxy_col[0]].dropna()
                prior_date = target - timedelta(days=30)
                prior_vals = dxy_series[dxy_series.index <= prior_date]
                current_vals = dxy_series[dxy_series.index <= target]
                if len(prior_vals) > 0 and len(current_vals) > 0:
                    dxy_prior = float(prior_vals.iloc[-1])
                    dxy_now = float(current_vals.iloc[-1])
                    if dxy_prior > 0:
                        dxy_1m_change = (dxy_now - dxy_prior) / dxy_prior * 100
        except Exception as e:
            logger.debug(f"DXY change calculation failed: {e}")

    return MacroFeatures(
        date=as_of.strftime("%Y-%m-%d"),
        tips_spread=fred.get("tips_spread", 0.0),
        dgs10=fred.get("dgs10", 0.0),
        t10yie=fred.get("t10yie", 0.0),
        dxy_broad=dxy_broad,
        dxy_1m_change=dxy_1m_change,
        vix=float(yf.get("vix") or 15.0),
        tnx=float(yf.get("tnx") or 0.0),
        cot_net=cot.get("cot_net"),
    )
