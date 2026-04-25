"""
Анализ MT5 свечей: проверка должны ли были открыться сделки
"""
import json
import pandas as pd
import numpy as np
from datetime import datetime, timezone

# Load MT5 candles
with open(r'C:\Users\Администратор\Downloads\mt5_candles_rows (7).json', 'r', encoding='utf-8') as f:
    data = json.load(f)

# Convert to DataFrame
df = pd.DataFrame(data)
df['time'] = pd.to_datetime(df['time'])
df = df.set_index('time')
df['open'] = df['open'].astype(float)
df['high'] = df['high'].astype(float)
df['low'] = df['low'].astype(float)
df['close'] = df['close'].astype(float)
df = df.sort_index()

print("="*80)
print("АНАЛИЗ MT5 СВЕЧЕЙ")
print("="*80)
print(f"Period: {df.index[0]} to {df.index[-1]}")
print(f"Total M15 candles: {len(df)}")
print()

# Calculate ATR
def calculate_atr(df, period=14):
    high = df['high']
    low = df['low']
    close = df['close']
    tr1 = high - low
    tr2 = abs(high - close.shift(1))
    tr3 = abs(low - close.shift(1))
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    return tr.rolling(window=period).mean()

df['atr'] = calculate_atr(df, 14)

# Resample to H4
df_h4 = df.resample('4h').agg({'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last'}).dropna()
df_h4['atr'] = calculate_atr(df_h4, 14)
df_h4['ema20'] = df_h4['close'].ewm(span=20, adjust=False).mean()

print("H4 data:")
print(f"Total H4 bars: {len(df_h4)}")
print(f"Last H4 bar: {df_h4.index[-1]}")
print(f"Last H4 close: {df_h4['close'].iloc[-1]:.2f}")
print(f"Last H4 EMA20: {df_h4['ema20'].iloc[-1]:.2f}")
print(f"Last H4 ATR: {df_h4['atr'].iloc[-1]:.2f}")
print()

# Parameters
LONG_SESSIONS = {
    'asian': (7, 10),
    'london': (13, 16),
    'ny': (18, 21)
}
SHORT_TYPE1_LOOKBACK_H4_BARS = 5
SHORT_TYPE2_H4_LOOKBACK = 3
SHORT_TYPE2_ATR_MULTIPLIER = 2.0
ATR_BUFFER = 0.5
TP_RR = 5.5

# Analyze each day
dates = df.index.date
unique_dates = sorted(set(dates))

print("="*80)
print("АНАЛИЗ ПО ДНЯМ")
print("="*80)

