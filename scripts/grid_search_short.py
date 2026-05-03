"""
FULL GRID SEARCH — SHORT параметры (зеркало LONG)

Концепция SHORT:
  - Те же сессии (asian/london/ny), те же окна диапазона
  - Вход: close < session_low  (пробой минимума вниз)
  - SL:   session_high + ATR_buffer * ATR
  - TP:   entry - risk * tp_rr
  - EMA фильтр: forming_close < ema_base  (медвежий H4 bias)
  - Slope фильтр: h4_ema < h4_ema20[ptr - N]  (EMA снижается)

Grid 864 комбинации:
  tp_rr       : [3, 4, 5, 5.5, 6, 7]
  trailing    : [True, False]
  asian_end   : [14, 16, 18, 24]   -- до какого часа ищем Short по Asian
  atr_buffer  : [0.3, 0.5, 1.0]
  ema_slope   : [0, 3, 5]          -- 0=нет slope фильтра
  use_h4_ema  : [True, False]      -- требовать close < ema_base

Risk = $100 фиксировано
"""
import sys, os, time, itertools
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from session_breakout_trader import (
    ATR_PERIOD, H4_EMA_PERIOD,
    calculate_atr, calculate_ema,
)
import pandas as pd
import numpy as np
from pathlib import Path

# ── константы ─────────────────────────────────────────────────────────────────
K_EMA  = 2.0 / (H4_EMA_PERIOD + 1.0)
H4_NS  = int(4 * 3600 * 1e9)
MIN_H4 = 22
RISK   = 100.0

# Сессии: range_hours — когда собираем H/L; entry_start/end — базовое окно входа
SESSIONS = {
    'asian':  {'range_hours': (7,  10), 'entry_start': 10},
    'london': {'range_hours': (13, 16), 'entry_start': 16, 'entry_end': 24},
    'ny':     {'range_hours': (13, 17), 'entry_start': 18, 'entry_end': 21},
}


