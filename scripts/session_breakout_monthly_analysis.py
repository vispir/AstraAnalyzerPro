"""
Полный бэктест Session Breakout с детальной разбивкой по месяцам
Использует точную логику из optimize_session_breakout.py
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
active_trade = None
balance = 10000
peak_balance = 10000
max_dd = 0

dates = df.index.date
unique_dates = sorted(set(dates))

print(f"\nЗапуск бэктеста с параметрами:")
print(f"TP: {TP_RR}R, Stop Buffer: {STOP_BUFFER_ATR}, Range: {MIN_RANGE_ATR}-{MAX_RANGE_ATR}, Trailing: {TRAILING_START}R")
print(f"Период: {START_DATE} - {END_DATE}")
print(f"\nОбработка {len(unique_dates)} дней...\n")

for date_idx, date in enumerate(unique_dates):
    if date_idx % 100 == 0:
        print(f"Обработано {date_idx}/{len(unique_dates)} дней, сделок: {len(trades)}", flush=True)

    day_data = df[df.index.date == date]

    if len(day_data) < 10:
        continue

    asian_high, asian_low = get_session_range(day_data, 0, 7)
    london_high, london_low = get_session_range(day_data, 7, 13)

    if asian_high is None or london_high is None:
        continue

    highs = day_data['high'].to_numpy()
    lows = day_data['low'].to_numpy()
    closes = day_data['close'].to_numpy()
    atrs = day_data['atr'].to_numpy()
    hours = np.array([t.hour for t in day_data.index])
    times = day_data.index.to_numpy()

    # Process all bars in the day
    for i in range(len(day_data)):
        # Check exit conditions for active trade
        if active_trade is not None:
            # Breakeven and trailing logic
            if active_trade['direction'] == 'LONG':
                risk = active_trade['entry'] - active_trade['initial_sl']

                # Breakeven at 1R
                if highs[i] >= active_trade['entry'] + risk:
                    active_trade['sl'] = max(active_trade['sl'], active_trade['entry'])

                # Trailing SL if enabled
                if TRAILING_START is not None:
                    if highs[i] >= active_trade['entry'] + TRAILING_START * risk:
                        trailing_sl = highs[i] - TRAILING_DISTANCE * risk
                        active_trade['sl'] = max(active_trade['sl'], trailing_sl)

            else:  # SHORT
                risk = active_trade['initial_sl'] - active_trade['entry']

                # Breakeven at 1R
                if lows[i] <= active_trade['entry'] - risk:
                    active_trade['sl'] = min(active_trade['sl'], active_trade['entry'])

                # Trailing SL if enabled
                if TRAILING_START is not None:
                    if lows[i] <= active_trade['entry'] - TRAILING_START * risk:
                        trailing_sl = lows[i] + TRAILING_DISTANCE * risk
                        active_trade['sl'] = min(active_trade['sl'], trailing_sl)

            # Check SL/TP
            if active_trade['direction'] == 'LONG':
                if lows[i] <= active_trade['sl']:
                    pnl = (active_trade['sl'] - active_trade['entry']) * active_trade['size']
                    balance += pnl
                    trades.append({
                        'entry_time': active_trade['entry_time'],
                        'exit_time': times[i],
                        'direction': 'LONG',
                        'entry_price': active_trade['entry'],
                        'exit_price': active_trade['sl'],
                        'pnl': pnl,
                        'exit_reason': 'sl',
                        'range_type': active_trade['range_type']
                    })
                    active_trade = None
                elif highs[i] >= active_trade['tp']:
                    pnl = (active_trade['tp'] - active_trade['entry']) * active_trade['size']
                    balance += pnl
                    trades.append({
                        'entry_time': active_trade['entry_time'],
                        'exit_time': times[i],
                        'direction': 'LONG',
                        'entry_price': active_trade['entry'],
                        'exit_price': active_trade['tp'],
                        'pnl': pnl,
                        'exit_reason': 'tp',
                        'range_type': active_trade['range_type']
                    })
                    active_trade = None
            else:  # SHORT
                if highs[i] >= active_trade['sl']:
                    pnl = (active_trade['entry'] - active_trade['sl']) * active_trade['size']
                    balance += pnl
                    trades.append({
                        'entry_time': active_trade['entry_time'],
                        'exit_time': times[i],
                        'direction': 'SHORT',
                        'entry_price': active_trade['entry'],
                        'exit_price': active_trade['sl'],
                        'pnl': pnl,
                        'exit_reason': 'sl',
                        'range_type': active_trade['range_type']
                    })
                    active_trade = None
                elif lows[i] <= active_trade['tp']:
                    pnl = (active_trade['entry'] - active_trade['tp']) * active_trade['size']
                    balance += pnl
                    trades.append({
                        'entry_time': active_trade['entry_time'],
                        'exit_time': times[i],
                        'direction': 'SHORT',
                        'entry_price': active_trade['entry'],
                        'exit_price': active_trade['tp'],
                        'pnl': pnl,
                        'exit_reason': 'tp',
                        'range_type': active_trade['range_type']
                    })
                    active_trade = None

            # Update max DD
            if balance > peak_balance:
                peak_balance = balance
            dd = (peak_balance - balance) / peak_balance * 100
            if dd > max_dd:
                max_dd = dd

            if active_trade is not None:
                continue

        # Check for new trade entry
        atr = atrs[i]
        if np.isnan(atr):
            continue

        hour = hours[i]

        # Asian breakout window (07:00-10:00)
        if 7 <= hour < 10:
            asian_range = asian_high - asian_low

            if asian_range < MIN_RANGE_ATR * atr or asian_range > MAX_RANGE_ATR * atr:
                continue

            if closes[i] > asian_high and active_trade is None:
                entry = closes[i]
                sl = asian_low - STOP_BUFFER_ATR * atr
                risk = entry - sl
                tp = entry + risk * TP_RR
                size = RISK_PER_TRADE / risk

                active_trade = {
                    'entry': entry,
                    'sl': sl,
                    'initial_sl': sl,
                    'tp': tp,
                    'size': size,
                    'direction': 'LONG',
                    'entry_time': times[i],
                    'range_type': 'asian'
                }

            elif closes[i] < asian_low and active_trade is None:
                entry = closes[i]
                sl = asian_high + STOP_BUFFER_ATR * atr
                risk = sl - entry
                tp = entry - risk * TP_RR
                size = RISK_PER_TRADE / risk

                active_trade = {
                    'entry': entry,
                    'sl': sl,
                    'initial_sl': sl,
                    'tp': tp,
                    'size': size,
                    'direction': 'SHORT',
                    'entry_time': times[i],
                    'range_type': 'asian'
                }

        # London breakout window (13:00-16:00)
        elif 13 <= hour < 16:
            london_range = london_high - london_low

            if london_range < MIN_RANGE_ATR * atr or london_range > MAX_RANGE_ATR * atr:
                continue

            if closes[i] > london_high and active_trade is None:
                entry = closes[i]
                sl = london_low - STOP_BUFFER_ATR * atr
                risk = entry - sl
                tp = entry + risk * TP_RR
                size = RISK_PER_TRADE / risk

                active_trade = {
                    'entry': entry,
                    'sl': sl,
                    'initial_sl': sl,
                    'tp': tp,
                    'size': size,
                    'direction': 'LONG',
                    'entry_time': times[i],
                    'range_type': 'london'
                }

            elif closes[i] < london_low and active_trade is None:
                entry = closes[i]
                sl = london_high + STOP_BUFFER_ATR * atr
                risk = sl - entry
                tp = entry - risk * TP_RR
                size = RISK_PER_TRADE / risk

                active_trade = {
                    'entry': entry,
                    'sl': sl,
                    'initial_sl': sl,
                    'tp': tp,
                    'size': size,
                    'direction': 'SHORT',
                    'entry_time': times[i],
                    'range_type': 'london'
                }

print(f"\nВсего сделок: {len(trades)}")
print(f"Финальный баланс: ${balance:,.2f}")
print(f"Total PnL: ${balance - 10000:,.2f}")
print(f"Max DD: {max_dd:.2f}%")

# Группировка по месяцам
monthly_stats = defaultdict(lambda: {'count': 0, 'pnl': 0, 'wins': 0, 'long': 0, 'short': 0})

for trade in trades:
    month_key = trade['entry_time'].strftime('%Y-%m')
    monthly_stats[month_key]['count'] += 1
    monthly_stats[month_key]['pnl'] += trade['pnl']
    if trade['pnl'] > 0:
        monthly_stats[month_key]['wins'] += 1
    if trade['direction'] == 'LONG':
        monthly_stats[month_key]['long'] += 1
    else:
        monthly_stats[month_key]['short'] += 1

# Вывод результатов
print("\n" + "="*100)
print("РАЗБИВКА СДЕЛОК SESSION BREAKOUT ПО МЕСЯЦАМ (2020-2026)")
print("="*100)
print(f"{'Месяц':<12} {'Сделок':>8} {'LONG':>6} {'SHORT':>6} {'PnL':>12} {'Win Rate':>10} {'Накопл.':>12}")
print("-"*100)

cumulative_pnl = 0
months_without_trades = []
all_months = pd.date_range(start=START_DATE, end=END_DATE, freq='MS')

for month in all_months:
    month_key = month.strftime('%Y-%m')
    stats = monthly_stats.get(month_key, {'count': 0, 'pnl': 0, 'wins': 0, 'long': 0, 'short': 0})

    if stats['count'] == 0:
        months_without_trades.append(month_key)
        print(f"{month_key:<12} {0:>8} {0:>6} {0:>6} {'$0.00':>12} {'-':>10} ${cumulative_pnl:>11,.2f}")
    else:
        cumulative_pnl += stats['pnl']
        win_rate = (stats['wins'] / stats['count'] * 100) if stats['count'] > 0 else 0
        print(f"{month_key:<12} {stats['count']:>8} {stats['long']:>6} {stats['short']:>6} ${stats['pnl']:>11,.2f} {win_rate:>9.1f}% ${cumulative_pnl:>11,.2f}")

print("-"*100)
total_wins = sum(s['wins'] for s in monthly_stats.values())
total_pnl = sum(s['pnl'] for s in monthly_stats.values())
total_long = sum(s['long'] for s in monthly_stats.values())
total_short = sum(s['short'] for s in monthly_stats.values())
win_rate = (total_wins / len(trades) * 100) if len(trades) > 0 else 0
print(f"{'ИТОГО':<12} {len(trades):>8} {total_long:>6} {total_short:>6} ${total_pnl:>11,.2f} {win_rate:>9.1f}% ${cumulative_pnl:>11,.2f}")
print("="*100)

print(f"\nМесяцев БЕЗ сделок: {len(months_without_trades)} из {len(all_months)}")
print(f"Процент месяцев без сделок: {len(months_without_trades)/len(all_months)*100:.1f}%")
