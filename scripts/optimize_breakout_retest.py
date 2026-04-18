"""
Grid search optimization for breakout_retest_v1 strategy (XAUUSD only).

Phase 1: Test key parameters with fixed STOP_BUFFER and PARTIAL_CLOSE.
"""

import itertools
import json
import sys
import os
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from astra_v2 import config
from astra_v2.data.dukascopy import load as load_bars_xau, load_timeframe
from astra_v2.data.fred_client import fetch_all as fetch_fred_bulk
from astra_v2.data.external import fetch_yfinance_bulk, fetch_cot_gold
from astra_v2.backtest.engine import run_backtest
from astra_v2.strategies import get_strategy


GRID = {
    "RETEST_PROXIMITY_ATR": [0.3, 0.5, 0.8],
    "TP_RR": [1.5, 2.0, 2.5],
    "REJECTION_MIN_BODY_ATR": [0.05, 0.10],
}

FIXED_PARAMS = {
    "STOP_BUFFER_ATR": 0.20,
    "PARTIAL_CLOSE_RR": 1.0,
}

BACKTEST_PARAMS = {
    "start": "2020-01-01",
    "end": "2024-12-31",
    "mode": "proxy",
    "strategy": "breakout_retest_v1",
    "balance": 10000.0,
    "train_months": 6,
    "test_months": 1,
}

CONSTRAINTS = {
    "max_drawdown_pct": 5.0,
    "min_trades": 50,
    "win_rate_min": 0.45,
}


def meets_constraints(summary):
    """Check if result meets all constraints."""
    if summary["max_drawdown_pct"] >= CONSTRAINTS["max_drawdown_pct"]:
        return False
    if summary["total_trades"] < CONSTRAINTS["min_trades"]:
        return False
    if summary["win_rate"] < CONSTRAINTS["win_rate_min"]:
        return False
    return True


def main():
    print("="*80)
    print("BREAKOUT_RETEST_V1 GRID SEARCH - PHASE 1 (XAUUSD)")
    print("="*80)

    # Load data once
    print("\nLoading data...")

    print("  [1/5] Loading FRED...")
    fred_df = fetch_fred_bulk(BACKTEST_PARAMS["start"], BACKTEST_PARAMS["end"])
    print(f"        OK - FRED: {len(fred_df)} rows")

    print("  [2/5] Loading yfinance...")
    yfinance_df = fetch_yfinance_bulk(BACKTEST_PARAMS["start"], BACKTEST_PARAMS["end"], cache_only=True)
    print(f"        OK - yfinance: {len(yfinance_df)} rows")

    print("  [3/5] Loading COT...")
    cot_df = fetch_cot_gold(cache_only=True)
    print(f"        OK - COT: {len(cot_df)} rows")

    print("  [4/5] Loading M15 bars...")
    bars = load_bars_xau(start=BACKTEST_PARAMS["start"], end=BACKTEST_PARAMS["end"])
    print(f"        OK - M15: {len(bars):,} bars")

    print("  [5/5] Loading H4 bars...")
    h4_bars = load_timeframe("H4", start=BACKTEST_PARAMS["start"], end=BACKTEST_PARAMS["end"], symbol="XAUUSD")
    print(f"        OK - H4: {len(h4_bars):,} bars")

    print(f"\n  All data loaded successfully!")

    # Generate combinations
    keys = list(GRID.keys())
    values = [GRID[k] for k in keys]
    combinations = list(itertools.product(*values))

    param_sets = []
    for combo in combinations:
        params = dict(zip(keys, combo))
        params.update(FIXED_PARAMS)
        param_sets.append(params)

    print(f"\nTotal combinations: {len(param_sets)}")
    print(f"Estimated time: ~{len(param_sets) * 4.5 / 60:.1f} minutes\n")

    # Run grid search with incremental save
    results = []
    valid_count = 0
    output_path = "optimization_results_breakout_retest_v1_phase1.json"

    for i, params in enumerate(param_sets, 1):
        # Override config
        for key, value in params.items():
            setattr(config, f"BREAKOUT_RETEST_V1_{key}", value)

        strategy = get_strategy("breakout_retest_v1")

        result = run_backtest(
            bars=bars,
            fred_df=fred_df,
            yfinance_df=yfinance_df,
            cot_df=cot_df,
            h4_bars=h4_bars,
            mode=BACKTEST_PARAMS["mode"],
            strategy_id=BACKTEST_PARAMS["strategy"],
            start_balance=BACKTEST_PARAMS["balance"],
            wf_train_months=BACKTEST_PARAMS["train_months"],
            wf_test_months=BACKTEST_PARAMS["test_months"],
            primary_symbol="XAUUSD",
        )

        summary = result.summary()
        valid = meets_constraints(summary)

        if valid:
            valid_count += 1

        results.append({
            "params": params,
            "summary": summary,
            "meets_constraints": valid,
        })

        status = "VALID" if valid else "SKIP"
        print(f"[{i}/{len(param_sets)}] {status} | "
              f"PROX={params['RETEST_PROXIMITY_ATR']:.1f} TP={params['TP_RR']:.1f} BODY={params['REJECTION_MIN_BODY_ATR']:.2f} | "
              f"T={summary['total_trades']} "
              f"WR={summary['win_rate']:.0%} PF={summary['profit_factor']:.2f} "
              f"DD={summary['max_drawdown_pct']:.1f}% RR={summary['avg_rr']:.2f} "
              f"PnL=${summary['net_pnl']:.0f}")

        # Save progress after each combination
        output = {
            "run_at": datetime.now().isoformat(),
            "phase": 1,
            "grid": GRID,
            "fixed_params": FIXED_PARAMS,
            "constraints": CONSTRAINTS,
            "total_combinations": len(param_sets),
            "completed_combinations": i,
            "valid_combinations": valid_count,
            "all_results": results,
        }
        with open(output_path, "w") as f:
            json.dump(output, f, indent=2)
        print(f"  Progress saved ({i}/{len(param_sets)})")

    print("\n" + "="*80)
    print(f"COMPLETE: {valid_count}/{len(results)} valid")
    print("="*80)

    # Sort by net_pnl and add top 10
    valid_results = [r for r in results if r["meets_constraints"]]
    valid_results.sort(key=lambda x: x["summary"]["net_pnl"], reverse=True)

    print("\nTOP 10:")
    for rank, r in enumerate(valid_results[:10], 1):
        p, s = r["params"], r["summary"]
        print(f"#{rank} ${s['net_pnl']:,.0f} | PROX={p['RETEST_PROXIMITY_ATR']:.1f} TP={p['TP_RR']:.1f} BODY={p['REJECTION_MIN_BODY_ATR']:.2f} | "
              f"T={s['total_trades']} WR={s['win_rate']:.0%} PF={s['profit_factor']:.2f} DD={s['max_drawdown_pct']:.1f}%")

    # Final save with top 10
    output = {
        "run_at": datetime.now().isoformat(),
        "phase": 1,
        "grid": GRID,
        "fixed_params": FIXED_PARAMS,
        "constraints": CONSTRAINTS,
        "total_combinations": len(param_sets),
        "completed_combinations": len(results),
        "valid_combinations": valid_count,
        "all_results": results,
        "top_10": valid_results[:10],
    }

    with open(output_path, "w") as f:
        json.dump(output, f, indent=2)

    print(f"\nFinal results saved to {output_path}")


if __name__ == "__main__":
    main()
