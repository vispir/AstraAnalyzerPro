"""
Worst Case Analysis: Max Consecutive Losses
Find longest losing streak and simulate starting from worst moment
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
import numpy as np
from astra_v2.data.dukascopy import load_timeframe

print("Analyzing Worst Case Scenario: Max Consecutive Losses")
print("="*80)
sys.stdout.flush()

df = load_timeframe("M15", start="2020-01-01", end="2026-04-18", symbol="XAUUSD")
if 'datetime' in df.columns:
    df.set_index('datetime', inplace=True)
df = df.sort_index()

high = df['high']
low = df['low']
close = df['close']
tr1 = high - low
tr2 = abs(high - close.shift(1))
tr3 = abs(low - close.shift(1))
tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
df['atr'] = tr.rolling(window=20).mean()

df_h4 = df.resample('4h').agg({'open':'first','high':'max','low':'min','close':'last'}).dropna()
df_h4['ema20'] = df_h4['close'].ewm(span=20, adjust=False).mean()

print(f"Loaded {len(df)} M15 bars")
sys.stdout.flush()

trades = []
active_trades = {}
balance = 10000
peak_balance = 10000

sessions = {
    'asian': {'range_hours': (0, 7), 'breakout_hours': (7, 10), 'min_range': 0.7, 'max_range': 3.0, 'stop_buffer': 0.1, 'tp_rr': 5.5},
    'london': {'range_hours': (7, 12), 'breakout_hours': (13, 16), 'min_range': 0.3, 'max_range': 3.0, 'stop_buffer': 0.3, 'tp_rr': 5.5},
    'ny': {'range_hours': (13, 17), 'breakout_hours': (18, 21), 'min_range': 0.5, 'max_range': 3.0, 'stop_buffer': 0.3, 'tp_rr': 5.5}
}

dates = df.index.date
unique_dates = sorted(set(dates))

for date in unique_dates:
    day_data = df[df.index.date == date]
    if len(day_data) < 10:
        continue

    session_ranges = {}
    for sess_name, sess_params in sessions.items():
        mask = (day_data.index.hour >= sess_params['range_hours'][0]) & (day_data.index.hour < sess_params['range_hours'][1])
        session_bars = day_data[mask]
        if len(session_bars) == 0:
            session_ranges[sess_name] = (None, None)
        else:
            session_ranges[sess_name] = (session_bars['high'].max(), session_bars['low'].min())

    highs = day_data['high'].to_numpy()
    lows = day_data['low'].to_numpy()
    closes = day_data['close'].to_numpy()
    atrs = day_data['atr'].to_numpy()
    times = day_data.index.to_numpy()
    hours = np.array([t.hour for t in day_data.index])

    for i in range(len(day_data)):
        atr = atrs[i]
        if np.isnan(atr):
            continue
        hour = hours[i]

        for session_name in list(active_trades.keys()):
            trade = active_trades[session_name]

            # Step Trailing Stop
            risk = trade['entry'] - trade['initial_sl']
            profit_r = (highs[i] - trade['entry']) / risk
            if profit_r >= 2.0:
                trade['sl'] = max(trade['sl'], trade['entry'] + 1.0 * risk)
            if profit_r >= 3.0:
                trade['sl'] = max(trade['sl'], trade['entry'] + 2.0 * risk)
            if profit_r >= 4.0:
                trade['sl'] = max(trade['sl'], trade['entry'] + 3.0 * risk)
            if profit_r >= 5.0:
                trade['sl'] = max(trade['sl'], trade['entry'] + 4.0 * risk)

            exit_trade = False
            if lows[i] <= trade['sl']:
                pnl = (trade['sl'] - trade['entry']) * trade['size']
                balance += pnl
                trade['pnl'] = pnl
                trade['exit_time'] = times[i]
                exit_trade = True
            elif highs[i] >= trade['tp']:
                pnl = (trade['tp'] - trade['entry']) * trade['size']
                balance += pnl
                trade['pnl'] = pnl
                trade['exit_time'] = times[i]
                exit_trade = True

            if exit_trade:
                trades.append(trade)
                del active_trades[session_name]
                if balance > peak_balance:
                    peak_balance = balance

        current_time = times[i]
        h4_bar = df_h4[df_h4.index <= current_time].iloc[-1] if len(df_h4[df_h4.index <= current_time]) > 0 else None

        if h4_bar is None or pd.isna(h4_bar['ema20']):
            continue

        is_bullish = h4_bar['close'] > h4_bar['ema20']
        if not is_bullish:
            continue

        for sess_name, sess_params in sessions.items():
            if sess_params['breakout_hours'][0] <= hour < sess_params['breakout_hours'][1]:
                if sess_name in active_trades:
                    continue

                range_high, range_low = session_ranges[sess_name]
                if range_high is None or range_low is None:
                    continue

                range_size = range_high - range_low
                if not (sess_params['min_range'] * atr <= range_size <= sess_params['max_range'] * atr):
                    continue

                if closes[i] > range_high:
                    entry = closes[i]
                    sl = range_low - sess_params['stop_buffer'] * atr
                    risk_amt = entry - sl
                    tp = entry + risk_amt * sess_params['tp_rr']
                    size = 200 / risk_amt
                    active_trades[sess_name] = {
                        'entry': entry, 'sl': sl, 'initial_sl': sl, 'tp': tp,
                        'size': size, 'direction': 'LONG', 'range_type': sess_name,
                        'entry_time': times[i]
                    }

for session_name, trade in active_trades.items():
    last_bar = df.iloc[-1]
    pnl = (last_bar['close'] - trade['entry']) * trade['size']
    balance += pnl
    trade['pnl'] = pnl
    trade['exit_time'] = df.index[-1]
    trades.append(trade)

trades_df = pd.DataFrame(trades)
trades_df['is_win'] = trades_df['pnl'] > 0

print(f"\nTotal trades: {len(trades_df)}")
print(f"Wins: {len(trades_df[trades_df['is_win']])}")
print(f"Losses: {len(trades_df[~trades_df['is_win']])}")
print()

# Find max consecutive losses
max_streak = 0
current_streak = 0
max_streak_start = 0
current_streak_start = 0

for i, is_win in enumerate(trades_df['is_win']):
    if not is_win:
        if current_streak == 0:
            current_streak_start = i
        current_streak += 1
        if current_streak > max_streak:
            max_streak = current_streak
            max_streak_start = current_streak_start
    else:
        current_streak = 0

print(f"MAX CONSECUTIVE LOSSES: {max_streak}")
print(f"Starting at trade index: {max_streak_start}")
print()

# Analyze worst streak
worst_streak = trades_df.iloc[max_streak_start:max_streak_start + max_streak]
total_loss = worst_streak['pnl'].sum()
print(f"Worst streak details:")
print(f"  Length: {max_streak} trades")
print(f"  Total loss: ${total_loss:,.2f}")
print(f"  Average loss per trade: ${total_loss / max_streak:,.2f}")
print(f"  Period: {worst_streak.iloc[0]['exit_time'].date()} to {worst_streak.iloc[-1]['exit_time'].date()}")
print()

# Simulate starting from worst case
initial_balance = 10000
worst_case_balance = initial_balance + total_loss
worst_case_dd = (initial_balance - worst_case_balance) / initial_balance * 100

print(f"WORST CASE SIMULATION:")
print(f"  Starting balance: ${initial_balance:,.0f}")
print(f"  Balance after {max_streak} losses: ${worst_case_balance:,.2f}")
print(f"  Drawdown: {worst_case_dd:.2f}%")
print(f"  Account survives: {'YES' if worst_case_balance > 0 else 'NO'}")
print(f"  Margin to $11,000 target: ${11000 - worst_case_balance:,.2f}")
print()

# Check if we can recover to $11,000
if worst_case_balance > 0:
    needed_profit = 11000 - worst_case_balance
    avg_win = trades_df[trades_df['is_win']]['pnl'].mean()
    trades_needed = int(np.ceil(needed_profit / avg_win))
    print(f"RECOVERY ANALYSIS:")
    print(f"  Profit needed to reach $11,000: ${needed_profit:,.2f}")
    print(f"  Average winning trade: ${avg_win:,.2f}")
    print(f"  Estimated winning trades needed: {trades_needed}")
    print(f"  With 50.8% WR, expected trades to get {trades_needed} wins: ~{int(trades_needed / 0.508)}")

print("="*80)
sys.stdout.flush()
