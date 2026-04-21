"""
Test TP variants from 6.0 to 7.0 with and without adaptive step trailing
Risk=$165 (safe for prop trading)
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from astra_v2.data.dukascopy import load_timeframe

# Load data
print("Loading M15 data...")
df = load_timeframe('M15', start='2020-01-01', end='2026-04-18', symbol='XAUUSD')
print(f"Loaded {len(df):,} M15 bars")

# Resample to H4 for EMA20 filter
print("\nResampling M15 to H4 for EMA20 filter...")
df_h4 = df.resample('4h').agg({'open':'first','high':'max','low':'min','close':'last'}).dropna()
df_h4['ema20'] = df_h4['close'].ewm(span=20, adjust=False).mean()
print(f"Resampled {len(df_h4):,} H4 bars, calculated EMA20")

# Calculate ATR on M15
df['atr'] = df['high'].rolling(14).max() - df['low'].rolling(14).min()

# Fixed parameters
RISK_PER_TRADE = 165
USE_H4_EMA_FILTER = True

ASIAN_BASE = {'stop_buffer_atr': 0.1, 'min_range_atr': 0.7, 'max_range_atr': 3.0, 'range_hours': (0, 7), 'breakout_hours': (7, 10)}
LONDON_BASE = {'stop_buffer_atr': 0.3, 'min_range_atr': 0.3, 'max_range_atr': 3.0, 'range_hours': (7, 12), 'breakout_hours': (13, 16)}
NY_BASE = {'stop_buffer_atr': 0.3, 'min_range_atr': 0.5, 'max_range_atr': 3.0, 'range_hours': (13, 17), 'breakout_hours': (18, 21)}

def run_backtest(tp_rr, use_trailing):
    """Run backtest with specific TP and trailing settings"""

    # Update session params with TP
    ASIAN_PARAMS = {**ASIAN_BASE, 'tp_rr': tp_rr}
    LONDON_PARAMS = {**LONDON_BASE, 'tp_rr': tp_rr}
    NY_PARAMS = {**NY_BASE, 'tp_rr': tp_rr}

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

            # Update active trades with adaptive step trailing
            for session_name, trade in list(active_trades.items()):
                exit_trade = False

                # Step trailing with 1R step (same logic as TP=5.5R)
                if use_trailing:
                    risk = abs(trade['entry'] - trade['initial_sl'])

                    if trade['direction'] == 'LONG':
                        profit_r = (highs[i] - trade['entry']) / risk

                        # Step trailing: 2R->1R, 3R->2R, 4R->3R, 5R->4R, 6R->5R, 7R->6R
                        # Use multiple 'if' (not elif) so all levels are checked
                        if profit_r >= 2.0:
                            trade['sl'] = max(trade['sl'], trade['entry'] + 1.0 * risk)
                        if profit_r >= 3.0:
                            trade['sl'] = max(trade['sl'], trade['entry'] + 2.0 * risk)
                        if profit_r >= 4.0:
                            trade['sl'] = max(trade['sl'], trade['entry'] + 3.0 * risk)
                        if profit_r >= 5.0:
                            trade['sl'] = max(trade['sl'], trade['entry'] + 4.0 * risk)
                        if profit_r >= 6.0:
                            trade['sl'] = max(trade['sl'], trade['entry'] + 5.0 * risk)
                        if profit_r >= 7.0:
                            trade['sl'] = max(trade['sl'], trade['entry'] + 6.0 * risk)

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
                        if profit_r >= 6.0:
                            trade['sl'] = min(trade['sl'], trade['entry'] - 5.0 * risk)
                        if profit_r >= 7.0:
                            trade['sl'] = min(trade['sl'], trade['entry'] - 6.0 * risk)

                # Check exit conditions
                if trade['direction'] == 'LONG':
                    if lows[i] <= trade['sl']:
                        pnl = (trade['sl'] - trade['entry']) * trade['size']
                        balance += pnl
                        trade['exit'] = trade['sl']
                        trade['pnl'] = pnl
                        trade['status'] = 'sl'
                        exit_trade = True
                    elif highs[i] >= trade['tp']:
                        pnl = (trade['tp'] - trade['entry']) * trade['size']
                        balance += pnl
                        trade['exit'] = trade['tp']
                        trade['pnl'] = pnl
                        trade['status'] = 'tp'
                        exit_trade = True
                else:  # SHORT
                    if highs[i] >= trade['sl']:
                        pnl = (trade['entry'] - trade['sl']) * trade['size']
                        balance += pnl
                        trade['exit'] = trade['sl']
                        trade['pnl'] = pnl
                        trade['status'] = 'sl'
                        exit_trade = True
                    elif lows[i] <= trade['tp']:
                        pnl = (trade['entry'] - trade['tp']) * trade['size']
                        balance += pnl
                        trade['exit'] = trade['tp']
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

    return {
        'tp_rr': tp_rr,
        'trailing': 'YES' if use_trailing else 'NO',
        'total_pnl': total_pnl,
        'balance': balance,
        'max_dd': max_dd,
        'max_daily_dd': max_daily_dd,
        'total_trades': total_trades,
        'win_rate': win_rate,
        'profit_factor': profit_factor
    }

# Test all variants
print("\n" + "="*80)
print("Testing TP variants from 6.0 to 7.0 with Risk=$165")
print("="*80)

results = []

for tp in [6.0, 6.5, 7.0]:
    for use_trailing in [True, False]:
        print(f"\nTesting TP={tp}R, Trailing={'YES' if use_trailing else 'NO'}...", end='', flush=True)
        result = run_backtest(tp, use_trailing)
        results.append(result)
        print(" Done")

# Print results table
print("\n" + "="*80)
print("RESULTS SUMMARY")
print("="*80)
print(f"{'TP':<6} {'Trailing':<10} {'PnL':<12} {'Balance':<12} {'Max DD':<10} {'Daily DD':<10} {'Trades':<8} {'WR':<8} {'PF':<8}")
print("-"*80)

for r in results:
    print(f"{r['tp_rr']:<6.1f} {r['trailing']:<10} ${r['total_pnl']:>10,.0f} ${r['balance']:>10,.0f} {r['max_dd']:>8.2f}% {r['max_daily_dd']:>8.2f}% {r['total_trades']:>6} {r['win_rate']:>6.1%} {r['profit_factor']:>6.3f}")

# Find best variant
print("\n" + "="*80)
print("BEST VARIANTS")
print("="*80)

# Best by PnL (with DD constraints)
valid_results = [r for r in results if r['max_dd'] < 10.0 and r['max_daily_dd'] < 5.0]
if valid_results:
    best_pnl = max(valid_results, key=lambda x: x['total_pnl'])
    print(f"\nBest PnL (within DD limits):")
    print(f"  TP={best_pnl['tp_rr']}R, Trailing={best_pnl['trailing']}")
    print(f"  PnL: ${best_pnl['total_pnl']:,.0f}, Max DD: {best_pnl['max_dd']:.2f}%, Daily DD: {best_pnl['max_daily_dd']:.2f}%")

    # Best by DD safety
    best_dd = min(valid_results, key=lambda x: x['max_dd'])
    print(f"\nSafest (lowest DD):")
    print(f"  TP={best_dd['tp_rr']}R, Trailing={best_dd['trailing']}")
    print(f"  PnL: ${best_dd['total_pnl']:,.0f}, Max DD: {best_dd['max_dd']:.2f}%, Daily DD: {best_dd['max_daily_dd']:.2f}%")
else:
    print("\nWARNING: No variants passed DD limits!")

print("\n" + "="*80)
