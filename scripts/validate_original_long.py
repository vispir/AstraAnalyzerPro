"""
Валидация оригинальной LONG стратегии
Ожидаемый результат: 360 сделок, DD 6.32%, WR 50.8%, PnL $40,134
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
print("ВАЛИДАЦИЯ ОРИГИНАЛЬНОЙ LONG СТРАТЕГИИ")
print("="*80)
print(f"Period: {df.index[0]} - {df.index[-1]}")
print(f"Initial Balance: $10,000")
print()

# ОРИГИНАЛЬНЫЕ ПАРАМЕТРЫ
RISK_PER_TRADE = 158
TP_RR = 5.5
USE_H4_EMA_FILTER = True
USE_STEP_TRAILING = True

# ATR calculation (ОРИГИНАЛ - не True Range!)
df['atr'] = df['high'].rolling(14).max() - df['low'].rolling(14).min()

# H4 data
df_h4 = df.resample('4h').agg({'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last'}).dropna()
df_h4['ema20'] = df_h4['close'].ewm(span=20, adjust=False).mean()

# Session parameters (ОТДЕЛЬНЫЕ окна Range и Breakout)
ASIAN_PARAMS = {
    'stop_buffer_atr': 0.1,
    'min_range_atr': 0.7,
    'max_range_atr': 3.0,
    'range_hours': (0, 7),
    'breakout_hours': (7, 10)
}

LONDON_PARAMS = {
    'stop_buffer_atr': 0.3,
    'min_range_atr': 0.3,
    'max_range_atr': 3.0,
    'range_hours': (7, 12),
    'breakout_hours': (13, 16)
}

NY_PARAMS = {
    'stop_buffer_atr': 0.3,
    'min_range_atr': 0.5,
    'max_range_atr': 3.0,
    'range_hours': (13, 17),
    'breakout_hours': (18, 21)
}

# Backtest
trades = []
balance = 10000
peak_balance = 10000
max_dd = 0
max_daily_dd = 0
active_trades = {}

dates = df.index.date
unique_dates = sorted(set(dates))

for date in unique_dates:
    day_data = df[df.index.date == date]

    if len(day_data) < 10:
        continue

    day_start_balance = balance

    # Reset ranges
    asian_high = None
    asian_low = None
    london_high = None
    london_low = None
    ny_high = None
    ny_low = None

    highs = day_data['high'].to_numpy()
    lows = day_data['low'].to_numpy()
    closes = day_data['close'].to_numpy()
    atrs = day_data['atr'].to_numpy()
    hours = np.array([t.hour for t in day_data.index])
    times = day_data.index.to_numpy()

    for i in range(len(day_data)):
        hour = hours[i]
        atr = atrs[i]

        if np.isnan(atr) or atr == 0:
            continue

        # Track ranges
        if ASIAN_PARAMS['range_hours'][0] <= hour < ASIAN_PARAMS['range_hours'][1]:
            if asian_high is None:
                asian_high = highs[i]
                asian_low = lows[i]
            else:
                asian_high = max(asian_high, highs[i])
                asian_low = min(asian_low, lows[i])

        if LONDON_PARAMS['range_hours'][0] <= hour < LONDON_PARAMS['range_hours'][1]:
            if london_high is None:
                london_high = highs[i]
                london_low = lows[i]
            else:
                london_high = max(london_high, highs[i])
                london_low = min(london_low, lows[i])

        if NY_PARAMS['range_hours'][0] <= hour < NY_PARAMS['range_hours'][1]:
            if ny_high is None:
                ny_high = highs[i]
                ny_low = lows[i]
            else:
                ny_high = max(ny_high, highs[i])
                ny_low = min(ny_low, lows[i])

        # Update active trades with step trailing
        for session_name, trade in list(active_trades.items()):
            risk = trade['entry'] - trade['initial_sl']
            profit_r = (closes[i] - trade['entry']) / risk

            if profit_r >= 5.0:
                trade['sl'] = max(trade['sl'], trade['entry'] + 4.0 * risk)
            elif profit_r >= 4.0:
                trade['sl'] = max(trade['sl'], trade['entry'] + 3.0 * risk)
            elif profit_r >= 3.0:
                trade['sl'] = max(trade['sl'], trade['entry'] + 2.0 * risk)
            elif profit_r >= 2.0:
                trade['sl'] = max(trade['sl'], trade['entry'] + 1.0 * risk)

            # Check exit
            if lows[i] <= trade['sl']:
                pnl = (trade['sl'] - trade['entry']) / risk * RISK_PER_TRADE
                balance += pnl
                trades.append({
                    'date': trade['entry_time'],
                    'exit_date': times[i],
                    'pnl': pnl,
                    'session': session_name
                })
                del active_trades[session_name]
            elif highs[i] >= trade['tp']:
                pnl = (trade['tp'] - trade['entry']) / risk * RISK_PER_TRADE
                balance += pnl
                trades.append({
                    'date': trade['entry_time'],
                    'exit_date': times[i],
                    'pnl': pnl,
                    'session': session_name
                })
                del active_trades[session_name]

        # Update DD
        if balance > peak_balance:
            peak_balance = balance
        dd = (peak_balance - balance) / peak_balance * 100
        if dd > max_dd:
            max_dd = dd

        # ASIAN ENTRY
        if ASIAN_PARAMS['breakout_hours'][0] <= hour < ASIAN_PARAMS['breakout_hours'][1]:
            if asian_high is not None and 'asian' not in active_trades:
                asian_range = asian_high - asian_low

                # Range filter
                if ASIAN_PARAMS['min_range_atr'] * atr <= asian_range <= ASIAN_PARAMS['max_range_atr'] * atr:
                    if closes[i] > asian_high:
                        # H4 EMA20 filter
                        if USE_H4_EMA_FILTER:
                            current_time = times[i]
                            h4_bar = df_h4[df_h4.index <= current_time].iloc[-1] if len(df_h4[df_h4.index <= current_time]) > 0 else None

                            if h4_bar is None or pd.isna(h4_bar['ema20']):
                                continue
                            if h4_bar['close'] <= h4_bar['ema20']:
                                continue

                        entry = closes[i]
                        sl = asian_low - ASIAN_PARAMS['stop_buffer_atr'] * atr
                        risk = entry - sl

                        if risk > 0:
                            tp = entry + risk * TP_RR

                            active_trades['asian'] = {
                                'entry': entry,
                                'sl': sl,
                                'initial_sl': sl,
                                'tp': tp,
                                'entry_time': times[i]
                            }

        # LONDON ENTRY
        if LONDON_PARAMS['breakout_hours'][0] <= hour < LONDON_PARAMS['breakout_hours'][1]:
            if london_high is not None and 'london' not in active_trades:
                london_range = london_high - london_low

                # Range filter
                if LONDON_PARAMS['min_range_atr'] * atr <= london_range <= LONDON_PARAMS['max_range_atr'] * atr:
                    if closes[i] > london_high:
                        # H4 EMA20 filter
                        if USE_H4_EMA_FILTER:
                            current_time = times[i]
                            h4_bar = df_h4[df_h4.index <= current_time].iloc[-1] if len(df_h4[df_h4.index <= current_time]) > 0 else None

                            if h4_bar is None or pd.isna(h4_bar['ema20']):
                                continue
                            if h4_bar['close'] <= h4_bar['ema20']:
                                continue

                        entry = closes[i]
                        sl = london_low - LONDON_PARAMS['stop_buffer_atr'] * atr
                        risk = entry - sl

                        if risk > 0:
                            tp = entry + risk * TP_RR

                            active_trades['london'] = {
                                'entry': entry,
                                'sl': sl,
                                'initial_sl': sl,
                                'tp': tp,
                                'entry_time': times[i]
                            }

        # NY ENTRY
        if NY_PARAMS['breakout_hours'][0] <= hour < NY_PARAMS['breakout_hours'][1]:
            if ny_high is not None and 'ny' not in active_trades:
                ny_range = ny_high - ny_low

                # Range filter
                if NY_PARAMS['min_range_atr'] * atr <= ny_range <= NY_PARAMS['max_range_atr'] * atr:
                    if closes[i] > ny_high:
                        # H4 EMA20 filter
                        if USE_H4_EMA_FILTER:
                            current_time = times[i]
                            h4_bar = df_h4[df_h4.index <= current_time].iloc[-1] if len(df_h4[df_h4.index <= current_time]) > 0 else None

                            if h4_bar is None or pd.isna(h4_bar['ema20']):
                                continue
                            if h4_bar['close'] <= h4_bar['ema20']:
                                continue

                        entry = closes[i]
                        sl = ny_low - NY_PARAMS['stop_buffer_atr'] * atr
                        risk = entry - sl

                        if risk > 0:
                            tp = entry + risk * TP_RR

                            active_trades['ny'] = {
                                'entry': entry,
                                'sl': sl,
                                'initial_sl': sl,
                                'tp': tp,
                                'entry_time': times[i]
                            }

    # Daily DD
    daily_dd = (day_start_balance - balance) / day_start_balance * 100 if day_start_balance > 0 else 0
    if daily_dd > max_daily_dd:
        max_daily_dd = daily_dd

# Results
trades_df = pd.DataFrame(trades)
winning_trades = trades_df[trades_df['pnl'] > 0]
losing_trades = trades_df[trades_df['pnl'] < 0]
gross_profit = winning_trades['pnl'].sum()
gross_loss = abs(losing_trades['pnl'].sum())
profit_factor = gross_profit / gross_loss if gross_loss > 0 else 0

print("="*80)
print("RESULTS: ОРИГИНАЛЬНАЯ LONG СТРАТЕГИЯ")
print("="*80)
print()
print(f"Total Trades: {len(trades_df)}")
print(f"Total PnL: ${trades_df['pnl'].sum():,.2f}")
print(f"Win Rate: {len(winning_trades) / len(trades_df) * 100:.1f}%")
print(f"Max DD: {max_dd:.2f}%")
print(f"Max Daily DD: {max_daily_dd:.2f}%")
print(f"Profit Factor: {profit_factor:.2f}")
print(f"Return: {(balance - 10000) / 10000 * 100:.1f}%")
print()

print("="*80)
print("YEARLY BREAKDOWN")
print("="*80)
trades_df['year'] = pd.to_datetime(trades_df['date']).dt.year
for year in sorted(trades_df['year'].unique()):
    year_trades = trades_df[trades_df['year'] == year]
    year_wins = year_trades[year_trades['pnl'] > 0]
    print(f"{year}: {len(year_trades)} trades, ${year_trades['pnl'].sum():,.0f} PnL, WR {len(year_wins)/len(year_trades)*100:.1f}%")
print()

print("="*80)
print("BY SESSION")
print("="*80)
for session in ['asian', 'london', 'ny']:
    session_trades = trades_df[trades_df['session'] == session]
    if len(session_trades) > 0:
        session_wins = session_trades[session_trades['pnl'] > 0]
        print(f"{session.upper()}: {len(session_trades)} trades, ${session_trades['pnl'].sum():,.0f} PnL, WR {len(session_wins)/len(session_trades)*100:.1f}%")
print()

print("="*80)
print("СРАВНЕНИЕ С ЭТАЛОНОМ")
print("="*80)
print(f"Trades: {len(trades_df)} vs 360 эталон ({len(trades_df) - 360:+d} разница)")
print(f"PnL: ${trades_df['pnl'].sum():,.0f} vs $40,134 эталон")
print(f"DD: {max_dd:.2f}% vs 6.32% эталон")
print(f"WR: {len(winning_trades) / len(trades_df) * 100:.1f}% vs 50.8% эталон")
print()

# Validation
if abs(len(trades_df) - 360) <= 10 and abs(max_dd - 6.32) < 2.0:
    print("="*80)
    print(">>> ВАЛИДАЦИЯ УСПЕШНА: ПАРАМЕТРЫ СОВПАДАЮТ <<<")
    print("="*80)
    print()
    print("Можно обновлять деплой версию с этими параметрами")
else:
    print("="*80)
    print("[!] ВНИМАНИЕ: Расхождение с эталоном")
    print("="*80)
    print("Требуется дополнительная проверка параметров")
print()
print("="*80)
