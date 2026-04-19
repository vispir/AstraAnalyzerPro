"""
Analyze all BTCUSD strategy backtest results.

Collects results from backtest_results/ and generates summary table.
Filters by: DD < 5%, PF > 1.3, WR > 50%
"""

import json
from pathlib import Path
from datetime import datetime

def main():
    results_dir = Path("backtest_results")

    strategies = [
        "sweep_reversal_v1",
        "sweep_reversal_v2",
        "sweep_reversal_v3",
        "sweep_reversal_v4",
        "sweep_reversal_v4a",
        "legacy_v1",
        "smc_fvg_v1",
    ]

    results = []

    for strat in strategies:
        # Find latest result file for this strategy on BTCUSD
        pattern = f"*_{strat}_proxy_BTCUSD_2020-01-01_2024-12-31.json"
        files = sorted(results_dir.glob(pattern), key=lambda p: p.stat().st_mtime, reverse=True)

        if not files:
            print(f"WARNING: No results found for {strat}")
            continue

        latest = files[0]

        try:
            with open(latest, 'r', encoding='utf-8') as f:
                data = json.load(f)
                summary = data['summary']

                results.append({
                    'strategy': strat,
                    'trades': summary['total_trades'],
                    'win_rate': summary['win_rate'],
                    'profit_factor': summary['profit_factor'],
                    'max_dd': summary['max_drawdown_pct'],
                    'avg_rr': summary['avg_rr'],
                    'net_pnl': summary['net_pnl'],
                })
        except Exception as e:
            print(f"ERROR reading {latest.name}: {e}")

    # Filter by criteria
    passing = [r for r in results if r['max_dd'] < 5.0 and r['profit_factor'] > 1.3 and r['win_rate'] > 0.50]

    # Sort by PF descending
    passing.sort(key=lambda x: x['profit_factor'], reverse=True)

    print("\n" + "="*90)
    print("BTCUSD STRATEGY COMPARISON (2020-2024)")
    print("="*90)
    print(f"Criteria: DD < 5%, PF > 1.3, WR > 50%")
    print(f"Total strategies tested: {len(results)}")
    print(f"Passing criteria: {len(passing)}")
    print("="*90)

    if passing:
        print("\nPASSING STRATEGIES:")
        print("-"*90)
        print(f"{'Strategy':<25} {'Trades':>7} {'WR':>7} {'PF':>6} {'DD':>7} {'Avg RR':>7} {'PnL':>12}")
        print("-"*90)

        for r in passing:
            print(f"{r['strategy']:<25} {r['trades']:>7} {r['win_rate']:>6.1%} {r['profit_factor']:>6.2f} "
                  f"{r['max_dd']:>6.2f}% {r['avg_rr']:>7.2f} ${r['net_pnl']:>11,.0f}")
    else:
        print("\nNO STRATEGIES PASSED CRITERIA")

    print("\n" + "="*90)
    print("ALL RESULTS:")
    print("-"*90)
    print(f"{'Strategy':<25} {'Trades':>7} {'WR':>7} {'PF':>6} {'DD':>7} {'Avg RR':>7} {'PnL':>12}")
    print("-"*90)

    results.sort(key=lambda x: x['profit_factor'], reverse=True)
    for r in results:
        status = "✓" if (r['max_dd'] < 5.0 and r['profit_factor'] > 1.3 and r['win_rate'] > 0.50) else "✗"
        print(f"{r['strategy']:<25} {r['trades']:>7} {r['win_rate']:>6.1%} {r['profit_factor']:>6.2f} "
              f"{r['max_dd']:>6.2f}% {r['avg_rr']:>7.2f} ${r['net_pnl']:>11,.0f} {status}")

    print("="*90)

if __name__ == "__main__":
    main()
