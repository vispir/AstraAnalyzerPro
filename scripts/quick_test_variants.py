"""
Quick test of 3 key profit improvement variants
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Just modify combined_session_backtest and run 3 times
variants = [
    {"name": "Baseline (Risk $100)", "risk": 100, "tp_mult": 1.0, "trailing": True},
    {"name": "Risk $150", "risk": 150, "tp_mult": 1.0, "trailing": True},
    {"name": "Risk $200", "risk": 200, "tp_mult": 1.0, "trailing": True},
]

results = []

for variant in variants:
    print(f"\nTesting: {variant['name']}...")

    # Modify parameters in combined_session_backtest.py
    with open('scripts/combined_session_backtest.py', 'r') as f:
        content = f.read()

    # Save original
    original_content = content

    # Modify RISK_PER_TRADE
    content = content.replace('RISK_PER_TRADE = 100', f'RISK_PER_TRADE = {variant["risk"]}')

    # Write modified
    with open('scripts/combined_session_backtest.py', 'w') as f:
        f.write(content)

    # Run backtest
    from scripts.combined_session_backtest import run_combined_backtest
    import importlib
    import scripts.combined_session_backtest
    importlib.reload(scripts.combined_session_backtest)

    trades_df = scripts.combined_session_backtest.run_combined_backtest()

    # Calculate stats
    wins = trades_df[trades_df['pnl'] > 0]
    losses = trades_df[trades_df['pnl'] <= 0]
    total_profit = wins['pnl'].sum() if len(wins) > 0 else 0
    total_loss = abs(losses['pnl'].sum()) if len(losses) > 0 else 0
    pf = total_profit / total_loss if total_loss > 0 else 0

    # Get DD from output (need to capture it)
    # For now, approximate based on balance curve
    balance_curve = [10000]
    for pnl in trades_df['pnl']:
        balance_curve.append(balance_curve[-1] + pnl)

    peak = 10000
    max_dd = 0
    for bal in balance_curve:
        if bal > peak:
            peak = bal
        dd = (peak - bal) / peak * 100
        if dd > max_dd:
            max_dd = dd

    results.append({
        'name': variant['name'],
        'pnl': trades_df['pnl'].sum(),
        'trades': len(trades_df),
        'pf': pf,
        'dd': max_dd,
        'passes': max_dd < 10.0 and len(trades_df) >= 150
    })

    # Restore original
    with open('scripts/combined_session_backtest.py', 'w') as f:
        f.write(original_content)

print("\n" + "=" * 100)
print("RESULTS")
print("=" * 100)
print(f"{'Variant':<25} {'PnL':<15} {'Trades':<10} {'PF':<8} {'DD%':<8} {'Status':<8}")
print("-" * 100)

for r in results:
    status = "PASS" if r['passes'] else "FAIL"
    print(f"{r['name']:<25} ${r['pnl']:<14,.0f} {r['trades']:<10} {r['pf']:<8.3f} {r['dd']:<8.2f} {status:<8}")

print("=" * 100)

passing = [r for r in results if r['passes']]
if len(passing) > 0:
    best = max(passing, key=lambda x: x['pnl'])
    print(f"\nBEST: {best['name']} - ${best['pnl']:,.0f}, PF {best['pf']:.3f}, DD {best['dd']:.2f}%")
