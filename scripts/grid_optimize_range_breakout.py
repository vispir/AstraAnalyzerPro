"""
Range Breakout V1 - Grid Optimization (240 combinations)
Saves results after each run to avoid data loss.
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

# Coarse grid parameters
MAX_RANGE_ATR_VALUES = [2.0, 4.0, 6.0, 8.0, 10.0]
STOP_BUFFER_ATR_VALUES = [0.3, 0.7, 1.0, 1.5]
TP_RR_VALUES = [1.5, 2.5, 3.5, 5.0]
LOOKBACK_VALUES = [15, 25, 35]

START = "2020-01-01"
END = "2026-04-18"
BALANCE = 10_000

OUTPUT_FILE = f"backtest_results/range_breakout_v1_grid_optimization_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"

def save_results(results):
    """Save results to file after each iteration"""
    with open(OUTPUT_FILE, "w") as f:
        json.dump(results, f, indent=2)

def run_optimization():
    print(f"=== Range Breakout V1 Grid Optimization ===", flush=True)
    print(f"Start time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}", flush=True)
    print(f"Output file: {OUTPUT_FILE}", flush=True)

    print("\nLoading data...", flush=True)
    bars = load_timeframe("M15", start=START, end=END, symbol="XAUUSD")
    print(f"Loaded {len(bars):,} bars", flush=True)

    fred_df = fetch_fred_bulk(START, END)
    yfinance_df = fetch_yfinance_bulk(START, END, cache_only=True)
    cot_df = fetch_cot_gold(cache_only=True)
    print("Data loaded successfully", flush=True)

    results = []
    total_runs = len(MAX_RANGE_ATR_VALUES) * len(STOP_BUFFER_ATR_VALUES) * len(TP_RR_VALUES) * len(LOOKBACK_VALUES)
    run_count = 0

    print(f"\nStarting optimization: {total_runs} combinations", flush=True)
    print("=" * 80, flush=True)

    for max_range, stop_buffer, tp_rr, lookback in product(
        MAX_RANGE_ATR_VALUES, STOP_BUFFER_ATR_VALUES, TP_RR_VALUES, LOOKBACK_VALUES
    ):
        run_count += 1
        start_time = datetime.now()

        # Update config
        config.RANGE_BREAKOUT_V1_MAX_RANGE_ATR = max_range
        config.RANGE_BREAKOUT_V1_STOP_BUFFER_ATR = stop_buffer
        config.RANGE_BREAKOUT_V1_TP_RR = tp_rr
        config.RANGE_BREAKOUT_V1_CONSOLIDATION_LOOKBACK = lookback

        print(f"\n[{run_count}/{total_runs}] Testing: MAX_RANGE={max_range}, STOP={stop_buffer}, TP_RR={tp_rr}, LOOKBACK={lookback}", flush=True)

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
            elapsed = (datetime.now() - start_time).total_seconds()

            # Filter: PF >= 1.5, Max DD < 10%, Daily DD < 5%
            passes_filter = (
                summary["profit_factor"] >= 1.5 and
                summary["max_drawdown_pct"] < 10.0 and
                summary["max_daily_dd_pct"] < 5.0
            )

            result_entry = {
                "run": run_count,
                "params": {
                    "max_range_atr": float(max_range),
                    "stop_buffer_atr": float(stop_buffer),
                    "tp_rr": float(tp_rr),
                    "lookback": int(lookback),
                },
                "summary": summary,
                "passes_filter": bool(passes_filter),
                "elapsed_seconds": float(elapsed),
            }

            results.append(result_entry)

            # Save after each run
            save_results(results)

            status = "PASS" if passes_filter else "FAIL"
            print(f"[{run_count}/{total_runs}] {status}: PF={summary['profit_factor']:.3f}, "
                  f"DD={summary['max_drawdown_pct']:.2f}%, Daily DD={summary['max_daily_dd_pct']:.2f}%, "
                  f"PnL=${summary['net_pnl']:,.0f}, Trades={summary['total_trades']}, "
                  f"Time={elapsed:.1f}s", flush=True)

        except Exception as e:
            print(f"[{run_count}/{total_runs}] ERROR: {str(e)}", flush=True)
            results.append({
                "run": run_count,
                "params": {
                    "max_range_atr": max_range,
                    "stop_buffer_atr": stop_buffer,
                    "tp_rr": tp_rr,
                    "lookback": lookback,
                },
                "error": str(e),
            })
            save_results(results)
            continue

    # Final summary
    print("\n" + "=" * 80, flush=True)
    print("=== OPTIMIZATION COMPLETE ===", flush=True)
    print(f"End time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}", flush=True)
    print(f"Total runs: {total_runs}", flush=True)

    passed = [r for r in results if r.get("passes_filter", False)]
    print(f"Passed filter: {len(passed)}", flush=True)
    print(f"Results saved to: {OUTPUT_FILE}", flush=True)

    # Sort by PnL descending
    valid_results = [r for r in results if "summary" in r]
    valid_results.sort(key=lambda x: x["summary"]["net_pnl"], reverse=True)

    # Print top 5
    print("\n=== TOP 5 BY PnL ===", flush=True)
    for i, r in enumerate(valid_results[:5], 1):
        p = r["params"]
        s = r["summary"]
        status = "PASS" if r["passes_filter"] else "FAIL"
        print(f"\n{i}. [{status}] MAX_RANGE={p['max_range_atr']}, STOP={p['stop_buffer_atr']}, "
              f"TP_RR={p['tp_rr']}, LOOKBACK={p['lookback']}", flush=True)
        print(f"   PnL=${s['net_pnl']:,.0f}, PF={s['profit_factor']:.3f}, "
              f"WR={s['win_rate']:.1%}, Trades={s['total_trades']}", flush=True)
        print(f"   DD={s['max_drawdown_pct']:.2f}%, Daily DD={s['max_daily_dd_pct']:.2f}%", flush=True)

if __name__ == "__main__":
    run_optimization()
