"""Risk Sizing Analysis: Monte Carlo Comparison"""
import json

# Load results
risk_04 = json.load(open('backtest_results/20260419_150326_range_breakout_v1_proxy_XAUUSD_2020-01-01_2024-12-31.json'))
risk_05 = json.load(open('backtest_results/20260419_150441_range_breakout_v1_proxy_XAUUSD_2020-01-01_2024-12-31.json'))
risk_06 = json.load(open('backtest_results/20260419_150555_range_breakout_v1_proxy_XAUUSD_2020-01-01_2024-12-31.json'))

print("=" * 80)
print("RANGE BREAKOUT V1: RISK SIZING ANALYSIS")
print("Configuration: London 07-12 + NY 13-17 UTC, 861 trades, 2020-2024")
print("=" * 80)
print()

# Backtest results
print("BACKTEST RESULTS (Single Run)")
print("-" * 80)
print(f"{'Risk %':<10} {'End Balance':<15} {'Net PnL':<15} {'Max DD %':<12} {'Pass 5%?'}")
print("-" * 80)

for risk_pct, data in [('0.4%', risk_04), ('0.5%', risk_05), ('0.6%', risk_06)]:
    balance = data['summary']['end_balance']
    pnl = data['summary']['net_pnl']
    dd = data['summary']['max_drawdown_pct']
    pass_dd = "PASS" if dd <= 5.0 else "FAIL"
    print(f"{risk_pct:<10} ${balance:>12,.2f} ${pnl:>12,.2f} {dd:>10.2f}% {pass_dd}")

print()
print()

# Monte Carlo results
print("MONTE CARLO RESULTS (10,000 simulations)")
print("-" * 80)
print(f"{'Risk %':<10} {'p50 Balance':<15} {'p50 DD %':<12} {'p95 DD %':<12} {'Prob DD>5%'}")
print("-" * 80)

for risk_pct, data in [('0.4%', risk_04), ('0.5%', risk_05), ('0.6%', risk_06)]:
    mc = data['monte_carlo']
    p50_bal = mc['final_balance']['p50']
    p50_dd = mc['max_drawdown_pct']['p50']
    p95_dd = mc['max_drawdown_pct']['p95']
    prob_exceed = mc['prob_exceed_dd_5pct']

    print(f"{risk_pct:<10} ${p50_bal:>12,.2f} {p50_dd:>10.2f}% {p95_dd:>10.2f}% {prob_exceed:>10.1f}%")

print()
print()

# Detailed comparison
print("DETAILED MONTE CARLO METRICS")
print("-" * 80)
print(f"{'Metric':<25} {'0.4%':<20} {'0.5%':<20} {'0.6%':<20}")
print("-" * 80)

metrics = [
    ('Final Balance (p5)', 'final_balance', 'p5'),
    ('Final Balance (p50)', 'final_balance', 'p50'),
    ('Final Balance (p95)', 'final_balance', 'p95'),
    ('Max DD % (p5)', 'max_drawdown_pct', 'p5'),
    ('Max DD % (p50)', 'max_drawdown_pct', 'p50'),
    ('Max DD % (p95)', 'max_drawdown_pct', 'p95'),
    ('Profit Factor (p50)', 'profit_factor', 'p50'),
]

for label, key, percentile in metrics:
    v04 = risk_04['monte_carlo'][key][percentile]
    v05 = risk_05['monte_carlo'][key][percentile]
    v06 = risk_06['monte_carlo'][key][percentile]

    if 'Balance' in label:
        print(f"{label:<25} ${v04:>17,.2f} ${v05:>17,.2f} ${v06:>17,.2f}")
    elif 'Factor' in label:
        print(f"{label:<25} {v04:>19.3f} {v05:>19.3f} {v06:>19.3f}")
    else:
        print(f"{label:<25} {v04:>18.2f}% {v05:>18.2f}% {v06:>18.2f}%")

print()
print("=" * 80)
print("RECOMMENDATION")
print("=" * 80)
print()
print("RECOMMENDED: 0.4% RISK")
print("   - Median DD: 3.68% (safe margin below 5%)")
print("   - p95 DD: 6.16% (acceptable tail risk)")
print("   - Prob DD>5%: 15.8% (low probability)")
print("   - Median balance: $42,052 (4.2x starting capital)")
print()
print("CAUTION: 0.5% RISK")
print("   - Median DD: 5.38% (exceeds 5% threshold)")
print("   - Prob DD>5%: 59.1% (too high for prop firm)")
print("   - Higher returns but unacceptable DD risk")
print()
print("AVOID: 0.6% RISK")
print("   - Median DD: 7.72% (far exceeds 5%)")
print("   - p95 DD: 14.45% (catastrophic)")
print("   - Prob DD>5%: 90.0% (almost certain failure)")
print("   - Not viable for prop firm challenge")
print()
print("=" * 80)
