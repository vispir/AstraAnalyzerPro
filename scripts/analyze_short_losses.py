"""
Детальный анализ убыточных SHORT сделок
Цель: найти паттерны и условия где SHORT может работать
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
import numpy as np
from astra_v2.data.dukascopy import load_timeframe
from datetime import datetime

# Session parameters
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

ATR_PERIOD = 20
H4_EMA_PERIOD = 20
START_DATE = "2020-01-01"
END_DATE = "2026-04-18"
RISK_PER_TRADE = 158
TP_RR = 5.5

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

def get_session_range(df, start_hour, end_hour):
    mask = (df.index.hour >= start_hour) & (df.index.hour < end_hour)
    session_bars = df[mask]

    if len(session_bars) == 0:
        return None, None

    range_high = session_bars['high'].max()
    range_low = session_bars['low'].min()

    return range_high, range_low

def run_short_backtest_detailed():
    """Запуск SHORT бэктеста с детальным логированием"""

    print("Загрузка данных...")
    df = load_timeframe("M15", start=START_DATE, end=END_DATE, symbol="XAUUSD")

    if 'datetime' in df.columns:
        df.set_index('datetime', inplace=True)
    df = df.sort_index()

    # Resample to H4 for EMA filter
    print("Ресемплинг в H4...")
    df_h4 = df.resample('4h').agg({'open':'first','high':'max','low':'min','close':'last'}).dropna()
    df_h4['ema20'] = calculate_ema(df_h4, H4_EMA_PERIOD)

    # Calculate ATR
    df['atr'] = calculate_atr(df, ATR_PERIOD)

    # Trading state
    balance = 10000
    trades = []
    active_trade = None

    print("Запуск бэктеста SHORT...")

    # Group by date
    for date, day_data in df.groupby(df.index.date):
        if len(day_data) == 0:
            continue

        # Check active trade first
        if active_trade:
            for idx, row in day_data.iterrows():
                if active_trade is None:
                    continue

                current_price = row['close']

                # Update trailing stop (simplified - no trailing for analysis)
                # Check TP
                if current_price <= active_trade['tp']:
                    pnl = RISK_PER_TRADE * TP_RR
                    balance += pnl
                    active_trade['exit_price'] = active_trade['tp']
                    active_trade['pnl'] = pnl
                    active_trade['exit_time'] = idx
                    active_trade['exit_reason'] = 'TP'
                    trades.append(active_trade)
                    active_trade = None
                    continue

                # Check SL
                if current_price >= active_trade['sl']:
                    pnl = -RISK_PER_TRADE
                    balance += pnl
                    active_trade['exit_price'] = active_trade['sl']
                    active_trade['pnl'] = pnl
                    active_trade['exit_time'] = idx
                    active_trade['exit_reason'] = 'SL'
                    trades.append(active_trade)
                    active_trade = None
                    continue

            continue

        # Check for new signals
        for session_name, params in [('asian', ASIAN_PARAMS), ('london', LONDON_PARAMS), ('ny', NY_PARAMS)]:
            range_start, range_end = params['range_hours']
            breakout_start, breakout_end = params['breakout_hours']

            # Get range
            range_data = day_data[(day_data.index.hour >= range_start) & (day_data.index.hour < range_end)]
            if len(range_data) == 0:
                continue

            range_high, range_low = get_session_range(day_data, range_start, range_end)
            if range_high is None:
                continue

            # Check breakout window
            breakout_data = day_data[(day_data.index.hour >= breakout_start) & (day_data.index.hour < breakout_end)]
            if len(breakout_data) == 0:
                continue

            for idx, row in breakout_data.iterrows():
                # Get H4 bar
                h4_time = idx.floor('4h')
                if h4_time not in df_h4.index:
                    continue

                h4_bar = df_h4.loc[h4_time]
                if pd.isna(h4_bar['ema20']):
                    continue

                # Check range size
                atr_val = row['atr']
                if pd.isna(atr_val):
                    continue

                range_size = range_high - range_low
                if range_size < params['min_range_atr'] * atr_val:
                    continue
                if range_size > params['max_range_atr'] * atr_val:
                    continue

                # SHORT breakout: price breaks BELOW range low
                if row['close'] >= range_low:
                    continue

                # Entry signal
                entry = row['close']
                sl = range_high + params['stop_buffer_atr'] * atr_val
                risk = sl - entry
                tp = entry - TP_RR * risk

                # Calculate H4 trend
                h4_close = h4_bar['close']
                h4_ema20 = h4_bar['ema20']
                h4_trend = 'downtrend' if h4_close < h4_ema20 else 'uptrend'
                h4_distance = ((h4_close - h4_ema20) / h4_ema20) * 100

                active_trade = {
                    'direction': 'SHORT',
                    'entry': entry,
                    'sl': sl,
                    'tp': tp,
                    'session': session_name,
                    'entry_time': idx,
                    'entry_hour': idx.hour,
                    'entry_date': idx.date(),
                    'risk_usd': RISK_PER_TRADE,
                    'atr': atr_val,
                    'range_size': range_size,
                    'range_size_atr': range_size / atr_val,
                    'h4_trend': h4_trend,
                    'h4_close': h4_close,
                    'h4_ema20': h4_ema20,
                    'h4_distance_pct': h4_distance,
                    'entry_price': entry
                }
                break

            if active_trade:
                break

    print(f"Всего сделок: {len(trades)}")

    # Convert to DataFrame
    df_trades = pd.DataFrame(trades)

    if len(df_trades) == 0:
        print("Нет сделок!")
        return

    # Analyze
    print("\n" + "="*80)
    print("ОБЩАЯ СТАТИСТИКА")
    print("="*80)

    total_pnl = df_trades['pnl'].sum()
    wins = df_trades[df_trades['pnl'] > 0]
    losses = df_trades[df_trades['pnl'] <= 0]

    print(f"\nВсего сделок: {len(df_trades)}")
    print(f"Прибыльных: {len(wins)} ({len(wins)/len(df_trades)*100:.1f}%)")
    print(f"Убыточных: {len(losses)} ({len(losses)/len(df_trades)*100:.1f}%)")
    print(f"Total PnL: ${total_pnl:,.0f}")

    # Analyze losing trades
    print("\n" + "="*80)
    print("АНАЛИЗ УБЫТОЧНЫХ СДЕЛОК")
    print("="*80)

    if len(losses) == 0:
        print("Нет убыточных сделок!")
        return

    print(f"\nВсего убыточных: {len(losses)}")
    print(f"Общий убыток: ${losses['pnl'].sum():,.0f}")

    # By session
    print("\n--- По сессиям ---")
    for session in ['asian', 'london', 'ny']:
        session_losses = losses[losses['session'] == session]
        if len(session_losses) > 0:
            print(f"{session.upper()}: {len(session_losses)} сделок ({len(session_losses)/len(losses)*100:.1f}%), PnL: ${session_losses['pnl'].sum():,.0f}")

    # By hour
    print("\n--- По часам входа (UTC) ---")
    hour_losses = losses.groupby('entry_hour').size().sort_values(ascending=False)
    for hour, count in hour_losses.head(10).items():
        print(f"{hour:02d}:00 UTC: {count} сделок ({count/len(losses)*100:.1f}%)")

    # By H4 trend
    print("\n--- По H4 тренду ---")
    for trend in ['uptrend', 'downtrend']:
        trend_losses = losses[losses['h4_trend'] == trend]
        if len(trend_losses) > 0:
            print(f"{trend.upper()}: {len(trend_losses)} сделок ({len(trend_losses)/len(losses)*100:.1f}%), PnL: ${trend_losses['pnl'].sum():,.0f}")

    # H4 distance from EMA
    print("\n--- Расстояние H4 от EMA20 (убыточные) ---")
    print(f"Среднее: {losses['h4_distance_pct'].mean():.2f}%")
    print(f"Медиана: {losses['h4_distance_pct'].median():.2f}%")
    print(f"Min: {losses['h4_distance_pct'].min():.2f}%")
    print(f"Max: {losses['h4_distance_pct'].max():.2f}%")

    # ATR analysis
    print("\n--- ATR в момент входа (убыточные) ---")
    print(f"Среднее: {losses['atr'].mean():.2f}")
    print(f"Медиана: {losses['atr'].median():.2f}")

    # Range size
    print("\n--- Размер range в ATR (убыточные) ---")
    print(f"Среднее: {losses['range_size_atr'].mean():.2f} ATR")
    print(f"Медиана: {losses['range_size_atr'].median():.2f} ATR")

    # Compare with winning trades
    print("\n" + "="*80)
    print("СРАВНЕНИЕ: УБЫТОЧНЫЕ vs ПРИБЫЛЬНЫЕ")
    print("="*80)

    if len(wins) > 0:
        print("\n--- H4 Trend ---")
        print(f"Убыточные - Uptrend: {len(losses[losses['h4_trend']=='uptrend'])/len(losses)*100:.1f}%")
        print(f"Прибыльные - Uptrend: {len(wins[wins['h4_trend']=='uptrend'])/len(wins)*100:.1f}%")

        print("\n--- H4 Distance from EMA20 ---")
        print(f"Убыточные: {losses['h4_distance_pct'].mean():.2f}% (среднее)")
        print(f"Прибыльные: {wins['h4_distance_pct'].mean():.2f}% (среднее)")

        print("\n--- ATR ---")
        print(f"Убыточные: {losses['atr'].mean():.2f} (среднее)")
        print(f"Прибыльные: {wins['atr'].mean():.2f} (среднее)")

        print("\n--- Range Size (ATR) ---")
        print(f"Убыточные: {losses['range_size_atr'].mean():.2f} ATR (среднее)")
        print(f"Прибыльные: {wins['range_size_atr'].mean():.2f} ATR (среднее)")

        print("\n--- Сессии ---")
        for session in ['asian', 'london', 'ny']:
            loss_pct = len(losses[losses['session']==session])/len(losses)*100 if len(losses) > 0 else 0
            win_pct = len(wins[wins['session']==session])/len(wins)*100 if len(wins) > 0 else 0
            print(f"{session.upper()}: Убыточные {loss_pct:.1f}%, Прибыльные {win_pct:.1f}%")

    # Find profitable windows
    print("\n" + "="*80)
    print("ПОИСК ПРИБЫЛЬНЫХ ОКОН")
    print("="*80)

    if len(wins) > 0:
        print("\n--- Прибыльные часы (UTC) ---")
        win_hours = wins.groupby('entry_hour').agg({
            'pnl': ['count', 'sum', 'mean']
        }).round(2)
        win_hours.columns = ['Count', 'Total PnL', 'Avg PnL']
        win_hours = win_hours.sort_values('Total PnL', ascending=False)
        print(win_hours.head(10))

        print("\n--- Прибыльные условия ---")
        # Downtrend only
        downtrend_wins = wins[wins['h4_trend'] == 'downtrend']
        if len(downtrend_wins) > 0:
            print(f"\nDOWNTREND only: {len(downtrend_wins)} сделок, PnL: ${downtrend_wins['pnl'].sum():,.0f}")
            print(f"  Win Rate: {len(downtrend_wins)/(len(downtrend_wins)+len(losses[losses['h4_trend']=='downtrend']))*100:.1f}%")

        # Strong downtrend (H4 < EMA20 - 1%)
        strong_down = wins[wins['h4_distance_pct'] < -1.0]
        if len(strong_down) > 0:
            print(f"\nSTRONG DOWNTREND (H4 < EMA20 - 1%): {len(strong_down)} сделок, PnL: ${strong_down['pnl'].sum():,.0f}")

    # Recommendations
    print("\n" + "="*80)
    print("РЕКОМЕНДАЦИИ ДЛЯ SHORT СТРАТЕГИИ")
    print("="*80)

    if len(wins) > 0:
        # Calculate best conditions
        downtrend_wr = len(wins[wins['h4_trend']=='downtrend']) / len(df_trades[df_trades['h4_trend']=='downtrend']) * 100 if len(df_trades[df_trades['h4_trend']=='downtrend']) > 0 else 0
        uptrend_wr = len(wins[wins['h4_trend']=='uptrend']) / len(df_trades[df_trades['h4_trend']=='uptrend']) * 100 if len(df_trades[df_trades['h4_trend']=='uptrend']) > 0 else 0

        print(f"\n1. H4 TREND FILTER:")
        print(f"   - Downtrend WR: {downtrend_wr:.1f}%")
        print(f"   - Uptrend WR: {uptrend_wr:.1f}%")
        if downtrend_wr > uptrend_wr:
            print(f"   [+] Рекомендация: SHORT только в downtrend (H4 < EMA20)")

        # Best sessions
        session_stats = []
        for session in ['asian', 'london', 'ny']:
            session_trades = df_trades[df_trades['session'] == session]
            if len(session_trades) > 0:
                session_wins = wins[wins['session'] == session]
                wr = len(session_wins) / len(session_trades) * 100
                pnl = session_trades['pnl'].sum()
                session_stats.append((session, wr, pnl, len(session_trades)))

        session_stats.sort(key=lambda x: x[2], reverse=True)

        print(f"\n2. ЛУЧШИЕ СЕССИИ:")
        for session, wr, pnl, count in session_stats:
            status = "[+]" if pnl > 0 else "[-]"
            print(f"   {status} {session.upper()}: WR {wr:.1f}%, PnL ${pnl:,.0f}, Trades {count}")

        # Best hours
        hour_stats = df_trades.groupby('entry_hour').agg({
            'pnl': ['sum', 'count']
        })
        hour_stats.columns = ['PnL', 'Count']
        hour_stats = hour_stats[hour_stats['Count'] >= 5]  # Min 5 trades
        best_hours = hour_stats[hour_stats['PnL'] > 0].sort_values('PnL', ascending=False)

        if len(best_hours) > 0:
            print(f"\n3. ПРИБЫЛЬНЫЕ ЧАСЫ (UTC):")
            for hour, row in best_hours.head(5).iterrows():
                print(f"   [+] {hour:02d}:00 UTC: PnL ${row['PnL']:,.0f}, Trades {int(row['Count'])}")

    print("\n" + "="*80)
    print("ВЫВОД")
    print("="*80)

    if total_pnl < 0:
        print(f"\n[-] SHORT стратегия убыточна: ${total_pnl:,.0f}")
        print(f"\nОсновные причины:")
        print(f"  - {len(losses[losses['h4_trend']=='uptrend'])/len(losses)*100:.1f}% убытков в uptrend")
        print(f"  - Win Rate всего {len(wins)/len(df_trades)*100:.1f}%")
        print(f"\nРекомендация: НЕ использовать SHORT на XAUUSD 2020-2026")
    else:
        print(f"\n[+] SHORT стратегия прибыльна: ${total_pnl:,.0f}")

if __name__ == "__main__":
    run_short_backtest_detailed()
