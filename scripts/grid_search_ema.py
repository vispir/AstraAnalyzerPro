"""
Grid search: EMA filter variant × TP_RR × Risk
Цель: найти комбинацию с PnL ≥ $60k, MaxDD < 10%, Daily DD < 5%, все годы в плюс.

Гипотеза: look-ahead давал $80k потому что использовал финальное закрытие H4.
Ищем эквивалент без look-ahead через M15/H1 EMA разных периодов.

Варианты EMA:
  no_filter     — без EMA фильтра вообще
  m15_ema80     — M15 EMA-80  (20 часов)
  m15_ema160    — M15 EMA-160 (40 часов)
  m15_ema320    — M15 EMA-320 (80 часов = то же окно что H4-EMA-20)
  h1_ema20      — последний закрытый H1 EMA-20  (20 часов)
  h1_ema40      — последний закрытый H1 EMA-40  (40 часов)
  h1_ema80      — последний закрытый H1 EMA-80  (80 часов)
  h4_forming    — текущий M15 close > last closed H4 EMA (= ea_mirror baseline)
  h4_closed     — last closed H4 close > last closed H4 EMA
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from session_breakout_trader import (
    ATR_PERIOD, ATR_BUFFER,
    LONG_SESSIONS,
    calculate_atr, calculate_ema,
)
import pandas as pd
import numpy as np
from pathlib import Path

# ─── Параметры grid ──────────────────────────────────────────────────────────
TP_RATIOS  = [3, 4, 5]
RISKS      = [100, 120]
H4_EMA_PERIOD = 20
MIN_WARMUP_M15 = 320   # ≥ M15-EMA-320 warmup

H4_NS  = int(4 * 3600 * 1e9)
H1_NS  = int(1 * 3600 * 1e9)


# ─── Построение EMA-фильтров (precomputed float arrays) ──────────────────────

def make_m15_ema(df, period):
    """EMA на M15. Фильтр: close[i] > result[i]  (result = EMA предыдущего бара)."""
    arr = df['close'].ewm(span=period, adjust=False).mean().shift(1).to_numpy()
    return arr


def make_h1_ema(df, period):
    """EMA последнего закрытого H1 бара. Фильтр: close[i] > result[i]."""
    h1_close = df['close'].resample('1h').last().dropna()
    h1_ema   = h1_close.ewm(span=period, adjust=False).mean()
    # H1 бар с индексом T закрывается в T+1h → доступен с T+1h
    h1_avail = h1_ema.copy()
    h1_avail.index = h1_avail.index + pd.Timedelta(hours=1)
    arr = h1_avail.reindex(df.index, method='ffill').to_numpy()
    return arr


def make_h4_forming(df, h4_times, h4_ema20):
    """
    M15 close > last closed H4 EMA   (математически эквивалентно ea_mirror).
    forming_close > forming_ema  ↔  forming_close > ema_base  (т.к. K<<1 не меняет знак).
    """
    times_ns = df.index.asi8
    n_h4 = len(h4_times)
    arr = np.full(len(df), np.nan)
    ptr = -1
    for i in range(len(df)):
        while ptr + 1 < n_h4 and h4_times[ptr + 1] + H4_NS <= times_ns[i]:
            ptr += 1
        if ptr >= 0:
            arr[i] = h4_ema20[ptr]
    return arr   # compare: close > arr[i]


def make_h4_closed(df, h4_times, h4_close, h4_ema20):
    """Фильтр: last closed H4 close > last closed H4 EMA → пропустить вход."""
    times_ns = df.index.asi8
    n_h4 = len(h4_times)
    # True = фильтр пропускает (H4 бар бычий)
    arr = np.zeros(len(df), dtype=bool)
    ptr = -1
    for i in range(len(df)):
        while ptr + 1 < n_h4 and h4_times[ptr + 1] + H4_NS <= times_ns[i]:
            ptr += 1
        if ptr >= 0:
            arr[i] = h4_close[ptr] > h4_ema20[ptr]
    return arr   # bool array


# ─── Backtest core ────────────────────────────────────────────────────────────

def apply_step_trailing(t, low, high):
    risk = t['entry'] - t['initial_sl']
    rr   = (low - t['entry']) / risk
    for trigger, lock in ((5, 4), (4, 3), (3, 2), (2, 1)):
        if rr >= trigger:
            t['sl'] = max(t['sl'], t['entry'] + lock * risk)
            break


def run_backtest(df, h4_times, h4_ema20,
                 ema_arr,        # float array или bool array или None
                 ema_is_bool,    # True: ema_arr[i] = bool фильтра; False: close>ema_arr[i]
                 tp_rr, risk):
    """
    ema_arr:
      None      → нет EMA фильтра
      bool arr  → прямой флаг (True = вход разрешён по EMA)
      float arr → вход разрешён если close > ema_arr[i] и ema_arr[i] не nan
    """
    n_h4     = len(h4_times)
    times_ns = df.index.asi8
    hours    = np.array([t.hour for t in df.index])
    m15_arr  = df.to_numpy()
    cols     = {c: i for i, c in enumerate(df.columns)}
    i_h = cols['high']; i_l = cols['low']
    i_c = cols['close']; i_a = cols['atr']

    ptr_closed = -1

    trades       = []
    active_long  = {}
    balance      = 10_000.0
    peak         = 10_000.0
    max_dd       = 0.0
    max_daily_dd = 0.0

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

        # Warmup: нужно MIN_WARMUP_M15 баров M15 И хотя бы 22 H4 бара
        if ptr_closed < 21 or i < MIN_WARMUP_M15:
            for sn, p in LONG_SESSIONS.items():
                sh, eh = p['range_hours']
                if sh <= hour < eh:
                    session_highs[sn] = max(session_highs.get(sn, 0), high)
                    session_lows[sn]  = min(session_lows.get(sn, 1e9), low)
            continue

        # EMA фильтр
        if ema_arr is None:
            ema_ok = True
        elif ema_is_bool:
            ema_ok = bool(ema_arr[i])
        else:
            v = float(ema_arr[i])
            ema_ok = (not np.isnan(v)) and (close > v)

        # Управление LONG сделками
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

        for sn, p in LONG_SESSIONS.items():
            sh, eh = p['range_hours']
            if sh <= hour < eh:
                session_highs[sn] = max(session_highs.get(sn, 0), high)
                session_lows[sn]  = min(session_lows.get(sn, 1e9), low)

        if not ema_ok:
            continue

        for sn, p in LONG_SESSIONS.items():
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

    tdf    = pd.DataFrame(trades)
    pnl    = balance - 10_000
    wr     = (tdf['pnl'] > 0).sum() / len(tdf) if len(tdf) > 0 else 0
    yearly = tdf.groupby('year')['pnl'].sum() if len(tdf) > 0 else pd.Series(dtype=float)
    all_pos = all(yearly > 0) if len(yearly) > 0 else False

    return {
        'n': len(tdf), 'wr': wr, 'pnl': pnl,
        'max_dd': max_dd, 'max_daily_dd': max_daily_dd,
        'yearly': yearly, 'all_pos': all_pos,
    }


# ─── Main ─────────────────────────────────────────────────────────────────────

def run():
    print("=" * 72)
    print("GRID SEARCH: EMA variant × TP_RR × Risk")
    print("=" * 72)

    data_path = (
        Path(__file__).parent.parent
        / "data_cache" / "dukascopy" / "m15" / "XAUUSD"
        / "xauusd_m15_2020-01-01_2026-04-18.parquet"
    )
    df = pd.read_parquet(data_path).sort_index()
    print(f"Period: {df.index[0].date()} -- {df.index[-1].date()}")
    df['atr'] = calculate_atr(df, ATR_PERIOD)

    df_h4 = df.resample('4h').agg(
        {'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last'}
    ).dropna()
    df_h4['ema20'] = calculate_ema(df_h4, H4_EMA_PERIOD)

    h4_times = df_h4.index.asi8
    h4_close = df_h4['close'].to_numpy()
    h4_ema20 = df_h4['ema20'].to_numpy()

    print("\nПредвычисляем EMA фильтры...", flush=True)
    variants = {}
    # (ema_arr, is_bool, description)
    variants['no_filter']   = (None,                                         False, 'Без фильтра')
    variants['m15_ema80']   = (make_m15_ema(df, 80),                         False, 'M15 EMA-80  (20ч)')
    variants['m15_ema160']  = (make_m15_ema(df, 160),                        False, 'M15 EMA-160 (40ч)')
    variants['m15_ema320']  = (make_m15_ema(df, 320),                        False, 'M15 EMA-320 (80ч)')
    variants['h1_ema20']    = (make_h1_ema(df, 20),                          False, 'H1-closed EMA-20 (20ч)')
    variants['h1_ema40']    = (make_h1_ema(df, 40),                          False, 'H1-closed EMA-40 (40ч)')
    variants['h1_ema80']    = (make_h1_ema(df, 80),                          False, 'H1-closed EMA-80 (80ч)')
    variants['h4_forming']  = (make_h4_forming(df, h4_times, h4_ema20),      False, 'H4-forming EMA-20 (ea_mirror)')
    variants['h4_closed']   = (make_h4_closed(df, h4_times, h4_close, h4_ema20), True, 'H4-closed close>EMA')
    print(f"  Готово. Вариантов: {len(variants)}")

    total = len(variants) * len(TP_RATIOS) * len(RISKS)
    print(f"\nЗапускаем {total} комбинаций...")

    all_results = []
    idx = 0
    for vname, (ema_arr, is_bool, vdesc) in variants.items():
        for tp in TP_RATIOS:
            for risk in RISKS:
                idx += 1
                print(f"  [{idx:2d}/{total}] {vname:<14} TP={tp}R  Risk=${risk}...", end='', flush=True)
                r = run_backtest(df, h4_times, h4_ema20,
                                 ema_arr, is_bool, tp, risk)
                all_results.append({
                    'variant': vname, 'desc': vdesc,
                    'tp': tp, 'risk': risk,
                    **r
                })
                tag = ''
                if r['max_dd'] < 10 and r['max_daily_dd'] < 5 and r['all_pos']:
                    tag = '  *** TARGET HIT ***'
                elif r['max_dd'] < 10:
                    tag = '  [DD<10%]'
                print(f"  {r['n']} trades  WR={r['wr']:.1%}  PnL=${r['pnl']:,.0f}  DD={r['max_dd']:.2f}%{tag}")

    # ── Сортировка и вывод ────────────────────────────────────────────────────
    rdf = pd.DataFrame(all_results)

    print()
    print("=" * 80)
    print("ТОП-15 по PnL (все у кого MaxDD < 15%)")
    print("=" * 80)
    sub = rdf[rdf['max_dd'] < 15].sort_values('pnl', ascending=False).head(15)
    hdr = f"  {'Вариант':<20} {'TP':>3} {'Risk':>5}  {'N':>5}  {'WR':>6}  {'PnL':>9}  {'MaxDD':>7}  {'DlyDD':>7}  {'AllYrs':>7}"
    print(hdr)
    print("  " + "-" * 77)
    for _, row in sub.iterrows():
        ay = 'YES' if row['all_pos'] else 'NO '
        ok = '<10% ✓' if row['max_dd'] < 10 else f"{row['max_dd']:.1f}% "
        print(f"  {row['variant']:<20} {row['tp']:>3}R ${row['risk']:>4}  "
              f"{row['n']:>5}  {row['wr']:>5.1%}  ${row['pnl']:>8,.0f}  "
              f"{ok:>8}  {row['max_daily_dd']:>6.2f}%  {ay:>7}")

    print()
    print("=" * 80)
    print("КОМБИНАЦИИ ДОСТИГШИЕ ЦЕЛИ (DD<10%, DlyDD<5%, все годы в плюс)")
    print("=" * 80)
    targets = rdf[
        (rdf['max_dd'] < 10) &
        (rdf['max_daily_dd'] < 5) &
        (rdf['all_pos'] == True)
    ].sort_values('pnl', ascending=False)

    if len(targets) == 0:
        print("  Ни одна комбинация не достигла всех целей.")
        print()
        print("  Лучшие кандидаты (MaxDD < 11%):")
        cands = rdf[rdf['max_dd'] < 11].sort_values('pnl', ascending=False).head(10)
        for _, row in cands.iterrows():
            ay = 'YES' if row['all_pos'] else 'NO '
            print(f"    {row['variant']:<20} TP={row['tp']}R Risk=${row['risk']:>3}  "
                  f"N={row['n']:>4}  WR={row['wr']:.1%}  PnL=${row['pnl']:>8,.0f}  "
                  f"MaxDD={row['max_dd']:.2f}%  DlyDD={row['max_daily_dd']:.2f}%  {ay}")
    else:
        for _, row in targets.iterrows():
            print(f"  *** {row['variant']:<18} TP={row['tp']}R Risk=${row['risk']:>3}  "
                  f"N={row['n']:>4}  WR={row['wr']:.1%}  PnL=${row['pnl']:>8,.0f}  "
                  f"MaxDD={row['max_dd']:.2f}%  DlyDD={row['max_daily_dd']:.2f}%")
            print(f"      PnL по годам: ", end='')
            yr_str = '  '.join(f"{yr}: ${v:,.0f}" for yr, v in sorted(row['yearly'].items()))
            print(yr_str)

    # ── Детальный вывод по лучшему результату ────────────────────────────────
    best = rdf.sort_values('pnl', ascending=False).iloc[0]
    print()
    print("=" * 80)
    print(f"ЛУЧШИЙ РЕЗУЛЬТАТ ПО PnL:")
    print(f"  {best['desc']}  TP={best['tp']}R  Risk=${best['risk']}")
    print(f"  Trades={best['n']}  WR={best['wr']:.1%}  PnL=${best['pnl']:,.0f}"
          f"  MaxDD={best['max_dd']:.2f}%  DlyDD={best['max_daily_dd']:.2f}%")

    # ── Сводная таблица по вариантам EMA (усреднено по TP и Risk) ────────────
    print()
    print("=" * 80)
    print("СРАВНЕНИЕ EMA МЕТОДОВ (TP=3R, Risk=$120, для сравнения с baseline)")
    print("=" * 80)
    base_filter = rdf[(rdf['tp'] == 3) & (rdf['risk'] == 120)].sort_values('pnl', ascending=False)
    print(f"  {'Вариант':<26} {'N':>5}  {'WR':>6}  {'PnL':>9}  {'MaxDD':>8}  {'DD<10%':>8}")
    print("  " + "-" * 72)
    for _, row in base_filter.iterrows():
        ok = 'YES ✓' if row['max_dd'] < 10 else 'NO  ✗'
        print(f"  {row['desc']:<26} {row['n']:>5}  {row['wr']:>5.1%}  "
              f"${row['pnl']:>8,.0f}  {row['max_dd']:>7.2f}%  {ok:>8}")
    print("=" * 80)


if __name__ == "__main__":
    run()
