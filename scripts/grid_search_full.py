"""
Полный grid search параметров LONG стратегии.

Параметры:
  tp_rr          : [3, 4, 5, 5.5, 6, 7]
  trailing       : [True, False]
  asian_end      : [14, 16, 18, 24]   — час закрытия entry window Asian (вар. Б)
  atr_buffer     : [0.3, 0.5, 1.0]   — отступ SL за session_low
  ema_slope      : [0, 3, 5]          — slope фильтр (0 = выкл)
  use_h4_ema     : [True, False]      — H4 EMA фильтр

Фиксировано: Risk=$100, London entry=16-24, NY entry=18-21, все 3 сессии.
Текущий EA: TP=5.5, trailing=True, asian_end=24, atr_buf=0.5, slope=0, h4_ema=True

Итого: 6×2×4×3×3×2 = 864 комбинации
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from session_breakout_trader import (
    ATR_PERIOD, H4_EMA_PERIOD,
    LONG_SESSIONS, calculate_atr, calculate_ema,
)
import pandas as pd
import numpy as np
from pathlib import Path
import itertools, time

K_EMA = 2.0 / (H4_EMA_PERIOD + 1.0)
H4_NS = int(4 * 3600 * 1e9)
MIN_H4 = 22
RISK   = 100.0


def floor4h_ns(ts_ns):
    return int(ts_ns // int(14400 * 1e9)) * int(14400 * 1e9)


def apply_step_trailing(t, low):
    risk = t['entry'] - t['initial_sl']
    rr   = (low - t['entry']) / risk
    for trigger, lock in ((5, 4), (4, 3), (3, 2), (2, 1)):
        if rr >= trigger:
            t['sl'] = max(t['sl'], t['entry'] + lock * risk)
            break


def run(df, h4_times, h4_ema20,
        tp_rr, trailing, asian_end, atr_buffer, ema_slope, use_h4_ema):

    # Сессии с кастомным asian entry end
    sessions = {}
    for sn, p in LONG_SESSIONS.items():
        sessions[sn] = dict(p)
    sessions['asian']['entry_end'] = asian_end

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

        # Флаги фильтров
        if use_h4_ema:
            ema_ok = (forming_close > ema_base)
        else:
            ema_ok = True

        slope_ok = True
        if ema_slope > 0:
            if ptr_closed < ema_slope:
                slope_ok = False
            else:
                ema_ago  = h4_ema20[ptr_closed - ema_slope]
                slope_ok = (not np.isnan(ema_ago)) and (h4_ema > ema_ago)

        # Управление LONG сделками — ВСЕГДА
        for sn in list(active_long.keys()):
            t = active_long[sn]
            if trailing:
                apply_step_trailing(t, low)
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

        # Диапазоны — ВСЕГДА
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
            es, ee = p['entry_start'], p['entry_end']
            if not (es <= hour < ee):
                continue
            if close > session_highs[sn]:
                sl  = session_lows[sn] - atr_buffer * atr
                rsk = close - sl
                if rsk <= 0: continue
                active_long[sn] = {
                    'entry': close, 'sl': sl, 'initial_sl': sl,
                    'tp': close + rsk * tp_rr,
                    'size': RISK / rsk,
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

    tdf    = pd.DataFrame(trades) if trades else pd.DataFrame(columns=['pnl','year'])
    pnl    = balance - 10_000
    n      = len(tdf)
    wr     = (tdf['pnl'] > 0).sum() / n if n > 0 else 0.0
    yearly = tdf.groupby('year')['pnl'].sum() if n > 0 else pd.Series(dtype=float)
    all_pos = all(yearly > 0) if len(yearly) > 0 else False

    return {'n': n, 'wr': wr, 'pnl': pnl,
            'max_dd': max_dd, 'max_daily_dd': max_daily_dd,
            'yearly': yearly, 'all_pos': all_pos}


def main():
    t0 = time.time()
    print("=" * 72)
    print("FULL GRID SEARCH — LONG параметры  (Risk=$100 фиксировано)")
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

    param_grid = {
        'tp_rr':      [3.0, 4.0, 5.0, 5.5, 6.0, 7.0],
        'trailing':   [True, False],
        'asian_end':  [14, 16, 18, 24],
        'atr_buffer': [0.3, 0.5, 1.0],
        'ema_slope':  [0, 3, 5],
        'use_h4_ema': [True, False],
    }

    keys   = list(param_grid.keys())
    values = list(param_grid.values())
    combos = list(itertools.product(*values))
    total  = len(combos)

    # Текущие параметры EA для сравнения
    ea_params = dict(tp_rr=5.5, trailing=True, asian_end=24,
                     atr_buffer=0.5, ema_slope=0, use_h4_ema=True)

    print(f"Всего комбинаций: {total}")
    print(f"Текущий EA: {ea_params}")
    print()

    all_res = []
    for idx, combo in enumerate(combos, 1):
        params = dict(zip(keys, combo))
        r = run(df, h4_times, h4_ema20, **params)
        all_res.append({**params, **r})

        if idx % 50 == 0 or idx == total:
            elapsed = time.time() - t0
            eta = elapsed / idx * (total - idx)
            good = [r2['pnl'] for r2 in all_res if r2['max_dd'] < 10]
            best_s = f"  best_DD<10%=${max(good):,.0f}" if good else ""
            print(f"  [{idx:4d}/{total}]  elapsed={elapsed:.0f}s  ETA={eta:.0f}s{best_s}",
                  flush=True)

    rdf = pd.DataFrame(all_res)
    elapsed = time.time() - t0
    print(f"\nГотово за {elapsed:.0f} сек.\n")

    # ── Текущий EA ────────────────────────────────────────────────────────────
    ea_row = rdf[
        (rdf['tp_rr'] == 5.5) & (rdf['trailing'] == True) &
        (rdf['asian_end'] == 24) & (rdf['atr_buffer'] == 0.5) &
        (rdf['ema_slope'] == 0) & (rdf['use_h4_ema'] == True)
    ].iloc[0]
    print("=" * 72)
    print(f"ТЕКУЩИЙ EA (baseline):  "
          f"N={ea_row['n']}  WR={ea_row['wr']:.1%}  PnL=${ea_row['pnl']:,.0f}  "
          f"MaxDD={ea_row['max_dd']:.2f}%  DlyDD={ea_row['max_daily_dd']:.2f}%  "
          f"AllYrs={'YES' if ea_row['all_pos'] else 'NO'}")

    # ── Топ-20 по PnL, с DD<10% и все годы в плюс ────────────────────────────
    print()
    print("=" * 72)
    print("ТОП-20 по PnL  (фильтр: DD<10%, DlyDD<5%, все годы в плюс)")
    print("=" * 72)
    targets = rdf[
        (rdf['max_dd'] < 10) &
        (rdf['max_daily_dd'] < 5) &
        (rdf['all_pos'] == True)
    ].sort_values('pnl', ascending=False).head(20)

    if len(targets) == 0:
        print("  Нет комбинаций с DD<10% и все годы в плюс.")
        print("  Топ-20 по PnL среди DD<12%:")
        targets = rdf[rdf['max_dd'] < 12].sort_values('pnl', ascending=False).head(20)

    hdr = (f"  {'TP':>5} {'Trl':>4} {'AsEnd':>6} {'ATRbuf':>7} "
           f"{'Slope':>6} {'H4':>4}  {'N':>5}  {'WR':>6}  {'PnL':>9}  "
           f"{'MaxDD':>7}  {'DlyDD':>6}  {'AllY'}")
    print(hdr)
    print("  " + "-" * 80)
    for _, row in targets.iterrows():
        ea_mark = ' <-- EA' if (
            row['tp_rr'] == 5.5 and row['trailing'] and row['asian_end'] == 24 and
            row['atr_buffer'] == 0.5 and row['ema_slope'] == 0 and row['use_h4_ema']
        ) else ''
        print(f"  {row['tp_rr']:>5.1f} {'Y' if row['trailing'] else 'N':>4} "
              f"{row['asian_end']:>6} {row['atr_buffer']:>7.1f} "
              f"{row['ema_slope']:>6} {'Y' if row['use_h4_ema'] else 'N':>4}  "
              f"{row['n']:>5}  {row['wr']:>5.1%}  ${row['pnl']:>8,.0f}  "
              f"{row['max_dd']:>6.2f}%  {row['max_daily_dd']:>5.2f}%  "
              f"{'YES' if row['all_pos'] else 'NO '}{ea_mark}")

    # ── Влияние каждого параметра (при прочих равных) ─────────────────────────
    print()
    print("=" * 72)
    print("ВЛИЯНИЕ КАЖДОГО ПАРАМЕТРА  (медиана PnL и DD по группам)")
    print("=" * 72)

    for param in keys:
        print(f"\n  {param}:")
        grp = rdf.groupby(param).agg(
            pnl_med=('pnl', 'median'),
            dd_med=('max_dd', 'median'),
            pnl_max=('pnl', 'max'),
            n_under10=('max_dd', lambda x: (x < 10).sum()),
        ).reset_index()
        for _, g in grp.iterrows():
            val = g[param]
            val_s = f"{val:.1f}" if isinstance(val, float) else str(val)
            print(f"    {val_s:>8}:  PnL_med=${g['pnl_med']:>7,.0f}  "
                  f"DD_med={g['dd_med']:>5.2f}%  "
                  f"PnL_max=${g['pnl_max']:>7,.0f}  "
                  f"DD<10%: {g['n_under10']:>3} комб.")

    # ── Asian entry window ────────────────────────────────────────────────────
    print()
    print("=" * 72)
    print("АЗИАТСКАЯ СЕССИЯ — влияние entry window (прочие = текущий EA)")
    print("=" * 72)
    for ae in [14, 16, 18, 24]:
        sub = rdf[
            (rdf['tp_rr'] == 5.5) & (rdf['trailing'] == True) &
            (rdf['asian_end'] == ae) & (rdf['atr_buffer'] == 0.5) &
            (rdf['ema_slope'] == 0) & (rdf['use_h4_ema'] == True)
        ]
        if len(sub) == 0: continue
        row = sub.iloc[0]
        mk = ' <-- текущий' if ae == 24 else ''
        print(f"  Asian entry 10-{ae:02d}:  N={row['n']:>4}  WR={row['wr']:.1%}  "
              f"PnL=${row['pnl']:>8,.0f}  MaxDD={row['max_dd']:.2f}%"
              f"  DlyDD={row['max_daily_dd']:.2f}%{mk}")

    # ── Детально лучший результат ─────────────────────────────────────────────
    best = rdf[(rdf['max_dd'] < 10) & rdf['all_pos']].sort_values('pnl', ascending=False)
    if len(best) > 0:
        row = best.iloc[0]
        print()
        print("=" * 72)
        print("ДЕТАЛЬНО — лучший (DD<10%, все годы в плюс, макс PnL):")
        print(f"  TP={row['tp_rr']}R  Trailing={'Y' if row['trailing'] else 'N'}  "
              f"AsianEnd={row['asian_end']}  ATRbuf={row['atr_buffer']}  "
              f"Slope={row['ema_slope']}  H4EMA={'Y' if row['use_h4_ema'] else 'N'}")
        print(f"  N={row['n']}  WR={row['wr']:.1%}  PnL=${row['pnl']:,.0f}  "
              f"MaxDD={row['max_dd']:.2f}%  DlyDD={row['max_daily_dd']:.2f}%")
        print(f"  При Risk=$120: PnL≈${row['pnl']*1.2:,.0f}  MaxDD≈{row['max_dd']*1.2:.2f}%")
        print(f"  PnL по годам:")
        for yr in sorted(row['yearly'].keys()):
            v = row['yearly'][yr]
            print(f"    {yr}: ${v:>7,.0f}  {'✓' if v > 0 else '✗'}")
    print("=" * 72)


if __name__ == "__main__":
    main()
