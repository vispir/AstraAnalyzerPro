"""
Backtest: ТОЛЬКО LONG (v4.0 - Multiple Positions)
Логика из session_breakout_trader.py - каждая сессия может иметь свою позицию одновременно
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

# Parameters (v4.0 production settings)
RISK_PER_TRADE = 120  # Консервативный риск для баланса $9,950
TP_RR = 5.5
ATR_PERIOD = 14
ATR_BUFFER = 0.5
H4_EMA_PERIOD = 20
USE_H4_EMA_FILTER = True

LONG_SESSIONS = {
    'asian': {
        'range_hours': (7, 10),
        'entry_start': 10,
        'entry_end': 24
    },
    'london': {
        'range_hours': (13, 16),
        'entry_start': 16,
        'entry_end': 24
    },
    'ny': {
        'range_hours': (13, 17),
        'entry_start': 18,
        'entry_end': 21
    }
}

# Calculate indicators
def calculate_atr(df, period=14):
    high = df['high']
    low = df['low']
    close = df['close']
    tr1 = high - low
    tr2 = abs(high - close.shift(1))
    tr3 = abs(low - close.shift(1))
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    return tr.rolling(window=period).mean()

df['atr'] = calculate_atr(df, ATR_PERIOD)

# H4 data
df_h4 = df.resample('4h').agg({'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last'}).dropna()
df_h4['atr'] = calculate_atr(df_h4, ATR_PERIOD)
df_h4['ema20'] = df_h4['close'].ewm(span=H4_EMA_PERIOD, adjust=False).mean()

print("="*80)
print("BACKTEST 1: ТОЛЬКО LONG")
print("="*80)
print(f"Period: {df.index[0]} - {df.index[-1]}")
print(f"Initial Balance: $10,000")
print()

# Backtest
trades = []
balance = 10000
peak_balance = 10000
max_dd = 0
max_daily_dd = 0
active_longs = {}  # v4.0: Multiple positions - dict by session_name

session_highs = {}
session_lows = {}
last_trading_date = None

dates = df.index.date
unique_dates = sorted(set(dates))

for date in unique_dates:
    day_start_balance = balance
    day_data = df[df.index.date == date]

    if len(day_data) < 10:
        continue

    if last_trading_date != date:
        session_highs = {}
        session_lows = {}
        last_trading_date = date

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

        # LONG TRADE MANAGEMENT - v4.0: Multiple positions
        sessions_to_close = []
        for session_name, active_long in active_longs.items():
            profit_r = (closes[i] - active_long['entry']) / (active_long['entry'] - active_long['initial_sl'])
            if profit_r >= 5.0:
                new_sl = active_long['entry'] + 4.0 * (active_long['entry'] - active_long['initial_sl'])
                active_long['sl'] = max(active_long['sl'], new_sl)
            elif profit_r >= 4.0:
                new_sl = active_long['entry'] + 3.0 * (active_long['entry'] - active_long['initial_sl'])
                active_long['sl'] = max(active_long['sl'], new_sl)
            elif profit_r >= 3.0:
                new_sl = active_long['entry'] + 2.0 * (active_long['entry'] - active_long['initial_sl'])
                active_long['sl'] = max(active_long['sl'], new_sl)
            elif profit_r >= 2.0:
                new_sl = active_long['entry'] + 1.0 * (active_long['entry'] - active_long['initial_sl'])
                active_long['sl'] = max(active_long['sl'], new_sl)

            if lows[i] <= active_long['sl']:
                pnl = (active_long['sl'] - active_long['entry']) / (active_long['entry'] - active_long['initial_sl']) * RISK_PER_TRADE
                balance += pnl
                trades.append({'date': active_long['entry_time'], 'exit_date': times[i], 'pnl': pnl, 'session': session_name})
                sessions_to_close.append(session_name)
            elif highs[i] >= active_long['tp']:
                pnl = (active_long['tp'] - active_long['entry']) / (active_long['entry'] - active_long['initial_sl']) * RISK_PER_TRADE
                balance += pnl
                trades.append({'date': active_long['entry_time'], 'exit_date': times[i], 'pnl': pnl, 'session': session_name})
                sessions_to_close.append(session_name)

        for session_name in sessions_to_close:
            del active_longs[session_name]

        # Update DD
        if balance > peak_balance:
            peak_balance = balance
        dd = (peak_balance - balance) / peak_balance * 100
        if dd > max_dd:
            max_dd = dd

        # LONG LOGIC - v4.0: Track session ranges
        for session_name, params in LONG_SESSIONS.items():
            start_hour, end_hour = params['range_hours']
            if start_hour <= hour < end_hour:
                if session_name not in session_highs:
                    session_highs[session_name] = highs[i]
                    session_lows[session_name] = lows[i]
                else:
                    session_highs[session_name] = max(session_highs[session_name], highs[i])
                    session_lows[session_name] = min(session_lows[session_name], lows[i])

        # LONG LOGIC - v4.0: Check breakouts for each session independently
        for session_name, params in LONG_SESSIONS.items():
            # Skip if already have active position for this session
            if session_name in active_longs:
                continue

            if session_name not in session_highs:
                continue

            entry_start = params['entry_start']
            entry_end = params['entry_end']

            if not (entry_start <= hour < entry_end):
                continue

            session_high = session_highs[session_name]
            session_low = session_lows[session_name]

            if closes[i] > session_high:
                if pd.isna(current_h4['ema20']):
                    continue
                if current_h4['close'] < current_h4['ema20']:
                    continue

                entry = closes[i]
                sl = session_low - ATR_BUFFER * atr
                risk = entry - sl

                if risk <= 0:
                    continue

                tp = entry + risk * TP_RR

                active_longs[session_name] = {
                    'entry': entry,
                    'sl': sl,
                    'tp': tp,
                    'initial_sl': sl,
                    'entry_time': times[i],
                    'session': session_name
                }

    # Daily DD
    daily_dd = (day_start_balance - balance) / day_start_balance * 100 if day_start_balance > 0 else 0
    if daily_dd > max_daily_dd:
        max_daily_dd = daily_dd

# Results
trades_df = pd.DataFrame(trades)
winning_trades = trades_df[trades_df['pnl'] > 0]
losing_trades = trades_df[trades_df['pnl'] < 0]
gross_profit = winning_trades['pnl'].sum()
gross_loss = abs(losing_trades['pnl'].sum())
profit_factor = gross_profit / gross_loss if gross_loss > 0 else 0

print("="*80)
print("RESULTS: LONG ONLY")
print("="*80)
print()
print(f"Total Trades: {len(trades_df)}")
print(f"Total PnL: ${trades_df['pnl'].sum():,.2f}")
print(f"Win Rate: {len(winning_trades) / len(trades_df) * 100:.1f}%")
print(f"Max DD: {max_dd:.2f}%")
print(f"Max Daily DD: {max_daily_dd:.2f}%")
print(f"Profit Factor: {profit_factor:.2f}")
print(f"Return: {(balance - 10000) / 10000 * 100:.1f}%")
print()

print("="*80)
print("YEARLY BREAKDOWN")
print("="*80)
trades_df['year'] = pd.to_datetime(trades_df['date']).dt.year
for year in sorted(trades_df['year'].unique()):
    year_trades = trades_df[trades_df['year'] == year]
    year_wins = year_trades[year_trades['pnl'] > 0]
    print(f"{year}: {len(year_trades)} trades, ${year_trades['pnl'].sum():,.0f} PnL, WR {len(year_wins)/len(year_trades)*100:.1f}%")
print()
print("="*80)
