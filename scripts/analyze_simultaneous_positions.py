"""
Анализ одновременных позиций и дневных убытков v4.0
====================================================
Проверяем:
1. Сколько раз все 4 позиции открыты одновременно
2. Сколько раз убытки по 2/3/4 позициям в один день
3. Реальный максимальный убыток за день
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
import numpy as np
from pathlib import Path
from collections import defaultdict

# v3.0 Parameters
ATR_PERIOD = 14
ATR_BUFFER = 0.5
TP_RR = 5.5
RISK_PER_TRADE = 158
USE_H4_EMA_FILTER = True
H4_EMA_PERIOD = 20

LONG_SESSIONS = {
    'asian': {
        'range_hours': (7, 10),
        'entry_start': 10,
        'entry_end': 24
    },
    'london': {
        'range_hours': (13, 16),
        'entry_start': 16,
        'entry_end': 24
    },
    'ny': {
        'range_hours': (13, 17),
        'entry_start': 18,
        'entry_end': 21
    }
}

SHORT_TYPE1_LOOKBACK_H4_BARS = 5
SHORT_TYPE2_H4_LOOKBACK = 3
SHORT_TYPE2_ATR_MULTIPLIER = 2.0

def calculate_atr(df, period=14):
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

def run_analysis():
    print("="*80)
    print("АНАЛИЗ ОДНОВРЕМЕННЫХ ПОЗИЦИЙ И ДНЕВНЫХ УБЫТКОВ")
    print("="*80)
    print()

    # Load data
    data_path = Path(__file__).parent.parent / "data_cache" / "dukascopy" / "m15" / "XAUUSD" / "xauusd_m15_2020-01-01_2026-04-18.parquet"
    df = pd.read_parquet(data_path)
    df = df.sort_index()

    # Prepare data
    df['atr'] = calculate_atr(df, ATR_PERIOD)
    df_h4 = df.resample('4h').agg({'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last'}).dropna()
    df_h4['atr'] = calculate_atr(df_h4, ATR_PERIOD)
    df_h4['ema20'] = calculate_ema(df_h4, H4_EMA_PERIOD)

    trades = []
    active_long_trades = {}
    active_short = None
    balance = 10000

    # Track simultaneous positions
    four_positions_count = 0
    four_positions_moments = []
    position_count_history = []

    # Track daily losses
    daily_pnl = defaultdict(float)
    daily_losses_by_position = defaultdict(lambda: {'count': 0, 'pnl': 0})

    short_type1_reversal_active = False
    short_type1_reversal_h4_high = None
    short_type2_reversal_active = False
    short_type2_reversal_h4_high = None
    last_h4_index = None

    dates = df.index.date
    unique_dates = sorted(set(dates))

    for date in unique_dates:
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
            atr = atrs[i]

            if np.isnan(atr):
                continue

            h4_bars = df_h4[df_h4.index <= current_time]
            if len(h4_bars) < max(SHORT_TYPE1_LOOKBACK_H4_BARS + 2, SHORT_TYPE2_H4_LOOKBACK + 1):
                continue

            current_h4 = h4_bars.iloc[-1]

            # Track simultaneous positions
            num_active = len(active_long_trades) + (1 if active_short is not None else 0)
            position_count_history.append({
                'time': current_time,
                'count': num_active,
                'sessions': list(active_long_trades.keys()) + (['short'] if active_short else [])
            })

            # Check if all 4 positions active
            if num_active == 4:
                if 'asian' in active_long_trades and 'london' in active_long_trades and 'ny' in active_long_trades and active_short is not None:
                    four_positions_count += 1
                    four_positions_moments.append(current_time)

            # === MANAGE ACTIVE LONG TRADES ===
            for session_name in list(active_long_trades.keys()):
                trade = active_long_trades[session_name]
                apply_step_trailing(trade, lows[i], highs[i], is_long=True)

                exit_trade = False
                if lows[i] <= trade['sl']:
                    pnl = (trade['sl'] - trade['entry']) * trade['size']
                    balance += pnl
                    trade['exit'] = trade['sl']
                    trade['pnl'] = pnl
                    trade['status'] = 'sl'
                    trade['exit_date'] = current_time.date()
                    exit_trade = True
                elif highs[i] >= trade['tp']:
                    pnl = (trade['tp'] - trade['entry']) * trade['size']
                    balance += pnl
                    trade['exit'] = trade['tp']
                    trade['pnl'] = pnl
                    trade['status'] = 'tp'
                    trade['exit_date'] = current_time.date()
                    exit_trade = True

                if exit_trade:
                    trade['exit_time'] = current_time
                    trade['year'] = current_time.year
                    trades.append(trade)

                    # Track daily PnL
                    daily_pnl[trade['exit_date']] += pnl

                    del active_long_trades[session_name]

            # === MANAGE ACTIVE SHORT TRADE ===
            if active_short is not None:
                apply_step_trailing(active_short, lows[i], highs[i], is_long=False)

                exit_trade = False
                if highs[i] >= active_short['sl']:
                    pnl = (active_short['entry'] - active_short['sl']) * active_short['size']
                    balance += pnl
                    active_short['exit'] = active_short['sl']
                    active_short['pnl'] = pnl
                    active_short['status'] = 'sl'
                    active_short['exit_date'] = current_time.date()
                    exit_trade = True
                elif lows[i] <= active_short['tp']:
                    pnl = (active_short['entry'] - active_short['tp']) * active_short['size']
                    balance += pnl
                    active_short['exit'] = active_short['tp']
                    active_short['pnl'] = pnl
                    active_short['status'] = 'tp'
                    active_short['exit_date'] = current_time.date()
                    exit_trade = True

                if exit_trade:
                    active_short['exit_time'] = current_time
                    active_short['year'] = current_time.year
                    trades.append(active_short)

                    # Track daily PnL
                    daily_pnl[active_short['exit_date']] += pnl

                    active_short = None
                    short_type1_reversal_active = False
                    short_type2_reversal_active = False

            # === TRACK SESSION RANGES ===
            for session_name, params in LONG_SESSIONS.items():
                start_hour, end_hour = params['range_hours']
                if start_hour <= hour < end_hour:
                    if session_name not in session_highs:
                        session_highs[session_name] = highs[i]
                        session_lows[session_name] = lows[i]
                    else:
                        session_highs[session_name] = max(session_highs[session_name], highs[i])
                        session_lows[session_name] = min(session_lows[session_name], lows[i])

            # === LONG ENTRY LOGIC ===
            for session_name, params in LONG_SESSIONS.items():
                if session_name in session_highs and session_name not in active_long_trades:
                    entry_start = params['entry_start']
                    entry_end = params['entry_end']

                    if entry_start <= hour < entry_end:
                        session_high = session_highs[session_name]
                        session_low = session_lows[session_name]

                        if closes[i] > session_high:
                            if USE_H4_EMA_FILTER:
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
                            size = RISK_PER_TRADE / risk

                            active_long_trades[session_name] = {
                                'entry': entry,
                                'sl': sl,
                                'initial_sl': sl,
                                'tp': tp,
                                'size': size,
                                'direction': 'LONG',
                                'entry_time': current_time,
                                'session': session_name
                            }

            # === SHORT ENTRY LOGIC ===
            if active_short is None and hour < 21:
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
                                'direction': 'SHORT',
                                'entry_time': current_time,
                                'session': 'short'
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
                                'direction': 'SHORT',
                                'entry_time': current_time,
                                'session': 'short'
                            }
                            short_type2_reversal_active = False

    # Close remaining trades
    for session_name, trade in active_long_trades.items():
        last_bar = df.iloc[-1]
        pnl = (last_bar['close'] - trade['entry']) * trade['size']
        balance += pnl
        trade['exit'] = last_bar['close']
        trade['exit_time'] = df.index[-1]
        trade['exit_date'] = df.index[-1].date()
        trade['pnl'] = pnl
        trade['status'] = 'eod'
        trade['year'] = df.index[-1].year
        trades.append(trade)
        daily_pnl[trade['exit_date']] += pnl

    if active_short is not None:
        last_bar = df.iloc[-1]
        pnl = (active_short['entry'] - last_bar['close']) * active_short['size']
        balance += pnl
        active_short['exit'] = last_bar['close']
        active_short['exit_time'] = df.index[-1]
        active_short['exit_date'] = df.index[-1].date()
        active_short['pnl'] = pnl
        active_short['status'] = 'eod'
        active_short['year'] = df.index[-1].year
        trades.append(active_short)
        daily_pnl[active_short['exit_date']] += pnl

    # Analysis
    trades_df = pd.DataFrame(trades)

    # Analyze daily losses by number of positions
    for date in daily_pnl.keys():
        day_trades = trades_df[trades_df['exit_date'] == date]
        day_losses = day_trades[day_trades['pnl'] < 0]

        if len(day_losses) > 0:
            num_losses = len(day_losses)
            total_loss = day_losses['pnl'].sum()
            daily_losses_by_position[num_losses]['count'] += 1
            daily_losses_by_position[num_losses]['pnl'] = min(
                daily_losses_by_position[num_losses]['pnl'],
                total_loss
            )

    # Find worst daily loss
    worst_daily_loss = min(daily_pnl.values())
    worst_daily_loss_date = [k for k, v in daily_pnl.items() if v == worst_daily_loss][0]

    # Print results
    print("="*80)
    print("1. ОДНОВРЕМЕННЫЕ ПОЗИЦИИ")
    print("="*80)
    print(f"Всего M15 баров обработано: {len(position_count_history):,}")
    print(f"Моментов с 4 позициями одновременно: {four_positions_count:,}")
    print(f"Процент времени с 4 позициями: {four_positions_count / len(position_count_history) * 100:.2f}%")
    print()

    if len(four_positions_moments) > 0:
        print("Первые 10 моментов с 4 позициями:")
        for moment in four_positions_moments[:10]:
            print(f"  {moment}")
        print()

    # Count position distribution
    position_counts = defaultdict(int)
    for record in position_count_history:
        position_counts[record['count']] += 1

    print("Распределение количества одновременных позиций:")
    for count in sorted(position_counts.keys()):
        pct = position_counts[count] / len(position_count_history) * 100
        print(f"  {count} позиций: {position_counts[count]:,} моментов ({pct:.2f}%)")
    print()

    print("="*80)
    print("2. ДНЕВНЫЕ УБЫТКИ ПО КОЛИЧЕСТВУ ПОЗИЦИЙ")
    print("="*80)

    for num_losses in sorted(daily_losses_by_position.keys()):
        data = daily_losses_by_position[num_losses]
        print(f"{num_losses} убыточных позиций в день:")
        print(f"  Количество дней: {data['count']}")
        print(f"  Худший убыток: ${data['pnl']:,.2f}")
        print()

    print("="*80)
    print("3. МАКСИМАЛЬНЫЙ ДНЕВНОЙ УБЫТОК")
    print("="*80)
    print(f"Худший день: {worst_daily_loss_date}")
    print(f"Убыток: ${worst_daily_loss:,.2f}")
    print()

    # Show trades on worst day
    worst_day_trades = trades_df[trades_df['exit_date'] == worst_daily_loss_date]
    print(f"Сделки в худший день ({len(worst_day_trades)} trades):")
    for _, trade in worst_day_trades.iterrows():
        status = "WIN" if trade['pnl'] > 0 else "LOSS"
        print(f"  {trade['session']:8s} {trade['direction']:5s} ${trade['pnl']:>8,.2f} ({status})")
    print()

    # Find best daily profit for comparison
    best_daily_profit = max(daily_pnl.values())
    best_daily_profit_date = [k for k, v in daily_pnl.items() if v == best_daily_profit][0]

    print("="*80)
    print("СРАВНЕНИЕ: ЛУЧШИЙ vs ХУДШИЙ ДЕНЬ")
    print("="*80)
    print(f"Лучший день: {best_daily_profit_date}, прибыль ${best_daily_profit:,.2f}")
    print(f"Худший день: {worst_daily_loss_date}, убыток ${worst_daily_loss:,.2f}")
    print(f"Разница: ${best_daily_profit - worst_daily_loss:,.2f}")
    print()

    print("="*80)
    print("ИТОГОВАЯ СТАТИСТИКА")
    print("="*80)
    print(f"Total PnL: ${balance - 10000:,.0f}")
    print(f"Total Trades: {len(trades_df)}")
    print(f"Дней с убытками: {len([v for v in daily_pnl.values() if v < 0])}")
    print(f"Дней с прибылью: {len([v for v in daily_pnl.values() if v > 0])}")
    print(f"Средний дневной PnL: ${sum(daily_pnl.values()) / len(daily_pnl):,.2f}")
    print("="*80)

if __name__ == "__main__":
    run_analysis()
