"""
Optimize impulse_retest_v1 for BTCUSD H4 - target 200+ trades.
Tests more aggressive parameter combinations.
"""
import subprocess
import json
import time
from pathlib import Path

# More aggressive grid for higher trade frequency
param_grid = [
    # Lower impulse threshold for more signals
    {'impulse': 2.0, 'fib_min': 0.5, 'fib_max': 0.618, 'stop': 1.5, 'tp_rr': 2.0},
    {'impulse': 2.0, 'fib_min': 0.4, 'fib_max': 0.7, 'stop': 1.5, 'tp_rr': 2.0},
    {'impulse': 2.0, 'fib_min': 0.3, 'fib_max': 0.618, 'stop': 2.0, 'tp_rr': 2.5},

    # Balanced: moderate impulse, wider retracement
    {'impulse': 2.2, 'fib_min': 0.4, 'fib_max': 0.7, 'stop': 1.5, 'tp_rr': 2.0},
    {'impulse': 2.2, 'fib_min': 0.3, 'fib_max': 0.618, 'stop': 1.8, 'tp_rr': 2.0},
    {'impulse': 2.2, 'fib_min': 0.5, 'fib_max': 0.786, 'stop': 2.0, 'tp_rr': 2.5},

    # Current baseline (should give 61 trades)
    {'impulse': 2.5, 'fib_min': 0.5, 'fib_max': 0.618, 'stop': 2.0, 'tp_rr': 2.0},

    # Slightly more aggressive than baseline
    {'impulse': 2.3, 'fib_min': 0.4, 'fib_max': 0.7, 'stop': 1.8, 'tp_rr': 2.0},
    {'impulse': 2.3, 'fib_min': 0.5, 'fib_max': 0.7, 'stop': 1.5, 'tp_rr': 2.5},

    # Very aggressive: low threshold, wide zone
    {'impulse': 1.8, 'fib_min': 0.3, 'fib_max': 0.7, 'stop': 1.5, 'tp_rr': 2.0},
    {'impulse': 1.8, 'fib_min': 0.4, 'fib_max': 0.786, 'stop': 2.0, 'tp_rr': 2.5},
]

print(f"Testing {len(param_grid)} combinations for BTCUSD H4")
print(f"Target: 200+ trades, DD < 5%, WR > 60%, PF > 1.5")
print()

results = []

