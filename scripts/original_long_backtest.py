"""
Test Risk=$158 with trailing and analyze worst case scenarios
Starting balance: $9,950 (already lost $50)
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
TP_RR = 5.5
USE_H4_EMA_FILTER = True
USE_STEP_TRAILING = True

ASIAN_BASE = {'stop_buffer_atr': 0.1, 'min_range_atr': 0.7, 'max_range_atr': 3.0, 'range_hours': (0, 7), 'breakout_hours': (7, 10)}
LONDON_BASE = {'stop_buffer_atr': 0.3, 'min_range_atr': 0.3, 'max_range_atr': 3.0, 'range_hours': (7, 12), 'breakout_hours': (13, 16)}
NY_BASE = {'stop_buffer_atr': 0.3, 'min_range_atr': 0.5, 'max_range_atr': 3.0, 'range_hours': (13, 17), 'breakout_hours': (18, 21)}

ASIAN_PARAMS = {**ASIAN_BASE, 'tp_rr': TP_RR}
LONDON_PARAMS = {**LONDON_BASE, 'tp_rr': TP_RR}
NY_PARAMS = {**NY_BASE, 'tp_rr': TP_RR}

def run_backtest(risk_per_trade, starting_balance=10000):
    """Run backtest with specific risk"""

    balance = starting_balance
    peak_balance = starting_balance
    max_dd = 0
    max_daily_dd = 0
    trades = []
    active_trades = {}

    unique_days = df.index.normalize().unique()

    for day in unique_days:
        day_data = df[df.index.normalize() == day]
        if len(day_data) == 0:
            continue

        day_start_balance = balance

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

            # Update active trades with step trailing
            for session_name, trade in list(active_trades.items()):
                exit_trade = False

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
                            size = risk_per_trade / risk

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
                            size = risk_per_trade / risk

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
                            size = risk_per_trade / risk

                            active_trades['ny'] = {
                                'entry': entry, 'sl': sl, 'initial_sl': sl, 'tp': tp,
                                'size': size, 'direction': 'LONG', 'entry_time': times[i],
                                'range_type': 'ny'
                            }

        # Calculate daily DD
        if day_start_balance > 0:
            daily_dd = (day_start_balance - balance) / day_start_balance * 100
            if daily_dd > max_daily_dd:
                max_daily_dd = daily_dd

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

    return pd.DataFrame(trades), balance, max_dd, max_daily_dd

# Test Risk=$158 and Risk=$165
print("="*80)
print("COMPARING Risk=$158 vs Risk=$165 (with trailing)")
print("="*80)

print("\nTesting Risk=$158...")
trades_158, balance_158, dd_158, daily_dd_158 = run_backtest(158, starting_balance=10000)

print("Testing Risk=$165...")
trades_165, balance_165, dd_165, daily_dd_165 = run_backtest(165, starting_balance=10000)

# Find worst consecutive loss streaks
def find_worst_streak(trades_df):
    if not pd.api.types.is_datetime64_any_dtype(trades_df['exit_time']):
        trades_df['exit_time'] = pd.to_datetime(trades_df['exit_time'], utc=True)

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

    if max_streak > 0:
        worst_trades = trades_df.iloc[worst_streak_start:worst_streak_end+1]
        return max_streak, worst_trades
    return 0, None

streak_158, worst_158 = find_worst_streak(trades_158)
streak_165, worst_165 = find_worst_streak(trades_165)

# Calculate stats
wins_158 = trades_158[trades_158['pnl'] > 0]
wins_165 = trades_165[trades_165['pnl'] > 0]

wr_158 = len(wins_158) / len(trades_158) if len(trades_158) > 0 else 0
wr_165 = len(wins_165) / len(trades_165) if len(trades_165) > 0 else 0

pf_158 = wins_158['pnl'].sum() / abs(trades_158[trades_158['pnl'] <= 0]['pnl'].sum()) if len(trades_158[trades_158['pnl'] <= 0]) > 0 else 0
pf_165 = wins_165['pnl'].sum() / abs(trades_165[trades_165['pnl'] <= 0]['pnl'].sum()) if len(trades_165[trades_165['pnl'] <= 0]) > 0 else 0

# Print comparison
print("\n" + "="*80)
print("RESULTS COMPARISON")
print("="*80)
print(f"{'Metric':<30} {'Risk=$158':<20} {'Risk=$165':<20} {'Difference':<15}")
print("-"*85)
print(f"{'Total PnL':<30} ${balance_158 - 10000:>18,.0f} ${balance_165 - 10000:>18,.0f} ${(balance_165 - 10000) - (balance_158 - 10000):>+13,.0f}")
print(f"{'Final Balance':<30} ${balance_158:>18,.0f} ${balance_165:>18,.0f} ${balance_165 - balance_158:>+13,.0f}")
print(f"{'Max DD':<30} {dd_158:>17.2f}% {dd_165:>17.2f}% {dd_165 - dd_158:>+12.2f}%")
print(f"{'Max Daily DD':<30} {daily_dd_158:>17.2f}% {daily_dd_165:>17.2f}% {daily_dd_165 - daily_dd_158:>+12.2f}%")
print(f"{'Total Trades':<30} {len(trades_158):>18} {len(trades_165):>18} {len(trades_165) - len(trades_158):>+13}")
print(f"{'Win Rate':<30} {wr_158:>17.1%} {wr_165:>17.1%} {wr_165 - wr_158:>+12.1%}")
print(f"{'Profit Factor':<30} {pf_158:>18.3f} {pf_165:>18.3f} {pf_165 - pf_158:>+13.3f}")
print(f"{'Max consecutive losses':<30} {streak_158:>18} {streak_165:>18} {streak_165 - streak_158:>+13}")

if worst_158 is not None:
    loss_158 = worst_158['pnl'].sum()
    dd_from_streak_158 = (abs(loss_158) / 10000) * 100
    print(f"{'Worst streak loss':<30} ${loss_158:>18,.2f} ${worst_165['pnl'].sum():>18,.2f} ${worst_165['pnl'].sum() - loss_158:>+13,.2f}")
    print(f"{'DD from worst streak':<30} {dd_from_streak_158:>17.2f}% {(abs(worst_165['pnl'].sum()) / 10000) * 100:>17.2f}% {((abs(worst_165['pnl'].sum()) / 10000) * 100) - dd_from_streak_158:>+12.2f}%")

# Worst case analysis for $9,950 starting balance
print("\n" + "="*80)
print("WORST CASE ANALYSIS (Starting balance: $9,950)")
print("="*80)

print(f"\nRisk=$158:")
print(f"  10 consecutive losses: 10 x $158 = $1,580 loss")
print(f"  Balance after: $9,950 - $1,580 = $8,370")
print(f"  DD from $9,950: {(1580 / 9950) * 100:.2f}%")
print(f"  DD from $10,000: {(10000 - 8370) / 10000 * 100:.2f}%")

print(f"\nRisk=$165:")
print(f"  10 consecutive losses: 10 x $165 = $1,650 loss")
print(f"  Balance after: $9,950 - $1,650 = $8,300")
print(f"  DD from $9,950: {(1650 / 9950) * 100:.2f}%")
print(f"  DD from $10,000: {(10000 - 8300) / 10000 * 100:.2f}%")

# Funding Pips validation
print("\n" + "="*80)
print("FUNDING PIPS CHALLENGE VALIDATION")
print("="*80)

def check_limits(dd, daily_dd, trades, label):
    passes_dd = dd < 10.0
    passes_daily_dd = daily_dd < 5.0
    passes_trades = trades >= 150

    print(f"\n{label}:")
    print(f"  Max DD < 10%: {'PASS' if passes_dd else 'FAIL'} ({dd:.2f}%)")
    print(f"  Max Daily DD < 5%: {'PASS' if passes_daily_dd else 'FAIL'} ({daily_dd:.2f}%)")
    print(f"  Total Trades >= 150: {'PASS' if passes_trades else 'FAIL'} ({trades})")

    if passes_dd and passes_daily_dd and passes_trades:
        print(f"  Result: ALL PASSED")
        return True
    else:
        print(f"  Result: FAILED")
        return False

pass_158 = check_limits(dd_158, daily_dd_158, len(trades_158), "Risk=$158")
pass_165 = check_limits(dd_165, daily_dd_165, len(trades_165), "Risk=$165")

# Recommendation
print("\n" + "="*80)
print("RECOMMENDATION FOR $9,950 BALANCE")
print("="*80)

if pass_158 and pass_165:
    print("\nBoth variants pass all limits!")
    if balance_165 > balance_158:
        profit_diff = (balance_165 - 10000) - (balance_158 - 10000)
        dd_diff = dd_165 - dd_158
        print(f"\nRisk=$165: +${profit_diff:,.0f} more profit, but +{dd_diff:.2f}% more DD")
        print(f"Risk=$158: Safer option with lower DD ({dd_158:.2f}% vs {dd_165:.2f}%)")
        print(f"\nFor $9,950 balance (already lost $50):")
        print(f"  Risk=$158 is RECOMMENDED - more conservative after initial loss")
elif pass_158:
    print("\nRisk=$158: PASS - Use this for safety")
elif pass_165:
    print("\nRisk=$165: PASS - Use this for more profit")
else:
    print("\nNeither variant passes - need to reduce risk further")

print("\n" + "="*80)
