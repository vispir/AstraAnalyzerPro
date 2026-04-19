"""
Optimize impulse_retest_v1 for BTCUSD H4 timeframe.
Tests parameter combinations with proper subprocess isolation.
"""
import subprocess
import json
from pathlib import Path
import time

# Parameter grid for H4 (wider parameters for larger timeframe)
param_grid = [
    # Conservative: strong impulse, deep retracement, wide stop
    {'impulse': 3.0, 'fib_min': 0.5, 'fib_max': 0.618, 'stop': 2.0, 'tp_rr': 2.0},
    {'impulse': 3.5, 'fib_min': 0.5, 'fib_max': 0.7, 'stop': 2.5, 'tp_rr': 2.0},
    {'impulse': 4.0, 'fib_min': 0.5, 'fib_max': 0.618, 'stop': 3.0, 'tp_rr': 2.5},

    # Moderate: balanced parameters
    {'impulse': 3.0, 'fib_min': 0.4, 'fib_max': 0.618, 'stop': 2.0, 'tp_rr': 2.0},
    {'impulse': 3.5, 'fib_min': 0.4, 'fib_max': 0.618, 'stop': 2.5, 'tp_rr': 2.5},
    {'impulse': 4.0, 'fib_min': 0.4, 'fib_max': 0.7, 'stop': 2.5, 'tp_rr': 2.0},

    # Aggressive: tighter parameters, higher TP
    {'impulse': 2.5, 'fib_min': 0.3, 'fib_max': 0.5, 'stop': 1.5, 'tp_rr': 3.0},
    {'impulse': 3.0, 'fib_min': 0.3, 'fib_max': 0.618, 'stop': 2.0, 'tp_rr': 2.5},

    # Very conservative: very strong impulse, deep retracement
    {'impulse': 4.5, 'fib_min': 0.5, 'fib_max': 0.7, 'stop': 3.0, 'tp_rr': 2.0},
    {'impulse': 5.0, 'fib_min': 0.618, 'fib_max': 0.786, 'stop': 3.5, 'tp_rr': 2.0},
]

print(f"Testing {len(param_grid)} combinations on BTCUSD H4")
print(f"Estimated time: {len(param_grid) * 2} minutes")
print()

results = []

