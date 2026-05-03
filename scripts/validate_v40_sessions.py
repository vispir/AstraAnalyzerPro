"""
Анализ сессий: какая сессия что даёт.

Тест: каждая сессия отдельно + все пары + все три.
Фильтры: ea_mirror EMA (baseline) + slope5 (лучший из предыдущих тестов).
TP: 3R, 4R, 5R   Risk: $100, $120
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

SESSION_COMBOS = {
    'asian':          ['asian'],
    'london':         ['london'],
    'ny':             ['ny'],
    'asian+london':   ['asian', 'london'],
    'asian+ny':       ['asian', 'ny'],
    'london+ny':      ['london', 'ny'],
    'all':            ['asian', 'london', 'ny'],
}


def floor4h_ns(ts_ns):
    return int(ts_ns // int(14400 * 1e9)) * int(14400 * 1e9)


def apply_step_trailing(t, low, high):
    risk = t['entry'] - t['initial_sl']
    rr   = (low - t['entry']) / risk
    for trigger, lock in ((5, 4), (4, 3), (3, 2), (2, 1)):
        if rr >= trigger:
            t['sl'] = max(t['sl'], t['entry'] + lock * risk)
            break


def run(df, h4_times, h4_ema20, active_sessions, slope_n, tp_rr, risk):
    sessions = {sn: p for sn, p in LONG_SESSIONS.items() if sn in active_sessions}

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
            for sn, p in sessions.items():
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

        # Фильтры — только флаги, без continue
        ema_ok = (forming_close > ema_base)
        slope_ok = True
        if slope_n > 0:
            if ptr_closed < slope_n:
                slope_ok = False
            else:
                ema_ago  = h4_ema20[ptr_closed - slope_n]
                slope_ok = (not np.isnan(ema_ago)) and (h4_ema > ema_ago)

        # Управление открытыми сделками — ВСЕГДА
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

        # Обновление диапазонов — ВСЕГДА
        for sn, p in sessions.items():
            sh, eh = p['range_hours']
            if sh <= hour < eh:
                session_highs[sn] = max(session_highs.get(sn, 0), high)
                session_lows[sn]  = min(session_lows.get(sn, 1e9), low)

        if not (ema_ok and slope_ok):
            continue

        # Входы
        for sn, p in sessions.items():
            if sn not in session_highs or sn in active_long:
                continue
            if not (p['entry_start'] <= hour < p['entry_end']):
                continue
            if close > session_highs[sn]:
                sl  = session_lows[sn] - ATR_BUFFER * atr
                rsk = close - sl
                if rsk <= 0: continue
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

    tdf    = pd.DataFrame(trades) if trades else pd.DataFrame(columns=['pnl','session','year'])
    pnl    = balance - 10_000
    n      = len(tdf)
    wr     = (tdf['pnl'] > 0).sum() / n if n > 0 else 0.0
    yearly = tdf.groupby('year')['pnl'].sum() if n > 0 else pd.Series(dtype=float)

    # По сессиям
    by_sn = {}
    if n > 0 and 'session' in tdf.columns:
        for sn in active_sessions:
            sub = tdf[tdf['session'] == sn]
            by_sn[sn] = {
                'n':   len(sub),
                'wr':  (sub['pnl'] > 0).sum() / len(sub) if len(sub) > 0 else 0,
                'pnl': float(sub['pnl'].sum()) if len(sub) > 0 else 0,
            }

    return {
        'n': n, 'wr': wr, 'pnl': pnl,
        'max_dd': max_dd, 'max_daily_dd': max_daily_dd,
        'yearly': yearly,
        'all_pos': all(yearly > 0) if len(yearly) > 0 else False,
        'by_sn': by_sn,
    }


def main():
    print("=" * 72)
    print("АНАЛИЗ СЕССИЙ — ea_mirror LONG only")
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

    # Конфигурации: (slope_n, tp_rr, risk)
    configs = [
        (0, 3, 120),   # baseline
        (0, 5, 120),   # baseline + TP=5R
        (5, 5, 100),   # slope5 + TP=5R + Risk=$100 (лучший DD)
        (5, 5, 120),   # slope5 + TP=5R + Risk=$120 (лучший PnL)
    ]
    cfg_labels = [
        'no-slope TP=3R $120',
        'no-slope TP=5R $120',
        'slope5   TP=5R $100',
        'slope5   TP=5R $120',
    ]

    all_results = []
    total = len(SESSION_COMBOS) * len(configs)
    idx = 0

    for combo_name, sess_list in SESSION_COMBOS.items():
        for (slope_n, tp_rr, risk), cfg_lbl in zip(configs, cfg_labels):
            idx += 1
            print(f"  [{idx:2d}/{total}] {combo_name:<14} {cfg_lbl}...", end='', flush=True)
            r = run(df, h4_times, h4_ema20, sess_list, slope_n, tp_rr, risk)
            all_results.append({
                'combo': combo_name, 'sessions': '+'.join(sess_list),
                'slope': slope_n, 'tp': tp_rr, 'risk': risk,
                'cfg': cfg_lbl, **r
            })
            hit = ''
            if r['max_dd'] < 10 and r['all_pos']: hit = '  *** DD<10% ***'
            elif r['max_dd'] < 10: hit = '  [DD<10%]'
            print(f"  {r['n']:>4} tr  WR={r['wr']:.1%}  PnL=${r['pnl']:,.0f}"
                  f"  DD={r['max_dd']:.2f}%{hit}")

    rdf = pd.DataFrame(all_results)

    # ── Детальная статистика одиночных сессий ────────────────────────────────
    print()
    print("=" * 72)
    print("ОДИНОЧНЫЕ СЕССИИ — baseline (no-slope, TP=3R, Risk=$120)")
    print("=" * 72)
    singles = rdf[(rdf['slope'] == 0) & (rdf['tp'] == 3) & (rdf['risk'] == 120) &
                  rdf['combo'].isin(['asian', 'london', 'ny'])]
    for _, row in singles.iterrows():
        print(f"\n  [{row['combo'].upper()}]")
        print(f"  Trades={row['n']}  WR={row['wr']:.1%}  PnL=${row['pnl']:,.0f}"
              f"  MaxDD={row['max_dd']:.2f}%  DlyDD={row['max_daily_dd']:.2f}%"
              f"  AllYrs={'YES' if row['all_pos'] else 'NO'}")
        sn_stats = row['by_sn']
        for sn, st in sn_stats.items():
            print(f"    {sn}: {st['n']} trades  WR={st['wr']:.1%}  PnL=${st['pnl']:,.0f}")
        print(f"  PnL по годам:", end='')
        for yr in sorted(row['yearly'].keys()):
            v = row['yearly'][yr]
            print(f"  {yr}:${v:,.0f}{'✓' if v>0 else '✗'}", end='')
        print()

    # ── Главная сводная таблица ───────────────────────────────────────────────
    for cfg_lbl in cfg_labels:
        sub = rdf[rdf['cfg'] == cfg_lbl].sort_values('pnl', ascending=False)
        slope_n = sub.iloc[0]['slope']
        tp_rr   = sub.iloc[0]['tp']
        risk    = sub.iloc[0]['risk']

        print()
        print("=" * 72)
        print(f"КОНФИГУРАЦИЯ: {cfg_lbl}")
        print("=" * 72)
        hdr = f"  {'Сессии':<16} {'N':>5}  {'WR':>6}  {'PnL':>9}  {'MaxDD':>8}  {'DlyDD':>7}  {'AllYrs'}"
        print(hdr)
        print("  " + "-" * 65)
        for _, row in sub.iterrows():
            ay  = 'YES' if row['all_pos'] else 'NO '
            dds = '<10%✓' if row['max_dd'] < 10 else f"{row['max_dd']:.2f}%"
            mark = ' ***' if row['max_dd'] < 10 and row['all_pos'] else ''
            print(f"  {row['combo']:<16} {row['n']:>5}  {row['wr']:>5.1%}"
                  f"  ${row['pnl']:>8,.0f}  {dds:>8}  {row['max_daily_dd']:>6.2f}%  {ay}{mark}")

    # ── Все комбинации достигшие DD<10% ──────────────────────────────────────
    print()
    print("=" * 72)
    targets = rdf[(rdf['max_dd'] < 10) & (rdf['max_daily_dd'] < 5) & rdf['all_pos']]
    if len(targets) > 0:
        print("*** ЦЕЛЬ ДОСТИГНУТА (DD<10%, DlyDD<5%, все годы в плюс) ***")
        for _, row in targets.sort_values('pnl', ascending=False).iterrows():
            print(f"  {row['combo']:<14} {row['cfg']}  "
                  f"N={row['n']}  WR={row['wr']:.1%}  PnL=${row['pnl']:,.0f}  "
                  f"MaxDD={row['max_dd']:.2f}%  DlyDD={row['max_daily_dd']:.2f}%")
    else:
        print("Цель не достигнута. Лучшие по DD:")
        for _, row in rdf.sort_values('max_dd').head(8).iterrows():
            print(f"  {row['combo']:<14} {row['cfg']}  "
                  f"N={row['n']}  WR={row['wr']:.1%}  PnL=${row['pnl']:,.0f}  "
                  f"MaxDD={row['max_dd']:.2f}%")
    print("=" * 72)


if __name__ == "__main__":
    main()
