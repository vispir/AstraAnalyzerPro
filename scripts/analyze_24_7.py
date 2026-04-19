"""Analyze 24/7 trading results vs baseline."""
import json
from collections import defaultdict

# Baseline: London+NY only (blocked 00-07, 12-13, 17-22)
baseline_file = "backtest_results/20260419_145733_range_breakout_v1_proxy_XAUUSD_2020-01-01_2024-12-31.json"
# 24/7: No blocks
full_file = "backtest_results/20260419_145943_range_breakout_v1_proxy_XAUUSD_2020-01-01_2024-12-31.json"

with open(baseline_file) as f:
    baseline = json.load(f)
with open(full_file) as f:
    full = json.load(f)

print("=== BASELINE (London 07-12 + NY 13-17) vs 24/7 ===\n")

print("BASELINE:")
print(f"  Trades: {baseline['summary']['total_trades']}")
print(f"  Win Rate: {baseline['summary']['win_rate']:.1%}")
print(f"  Profit Factor: {baseline['summary']['profit_factor']:.2f}")
print(f"  Max DD: {baseline['summary']['max_drawdown_pct']:.2f}%")
print(f"  Net PnL: ${baseline['summary']['net_pnl']:.2f}")

print("\n24/7 (No blocks):")
print(f"  Trades: {full['summary']['total_trades']}")
print(f"  Win Rate: {full['summary']['win_rate']:.1%}")
print(f"  Profit Factor: {full['summary']['profit_factor']:.2f}")
print(f"  Max DD: {full['summary']['max_drawdown_pct']:.2f}%")
print(f"  Net PnL: ${full['summary']['net_pnl']:.2f}")

print("\nCHANGE:")
print(f"  Trades: {full['summary']['total_trades'] - baseline['summary']['total_trades']:+d}")
print(f"  Win Rate: {(full['summary']['win_rate'] - baseline['summary']['win_rate'])*100:+.1f}%")
print(f"  Profit Factor: {full['summary']['profit_factor'] - baseline['summary']['profit_factor']:+.2f}")
print(f"  Max DD: {full['summary']['max_drawdown_pct'] - baseline['summary']['max_drawdown_pct']:+.2f}%")
print(f"  Net PnL: ${full['summary']['net_pnl'] - baseline['summary']['net_pnl']:+.2f}")

# Analyze by hour
from datetime import datetime

baseline_hours = defaultdict(int)
full_hours = defaultdict(int)

for t in baseline['trades']:
    ts = t.get('timestamp', '')
    if ts:
        try:
            dt = datetime.fromisoformat(ts.replace('Z', '+00:00'))
            baseline_hours[dt.hour] += 1
        except:
            pass

for t in full['trades']:
    ts = t.get('timestamp', '')
    if ts:
        try:
            dt = datetime.fromisoformat(ts.replace('Z', '+00:00'))
            full_hours[dt.hour] += 1
        except:
            pass

print("\n=== TRADES BY HOUR (UTC) ===")
print("Hour | Baseline | 24/7 | Diff | Session")
print("-----|----------|------|------|----------")
for h in range(24):
    b = baseline_hours.get(h, 0)
    f = full_hours.get(h, 0)
    diff = f - b

    # Session label
    if 22 <= h or h < 6:
        sess = "Sydney"
    elif 6 <= h < 8:
        sess = "Tokyo"
    elif 7 <= h < 12:
        sess = "London"
    elif 13 <= h < 17:
        sess = "NY"
    else:
        sess = "Other"

    print(f"{h:4d} | {b:8d} | {f:4d} | {diff:+4d} | {sess}")

# Session summary
baseline_sessions = {}
full_sessions = {}

for t in baseline['trades']:
    s = t.get('session_label', 'unknown')
    baseline_sessions[s] = baseline_sessions.get(s, 0) + 1

for t in full['trades']:
    s = t.get('session_label', 'unknown')
    full_sessions[s] = full_sessions.get(s, 0) + 1

print("\n=== SESSION SUMMARY ===")
print("Session  | Baseline | 24/7 | Diff")
print("---------|----------|------|------")
all_sess = sorted(set(list(baseline_sessions.keys()) + list(full_sessions.keys())))
for s in all_sess:
    b = baseline_sessions.get(s, 0)
    f = full_sessions.get(s, 0)
    diff = f - b
    print(f"{s:8s} | {b:8d} | {f:4d} | {diff:+4d}")
