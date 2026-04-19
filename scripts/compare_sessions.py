"""Compare backtest results before/after adding Asian sessions."""
import json
from pathlib import Path

# Before: blocked 00:00-07:00 UTC (no Sydney/Tokyo)
before_file = "backtest_results/20260419_144807_range_breakout_v1_proxy_XAUUSD_2020-01-01_2024-12-31.json"
# After: allowed Sydney (22:00-06:00) and Tokyo (00:00-08:00), blocked only 12:00-13:00 and 17:00-22:00
after_file = "backtest_results/20260419_145022_range_breakout_v1_proxy_XAUUSD_2020-01-01_2024-12-31.json"

with open(before_file) as f:
    before = json.load(f)
with open(after_file) as f:
    after = json.load(f)

print("=== SESSION COMPARISON ===\n")
print("BEFORE (blocked 00:00-07:00 UTC):")
print(f"  Trades: {before['summary']['total_trades']}")
print(f"  Win Rate: {before['summary']['win_rate']:.1%}")
print(f"  Profit Factor: {before['summary']['profit_factor']:.2f}")
print(f"  Max DD: {before['summary']['max_drawdown_pct']:.2f}%")
print(f"  Net PnL: ${before['summary']['net_pnl']:.2f}")

print("\nAFTER (allowed Sydney 22:00-06:00 + Tokyo 00:00-08:00):")
print(f"  Trades: {after['summary']['total_trades']}")
print(f"  Win Rate: {after['summary']['win_rate']:.1%}")
print(f"  Profit Factor: {after['summary']['profit_factor']:.2f}")
print(f"  Max DD: {after['summary']['max_drawdown_pct']:.2f}%")
print(f"  Net PnL: ${after['summary']['net_pnl']:.2f}")

print("\nCHANGE:")
trade_diff = after['summary']['total_trades'] - before['summary']['total_trades']
wr_diff = after['summary']['win_rate'] - before['summary']['win_rate']
pf_diff = after['summary']['profit_factor'] - before['summary']['profit_factor']
dd_diff = after['summary']['max_drawdown_pct'] - before['summary']['max_drawdown_pct']
pnl_diff = after['summary']['net_pnl'] - before['summary']['net_pnl']

print(f"  Trades: {trade_diff:+d} ({trade_diff/before['summary']['total_trades']*100:+.1f}%)")
print(f"  Win Rate: {wr_diff:+.1%}")
print(f"  Profit Factor: {pf_diff:+.2f}")
print(f"  Max DD: {dd_diff:+.2f}%")
print(f"  Net PnL: ${pnl_diff:+.2f}")

# Count trades by session
before_sessions = {}
after_sessions = {}

for t in before['trades']:
    sess = t.get('session_label', 'unknown')
    before_sessions[sess] = before_sessions.get(sess, 0) + 1

for t in after['trades']:
    sess = t.get('session_label', 'unknown')
    after_sessions[sess] = after_sessions.get(sess, 0) + 1

print("\n=== TRADES BY SESSION ===")
all_sessions = sorted(set(list(before_sessions.keys()) + list(after_sessions.keys())))
for sess in all_sessions:
    b = before_sessions.get(sess, 0)
    a = after_sessions.get(sess, 0)
    diff = a - b
    print(f"{sess:12s}: {b:4d} → {a:4d} ({diff:+4d})")
