"""
Compare current strategy (Risk=$165, TP=5.5R) with and without step trailing
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
print("Resampling M15 to H4 for EMA20 filter...")
df_h4 = df.resample('4h').agg({'open':'first','high':'max','low':'min','close':'last'}).dropna()
df_h4['ema20'] = df_h4['close'].ewm(span=20, adjust=False).mean()
print(f"Resampled {len(df_h4):,} H4 bars, calculated EMA20\n")

# Calculate ATR
df['atr'] = df['high'].rolling(14).max() - df['low'].rolling(14).min()

# Fixed parameters
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
    """Run backtest with or without step trailing"""

    balance = 10000
    peak_balance = 10000
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

            # Update session ranges
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

                # Step trailing: 2R->1R, 3R->2R, 4R->3R, 5R->4R
                if use_trailing:
                    risk = abs(trade['entry'] - trade['initial_sl'])

                    if trade['direction'] == 'LONG':
                        profit_r = (highs[i] - trade['entry']) / risk

                        if profit_r >= 2.0:
                            trade['sl'] = max(trade['sl'], trade['entry'] + 1.0 * risk)
                        if profit_r >= 3.0:
                            trade['sl'] = max(trade['sl'], trade['entry'] + 2.0 * risk)
                        if profit_r >= 4.0:
                            trade['sl'] = max(trade['sl'], trade['entry'] + 3.0 * risk)
                        if profit_r >= 5.0:
                            trade['sl'] = max(trade['sl'], trade['entry'] + 4.0 * risk)

                    else:  # SHORT
                        profit_r = (trade['entry'] - lows[i]) / risk

                        if profit_r >= 2.0:
                            trade['sl'] = min(trade['sl'], trade['entry'] - 1.0 * risk)
                        if profit_r >= 3.0:
                            trade['sl'] = min(trade['sl'], trade['entry'] - 2.0 * risk)
                        if profit_r >= 4.0:
                            trade['sl'] = min(trade['sl'], trade['entry'] - 3.0 * risk)
                        if profit_r >= 5.0:
                            trade['sl'] = min(trade['sl'], trade['entry'] - 4.0 * risk)

                # Check exit conditions
                if trade['direction'] == 'LONG':
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
                else:  # SHORT
                    if highs[i] >= trade['sl']:
                        pnl = (trade['entry'] - trade['sl']) * trade['size']
                        balance += pnl
                        trade['exit'] = trade['sl']
                        trade['exit_time'] = times[i]
                        trade['pnl'] = pnl
                        trade['status'] = 'sl'
                        exit_trade = True
                    elif lows[i] <= trade['tp']:
                        pnl = (trade['entry'] - trade['tp']) * trade['size']
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

            # Check for new entries (Asian)
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
                            tp = entry + risk * ASIAN_PARAMS['tp_rr']
                            size = RISK_PER_TRADE / risk

                            active_trades['asian'] = {
                                'entry': entry, 'sl': sl, 'initial_sl': sl, 'tp': tp,
                                'size': size, 'direction': 'LONG', 'entry_time': times[i],
                                'range_type': 'asian'
                            }

            # Check for new entries (London)
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
                            tp = entry + risk * LONDON_PARAMS['tp_rr']
                            size = RISK_PER_TRADE / risk

                            active_trades['london'] = {
                                'entry': entry, 'sl': sl, 'initial_sl': sl, 'tp': tp,
                                'size': size, 'direction': 'LONG', 'entry_time': times[i],
                                'range_type': 'london'
                            }

            # Check for new entries (NY)
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
                            tp = entry + risk * NY_PARAMS['tp_rr']
                            size = RISK_PER_TRADE / risk

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

    # Close remaining trades
    for session_name, trade in active_trades.items():
        last_bar = df.iloc[-1]
        if trade['direction'] == 'LONG':
            pnl = (last_bar['close'] - trade['entry']) * trade['size']
        else:
            pnl = (trade['entry'] - last_bar['close']) * trade['size']

        balance += pnl
        trade['exit'] = last_bar['close']
        trade['exit_time'] = df.index[-1]
        trade['pnl'] = pnl
        trade['status'] = 'eod'
        trades.append(trade)

    # Calculate stats
    trades_df = pd.DataFrame(trades)
    total_trades = len(trades_df)
    wins = trades_df[trades_df['pnl'] > 0]
    win_rate = len(wins) / total_trades if total_trades > 0 else 0

    total_profit = wins['pnl'].sum() if len(wins) > 0 else 0
    total_loss = abs(trades_df[trades_df['pnl'] <= 0]['pnl'].sum())
    profit_factor = total_profit / total_loss if total_loss > 0 else 0

    total_pnl = balance - 10000

    # Calculate average win/loss
    avg_win = wins['pnl'].mean() if len(wins) > 0 else 0
    avg_loss = trades_df[trades_df['pnl'] <= 0]['pnl'].mean() if len(trades_df[trades_df['pnl'] <= 0]) > 0 else 0

    return {
        'trailing': 'YES' if use_trailing else 'NO',
        'total_pnl': total_pnl,
        'balance': balance,
        'max_dd': max_dd,
        'max_daily_dd': max_daily_dd,
        'total_trades': total_trades,
        'win_rate': win_rate,
        'profit_factor': profit_factor,
        'avg_win': avg_win,
        'avg_loss': avg_loss,
        'trades_df': trades_df
    }

# Test both variants
print("="*80)
print("COMPARING: Risk=$165, TP=5.5R with and without Step Trailing")
print("="*80)

print("\nTesting WITH trailing...", end='', flush=True)
result_with = run_backtest(use_trailing=True)
print(" Done")

print("Testing WITHOUT trailing...", end='', flush=True)
result_without = run_backtest(use_trailing=False)
print(" Done")

# Print comparison
print("\n" + "="*80)
print("RESULTS COMPARISON")
print("="*80)
print(f"{'Metric':<25} {'WITH Trailing':<20} {'WITHOUT Trailing':<20} {'Difference':<15}")
print("-"*80)

print(f"{'Total PnL':<25} ${result_with['total_pnl']:>18,.0f} ${result_without['total_pnl']:>18,.0f} {result_without['total_pnl'] - result_with['total_pnl']:>+14,.0f}")
print(f"{'Final Balance':<25} ${result_with['balance']:>18,.0f} ${result_without['balance']:>18,.0f} {result_without['balance'] - result_with['balance']:>+14,.0f}")
print(f"{'Max DD':<25} {result_with['max_dd']:>17.2f}% {result_without['max_dd']:>17.2f}% {result_without['max_dd'] - result_with['max_dd']:>+13.2f}%")
print(f"{'Max Daily DD':<25} {result_with['max_daily_dd']:>17.2f}% {result_without['max_daily_dd']:>17.2f}% {result_without['max_daily_dd'] - result_with['max_daily_dd']:>+13.2f}%")
print(f"{'Total Trades':<25} {result_with['total_trades']:>18} {result_without['total_trades']:>18} {result_without['total_trades'] - result_with['total_trades']:>+14}")
print(f"{'Win Rate':<25} {result_with['win_rate']:>17.1%} {result_without['win_rate']:>17.1%} {result_without['win_rate'] - result_with['win_rate']:>+13.1%}")
print(f"{'Profit Factor':<25} {result_with['profit_factor']:>18.3f} {result_without['profit_factor']:>18.3f} {result_without['profit_factor'] - result_with['profit_factor']:>+14.3f}")
print(f"{'Avg Win':<25} ${result_with['avg_win']:>18,.2f} ${result_without['avg_win']:>18,.2f} ${result_without['avg_win'] - result_with['avg_win']:>+13,.2f}")
print(f"{'Avg Loss':<25} ${result_with['avg_loss']:>18,.2f} ${result_without['avg_loss']:>18,.2f} ${result_without['avg_loss'] - result_with['avg_loss']:>+13,.2f}")

# Check if passes Funding Pips limits
print("\n" + "="*80)
print("FUNDING PIPS CHALLENGE VALIDATION")
print("="*80)

def check_limits(result, label):
    passes_dd = result['max_dd'] < 10.0
    passes_daily_dd = result['max_daily_dd'] < 5.0
    passes_trades = result['total_trades'] >= 150

    print(f"\n{label}:")
    print(f"  Max DD < 10%: {'PASS' if passes_dd else 'FAIL'} ({result['max_dd']:.2f}%)")
    print(f"  Max Daily DD < 5%: {'PASS' if passes_daily_dd else 'FAIL'} ({result['max_daily_dd']:.2f}%)")
    print(f"  Total Trades >= 150: {'PASS' if passes_trades else 'FAIL'} ({result['total_trades']})")

    if passes_dd and passes_daily_dd and passes_trades:
        print(f"  Result: ALL PASSED")
        return True
    else:
        print(f"  Result: FAILED")
        return False

with_passed = check_limits(result_with, "WITH Trailing")
without_passed = check_limits(result_without, "WITHOUT Trailing")

# Recommendation
print("\n" + "="*80)
print("RECOMMENDATION")
print("="*80)

if with_passed and not without_passed:
    print("Use WITH Trailing - only this variant passes all limits")
    print(f"Trailing reduces DD by {result_without['max_dd'] - result_with['max_dd']:.2f}% while sacrificing ${result_without['total_pnl'] - result_with['total_pnl']:,.0f} in profit")
elif without_passed and not with_passed:
    print("Use WITHOUT Trailing - only this variant passes all limits")
    print(f"No trailing gives ${result_without['total_pnl'] - result_with['total_pnl']:,.0f} more profit")
elif with_passed and without_passed:
    print("Both variants pass all limits!")
    if result_without['total_pnl'] > result_with['total_pnl']:
        print(f"WITHOUT Trailing is better: ${result_without['total_pnl'] - result_with['total_pnl']:,.0f} more profit")
        print(f"But DD is {result_without['max_dd'] - result_with['max_dd']:.2f}% higher - consider risk tolerance")
    else:
        print(f"WITH Trailing is better: safer DD ({result_with['max_dd']:.2f}% vs {result_without['max_dd']:.2f}%)")
else:
    print("Neither variant passes all limits - need to adjust parameters")

print("="*80)
