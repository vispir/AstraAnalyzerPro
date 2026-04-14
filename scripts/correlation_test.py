"""
LLM vs Proxy Correlation Test

Runs both proxy_macro_bias() and get_bias() (real LLM) on N historical dates,
computes direction agreement rate. Target: >= 75%.

This is a one-time calibration test, not part of CI (it costs ~$0.05-0.10
per run and requires FRED/yfinance data + Gemini API key).

Usage:
    python scripts/correlation_test.py --n 100 --start 2023-01-01 --end 2024-01-01

Output:
    Agreement rate: 0.82 (82/100 dates matched)
    BULLISH precision: 0.88
    BEARISH precision: 0.79
    NEUTRAL agreement: 0.73

If agreement < 75%, review proxy rules against the LLM prompt in macro_engine.py.
The most common disagreement is usually on the VIX threshold or COT weighting.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import time
import pandas as pd

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


def run_correlation_test(
    n: int = 100,
    start: str = "2023-01-01",
    end: str = "2024-01-01",
) -> dict:
    from astra_v2 import config
    from astra_v2.data.macro_features import compute_macro_features
    from astra_v2.backtest.llm_proxy import proxy_macro_bias
    from astra_v2.core.macro_engine import get_bias
    from astra_v2.data.fred_client import fetch_all as fetch_fred_bulk
    from astra_v2.data.external import fetch_yfinance_bulk, fetch_cot_gold

    logger.info("Loading bulk data...")
    try:
        fred_df = fetch_fred_bulk(start, end)
        yfinance_df = fetch_yfinance_bulk(start, end)
        cot_df = fetch_cot_gold()
    except Exception as e:
        logger.error(f"Data fetch failed: {e}")
        sys.exit(1)

    # Sample N business days in the date range
    all_dates = pd.bdate_range(start=start, end=end)
    if len(all_dates) < n:
        logger.warning(f"Only {len(all_dates)} business days available, using all")
        n = len(all_dates)

    sample_dates = all_dates[:: max(1, len(all_dates) // n)][:n]
    logger.info(f"Testing on {len(sample_dates)} dates")

    proxy_dirs = []
    llm_dirs = []
    agreements = []

    for i, date in enumerate(sample_dates):
        dt = datetime(date.year, date.month, date.day, 8, 0, tzinfo=timezone.utc)
        logger.info(f"[{i+1}/{len(sample_dates)}] {dt.strftime('%Y-%m-%d')}")

        try:
            features = compute_macro_features(
                dt, fred_df=fred_df, yfinance_df=yfinance_df, cot_df=cot_df
            )
        except Exception as e:
            logger.warning(f"  Feature computation failed: {e}")
            continue

        # Proxy (deterministic, free)
        try:
            proxy = proxy_macro_bias(features)
            proxy_dir = proxy.direction
        except Exception as e:
            logger.warning(f"  Proxy failed: {e}")
            continue

        # LLM (real Gemini call) — pass dt so it uses historical data, not today's
        try:
            llm = get_bias(fred_df=fred_df, yfinance_df=yfinance_df, cot_df=cot_df, dt=dt)
            llm_dir = llm.direction
        except Exception as e:
            logger.warning(f"  LLM failed: {e}")
            continue

        agree = proxy_dir == llm_dir
        agreements.append(agree)
        proxy_dirs.append(proxy_dir)
        llm_dirs.append(llm_dir)

        logger.info(f"  Proxy={proxy_dir} LLM={llm_dir} {'AGREE' if agree else 'DISAGREE'}")
        if i < len(sample_dates) - 1:
            time.sleep(20)  # mistral-7b free tier: ~3 req/min

    if not agreements:
        logger.error("No successful comparisons.")
        return {}

    total = len(agreements)
    agreed = sum(agreements)
    rate = agreed / total

    # Per-direction precision
    for direction in ("BULLISH", "BEARISH", "NEUTRAL"):
        llm_positive = [i for i, d in enumerate(llm_dirs) if d == direction]
        proxy_agreed = sum(proxy_dirs[i] == direction for i in llm_positive)
        precision = proxy_agreed / len(llm_positive) if llm_positive else 0.0
        logger.info(f"{direction} precision: {precision:.2f} ({proxy_agreed}/{len(llm_positive)})")

    result = {
        "n_tested": total,
        "n_agreed": agreed,
        "agreement_rate": round(rate, 3),
        "target_met": rate >= 0.75,
    }

    print(f"\n=== CORRELATION TEST RESULTS ===")
    print(f"Dates tested:    {total}")
    print(f"Agreements:      {agreed}")
    print(f"Agreement rate:  {rate:.1%}")
    print(f"Target (>=75%):  {'PASS' if rate >= 0.75 else 'FAIL'}")
    print()

    if rate < 0.75:
        print("FAIL: Review proxy rules in backtest/llm_proxy.py.")
        print("Compare against the LLM prompt in core/macro_engine.py._build_prompt()")
        sys.exit(1)

    return result


def main():
    parser = argparse.ArgumentParser(description="LLM vs Proxy correlation test")
    parser.add_argument("--n", type=int, default=100, help="Number of dates to test")
    parser.add_argument("--start", default="2023-01-01", help="Start date (YYYY-MM-DD)")
    parser.add_argument("--end", default="2024-01-01", help="End date (YYYY-MM-DD)")
    args = parser.parse_args()

    run_correlation_test(n=args.n, start=args.start, end=args.end)


if __name__ == "__main__":
    main()
