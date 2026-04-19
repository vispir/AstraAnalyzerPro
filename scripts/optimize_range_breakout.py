"""
Optimize range_breakout_v1 parameters for XAUUSD.

Grid search over:
- MAX_RANGE_ATR: [2.0, 3.0, 4.0, 5.0, 6.0]
- STOP_BUFFER_ATR: [0.3, 0.5, 0.7, 1.0]
- TP_RR: [1.5, 2.0, 2.5, 3.0]
- CONSOLIDATION_LOOKBACK: [15, 20, 25, 30]

Target: PF >= 1.5, Max DD < 10%, Daily DD < 5%, maximize PnL
"""

import sys
import os
import json
from datetime import datetime
from itertools import product

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from astra_v2 import config
from astra_v2.data.dukascopy import load_timeframe
from astra_v2.data.fred_client import fetch_all as fetch_fred_bulk
from astra_v2.data.external import fetch_yfinance_bulk, fetch_cot_gold
from astra_v2.backtest.engine import run_backtest
from astra_v2.strategies import get_strategy

# Parameter grid (reduced for faster testing)
MAX_RANGE_ATR_VALUES = [3.0, 4.0, 6.0]
STOP_BUFFER_ATR_VALUES = [0.5, 1.0]
TP_RR_VALUES = [2.0, 2.5, 3.0]
LOOKBACK_VALUES = [20, 25]

START = "2020-01-01"
END = "2026-04-18"
BALANCE = 10_000

def run_optimization():
    print("Loading data...", flush=True)
    bars = load_timeframe("M15", start=START, end=END, symbol="XAUUSD")
    print(f"Loaded {len(bars)} bars", flush=True)
    fred_df = fetch_fred_bulk(START, END)
    yfinance_df = fetch_yfinance_bulk(START, END, cache_only=True)
    cot_df = fetch_cot_gold(cache_only=True)
    print("Data loaded", flush=True)

    strategy = get_strategy("range_breakout_v1")

    results = []
    total_runs = len(MAX_RANGE_ATR_VALUES) * len(STOP_BUFFER_ATR_VALUES) * len(TP_RR_VALUES) * len(LOOKBACK_VALUES)
    run_count = 0

    print(f"Starting optimization: {total_runs} combinations", flush=True)

    for max_range, stop_buffer, tp_rr, lookback in product(
        MAX_RANGE_ATR_VALUES, STOP_BUFFER_ATR_VALUES, TP_RR_VALUES, LOOKBACK_VALUES
    ):
        run_count += 1

        # Update config
        config.RANGE_BREAKOUT_V1_MAX_RANGE_ATR = max_range
        config.RANGE_BREAKOUT_V1_STOP_BUFFER_ATR = stop_buffer
        config.RANGE_BREAKOUT_V1_TP_RR = tp_rr
        config.RANGE_BREAKOUT_V1_CONSOLIDATION_LOOKBACK = lookback

        try:
            result = run_backtest(
                bars=bars,
                fred_df=fred_df,
                yfinance_df=yfinance_df,
                cot_df=cot_df,
                mode="proxy",
                strategy_id="range_breakout_v1",
                start_balance=BALANCE,
                primary_symbol="XAUUSD",
            )

            summary = result.summary()

            # Filter: PF >= 1.5, Max DD < 10%, Daily DD < 5%
            passes_filter = (
                summary["profit_factor"] >= 1.5 and
                summary["max_drawdown_pct"] < 10.0 and
                summary["max_daily_dd_pct"] < 5.0
            )

            results.append({
                "params": {
                    "max_range_atr": max_range,
                    "stop_buffer_atr": stop_buffer,
                    "tp_rr": tp_rr,
                    "lookback": lookback,
                },
                "summary": summary,
                "passes_filter": passes_filter,
            })

            if passes_filter:
                print(f"[{run_count}/{total_runs}] ✓ PASS: PF={summary['profit_factor']:.3f}, "
                      f"DD={summary['max_drawdown_pct']:.2f}%, PnL=${summary['net_pnl']:,.0f}")
            else:
                print(f"[{run_count}/{total_runs}] ✗ FAIL: PF={summary['profit_factor']:.3f}, "
                      f"DD={summary['max_drawdown_pct']:.2f}%")

        except Exception as e:
            print(f"[{run_count}/{total_runs}] ERROR: {e}")
            continue

    # Sort by PnL descending
    results.sort(key=lambda x: x["summary"]["net_pnl"], reverse=True)

    # Save results
    output_path = f"backtest_results/range_breakout_v1_optimization_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    with open(output_path, "w") as f:
        json.dump(results, f, indent=2)

    print(f"\n=== OPTIMIZATION COMPLETE ===")
    print(f"Total runs: {total_runs}")
    print(f"Passed filter: {sum(1 for r in results if r['passes_filter'])}")
    print(f"Results saved to: {output_path}")

    # Print top 5
    print("\n=== TOP 5 BY PnL ===")
    for i, r in enumerate(results[:5], 1):
        p = r["params"]
        s = r["summary"]
        print(f"{i}. MAX_RANGE={p['max_range_atr']}, STOP={p['stop_buffer_atr']}, "
              f"TP_RR={p['tp_rr']}, LOOKBACK={p['lookback']}")
        print(f"   PnL=${s['net_pnl']:,.0f}, PF={s['profit_factor']:.3f}, "
              f"WR={s['win_rate']:.1%}, DD={s['max_drawdown_pct']:.2f}%, "
              f"Daily DD={s['max_daily_dd_pct']:.2f}%")
        print(f"   Passes: {'✓' if r['passes_filter'] else '✗'}")

if __name__ == "__main__":
    run_optimization()
