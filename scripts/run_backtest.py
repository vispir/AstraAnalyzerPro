"""
Backtest runner script.

Usage:
    python scripts/run_backtest.py --start 2020-01-01 --end 2024-12-31 --mode proxy
    python scripts/run_backtest.py --start 2022-01-01 --end 2024-01-01 --mode llm

Modes:
    proxy  — deterministic LLM proxy (fast, free, reproducible)
    llm    — real Gemini calls (slow, ~$13 for 5yr, use for validation)

Output: prints BacktestResult.summary() + Monte Carlo confidence intervals.
"""

from __future__ import annotations

import argparse
import logging
import json
import os
import sys

# Ensure project root is on the path when run as `python scripts/run_backtest.py`
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


def main():
    parser = argparse.ArgumentParser(description="Astra v2 Backtester")
    parser.add_argument("--start", default="2020-01-01", help="Start date")
    parser.add_argument("--end", default="2024-12-31", help="End date")
    parser.add_argument("--mode", choices=["proxy", "llm"], default="proxy")
    parser.add_argument("--balance", type=float, default=10_000.0, help="Starting balance USD")
    parser.add_argument("--train-months", type=int, default=6)
    parser.add_argument("--test-months", type=int, default=1)
    parser.add_argument("--monte-carlo", action="store_true", help="Run Monte Carlo simulation")
    args = parser.parse_args()

    from astra_v2 import config
    from astra_v2.data.dukascopy import load as load_bars
    from astra_v2.data.fred_client import fetch_all as fetch_fred_bulk
    from astra_v2.data.external import fetch_yfinance_bulk, fetch_cot_gold
    from astra_v2.backtest.engine import run_backtest
    from astra_v2.backtest.monte_carlo import run_monte_carlo

    logger.info(f"Backtest: {args.start} to {args.end}, mode={args.mode}")

    # Load data
    logger.info("Loading Dukascopy M15 bars...")
    bars = load_bars(start=args.start, end=args.end)
    logger.info(f"Bars loaded: {len(bars):,}")

    logger.info("Loading FRED bulk data...")
    fred_df = fetch_fred_bulk(args.start, args.end)

    logger.info("Loading yfinance bulk data...")
    yfinance_df = fetch_yfinance_bulk(args.start, args.end)

    logger.info("Loading COT data...")
    cot_df = fetch_cot_gold()

    # Run backtest
    result = run_backtest(
        bars=bars,
        fred_df=fred_df,
        yfinance_df=yfinance_df,
        cot_df=cot_df,
        mode=args.mode,
        start_balance=args.balance,
        wf_train_months=args.train_months,
        wf_test_months=args.test_months,
    )

    summary = result.summary()
    print("\n=== BACKTEST RESULTS ===")
    print(json.dumps(summary, indent=2))

    # Check against prop firm targets
    print("\n=== PROP FIRM VALIDATION ===")
    pf_ok = summary["profit_factor"] >= 1.5
    dd_ok = summary["max_drawdown_pct"] <= 5.0
    print(f"Profit Factor >= 1.5:  {'PASS' if pf_ok else 'FAIL'} ({summary['profit_factor']:.3f})")
    print(f"Max DD <= 5%:          {'PASS' if dd_ok else 'FAIL'} ({summary['max_drawdown_pct']:.2f}%)")

    if args.monte_carlo:
        pnl_list = [t.dollar_pnl for t in result.trades if t.status != "open"]
        if len(pnl_list) >= 10:
            logger.info("Running Monte Carlo (10,000 runs)...")
            mc = run_monte_carlo(pnl_list, start_balance=args.balance)
            print("\n=== MONTE CARLO (10,000 runs) ===")
            print(json.dumps(mc.summary(), indent=2))
        else:
            logger.warning("Not enough trades for Monte Carlo")


if __name__ == "__main__":
    main()
