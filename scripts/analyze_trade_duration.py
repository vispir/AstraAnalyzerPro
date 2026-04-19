"""Analyze trade duration statistics for range_breakout_v1."""
import json
from datetime import datetime
from collections import Counter

# Load latest backtest result
result_file = 'backtest_results/20260419_150326_range_breakout_v1_proxy_XAUUSD_2020-01-01_2024-12-31.json'
with open(result_file) as f:
    data = json.load(f)

trades = data['trades']
durations_minutes = []

print("=" * 80)
print("RANGE BREAKOUT V1: TRADE DURATION ANALYSIS")
print(f"Total trades: {len(trades)}")
print("=" * 80)
print()

# Calculate durations
valid_durations = 0
invalid_durations = 0

for t in trades:
    entry_ts = t.get('opened_at')
    exit_ts = t.get('closed_at')

    if entry_ts and exit_ts and entry_ts != exit_ts:
        try:
            entry_dt = datetime.fromisoformat(entry_ts.replace('Z', '+00:00'))
            exit_dt = datetime.fromisoformat(exit_ts.replace('Z', '+00:00'))
            duration_seconds = (exit_dt - entry_dt).total_seconds()

            # Skip negative durations (data error)
            if duration_seconds > 0:
                duration_minutes = duration_seconds / 60
                durations_minutes.append(duration_minutes)
                valid_durations += 1
            else:
                invalid_durations += 1
        except:
            invalid_durations += 1
    else:
        invalid_durations += 1

print(f"Valid duration data: {valid_durations} trades")
print(f"Invalid/missing data: {invalid_durations} trades")
print()

if not durations_minutes:
    print("ERROR: No valid duration data found in trades")
    print("This may be a bug in the backtest engine where opened_at == closed_at")
    exit(1)
    print("ERROR: No valid duration data found in trades")
    exit(1)

# Statistics
avg_duration = sum(durations_minutes) / len(durations_minutes)
min_duration = min(durations_minutes)
max_duration = max(durations_minutes)
median_duration = sorted(durations_minutes)[len(durations_minutes) // 2]

print("DURATION STATISTICS:")
print("-" * 80)
print(f"Average duration:    {avg_duration:>8.1f} minutes ({avg_duration/60:.1f} hours)")
print(f"Median duration:     {median_duration:>8.1f} minutes ({median_duration/60:.1f} hours)")
print(f"Minimum duration:    {min_duration:>8.1f} minutes ({min_duration/60:.1f} hours)")
print(f"Maximum duration:    {max_duration:>8.1f} minutes ({max_duration/60:.1f} hours)")
print()

# Distribution buckets
buckets = {
    '< 5 min (HFT)': 0,
    '5-15 min (1 candle)': 0,
    '15-30 min (1-2 candles)': 0,
    '30-60 min (2-4 candles)': 0,
    '1-2 hours': 0,
    '2-4 hours': 0,
    '4-8 hours': 0,
    '> 8 hours': 0,
}

for d in durations_minutes:
    if d < 5:
        buckets['< 5 min (HFT)'] += 1
    elif d < 15:
        buckets['5-15 min (1 candle)'] += 1
    elif d < 30:
        buckets['15-30 min (1-2 candles)'] += 1
    elif d < 60:
        buckets['30-60 min (2-4 candles)'] += 1
    elif d < 120:
        buckets['1-2 hours'] += 1
    elif d < 240:
        buckets['2-4 hours'] += 1
    elif d < 480:
        buckets['4-8 hours'] += 1
    else:
        buckets['> 8 hours'] += 1

print("DURATION DISTRIBUTION:")
print("-" * 80)
print(f"{'Bucket':<30} {'Count':<10} {'Percentage':<15} {'Bar'}")
print("-" * 80)

for bucket, count in buckets.items():
    pct = (count / len(durations_minutes)) * 100
    bar = '#' * int(pct / 2)
    print(f"{bucket:<30} {count:<10} {pct:>6.1f}%        {bar}")

print()
print("=" * 80)
print("FUNDING PIPS COMPLIANCE CHECK")
print("=" * 80)
print()

# Check for HFT/scalping patterns
hft_trades = buckets['< 5 min (HFT)']
single_candle = buckets['5-15 min (1 candle)']
very_short = hft_trades + single_candle

print(f"Trades < 5 minutes (HFT):        {hft_trades:>4} ({hft_trades/len(durations_minutes)*100:.1f}%)")
print(f"Trades 5-15 min (1 candle):      {single_candle:>4} ({single_candle/len(durations_minutes)*100:.1f}%)")
print(f"Total very short (< 15 min):     {very_short:>4} ({very_short/len(durations_minutes)*100:.1f}%)")
print()

if hft_trades > 0:
    print("WARNING: Found HFT trades (< 5 min) - may violate Funding Pips rules")
elif single_candle / len(durations_minutes) > 0.5:
    print("WARNING: >50% trades close within 1 candle - may be flagged as scalping")
elif avg_duration < 30:
    print("WARNING: Average duration < 30 min - may be considered scalping")
else:
    print("PASS: Average duration {:.1f} min - acceptable for intraday trading".format(avg_duration))
    print("PASS: No HFT patterns detected")

print()
print("RECOMMENDATION:")
if avg_duration >= 60:
    print("  Strategy is safe for Funding Pips (avg {:.1f} hours hold time)".format(avg_duration/60))
elif avg_duration >= 30:
    print("  Strategy is borderline - monitor for scalping flags (avg {:.1f} min)".format(avg_duration))
else:
    print("  Strategy may be flagged as scalping - consider longer holds")

print()
print("=" * 80)
