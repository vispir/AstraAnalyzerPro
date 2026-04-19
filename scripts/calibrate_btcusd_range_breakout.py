"""
Calibrate range_breakout_v1 for BTCUSD.

Grid search over MAX_RANGE_ATR, STOP_BUFFER_ATR, TRAIL_DISTANCE_ATR
to find parameters that reduce DD while maintaining profitability.

Target: DD < 10%, PF > 1.4

Usage:
    python scripts/calibrate_btcusd_range_breakout.py
"""

import subprocess
import json
import sys
import os
from pathlib import Path
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from astra_v2 import config

# Grid search parameters
PARAM_GRID = {
    "MAX_RANGE_ATR": [1.5, 2.0, 3.0],
    "STOP_BUFFER_ATR": [0.5, 1.0, 1.5],
    "TRAIL_DISTANCE_ATR": [0.3, 0.5, 0.8],
}

BACKTEST_PARAMS = {
    "start": "2020-01-01",
    "end": "2024-12-31",
    "mode": "proxy",
    "strategy": "range_breakout_v1",
    "symbol": "BTCUSD",
}


def update_config_params(max_range_atr: float, stop_buffer_atr: float, trail_distance_atr: float):
    """Update config.py with new parameters."""
    config_path = Path("astra_v2/config.py")

    with open(config_path, "r", encoding="utf-8") as f:
        content = f.read()

    # Update RANGE_BREAKOUT_V1_MAX_RANGE_ATR
    content = content.replace(
        f"RANGE_BREAKOUT_V1_CONSOLIDATION_LOOKBACK = {config.RANGE_BREAKOUT_V1_CONSOLIDATION_LOOKBACK}",
        f"RANGE_BREAKOUT_V1_CONSOLIDATION_LOOKBACK = {config.RANGE_BREAKOUT_V1_CONSOLIDATION_LOOKBACK}"
    )

    # Find and replace MAX_RANGE_ATR in _detect_range comment
    import re
    # Update the hardcoded 3.0 in the range detection logic
    content = re.sub(
        r'if range_size > [\d.]+\s*\*\s*atr:',
        f'if range_size > {max_range_atr} * atr:',
        content
    )

    # Update STOP_BUFFER_ATR
    content = re.sub(
        r'RANGE_BREAKOUT_V1_STOP_BUFFER_ATR = [\d.]+',
        f'RANGE_BREAKOUT_V1_STOP_BUFFER_ATR = {stop_buffer_atr}',
        content
    )

    # Update TRAIL_DISTANCE_ATR
    content = re.sub(
        r'TRAIL_DISTANCE_ATR = [\d.]+',
        f'TRAIL_DISTANCE_ATR = {trail_distance_atr}',
        content
    )

    with open(config_path, "w", encoding="utf-8") as f:
        f.write(content)


def update_strategy_max_range(max_range_atr: float):
    """Update hardcoded MAX_RANGE_ATR in range_breakout_v1.py."""
    strategy_path = Path("astra_v2/strategies/range_breakout_v1.py")

    with open(strategy_path, "r", encoding="utf-8") as f:
        content = f.read()

    # Update the hardcoded 3.0 in _detect_range
    import re
    content = re.sub(
        r'if range_size > [\d.]+\s*\*\s*atr:',
        f'if range_size > {max_range_atr} * atr:',
        content
    )

    with open(strategy_path, "w", encoding="utf-8") as f:
        f.write(content)


def run_backtest() -> dict | None:
    """Run backtest and return summary."""
    cmd = [
        "python", "scripts/run_backtest.py",
        "--start", BACKTEST_PARAMS["start"],
        "--end", BACKTEST_PARAMS["end"],
        "--mode", BACKTEST_PARAMS["mode"],
        "--strategy", BACKTEST_PARAMS["strategy"],
        "--primary-symbol", BACKTEST_PARAMS["symbol"],
        "--cross-symbols", "none",
    ]

    result = subprocess.run(cmd, capture_output=True, text=True)

    if result.returncode != 0:
        print(f"ERROR: Backtest failed")
        print(result.stderr)
        return None

    # Find latest result file
    results_dir = Path("backtest_results")
    pattern = f"*_{BACKTEST_PARAMS['strategy']}_{BACKTEST_PARAMS['mode']}_{BACKTEST_PARAMS['symbol']}_*.json"
    matching_files = sorted(results_dir.glob(pattern), key=lambda p: p.stat().st_mtime, reverse=True)

    if not matching_files:
        print(f"ERROR: No result file found")
        return None

    with open(matching_files[0], "r", encoding="utf-8") as f:
        data = json.load(f)

    return data["summary"]


