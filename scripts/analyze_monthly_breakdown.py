"""
Детальный анализ сделок Session Breakout LONG по месяцам (2020-2026)
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
import numpy as np
from datetime import datetime
from collections import defaultdict
from astra_v2.data.dukascopy import load_timeframe

# Лучшие параметры из Run 90
TP_RR = 3.5
STOP_BUFFER_ATR = 0.5
MIN_RANGE_ATR = 0.7
MAX_RANGE_ATR = 3.0
TRAILING_START = 2.0
TRAILING_DISTANCE = 0.5

ATR_PERIOD = 20
RISK_PER_TRADE = 100
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

def get_session_range(df, start_hour, end_hour):
    mask = (df.index.hour >= start_hour) & (df.index.hour < end_hour)
    session_bars = df[mask]

    if len(session_bars) == 0:
        return None, None

    range_high = session_bars['high'].max()
    range_low = session_bars['low'].min()

    return range_high, range_low

print("Загрузка данных XAUUSD M15...")
df = load_timeframe("m15", start=START_DATE, end=END_DATE, symbol="XAUUSD")
print(f"Загружено {len(df)} баров")

df['atr'] = calculate_atr(df, ATR_PERIOD)

trades = []
balance = 10000
peak_balance = 10000

dates = df.index.date
unique_dates = sorted(set(dates))

print(f"\nЗапуск бэктеста с параметрами:")
print(f"TP: {TP_RR}R, Stop Buffer: {STOP_BUFFER_ATR}, Range: {MIN_RANGE_ATR}-{MAX_RANGE_ATR}, Trailing: {TRAILING_START}R")
print(f"Период: {START_DATE} - {END_DATE}")
print(f"\nОбработка {len(unique_dates)} дней...\n")

for date in unique_dates:
    day_data = df[df.index.date == date]

    if len(day_data) < 10:
        continue

    asian_high, asian_low = get_session_range(day_data, 0, 7)
    london_high, london_low = get_session_range(day_data, 7, 12)

    if asian_high is None or london_high is None:
        continue

    highs = day_data['high'].to_numpy()
    lows = day_data['low'].to_numpy()
    closes = day_data['close'].to_numpy()
    atrs = day_data['atr'].to_numpy()
    hours = np.array([t.hour for t in day_data.index])
    times = day_data.index.to_numpy()

    ny_mask = hours >= 12
    ny_indices = np.where(ny_mask)[0]

    if len(ny_indices) == 0:
        continue

    first_ny_idx = ny_indices[0]
    atr_at_ny = atrs[first_ny_idx]

    if np.isnan(atr_at_ny):
        continue

    asian_range = asian_high - asian_low
    london_range = london_high - london_low

    if asian_range < MIN_RANGE_ATR * atr_at_ny or asian_range > MAX_RANGE_ATR * atr_at_ny:
        continue
    if london_range < MIN_RANGE_ATR * atr_at_ny or london_range > MAX_RANGE_ATR * atr_at_ny:
        continue

    combined_high = max(asian_high, london_high)
    combined_low = min(asian_low, london_low)

    entry_long = combined_high
    stop_long = combined_low - STOP_BUFFER_ATR * atr_at_ny

    if entry_long <= stop_long:
        continue

    risk_distance = entry_long - stop_long
    position_size = RISK_PER_TRADE / risk_distance

    tp_long = entry_long + TP_RR * risk_distance

    trade_entered = False
    entry_time = None
    entry_price = None
    current_stop = stop_long
    highest_price = entry_long

    for i in ny_indices:
        if not trade_entered:
            if highs[i] >= entry_long:
                trade_entered = True
                entry_time = times[i]
                entry_price = entry_long
                highest_price = entry_long
        else:
            highest_price = max(highest_price, highs[i])

            if TRAILING_START is not None:
                profit_in_r = (highest_price - entry_price) / risk_distance
                if profit_in_r >= TRAILING_START:
                    new_stop = highest_price - TRAILING_DISTANCE * risk_distance
                    current_stop = max(current_stop, new_stop)

            if lows[i] <= current_stop:
                exit_price = current_stop
                exit_time = times[i]
                pnl = (exit_price - entry_price) * position_size

                trades.append({
                    'entry_time': entry_time,
                    'exit_time': exit_time,
                    'entry_price': entry_price,
                    'exit_price': exit_price,
                    'stop_loss': stop_long,
                    'take_profit': tp_long,
                    'pnl': pnl,
                    'risk_distance': risk_distance,
                    'position_size': position_size,
                    'exit_reason': 'stop'
                })

                balance += pnl
                peak_balance = max(peak_balance, balance)
                break

            if highs[i] >= tp_long:
                exit_price = tp_long
                exit_time = times[i]
                pnl = (exit_price - entry_price) * position_size

                trades.append({
                    'entry_time': entry_time,
                    'exit_time': exit_time,
                    'entry_price': entry_price,
                    'exit_price': exit_price,
                    'stop_loss': stop_long,
                    'take_profit': tp_long,
                    'pnl': pnl,
                    'risk_distance': risk_distance,
                    'position_size': position_size,
                    'exit_reason': 'tp'
                })

                balance += pnl
                peak_balance = max(peak_balance, balance)
                break

print(f"Всего сделок: {len(trades)}")

# Группировка по месяцам
monthly_stats = defaultdict(lambda: {'count': 0, 'pnl': 0, 'wins': 0})

for trade in trades:
    month_key = trade['entry_time'].strftime('%Y-%m')
    monthly_stats[month_key]['count'] += 1
    monthly_stats[month_key]['pnl'] += trade['pnl']
    if trade['pnl'] > 0:
        monthly_stats[month_key]['wins'] += 1

# Вывод результатов
print("\n" + "="*80)
print("РАЗБИВКА СДЕЛОК SESSION BREAKOUT LONG ПО МЕСЯЦАМ (2020-2026)")
print("="*80)
print(f"{'Месяц':<12} {'Сделок':>8} {'PnL':>12} {'Win Rate':>10} {'Накопл.':>12}")
print("-"*80)

cumulative_pnl = 0
months_without_trades = []
all_months = pd.date_range(start=START_DATE, end=END_DATE, freq='MS')

for month in all_months:
    month_key = month.strftime('%Y-%m')
    stats = monthly_stats.get(month_key, {'count': 0, 'pnl': 0, 'wins': 0})

    if stats['count'] == 0:
        months_without_trades.append(month_key)
        print(f"{month_key:<12} {0:>8} {'$0.00':>12} {'-':>10} ${cumulative_pnl:>11,.2f}")
    else:
        cumulative_pnl += stats['pnl']
        win_rate = (stats['wins'] / stats['count'] * 100) if stats['count'] > 0 else 0
        print(f"{month_key:<12} {stats['count']:>8} ${stats['pnl']:>11,.2f} {win_rate:>9.1f}% ${cumulative_pnl:>11,.2f}")

print("-"*80)
total_wins = sum(s['wins'] for s in monthly_stats.values())
total_pnl = sum(s['pnl'] for s in monthly_stats.values())
win_rate = (total_wins / len(trades) * 100) if len(trades) > 0 else 0
print(f"{'ИТОГО':<12} {len(trades):>8} ${total_pnl:>11,.2f} {win_rate:>9.1f}% ${cumulative_pnl:>11,.2f}")
print("="*80)

print(f"\nМесяцев БЕЗ сделок: {len(months_without_trades)}")
if months_without_trades:
    print("Список месяцев без сделок:")
    for i in range(0, len(months_without_trades), 6):
        print("  " + ", ".join(months_without_trades[i:i+6]))
