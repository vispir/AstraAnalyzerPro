"""
Monte Carlo simulation for backtest results.

Randomly resamples trade sequence to estimate distribution of outcomes.

Usage:
    python scripts/monte_carlo_simulation.py --result-file <path> --iterations 1000
"""

import json
import sys
import os
import argparse
import numpy as np
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def load_trades(result_file: str) -> list[dict]:
    """Load trades from backtest result file."""
    with open(result_file, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data.get("trades", [])


def calculate_equity_curve(trades: list[dict], start_balance: float = 10000.0) -> tuple[list[float], float, float]:
    """Calculate equity curve from trade sequence."""
    balance = start_balance
    equity = [balance]
    peak = balance
    max_dd_pct = 0.0

    for trade in trades:
        pnl = trade.get("dollar_pnl", 0.0)
        balance += pnl
        equity.append(balance)

        if balance > peak:
            peak = balance

        dd_pct = ((peak - balance) / peak) * 100 if peak > 0 else 0.0
        max_dd_pct = max(max_dd_pct, dd_pct)

    net_pnl = balance - start_balance
    return equity, net_pnl, max_dd_pct


def monte_carlo_simulation(trades: list[dict], iterations: int = 1000, start_balance: float = 10000.0) -> dict:
    """
    Run Monte Carlo simulation by randomly resampling trade sequence.

    Returns distribution statistics.
    """
    results = {
        "net_pnl": [],
        "max_dd_pct": [],
        "end_balance": [],
    }

    for _ in range(iterations):
        # Randomly resample trades with replacement
        resampled_trades = np.random.choice(trades, size=len(trades), replace=True).tolist()

        equity, net_pnl, max_dd_pct = calculate_equity_curve(resampled_trades, start_balance)

        results["net_pnl"].append(net_pnl)
        results["max_dd_pct"].append(max_dd_pct)
        results["end_balance"].append(equity[-1])

    # Calculate statistics
    net_pnl_arr = np.array(results["net_pnl"])
    max_dd_arr = np.array(results["max_dd_pct"])
    end_balance_arr = np.array(results["end_balance"])

    stats = {
        "iterations": iterations,
        "total_trades": len(trades),
        "start_balance": start_balance,
        # Net PnL stats
        "median_pnl": float(np.median(net_pnl_arr)),
        "mean_pnl": float(np.mean(net_pnl_arr)),
        "pnl_5th_percentile": float(np.percentile(net_pnl_arr, 5)),
        "pnl_95th_percentile": float(np.percentile(net_pnl_arr, 95)),
        "pnl_std": float(np.std(net_pnl_arr)),
        # Max DD stats
        "median_max_dd": float(np.median(max_dd_arr)),
        "mean_max_dd": float(np.mean(max_dd_arr)),
        "max_dd_5th_percentile": float(np.percentile(max_dd_arr, 5)),
        "max_dd_95th_percentile": float(np.percentile(max_dd_arr, 95)),
        # Probabilities
        "prob_profitable": float(np.sum(net_pnl_arr > 0) / iterations),
        "prob_dd_over_5pct": float(np.sum(max_dd_arr > 5.0) / iterations),
        "prob_dd_over_10pct": float(np.sum(max_dd_arr > 10.0) / iterations),
        # End balance stats
        "median_end_balance": float(np.median(end_balance_arr)),
        "end_balance_5th_percentile": float(np.percentile(end_balance_arr, 5)),
        "end_balance_95th_percentile": float(np.percentile(end_balance_arr, 95)),
    }

    return stats


def print_results(stats: dict):
    """Print Monte Carlo results in formatted table."""
    print("\n" + "="*80)
    print("MONTE CARLO SIMULATION RESULTS")
    print("="*80)
    print(f"Iterations: {stats['iterations']:,}")
    print(f"Total Trades: {stats['total_trades']}")
    print(f"Start Balance: ${stats['start_balance']:,.2f}")
    print("="*80)

    print("\n--- NET PnL DISTRIBUTION ---")
    print(f"Median:          ${stats['median_pnl']:>10,.2f}")
    print(f"Mean:            ${stats['mean_pnl']:>10,.2f}")
    print(f"Std Dev:         ${stats['pnl_std']:>10,.2f}")
    print(f"5th Percentile:  ${stats['pnl_5th_percentile']:>10,.2f}  (worst case)")
    print(f"95th Percentile: ${stats['pnl_95th_percentile']:>10,.2f}  (best case)")

    print("\n--- MAX DRAWDOWN DISTRIBUTION ---")
    print(f"Median:          {stats['median_max_dd']:>6.2f}%")
    print(f"Mean:            {stats['mean_max_dd']:>6.2f}%")
    print(f"5th Percentile:  {stats['max_dd_5th_percentile']:>6.2f}%  (best case)")
    print(f"95th Percentile: {stats['max_dd_95th_percentile']:>6.2f}%  (worst case)")

    print("\n--- PROBABILITIES ---")
    print(f"Profitable Year:     {stats['prob_profitable']*100:>6.2f}%")
    print(f"DD > 5%:             {stats['prob_dd_over_5pct']*100:>6.2f}%")
    print(f"DD > 10%:            {stats['prob_dd_over_10pct']*100:>6.2f}%")

    print("\n--- END BALANCE DISTRIBUTION ---")
    print(f"Median:          ${stats['median_end_balance']:>10,.2f}")
    print(f"5th Percentile:  ${stats['end_balance_5th_percentile']:>10,.2f}")
    print(f"95th Percentile: ${stats['end_balance_95th_percentile']:>10,.2f}")

    print("="*80 + "\n")


def main():
    parser = argparse.ArgumentParser(description="Monte Carlo simulation for backtest results")
    parser.add_argument("--result-file", type=str, required=True, help="Path to backtest result JSON")
    parser.add_argument("--iterations", type=int, default=1000, help="Number of Monte Carlo iterations")
    parser.add_argument("--start-balance", type=float, default=10000.0, help="Starting balance")
    args = parser.parse_args()

    if not Path(args.result_file).exists():
        print(f"ERROR: Result file not found: {args.result_file}")
        sys.exit(1)

    print(f"Loading trades from {args.result_file}...")
    trades = load_trades(args.result_file)

    if not trades:
        print("ERROR: No trades found in result file")
        sys.exit(1)

    print(f"Running Monte Carlo simulation ({args.iterations:,} iterations)...")
    stats = monte_carlo_simulation(trades, iterations=args.iterations, start_balance=args.start_balance)

    print_results(stats)

    # Save results
    output_file = args.result_file.replace(".json", "_monte_carlo.json")
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(stats, f, indent=2)
    print(f"Results saved to {output_file}")


if __name__ == "__main__":
    main()
