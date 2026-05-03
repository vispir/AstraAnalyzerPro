"""
Grid search: LONG Session Breakout — TP 7..12R
Полный ступенчатый трейлинг: при N*R → лок (N-1)*R, ..., при 2R → лок 1R
Trailing триггер по HIGH бара (правильно для LONG).
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
import numpy as np
from pathlib import Path
import time

# ── Фиксированные параметры ───────────────────────────────────────────────────
RISK          = 100.0
ATR_BUFFER    = 0.5
ATR_PERIOD    = 14
H4_EMA_PERIOD = 20
SLOPE_N       = 5
MIN_H4_BARS   = 22
K_EMA         = 2.0 / (H4_EMA_PERIOD + 1.0)
H4_NS         = int(4 * 3600 * 1e9)

SESSIONS = {
    'asian':  {'range_hours': (7,  10), 'entry_start': 10, 'entry_end': 24},
    'london': {'range_hours': (13, 16), 'entry_start': 16, 'entry_end': 24},
    'ny':     {'range_hours': (13, 17), 'entry_start': 18, 'entry_end': 21},
}

# ── Grid ──────────────────────────────────────────────────────────────────────
TP_VALUES        = [7, 8, 9, 10, 11, 12]
TRAIL_MODES      = ['high', 'low']   # high = правильный LONG; low = консервативный (оригинал)


def make_trail_steps(tp_rr: int) -> list:
    """(trigger, lock): при достижении trigger*R → лок на (trigger-1)*R."""
    return [(i, i - 1) for i in range(tp_rr - 1, 1, -1)]


# ── Индикаторы ────────────────────────────────────────────────────────────────
def _atr(df: pd.DataFrame, period: int) -> pd.Series:
    h, l, c = df['high'], df['low'], df['close']
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()


def _ema(df: pd.DataFrame, period: int) -> pd.Series:
    return df['close'].ewm(span=period, min_periods=period, adjust=False).mean()


# ── Трейлинг ─────────────────────────────────────────────────────────────────
def _trail(t: dict, price: float) -> None:
    """price = high (HIGH-режим) или low (LOW-режим)."""
    risk = t['entry'] - t['initial_sl']
    rr   = (price - t['entry']) / risk
    for trigger, lock in t['steps']:
        if rr >= trigger:
            t['sl'] = max(t['sl'], t['entry'] + lock * risk)
            break


# ── Бэктест ───────────────────────────────────────────────────────────────────
def run(df: pd.DataFrame, h4_times: np.ndarray, h4_ema20: np.ndarray,
        tp_rr: int, trail_mode: str = 'high') -> dict:
    trail_steps = make_trail_steps(tp_rr)
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
    active_long  = {}
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

        if ptr_closed < MIN_H4_BARS - 1:
            for sn, p in SESSIONS.items():
                sh, eh = p['range_hours']
                if sh <= hour < eh:
                    s_highs[sn] = max(s_highs.get(sn, 0),  high)
                    s_lows[sn]  = min(s_lows.get(sn, 1e9), low)
            continue

        h4p = int(ts_ns // int(14400 * 1e9)) * int(14400 * 1e9)
        if h4p != forming_period:
            forming_period = h4p
            forming_close  = close
            ema_base = h4_ema20[ptr_closed] if ptr_closed >= 0 else np.nan
        else:
            forming_close = close

        if np.isnan(ema_base):
            continue

        h4_ema   = forming_close * K_EMA + ema_base * (1.0 - K_EMA)
        ema_ok   = forming_close > ema_base
        slope_ok = (ptr_closed >= SLOPE_N) \
                   and not np.isnan(h4_ema20[ptr_closed - SLOPE_N]) \
                   and (h4_ema > h4_ema20[ptr_closed - SLOPE_N])

        # управление открытыми сделками — всегда
        trail_price = high if trail_mode == 'high' else low
        for sn in list(active_long.keys()):
            t = active_long[sn]
            _trail(t, trail_price)
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

        # диапазоны сессий — всегда
        for sn, p in SESSIONS.items():
            sh, eh = p['range_hours']
            if sh <= hour < eh:
                s_highs[sn] = max(s_highs.get(sn, 0),  high)
                s_lows[sn]  = min(s_lows.get(sn, 1e9), low)

        if not (ema_ok and slope_ok):
            continue

        for sn, p in SESSIONS.items():
            if sn not in s_highs or sn in active_long:
                continue
            if not (p['entry_start'] <= hour < p['entry_end']):
                continue
            if close > s_highs[sn]:
                sl  = s_lows[sn] - ATR_BUFFER * atr
                rsk = close - sl
                if rsk <= 0: continue
                active_long[sn] = {
                    'entry': close, 'sl': sl, 'initial_sl': sl,
                    'tp': close + rsk * tp_rr,
                    'size': RISK / rsk,
                    'session': sn,
                    'steps': trail_steps,
                }

    if prev_date is not None and day_start > 0:
        dd = (day_start - balance) / day_start * 100
        if dd > max_daily_dd: max_daily_dd = dd

    last_close = float(m15[-1, i_c])
    last_year  = int(df.index[-1].year)
    for sn, t in active_long.items():
        t['pnl'] = (last_close - t['entry']) * t['size']
        balance += t['pnl']; t['year'] = last_year; trades.append(t)

    tdf    = pd.DataFrame(trades) if trades else pd.DataFrame(columns=['pnl', 'year'])
    pnl    = balance - 10_000
    n      = len(tdf)
    wr     = (tdf['pnl'] > 0).sum() / n if n > 0 else 0.0
    yearly = tdf.groupby('year')['pnl'].sum() if n > 0 else pd.Series(dtype=float)

    return {
        'tp': tp_rr, 'n': n, 'wr': wr, 'pnl': pnl,
        'max_dd': max_dd, 'max_daily_dd': max_daily_dd,
        'yearly': yearly,
        'all_pos': bool(all(yearly > 0)) if len(yearly) > 0 else False,
    }


def main():
    data_path = (
        Path(__file__).parent.parent
        / "data_cache" / "dukascopy" / "m15" / "XAUUSD"
        / "xauusd_m15_2020-01-01_2026-04-18.parquet"
    )

    print("=" * 72)
    print("LONG Session Breakout — TP 7..12R, полный ступенчатый трейлинг")
    print(f"Risk=${RISK:.0f}  ATRbuf={ATR_BUFFER}  Slope={SLOPE_N}  EMA{H4_EMA_PERIOD}")
    print("Trailing: HIGH-trigger, shag kazhdyy R (N-1->N-2, ..., 2->1)")
    print("=" * 72)

    df = pd.read_parquet(data_path).sort_index()
    df['atr'] = _atr(df, ATR_PERIOD)

    df_h4 = df.resample('4h').agg(
        {'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last'}
    ).dropna()
    df_h4['ema20'] = _ema(df_h4, H4_EMA_PERIOD)
    h4_times = df_h4.index.asi8
    h4_ema20 = df_h4['ema20'].to_numpy()

    print(f"Данные: {df.index[0].date()} — {df.index[-1].date()}  ({len(df):,} M15 баров)\n")

    all_results = {}
    t0_all = time.time()

    for mode in TRAIL_MODES:
        label = 'HIGH-trigger' if mode == 'high' else 'LOW-trigger (orig)'
        print(f"\n--- Trailing: {label} ---")
        results = []
        for tp in TP_VALUES:
            t0 = time.time()
            r = run(df, h4_times, h4_ema20, tp, trail_mode=mode)
            r['mode'] = mode
            elapsed = time.time() - t0
            results.append(r)
            allyr = 'YES' if r['all_pos'] else 'NO '
            print(f"  TP={tp:2d}R  N={r['n']:4d}  WR={r['wr']:.1%}  PnL=${r['pnl']:>8,.0f}  "
                  f"MaxDD={r['max_dd']:.2f}%  DlyDD={r['max_daily_dd']:.2f}%  AllYrs={allyr}  [{elapsed:.0f}s]")
        all_results[mode] = results

    total = time.time() - t0_all
    print(f"\nGotovo za {total:.0f} sek.\n")

    # ── Итог по каждому режиму ────────────────────────────────────────────────
    for mode in TRAIL_MODES:
        results = all_results[mode]
        label = 'HIGH-trigger' if mode == 'high' else 'LOW-trigger (orig)'
        print("=" * 72)
        print(f"ITOG [{label}]  filter: MaxDD<10%, DlyDD<5%, AllYrs=YES")
        print("=" * 72)
        passed = [r for r in results if r['max_dd'] < 10.0 and r['max_daily_dd'] < 5.0 and r['all_pos']]
        src = passed if passed else results
        for r in sorted(src, key=lambda x: -x['pnl'])[:6]:
            star = ' ***' if r in passed else ''
            print(f"  {r['tp']:2d}R  N={r['n']:4d}  WR={r['wr']:.1%}  PnL=${r['pnl']:>8,.0f}  "
                  f"MaxDD={r['max_dd']:.2f}%  DlyDD={r['max_daily_dd']:.2f}%  "
                  f"AllYrs={'YES' if r['all_pos'] else 'NO'}{star}")

        all_years = sorted(set(yr for r in results for yr in r['yearly'].index))
        print()
        yr_cols = "  ".join(f"{yr}" for yr in all_years)
        print(f"  {'TP':>4}  {'AllY':<4}  {yr_cols}")
        print("  " + "-" * (6 + 5 + len(yr_cols) + 2 * len(all_years)))
        for r in results:
            yr_vals = "  ".join(f"{r['yearly'].get(yr, 0):>6,.0f}" for yr in all_years)
            allyr = 'YES' if r['all_pos'] else 'NO '
            print(f"  {r['tp']:2d}R  {allyr}  {yr_vals}")
        print()

    # ── Прямое сравнение HIGH vs LOW ──────────────────────────────────────────
    print("=" * 72)
    print("SRAVNENIE: HIGH vs LOW trigger")
    print("=" * 72)
    print(f"  {'TP':>4}  {'PnL(HIGH)':>10}  {'PnL(LOW)':>10}  {'delta':>8}  {'DD(HIGH)':>9}  {'DD(LOW)':>8}")
    print("  " + "-" * 62)
    for tp in TP_VALUES:
        rh = next(r for r in all_results['high'] if r['tp'] == tp)
        rl = next(r for r in all_results['low']  if r['tp'] == tp)
        delta = rh['pnl'] - rl['pnl']
        sign  = '+' if delta >= 0 else ''
        print(f"  {tp:2d}R  ${rh['pnl']:>9,.0f}  ${rl['pnl']:>9,.0f}  {sign}${delta:>7,.0f}  "
              f"{rh['max_dd']:8.2f}%  {rl['max_dd']:7.2f}%")


if __name__ == "__main__":
    main()
