"""
Test different RISK levels with Step Trailing
Find optimal risk where worst case (6 losses) stays under $1,000 (10% DD)
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
import numpy as np
from astra_v2.data.dukascopy import load_timeframe

print("Testing RISK levels: 160, 165, 170, 175, 180")
print("With TP=5.5 and Step Trailing enabled")
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

print(f"Loaded {len(df)} M15 bars\n")
sys.stdout.flush()

def run_backtest(risk_per_trade):
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
                        size = risk_per_trade / risk_amt
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
    trades_df['is_win'] = trades_df['pnl'] > 0

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

    worst_streak_loss = 0
    if max_streak > 0:
        worst_streak = trades_df.iloc[max_streak_start:max_streak_start + max_streak]
        worst_streak_loss = abs(worst_streak['pnl'].sum())

    total_pnl = balance - 10000
    wins = trades_df[trades_df['pnl'] > 0]
    losses = trades_df[trades_df['pnl'] <= 0]
    total_profit = wins['pnl'].sum() if len(wins) > 0 else 0
    total_loss = abs(losses['pnl'].sum()) if len(losses) > 0 else 0
    pf = total_profit / total_loss if total_loss > 0 else 0

    return {
        'risk': risk_per_trade,
        'pnl': total_pnl,
        'max_dd': max_dd,
        'daily_dd': max_daily_dd,
        'max_streak': max_streak,
        'worst_loss': worst_streak_loss,
        'worst_case_6x': risk_per_trade * 6  # Theoretical worst case
    }

risk_values = [160, 165, 170, 175, 180]
results = []

for risk in risk_values:
    print(f"Testing Risk=${risk}...")
    sys.stdout.flush()
    result = run_backtest(risk)
    if result:
        results.append(result)

print("\n" + "="*100)
print("RESULTS")
print("="*100)
print(f"{'Risk':<6} {'PnL':<12} {'MaxDD%':<8} {'DailyDD%':<10} {'MaxStreak':<11} {'WorstLoss':<12} {'6xRisk':<10} {'Safe?':<6}")
print("-"*100)

for r in results:
    safe = "YES" if r['worst_case_6x'] <= 1000 else "NO"
    print(f"${r['risk']:<5} ${r['pnl']:<11,.0f} {r['max_dd']:<8.2f} {r['daily_dd']:<10.2f} "
          f"{r['max_streak']:<11} ${r['worst_loss']:<11,.0f} ${r['worst_case_6x']:<9} {safe:<6}")

print("="*100)

# Find best safe option
safe_options = [r for r in results if r['worst_case_6x'] <= 1000]
if safe_options:
    best = max(safe_options, key=lambda x: x['risk'])
    print(f"\nBEST SAFE OPTION: Risk=${best['risk']}")
    print(f"  PnL: ${best['pnl']:,.0f}")
    print(f"  Max DD: {best['max_dd']:.2f}%")
    print(f"  Worst case (6 losses): ${best['worst_case_6x']} (10% DD limit)")
    print(f"  Actual max streak: {best['max_streak']} losses = ${best['worst_loss']:,.0f}")
else:
    print("\nNo safe options found (all exceed $1,000 for 6 losses)")

print("="*100)
sys.stdout.flush()
