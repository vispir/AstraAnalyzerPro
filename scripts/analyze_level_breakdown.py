"""
Analyze breakout_retest_v1 trades by level type.
Shows which level types contribute quality trades vs noise.
"""

import json
import sys
from collections import defaultdict

def analyze_trades_by_level(json_path):
    with open(json_path, 'r') as f:
        data = json.load(f)

    trades = data['trades']

    # Group by level_type
    by_level = defaultdict(list)
    for trade in trades:
        level_type = trade.get('level_type', 'unknown')
        by_level[level_type].append(trade)

    print("="*80)
    print("BREAKOUT_RETEST_V1 BREAKDOWN BY LEVEL TYPE")
    print("="*80)
    print(f"Total trades: {len(trades)}")
    print(f"Overall WR: {data['summary']['win_rate']:.1%}")
    print(f"Overall avg_rr: {data['summary']['avg_rr']:.2f}")
    print()

    # Calculate stats per level type
    results = []
    for level_type, level_trades in by_level.items():
        wins = sum(1 for t in level_trades if t['pnl'] > 0)
        total = len(level_trades)
        wr = wins / total if total > 0 else 0

        # Calculate RR for each trade
        rr_values = []
        for t in level_trades:
            risk = abs(t['entry'] - t['stop_loss'])
            if risk > 0:
                rr = t['pnl'] / risk
                rr_values.append(rr)

        avg_rr = sum(rr_values) / len(rr_values) if rr_values else 0
        total_pnl = sum(t['pnl'] for t in level_trades)

        results.append({
            'level_type': level_type,
            'trades': total,
            'wr': wr,
            'avg_rr': avg_rr,
            'total_pnl': total_pnl,
        })

    # Sort by number of trades (descending)
    results.sort(key=lambda x: x['trades'], reverse=True)

    print(f"{'Level Type':<20} {'Trades':>8} {'WR':>8} {'Avg RR':>10} {'Total PnL':>12}")
    print("-"*80)

    for r in results:
        print(f"{r['level_type']:<20} {r['trades']:>8} {r['wr']:>7.1%} {r['avg_rr']:>10.2f} ${r['total_pnl']:>10.2f}")

    print()
    print("="*80)
    print("QUALITY FILTER RECOMMENDATIONS")
    print("="*80)

    # Identify good vs bad level types
    good = [r for r in results if r['wr'] >= 0.50 and r['avg_rr'] >= 0.20]
    bad = [r for r in results if r['wr'] < 0.45 or r['avg_rr'] < 0.0]

    if good:
        print("\nKEEP (WR>=50% AND avg_rr>=0.20):")
        for r in good:
            print(f"  - {r['level_type']}: {r['trades']} trades, WR={r['wr']:.1%}, RR={r['avg_rr']:.2f}")

    if bad:
        print("\nREMOVE (WR<45% OR avg_rr<0):")
        for r in bad:
            print(f"  - {r['level_type']}: {r['trades']} trades, WR={r['wr']:.1%}, RR={r['avg_rr']:.2f}")

    # Calculate what happens if we remove bad levels
    if bad:
        bad_level_types = {r['level_type'] for r in bad}
        filtered_trades = [t for t in trades if t.get('level_type') not in bad_level_types]

        if filtered_trades:
            filtered_wins = sum(1 for t in filtered_trades if t['pnl'] > 0)
            filtered_wr = filtered_wins / len(filtered_trades)

            # Calculate RR for filtered trades
            filtered_rr_values = []
            for t in filtered_trades:
                risk = abs(t['entry'] - t['stop_loss'])
                if risk > 0:
                    rr = t['pnl'] / risk
                    filtered_rr_values.append(rr)

            filtered_avg_rr = sum(filtered_rr_values) / len(filtered_rr_values) if filtered_rr_values else 0
            filtered_pnl = sum(t['pnl'] for t in filtered_trades)

            print("\nPROJECTED AFTER REMOVING BAD LEVELS:")
            print(f"  Trades: {len(trades)} -> {len(filtered_trades)} ({len(filtered_trades)/len(trades):.1%})")
            print(f"  WR: {data['summary']['win_rate']:.1%} -> {filtered_wr:.1%}")
            print(f"  Avg RR: {data['summary']['avg_rr']:.2f} -> {filtered_avg_rr:.2f}")
            print(f"  Net PnL: ${data['summary']['net_pnl']:.2f} -> ${filtered_pnl:.2f}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python analyze_level_breakdown.py <backtest_result.json>")
        sys.exit(1)

    analyze_trades_by_level(sys.argv[1])
