"""
Валидация session_breakout_trader.py из коммита 865b8e1
LONG only с range фильтрами (ATR=20)
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
import numpy as np
from pathlib import Path

# Load data
data_path = Path(__file__).parent.parent / "data_cache" / "dukascopy" / "m15" / "XAUUSD" / "xauusd_m15_2020-01-01_2026-04-18.parquet"
df = pd.read_parquet(data_path)
df = df.sort_index()

print("="*80)
print("ВАЛИДАЦИЯ: session_breakout_trader.py (865b8e1)")
print("="*80)
print(f"Period: {df.index[0]} - {df.index[-1]}")
print()

# Parameters from 865b8e1
RISK_PER_TRADE = 158
TP_RR = 5.5
USE_H4_EMA_FILTER = True
H4_EMA_PERIOD = 20
ATR_PERIOD = 20

ASIAN_PARAMS = {
    'tp_rr': 5.5,
    'stop_buffer_atr': 0.1,
    'min_range_atr': 0.7,
    'max_range_atr': 3.0,
    'range_hours': (0, 7),
    'breakout_hours': (7, 10)
}

LONDON_PARAMS = {
    'tp_rr': 5.5,
    'stop_buffer_atr': 0.3,
    'min_range_atr': 0.3,
    'max_range_atr': 3.0,
    'range_hours': (7, 12),
    'breakout_hours': (13, 16)
}

NY_PARAMS = {
    'tp_rr': 5.5,
    'stop_buffer_atr': 0.3,
    'min_range_atr': 0.5,
    'max_range_atr': 3.0,
    'range_hours': (13, 17),
    'breakout_hours': (18, 21)
}

# Calculate indicators
def calculate_atr(df, period):
    high = df['high']
    low = df['low']
    close = df['close']
    tr1 = high - low
    tr2 = abs(high - close.shift(1))
    tr3 = abs(low - close.shift(1))
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    return tr.rolling(window=period).mean()

def apply_step_trailing(active_trade, current_low, current_high, is_long=True):
    if is_long:
        risk = active_trade['entry'] - active_trade['initial_sl']
        profit_in_r = (current_low - active_trade['entry']) / risk
        if profit_in_r >= 5.0:
            new_sl = active_trade['entry'] + 4.0 * risk
            active_trade['sl'] = max(active_trade['sl'], new_sl)
        elif profit_in_r >= 4.0:
            new_sl = active_trade['entry'] + 3.0 * risk
            active_trade['sl'] = max(active_trade['sl'], new_sl)
        elif profit_in_r >= 3.0:
            new_sl = active_trade['entry'] + 2.0 * risk
            active_trade['sl'] = max(active_trade['sl'], new_sl)
        elif profit_in_r >= 2.0:
            new_sl = active_trade['entry'] + 1.0 * risk
            active_trade['sl'] = max(active_trade['sl'], new_sl)

df['atr'] = calculate_atr(df, ATR_PERIOD)

# H4 data
df_h4 = df.resample('4h').agg({'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last'}).dropna()
df_h4['ema20'] = df_h4['close'].ewm(span=H4_EMA_PERIOD, adjust=False).mean()

print(f"M15 bars: {len(df)}")
print(f"H4 bars: {len(df_h4)}")
print()

# Backtest
trades = []
balance = 10000
peak_balance = 10000
max_dd = 0
max_daily_dd = 0
active_long = None

dates = df.index.date
unique_dates = sorted(set(dates))

for date in unique_dates:
    day_start_balance = balance
    day_data = df[df.index.date == date]

    if len(day_data) < 10:
        continue

    session_highs = {}
    session_lows = {}

    highs = day_data['high'].to_numpy()
    lows = day_data['low'].to_numpy()
    closes = day_data['close'].to_numpy()
    atrs = day_data['atr'].to_numpy()
    hours = np.array([t.hour for t in day_data.index])
    times = day_data.index.to_numpy()

    for i in range(len(day_data)):
        current_time = times[i]
        hour = hours[i]

        h4_bars = df_h4[df_h4.index <= current_time]
        if len(h4_bars) < 2:
            continue

        current_h4 = h4_bars.iloc[-1]
        atr = atrs[i]

        if np.isnan(atr):
            continue

        # LONG TRADE MANAGEMENT
        if active_long is not None:
            apply_step_trailing(active_long, lows[i], highs[i], is_long=True)

            if lows[i] <= active_long['sl']:
                pnl = (active_long['sl'] - active_long['entry']) * active_long['size']
                balance += pnl
                trades.append({'entry_time': times[i], 'pnl': pnl, 'direction': 'LONG', 'session': active_long['session']})
                active_long = None
            elif highs[i] >= active_long['tp']:
                pnl = (active_long['tp'] - active_long['entry']) * active_long['size']
                balance += pnl
                trades.append({'entry_time': times[i], 'pnl': pnl, 'direction': 'LONG', 'session': active_long['session']})
                active_long = None

        # Update DD
        if balance > peak_balance:
            peak_balance = balance
        dd = (peak_balance - balance) / peak_balance * 100
        if dd > max_dd:
            max_dd = dd

        # LONG LOGIC
        if active_long is None:
            for session_name, params in [('asian', ASIAN_PARAMS), ('london', LONDON_PARAMS), ('ny', NY_PARAMS)]:
                breakout_start, breakout_end = params['breakout_hours']

                if breakout_start <= hour < breakout_end:
                    range_start, range_end = params['range_hours']
                    range_data = day_data[(day_data.index.hour >= range_start) & (day_data.index.hour < range_end)]

                    if len(range_data) > 0:
                        range_high = range_data['high'].max()
                        range_low = range_data['low'].min()
                        range_size = range_high - range_low

                        min_range = params['min_range_atr'] * atr
                        max_range = params['max_range_atr'] * atr

                        if min_range <= range_size <= max_range:
                            if closes[i] > range_high:
                                if USE_H4_EMA_FILTER:
                                    if pd.isna(current_h4['ema20']):
                                        continue
                                    if current_h4['close'] <= current_h4['ema20']:
                                        continue

                                entry = closes[i]
                                sl = range_low - params['stop_buffer_atr'] * atr
                                risk = entry - sl

                                if risk > 0:
                                    tp = entry + risk * params['tp_rr']
                                    size = RISK_PER_TRADE / risk

                                    active_long = {
                                        'entry': entry,
                                        'sl': sl,
                                        'initial_sl': sl,
                                        'tp': tp,
                                        'size': size,
                                        'entry_time': times[i],
                                        'session': session_name
                                    }
                                    break

    # Daily DD
    daily_dd = (day_start_balance - balance) / day_start_balance * 100 if day_start_balance > 0 else 0
    if daily_dd > max_daily_dd:
        max_daily_dd = daily_dd

# Results
trades_df = pd.DataFrame(trades)
long_df = trades_df[trades_df['direction'] == 'LONG']

winning_trades = trades_df[trades_df['pnl'] > 0]
losing_trades = trades_df[trades_df['pnl'] < 0]
gross_profit = winning_trades['pnl'].sum()
gross_loss = abs(losing_trades['pnl'].sum())
profit_factor = gross_profit / gross_loss if gross_loss > 0 else 0

print("="*80)
print("RESULTS")
print("="*80)
print()
print(f"Total Trades: {len(trades_df)}")
print(f"  LONG: {len(long_df)} (100.0%)")
print(f"  SHORT: 0 (0.0%)")
print()
print(f"Total PnL: ${trades_df['pnl'].sum():,.2f}")
print(f"  LONG PnL: ${long_df['pnl'].sum():,.2f}")
print(f"  SHORT PnL: $0.00")
print()
print(f"Win Rate: {len(winning_trades) / len(trades_df) * 100:.1f}%")
print(f"  LONG WR: {len(long_df[long_df['pnl'] > 0]) / len(long_df) * 100:.1f}%")
print()
print(f"Max DD: {max_dd:.2f}%")
print(f"Max Daily DD: {max_daily_dd:.2f}%")
print(f"Profit Factor: {profit_factor:.2f}")
print(f"Return: {(balance - 10000) / 10000 * 100:.1f}%")
print()

print("="*80)
print("BY SESSION (LONG)")
print("="*80)
for session in ['asian', 'london', 'ny']:
    session_trades = long_df[long_df['session'] == session]
    if len(session_trades) > 0:
        session_wins = session_trades[session_trades['pnl'] > 0]
        print(f"{session.upper()}: {len(session_trades)} trades, ${session_trades['pnl'].sum():,.0f} PnL, WR {len(session_wins)/len(session_trades)*100:.1f}%")
print()
print("="*80)