def main():
    results = []
    total_runs = len(PARAM_GRID["MAX_RANGE_ATR"]) * len(PARAM_GRID["STOP_BUFFER_ATR"]) * len(PARAM_GRID["TRAIL_DISTANCE_ATR"])
    run_count = 0

    print("="*80)
    print(f"BTCUSD CALIBRATION: range_breakout_v1")
    print(f"Total runs: {total_runs}")
    print("="*80)

    for max_range in PARAM_GRID["MAX_RANGE_ATR"]:
        for stop_buffer in PARAM_GRID["STOP_BUFFER_ATR"]:
            for trail_distance in PARAM_GRID["TRAIL_DISTANCE_ATR"]:
                run_count += 1

                print(f"\n[{run_count}/{total_runs}] Testing: MAX_RANGE={max_range}, STOP_BUFFER={stop_buffer}, TRAIL={trail_distance}")

                # Update config
                update_config_params(max_range, stop_buffer, trail_distance)
                update_strategy_max_range(max_range)

                # Run backtest
                summary = run_backtest()

                if summary:
                    result = {
                        "max_range_atr": max_range,
                        "stop_buffer_atr": stop_buffer,
                        "trail_distance_atr": trail_distance,
                        "total_trades": summary["total_trades"],
                        "win_rate": summary["win_rate"],
                        "profit_factor": summary["profit_factor"],
                        "max_drawdown_pct": summary["max_drawdown_pct"],
                        "avg_rr": summary["avg_rr"],
                        "net_pnl": summary["net_pnl"],
                    }
                    results.append(result)

                    print(f"  Trades: {result['total_trades']}, WR: {result['win_rate']:.1%}, "
                          f"PF: {result['profit_factor']:.2f}, DD: {result['max_drawdown_pct']:.2f}%, "
                          f"PnL: ${result['net_pnl']:,.0f}")

    # Filter by constraints: DD < 10%, PF > 1.4
    valid_results = [r for r in results if r["max_drawdown_pct"] < 10.0 and r["profit_factor"] > 1.4]

    # Sort by net_pnl descending
    valid_results.sort(key=lambda x: x["net_pnl"], reverse=True)

    print("\n" + "="*80)
    print("TOP 3 RESULTS (DD < 10%, PF > 1.4)")
    print("="*80)

    if not valid_results:
        print("No configurations met the constraints (DD < 10%, PF > 1.4)")
        print("\nAll results sorted by PnL:")
        results.sort(key=lambda x: x["net_pnl"], reverse=True)
        for i, r in enumerate(results[:5], 1):
            print(f"\n{i}. MAX_RANGE={r['max_range_atr']}, STOP_BUFFER={r['stop_buffer_atr']}, TRAIL={r['trail_distance_atr']}")
            print(f"   Trades: {r['total_trades']}, WR: {r['win_rate']:.1%}, PF: {r['profit_factor']:.2f}")
            print(f"   DD: {r['max_drawdown_pct']:.2f}%, Avg RR: {r['avg_rr']:.2f}, PnL: ${r['net_pnl']:,.0f}")
    else:
        for i, r in enumerate(valid_results[:3], 1):
            print(f"\n{i}. MAX_RANGE={r['max_range_atr']}, STOP_BUFFER={r['stop_buffer_atr']}, TRAIL={r['trail_distance_atr']}")
            print(f"   Trades: {r['total_trades']}, WR: {r['win_rate']:.1%}, PF: {r['profit_factor']:.2f}")
            print(f"   DD: {r['max_drawdown_pct']:.2f}%, Avg RR: {r['avg_rr']:.2f}, PnL: ${r['net_pnl']:,.0f}")

    # Save all results
    output_file = f"calibration_btcusd_range_breakout_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump({
            "valid_results": valid_results,
            "all_results": results,
            "param_grid": PARAM_GRID,
        }, f, indent=2)

    print(f"\n\nResults saved to {output_file}")
    print("="*80)


if __name__ == "__main__":
    main()
