"""
Grid search optimization for impulse_retest_v1 on BTCUSD.
Test different parameter combinations to find optimal settings.
"""
import subprocess
import json
import sys
from pathlib import Path

# Parameter grid
param_grid = {
    'MIN_IMPULSE_BODY_ATR': [2.0, 2.5, 3.0, 3.5, 4.0],
    'RETRACEMENT_FIB_MIN': [0.3, 0.4, 0.5],
    'RETRACEMENT_FIB_MAX': [0.5, 0.618, 0.7],
    'STOP_BUFFER_ATR': [1.0, 1.5, 2.0, 2.5, 3.0],
}

# Generate combinations (sample subset to avoid too many runs)
combinations = []

# Test key combinations
for impulse in param_grid['MIN_IMPULSE_BODY_ATR']:
    for fib_min in param_grid['RETRACEMENT_FIB_MIN']:
        for fib_max in param_grid['RETRACEMENT_FIB_MAX']:
            if fib_max <= fib_min:
                continue
            for stop_buffer in param_grid['STOP_BUFFER_ATR']:
                combinations.append({
                    'MIN_IMPULSE_BODY_ATR': impulse,
                    'RETRACEMENT_FIB_MIN': fib_min,
                    'RETRACEMENT_FIB_MAX': fib_max,
                    'STOP_BUFFER_ATR': stop_buffer,
                })

print(f"Total combinations to test: {len(combinations)}")
print(f"Estimated time: {len(combinations) * 2} minutes")
print()

# Limit to reasonable number
if len(combinations) > 50:
    print(f"WARNING: Too many combinations ({len(combinations)})")
    print("Sampling 30 best candidates...")
    # Sample evenly
    step = len(combinations) // 30
    combinations = combinations[::step][:30]
    print(f"Testing {len(combinations)} combinations")
    print()

results = []

for i, params in enumerate(combinations, 1):
    print(f"[{i}/{len(combinations)}] Testing: impulse={params['MIN_IMPULSE_BODY_ATR']}, "
          f"fib=[{params['RETRACEMENT_FIB_MIN']}-{params['RETRACEMENT_FIB_MAX']}], "
          f"stop={params['STOP_BUFFER_ATR']}")

    # Update config.py
    config_path = Path("astra_v2/config.py")
    config_text = config_path.read_text()

    # Replace parameters
    config_text = config_text.replace(
        f"IMPULSE_RETEST_V1_MIN_IMPULSE_BODY_ATR = 2.0",
        f"IMPULSE_RETEST_V1_MIN_IMPULSE_BODY_ATR = {params['MIN_IMPULSE_BODY_ATR']}"
    )
    config_text = config_text.replace(
        f"IMPULSE_RETEST_V1_RETRACEMENT_FIB_MIN = 0.3",
        f"IMPULSE_RETEST_V1_RETRACEMENT_FIB_MIN = {params['RETRACEMENT_FIB_MIN']}"
    )
    config_text = config_text.replace(
        f"IMPULSE_RETEST_V1_RETRACEMENT_FIB_MAX = 0.5",
        f"IMPULSE_RETEST_V1_RETRACEMENT_FIB_MAX = {params['RETRACEMENT_FIB_MAX']}"
    )
    config_text = config_text.replace(
        f"IMPULSE_RETEST_V1_STOP_BUFFER_ATR = 1.0",
        f"IMPULSE_RETEST_V1_STOP_BUFFER_ATR = {params['STOP_BUFFER_ATR']}"
    )

    config_path.write_text(config_text)

    # Run backtest
    try:
        result = subprocess.run(
            ["python", "scripts/run_backtest.py",
             "--strategy", "impulse_retest_v1",
             "--primary-symbol", "BTCUSD",
             "--start", "2020-01-01",
             "--end", "2024-12-31",
             "--mode", "proxy"],
            capture_output=True,
            text=True,
            timeout=180
        )

        # Parse output
        output = result.stdout

        # Extract metrics from output
        trades = None
        win_rate = None
        pf = None
        dd = None
        pnl = None

        for line in output.split('\n'):
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

            print(f"  > Trades: {trades}, WR: {win_rate:.1%}, PF: {pf:.2f}, DD: {dd:.2f}%, PnL: ${pnl:.0f}")
        else:
            print(f"  > FAILED to parse results")

    except subprocess.TimeoutExpired:
        print(f"  > TIMEOUT")
    except Exception as e:
        print(f"  > ERROR: {e}")

    print()

# Sort by profit factor
results.sort(key=lambda x: x['profit_factor'] if x['profit_factor'] else 0, reverse=True)

print("=" * 80)
print("TOP 10 RESULTS (by Profit Factor)")
print("=" * 80)
print()

for i, r in enumerate(results[:10], 1):
    p = r['params']
    print(f"{i}. impulse={p['MIN_IMPULSE_BODY_ATR']}, fib=[{p['RETRACEMENT_FIB_MIN']}-{p['RETRACEMENT_FIB_MAX']}], stop={p['STOP_BUFFER_ATR']}")
    print(f"   Trades: {r['trades']}, WR: {r['win_rate']:.1%}, PF: {r['profit_factor']:.2f}, DD: {r['max_dd']:.2f}%, PnL: ${r['net_pnl']:.0f}")
    print()

# Save results
with open('backtest_results/impulse_retest_v1_optimization.json', 'w') as f:
    json.dump(results, f, indent=2)

print("Results saved to backtest_results/impulse_retest_v1_optimization.json")
