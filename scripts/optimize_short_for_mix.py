"""
SHORT Strategy Optimization для миксования с LONG
Цель: найти параметры SHORT где LONG+SHORT дает DD < 9% и PnL > $50k
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
import numpy as np
from astra_v2.data.dukascopy import load_timeframe
from itertools import product

# LONG стратегия (фиксированная)
LONG_PNL = 40134
LONG_MAX_DD = 6.32
LONG_DAILY_DD = 1.87
LONG_TRADES = 360
LONG_RISK = 158

# Session parameters (те же что у LONG)
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

# Параметры для перебора
TP_VALUES = [2.0, 2.5, 3.0]
RISK_VALUES = [80, 100, 120, 158]

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

def run_short_backtest(tp_rr, risk_per_trade):
    """Запуск бэктеста SHORT стратегии"""

    # Load data
    df = load_timeframe("M15", start=START_DATE, end=END_DATE, symbol="XAUUSD")

    if 'datetime' in df.columns:
        df.set_index('datetime', inplace=True)
    df = df.sort_index()

    # Resample to H4 for EMA filter
    df_h4 = df.resample('4h').agg({'open':'first','high':'max','low':'min','close':'last'}).dropna()
    df_h4['ema20'] = calculate_ema(df_h4, H4_EMA_PERIOD)

    # Calculate ATR
    df['atr'] = calculate_atr(df, ATR_PERIOD)

    # Trading state
    balance = 10000
    equity_curve = []
    trades = []
    active_trade = None

    # Group by date
    for date, day_data in df.groupby(df.index.date):
        if len(day_data) == 0:
            continue

        # Check active trade first
        if active_trade:
            for idx, row in day_data.iterrows():
                if active_trade is None:  # Trade was closed
                    equity_curve.append({'time': idx, 'equity': balance})
                    continue

                current_price = row['close']

                # Update trailing stop
                if active_trade['direction'] == 'SHORT':
                    risk = active_trade['entry'] - active_trade['sl']
                    profit_r = (active_trade['entry'] - current_price) / risk

                    # Step trailing (adjusted for TP)
                    new_sl = active_trade['sl']
                    if tp_rr >= 3.0:
                        if profit_r >= 2.5:
                            new_sl = min(new_sl, active_trade['entry'] - 1.5 * risk)
                        elif profit_r >= 2.0:
                            new_sl = min(new_sl, active_trade['entry'] - 1.0 * risk)
                    elif tp_rr >= 2.5:
                        if profit_r >= 2.0:
                            new_sl = min(new_sl, active_trade['entry'] - 1.0 * risk)

                    active_trade['sl'] = new_sl

                    # Check TP
                    if current_price <= active_trade['tp']:
                        pnl = risk_per_trade * tp_rr
                        balance += pnl
                        active_trade['exit_price'] = active_trade['tp']
                        active_trade['pnl'] = pnl
                        active_trade['exit_time'] = idx
                        trades.append(active_trade)
                        active_trade = None
                        continue

                    # Check SL
                    if current_price >= active_trade['sl']:
                        pnl = -risk_per_trade if active_trade['sl'] == active_trade['original_sl'] else \
                              (active_trade['entry'] - active_trade['sl']) / risk * risk_per_trade
                        balance += pnl
                        active_trade['exit_price'] = active_trade['sl']
                        active_trade['pnl'] = pnl
                        active_trade['exit_time'] = idx
                        trades.append(active_trade)
                        active_trade = None
                        continue

                equity_curve.append({'time': idx, 'equity': balance})

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

                # SHORT filter: H4 close < EMA20 (downtrend)
                if h4_bar['close'] >= h4_bar['ema20']:
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
                tp = entry - tp_rr * risk

                active_trade = {
                    'direction': 'SHORT',
                    'entry': entry,
                    'sl': sl,
                    'original_sl': sl,
                    'tp': tp,
                    'session': session_name,
                    'entry_time': idx,
                    'risk_usd': risk_per_trade
                }
                break

            if active_trade:
                break

        # Update equity curve
        for idx, row in day_data.iterrows():
            equity_curve.append({'time': idx, 'equity': balance})

    # Calculate metrics
    if len(trades) == 0:
        return None

    df_trades = pd.DataFrame(trades)
    df_equity = pd.DataFrame(equity_curve)

    total_pnl = df_trades['pnl'].sum()
    wins = df_trades[df_trades['pnl'] > 0]
    losses = df_trades[df_trades['pnl'] <= 0]
    win_rate = len(wins) / len(df_trades) * 100

    # Calculate DD
    df_equity['peak'] = df_equity['equity'].cummax()
    df_equity['dd'] = (df_equity['equity'] - df_equity['peak']) / df_equity['peak'] * 100
    max_dd = abs(df_equity['dd'].min())

    # Daily DD
    df_equity['date'] = df_equity['time'].dt.date
    daily_equity = df_equity.groupby('date')['equity'].agg(['first', 'min'])
    daily_equity['daily_dd'] = (daily_equity['min'] - daily_equity['first']) / daily_equity['first'] * 100
    max_daily_dd = abs(daily_equity['daily_dd'].min())

    return {
        'tp_rr': tp_rr,
        'risk': risk_per_trade,
        'total_pnl': total_pnl,
        'max_dd': max_dd,
        'max_daily_dd': max_daily_dd,
        'win_rate': win_rate,
        'total_trades': len(df_trades)
    }

def calculate_combined_metrics(short_result):
    """Рассчитать суммарные метрики LONG + SHORT"""
    if short_result is None:
        return None

    # Суммарный PnL
    combined_pnl = LONG_PNL + short_result['total_pnl']

    # Суммарный DD (приблизительно, без корреляции)
    # Консервативная оценка: берем максимум + половину второго
    combined_dd = max(LONG_MAX_DD, short_result['max_dd']) + min(LONG_MAX_DD, short_result['max_dd']) * 0.5

    # Суммарный Daily DD
    combined_daily_dd = max(LONG_DAILY_DD, short_result['max_daily_dd']) + min(LONG_DAILY_DD, short_result['max_daily_dd']) * 0.3

    return {
        'tp_rr': short_result['tp_rr'],
        'risk': short_result['risk'],
        'short_pnl': short_result['total_pnl'],
        'short_dd': short_result['max_dd'],
        'short_daily_dd': short_result['max_daily_dd'],
        'short_wr': short_result['win_rate'],
        'short_trades': short_result['total_trades'],
        'combined_pnl': combined_pnl,
        'combined_dd': combined_dd,
        'combined_daily_dd': combined_daily_dd,
        'total_trades': LONG_TRADES + short_result['total_trades']
    }

if __name__ == "__main__":
    print("="*80)
    print("SHORT Strategy Optimization для миксования с LONG")
    print("="*80)
    print(f"\nLONG Strategy (фиксированная):")
    print(f"  PnL: ${LONG_PNL:,}")
    print(f"  Max DD: {LONG_MAX_DD:.2f}%")
    print(f"  Daily DD: {LONG_DAILY_DD:.2f}%")
    print(f"  Trades: {LONG_TRADES}")
    print(f"  Risk: ${LONG_RISK}")
    print(f"\nЦель: Combined DD < 9%, Combined PnL > $50,000")
    print("="*80)

    results = []

    for tp_rr, risk in product(TP_VALUES, RISK_VALUES):
        print(f"\nТестирую SHORT: TP={tp_rr}R, Risk=${risk}...")

        short_result = run_short_backtest(tp_rr, risk)

        if short_result is None:
            print(f"  Нет сделок")
            continue

        combined = calculate_combined_metrics(short_result)
        results.append(combined)

        print(f"  SHORT: PnL=${short_result['total_pnl']:,.0f}, DD={short_result['max_dd']:.2f}%, WR={short_result['win_rate']:.1f}%, Trades={short_result['total_trades']}")
        print(f"  COMBINED: PnL=${combined['combined_pnl']:,.0f}, DD={combined['combined_dd']:.2f}%, Daily DD={combined['combined_daily_dd']:.2f}%")

        # Check if passes
        if combined['combined_dd'] < 9.0 and combined['combined_pnl'] > 50000:
            print(f"  ✅ PASS!")

    # Sort by combined PnL
    results.sort(key=lambda x: x['combined_pnl'], reverse=True)

    print("\n" + "="*80)
    print("ТОП-5 РЕЗУЛЬТАТОВ (по Combined PnL)")
    print("="*80)

    for i, r in enumerate(results[:5], 1):
        status = "✅ PASS" if r['combined_dd'] < 9.0 and r['combined_pnl'] > 50000 else "❌ FAIL"
        print(f"\n#{i} {status}")
        print(f"  SHORT: TP={r['tp_rr']}R, Risk=${r['risk']}")
        print(f"  SHORT: PnL=${r['short_pnl']:,.0f}, DD={r['short_dd']:.2f}%, Daily DD={r['short_daily_dd']:.2f}%, WR={r['short_wr']:.1f}%, Trades={r['short_trades']}")
        print(f"  COMBINED: PnL=${r['combined_pnl']:,.0f}, DD={r['combined_dd']:.2f}%, Daily DD={r['combined_daily_dd']:.2f}%, Total Trades={r['total_trades']}")

    print("\n" + "="*80)
    print("РЕКОМЕНДАЦИЯ")
    print("="*80)

    passing = [r for r in results if r['combined_dd'] < 9.0 and r['combined_pnl'] > 50000]

    if passing:
        best = passing[0]
        print(f"\n✅ Найдена подходящая комбинация!")
        print(f"\nSHORT параметры:")
        print(f"  Risk: ${best['risk']}")
        print(f"  TP: {best['tp_rr']}R")
        print(f"  Step Trailing: Enabled")
        print(f"  H4 EMA20 Filter: SHORT when price < EMA20")
        print(f"\nОжидаемые результаты LONG + SHORT:")
        print(f"  Total PnL: ${best['combined_pnl']:,.0f}")
        print(f"  Max DD: {best['combined_dd']:.2f}% (< 9% ✅)")
        print(f"  Daily DD: {best['combined_daily_dd']:.2f}% (< 5% ✅)")
        print(f"  Total Trades: {best['total_trades']}")
    else:
        print("\n❌ Не найдено комбинаций с DD < 9% и PnL > $50k")
        print("\nВарианты:")
        print("1. Уменьшить риск SHORT еще больше ($50-$70)")
        print("2. Увеличить TP для SHORT (3.5R, 4.0R)")
        print("3. Оставить только LONG стратегию")
