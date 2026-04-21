import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
import numpy as np
from astra_v2.data.dukascopy import load_timeframe
from itertools import product

ATR_PERIOD = 20
START_DATE = "2020-01-01"
END_DATE = "2026-04-18"

def calculate_atr(df, period=20):
    high = df['high']
    low = df['low']
    close = df['close']
    tr1 = high - low
    tr2 = abs(high - close.shift(1))
    tr3 = abs(low - close.shift(1))
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    atr = tr.rolling(window=period).mean()
    return atr

def calculate_ema(df, period):
    return df['close'].ewm(span=period, adjust=False).mean()

def get_session_range(df, start_hour, end_hour):
    mask = (df.index.hour >= start_hour) & (df.index.hour < end_hour)
    session_bars = df[mask]
    if len(session_bars) == 0:
        return None, None
    range_high = session_bars['high'].max()
    range_low = session_bars['low'].min()
    return range_high, range_low

def run_short_variant(risk, tp_rr, stop_buffer, df, df_h4):
    trades = []
    active_trades = {}
    balance = 10000
    peak_balance = 10000
    max_dd = 0

    sessions = {
        'asian': {'range_hours': (0, 7), 'breakout_hours': (7, 10), 'min_range': 0.7, 'max_range': 3.0},
        'london': {'range_hours': (7, 12), 'breakout_hours': (13, 16), 'min_range': 0.3, 'max_range': 3.0},
        'ny': {'range_hours': (13, 17), 'breakout_hours': (18, 21), 'min_range': 0.5, 'max_range': 3.0}
    }

    dates = df.index.date
    unique_dates = sorted(set(dates))

    for date in unique_dates:
        day_data = df[df.index.date == date]
        if len(day_data) < 10:
            continue

        session_ranges = {}
        for sess_name, sess_params in sessions.items():
            high, low = get_session_range(day_data, *sess_params['range_hours'])
            session_ranges[sess_name] = (high, low)

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
                exit_trade = False
                if highs[i] >= trade['sl']:
                    pnl = (trade['entry'] - trade['sl']) * trade['size']
                    balance += pnl
                    trade['pnl'] = pnl
                    trade['status'] = 'sl'
                    exit_trade = True
                elif lows[i] <= trade['tp']:
                    pnl = (trade['entry'] - trade['tp']) * trade['size']
                    balance += pnl
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

            current_time = times[i]
            h4_bar = df_h4[df_h4.index <= current_time].iloc[-1] if len(df_h4[df_h4.index <= current_time]) > 0 else None

            if h4_bar is None or pd.isna(h4_bar['ema20']):
                continue

            is_bearish = h4_bar['close'] < h4_bar['ema20']
            if not is_bearish:
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

                    if closes[i] < range_low:
                        entry = closes[i]
                        sl = range_high + stop_buffer * atr
                        risk_amt = sl - entry
                        tp = entry - risk_amt * tp_rr
                        size = risk / risk_amt
                        active_trades[sess_name] = {
                            'entry': entry, 'sl': sl, 'tp': tp,
                            'size': size, 'direction': 'SHORT', 'range_type': sess_name
                        }

    for session_name, trade in active_trades.items():
        last_bar = df.iloc[-1]
        pnl = (trade['entry'] - last_bar['close']) * trade['size']
        balance += pnl
        trade['pnl'] = pnl
        trade['status'] = 'eod'
        trades.append(trade)

    if len(trades) == 0:
        return None

    trades_df = pd.DataFrame(trades)
    total_pnl = balance - 10000
    wins = trades_df[trades_df['pnl'] > 0]
    losses = trades_df[trades_df['pnl'] <= 0]
    total_profit = wins['pnl'].sum() if len(wins) > 0 else 0
    total_loss = abs(losses['pnl'].sum()) if len(losses) > 0 else 0
    pf = total_profit / total_loss if total_loss > 0 else 0

    return {
        'risk': risk,
        'tp_rr': tp_rr,
        'stop_buffer': stop_buffer,
        'trades': len(trades_df),
        'pnl': total_pnl,
        'pf': pf,
        'dd': max_dd
    }

print("SHORT STRATEGY GRID SEARCH")
print("Loading data...")
df = load_timeframe("M15", start=START_DATE, end=END_DATE, symbol="XAUUSD")
if 'datetime' in df.columns:
    df.set_index('datetime', inplace=True)
df = df.sort_index()
df['atr'] = calculate_atr(df, ATR_PERIOD)

df_h4 = df.resample('4h').agg({'open':'first','high':'max','low':'min','close':'last'}).dropna()
df_h4['ema20'] = calculate_ema(df_h4, 20)
print(f"Loaded {len(df)} M15 bars, {len(df_h4)} H4 bars")

risk_values = [50, 75, 100]
tp_rr_values = [2.5, 3.0, 3.5, 4.0]
stop_buffer_values = [0.2, 0.3, 0.4]

param_combinations = list(product(risk_values, tp_rr_values, stop_buffer_values))
print(f"Testing {len(param_combinations)} combinations")

results = []
for i, params in enumerate(param_combinations):
    print(f"Progress: {i + 1}/{len(param_combinations)}")
    result = run_short_variant(*params, df, df_h4)
    if result is not None:
        results.append(result)

print("\nTOP 10 RESULTS sorted by PnL")
print("Risk    TP_RR   STOP    Trades  PnL         PF      DD")

results.sort(key=lambda x: x['pnl'], reverse=True)

for r in results[:10]:
    print(f"${r['risk']:<6} {r['tp_rr']:<7.1f} {r['stop_buffer']:<7.1f} {r['trades']:<7} ${r['pnl']:<10,.0f} {r['pf']:<7.3f} {r['dd']:<6.2f}")

profitable = [r for r in results if r['pnl'] > 0 and r['dd'] < 15.0]
if len(profitable) > 0:
    best = profitable[0]
    print(f"\nBEST PROFITABLE SHORT: Risk=${best['risk']}, TP={best['tp_rr']}, STOP={best['stop_buffer']}")
    print(f"PnL: ${best['pnl']:,.0f}, PF: {best['pf']:.3f}, DD: {best['dd']:.2f}%, Trades: {best['trades']}")
else:
    print("\nNo profitable SHORT variants found with DD < 15%")
