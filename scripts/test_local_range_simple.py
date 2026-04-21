"""
Local Range Breakout - Simple Test
Single parameter set to verify logic works
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
import numpy as np
from astra_v2.data.dukascopy import load_timeframe

START_DATE = "2020-01-01"
END_DATE = "2026-04-18"
ATR_PERIOD = 20
RISK_PER_TRADE = 100
H4_EMA_PERIOD = 20

# Fixed parameters for single test
LOOKBACK = 20
MIN_RANGE_ATR = 0.5
MAX_RANGE_ATR = 3.0
STOP_BUFFER = 0.3
TP_RR = 3.0

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

print("=" * 80)
print("LOCAL RANGE BREAKOUT - SIMPLE TEST")
print("=" * 80)

print("\nLoading M15 data...")
df = load_timeframe("M15", start=START_DATE, end=END_DATE, symbol="XAUUSD")
if 'datetime' in df.columns:
    df.set_index('datetime', inplace=True)
df = df.sort_index()
df['atr'] = calculate_atr(df, ATR_PERIOD)
print(f"Loaded {len(df):,} M15 bars")

print("Resampling to H4...")
df_h4 = df.resample('4h').agg({'open':'first','high':'max','low':'min','close':'last'}).dropna()
df_h4['ema20'] = calculate_ema(df_h4, H4_EMA_PERIOD)
df_h4['atr'] = calculate_atr(df_h4, ATR_PERIOD)
print(f"Resampled {len(df_h4):,} H4 bars")

print("\nRunning backtest...")
print(f"LOOKBACK={LOOKBACK}, MIN_R={MIN_RANGE_ATR}, MAX_R={MAX_RANGE_ATR}, STOP={STOP_BUFFER}, TP_RR={TP_RR}")

trades = []
active_trade = None
balance = 10000
peak_balance = 10000
max_dd = 0

highs = df['high'].to_numpy()
lows = df['low'].to_numpy()
closes = df['close'].to_numpy()
atrs = df['atr'].to_numpy()
times = df.index.to_numpy()

h4_times = df_h4.index.to_numpy()
h4_closes = df_h4['close'].to_numpy()
h4_emas = df_h4['ema20'].to_numpy()
h4_atrs = df_h4['atr'].to_numpy()

dates = df.index.date
unique_dates = sorted(set(dates))
total_days = len(unique_dates)

for day_num, date in enumerate(unique_dates):
    if (day_num + 1) % 200 == 0:
        print(f"Progress: {day_num + 1}/{total_days} days, Trades: {len(trades)}, Balance: ${balance:,.0f}")

    day_mask = df.index.date == date
    day_indices = np.where(day_mask)[0]

    if len(day_indices) < LOOKBACK + 5:
        continue

    for idx in day_indices:
        if idx < LOOKBACK + 3:
            continue

        atr = atrs[idx]
        if np.isnan(atr):
            continue

        # Check exit
        if active_trade is not None:
            if active_trade['direction'] == 'LONG':
                risk = active_trade['entry'] - active_trade['initial_sl']
                if highs[idx] >= active_trade['entry'] + risk:
                    active_trade['sl'] = max(active_trade['sl'], active_trade['entry'])

                if lows[idx] <= active_trade['sl']:
                    pnl = (active_trade['sl'] - active_trade['entry']) * active_trade['size']
                    balance += pnl
                    trades.append({'pnl': pnl, 'status': 'sl'})
                    active_trade = None
                    if balance > peak_balance:
                        peak_balance = balance
                    dd = (peak_balance - balance) / peak_balance * 100
                    if dd > max_dd:
                        max_dd = dd
                elif highs[idx] >= active_trade['tp']:
                    pnl = (active_trade['tp'] - active_trade['entry']) * active_trade['size']
                    balance += pnl
                    trades.append({'pnl': pnl, 'status': 'tp'})
                    active_trade = None
                    if balance > peak_balance:
                        peak_balance = balance
            else:
                risk = active_trade['initial_sl'] - active_trade['entry']
                if lows[idx] <= active_trade['entry'] - risk:
                    active_trade['sl'] = min(active_trade['sl'], active_trade['entry'])

                if highs[idx] >= active_trade['sl']:
                    pnl = (active_trade['entry'] - active_trade['sl']) * active_trade['size']
                    balance += pnl
                    trades.append({'pnl': pnl, 'status': 'sl'})
                    active_trade = None
                    if balance > peak_balance:
                        peak_balance = balance
                    dd = (peak_balance - balance) / peak_balance * 100
                    if dd > max_dd:
                        max_dd = dd
                elif lows[idx] <= active_trade['tp']:
                    pnl = (active_trade['entry'] - active_trade['tp']) * active_trade['size']
                    balance += pnl
                    trades.append({'pnl': pnl, 'status': 'tp'})
                    active_trade = None
                    if balance > peak_balance:
                        peak_balance = balance

        # Entry logic
        if active_trade is None:
            range_start = idx - LOOKBACK - 3
            range_end = idx - 3
            if range_start < 0:
                continue

            range_high = highs[range_start:range_end].max()
            range_low = lows[range_start:range_end].min()
            range_size = range_high - range_low

            if not (MIN_RANGE_ATR * atr <= range_size <= MAX_RANGE_ATR * atr):
                continue

            current_time = times[idx]
            h4_idx = np.searchsorted(h4_times, current_time, side='right') - 1
            if h4_idx < 0 or h4_idx >= len(h4_times):
                continue
            if np.isnan(h4_emas[h4_idx]) or np.isnan(h4_atrs[h4_idx]):
                continue

            h4_close = h4_closes[h4_idx]
            h4_ema = h4_emas[h4_idx]
            h4_atr = h4_atrs[h4_idx]

            if h4_close > h4_ema and abs(h4_close - h4_ema) >= 0.3 * h4_atr:
                trend = 'up'
            elif h4_close < h4_ema and abs(h4_close - h4_ema) >= 0.3 * h4_atr:
                trend = 'down'
            else:
                trend = 'neutral'

            if closes[idx] > range_high and (trend == 'up' or trend == 'neutral'):
                entry = closes[idx]
                sl = range_low - STOP_BUFFER * atr
                risk = entry - sl
                tp = entry + risk * TP_RR
                size = RISK_PER_TRADE / risk
                active_trade = {
                    'entry': entry, 'sl': sl, 'initial_sl': sl, 'tp': tp,
                    'size': size, 'direction': 'LONG'
                }
            elif closes[idx] < range_low and (trend == 'down' or trend == 'neutral'):
                entry = closes[idx]
                sl = range_high + STOP_BUFFER * atr
                risk = sl - entry
                tp = entry - risk * TP_RR
                size = RISK_PER_TRADE / risk
                active_trade = {
                    'entry': entry, 'sl': sl, 'initial_sl': sl, 'tp': tp,
                    'size': size, 'direction': 'SHORT'
                }

if active_trade is not None:
    last_close = closes[-1]
    if active_trade['direction'] == 'LONG':
        pnl = (last_close - active_trade['entry']) * active_trade['size']
    else:
        pnl = (active_trade['entry'] - last_close) * active_trade['size']
    balance += pnl
    trades.append({'pnl': pnl, 'status': 'eod'})

print("\n" + "=" * 80)
print("RESULTS")
print("=" * 80)

if len(trades) == 0:
    print("No trades generated!")
else:
    trades_df = pd.DataFrame(trades)
    wins = trades_df[trades_df['pnl'] > 0]
    losses = trades_df[trades_df['pnl'] <= 0]
    win_rate = len(wins) / len(trades_df) * 100
    total_profit = wins['pnl'].sum() if len(wins) > 0 else 0
    total_loss = abs(losses['pnl'].sum()) if len(losses) > 0 else 0
    profit_factor = total_profit / total_loss if total_loss > 0 else 0
    total_pnl = balance - 10000

    print(f"Total Trades: {len(trades_df)}")
    print(f"Win Rate: {win_rate:.1f}%")
    print(f"Profit Factor: {profit_factor:.3f}")
    print(f"Total PnL: ${total_pnl:,.0f}")
    print(f"Max DD: {max_dd:.2f}%")
    print(f"Final Balance: ${balance:,.0f}")
