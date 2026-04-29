"""
Анализ почему не было сигналов с понедельника
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from session_breakout_trader import (
    load_candles_from_supabase,
    calculate_atr, calculate_ema,
    LONG_SESSIONS, ATR_PERIOD, ATR_BUFFER, H4_EMA_PERIOD
)
import pandas as pd
import numpy as np

# Load data
print("Loading candles from Supabase...")
df = load_candles_from_supabase(limit=2000)
print(f"✓ Loaded {len(df)} candles")
print(f"Date range: {df.index[0]} to {df.index[-1]}")
print()

# Calculate indicators
df['atr'] = calculate_atr(df, ATR_PERIOD)
df_h4 = df.resample('4h').agg({'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last'}).dropna()
df_h4['atr'] = calculate_atr(df_h4, ATR_PERIOD)
df_h4['ema20'] = calculate_ema(df_h4['close'], H4_EMA_PERIOD)

# Get Monday data (2026-04-28 UTC, but market opened Sunday 22:00)
# Check from Sunday 22:00 onwards
market_open = pd.Timestamp('2026-04-27 22:00:00', tz='UTC')
monday_data = df[df.index >= market_open]

print(f"Market data from {market_open}:")
print(f"  Bars: {len(monday_data)}")
print(f"  Range: {monday_data.index[0]} to {monday_data.index[-1]}")
print(f"  High: {monday_data['high'].max():.2f}")
print(f"  Low: {monday_data['low'].min():.2f}")
print()

# Check H4 trend
current_h4 = df_h4.iloc[-1]
print(f"H4 TREND:")
print(f"  Close: {current_h4['close']:.2f}")
print(f"  EMA20: {current_h4['ema20']:.2f}")
print(f"  Trend: {'UP' if current_h4['close'] > current_h4['ema20'] else 'DOWN'}")
print()

# Analyze each session
print("="*80)
print("SESSION ANALYSIS")
print("="*80)

for session_name, params in LONG_SESSIONS.items():
    print(f"\n{session_name.upper()} SESSION:")

    start_hour, end_hour = params['range_hours']
    entry_start = params['entry_start']
    entry_end = params['entry_end']

    # Get session range bars
    session_bars = monday_data[(monday_data.index.hour >= start_hour) & (monday_data.index.hour < end_hour)]

    if len(session_bars) == 0:
        print(f"  ⚠ No bars in range window {start_hour}-{end_hour}")
        continue

    session_high = session_bars['high'].max()
    session_low = session_bars['low'].min()

    print(f"  Range window: {start_hour:02d}:00 - {end_hour:02d}:00")
    print(f"  Range bars: {len(session_bars)}")
    print(f"  Session High: {session_high:.2f}")
    print(f"  Session Low: {session_low:.2f}")

    # Check entry window
    entry_bars = monday_data[(monday_data.index.hour >= entry_start) & (monday_data.index.hour < entry_end)]

    if len(entry_bars) == 0:
        print(f"  ⚠ No bars in entry window {entry_start}-{entry_end}")
        continue

    print(f"  Entry window: {entry_start:02d}:00 - {entry_end:02d}:00")
    print(f"  Entry bars: {len(entry_bars)}")

    # Check for breakouts
    max_close = entry_bars['close'].max()
    breakout = max_close > session_high

    print(f"  Max close in entry: {max_close:.2f}")
    print(f"  Breakout: {breakout}")

    if breakout:
        # Find when breakout happened
        breakout_bars = entry_bars[entry_bars['close'] > session_high]
        if len(breakout_bars) > 0:
            first_breakout = breakout_bars.index[0]
            print(f"  ✓ BREAKOUT at {first_breakout}")

            # Check H4 filter at that time
            h4_at_breakout = df_h4[df_h4.index <= first_breakout].iloc[-1]
            h4_ok = h4_at_breakout['close'] > h4_at_breakout['ema20']
            print(f"  H4 filter: {'PASS' if h4_ok else 'FAIL'} (close={h4_at_breakout['close']:.2f}, ema20={h4_at_breakout['ema20']:.2f})")

            if not h4_ok:
                print(f"  ❌ BLOCKED by H4 EMA20 filter")
    else:
        print(f"  ❌ No breakout (need close > {session_high:.2f})")

print("\n" + "="*80)
print("SUMMARY")
print("="*80)
print(f"Current time: {monday_data.index[-1]}")
print(f"H4 trend: {'UP' if current_h4['close'] > current_h4['ema20'] else 'DOWN'}")
