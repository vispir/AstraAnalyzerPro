"""
Validation Backtest: Проверка что deployed logic дает 606 сделок и $57k PnL
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

# Parameters (from session_breakout_trader.py v3.0)
RISK_PER_TRADE = 158
TP_RR = 5.5
ATR_PERIOD = 14
ATR_BUFFER = 0.5
H4_EMA_PERIOD = 20

LONG_SESSIONS = {
    'asian': (7, 10),
    'london': (13, 16),
    'ny': (18, 21)
}

SHORT_TYPE1_LOOKBACK_H4_BARS = 5
SHORT_TYPE2_H4_LOOKBACK = 3
SHORT_TYPE2_ATR_MULTIPLIER = 2.0

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
print("VALIDATION BACKTEST: DEPLOYED LOGIC")
print("="*80)
print(f"Period: {df.index[0]} - {df.index[-1]}")
print(f"Initial Balance: $10,000")
print()

# Backtest
trades = []
balance = 10000
active_long = None
active_short = None

# SHORT state machine
short_type1_reversal_active = False
short_type1_reversal_h4_high = None
short_type2_reversal_active = False
short_type2_reversal_h4_high = None
last_h4_index = None

# LONG session tracking
session_highs = {}
session_lows = {}

dates = df.index.date
unique_dates = sorted(set(dates))

for date in unique_dates:
    day_data = df[df.index.date == date]

    if len(day_data) < 10:
        continue

    # Reset session tracking at start of each day
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

        # Get current H4 bar
        h4_bars = df_h4[df_h4.index <= current_time]
        if len(h4_bars) < max(SHORT_TYPE1_LOOKBACK_H4_BARS + 2, SHORT_TYPE2_H4_LOOKBACK + 1):
            continue

        current_h4 = h4_bars.iloc[-1]
        atr = atrs[i]

        if np.isnan(atr):
            continue

        # ================================================================
        # LONG TRADE MANAGEMENT
        # ================================================================
        if active_long is not None:
            # Step trailing
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

            # Check SL/TP
            if lows[i] <= active_long['sl']:
                pnl = (active_long['sl'] - active_long['entry']) / (active_long['entry'] - active_long['initial_sl']) * RISK_PER_TRADE
                balance += pnl
                trades.append({'date': active_long['entry_time'], 'exit_date': times[i], 'pnl': pnl, 'direction': 'LONG'})
                active_long = None
            elif highs[i] >= active_long['tp']:
                pnl = (active_long['tp'] - active_long['entry']) / (active_long['entry'] - active_long['initial_sl']) * RISK_PER_TRADE
                balance += pnl
                trades.append({'date': active_long['entry_time'], 'exit_date': times[i], 'pnl': pnl, 'direction': 'LONG'})
                active_long = None

        # ================================================================
        # SHORT TRADE MANAGEMENT
        # ================================================================
        if active_short is not None:
            # Step trailing
            profit_r = (active_short['entry'] - closes[i]) / (active_short['initial_sl'] - active_short['entry'])
            if profit_r >= 5.0:
                new_sl = active_short['entry'] - 4.0 * (active_short['initial_sl'] - active_short['entry'])
                active_short['sl'] = min(active_short['sl'], new_sl)
            elif profit_r >= 4.0:
                new_sl = active_short['entry'] - 3.0 * (active_short['initial_sl'] - active_short['entry'])
                active_short['sl'] = min(active_short['sl'], new_sl)
            elif profit_r >= 3.0:
                new_sl = active_short['entry'] - 2.0 * (active_short['initial_sl'] - active_short['entry'])
                active_short['sl'] = min(active_short['sl'], new_sl)
            elif profit_r >= 2.0:
                new_sl = active_short['entry'] - 1.0 * (active_short['initial_sl'] - active_short['entry'])
                active_short['sl'] = min(active_short['sl'], new_sl)

            # Check SL/TP
            if highs[i] >= active_short['sl']:
                pnl = (active_short['entry'] - active_short['sl']) / (active_short['initial_sl'] - active_short['entry']) * RISK_PER_TRADE
                balance += pnl
                trades.append({'date': active_short['entry_time'], 'exit_date': times[i], 'pnl': pnl, 'direction': 'SHORT'})
                active_short = None
                short_type1_reversal_active = False
                short_type2_reversal_active = False
            elif lows[i] <= active_short['tp']:
                pnl = (active_short['entry'] - active_short['tp']) / (active_short['initial_sl'] - active_short['entry']) * RISK_PER_TRADE
                balance += pnl
                trades.append({'date': active_short['entry_time'], 'exit_date': times[i], 'pnl': pnl, 'direction': 'SHORT'})
                active_short = None
                short_type1_reversal_active = False
                short_type2_reversal_active = False

        # ================================================================
        # LONG: SESSION BREAKOUT LOGIC
        # ================================================================
        if active_long is None:
            # Track session ranges
            for session_name, (start_hour, end_hour) in LONG_SESSIONS.items():
                if start_hour <= hour < end_hour:
                    if session_name not in session_highs:
                        session_highs[session_name] = highs[i]
                        session_lows[session_name] = lows[i]
                    else:
                        session_highs[session_name] = max(session_highs[session_name], highs[i])
                        session_lows[session_name] = min(session_lows[session_name], lows[i])

            # Check for breakout
            for session_name, (start_hour, end_hour) in LONG_SESSIONS.items():
                if session_name in session_highs and hour >= end_hour:
                    session_high = session_highs[session_name]
                    session_low = session_lows[session_name]

                    if closes[i] > session_high:
                        # H4 EMA20 filter
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

                        active_long = {
                            'entry': entry,
                            'sl': sl,
                            'initial_sl': sl,
                            'tp': tp,
                            'entry_time': times[i]
                        }

                        # Clear session tracking
                        del session_highs[session_name]
                        del session_lows[session_name]
                        break

        # ================================================================
        # SHORT: REVERSAL LOGIC
        # ================================================================
        if active_short is None:
            prev_h4 = h4_bars.iloc[-2]

            # Check if new H4 bar
            current_h4_index = current_h4.name
            if last_h4_index != current_h4_index:
                last_h4_index = current_h4_index

                # H4 EMA20 filter
                if pd.isna(current_h4['ema20']):
                    short_type1_reversal_active = False
                    short_type2_reversal_active = False
                    continue

                if current_h4['close'] >= current_h4['ema20']:
                    short_type1_reversal_active = False
                    short_type2_reversal_active = False
                    continue

                # Type 1: Historical High Reversal
                if not short_type1_reversal_active:
                    lookback_highs = h4_bars.iloc[-SHORT_TYPE1_LOOKBACK_H4_BARS-1:-1]['high']
                    historical_high = lookback_highs.max()

                    if current_h4['high'] > historical_high:
                        if current_h4['close'] < prev_h4['close']:
                            short_type1_reversal_active = True
                            short_type1_reversal_h4_high = current_h4['high']

                # Type 2: Local Reversal
                if not short_type2_reversal_active:
                    if len(h4_bars) >= SHORT_TYPE2_H4_LOOKBACK + 1:
                        lookback_bars = h4_bars.iloc[-SHORT_TYPE2_H4_LOOKBACK-1:-1]
                        price_change = current_h4['high'] - lookback_bars['low'].min()
                        h4_atr = current_h4.get('atr', atr)

                        if not np.isnan(h4_atr) and price_change >= SHORT_TYPE2_ATR_MULTIPLIER * h4_atr:
                            if current_h4['close'] < prev_h4['close']:
                                short_type2_reversal_active = True
                                short_type2_reversal_h4_high = current_h4['high']

            # M15 entry logic
            if i > 0:
                prev_m15_low = lows[i-1]

                # Type 1 entry
                if short_type1_reversal_active and closes[i] < prev_m15_low:
                    entry = closes[i]
                    sl = short_type1_reversal_h4_high + ATR_BUFFER * atr
                    risk = sl - entry

                    if risk > 0:
                        tp = entry - risk * TP_RR

                        active_short = {
                            'entry': entry,
                            'sl': sl,
                            'initial_sl': sl,
                            'tp': tp,
                            'entry_time': times[i]
                        }
                        short_type1_reversal_active = False

                # Type 2 entry
                elif short_type2_reversal_active and closes[i] < prev_m15_low:
                    entry = closes[i]
                    sl = short_type2_reversal_h4_high + ATR_BUFFER * atr
                    risk = sl - entry

                    if risk > 0:
                        tp = entry - risk * TP_RR

                        active_short = {
                            'entry': entry,
                            'sl': sl,
                            'initial_sl': sl,
                            'tp': tp,
                            'entry_time': times[i]
                        }
                        short_type2_reversal_active = False

# Results
trades_df = pd.DataFrame(trades)
long_df = trades_df[trades_df['direction'] == 'LONG']
short_df = trades_df[trades_df['direction'] == 'SHORT']

print("="*80)
print("RESULTS")
print("="*80)
print()
print(f"Total Trades: {len(trades_df)}")
print(f"  LONG: {len(long_df)} ({len(long_df)/len(trades_df)*100:.1f}%)")
print(f"  SHORT: {len(short_df)} ({len(short_df)/len(trades_df)*100:.1f}%)")
print()
print(f"Gross PnL: ${trades_df['pnl'].sum():,.2f}")
print(f"Win Rate: {len(trades_df[trades_df['pnl'] > 0]) / len(trades_df) * 100:.1f}%")
print(f"Final Balance: ${balance:,.2f}")
print(f"Total Return: {(balance - 10000) / 10000 * 100:.2f}%")
print()

# Compare with expected
print("="*80)
print("VALIDATION CHECK")
print("="*80)
expected_trades = 606
expected_pnl = 57274

trades_match = abs(len(trades_df) - expected_trades) <= 10
pnl_match = abs(trades_df['pnl'].sum() - expected_pnl) <= 1000

print(f"Expected: {expected_trades} trades, ${expected_pnl:,} PnL")
print(f"Got: {len(trades_df)} trades, ${trades_df['pnl'].sum():,.0f} PnL")
print()

if trades_match and pnl_match:
    print("✅ VALIDATION PASSED! Deployed logic matches expected results!")
else:
    print("❌ VALIDATION FAILED! Results don't match!")
    print(f"  Trades diff: {len(trades_df) - expected_trades}")
    print(f"  PnL diff: ${trades_df['pnl'].sum() - expected_pnl:,.0f}")

print("="*80)
