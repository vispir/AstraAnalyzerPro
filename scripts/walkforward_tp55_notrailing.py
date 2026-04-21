"""
Walk-Forward Test for Risk=$200, TP=5.5, No Trailing
Split into yearly periods to test stability
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
import numpy as np
from astra_v2.data.dukascopy import load_timeframe

print("Walk-Forward Test: Risk=$200, TP=5.5, No Trailing")
print("="*80)
sys.stdout.flush()

df_full = load_timeframe("M15", start="2020-01-01", end="2026-04-18", symbol="XAUUSD")
if 'datetime' in df_full.columns:
    df_full.set_index('datetime', inplace=True)
df_full = df_full.sort_index()

high = df_full['high']
low = df_full['low']
close = df_full['close']
tr1 = high - low
tr2 = abs(high - close.shift(1))
tr3 = abs(low - close.shift(1))
tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
df_full['atr'] = tr.rolling(window=20).mean()

df_h4_full = df_full.resample('4h').agg({'open':'first','high':'max','low':'min','close':'last'}).dropna()
df_h4_full['ema20'] = df_h4_full['close'].ewm(span=20, adjust=False).mean()

print(f"Loaded {len(df_full)} M15 bars total\n")
sys.stdout.flush()

def run_period(df, df_h4, period_name):
    trades = []
    active_trades = {}
    balance = 10000
    peak_balance = 10000
    max_dd = 0
    max_daily_dd = 0

    sessions = {
        'asian': {'range_hours': (0, 7), 'breakout_hours': (7, 10), 'min_range': 0.7, 'max_range': 3.0, 'stop_buffer': 0.1, 'tp_rr': 5.5},
        'london': {'range_hours': (7, 12), 'breakout_hours': (13, 16), 'min_range': 0.3, 'max_range': 3.0, 'stop_buffer': 0.3, 'tp_rr': 5.5},
        'ny': {'range_hours': (13, 17), 'breakout_hours': (18, 21), 'min_range': 0.5, 'max_range': 3.0, 'stop_buffer': 0.3, 'tp_rr': 5.5}
    }

    dates = df.index.date
    unique_dates = sorted(set(dates))

    for date in unique_dates:
        day_start_balance = balance
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

                # NO TRAILING - just check SL/TP
                exit_trade = False
                if lows[i] <= trade['sl']:
                    pnl = (trade['sl'] - trade['entry']) * trade['size']
                    balance += pnl
                    trade['pnl'] = pnl
                    exit_trade = True
                elif highs[i] >= trade['tp']:
                    pnl = (trade['tp'] - trade['entry']) * trade['size']
                    balance += pnl
                    trade['pnl'] = pnl
                    exit_trade = True

                if exit_trade:
                    trades.append(trade)
                    del active_trades[session_name]
                    if balance > peak_balance:
                        peak_balance = balance
                    dd = (peak_balance - balance) / peak_balance * 100
                    if dd > max_dd:
                        max_dd = dd

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
                            'entry': entry, 'sl': sl, 'tp': tp,
                            'size': size, 'direction': 'LONG', 'range_type': sess_name
                        }

        if day_start_balance > 0:
            daily_dd = (day_start_balance - balance) / day_start_balance * 100
            if daily_dd > max_daily_dd:
                max_daily_dd = daily_dd

    for session_name, trade in active_trades.items():
        last_bar = df.iloc[-1]
        pnl = (last_bar['close'] - trade['entry']) * trade['size']
        balance += pnl
        trade['pnl'] = pnl
        trades.append(trade)

    if len(trades) == 0:
        return None

    trades_df = pd.DataFrame(trades)
    total_pnl = balance - 10000
    wins = trades_df[trades_df['pnl'] > 0]
    losses = trades_df[trades_df['pnl'] <= 0]
    win_rate = len(wins) / len(trades_df) if len(trades_df) > 0 else 0
    total_profit = wins['pnl'].sum() if len(wins) > 0 else 0
    total_loss = abs(losses['pnl'].sum()) if len(losses) > 0 else 0
    pf = total_profit / total_loss if total_loss > 0 else 0

    return {
        'period': period_name,
        'pnl': total_pnl,
        'pf': pf,
        'dd': max_dd,
        'daily_dd': max_daily_dd,
        'trades': len(trades_df),
        'wr': win_rate
    }

# Walk-forward by year
periods = [
    ('2020', '2020-01-01', '2020-12-31'),
    ('2021', '2021-01-01', '2021-12-31'),
    ('2022', '2022-01-01', '2022-12-31'),
    ('2023', '2023-01-01', '2023-12-31'),
    ('2024', '2024-01-01', '2024-12-31'),
    ('2025', '2025-01-01', '2025-12-31'),
    ('2026', '2026-01-01', '2026-04-18')
]

results = []
for year, start, end in periods:
    df_period = df_full[(df_full.index >= start) & (df_full.index <= end)]
    df_h4_period = df_h4_full[(df_h4_full.index >= start) & (df_h4_full.index <= end)]

    result = run_period(df_period, df_h4_period, year)
    if result:
        results.append(result)

print("WALK-FORWARD RESULTS BY YEAR")
print("-"*80)
print(f"{'Year':<6} {'PnL':<12} {'PF':<8} {'DD%':<8} {'DailyDD%':<10} {'Trades':<8} {'WR%':<8}")
print("-"*80)

for r in results:
    print(f"{r['period']:<6} ${r['pnl']:<11,.0f} {r['pf']:<8.3f} {r['dd']:<8.2f} {r['daily_dd']:<10.2f} {r['trades']:<8} {r['wr']:<8.1%}")

print("-"*80)
total_pnl = sum(r['pnl'] for r in results)
max_dd_overall = max(r['dd'] for r in results)
max_daily_dd_overall = max(r['daily_dd'] for r in results)
total_trades = sum(r['trades'] for r in results)
avg_pf = sum(r['pf'] for r in results) / len(results)

print(f"\nTOTAL PnL: ${total_pnl:,.0f}")
print(f"MAX DD (any year): {max_dd_overall:.2f}%")
print(f"MAX Daily DD (any year): {max_daily_dd_overall:.2f}%")
print(f"Total Trades: {total_trades}")
print(f"Average PF: {avg_pf:.3f}")
print("="*80)
sys.stdout.flush()