for i, params in enumerate(param_grid, 1):
    print(f"[{i}/{len(param_grid)}] impulse={params['impulse']}, "
          f"fib=[{params['fib_min']}-{params['fib_max']}], stop={params['stop']}, tp={params['tp_rr']}R")

    # Update config file directly
    config_path = Path('astra_v2/config.py')
    config_text = config_path.read_text(encoding='utf-8')

    # Replace parameters
    import re
    config_text = re.sub(
        r'IMPULSE_RETEST_V1_MIN_IMPULSE_BODY_ATR = [\d.]+',
        f'IMPULSE_RETEST_V1_MIN_IMPULSE_BODY_ATR = {params["impulse"]}',
        config_text
    )
    config_text = re.sub(
        r'IMPULSE_RETEST_V1_RETRACEMENT_FIB_MIN = [\d.]+',
        f'IMPULSE_RETEST_V1_RETRACEMENT_FIB_MIN = {params["fib_min"]}',
        config_text
    )
    config_text = re.sub(
        r'IMPULSE_RETEST_V1_RETRACEMENT_FIB_MAX = [\d.]+',
        f'IMPULSE_RETEST_V1_RETRACEMENT_FIB_MAX = {params["fib_max"]}',
        config_text
    )
    config_text = re.sub(
        r'IMPULSE_RETEST_V1_STOP_BUFFER_ATR = [\d.]+',
        f'IMPULSE_RETEST_V1_STOP_BUFFER_ATR = {params["stop"]}',
        config_text
    )
    config_text = re.sub(
        r'IMPULSE_RETEST_V1_TP_RR = [\d.]+',
        f'IMPULSE_RETEST_V1_TP_RR = {params["tp_rr"]}',
        config_text
    )

    config_path.write_text(config_text, encoding='utf-8')

    # Run backtest
    try:
        result = subprocess.run(
            ['python', 'scripts/run_backtest.py',
             '--strategy', 'impulse_retest_v1',
             '--primary-symbol', 'BTCUSD',
             '--start', '2020-01-01',
             '--end', '2024-12-31',
             '--mode', 'proxy'],
            capture_output=True,
            text=True,
            timeout=180,
            cwd='.'
        )

        output = result.stdout

        # Parse JSON output
        if '=== BACKTEST RESULTS [BTCUSD] ===' in output:
            json_start = output.find('{', output.find('=== BACKTEST RESULTS'))
            json_end = output.find('}', json_start) + 1
            json_str = output[json_start:json_end]

            try:
                summary = json.loads(json_str)

                trades = summary.get('total_trades', 0)
                wr = summary.get('win_rate', 0)
                pf = summary.get('profit_factor', 0)
                dd = summary.get('max_drawdown_pct', 0)
                pnl = summary.get('net_pnl', 0)

                results.append({
                    'params': params,
                    'trades': trades,
                    'win_rate': wr,
                    'profit_factor': pf,
                    'max_dd': dd,
                    'net_pnl': pnl,
                })

                # Check criteria
                meets_criteria = (
                    trades >= 200 and
                    dd < 5.0 and
                    wr >= 0.6 and
                    pf >= 1.5
                )

                status = "✓ TARGET" if meets_criteria else "PASS" if dd < 5.0 and pf > 1.5 else "FAIL"
                print(f"  Trades: {trades}, WR: {wr:.1%}, PF: {pf:.2f}, DD: {dd:.2f}%, PnL: ${pnl:.0f} [{status}]")

            except json.JSONDecodeError:
                print(f"  > Failed to parse JSON")
        else:
            print(f"  > No results found")

    except subprocess.TimeoutExpired:
        print(f"  > TIMEOUT")
    except Exception as e:
        print(f"  > ERROR: {e}")

    print()
    time.sleep(0.5)

# Sort results
print("=" * 80)
print("RESULTS SUMMARY")
print("=" * 80)
print()

# Filter by criteria
target_results = [r for r in results
                  if r['trades'] >= 200
                  and r['max_dd'] < 5.0
                  and r['win_rate'] >= 0.6
                  and r['profit_factor'] >= 1.5]

if target_results:
    print(f"✓ FOUND {len(target_results)} COMBINATIONS MEETING ALL CRITERIA:")
    print()
    target_results.sort(key=lambda x: x['net_pnl'], reverse=True)
    for i, r in enumerate(target_results[:5], 1):
        p = r['params']
        print(f"{i}. impulse={p['impulse']}, fib=[{p['fib_min']}-{p['fib_max']}], stop={p['stop']}, tp={p['tp_rr']}R")
        print(f"   Trades: {r['trades']}, WR: {r['win_rate']:.1%}, PF: {r['profit_factor']:.2f}, DD: {r['max_dd']:.2f}%, PnL: ${r['net_pnl']:.0f}")
        print()
else:
    print("NO COMBINATIONS MEET ALL CRITERIA (200+ trades, DD<5%, WR≥60%, PF≥1.5)")
    print()
    print("BEST BY TRADE COUNT (DD < 5%):")
    valid = [r for r in results if r['max_dd'] < 5.0 and r['profit_factor'] >= 1.5]
    valid.sort(key=lambda x: x['trades'], reverse=True)
    for i, r in enumerate(valid[:5], 1):
        p = r['params']
        print(f"{i}. impulse={p['impulse']}, fib=[{p['fib_min']}-{p['fib_max']}], stop={p['stop']}, tp={p['tp_rr']}R")
        print(f"   Trades: {r['trades']}, WR: {r['win_rate']:.1%}, PF: {r['profit_factor']:.2f}, DD: {r['max_dd']:.2f}%, PnL: ${r['net_pnl']:.0f}")
        print()

# Save results
output_path = 'backtest_results/btc_h4_optimization_v2.json'
with open(output_path, 'w') as f:
    json.dump(results, f, indent=2)

print(f"Results saved to {output_path}")
