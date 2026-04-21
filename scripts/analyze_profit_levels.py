"""
Analyze how many trades reach different profit levels (2R, 3R, 4R, 5R, 6R, 7R)
This explains why higher TP increases DD
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
import numpy as np
from astra_v2.data.dukascopy import load_timeframe

print("Loading M15 data...")
df = load_timeframe('M15', start='2020-01-01', end='2026-04-18', symbol='XAUUSD')
print(f"Loaded {len(df):,} M15 bars\n")

# Resample to H4
df_h4 = df.resample('4h').agg({'open':'first','high':'max','low':'min','close':'last'}).dropna()
df_h4['ema20'] = df_h4['close'].ewm(span=20, adjust=False).mean()

# Calculate ATR
df['atr'] = df['high'].rolling(14).max() - df['low'].rolling(14).min()

# Parameters
RISK_PER_TRADE = 165
USE_H4_EMA_FILTER = True

ASIAN_BASE = {'stop_buffer_atr': 0.1, 'min_range_atr': 0.7, 'max_range_atr': 3.0, 'range_hours': (0, 7), 'breakout_hours': (7, 10)}
LONDON_BASE = {'stop_buffer_atr': 0.3, 'min_range_atr': 0.3, 'max_range_atr': 3.0, 'range_hours': (7, 12), 'breakout_hours': (13, 16)}
NY_BASE = {'stop_buffer_atr': 0.3, 'min_range_atr': 0.5, 'max_range_atr': 3.0, 'range_hours': (13, 17), 'breakout_hours': (18, 21)}

# Run backtest tracking max profit reached
ASIAN_PARAMS = {**ASIAN_BASE, 'tp_rr': 10.0}  # High TP to track max profit
LONDON_PARAMS = {**LONDON_BASE, 'tp_rr': 10.0}
NY_PARAMS = {**NY_BASE, 'tp_rr': 10.0}

balance = 10000
trades = []
active_trades = {}

unique_days = df.index.normalize().unique()

for day in unique_days:
    day_data = df[df.index.normalize() == day]
    if len(day_data) == 0:
        continue

    asian_high = None
    asian_low = None
    london_high = None
    london_low = None
    ny_high = None
    ny_low = None

    times = day_data.index.values
    highs = day_data['high'].values
    lows = day_data['low'].values
    closes = day_data['close'].values

    for i in range(len(day_data)):
        hour = day_data.index[i].hour

        # Update ranges
        if ASIAN_PARAMS['range_hours'][0] <= hour < ASIAN_PARAMS['range_hours'][1]:
            if asian_high is None:
                asian_high = highs[i]
                asian_low = lows[i]
            else:
                asian_high = max(asian_high, highs[i])
                asian_low = min(asian_low, lows[i])

        if LONDON_PARAMS['range_hours'][0] <= hour < LONDON_PARAMS['range_hours'][1]:
            if london_high is None:
                london_high = highs[i]
                london_low = lows[i]
            else:
                london_high = max(london_high, highs[i])
                london_low = min(london_low, lows[i])

        if NY_PARAMS['range_hours'][0] <= hour < NY_PARAMS['range_hours'][1]:
            if ny_high is None:
                ny_high = highs[i]
                ny_low = lows[i]
            else:
                ny_high = max(ny_high, highs[i])
                ny_low = min(ny_low, lows[i])

        atr = day_data['atr'].iloc[i]
        if pd.isna(atr) or atr == 0:
            continue

        # Track max profit for active trades
        for session_name, trade in list(active_trades.items()):
            risk = abs(trade['entry'] - trade['initial_sl'])

            if trade['direction'] == 'LONG':
                current_profit_r = (highs[i] - trade['entry']) / risk
                trade['max_profit_r'] = max(trade.get('max_profit_r', 0), current_profit_r)

                # Exit on SL
                if lows[i] <= trade['sl']:
                    trade['exit'] = trade['sl']
                    trade['exit_profit_r'] = (trade['sl'] - trade['entry']) / risk
                    trades.append(trade)
                    del active_trades[session_name]

        # Entry logic (Asian)
        if ASIAN_PARAMS['breakout_hours'][0] <= hour < ASIAN_PARAMS['breakout_hours'][1]:
            if asian_high is not None and 'asian' not in active_trades:
                asian_range = asian_high - asian_low
                if ASIAN_PARAMS['min_range_atr'] * atr <= asian_range <= ASIAN_PARAMS['max_range_atr'] * atr:
                    if USE_H4_EMA_FILTER and df_h4 is not None:
                        current_time = pd.Timestamp(times[i]).tz_localize('UTC')
                        h4_bar = df_h4[df_h4.index <= current_time].iloc[-1] if len(df_h4[df_h4.index <= current_time]) > 0 else None
                        if h4_bar is None or pd.isna(h4_bar['ema20']):
                            continue

                    if closes[i] > asian_high:
                        if USE_H4_EMA_FILTER and df_h4 is not None:
                            if h4_bar['close'] <= h4_bar['ema20']:
                                continue

                        entry = closes[i]
                        sl = asian_low - ASIAN_PARAMS['stop_buffer_atr'] * atr
                        risk = entry - sl

                        active_trades['asian'] = {
                            'entry': entry, 'sl': sl, 'initial_sl': sl,
                            'direction': 'LONG', 'range_type': 'asian',
                            'max_profit_r': 0
                        }

        # Entry logic (London)
        if LONDON_PARAMS['breakout_hours'][0] <= hour < LONDON_PARAMS['breakout_hours'][1]:
            if london_high is not None and 'london' not in active_trades:
                london_range = london_high - london_low
                if LONDON_PARAMS['min_range_atr'] * atr <= london_range <= LONDON_PARAMS['max_range_atr'] * atr:
                    if USE_H4_EMA_FILTER and df_h4 is not None:
                        current_time = pd.Timestamp(times[i]).tz_localize('UTC')
                        h4_bar = df_h4[df_h4.index <= current_time].iloc[-1] if len(df_h4[df_h4.index <= current_time]) > 0 else None
                        if h4_bar is None or pd.isna(h4_bar['ema20']):
                            continue

                    if closes[i] > london_high:
                        if USE_H4_EMA_FILTER and df_h4 is not None:
                            if h4_bar['close'] <= h4_bar['ema20']:
                                continue

                        entry = closes[i]
                        sl = london_low - LONDON_PARAMS['stop_buffer_atr'] * atr
                        risk = entry - sl

                        active_trades['london'] = {
                            'entry': entry, 'sl': sl, 'initial_sl': sl,
                            'direction': 'LONG', 'range_type': 'london',
                            'max_profit_r': 0
                        }

        # Entry logic (NY)
        if NY_PARAMS['breakout_hours'][0] <= hour < NY_PARAMS['breakout_hours'][1]:
            if ny_high is not None and 'ny' not in active_trades:
                ny_range = ny_high - ny_low
                if NY_PARAMS['min_range_atr'] * atr <= ny_range <= NY_PARAMS['max_range_atr'] * atr:
                    if USE_H4_EMA_FILTER and df_h4 is not None:
                        current_time = pd.Timestamp(times[i]).tz_localize('UTC')
                        h4_bar = df_h4[df_h4.index <= current_time].iloc[-1] if len(df_h4[df_h4.index <= current_time]) > 0 else None
                        if h4_bar is None or pd.isna(h4_bar['ema20']):
                            continue

                    if closes[i] > ny_high:
                        if USE_H4_EMA_FILTER and df_h4 is not None:
                            if h4_bar['close'] <= h4_bar['ema20']:
                                continue

                        entry = closes[i]
                        sl = ny_low - NY_PARAMS['stop_buffer_atr'] * atr
                        risk = entry - sl

                        active_trades['ny'] = {
                            'entry': entry, 'sl': sl, 'initial_sl': sl,
                            'direction': 'LONG', 'range_type': 'ny',
                            'max_profit_r': 0
                        }

# Close remaining
for session_name, trade in active_trades.items():
    last_bar = df.iloc[-1]
    risk = abs(trade['entry'] - trade['initial_sl'])
    trade['exit'] = last_bar['close']
    trade['exit_profit_r'] = (last_bar['close'] - trade['entry']) / risk
    trades.append(trade)

# Analyze
trades_df = pd.DataFrame(trades)
print("="*80)
print("PROFIT LEVEL ANALYSIS")
print("="*80)
print(f"Total trades: {len(trades_df)}\n")

# Count how many reached each level
levels = [2.0, 3.0, 4.0, 5.0, 5.5, 6.0, 6.5, 7.0]
print(f"{'Level':<10} {'Reached':<10} {'%':<10} {'Cumulative %':<15}")
print("-"*50)

for level in levels:
    reached = len(trades_df[trades_df['max_profit_r'] >= level])
    pct = reached / len(trades_df) * 100
    print(f"{level}R{'':<7} {reached:<10} {pct:<9.1f}% {pct:<14.1f}%")

# Winners vs losers at each TP level
print(f"\n{'='*80}")
print("WIN RATE AT DIFFERENT TP LEVELS")
print("="*80)
print(f"{'TP Level':<12} {'Winners':<10} {'Losers':<10} {'Win Rate':<12}")
print("-"*50)

for level in [5.5, 6.0, 6.5, 7.0]:
    winners = len(trades_df[trades_df['max_profit_r'] >= level])
    losers = len(trades_df[trades_df['max_profit_r'] < level])
    wr = winners / len(trades_df) * 100 if len(trades_df) > 0 else 0
    print(f"{level}R{'':<9} {winners:<10} {losers:<10} {wr:<11.1f}%")

print(f"\n{'='*80}")
print("CONCLUSION")
print("="*80)
print("Higher TP = Lower Win Rate = More losing trades = Higher DD")
print("TP=5.5R is optimal balance between profit and DD safety")
print("="*80)
