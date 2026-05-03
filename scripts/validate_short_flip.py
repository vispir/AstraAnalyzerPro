"""
SHORT after LONG SL (failed breakout flip).

Когда LONG закрывается по SL (пробой сессии провалился) —
сразу открываем SHORT в той же сессии:
  entry = цена закрытия LONG по SL
  sl    = LONG entry + ATR_buffer * ATR  (уровень провала + буфер)
  tp    = entry - risk * tp_rr_short

LONG параметры фиксированы (лучшие из grid_search_full.py):
  slope=5, TP=7R, ATRbuf=0.5, Risk=$100

Grid SHORT параметров:
  tp_rr_short  : [2, 3, 4, 5, 7]
  risk_short   : [50, 75, 100]
  trailing     : [True, False]
  atr_buf_short: [0.3, 0.5, 1.0]   -- SHORT SL = long_entry + atr_buf_short * ATR
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

K_EMA  = 2.0 / (H4_EMA_PERIOD + 1.0)
H4_NS  = int(4 * 3600 * 1e9)
MIN_H4 = 22

LONG_SLOPE_N  = 5
LONG_TP_RR    = 7.0
LONG_ATR_BUF  = 0.5
LONG_RISK     = 100.0

SESSIONS = {
    'asian':  {'range_hours': (7,  10), 'entry_start': 10, 'entry_end': 24},
    'london': {'range_hours': (13, 16), 'entry_start': 16, 'entry_end': 24},
    'ny':     {'range_hours': (13, 17), 'entry_start': 18, 'entry_end': 21},
}


def floor4h_ns(ts_ns):
    return int(ts_ns // int(14400 * 1e9)) * int(14400 * 1e9)


def trail_long(t, low):
    risk = t['entry'] - t['initial_sl']
    rr   = (low - t['entry']) / risk
    for trigger, lock in ((5, 4), (4, 3), (3, 2), (2, 1)):
        if rr >= trigger:
            t['sl'] = max(t['sl'], t['entry'] + lock * risk)
            break


def trail_short(t, high):
    risk = t['initial_sl'] - t['entry']
    rr   = (t['entry'] - high) / risk
    for trigger, lock in ((5, 4), (4, 3), (3, 2), (2, 1)):
        if rr >= trigger:
            t['sl'] = min(t['sl'], t['entry'] - lock * risk)
            break


def run(df, h4_times, h4_ema20,
        tp_rr_short, risk_short, trailing_short, atr_buf_short):
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
    active_long  = {}   # sn -> long trade
    active_short = {}   # sn -> short trade (flip от провала LONG)
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

        ema_ok   = (forming_close > ema_base)
        slope_ok = (ptr_closed >= LONG_SLOPE_N) \
                   and (not np.isnan(h4_ema20[ptr_closed - LONG_SLOPE_N])) \
                   and (h4_ema > h4_ema20[ptr_closed - LONG_SLOPE_N])

        # ── управление LONG (ВСЕГДА) ──────────────────────────────────────────
        for sn in list(active_long.keys()):
            t = active_long[sn]
            trail_long(t, low)

            if low <= t['sl']:
                # LONG закрыт по SL — провал пробоя
                exit_price = t['sl']
                t['pnl'] = (exit_price - t['entry']) * t['size']
                balance += t['pnl']; t['year'] = cur_ts.year
                trades.append(dict(t, type='long'))
                del active_long[sn]

                # Открываем SHORT flip если нет активного SHORT по этой сессии
                if sn not in active_short and risk_short > 0:
                    short_sl  = t['entry'] + atr_buf_short * atr
                    short_rsk = short_sl - exit_price
                    if short_rsk > 0:
                        active_short[sn] = {
                            'entry': exit_price, 'sl': short_sl, 'initial_sl': short_sl,
                            'tp': exit_price - short_rsk * tp_rr_short,
                            'size': risk_short / short_rsk,
                            'session': sn,
                        }

            elif high >= t['tp']:
                t['pnl'] = (t['tp'] - t['entry']) * t['size']
                balance += t['pnl']; t['year'] = cur_ts.year
                trades.append(dict(t, type='long'))
                del active_long[sn]

        # ── управление SHORT flip (ВСЕГДА) ────────────────────────────────────
        for sn in list(active_short.keys()):
            t = active_short[sn]
            if trailing_short:
                trail_short(t, high)

            if high >= t['sl']:
                t['pnl'] = (t['entry'] - t['sl']) * t['size']
                balance += t['pnl']; t['year'] = cur_ts.year
                trades.append(dict(t, type='short'))
                del active_short[sn]
            elif low <= t['tp']:
                t['pnl'] = (t['entry'] - t['tp']) * t['size']
                balance += t['pnl']; t['year'] = cur_ts.year
                trades.append(dict(t, type='short'))
                del active_short[sn]

        if balance > peak: peak = balance
        dd = (peak - balance) / peak * 100
        if dd > max_dd: max_dd = dd

        # ── диапазоны сессий (ВСЕГДА) ─────────────────────────────────────────
        for sn, p in SESSIONS.items():
            sh, eh = p['range_hours']
            if sh <= hour < eh:
                s_highs[sn] = max(s_highs.get(sn, 0),   high)
                s_lows[sn]  = min(s_lows.get(sn, 1e9),  low)

        # ── LONG входы (только если фильтры прошли) ──────────────────────────
        if not (ema_ok and slope_ok):
            continue

        for sn, p in SESSIONS.items():
            if sn not in s_highs or sn in active_long:
                continue
            if not (p['entry_start'] <= hour < p['entry_end']):
                continue
            if close > s_highs[sn]:
                sl  = s_lows[sn] - LONG_ATR_BUF * atr
                rsk = close - sl
                if rsk <= 0: continue
                active_long[sn] = {
                    'entry': close, 'sl': sl, 'initial_sl': sl,
                    'tp': close + rsk * LONG_TP_RR,
                    'size': LONG_RISK / rsk,
                    'session': sn,
                }

    # дневной DD последнего дня
    if prev_date is not None and day_start > 0:
        dd = (day_start - balance) / day_start * 100
        if dd > max_daily_dd: max_daily_dd = dd

    # закрыть незавершённые
    last_close = float(m15[-1, i_c])
    last_year  = int(df.index[-1].year)
    for sn, t in active_long.items():
        t['pnl'] = (last_close - t['entry']) * t['size']
        balance += t['pnl']; t['year'] = last_year; trades.append(dict(t, type='long'))
    for sn, t in active_short.items():
        t['pnl'] = (t['entry'] - last_close) * t['size']
        balance += t['pnl']; t['year'] = last_year; trades.append(dict(t, type='short'))

    tdf    = pd.DataFrame(trades) if trades else pd.DataFrame(columns=['pnl','year','type'])
    pnl    = balance - 10_000
    n      = len(tdf)
    wr     = (tdf['pnl'] > 0).sum() / n if n > 0 else 0.0
    yearly = tdf.groupby('year')['pnl'].sum() if n > 0 else pd.Series(dtype=float)

    long_df  = tdf[tdf['type'] == 'long']  if n > 0 else pd.DataFrame()
    short_df = tdf[tdf['type'] == 'short'] if n > 0 else pd.DataFrame()

    return {
        'n': n, 'wr': wr, 'pnl': pnl,
        'max_dd': max_dd, 'max_daily_dd': max_daily_dd,
        'yearly': yearly,
        'all_pos': bool(all(yearly > 0)) if len(yearly) > 0 else False,
        'long_n':    len(long_df),
        'long_pnl':  float(long_df['pnl'].sum())  if len(long_df)  > 0 else 0.0,
        'short_n':   len(short_df),
        'short_pnl': float(short_df['pnl'].sum()) if len(short_df) > 0 else 0.0,
    }


def main():
    print("=" * 72)
    print("SHORT flip после LONG SL (failed breakout reversal)")
    print(f"LONG: slope={LONG_SLOPE_N}, TP={LONG_TP_RR}R, ATRbuf={LONG_ATR_BUF}, Risk=${LONG_RISK:.0f}")
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

    print(f"Данные: {df.index[0].date()} -- {df.index[-1].date()}  ({len(df):,} M15 баров)\n")

    # Baseline LONG only
    print("[Baseline LONG only]...", end='', flush=True)
    r0 = run(df, h4_times, h4_ema20, tp_rr_short=3, risk_short=0,
             trailing_short=False, atr_buf_short=0.5)
    print(f"  N={r0['n']}  WR={r0['wr']:.1%}  PnL=${r0['pnl']:,.0f}"
          f"  MaxDD={r0['max_dd']:.2f}%  DlyDD={r0['max_daily_dd']:.2f}%"
          f"  AllYrs={'YES' if r0['all_pos'] else 'NO'}")

    # Grid
    param_grid = {
        'tp_rr_short':   [2, 3, 4, 5, 7],
        'risk_short':    [50, 75, 100],
        'trailing_short':[True, False],
        'atr_buf_short': [0.3, 0.5, 1.0],
    }
    combos = list(itertools.product(*param_grid.values()))
    keys   = list(param_grid.keys())
    total  = len(combos)

    print(f"\nGrid {total} комбинаций SHORT параметров:\n")

    results = []
    t0 = time.time()
    best_pnl = -1e9

    for idx, vals in enumerate(combos, 1):
        params = dict(zip(keys, vals))
        r = run(df, h4_times, h4_ema20, **params)
        results.append({**params, **r})

        if r['max_dd'] < 10 and r['all_pos'] and r['pnl'] > best_pnl:
            best_pnl = r['pnl']

        if idx % 15 == 0 or idx == total:
            elapsed = time.time() - t0
            eta     = elapsed / idx * (total - idx)
            best_s  = f"  best_DD<10%=${best_pnl:,.0f}" if best_pnl > -1e9 else ""
            print(f"  [{idx:3d}/{total}]  elapsed={elapsed:.0f}s  ETA={eta:.0f}s{best_s}",
                  flush=True)

    rdf = pd.DataFrame(results)

    # Сводная таблица
    print()
    print("=" * 80)
    print("РЕЗУЛЬТАТЫ  (DD<10% + все годы -> первые; остальные по PnL)")
    print("=" * 80)
    hdr = f"  {'TP':>3} {'Risk':>5} {'Trl':>4} {'ATRbf':>6}  {'N':>4}({'L':>3}+{'S':>3})  {'WR':>6}  {'PnL':>9}  {'MaxDD':>7}  {'DlyDD':>6}  AllY"
    print(hdr)
    print("  " + "-" * 78)

    good = rdf[(rdf['max_dd'] < 10) & rdf['all_pos']].sort_values('pnl', ascending=False)
    rest = rdf[~((rdf['max_dd'] < 10) & rdf['all_pos'])].sort_values('pnl', ascending=False)

    def print_row(row, mark=''):
        dd_s = f"<10%+" if row['max_dd'] < 10 else f"{row['max_dd']:.2f}%"
        ay   = 'YES' if row['all_pos'] else 'NO '
        trl  = 'Y' if row['trailing_short'] else 'N'
        print(f"  {row['tp_rr_short']:>3.0f}R ${row['risk_short']:>4.0f}  {trl:>3}"
              f"  {row['atr_buf_short']:>5.1f}"
              f"  {row['n']:>4.0f}({row['long_n']:>3.0f}+{row['short_n']:>3.0f})"
              f"  {row['wr']:>5.1%}  ${row['pnl']:>8,.0f}  {dd_s:>7}  {row['max_daily_dd']:>5.2f}%  {ay}{mark}")

    if len(good) > 0:
        for _, row in good.head(20).iterrows():
            print_row(row, '  ***')
        print("  " + "-" * 78)
    else:
        print("  (нет комбинаций с DD<10% и все годы в плюс)")
        print("  " + "-" * 78)

    for _, row in rest.head(20).iterrows():
        print_row(row)

    # Детально лучший
    pool = good if len(good) > 0 else rdf.sort_values('pnl', ascending=False)
    best = pool.iloc[0]

    print()
    print("=" * 72)
    print("ДЕТАЛЬНО -- лучший:")
    print(f"  SHORT: TP={best['tp_rr_short']}R  Risk=${best['risk_short']:.0f}"
          f"  Trailing={'Y' if best['trailing_short'] else 'N'}"
          f"  ATRbuf={best['atr_buf_short']}")
    print(f"  Trades: {best['n']:.0f} ({best['long_n']:.0f} LONG + {best['short_n']:.0f} SHORT)")
    print(f"  WR={best['wr']:.1%}  PnL=${best['pnl']:,.0f}"
          f"  MaxDD={best['max_dd']:.2f}%  DlyDD={best['max_daily_dd']:.2f}%")
    print(f"  LONG PnL: ${best['long_pnl']:,.0f}   SHORT PnL: ${best['short_pnl']:,.0f}")

    r_best = run(df, h4_times, h4_ema20,
                 tp_rr_short=float(best['tp_rr_short']),
                 risk_short=float(best['risk_short']),
                 trailing_short=bool(best['trailing_short']),
                 atr_buf_short=float(best['atr_buf_short']))
    print("  PnL по годам:")
    for yr in sorted(r_best['yearly'].index):
        v = r_best['yearly'][yr]
        print(f"    {yr}: ${v:>8,.0f}  {'OK' if v > 0 else 'LOSS'}")

    print()
    print("=" * 72)
    print("СРАВНЕНИЕ с LONG only:")
    print(f"  LONG only: N={r0['n']}  PnL=${r0['pnl']:,.0f}  MaxDD={r0['max_dd']:.2f}%")
    print(f"  LONG+flip: N={best['n']:.0f}  PnL=${best['pnl']:,.0f}  MaxDD={best['max_dd']:.2f}%")
    print(f"  delta PnL: ${best['pnl'] - r0['pnl']:+,.0f}   delta DD: {best['max_dd'] - r0['max_dd']:+.2f}%")
    print("=" * 72)


if __name__ == "__main__":
    main()
