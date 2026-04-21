"""
Analyze strategy WITHOUT trailing:
1. Check if at least 1 trade per month (Funding Pips rule)
2. Find worst consecutive loss streak
3. Compare with trailing version
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
import numpy as np
from astra_v2.data.dukascopy import load_timeframe
from datetime import datetime

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
TP_RR = 5.5
USE_H4_EMA_FILTER = True

ASIAN_BASE = {'stop_buffer_atr': 0.1, 'min_range_atr': 0.7, 'max_range_atr': 3.0, 'range_hours': (0, 7), 'breakout_hours': (7, 10)}
LONDON_BASE = {'stop_buffer_atr': 0.3, 'min_range_atr': 0.3, 'max_range_atr': 3.0, 'range_hours': (7, 12), 'breakout_hours': (13, 16)}
NY_BASE = {'stop_buffer_atr': 0.3, 'min_range_atr': 0.5, 'max_range_atr': 3.0, 'range_hours': (13, 17), 'breakout_hours': (18, 21)}

ASIAN_PARAMS = {**ASIAN_BASE, 'tp_rr': TP_RR}
LONDON_PARAMS = {**LONDON_BASE, 'tp_rr': TP_RR}
NY_PARAMS = {**NY_BASE, 'tp_rr': TP_RR}

def run_backtest(use_trailing):
    """Run backtest with or without trailing"""

    balance = 10000
    peak_balance = 10000
    max_dd = 0
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

            # Update active trades
            for session_name, trade in list(active_trades.items()):
                exit_trade = False

                # Step trailing
                if use_trailing:
                    risk = abs(trade['entry'] - trade['initial_sl'])
                    profit_r = (highs[i] - trade['entry']) / risk

                    if profit_r >= 2.0:
                        trade['sl'] = max(trade['sl'], trade['entry'] + 1.0 * risk)
                    if profit_r >= 3.0:
                        trade['sl'] = max(trade['sl'], trade['entry'] + 2.0 * risk)
                    if profit_r >= 4.0:
                        trade['sl'] = max(trade['sl'], trade['entry'] + 3.0 * risk)
                    if profit_r >= 5.0:
                        trade['sl'] = max(trade['sl'], trade['entry'] + 4.0 * risk)

                # Check exit
                if lows[i] <= trade['sl']:
                    pnl = (trade['sl'] - trade['entry']) * trade['size']
                    balance += pnl
                    trade['exit'] = trade['sl']
                    trade['exit_time'] = times[i]
                    trade['pnl'] = pnl
                    trade['status'] = 'sl'
                    exit_trade = True
                elif highs[i] >= trade['tp']:
                    pnl = (trade['tp'] - trade['entry']) * trade['size']
                    balance += pnl
                    trade['exit'] = trade['tp']
                    trade['exit_time'] = times[i]
                    trade['pnl'] = pnl
                    trade['status'] = 'tp'
                    exit_trade = True

                if exit_trade:
                    trades.append(trade)
                    del active_trades[session_name]

                    if balance > peak_balance:
                        peak_balance = balance
                    dd = (peak_balance - balance) / peak_balance * 100
                    if dd > max_dd:
                        max_dd = dd

            # Entry logic (Asian)
            if ASIAN_PARAMS['breakout_hours'][0] <= hour < ASIAN_PARAMS['breakout_hours'][1]:
                if asian_high is not None and 'asian' not in active_trades:
                    asian_range = asian_high - asian_low
                    if ASIAN_PARAMS['min_range_atr'] * atr <= asian_range <= ASIAN_PARAMS['max_range_atr'] * atr:
                        if USE_H4_EMA_FILTER:
                            current_time = pd.Timestamp(times[i]).tz_localize('UTC')
                            h4_bar = df_h4[df_h4.index <= current_time].iloc[-1] if len(df_h4[df_h4.index <= current_time]) > 0 else None
                            if h4_bar is None or pd.isna(h4_bar['ema20']):
                                continue

                        if closes[i] > asian_high:
                            if USE_H4_EMA_FILTER:
                                if h4_bar['close'] <= h4_bar['ema20']:
                                    continue

                            entry = closes[i]
                            sl = asian_low - ASIAN_PARAMS['stop_buffer_atr'] * atr
                            risk = entry - sl
                            tp = entry + risk * ASIAN_PARAMS['tp_rr']
                            size = RISK_PER_TRADE / risk

                            active_trades['asian'] = {
                                'entry': entry, 'sl': sl, 'initial_sl': sl, 'tp': tp,
                                'size': size, 'direction': 'LONG', 'entry_time': times[i],
                                'range_type': 'asian'
                            }

            # Entry logic (London)
            if LONDON_PARAMS['breakout_hours'][0] <= hour < LONDON_PARAMS['breakout_hours'][1]:
                if london_high is not None and 'london' not in active_trades:
                    london_range = london_high - london_low
                    if LONDON_PARAMS['min_range_atr'] * atr <= london_range <= LONDON_PARAMS['max_range_atr'] * atr:
                        if USE_H4_EMA_FILTER:
                            current_time = pd.Timestamp(times[i]).tz_localize('UTC')
                            h4_bar = df_h4[df_h4.index <= current_time].iloc[-1] if len(df_h4[df_h4.index <= current_time]) > 0 else None
                            if h4_bar is None or pd.isna(h4_bar['ema20']):
                                continue

                        if closes[i] > london_high:
                            if USE_H4_EMA_FILTER:
                                if h4_bar['close'] <= h4_bar['ema20']:
                                    continue

                            entry = closes[i]
                            sl = london_low - LONDON_PARAMS['stop_buffer_atr'] * atr
                            risk = entry - sl
                            tp = entry + risk * LONDON_PARAMS['tp_rr']
                            size = RISK_PER_TRADE / risk

                            active_trades['london'] = {
                                'entry': entry, 'sl': sl, 'initial_sl': sl, 'tp': tp,
                                'size': size, 'direction': 'LONG', 'entry_time': times[i],
                                'range_type': 'london'
                            }

            # Entry logic (NY)
            if NY_PARAMS['breakout_hours'][0] <= hour < NY_PARAMS['breakout_hours'][1]:
                if ny_high is not None and 'ny' not in active_trades:
                    ny_range = ny_high - ny_low
                    if NY_PARAMS['min_range_atr'] * atr <= ny_range <= NY_PARAMS['max_range_atr'] * atr:
                        if USE_H4_EMA_FILTER:
                            current_time = pd.Timestamp(times[i]).tz_localize('UTC')
                            h4_bar = df_h4[df_h4.index <= current_time].iloc[-1] if len(df_h4[df_h4.index <= current_time]) > 0 else None
                            if h4_bar is None or pd.isna(h4_bar['ema20']):
                                continue

                        if closes[i] > ny_high:
                            if USE_H4_EMA_FILTER:
                                if h4_bar['close'] <= h4_bar['ema20']:
                                    continue

                            entry = closes[i]
                            sl = ny_low - NY_PARAMS['stop_buffer_atr'] * atr
                            risk = entry - sl
                            tp = entry + risk * NY_PARAMS['tp_rr']
                            size = RISK_PER_TRADE / risk

                            active_trades['ny'] = {
                                'entry': entry, 'sl': sl, 'initial_sl': sl, 'tp': tp,
                                'size': size, 'direction': 'LONG', 'entry_time': times[i],
                                'range_type': 'ny'
                            }

    # Close remaining
    for session_name, trade in active_trades.items():
        last_bar = df.iloc[-1]
        pnl = (last_bar['close'] - trade['entry']) * trade['size']
        balance += pnl
        trade['exit'] = last_bar['close']
        trade['exit_time'] = df.index[-1]
        trade['pnl'] = pnl
        trade['status'] = 'eod'
        trades.append(trade)

    return pd.DataFrame(trades), balance, max_dd

# Run both versions
print("="*80)
print("ANALYZING: Risk=$165, TP=5.5R")
print("="*80)

print("\nRunning WITH trailing...")
trades_with, balance_with, dd_with = run_backtest(use_trailing=True)

print("Running WITHOUT trailing...")
trades_without, balance_without, dd_without = run_backtest(use_trailing=False)

# Analysis 1: Trades per month
print("\n" + "="*80)
print("1. TRADES PER MONTH ANALYSIS")
print("="*80)

def analyze_monthly_trades(trades_df, label):
    # Convert exit_time to datetime if needed
    if not pd.api.types.is_datetime64_any_dtype(trades_df['exit_time']):
        trades_df['exit_time'] = pd.to_datetime(trades_df['exit_time'], utc=True)

    trades_df['month'] = trades_df['exit_time'].dt.to_period('M')
    monthly_counts = trades_df.groupby('month').size()

    print(f"\n{label}:")
    print(f"  Total months: {len(monthly_counts)}")
    print(f"  Months with 0 trades: {len(monthly_counts[monthly_counts == 0])}")
    print(f"  Months with 1+ trades: {len(monthly_counts[monthly_counts >= 1])}")
    print(f"  Min trades/month: {monthly_counts.min()}")
    print(f"  Max trades/month: {monthly_counts.max()}")
    print(f"  Avg trades/month: {monthly_counts.mean():.1f}")

    if len(monthly_counts[monthly_counts == 0]) > 0:
        print(f"\n  WARNING: Months with 0 trades:")
        for month in monthly_counts[monthly_counts == 0].index:
            print(f"    {month}")
    else:
        print(f"\n  PASS: All months have at least 1 trade")

    return monthly_counts

monthly_with = analyze_monthly_trades(trades_with, "WITH Trailing")
monthly_without = analyze_monthly_trades(trades_without, "WITHOUT Trailing")

# Analysis 2: Worst consecutive loss streak
print("\n" + "="*80)
print("2. WORST CONSECUTIVE LOSS STREAK")
print("="*80)

def find_worst_streak(trades_df, label):
    trades_df = trades_df.sort_values('exit_time').reset_index(drop=True)
    trades_df['is_loss'] = trades_df['pnl'] <= 0

    max_streak = 0
    current_streak = 0
    streak_start_idx = 0
    worst_streak_start = 0
    worst_streak_end = 0

    for i, row in trades_df.iterrows():
        if row['is_loss']:
            if current_streak == 0:
                streak_start_idx = i
            current_streak += 1
            if current_streak > max_streak:
                max_streak = current_streak
                worst_streak_start = streak_start_idx
                worst_streak_end = i
        else:
            current_streak = 0

    print(f"\n{label}:")
    print(f"  Max consecutive losses: {max_streak}")

    if max_streak > 0:
        worst_trades = trades_df.iloc[worst_streak_start:worst_streak_end+1]
        total_loss = worst_trades['pnl'].sum()
        dd_from_streak = (abs(total_loss) / 10000) * 100

        print(f"  Period: {worst_trades.iloc[0]['exit_time'].date()} to {worst_trades.iloc[-1]['exit_time'].date()}")
        print(f"  Total loss: ${total_loss:,.2f}")
        print(f"  DD from streak: {dd_from_streak:.2f}%")

        print(f"\n  Trades in worst streak:")
        for idx, trade in worst_trades.iterrows():
            print(f"    {trade['exit_time'].date()} {trade['range_type']:>6} {trade['status']:>3} ${trade['pnl']:>8,.2f}")

    return max_streak, worst_trades if max_streak > 0 else None

streak_with, worst_with = find_worst_streak(trades_with, "WITH Trailing")
streak_without, worst_without = find_worst_streak(trades_without, "WITHOUT Trailing")

# Summary
print("\n" + "="*80)
print("SUMMARY")
print("="*80)

print(f"\n{'Metric':<30} {'WITH Trailing':<20} {'WITHOUT Trailing':<20}")
print("-"*70)
print(f"{'Total PnL':<30} ${balance_with - 10000:>18,.0f} ${balance_without - 10000:>18,.0f}")
print(f"{'Max DD':<30} {dd_with:>17.2f}% {dd_without:>17.2f}%")
print(f"{'Total Trades':<30} {len(trades_with):>18} {len(trades_without):>18}")
print(f"{'Avg trades/month':<30} {monthly_with.mean():>18.1f} {monthly_without.mean():>18.1f}")
print(f"{'Months with 0 trades':<30} {len(monthly_with[monthly_with == 0]):>18} {len(monthly_without[monthly_without == 0]):>18}")
print(f"{'Max consecutive losses':<30} {streak_with:>18} {streak_without:>18}")
print(f"{'Worst streak loss':<30} ${worst_with['pnl'].sum() if worst_with is not None else 0:>18,.2f} ${worst_without['pnl'].sum() if worst_without is not None else 0:>18,.2f}")

print("\n" + "="*80)
print("RECOMMENDATION")
print("="*80)

if len(monthly_without[monthly_without == 0]) == 0:
    print("\nWITHOUT Trailing: PASS - All months have at least 1 trade")
else:
    print(f"\nWITHOUT Trailing: FAIL - {len(monthly_without[monthly_without == 0])} months with 0 trades")

if len(monthly_with[monthly_with == 0]) == 0:
    print("WITH Trailing: PASS - All months have at least 1 trade")
else:
    print(f"WITH Trailing: FAIL - {len(monthly_with[monthly_with == 0])} months with 0 trades")

print("\n" + "="*80)