def floor4h_ns(ts_ns):
    return int(ts_ns // int(14400 * 1e9)) * int(14400 * 1e9)


def apply_trailing_short(t, high):
    """Step trailing для SHORT: lock SL при 2/3/4/5R прибыли."""
    risk = t['initial_sl'] - t['entry']
    rr   = (t['entry'] - high) / risk
    for trigger, lock in ((5, 4), (4, 3), (3, 2), (2, 1)):
        if rr >= trigger:
            t['sl'] = min(t['sl'], t['entry'] - lock * risk)
            break


def run(df, h4_times, h4_ema20,
        tp_rr, trailing, asian_end, atr_buffer, ema_slope_n, use_h4_ema):
    """
    Один прогон SHORT стратегии.

    No look-ahead:
      ptr_closed продвигается когда h4_times[ptr+1] + H4_NS <= ts_ns
      ema_base  = h4_ema20[ptr_closed]  (только закрытые бары)
      forming_close = текущий M15 close
      h4_ema = forming_close * K + ema_base * (1-K)
    """
    n_h4     = len(h4_times)
    times_ns = df.index.asi8
    m15      = df.to_numpy()
    col      = {c: i for i, c in enumerate(df.columns)}
    i_h = col['high']; i_l = col['low']; i_c = col['close']; i_a = col['atr']

    ptr_closed     = -1
    forming_period = -1
    forming_close  = np.nan
    ema_base       = np.nan

    trades       = []
    active_short = {}          # sn -> trade dict
    balance      = 10_000.0
    peak         = 10_000.0
    max_dd       = 0.0
    max_daily_dd = 0.0
    prev_date    = None
    day_start    = balance
    s_highs      = {}
    s_lows       = {}

    for i in range(len(df)):
        ts_ns  = int(times_ns[i])
        cur_ts = df.index[i]
        high   = float(m15[i, i_h])
        low    = float(m15[i, i_l])
        close  = float(m15[i, i_c])
        atr    = float(m15[i, i_a])
        hour   = cur_ts.hour

        if np.isnan(atr):
            continue

        cur_date = cur_ts.date()
        if cur_date != prev_date:
            if prev_date is not None and day_start > 0:
                dd = (day_start - balance) / day_start * 100
                if dd > max_daily_dd: max_daily_dd = dd
            day_start = balance
            prev_date = cur_date
            s_highs   = {}
            s_lows    = {}

        while ptr_closed + 1 < n_h4 and h4_times[ptr_closed + 1] + H4_NS <= ts_ns:
            ptr_closed += 1

        if ptr_closed < MIN_H4 - 1:
            for sn, p in SESSIONS.items():
                sh, eh = p['range_hours']
                if sh <= hour < eh:
                    s_highs[sn] = max(s_highs.get(sn, 0),   high)
                    s_lows[sn]  = min(s_lows.get(sn, 1e9),  low)
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

        # ── фильтры SHORT (флаги) ─────────────────────────────────────────────
        ema_ok = (forming_close < ema_base) if use_h4_ema else True

        slope_ok = True
        if ema_slope_n > 0:
            if ptr_closed < ema_slope_n:
                slope_ok = False
            else:
                ema_ago  = h4_ema20[ptr_closed - ema_slope_n]
                slope_ok = (not np.isnan(ema_ago)) and (h4_ema < ema_ago)

        # ── управление SHORT сделками — ВСЕГДА ───────────────────────────────
        for sn in list(active_short.keys()):
            t = active_short[sn]
            if trailing:
                apply_trailing_short(t, high)
            if high >= t['sl']:
                t['pnl'] = (t['entry'] - t['sl']) * t['size']
                balance += t['pnl']; t['year'] = cur_ts.year
                trades.append(t); del active_short[sn]
            elif low <= t['tp']:
                t['pnl'] = (t['entry'] - t['tp']) * t['size']
                balance += t['pnl']; t['year'] = cur_ts.year
                trades.append(t); del active_short[sn]

        if balance > peak: peak = balance
        dd = (peak - balance) / peak * 100
        if dd > max_dd: max_dd = dd

        # ── диапазоны сессий — ВСЕГДА ─────────────────────────────────────────
        for sn, p in SESSIONS.items():
            sh, eh = p['range_hours']
            if sh <= hour < eh:
                s_highs[sn] = max(s_highs.get(sn, 0),   high)
                s_lows[sn]  = min(s_lows.get(sn, 1e9),  low)

        # ── новые SHORT входы — только если фильтры прошли ───────────────────
        if not (ema_ok and slope_ok):
            continue

        for sn, p in SESSIONS.items():
            if sn not in s_lows or sn in active_short:
                continue

            # окно входа
            es = p['entry_start']
            ee = asian_end if sn == 'asian' else p['entry_end']
            if not (es <= hour < ee):
                continue

            if close < s_lows[sn]:
                sl  = s_highs[sn] + atr_buffer * atr
                rsk = sl - close
                if rsk <= 0: continue
                active_short[sn] = {
                    'entry': close, 'sl': sl, 'initial_sl': sl,
                    'tp': close - rsk * tp_rr,
                    'size': RISK / rsk,
                    'direction': 'SHORT', 'session': sn,
                }

    if prev_date is not None and day_start > 0:
        dd = (day_start - balance) / day_start * 100
        if dd > max_daily_dd: max_daily_dd = dd

    last_close = float(m15[-1, i_c])
    last_year  = int(df.index[-1].year)
    for sn, t in active_short.items():
        t['pnl'] = (t['entry'] - last_close) * t['size']
        balance += t['pnl']; t['year'] = last_year; trades.append(t)

    tdf    = pd.DataFrame(trades) if trades else pd.DataFrame(columns=['pnl', 'year'])
    pnl    = balance - 10_000
    n      = len(tdf)
    wr     = (tdf['pnl'] > 0).sum() / n if n > 0 else 0.0
    yearly = tdf.groupby('year')['pnl'].sum() if n > 0 else pd.Series(dtype=float)

    return {
        'n': n, 'wr': wr, 'pnl': pnl,
        'max_dd': max_dd, 'max_daily_dd': max_daily_dd,
        'yearly': yearly,
        'all_pos': bool(all(yearly > 0)) if len(yearly) > 0 else False,
    }


def main():
    print("=" * 72)
    print("FULL GRID SEARCH -- SHORT (зеркало LONG)  Risk=$100 фиксировано")
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

    # ── Grid параметры ────────────────────────────────────────────────────────
    param_grid = {
        'tp_rr':      [3, 4, 5, 5.5, 6, 7],
        'trailing':   [True, False],
        'asian_end':  [14, 16, 18, 24],
        'atr_buffer': [0.3, 0.5, 1.0],
        'ema_slope_n': [0, 3, 5],
        'use_h4_ema': [True, False],
    }

    combos = list(itertools.product(*param_grid.values()))
    keys   = list(param_grid.keys())
    total  = len(combos)

    print(f"Всего комбинаций: {total}")

    results   = []
    t0        = time.time()
    best_pnl  = -1e9
    best_row  = None

    for idx, vals in enumerate(combos, 1):
        params = dict(zip(keys, vals))
        r = run(df, h4_times, h4_ema20, **params)

        row = {**params, **r}
        results.append(row)

        # Обновить лучший (DD<10%, все годы, DlyDD<5%)
        if r['max_dd'] < 10 and r['max_daily_dd'] < 5 and r['all_pos']:
            if r['pnl'] > best_pnl:
                best_pnl = r['pnl']
                best_row = row

        if idx % 50 == 0 or idx == total:
            elapsed = time.time() - t0
            eta     = elapsed / idx * (total - idx)
            best_s  = f"  best_DD<10%=${best_pnl:,.0f}" if best_pnl > -1e9 else ""
            print(f"  [{idx:4d}/{total}]  elapsed={elapsed:.0f}s  ETA={eta:.0f}s{best_s}")

    elapsed = time.time() - t0
    print(f"\nГотово за {elapsed:.0f} сек.\n")

    rdf = pd.DataFrame(results)

    # ── Baseline: текущий EA параметры ───────────────────────────────────────
    base = run(df, h4_times, h4_ema20,
               tp_rr=5.5, trailing=True, asian_end=24,
               atr_buffer=0.5, ema_slope_n=0, use_h4_ema=True)
    print("=" * 72)
    print(f"BASELINE (TP=5.5R, trail=Y, asEnd=24, ATRbuf=0.5, slope=0, H4=Y):")
    print(f"  N={base['n']}  WR={base['wr']:.1%}  PnL=${base['pnl']:,.0f}"
          f"  MaxDD={base['max_dd']:.2f}%  DlyDD={base['max_daily_dd']:.2f}%"
          f"  AllYrs={'YES' if base['all_pos'] else 'NO'}")

    # ── ТОП-20 по PnL (DD<10%, DlyDD<5%, все годы) ───────────────────────────
    good = rdf[(rdf['max_dd'] < 10) & (rdf['max_daily_dd'] < 5) & rdf['all_pos']]
    print()
    print("=" * 72)
    print(f"ТОП-20 по PnL  (фильтр: DD<10%, DlyDD<5%, все годы в плюс)  [{len(good)} комб.]")
    print("=" * 72)
    print(f"     TP  Trl  AsEnd  ATRbuf  Slope   H4      N      WR        PnL    MaxDD   DlyDD  AllY")
    print("  " + "-" * 84)

    top20 = good.sort_values('pnl', ascending=False).head(20)
    for _, row in top20.iterrows():
        ay = 'YES' if row['all_pos'] else 'NO '
        print(f"  {row['tp_rr']:>5.1f}  {'Y' if row['trailing'] else 'N':>3}"
              f"  {row['asian_end']:>5.0f}  {row['atr_buffer']:>6.1f}"
              f"  {row['ema_slope_n']:>5.0f}  {'Y' if row['use_h4_ema'] else 'N':>4}"
              f"  {row['n']:>5.0f}  {row['wr']:>5.1%}  ${row['pnl']:>8,.0f}"
              f"  {row['max_dd']:>6.2f}%  {row['max_daily_dd']:>5.2f}%  {ay}")

    if len(good) == 0:
        print("  (нет комбинаций с DD<10%, DlyDD<5%, все годы в плюс)")
        print()
        print("  Лучшие по DD (без фильтра по годам):")
        best_dd = rdf.sort_values('max_dd').head(20)
        for _, row in best_dd.iterrows():
            ay = 'YES' if row['all_pos'] else 'NO '
            print(f"  {row['tp_rr']:>5.1f}  {'Y' if row['trailing'] else 'N':>3}"
                  f"  {row['asian_end']:>5.0f}  {row['atr_buffer']:>6.1f}"
                  f"  {row['ema_slope_n']:>5.0f}  {'Y' if row['use_h4_ema'] else 'N':>4}"
                  f"  {row['n']:>5.0f}  {row['wr']:>5.1%}  ${row['pnl']:>8,.0f}"
                  f"  {row['max_dd']:>6.2f}%  {row['max_daily_dd']:>5.2f}%  {ay}")

    # ── Влияние каждого параметра ─────────────────────────────────────────────
    print()
    print("=" * 72)
    print("ВЛИЯНИЕ КАЖДОГО ПАРАМЕТРА  (медиана PnL и DD по группам)")
    print("=" * 72)
    for param in keys:
        print(f"\n  {param}:")
        for val, grp in rdf.groupby(param):
            good_cnt = ((grp['max_dd'] < 10) & (grp['max_daily_dd'] < 5) & grp['all_pos']).sum()
            print(f"    {str(val):>8}:  PnL_med=${grp['pnl'].median():>8,.0f}"
                  f"  DD_med={grp['max_dd'].median():>5.2f}%"
                  f"  PnL_max=${grp['pnl'].max():>8,.0f}"
                  f"  DD<10%: {good_cnt} комб.")

    # ── Детально лучший ───────────────────────────────────────────────────────
    pool = good if len(good) > 0 else rdf.sort_values('max_dd')
    best = pool.sort_values('pnl', ascending=False).iloc[0]

    print()
    print("=" * 72)
    print("ДЕТАЛЬНО — лучший (DD<10% + все годы в плюс, макс PnL):")
    print(f"  TP={best['tp_rr']}R  Trailing={'Y' if best['trailing'] else 'N'}"
          f"  AsianEnd={best['asian_end']:.0f}  ATRbuf={best['atr_buffer']}"
          f"  Slope={best['ema_slope_n']:.0f}  H4EMA={'Y' if best['use_h4_ema'] else 'N'}")
    print(f"  N={best['n']:.0f}  WR={best['wr']:.1%}  PnL=${best['pnl']:,.0f}"
          f"  MaxDD={best['max_dd']:.2f}%  DlyDD={best['max_daily_dd']:.2f}%")

    # Прогнать лучшего для годового PnL
    r_best = run(df, h4_times, h4_ema20,
                 tp_rr=float(best['tp_rr']),
                 trailing=bool(best['trailing']),
                 asian_end=int(best['asian_end']),
                 atr_buffer=float(best['atr_buffer']),
                 ema_slope_n=int(best['ema_slope_n']),
                 use_h4_ema=bool(best['use_h4_ema']))
    print(f"  PnL по годам:")
    for yr in sorted(r_best['yearly'].index):
        v = r_best['yearly'][yr]
        print(f"    {yr}: ${v:>8,.0f}  {'OK' if v > 0 else 'LOSS'}")
    print("=" * 72)


if __name__ == "__main__":
    main()
