"""
Бэктест v4.0 — SHORT использует H1 бары вместо H4 (без look-ahead).

Гипотеза: H4 без look-ahead = сигнал опаздывает до 4 часов.
         H1 без look-ahead = сигнал опаздывает только 1 час → больше входов, лучше результат.

Два варианта SHORT:
  A) "same_bars"  — те же числа баров: lookback=5 H1, t2=3 H1, EMA20 H1
  B) "time_equiv" — то же время:      lookback=20 H1, t2=12 H1, EMA80 H1, ATR_mult=8.0

LONG: без look-ahead H4 (аналогично validate_v40_no_lookahead.py).

Сравнение:
  Оригинал (H4 look-ahead): SHORT 188 сделок $19,320  LONG 693 $61,181  Total $80,501
  H4 без look-ahead:        SHORT 136 сделок $840      LONG 518 $43,601  Total $44,441
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from session_breakout_trader import (
    RISK_PER_TRADE, TP_RR, ATR_PERIOD, ATR_BUFFER,
    USE_H4_EMA_FILTER,
    LONG_SESSIONS,
    SHORT_TYPE2_ATR_MULTIPLIER,
    calculate_atr, calculate_ema
)

import pandas as pd
import numpy as np
from pathlib import Path


def apply_step_trailing(active_trade, current_low, current_high, is_long=True):
    if is_long:
        risk = active_trade['entry'] - active_trade['initial_sl']
        profit_in_r = (current_low - active_trade['entry']) / risk
        if profit_in_r >= 5.0:
            active_trade['sl'] = max(active_trade['sl'], active_trade['entry'] + 4.0 * risk)
        elif profit_in_r >= 4.0:
            active_trade['sl'] = max(active_trade['sl'], active_trade['entry'] + 3.0 * risk)
        elif profit_in_r >= 3.0:
            active_trade['sl'] = max(active_trade['sl'], active_trade['entry'] + 2.0 * risk)
        elif profit_in_r >= 2.0:
            active_trade['sl'] = max(active_trade['sl'], active_trade['entry'] + 1.0 * risk)
    else:
        risk = active_trade['initial_sl'] - active_trade['entry']
        profit_in_r = (active_trade['entry'] - current_high) / risk
        if profit_in_r >= 5.0:
            active_trade['sl'] = min(active_trade['sl'], active_trade['entry'] - 4.0 * risk)
        elif profit_in_r >= 4.0:
            active_trade['sl'] = min(active_trade['sl'], active_trade['entry'] - 3.0 * risk)
        elif profit_in_r >= 3.0:
            active_trade['sl'] = min(active_trade['sl'], active_trade['entry'] - 2.0 * risk)
        elif profit_in_r >= 2.0:
            active_trade['sl'] = min(active_trade['sl'], active_trade['entry'] - 1.0 * risk)


def run_variant(
    df, df_h4, df_h1,
    short_lookback, short_t2_lookback, short_ema_period, short_t2_atr_mult,
    label
):
    """Общий движок для обоих вариантов H1 SHORT."""
    h4_offset = pd.Timedelta(hours=4)
    h1_offset = pd.Timedelta(hours=1)

    # Пересчитать EMA на h1 с нужным периодом
    df_h1 = df_h1.copy()
    df_h1[f'ema{short_ema_period}'] = calculate_ema(df_h1, short_ema_period)

    trades = []
    active_long_trades = {}
    active_short = None
    balance = 10000
    peak_balance = 10000
    max_dd = 0
    max_daily_dd = 0

    short_type1_active = False
    short_type1_h1_high = None
    short_type2_active = False
    short_type2_h1_high = None
    last_h1_index = None

    min_h1_bars_needed = max(short_lookback + 2, short_t2_lookback + 2)
    min_h4_bars_needed = 22  # для EMA20 на H4 (LONG фильтр)

    for date in sorted(set(df.index.date)):
        day_start_balance = balance
        day_data = df[df.index.date == date]
        if len(day_data) < 10:
            continue

        session_highs = {}
        session_lows = {}
        highs  = day_data['high'].to_numpy()
        lows   = day_data['low'].to_numpy()
        closes = day_data['close'].to_numpy()
        atrs   = day_data['atr'].to_numpy()
        hours  = np.array([t.hour for t in day_data.index])
        times  = day_data.index.to_numpy()

        for i in range(len(day_data)):
            current_time = times[i]
            hour = hours[i]
            atr  = atrs[i]
            if np.isnan(atr):
                continue

            # LONG: завершённые H4 бары (без look-ahead)
            h4_bars = df_h4[df_h4.index + h4_offset <= current_time]
            # SHORT: завершённые H1 бары (без look-ahead)
            h1_bars = df_h1[df_h1.index + h1_offset <= current_time]

            if len(h4_bars) < min_h4_bars_needed or len(h1_bars) < min_h1_bars_needed:
                continue

            current_h4 = h4_bars.iloc[-1]

            # Управление LONG сделками
            for sn in list(active_long_trades.keys()):
                t = active_long_trades[sn]
                apply_step_trailing(t, lows[i], highs[i], is_long=True)
                if lows[i] <= t['sl']:
                    t['pnl'] = (t['sl'] - t['entry']) * t['size']
                    balance += t['pnl']; t['year'] = current_time.year
                    trades.append(t); del active_long_trades[sn]
                elif highs[i] >= t['tp']:
                    t['pnl'] = (t['tp'] - t['entry']) * t['size']
                    balance += t['pnl']; t['year'] = current_time.year
                    trades.append(t); del active_long_trades[sn]

            # Управление SHORT сделкой
            if active_short is not None:
                apply_step_trailing(active_short, lows[i], highs[i], is_long=False)
                if highs[i] >= active_short['sl']:
                    active_short['pnl'] = (active_short['entry'] - active_short['sl']) * active_short['size']
                    balance += active_short['pnl']; active_short['year'] = current_time.year
                    trades.append(active_short); active_short = None
                    short_type1_active = False; short_type2_active = False
                elif lows[i] <= active_short['tp']:
                    active_short['pnl'] = (active_short['entry'] - active_short['tp']) * active_short['size']
                    balance += active_short['pnl']; active_short['year'] = current_time.year
                    trades.append(active_short); active_short = None
                    short_type1_active = False; short_type2_active = False

            # DD
            if balance > peak_balance: peak_balance = balance
            dd = (peak_balance - balance) / peak_balance * 100
            if dd > max_dd: max_dd = dd

            # Диапазоны сессий
            for sn, params in LONG_SESSIONS.items():
                sh, eh = params['range_hours']
                if sh <= hour < eh:
                    session_highs[sn] = max(session_highs.get(sn, 0), highs[i])
                    session_lows[sn]  = min(session_lows.get(sn, 1e9), lows[i])

            # LONG входы (H4 без look-ahead)
            for sn, params in LONG_SESSIONS.items():
                if sn not in session_highs or sn in active_long_trades:
                    continue
                if not (params['entry_start'] <= hour < params['entry_end']):
                    continue
                if closes[i] > session_highs[sn]:
                    if USE_H4_EMA_FILTER:
                        if pd.isna(current_h4['ema20']) or current_h4['close'] < current_h4['ema20']:
                            continue
                    sl   = session_lows[sn] - ATR_BUFFER * atr
                    risk = closes[i] - sl
                    if risk <= 0: continue
                    tp = closes[i] + risk * TP_RR
                    active_long_trades[sn] = {
                        'entry': closes[i], 'sl': sl, 'initial_sl': sl,
                        'tp': tp, 'size': RISK_PER_TRADE / risk,
                        'direction': 'LONG', 'session': sn
                    }

            # SHORT state machine — H1 бары, без look-ahead
            if active_short is None and hour < 21:
                current_h1 = h1_bars.iloc[-1]
                prev_h1    = h1_bars.iloc[-2]
                cur_h1_idx = current_h1.name

                if last_h1_index != cur_h1_idx:
                    last_h1_index = cur_h1_idx
                    ema_col = f'ema{short_ema_period}'

                    if USE_H4_EMA_FILTER:
                        if pd.isna(current_h1[ema_col]) or current_h1['close'] >= current_h1[ema_col]:
                            short_type1_active = False; short_type2_active = False
                            continue

                    # Type 1: новый максимум за lookback H1 баров + медвежье закрытие
                    if not short_type1_active:
                        lookback_highs = h1_bars.iloc[-short_lookback-1:-1]['high']
                        if current_h1['high'] > lookback_highs.max() and current_h1['close'] < prev_h1['close']:
                            short_type1_active = True
                            short_type1_h1_high = current_h1['high']

                    # Type 2: движение >= ATR_mult x H1_ATR за t2_lookback баров + медвежье закрытие
                    if not short_type2_active and len(h1_bars) >= short_t2_lookback + 2:
                        lookback_bars = h1_bars.iloc[-short_t2_lookback-1:-1]
                        move = current_h1['high'] - lookback_bars['low'].min()
                        h1_atr = current_h1.get('atr', atr)
                        if not np.isnan(h1_atr) and move >= short_t2_atr_mult * h1_atr:
                            if current_h1['close'] < prev_h1['close']:
                                short_type2_active = True
                                short_type2_h1_high = current_h1['high']

                # M15 триггер входа
                if i > 0:
                    prev_low = lows[i-1]
                    if short_type1_active and closes[i] < prev_low:
                        sl = short_type1_h1_high + ATR_BUFFER * atr
                        risk = sl - closes[i]
                        if risk > 0:
                            active_short = {
                                'entry': closes[i], 'sl': sl, 'initial_sl': sl,
                                'tp': closes[i] - risk * TP_RR,
                                'size': RISK_PER_TRADE / risk,
                                'direction': 'SHORT', 'session': 'short'
                            }
                            short_type1_active = False
                    elif short_type2_active and closes[i] < prev_low:
                        sl = short_type2_h1_high + ATR_BUFFER * atr
                        risk = sl - closes[i]
                        if risk > 0:
                            active_short = {
                                'entry': closes[i], 'sl': sl, 'initial_sl': sl,
                                'tp': closes[i] - risk * TP_RR,
                                'size': RISK_PER_TRADE / risk,
                                'direction': 'SHORT', 'session': 'short'
                            }
                            short_type2_active = False

        if day_start_balance > 0:
            ddaily = (day_start_balance - balance) / day_start_balance * 100
            if ddaily > max_daily_dd: max_daily_dd = ddaily

    # Закрыть оставшиеся сделки
    last_bar = df.iloc[-1]
    for sn, t in active_long_trades.items():
        t['pnl'] = (last_bar['close'] - t['entry']) * t['size']
        balance += t['pnl']; t['year'] = df.index[-1].year; trades.append(t)
    if active_short is not None:
        active_short['pnl'] = (active_short['entry'] - last_bar['close']) * active_short['size']
        balance += active_short['pnl']; active_short['year'] = df.index[-1].year; trades.append(active_short)

    tdf = pd.DataFrame(trades)
    total_pnl = balance - 10000
    wr = len(tdf[tdf['pnl'] > 0]) / len(tdf)
    long_df  = tdf[tdf['direction'] == 'LONG']
    short_df = tdf[tdf['direction'] == 'SHORT']
    yearly   = tdf.groupby('year')['pnl'].sum()

    print(f"\n{'='*70}")
    print(f"ВАРИАНТ: {label}")
    print(f"  SHORT lookback: {short_lookback} H1 баров ({short_lookback}ч)")
    print(f"  SHORT T2 lookback: {short_t2_lookback} H1 баров ({short_t2_lookback}ч)")
    print(f"  EMA: {short_ema_period} на H1 ({short_ema_period}ч период)")
    print(f"  ATR multiplier: {short_t2_atr_mult}x H1 ATR")
    print(f"{'='*70}")
    print(f"Total Trades: {len(tdf)}  Win Rate: {wr:.1%}")
    print(f"Total PnL:   ${total_pnl:,.0f}")
    print(f"Max DD:      {max_dd:.2f}%  Max Daily DD: {max_daily_dd:.2f}%")
    print(f"  LONG:  {len(long_df)} trades  ${long_df['pnl'].sum():,.0f}")
    print(f"  SHORT: {len(short_df)} trades  ${short_df['pnl'].sum():,.0f}")
    print(f"PNL по годам:")
    for yr in sorted(yearly.keys()):
        print(f"  {yr}: ${yearly[yr]:,.0f}")
    print(f"Все годы прибыльны: {'ДА' if all(yearly > 0) else 'НЕТ'}")

    return {
        'label': label,
        'total': len(tdf), 'wr': wr, 'pnl': total_pnl,
        'max_dd': max_dd, 'max_daily_dd': max_daily_dd,
        'long_n': len(long_df), 'long_pnl': long_df['pnl'].sum(),
        'short_n': len(short_df), 'short_pnl': short_df['pnl'].sum(),
        'yearly': yearly,
    }


def run():
    print("="*70)
    print("БЭКТЕСТ v4.0 — SHORT на H1 барах (без look-ahead)")
    print("LONG: H4 завершённые бары (без look-ahead)")
    print("="*70)

    data_path = Path(__file__).parent.parent / "data_cache" / "dukascopy" / "m15" / "XAUUSD" / "xauusd_m15_2020-01-01_2026-04-18.parquet"
    df = pd.read_parquet(data_path)
    df = df.sort_index()
    print(f"Период: {df.index[0]} - {df.index[-1]}")

    df['atr'] = calculate_atr(df, ATR_PERIOD)

    # H4 для LONG
    df_h4 = df.resample('4h').agg({'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last'}).dropna()
    df_h4['atr'] = calculate_atr(df_h4, ATR_PERIOD)
    df_h4['ema20'] = calculate_ema(df_h4, 20)

    # H1 для SHORT
    df_h1 = df.resample('1h').agg({'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last'}).dropna()
    df_h1['atr'] = calculate_atr(df_h1, ATR_PERIOD)

    print("\nЗапуск вариантов...\n")

    # Вариант A: те же числа баров (5 H1, 3 H1, EMA20)
    # 5 H1 = 5 часов lookback вместо 20 часов (5 H4)
    result_a = run_variant(
        df, df_h4, df_h1,
        short_lookback=5,
        short_t2_lookback=3,
        short_ema_period=20,
        short_t2_atr_mult=SHORT_TYPE2_ATR_MULTIPLIER,   # 2.0x H1 ATR
        label="A — same_bars (5 H1, 3 H1, EMA20, ATRx2.0)"
    )

    # Вариант B: то же время (20 H1 = 5 H4, 12 H1 = 3 H4, EMA80 ≈ EMA20 H4)
    # H1 ATR ≈ H4 ATR/4, поэтому 2x H4 ATR ≈ 8x H1 ATR
    result_b = run_variant(
        df, df_h4, df_h1,
        short_lookback=20,
        short_t2_lookback=12,
        short_ema_period=80,
        short_t2_atr_mult=8.0,                           # ~2x H4 ATR в единицах H1 ATR
        label="B — time_equiv (20 H1, 12 H1, EMA80, ATRx8.0)"
    )

    # Вариант C: промежуточный (10 H1 = ~2.5 H4, 6 H1, EMA40)
    result_c = run_variant(
        df, df_h4, df_h1,
        short_lookback=10,
        short_t2_lookback=6,
        short_ema_period=40,
        short_t2_atr_mult=4.0,
        label="C — mid (10 H1, 6 H1, EMA40, ATRx4.0)"
    )

    print("\n" + "="*70)
    print("ИТОГОВОЕ СРАВНЕНИЕ")
    print("="*70)
    print(f"{'Версия':<45} {'Trades':>6} {'PnL':>9} {'SHORT':>8} {'SHORT PnL':>11} {'DD%':>6} {'Все +':>6}")
    print("-"*70)

    baselines = [
        ("Оригинал H4 (look-ahead)",       881, 80501,  188, 19320, 6.99, True),
        ("H4 без look-ahead",               654, 44441,  136,   840, None, None),
    ]
    for name, t, pnl, sn, spnl, dd, ay in baselines:
        dd_str = f"{dd:.2f}" if dd else "?"
        ay_str = "ДА" if ay else ("НЕТ" if ay is False else "?")
        print(f"  {name:<43} {t:>6} ${pnl:>8,} {sn:>8} ${spnl:>9,} {dd_str:>6} {ay_str:>6}")

    print()
    for r in [result_a, result_b, result_c]:
        ay = all(r['yearly'] > 0)
        print(f"  {r['label']:<43} {r['total']:>6} ${r['pnl']:>8,.0f} {r['short_n']:>8} ${r['short_pnl']:>9,.0f} {r['max_dd']:>5.2f}% {'ДА' if ay else 'НЕТ':>6}")

    print("="*70)


if __name__ == "__main__":
    run()
