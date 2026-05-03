"""
Комбинации EMA slope + сила свечи (вариант C).

Фильтры:
  slope-N  : h4_ema (forming) > h4_ema20[ptr_closed - N]
  candle-C : (close - low) / (high - low) > 0.5

Варианты:
  baseline   — без slope, без C
  slope3     — только slope-3
  slope5     — только slope-5
  C          — только сила свечи
  slope3+C   — оба
  slope5+C   — оба

Тест: × TP=[3,4,5] × Risk=[$100,$120]
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from session_breakout_trader import (
    ATR_PERIOD, ATR_BUFFER, H4_EMA_PERIOD,
    LONG_SESSIONS, calculate_atr, calculate_ema,
)
import pandas as pd
import numpy as np
from pathlib import Path

K_EMA = 2.0 / (H4_EMA_PERIOD + 1.0)
H4_NS = int(4 * 3600 * 1e9)
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


def run(df, h4_times, h4_ema20, slope_n, use_candle_c, tp_rr, risk):
    """
    slope_n     : 0 = нет slope фильтра, иначе количество H4 баров назад
    use_candle_c: фильтр силы свечи
    """
    n_h4     = len(h4_times)
    times_ns = df.index.asi8
    hours    = np.array([t.hour for t in df.index])
    m15_arr  = df.to_numpy()
    cols     = {c: i for i, c in enumerate(df.columns)}
    i_h = cols['high']; i_l = cols['low']
    i_c = cols['close']; i_a = cols['atr']

    ptr_closed     = -1
    forming_period = -1
    forming_close  = np.nan
    ema_base       = np.nan

    trades        = []
    active_long   = {}
    balance       = 10_000.0
    peak          = 10_000.0
    max_dd        = 0.0
    max_daily_dd  = 0.0
    prev_date     = None
    day_start_bal = balance
    session_highs = {}
    session_lows  = {}

    for i in range(len(df)):
        ts_ns  = int(times_ns[i])
        cur_ts = df.index[i]
        hour   = int(hours[i])
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

        while ptr_closed + 1 < n_h4 and h4_times[ptr_closed + 1] + H4_NS <= ts_ns:
            ptr_closed += 1

        if ptr_closed < MIN_H4 - 1:
            for sn, p in LONG_SESSIONS.items():
                sh, eh = p['range_hours']
                if sh <= hour < eh:
                    session_highs[sn] = max(session_highs.get(sn, 0), high)
                    session_lows[sn]  = min(session_lows.get(sn, 1e9), low)
            continue

        h4p = floor4h_ns(ts_ns)
        if h4p != forming_period:
            forming_period = h4p
            forming_close  = close
            ema_base = h4_ema20[ptr_closed] if ptr_closed >= 0 else np.nan
        else:
            forming_close = close

        if np.isnan(ema_base):
            continue

        h4_ema = forming_close * K_EMA + ema_base * (1.0 - K_EMA)

        # Вычислить флаги фильтров (без continue — сначала всегда управляем сделками)
        ema_ok = (forming_close > ema_base)

        slope_ok = True
        if slope_n > 0:
            if ptr_closed < slope_n:
                slope_ok = False
            else:
                ema_ago = h4_ema20[ptr_closed - slope_n]
                slope_ok = (not np.isnan(ema_ago)) and (h4_ema > ema_ago)

        # Управление LONG сделками — ВСЕГДА, независимо от фильтров
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

        if balance > peak: peak = balance
        dd = (peak - balance) / peak * 100
        if dd > max_dd: max_dd = dd

        # Обновление session_highs/lows — ВСЕГДА
        for sn, p in LONG_SESSIONS.items():
            sh, eh = p['range_hours']
            if sh <= hour < eh:
                session_highs[sn] = max(session_highs.get(sn, 0), high)
                session_lows[sn]  = min(session_lows.get(sn, 1e9), low)

        # Новые входы — только если все фильтры прошли
        if not (ema_ok and slope_ok):
            continue

        for sn, p in LONG_SESSIONS.items():
            if sn not in session_highs or sn in active_long:
                continue
            if not (p['entry_start'] <= hour < p['entry_end']):
                continue
            if close > session_highs[sn]:
                # Candle-C filter
                if use_candle_c:
                    bar_range = high - low
                    if bar_range > 0 and (close - low) / bar_range <= 0.5:
                        continue
                sl  = session_lows[sn] - ATR_BUFFER * atr
                rsk = close - sl
                if rsk <= 0:
                    continue
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
    print("GRID: EMA slope-N + Candle-C × TP × Risk")
    print("=" * 72)

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

    # (label, slope_n, use_C)
    combos = [
        ('baseline',  0, False),
        ('slope3',    3, False),
        ('slope5',    5, False),
        ('C',         0, True),
        ('slope3+C',  3, True),
        ('slope5+C',  5, True),
    ]
    tp_list   = [3, 4, 5]
    risk_list = [100, 120]
    total     = len(combos) * len(tp_list) * len(risk_list)

    all_res = []
    idx = 0
    for label, slope_n, use_c in combos:
        for tp in tp_list:
            for rsk in risk_list:
                idx += 1
                print(f"  [{idx:2d}/{total}] {label:<12} TP={tp}R Risk=${rsk}...", end='', flush=True)
                r = run(df, h4_times, h4_ema20, slope_n, use_c, tp, rsk)
                all_res.append({'label': label, 'slope': slope_n, 'candle_c': use_c,
                                'tp': tp, 'risk': rsk, **r})
                hit = ''
                if r['max_dd'] < 10 and r['max_daily_dd'] < 5 and r['all_pos']:
                    hit = '  *** ЦЕЛЬ ***'
                elif r['max_dd'] < 10:
                    hit = '  [DD<10%]'
                print(f"  {r['n']:>4} trades  WR={r['wr']:.1%}  PnL=${r['pnl']:,.0f}"
                      f"  DD={r['max_dd']:.2f}%{hit}")

    rdf = pd.DataFrame(all_res)

    # ── Сводная таблица ───────────────────────────────────────────────────────
    print()
    print("=" * 80)
    print("ВСЕ РЕЗУЛЬТАТЫ  (сортировка: DD по возрастанию, при DD<12 — PnL по убыванию)")
    print("=" * 80)
    hdr = f"  {'Вариант':<14} {'TP':>3} {'Risk':>5}  {'N':>5}  {'WR':>6}  {'PnL':>9}  {'MaxDD':>8}  {'DlyDD':>7}  {'AllYrs'}"
    print(hdr)
    print("  " + "-" * 76)

    # Разделить: DD<12 (sorted by PnL desc) + остальные (sorted by DD asc)
    good = rdf[rdf['max_dd'] < 12].sort_values('pnl', ascending=False)
    rest = rdf[rdf['max_dd'] >= 12].sort_values('max_dd')
    for _, row in pd.concat([good, rest]).iterrows():
        ay = 'YES' if row['all_pos'] else 'NO '
        dd_s = f"<10%✓" if row['max_dd'] < 10 else f"{row['max_dd']:.2f}%"
        print(f"  {row['label']:<14} {row['tp']:>3}R ${row['risk']:>4}  "
              f"{row['n']:>5}  {row['wr']:>5.1%}  ${row['pnl']:>8,.0f}  "
              f"{dd_s:>8}  {row['max_daily_dd']:>6.2f}%  {ay}")

    # ── Достигшие цели ────────────────────────────────────────────────────────
    print()
    print("=" * 80)
    targets = rdf[(rdf['max_dd'] < 10) & (rdf['max_daily_dd'] < 5) & rdf['all_pos']]
    if len(targets) > 0:
        print("*** ЦЕЛЬ ДОСТИГНУТА (DD<10%, DlyDD<5%, все годы в плюс) ***")
        for _, row in targets.sort_values('pnl', ascending=False).iterrows():
            print(f"  {row['label']:<12} TP={row['tp']}R Risk=${row['risk']}  "
                  f"N={row['n']}  WR={row['wr']:.1%}  PnL=${row['pnl']:,.0f}  "
                  f"MaxDD={row['max_dd']:.2f}%  DlyDD={row['max_daily_dd']:.2f}%")
    else:
        print("Цель (DD<10%) не достигнута. Лучшие по DD:")
        for _, row in rdf.sort_values('max_dd').head(5).iterrows():
            print(f"  {row['label']:<12} TP={row['tp']}R Risk=${row['risk']}  "
                  f"N={row['n']}  WR={row['wr']:.1%}  PnL=${row['pnl']:,.0f}  "
                  f"MaxDD={row['max_dd']:.2f}%  DlyDD={row['max_daily_dd']:.2f}%")

    # ── Детальный вывод по лучшей комбинации ─────────────────────────────────
    best_pnl = rdf[rdf['max_dd'] < 12].sort_values('pnl', ascending=False)
    if len(best_pnl) > 0:
        row = best_pnl.iloc[0]
        print()
        print("=" * 80)
        print(f"ДЕТАЛЬНО — лучший при DD<12%:  {row['label']}  TP={row['tp']}R  Risk=${row['risk']}")
        print(f"  Trades={row['n']}  WR={row['wr']:.1%}  PnL=${row['pnl']:,.0f}"
              f"  MaxDD={row['max_dd']:.2f}%  DlyDD={row['max_daily_dd']:.2f}%")
        print(f"  PnL по годам:")
        for yr in sorted(row['yearly'].keys()):
            v = row['yearly'][yr]
            print(f"    {yr}: ${v:>7,.0f}  {'✓' if v > 0 else '✗'}")
    print("=" * 80)


if __name__ == "__main__":
    main()