for i, params in enumerate(param_grid, 1):
    print(f"[{i}/{len(param_grid)}] Testing: impulse={params['impulse']}, "
          f"fib=[{params['fib_min']}-{params['fib_max']}], stop={params['stop']}, tp={params['tp_rr']}R")

    # Create temporary config with parameters
    config_updates = f"""
# Temporary optimization parameters
IMPULSE_RETEST_V1_MIN_IMPULSE_BODY_ATR = {params['impulse']}
IMPULSE_RETEST_V1_RETRACEMENT_FIB_MIN = {params['fib_min']}
IMPULSE_RETEST_V1_RETRACEMENT_FIB_MAX = {params['fib_max']}
IMPULSE_RETEST_V1_STOP_BUFFER_ATR = {params['stop']}
IMPULSE_RETEST_V1_TP_RR = {params['tp_rr']}
"""

    # Write to temp file
    Path('astra_v2/config_temp.py').write_text(config_updates)

    # Run backtest with environment variable to use H4
    try:
        result = subprocess.run(
            ["python", "-c", f"""
import sys
sys.path.insert(0, '.')

# Override config values
from astra_v2 import config
config.IMPULSE_RETEST_V1_MIN_IMPULSE_BODY_ATR = {params['impulse']}
config.IMPULSE_RETEST_V1_RETRACEMENT_FIB_MIN = {params['fib_min']}
config.IMPULSE_RETEST_V1_RETRACEMENT_FIB_MAX = {params['fib_max']}
config.IMPULSE_RETEST_V1_STOP_BUFFER_ATR = {params['stop']}
config.IMPULSE_RETEST_V1_TP_RR = {params['tp_rr']}

# Run backtest
import subprocess
result = subprocess.run([
    'python', 'scripts/run_backtest.py',
    '--strategy', 'impulse_retest_v1',
    '--primary-symbol', 'BTCUSD',
    '--start', '2020-01-01',
    '--end', '2024-12-31',
    '--mode', 'proxy'
], capture_output=True, text=True)
print(result.stdout)
"""],
            capture_output=True,
            text=True,
            timeout=180,
            cwd='.'
        )

        output = result.stdout

        # Parse metrics
        trades = win_rate = pf = dd = pnl = None

        for line in output.split('\\n'):
            if '"total_trades":' in line:
                trades = int(line.split(':')[1].strip().rstrip(','))
            elif '"win_rate":' in line:
                win_rate = float(line.split(':')[1].strip().rstrip(','))
            elif '"profit_factor":' in line:
                pf = float(line.split(':')[1].strip().rstrip(','))
            elif '"max_drawdown_pct":' in line:
                dd = float(line.split(':')[1].strip().rstrip(','))
            elif '"net_pnl":' in line:
                pnl = float(line.split(':')[1].strip().rstrip(','))

        if trades is not None:
            results.append({
                'params': params,
                'trades': trades,
                'win_rate': win_rate,
                'profit_factor': pf,
                'max_dd': dd,
                'net_pnl': pnl,
            })

            status = "PASS" if dd and dd < 5.0 and pf and pf > 1.3 else "FAIL"
            print(f"  > Trades: {trades}, WR: {win_rate:.1%}, PF: {pf:.2f}, DD: {dd:.2f}%, PnL: ${pnl:.0f} [{status}]")
        else:
            print(f"  > FAILED to parse")

    except subprocess.TimeoutExpired:
        print(f"  > TIMEOUT")
    except Exception as e:
        print(f"  > ERROR: {e}")

    print()
    time.sleep(1)

# Sort by criteria: DD < 5%, then by PF
valid_results = [r for r in results if r['max_dd'] and r['max_dd'] < 5.0 and r['profit_factor'] and r['profit_factor'] > 1.3]
valid_results.sort(key=lambda x: x['profit_factor'], reverse=True)

print("=" * 80)
print("RESULTS SUMMARY")
print("=" * 80)
print()

if valid_results:
    print(f"VALID RESULTS (DD < 5%, PF > 1.3): {len(valid_results)}")
    print()
    for i, r in enumerate(valid_results[:5], 1):
        p = r['params']
        print(f"{i}. impulse={p['impulse']}, fib=[{p['fib_min']}-{p['fib_max']}], stop={p['stop']}, tp={p['tp_rr']}R")
        print(f"   Trades: {r['trades']}, WR: {r['win_rate']:.1%}, PF: {r['profit_factor']:.2f}, DD: {r['max_dd']:.2f}%, PnL: ${r['net_pnl']:.0f}")
        print()
else:
    print("NO VALID RESULTS FOUND (all have DD >= 5% or PF <= 1.3)")
    print()
    print("TOP 5 BY PROFIT FACTOR:")
    results.sort(key=lambda x: x['profit_factor'] if x['profit_factor'] else 0, reverse=True)
    for i, r in enumerate(results[:5], 1):
        p = r['params']
        print(f"{i}. impulse={p['impulse']}, fib=[{p['fib_min']}-{p['fib_max']}], stop={p['stop']}, tp={p['tp_rr']}R")
        print(f"   Trades: {r['trades']}, WR: {r['win_rate']:.1%}, PF: {r['profit_factor']:.2f}, DD: {r['max_dd']:.2f}%, PnL: ${r['net_pnl']:.0f}")
        print()

# Save results
with open('backtest_results/impulse_retest_v1_h4_optimization.json', 'w') as f:
    json.dump(results, f, indent=2)

print("Results saved to backtest_results/impulse_retest_v1_h4_optimization.json")