for date in unique_dates:
    day_data = df[df.index.date == date]

    if len(day_data) == 0:
        continue

    print()
    print(f"DATE: {date}")
    print("-"*80)

    # Check LONG conditions
    session_highs = {}
    session_lows = {}
    long_signals = []

    for idx, bar in day_data.iterrows():
        hour = idx.hour

        # Track session ranges
        for session_name, (start_hour, end_hour) in LONG_SESSIONS.items():
            if start_hour <= hour < end_hour:
                if session_name not in session_highs:
                    session_highs[session_name] = bar['high']
                    session_lows[session_name] = bar['low']
                else:
                    session_highs[session_name] = max(session_highs[session_name], bar['high'])
                    session_lows[session_name] = min(session_lows[session_name], bar['low'])

        # Check for breakout
        for session_name, (start_hour, end_hour) in LONG_SESSIONS.items():
            if session_name in session_highs and hour >= end_hour:
                session_high = session_highs[session_name]
                session_low = session_lows[session_name]

                if bar['close'] > session_high:
                    # Check H4 EMA20 filter
                    h4_bar = df_h4[df_h4.index <= idx].iloc[-1] if len(df_h4[df_h4.index <= idx]) > 0 else None

                    if h4_bar is not None and not pd.isna(h4_bar['ema20']):
                        if h4_bar['close'] >= h4_bar['ema20']:
                            entry = bar['close']
                            sl = session_low - ATR_BUFFER * bar['atr']
                            risk = entry - sl
                            tp = entry + risk * TP_RR

                            long_signals.append({
                                'time': idx,
                                'session': session_name,
                                'entry': entry,
                                'sl': sl,
                                'tp': tp,
                                'range_high': session_high,
                                'range_low': session_low,
                                'h4_close': h4_bar['close'],
                                'h4_ema20': h4_bar['ema20']
                            })

                            # Clear session after signal
                            del session_highs[session_name]
                            del session_lows[session_name]

    # Check SHORT conditions
    short_signals = []
    short_type1_active = False
    short_type1_h4_high = None
    short_type2_active = False
    short_type2_h4_high = None
    last_h4_idx = None

    for i, (idx, bar) in enumerate(day_data.iterrows()):
        hour = idx.hour

        if hour < 0 or hour >= 21:
            continue

        h4_bars = df_h4[df_h4.index <= idx]
        if len(h4_bars) < SHORT_TYPE1_LOOKBACK_H4_BARS + 2:
            continue

        current_h4 = h4_bars.iloc[-1]
        prev_h4 = h4_bars.iloc[-2]

        # Check if new H4 bar
        if last_h4_idx != current_h4.name:
            last_h4_idx = current_h4.name

            # H4 EMA20 filter
            if not pd.isna(current_h4['ema20']) and current_h4['close'] < current_h4['ema20']:
                # Type 1: Historical High
                if not short_type1_active:
                    lookback_highs = h4_bars.iloc[-SHORT_TYPE1_LOOKBACK_H4_BARS-1:-1]['high']
                    historical_high = lookback_highs.max()

                    if current_h4['high'] > historical_high and current_h4['close'] < prev_h4['close']:
                        short_type1_active = True
                        short_type1_h4_high = current_h4['high']

                # Type 2: Strong Move
                if not short_type2_active and len(h4_bars) >= SHORT_TYPE2_H4_LOOKBACK + 1:
                    lookback_bars = h4_bars.iloc[-SHORT_TYPE2_H4_LOOKBACK-1:-1]
                    price_change = current_h4['high'] - lookback_bars['low'].min()
                    h4_atr = current_h4['atr']

                    if not np.isnan(h4_atr) and price_change >= SHORT_TYPE2_ATR_MULTIPLIER * h4_atr:
                        if current_h4['close'] < prev_h4['close']:
                            short_type2_active = True
                            short_type2_h4_high = current_h4['high']

        # M15 entry
        if i > 0:
            prev_m15_low = day_data.iloc[i-1]['low']

            if short_type1_active and bar['close'] < prev_m15_low:
                entry = bar['close']
                sl = short_type1_h4_high + ATR_BUFFER * bar['atr']
                risk = sl - entry
                tp = entry - risk * TP_RR

                short_signals.append({
                    'time': idx,
                    'type': 'Type1',
                    'entry': entry,
                    'sl': sl,
                    'tp': tp,
                    'h4_high': short_type1_h4_high,
                    'h4_close': current_h4['close'],
                    'h4_ema20': current_h4['ema20']
                })
                short_type1_active = False

            elif short_type2_active and bar['close'] < prev_m15_low:
                entry = bar['close']
                sl = short_type2_h4_high + ATR_BUFFER * bar['atr']
                risk = sl - entry
                tp = entry - risk * TP_RR

                short_signals.append({
                    'time': idx,
                    'type': 'Type2',
                    'entry': entry,
                    'sl': sl,
                    'tp': tp,
                    'h4_high': short_type2_h4_high,
                    'h4_close': current_h4['close'],
                    'h4_ema20': current_h4['ema20']
                })
                short_type2_active = False

    # Print results
    if len(long_signals) > 0:
        print(f"\nLONG SIGNALS: {len(long_signals)}")
        for sig in long_signals:
            print(f"  {sig['time']} - {sig['session'].upper()}")
            print(f"    Entry: {sig['entry']:.2f}, SL: {sig['sl']:.2f}, TP: {sig['tp']:.2f}")
            print(f"    Range: {sig['range_low']:.2f} - {sig['range_high']:.2f}")
            print(f"    H4: close={sig['h4_close']:.2f} > EMA20={sig['h4_ema20']:.2f}")
    else:
        print("\nLONG SIGNALS: 0")
        print("  Причины:")
        if len(session_highs) > 0:
            for sess, high in session_highs.items():
                low = session_lows[sess]
                print(f"    {sess}: range {low:.2f}-{high:.2f}, нет пробоя или EMA20 фильтр")
        else:
            print("    Нет активных сессий или не сформированы рейнджи")

    if len(short_signals) > 0:
        print(f"\nSHORT SIGNALS: {len(short_signals)}")
        for sig in short_signals:
            print(f"  {sig['time']} - {sig['type']}")
            print(f"    Entry: {sig['entry']:.2f}, SL: {sig['sl']:.2f}, TP: {sig['tp']:.2f}")
            print(f"    H4: close={sig['h4_close']:.2f} < EMA20={sig['h4_ema20']:.2f}")
    else:
        print("\nSHORT SIGNALS: 0")
        print("  Причины:")
        last_h4 = df_h4[df_h4.index.date == date].iloc[-1] if len(df_h4[df_h4.index.date == date]) > 0 else None
        if last_h4 is not None:
            if last_h4['close'] >= last_h4['ema20']:
                print(f"    H4 close {last_h4['close']:.2f} >= EMA20 {last_h4['ema20']:.2f} (нужен downtrend)")
            else:
                print(f"    H4 в downtrend, но нет разворотных паттернов или M15 entry")

print()
print("="*80)
print("ИТОГ")
print("="*80)
print("Анализ завершен. Проверьте сигналы выше.")
