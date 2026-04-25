"""
Комбинированный бэктест с ограничением SHORT:
LONG: все сессии (Asian 7-10, London 13-16, NY 18-21 UTC)
SHORT: только когда час < 18 UTC (не торгует в NY окне 18-21)
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
print("КОМБИНИРОВАННЫЙ: LONG (все сессии) + SHORT (только до 18:00 UTC)")
print("="*80)
print(f"Period: {df.index[0]} - {df.index[-1]}")
print()

# Parameters
RISK_PER_TRADE = 158
TP_RR = 5.5
ATR_PERIOD = 14
USE_H4_EMA_FILTER = True
H4_EMA_PERIOD = 20
ATR_BUFFER = 0.5

# LONG: Simple session windows
LONG_SESSIONS = {
    'asian': (7, 10),
    'london': (13, 16),
    'ny': (18, 21)
}

# SHORT parameters
SHORT_TYPE1_LOOKBACK_H4_BARS = 5
SHORT_TYPE2_H4_LOOKBACK = 3
SHORT_TYPE2_ATR_MULTIPLIER = 2.0
SHORT_MAX_HOUR = 18  # SHORT только до 18:00 UTC

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
    else:
        risk = active_trade['initial_sl'] - active_trade['entry']
        profit_in_r = (active_trade['entry'] - current_high) / risk
        if profit_in_r >= 5.0:
            new_sl = active_trade['entry'] - 4.0 * risk
            active_trade['sl'] = min(active_trade['sl'], new_sl)
        elif profit_in_r >= 4.0:
            new_sl = active_trade['entry'] - 3.0 * risk
            active_trade['sl'] = min(active_trade['sl'], new_sl)
        elif profit_in_r >= 3.0:
            new_sl = active_trade['entry'] - 2.0 * risk
            active_trade['sl'] = min(active_trade['sl'], new_sl)
        elif profit_in_r >= 2.0:
            new_sl = active_trade['entry'] - 1.0 * risk
            active_trade['sl'] = min(active_trade['sl'], new_sl)

df['atr'] = calculate_atr(df, ATR_PERIOD)

# H4 data
df_h4 = df.resample('4h').agg({'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last'}).dropna()
df_h4['atr'] = calculate_atr(df_h4, ATR_PERIOD)
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
active_short = None

short_type1_reversal_active = False
short_type1_reversal_h4_high = None
short_type2_reversal_active = False
short_type2_reversal_h4_high = None
last_h4_index = None

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
        if len(h4_bars) < max(SHORT_TYPE1_LOOKBACK_H4_BARS + 2, SHORT_TYPE2_H4_LOOKBACK + 1):
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

        # SHORT TRADE MANAGEMENT
        if active_short is not None:
            apply_step_trailing(active_short, lows[i], highs[i], is_long=False)

            if highs[i] >= active_short['sl']:
                pnl = (active_short['entry'] - active_short['sl']) * active_short['size']
                balance += pnl
                trades.append({'entry_time': times[i], 'pnl': pnl, 'direction': 'SHORT'})
                active_short = None
                short_type1_reversal_active = False
                short_type2_reversal_active = False
            elif lows[i] <= active_short['tp']:
                pnl = (active_short['entry'] - active_short['tp']) * active_short['size']
                balance += pnl
                trades.append({'entry_time': times[i], 'pnl': pnl, 'direction': 'SHORT'})
                active_short = None
                short_type1_reversal_active = False
                short_type2_reversal_active = False

        # Update DD
        if balance > peak_balance:
            peak_balance = balance
        dd = (peak_balance - balance) / peak_balance * 100
        if dd > max_dd:
            max_dd = dd

        # LONG LOGIC
        if active_long is None:
            for session_name, (start_hour, end_hour) in LONG_SESSIONS.items():
                if start_hour <= hour < end_hour:
                    if session_name not in session_highs:
                        session_highs[session_name] = highs[i]
                        session_lows[session_name] = lows[i]
                    else:
                        session_highs[session_name] = max(session_highs[session_name], highs[i])
                        session_lows[session_name] = min(session_lows[session_name], lows[i])

            for session_name, (start_hour, end_hour) in LONG_SESSIONS.items():
                if session_name in session_highs and hour >= end_hour:
                    session_high = session_highs[session_name]

                    if closes[i] > session_high:
                        if USE_H4_EMA_FILTER:
                            if pd.isna(current_h4['ema20']):
                                continue
                            if current_h4['close'] < current_h4['ema20']:
                                continue

                        entry = closes[i]
                        sl = session_lows[session_name] - ATR_BUFFER * atr
                        risk = entry - sl

                        if risk <= 0:
                            continue

                        tp = entry + risk * TP_RR
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

        # SHORT LOGIC (только до 18:00 UTC)
        if active_short is None and hour < SHORT_MAX_HOUR:
            prev_h4 = h4_bars.iloc[-2]

            current_h4_index = current_h4.name
            if last_h4_index != current_h4_index:
                last_h4_index = current_h4_index

                if USE_H4_EMA_FILTER:
                    if pd.isna(current_h4['ema20']):
                        short_type1_reversal_active = False
                        short_type2_reversal_active = False
                        continue

                    if current_h4['close'] >= current_h4['ema20']:
                        short_type1_reversal_active = False
                        short_type2_reversal_active = False
                        continue

                if not short_type1_reversal_active:
                    lookback_highs = h4_bars.iloc[-SHORT_TYPE1_LOOKBACK_H4_BARS-1:-1]['high']
                    historical_high = lookback_highs.max()

                    if current_h4['high'] > historical_high:
                        if current_h4['close'] < prev_h4['close']:
                            short_type1_reversal_active = True
                            short_type1_reversal_h4_high = current_h4['high']

                if not short_type2_reversal_active:
                    if len(h4_bars) >= SHORT_TYPE2_H4_LOOKBACK + 1:
                        lookback_bars = h4_bars.iloc[-SHORT_TYPE2_H4_LOOKBACK-1:-1]
                        price_change = current_h4['high'] - lookback_bars['low'].min()
                        h4_atr = current_h4.get('atr', atr)

                        if not np.isnan(h4_atr) and price_change >= SHORT_TYPE2_ATR_MULTIPLIER * h4_atr:
                            if current_h4['close'] < prev_h4['close']:
                                short_type2_reversal_active = True
                                short_type2_reversal_h4_high = current_h4['high']

            if i > 0:
                prev_m15_low = lows[i-1]

                if short_type1_reversal_active and closes[i] < prev_m15_low:
                    entry = closes[i]
                    sl = short_type1_reversal_h4_high + ATR_BUFFER * atr
                    risk = sl - entry

                    if risk > 0:
                        tp = entry - risk * TP_RR
                        size = RISK_PER_TRADE / risk

                        active_short = {
                            'entry': entry,
                            'sl': sl,
                            'initial_sl': sl,
                            'tp': tp,
                            'size': size,
                            'entry_time': times[i]
                        }
                        short_type1_reversal_active = False

                elif short_type2_reversal_active and closes[i] < prev_m15_low:
                    entry = closes[i]
                    sl = short_type2_reversal_h4_high + ATR_BUFFER * atr
                    risk = sl - entry

                    if risk > 0:
                        tp = entry - risk * TP_RR
                        size = RISK_PER_TRADE / risk

                        active_short = {
                            'entry': entry,
                            'sl': sl,
                            'initial_sl': sl,
                            'tp': tp,
                            'size': size,
                            'entry_time': times[i]
                        }
                        short_type2_reversal_active = False

    # Daily DD
    daily_dd = (day_start_balance - balance) / day_start_balance * 100 if day_start_balance > 0 else 0
    if daily_dd > max_daily_dd:
        max_daily_dd = daily_dd

# Results
trades_df = pd.DataFrame(trades)
long_df = trades_df[trades_df['direction'] == 'LONG']
short_df = trades_df[trades_df['direction'] == 'SHORT']

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
print(f"  LONG: {len(long_df)} ({len(long_df)/len(trades_df)*100:.1f}%)")
print(f"  SHORT: {len(short_df)} ({len(short_df)/len(trades_df)*100:.1f}%)")
print()
print(f"Total PnL: ${trades_df['pnl'].sum():,.2f}")
print(f"  LONG PnL: ${long_df['pnl'].sum():,.2f}")
print(f"  SHORT PnL: ${short_df['pnl'].sum():,.2f}")
print()
print(f"Win Rate: {len(winning_trades) / len(trades_df) * 100:.1f}%")
print(f"  LONG WR: {len(long_df[long_df['pnl'] > 0]) / len(long_df) * 100:.1f}%")
print(f"  SHORT WR: {len(short_df[short_df['pnl'] > 0]) / len(short_df) * 100:.1f}%")
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
