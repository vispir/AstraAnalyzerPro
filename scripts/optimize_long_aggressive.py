import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
import numpy as np
from astra_v2.data.dukascopy import load_timeframe
from itertools import product

print("Loading data...")
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

print(f"Loaded {len(df)} M15 bars, {len(df_h4)} H4 bars")
sys.stdout.flush()

def run_backtest(tp_rr, trailing_start, trailing_distance):
    trades = []
    active_trades = {}
    balance = 10000
    peak_balance = 10000
    max_dd = 0
    max_daily_dd = 0

    sessions = {
        'asian': {'range_hours': (0, 7), 'breakout_hours': (7, 10), 'min_range': 0.7, 'max_range': 3.0, 'stop_buffer': 0.1},
        'london': {'range_hours': (7, 12), 'breakout_hours': (13, 16), 'min_range': 0.3, 'max_range': 3.0, 'stop_buffer': 0.3},
        'ny': {'range_hours': (13, 17), 'breakout_hours': (18, 21), 'min_range': 0.5, 'max_range': 3.0, 'stop_buffer': 0.3}
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

                exit_trade = False
                if lows[i] <= trade['sl']:
                    pnl = (trade['sl'] - trade['entry']) * trade['size']
                    balance += pnl
                    trade['pnl'] = pnl
                    trade['status'] = 'sl'
                    exit_trade = True
                elif highs[i] >= trade['tp']:
                    pnl = (trade['tp'] - trade['entry']) * trade['size']
                    balance += pnl
                    trade['pnl'] = pnl
                    trade['status'] = 'tp'
                    exit_trade = True
                else:
                    # Trailing stop logic
                    if trailing_start is not None and trailing_distance is not None:
                        risk_amt = trade['entry'] - trade['initial_sl']
                        profit_r = (highs[i] - trade['entry']) / risk_amt
                        if profit_r >= trailing_start:
                            new_sl = highs[i] - trailing_distance * risk_amt
                            if new_sl > trade['sl']:
                                trade['sl'] = new_sl

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
                        tp = entry + risk_amt * tp_rr
                        size = 200 / risk_amt
                        active_trades[sess_name] = {
                            'entry': entry, 'sl': sl, 'initial_sl': sl, 'tp': tp,
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
    total_profit = wins['pnl'].sum() if len(wins) > 0 else 0
    total_loss = abs(losses['pnl'].sum()) if len(losses) > 0 else 0
    pf = total_profit / total_loss if total_loss > 0 else 0

    return {
        'tp_rr': tp_rr,
        'trailing_start': trailing_start if trailing_start is not None else 0,
        'trailing_dist': trailing_distance if trailing_distance is not None else 0,
        'trades': len(trades_df),
        'pnl': total_pnl,
        'pf': pf,
        'dd': max_dd,
        'daily_dd': max_daily_dd
    }

tp_rr_values = [3.0, 3.5, 4.0, 4.5, 5.0, 5.5, 6.0]
trailing_start_values = [None, 3.0, 4.0, 5.0]
trailing_distance_values = [None, 1.0, 1.5, 2.0]

param_combinations = []
for tp in tp_rr_values:
    param_combinations.append((tp, None, None))
    for ts in [3.0, 4.0, 5.0]:
        for td in [1.0, 1.5, 2.0]:
            param_combinations.append((tp, ts, td))

print(f"Testing {len(param_combinations)} combinations")
sys.stdout.flush()

results = []
for i, params in enumerate(param_combinations):
    if (i + 1) % 10 == 0:
        print(f"Progress: {i + 1}/{len(param_combinations)}")
        sys.stdout.flush()
    result = run_backtest(*params)
    if result is not None:
        results.append(result)

print(f"\nProgress: {len(param_combinations)}/{len(param_combinations)}")
sys.stdout.flush()

print("\nTOP 15 RESULTS sorted by PnL")
print("TP_RR   TrailStart  TrailDist  Trades  PnL          PF      DD      DailyDD")
sys.stdout.flush()

results.sort(key=lambda x: x['pnl'], reverse=True)

for r in results[:15]:
    ts = f"{r['trailing_start']:.1f}" if r['trailing_start'] > 0 else "None"
    td = f"{r['trailing_dist']:.1f}" if r['trailing_dist'] > 0 else "None"
    print(f"{r['tp_rr']:<7.1f} {ts:<11} {td:<10} {r['trades']:<7} ${r['pnl']:<11,.0f} {r['pf']:<7.3f} {r['dd']:<7.2f} {r['daily_dd']:<7.2f}")
    sys.stdout.flush()

best = [r for r in results if r['dd'] < 10.0 and r['daily_dd'] < 5.0]
if len(best) > 0:
    b = best[0]
    ts = f"{b['trailing_start']:.1f}" if b['trailing_start'] > 0 else "None"
    td = f"{b['trailing_dist']:.1f}" if b['trailing_dist'] > 0 else "None"
    print(f"\nBEST within DD limits: TP={b['tp_rr']}, TrailStart={ts}, TrailDist={td}")
    print(f"PnL: ${b['pnl']:,.0f}, PF: {b['pf']:.3f}, DD: {b['dd']:.2f}%, DailyDD: {b['daily_dd']:.2f}%, Trades: {b['trades']}")
else:
    print("\nNo variants found within DD limits (DD<10%, DailyDD<5%)")
sys.stdout.flush()
