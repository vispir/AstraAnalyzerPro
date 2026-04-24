"""
Анализ почему нет сигналов LONG/SHORT
"""
import json
import pandas as pd
from datetime import datetime, timezone

# Читаем свечи
with open(r'c:\Users\Администратор\Downloads\mt5_candles_rows (4).json', 'r') as f:
    data = json.load(f)

df = pd.DataFrame(data)
df['time'] = pd.to_datetime(df['time'])
df = df.sort_values('time')
df['open'] = df['open'].astype(float)
df['high'] = df['high'].astype(float)
df['low'] = df['low'].astype(float)
df['close'] = df['close'].astype(float)

print("="*80)
print("АНАЛИЗ ОТСУТСТВИЯ СИГНАЛОВ")
print("="*80)
print(f"Всего свечей: {len(df)}")
print(f"Период: {df['time'].min()} - {df['time'].max()}")
print()

# Последние 10 свечей
print("ПОСЛЕДНИЕ 10 СВЕЧЕЙ M15:")
print("-"*80)
last_10 = df.tail(10)[['time', 'open', 'high', 'low', 'close']]
for idx, row in last_10.iterrows():
    print(f"{row['time']} | O:{row['open']:.2f} H:{row['high']:.2f} L:{row['low']:.2f} C:{row['close']:.2f}")
print()

# Конвертируем в H4
df_h4 = df.set_index('time').resample('4H').agg({
    'open': 'first',
    'high': 'max',
    'low': 'min',
    'close': 'last'
}).dropna()

# EMA20 на H4
df_h4['ema20'] = df_h4['close'].ewm(span=20, adjust=False).mean()

print("ПОСЛЕДНИЕ 5 H4 СВЕЧЕЙ + EMA20:")
print("-"*80)
last_h4 = df_h4.tail(5)
for idx, row in last_h4.iterrows():
    above_ema = "ABOVE" if row['close'] > row['ema20'] else "BELOW"
    print(f"{idx} | C:{row['close']:.2f} | EMA20:{row['ema20']:.2f} | {above_ema}")
print()

# Текущая цена vs EMA20
current_price = df['close'].iloc[-1]
current_ema20 = df_h4['ema20'].iloc[-1]
print(f"ТЕКУЩАЯ ЦЕНА: ${current_price:.2f}")
print(f"H4 EMA20: ${current_ema20:.2f}")
print(f"Позиция: {'✓ ВЫШЕ EMA20 (LONG разрешён)' if current_price > current_ema20 else '✗ НИЖЕ EMA20 (SHORT разрешён)'}")
print()

# Проверка LONG условий (Session Breakout)
print("="*80)
print("ПРОВЕРКА LONG УСЛОВИЙ (Session Breakout)")
print("="*80)

# Определяем текущую сессию
now = datetime.now(timezone.utc)
current_hour = now.hour

if 0 <= current_hour < 7:
    session = "ASIAN_RANGE"
    session_start = 0
    session_end = 7
elif 7 <= current_hour < 10:
    session = "ASIAN_BREAKOUT"
    range_start = 0
    range_end = 7
elif 7 <= current_hour < 13:
    session = "LONDON_RANGE"
    session_start = 7
    session_end = 13
elif 13 <= current_hour < 16:
    session = "LONDON_BREAKOUT"
    range_start = 7
    range_end = 13
elif 13 <= current_hour < 18:
    session = "NY_RANGE"
    session_start = 13
    session_end = 18
elif 18 <= current_hour < 21:
    session = "NY_BREAKOUT"
    range_start = 13
    range_end = 18
else:
    session = "CLOSED"

print(f"Текущее время UTC: {now.strftime('%H:%M')}")
print(f"Текущая сессия: {session}")
print()

if "BREAKOUT" in session:
    # Находим range high
    range_bars = df[(df['time'].dt.hour >= range_start) & (df['time'].dt.hour < range_end)]
    if len(range_bars) > 0:
        range_high = range_bars['high'].max()
        range_low = range_bars['low'].min()
        print(f"Range High: ${range_high:.2f}")
        print(f"Range Low: ${range_low:.2f}")
        print(f"Текущая цена: ${current_price:.2f}")

        if current_price > range_high:
            print(f"✓ Цена ВЫШЕ range high (+{current_price - range_high:.2f})")
            if current_price > current_ema20:
                print("✓ Цена ВЫШЕ H4 EMA20")
                print("✅ LONG УСЛОВИЯ ВЫПОЛНЕНЫ!")
            else:
                print("✗ Цена НИЖЕ H4 EMA20 - LONG заблокирован")
        else:
            print(f"✗ Цена НЕ пробила range high (нужно +{range_high - current_price:.2f})")
    else:
        print("✗ Нет данных для range периода")
else:
    print(f"⏳ Сейчас {session} - ждём breakout окна")

print()

# Проверка SHORT условий
print("="*80)
print("ПРОВЕРКА SHORT УСЛОВИЙ (Reversal)")
print("="*80)

# Type 1: Historical High Reversal
print("TYPE 1: Historical High Reversal")
print("-"*80)
last_5_h4 = df_h4.tail(5)
h4_high_max = last_5_h4['high'].max()
h4_high_max_idx = last_5_h4['high'].idxmax()

print(f"Максимум за последние 5 H4: ${h4_high_max:.2f} ({h4_high_max_idx})")
print(f"Текущая H4 close: ${df_h4['close'].iloc[-1]:.2f}")

# Проверка разворота
last_h4_close = df_h4['close'].iloc[-1]
prev_h4_close = df_h4['close'].iloc[-2]

if last_h4_close < prev_h4_close:
    print("✓ Последняя H4 свеча закрылась НИЖЕ предыдущей (разворот вниз)")
else:
    print("✗ Нет разворота вниз на H4")

if current_price < current_ema20:
    print("✓ Цена НИЖЕ H4 EMA20")
else:
    print("✗ Цена ВЫШЕ H4 EMA20 - SHORT заблокирован")

# Проверка пробоя M15 low
last_3_m15 = df.tail(3)
m15_low = last_3_m15['low'].min()
print(f"M15 Low последних 3 свечей: ${m15_low:.2f}")

if current_price < m15_low:
    print(f"✓ Цена пробила M15 low (${current_price:.2f} < ${m15_low:.2f})")
else:
    print(f"✗ Цена НЕ пробила M15 low (нужно -{current_price - m15_low:.2f})")

print()
print("="*80)
print("ВЫВОД")
print("="*80)
print("Если нет сигналов:")
print("1. LONG: Ждём пробой session high в breakout окне")
print("2. SHORT: Ждём разворот после максимума + пробой M15 low")
print("3. Проверь что цена соответствует H4 EMA20 фильтру")
