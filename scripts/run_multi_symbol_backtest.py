"""
Multi-symbol backtest runner with aggregated summary.

Runs backtest for multiple symbols sequentially and aggregates results.

Usage:
    python scripts/run_multi_symbol_backtest.py --strategy range_breakout_v1
"""

import subprocess
import json
import os
import sys
import argparse
from datetime import datetime
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


SYMBOLS = ["XAUUSD", "XAGUSD", "EURUSD", "BTCUSD"]
PARAMS = {
    "start": "2020-01-01",
    "end": "2024-12-31",
    "mode": "proxy",
    "strategy": "breakout_retest_v1",
    "balance": 10000.0,
    "train_months": 6,
    "test_months": 1,
}


def run_backtest_for_symbol(symbol: str) -> dict | None:
    """Run backtest for a single symbol and return summary."""
    print(f"\n{'='*60}")
    print(f"Running backtest for {symbol}...")
    print(f"{'='*60}\n")

    cmd = [
        "python", "scripts/run_backtest.py",
        "--start", PARAMS["start"],
        "--end", PARAMS["end"],
        "--mode", PARAMS["mode"],
        "--strategy", PARAMS["strategy"],
        "--primary-symbol", symbol,
        "--cross-symbols", "none",
        "--balance", str(PARAMS["balance"]),
        "--train-months", str(PARAMS["train_months"]),
        "--test-months", str(PARAMS["test_months"]),
    ]

    result = subprocess.run(cmd, capture_output=False, text=True)

    if result.returncode != 0:
        print(f"ERROR: Backtest failed for {symbol}")
        return None

    results_dir = Path("backtest_results")
    pattern = f"*_{PARAMS['strategy']}_{PARAMS['mode']}_{symbol}_{PARAMS['start']}_{PARAMS['end']}.json"

    matching_files = sorted(results_dir.glob(pattern), key=lambda p: p.stat().st_mtime, reverse=True)

    if not matching_files:
        print(f"ERROR: No result file found for {symbol}")
        return None

    latest_file = matching_files[0]

    with open(latest_file, "r", encoding="utf-8") as f:
        data = json.load(f)

    return data["summary"]


def aggregate_results(results: dict[str, dict]) -> dict:
    """Aggregate results from multiple symbols."""
    rows = []
    total_trades = 0
    total_net_pnl = 0.0
    weighted_win_rate = 0.0
    weighted_pf = 0.0
    weighted_avg_rr = 0.0
    max_dd_values = []

    for symbol, summary in results.items():
        trades = summary["total_trades"]
        win_rate = summary["win_rate"]
        pf = summary["profit_factor"]
        max_dd = summary["max_drawdown_pct"]
        avg_rr = summary["avg_rr"]
        net_pnl = summary["net_pnl"]
        end_balance = summary["end_balance"]

        rows.append({
            "pair": symbol,
            "total_trades": trades,
            "win_rate": win_rate,
            "profit_factor": pf,
            "max_drawdown_pct": max_dd,
            "avg_rr": avg_rr,
            "net_pnl": net_pnl,
            "end_balance": end_balance,
        })

        total_trades += trades
        total_net_pnl += net_pnl
        weighted_win_rate += win_rate * trades
        weighted_pf += pf * trades
        weighted_avg_rr += avg_rr * trades
        max_dd_values.append(max_dd)

    if total_trades > 0:
        avg_win_rate = weighted_win_rate / total_trades
        avg_pf = weighted_pf / total_trades
        avg_avg_rr = weighted_avg_rr / total_trades
        avg_max_dd = sum(max_dd_values) / len(max_dd_values)
    else:
        avg_win_rate = avg_pf = avg_avg_rr = avg_max_dd = 0.0

    rows.append({
        "pair": "TOTAL",
        "total_trades": total_trades,
        "win_rate": avg_win_rate,
        "profit_factor": avg_pf,
        "max_drawdown_pct": avg_max_dd,
        "avg_rr": avg_avg_rr,
        "net_pnl": total_net_pnl,
        "end_balance": PARAMS["balance"] + total_net_pnl,
    })

    return {"rows": rows, "params": PARAMS}


def print_table(data: dict):
    """Print results as formatted table."""
    rows = data["rows"]

    print("\n" + "="*100)
    print("MULTI-SYMBOL BACKTEST SUMMARY")
    print("="*100)
    print(f"Strategy: {PARAMS['strategy']} | Period: {PARAMS['start']} to {PARAMS['end']} | Mode: {PARAMS['mode']}")
    print("="*100)

    header = f"{'Pair':<10} {'Trades':>8} {'Win Rate':>10} {'PF':>8} {'Max DD %':>10} {'Avg RR':>8} {'Net PnL':>12} {'End Balance':>14}"
    print(header)
    print("-"*100)

    for row in rows:
        is_total = row["pair"] == "TOTAL"
        line = (
            f"{row['pair']:<10} "
            f"{row['total_trades']:>8} "
            f"{row['win_rate']:>9.2f}% "
            f"{row['profit_factor']:>8.3f} "
            f"{row['max_drawdown_pct']:>9.2f}% "
            f"{row['avg_rr']:>8.2f} "
            f"${row['net_pnl']:>11,.2f} "
            f"${row['end_balance']:>13,.2f}"
        )

        if is_total:
            print("-"*100)
        print(line)

    print("="*100 + "\n")


def main():
    parser = argparse.ArgumentParser(description="Multi-symbol backtest runner")
    parser.add_argument("--strategy", type=str, default="breakout_retest_v1", help="Strategy ID")
    parser.add_argument("--start", type=str, default="2020-01-01", help="Start date")
    parser.add_argument("--end", type=str, default="2024-12-31", help="End date")
    parser.add_argument("--mode", type=str, default="proxy", help="Backtest mode")
    args = parser.parse_args()

    PARAMS["strategy"] = args.strategy
    PARAMS["start"] = args.start
    PARAMS["end"] = args.end
    PARAMS["mode"] = args.mode

    results = {}

    for symbol in SYMBOLS:
        summary = run_backtest_for_symbol(symbol)
        if summary:
            results[symbol] = summary
        else:
            print(f"Skipping {symbol} due to error")

    if not results:
        print("ERROR: No successful backtests")
        sys.exit(1)

    aggregated = aggregate_results(results)

    print_table(aggregated)

    output_path = "backtest_summary.json"
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(aggregated, f, indent=2)

    print(f"Summary saved to {output_path}")


if __name__ == "__main__":
    main()
