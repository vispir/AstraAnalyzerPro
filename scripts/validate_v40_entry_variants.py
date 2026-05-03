"""
Три варианта улучшения входа — ea_mirror LONG only.

Baseline: ea_mirror, TP=3R, Risk=$120 → 699 trades, WR=42.9%, PnL=$46,721, DD=16.60%

Вариант A: Два подряд M15 бара выше session_high (фильтр ложных пробоев)
Вариант B: Лимитный вход на ретресте к session_high + 0.1*ATR
Вариант C: Фильтр силы пробойной свечи — close в верхней половине бара
            (close - low) / (high - low) > 0.5

Тест: каждый вариант × TP=[3,4,5] × Risk=[$100,$120]
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from session_breakout_trader import (
    ATR_PERIOD, ATR_BUFFER, H4_EMA_PERIOD,
    LONG_SESSIONS,
    calculate_atr, calculate_ema,
)
import pandas as pd
import numpy as np
from pathlib import Path

K_EMA  = 2.0 / (H4_EMA_PERIOD + 1.0)
H4_NS  = int(4 * 3600 * 1e9)
MIN_H4 = 22


def floor4h_ns(ts_ns):
    return int(ts_ns // int(14400 * 1e9)) * int(14400 * 1e9)


def apply_step_trailing(t, low, high):
    risk = t['entry'] - t['initial_sl']
    rr   = (low - t['entry']) / risk
    for trigger, lock in ((5, 4), (4, 3), (3, 2), (2, 1)):
        if rr >= trigger:
            t['sl'] = max(t['sl'], t['entry'] + lock * risk)
            break


def run(df, h4_times, h4_ema20, variant, tp_rr, risk):
    """
    variant: 'baseline' | 'A' | 'B' | 'C'
    """
    n_h4     = len(h4_times)
    times_ns = df.index.asi8
    hours    = np.array([t.hour for t in df.index])
    m15_arr  = df.to_numpy()
    cols     = {c: i for i, c in enumerate(df.columns)}
    i_o = cols['open']; i_h = cols['high']
    i_l = cols['low'];  i_c = cols['close']; i_a = cols['atr']

    ptr_closed     = -1
    forming_period = -1
    forming_close  = np.nan
    ema_base       = np.nan

    trades        = []
    active_long   = {}
    pending_limit = {}   # только для варианта B: sn -> {price, sl, atr}
    balance       = 10_000.0
    peak          = 10_000.0
    max_dd        = 0.0
    max_daily_dd  = 0.0

    prev_date     = None
    day_start_bal = balance
    session_highs = {}
    session_lows  = {}
    prev_above    = {}   # только для варианта A: sn -> bool

    for i in range(len(df)):
        ts_ns  = int(times_ns[i])
        cur_ts = df.index[i]
        hour   = int(hours[i])
        open_  = float(m15_arr[i, i_o])
        high   = float(m15_arr[i, i_h])
        low    = float(m15_arr[i, i_l])
        close  = float(m15_arr[i, i_c])
        atr    = float(m15_arr[i, i_a])
        if np.isnan(atr):
            continue

        cur_date = cur_ts.date()
        if cur_date != prev_date:
            if prev_date is not None and day_start_bal > 0:
                ddaily = (day_start_bal - balance) / day_start_bal * 100
                if ddaily > max_daily_dd: max_daily_dd = ddaily
            day_start_bal = balance
            prev_date     = cur_date
            session_highs = {}
            session_lows  = {}
            prev_above    = {}
            pending_limit = {}

        while ptr_closed + 1 < n_h4 and h4_times[ptr_closed + 1] + H4_NS <= ts_ns:
            ptr_closed += 1

        if ptr_closed < MIN_H4 - 1:
            for sn, p in LONG_SESSIONS.items():
                sh, eh = p['range_hours']
                if sh <= hour < eh:
                    session_highs[sn] = max(session_highs.get(sn, 0), high)
                    session_lows[sn]  = min(session_lows.get(sn, 1e9), low)
            continue

        # Формирующийся H4 EMA (ea_mirror)
        h4p = floor4h_ns(ts_ns)
        if h4p != forming_period:
            forming_period = h4p
            forming_close  = close
            ema_base = h4_ema20[ptr_closed] if ptr_closed >= 0 else np.nan
        else:
            forming_close = close
        if np.isnan(ema_base):
            continue
        ema_ok = forming_close > ema_base  # H4 EMA фильтр

        # ── Управление LONG сделками ─────────────────────────────────────────
        for sn in list(active_long.keys()):
            t = active_long[sn]
            apply_step_trailing(t, low, high)
            if low <= t['sl']:
                t['pnl'] = (t['sl'] - t['entry']) * t['size']
                balance += t['pnl']; t['year'] = cur_ts.year
                trades.append(t); del active_long[sn]
            elif high >= t['tp']:
                t['pnl'] = (t['tp'] - t['entry']) * t['size']
                balance += t['pnl']; t['year'] = cur_ts.year
                trades.append(t); del active_long[sn]

        # ── Вариант B: проверить лимитные ордера ────────────────────────────
        if variant == 'B':
            for sn in list(pending_limit.keys()):
                if sn in active_long:
                    del pending_limit[sn]
                    continue
                pm = pending_limit[sn]
                p  = LONG_SESSIONS[sn]
                # Отменить если вышли за entry window
                if not (p['entry_start'] <= hour < p['entry_end']):
                    del pending_limit[sn]
                    continue
                # Исполнить если цена опустилась до лимита
                if low <= pm['price']:
                    entry = pm['price']
                    sl    = pm['sl']
                    rsk   = entry - sl
                    if rsk > 0:
                        active_long[sn] = {
                            'entry': entry, 'sl': sl, 'initial_sl': sl,
                            'tp': entry + rsk * tp_rr,
                            'size': risk / rsk,
                            'direction': 'LONG', 'session': sn,
                        }
                    del pending_limit[sn]

        if balance > peak: peak = balance
        dd = (peak - balance) / peak * 100
        if dd > max_dd: max_dd = dd

        # ── Диапазоны сессий ─────────────────────────────────────────────────
        for sn, p in LONG_SESSIONS.items():
            sh, eh = p['range_hours']
            if sh <= hour < eh:
                session_highs[sn] = max(session_highs.get(sn, 0), high)
                session_lows[sn]  = min(session_lows.get(sn, 1e9), low)

        if not ema_ok:
            # Вариант A: сбросить prev_above при отсутствии EMA
            if variant == 'A':
                prev_above = {}
            continue

        # ── Входы ────────────────────────────────────────────────────────────
        for sn, p in LONG_SESSIONS.items():
            if sn not in session_highs or sn in active_long:
                continue
            if not (p['entry_start'] <= hour < p['entry_end']):
                if variant == 'A':
                    prev_above.pop(sn, None)
                continue

            sh = session_highs[sn]
            above_now = close > sh

            if variant == 'baseline':
                if above_now:
                    sl  = session_lows[sn] - ATR_BUFFER * atr
                    rsk = close - sl
                    if rsk > 0:
                        active_long[sn] = {
                            'entry': close, 'sl': sl, 'initial_sl': sl,
                            'tp': close + rsk * tp_rr,
                            'size': risk / rsk,
                            'direction': 'LONG', 'session': sn,
                        }

            elif variant == 'A':
                # Два подряд закрытия выше session_high
                was_above = prev_above.get(sn, False)
                if above_now and was_above:
                    sl  = session_lows[sn] - ATR_BUFFER * atr
                    rsk = close - sl
                    if rsk > 0:
                        active_long[sn] = {
                            'entry': close, 'sl': sl, 'initial_sl': sl,
                            'tp': close + rsk * tp_rr,
                            'size': risk / rsk,
                            'direction': 'LONG', 'session': sn,
                        }
                prev_above[sn] = above_now

            elif variant == 'B':
                # Первый пробой → ставим лимит на ретрест
                if above_now and sn not in pending_limit:
                    limit_price = sh + 0.1 * atr
                    sl = session_lows[sn] - ATR_BUFFER * atr
                    rsk = limit_price - sl
                    if rsk > 0:
                        pending_limit[sn] = {
                            'price': limit_price,
                            'sl': sl,
                        }

            elif variant == 'C':
                # Сила свечи: close в верхней половине бара
                bar_range = high - low
                if above_now and bar_range > 0:
                    close_pos = (close - low) / bar_range
                    if close_pos > 0.5:   # close в верхней половине
                        sl  = session_lows[sn] - ATR_BUFFER * atr
                        rsk = close - sl
                        if rsk > 0:
                            active_long[sn] = {
                                'entry': close, 'sl': sl, 'initial_sl': sl,
                                'tp': close + rsk * tp_rr,
                                'size': risk / rsk,
                                'direction': 'LONG', 'session': sn,
                            }

    if prev_date is not None and day_start_bal > 0:
        ddaily = (day_start_bal - balance) / day_start_bal * 100
        if ddaily > max_daily_dd: max_daily_dd = ddaily

    last_close = float(m15_arr[-1, i_c])
    last_year  = int(df.index[-1].year)
    for sn, t in active_long.items():
        t['pnl'] = (last_close - t['entry']) * t['size']
        balance += t['pnl']; t['year'] = last_year; trades.append(t)

    tdf    = pd.DataFrame(trades)
    pnl    = balance - 10_000
    wr     = (tdf['pnl'] > 0).sum() / len(tdf) if len(tdf) > 0 else 0
    yearly = tdf.groupby('year')['pnl'].sum() if len(tdf) > 0 else pd.Series(dtype=float)

    return {
        'n': len(tdf), 'wr': wr, 'pnl': pnl,
        'max_dd': max_dd, 'max_daily_dd': max_daily_dd,
        'yearly': yearly,
        'all_pos': all(yearly > 0) if len(yearly) > 0 else False,
    }


def main():
    print("=" * 72)
    print("ТЕСТ ВАРИАНТОВ ВХОДА — ea_mirror LONG only")
    print("=" * 72)
    print("A: Два M15 закрытия выше session_high")
    print("B: Лимитный вход на ретресте (limit = session_high + 0.1*ATR)")
    print("C: Фильтр силы свечи (close в верхней половине бара)")
    print()

    data_path = (
        Path(__file__).parent.parent
        / "data_cache" / "dukascopy" / "m15" / "XAUUSD"
        / "xauusd_m15_2020-01-01_2026-04-18.parquet"
    )
    df = pd.read_parquet(data_path).sort_index()
    df['atr'] = calculate_atr(df, ATR_PERIOD)

    df_h4 = df.resample('4h').agg(
        {'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last'}
    ).dropna()
    df_h4['ema20'] = calculate_ema(df_h4, H4_EMA_PERIOD)

    h4_times = df_h4.index.asi8
    h4_ema20 = df_h4['ema20'].to_numpy()

    variants  = ['baseline', 'A', 'B', 'C']
    tp_ratios = [3, 4, 5]
    risks     = [100, 120]
    total     = len(variants) * len(tp_ratios) * len(risks)

    all_results = []
    idx = 0
    for v in variants:
        for tp in tp_ratios:
            for rsk in risks:
                idx += 1
                tag = {'baseline':'Baseline','A':'A: двойной пробой','B':'B: ретрест','C':'C: сила свечи'}[v]
                print(f"  [{idx:2d}/{total}] {tag:<22} TP={tp}R Risk=${rsk}...", end='', flush=True)
                r = run(df, h4_times, h4_ema20, v, tp, rsk)
                all_results.append({'variant': v, 'tp': tp, 'risk': rsk, **r})
                hit = ''
                if r['max_dd'] < 10 and r['max_daily_dd'] < 5 and r['all_pos']:
                    hit = '  *** ЦЕЛЬ ***'
                elif r['max_dd'] < 10:
                    hit = '  [DD<10%]'
                print(f"  {r['n']:>4} trades  WR={r['wr']:.1%}  PnL=${r['pnl']:,.0f}  DD={r['max_dd']:.2f}%{hit}")

    rdf = pd.DataFrame(all_results)

    # ── Сводная таблица ───────────────────────────────────────────────────────
    print()
    print("=" * 80)
    print("СВОДНАЯ ТАБЛИЦА (сортировка по PnL, MaxDD < 20%)")
    print("=" * 80)
    labels = {'baseline': 'Baseline', 'A': 'A: двойной пробой', 'B': 'B: ретрест лимит', 'C': 'C: сила свечи'}
    hdr = f"  {'Вариант':<22} {'TP':>3} {'Risk':>5}  {'N':>5}  {'WR':>6}  {'PnL':>9}  {'MaxDD':>8}  {'DlyDD':>7}  {'AllYrs'}"
    print(hdr)
    print("  " + "-" * 78)
    sub = rdf[rdf['max_dd'] < 20].sort_values('pnl', ascending=False)
    for _, row in sub.iterrows():
        ay = 'YES' if row['all_pos'] else 'NO '
        ok = '<10%✓' if row['max_dd'] < 10 else f"{row['max_dd']:.1f}%"
        print(f"  {labels[row['variant']]:<22} {row['tp']:>3}R ${row['risk']:>4}  "
              f"{row['n']:>5}  {row['wr']:>5.1%}  ${row['pnl']:>8,.0f}  "
              f"{ok:>8}  {row['max_daily_dd']:>6.2f}%  {ay}")

    # ── Детальный вывод по лучшему в каждом варианте ─────────────────────────
    print()
    print("=" * 80)
    print("ЛУЧШИЙ РЕЗУЛЬТАТ КАЖДОГО ВАРИАНТА (по PnL)")
    print("=" * 80)
    for v in variants:
        best = rdf[rdf['variant'] == v].sort_values('pnl', ascending=False).iloc[0]
        print(f"\n  [{labels[v]}]  TP={best['tp']}R  Risk=${best['risk']}")
        print(f"  Trades={best['n']}  WR={best['wr']:.1%}  PnL=${best['pnl']:,.0f}"
              f"  MaxDD={best['max_dd']:.2f}%  DlyDD={best['max_daily_dd']:.2f}%"
              f"  AllYrs={'YES' if best['all_pos'] else 'NO'}")
        print(f"  PnL по годам:")
        for yr in sorted(best['yearly'].keys()):
            v2 = best['yearly'][yr]
            mk = '✓' if v2 > 0 else '✗'
            print(f"    {yr}: ${v2:>7,.0f} {mk}")

    # ── Достигшие цели ────────────────────────────────────────────────────────
    print()
    print("=" * 80)
    targets = rdf[(rdf['max_dd'] < 10) & (rdf['max_daily_dd'] < 5) & (rdf['all_pos'])]
    if len(targets) > 0:
        print("ЦЕЛЬ ДОСТИГНУТА (DD<10%, DlyDD<5%, все годы в плюс):")
        for _, row in targets.sort_values('pnl', ascending=False).iterrows():
            print(f"  *** {labels[row['variant']]}  TP={row['tp']}R  Risk=${row['risk']}  "
                  f"N={row['n']}  WR={row['wr']:.1%}  PnL=${row['pnl']:,.0f}  "
                  f"MaxDD={row['max_dd']:.2f}%")
    else:
        print("Цель не достигнута ни одним вариантом.")
        best_dd = rdf.sort_values('max_dd').iloc[0]
        print(f"  Лучший по DD: {labels[best_dd['variant']]} TP={best_dd['tp']}R "
              f"Risk=${best_dd['risk']}  MaxDD={best_dd['max_dd']:.2f}%  "
              f"PnL=${best_dd['pnl']:,.0f}")
    print("=" * 80)


if __name__ == "__main__":
    main()
