"""
LONG+SHORT комбинированный тест — без look-ahead.

LONG : ea_mirror (slope=5, TP=7R, ATR_buf=0.5, Risk=$100) — best из grid_search_full.py
SHORT: H1 завершённые бары (ptr_h1 указатель), H1 new high + bearish close → M15 breakdown

Нет look-ahead:
  - LONG: ptr_closed advances when h4_times[ptr+1] + H4_NS <= ts_ns
           forming_close берётся из текущего M15 bar
  - SHORT: ptr_h1 advances when h1_times[ptr+1] + H1_NS <= ts_ns
           Сигнал только от завершённых H1 баров

Grid по SHORT параметрам:
  lookback_h1 : [5, 10, 20]   — сколько H1 баров назад искать новый максимум
  tp_rr_short : [3, 5, 7]     — TP SHORT в R
  risk_short  : [50, 100]     — риск на SHORT сделку
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

# ─── константы ───────────────────────────────────────────────────────────────
K_EMA   = 2.0 / (H4_EMA_PERIOD + 1.0)
H4_NS   = int(4 * 3600 * 1e9)
H1_NS   = int(1 * 3600 * 1e9)
MIN_H4  = 22      # минимум закрытых H4 баров для EMA
MIN_H1  = 25      # минимум закрытых H1 баров для SHORT сигнала

# LONG параметры (фиксированы — best из grid_search_full.py)
LONG_SLOPE_N   = 5
LONG_TP_RR     = 7.0
LONG_ATR_BUF   = 0.5
LONG_RISK      = 100.0

# SHORT сигнал: сбрасывается через MAX_SIGNAL_BARS H1 баров без входа
SHORT_SIGNAL_EXPIRY_H1 = 8   # ~8 часов


def floor4h_ns(ts_ns):
    return int(ts_ns // int(14400 * 1e9)) * int(14400 * 1e9)


def apply_trailing_long(t, low):
    risk = t['entry'] - t['initial_sl']
    rr   = (low - t['entry']) / risk
    for trigger, lock in ((5, 4), (4, 3), (3, 2), (2, 1)):
        if rr >= trigger:
            t['sl'] = max(t['sl'], t['entry'] + lock * risk)
            break


def apply_trailing_short(t, high):
    risk = t['initial_sl'] - t['entry']
    rr   = (t['entry'] - high) / risk
    for trigger, lock in ((5, 4), (4, 3), (3, 2), (2, 1)):
        if rr >= trigger:
            t['sl'] = min(t['sl'], t['entry'] - lock * risk)
            break


def run(df, h4_times, h4_ema20, h1_times, h1_highs, h1_closes,
        lookback_h1, tp_rr_short, risk_short):
    """
    Один прогон LONG+SHORT.

    Look-ahead проверка:
      H4: ptr_closed = последний H4 бар с open_time + 4h <= current_m15_time
          ema_base  = h4_ema20[ptr_closed]  — EMA от прошлого закрытого бара
          forming_close = текущий M15 close (нет будущего)
      H1: ptr_h1    = последний H1 бар с open_time + 1h <= current_m15_time
          h1_highs/h1_closes — массивы значений закрытых H1 баров
    """
    n_h4     = len(h4_times)
    n_h1     = len(h1_times)
    times_ns = df.index.asi8
    m15_arr  = df.to_numpy()
    cols     = {c: i for i, c in enumerate(df.columns)}
    i_h = cols['high']; i_l = cols['low']
    i_c = cols['close']; i_a = cols['atr']

    # Указатели
    ptr_closed     = -1
    ptr_h1         = -1
    forming_period = -1
    forming_close  = np.nan
    ema_base       = np.nan

    # Состояние LONG
    active_long  = {}

    # Состояние SHORT
    short_signal      = False
    short_signal_high = np.nan
    short_signal_bar  = -1  # ptr_h1 момента сигнала

    active_short = None

    # Баланс / DD
    trades       = []
    balance      = 10_000.0
    peak         = 10_000.0
    max_dd       = 0.0
    max_daily_dd = 0.0
    prev_date    = None
    day_start    = balance

    session_highs = {}
    session_lows  = {}

    prev_m15_low = np.nan

    for i in range(len(df)):
        ts_ns  = int(times_ns[i])
        cur_ts = df.index[i]
        high   = float(m15_arr[i, i_h])
        low    = float(m15_arr[i, i_l])
        close  = float(m15_arr[i, i_c])
        atr    = float(m15_arr[i, i_a])
        hour   = cur_ts.hour

        if np.isnan(atr):
            prev_m15_low = low
            continue

        # ── дневной DD ────────────────────────────────────────────────────────
        cur_date = cur_ts.date()
        if cur_date != prev_date:
            if prev_date is not None and day_start > 0:
                ddaily = (day_start - balance) / day_start * 100
                if ddaily > max_daily_dd: max_daily_dd = ddaily
            day_start     = balance
            prev_date     = cur_date
            session_highs = {}
            session_lows  = {}

        # ── H4 указатель ──────────────────────────────────────────────────────
        while ptr_closed + 1 < n_h4 and h4_times[ptr_closed + 1] + H4_NS <= ts_ns:
            ptr_closed += 1

        # ── H1 указатель ──────────────────────────────────────────────────────
        prev_ptr_h1 = ptr_h1
        while ptr_h1 + 1 < n_h1 and h1_times[ptr_h1 + 1] + H1_NS <= ts_ns:
            ptr_h1 += 1

        new_h1_bar_closed = (ptr_h1 > prev_ptr_h1) and (ptr_h1 >= 0)

        # ── минимум данных ────────────────────────────────────────────────────
        if ptr_closed < MIN_H4 - 1:
            for sn, p in LONG_SESSIONS.items():
                sh, eh = p['range_hours']
                if sh <= hour < eh:
                    session_highs[sn] = max(session_highs.get(sn, 0), high)
                    session_lows[sn]  = min(session_lows.get(sn, 1e9), low)
            prev_m15_low = low
            continue

        # ── H4 forming ema ────────────────────────────────────────────────────
        h4p = floor4h_ns(ts_ns)
        if h4p != forming_period:
            forming_period = h4p
            forming_close  = close
            ema_base = h4_ema20[ptr_closed] if ptr_closed >= 0 else np.nan
        else:
            forming_close = close

        if np.isnan(ema_base):
            prev_m15_low = low
            continue

        h4_ema = forming_close * K_EMA + ema_base * (1.0 - K_EMA)

        # ── LONG фильтры ──────────────────────────────────────────────────────
        ema_ok = (forming_close > ema_base)
        slope_ok = True
        if LONG_SLOPE_N > 0:
            if ptr_closed < LONG_SLOPE_N:
                slope_ok = False
            else:
                ema_ago  = h4_ema20[ptr_closed - LONG_SLOPE_N]
                slope_ok = (not np.isnan(ema_ago)) and (h4_ema > ema_ago)

        # ── Управление LONG сделками (ВСЕГДА) ─────────────────────────────────
        for sn in list(active_long.keys()):
            t = active_long[sn]
            apply_trailing_long(t, low)
            if low <= t['sl']:
                t['pnl'] = (t['sl'] - t['entry']) * t['size']
                balance += t['pnl']; t['year'] = cur_ts.year
                trades.append(t); del active_long[sn]
            elif high >= t['tp']:
                t['pnl'] = (t['tp'] - t['entry']) * t['size']
                balance += t['pnl']; t['year'] = cur_ts.year
                trades.append(t); del active_long[sn]

        # ── Управление SHORT сделкой (ВСЕГДА) ────────────────────────────────
        if active_short is not None:
            apply_trailing_short(active_short, high)
            if high >= active_short['sl']:
                active_short['pnl'] = (active_short['entry'] - active_short['sl']) * active_short['size']
                balance += active_short['pnl']; active_short['year'] = cur_ts.year
                trades.append(active_short); active_short = None
            elif low <= active_short['tp']:
                active_short['pnl'] = (active_short['entry'] - active_short['tp']) * active_short['size']
                balance += active_short['pnl']; active_short['year'] = cur_ts.year
                trades.append(active_short); active_short = None

        # ── DD ────────────────────────────────────────────────────────────────
        if balance > peak: peak = balance
        dd = (peak - balance) / peak * 100
        if dd > max_dd: max_dd = dd

        # ── Диапазоны сессий (ВСЕГДА) ─────────────────────────────────────────
        for sn, p in LONG_SESSIONS.items():
            sh, eh = p['range_hours']
            if sh <= hour < eh:
                session_highs[sn] = max(session_highs.get(sn, 0), high)
                session_lows[sn]  = min(session_lows.get(sn, 1e9), low)

        # ── SHORT сигнал (новый закрытый H1 бар) ─────────────────────────────
        if new_h1_bar_closed and ptr_h1 >= MIN_H1 and active_short is None:
            cur_h1_close = float(h1_closes[ptr_h1])
            cur_h1_high  = float(h1_highs[ptr_h1])
            prev_h1_close = float(h1_closes[ptr_h1 - 1])

            # Медвежье закрытие H1
            bearish_h1 = (cur_h1_close < prev_h1_close)

            if bearish_h1:
                # Новый максимум за lookback H1 баров
                lookback_start = max(0, ptr_h1 - lookback_h1)
                prev_highs = h1_highs[lookback_start:ptr_h1]
                if len(prev_highs) > 0 and cur_h1_high > prev_highs.max():
                    short_signal      = True
                    short_signal_high = cur_h1_high
                    short_signal_bar  = ptr_h1

            # Сбросить устаревший сигнал
            if short_signal and ptr_h1 - short_signal_bar > SHORT_SIGNAL_EXPIRY_H1:
                short_signal = False

        # ── SHORT вход (M15 триггер) ──────────────────────────────────────────
        if (short_signal and active_short is None
                and risk_short > 0
                and not np.isnan(prev_m15_low)
                and close < prev_m15_low
                and ptr_h1 >= MIN_H1):
            sl_short  = short_signal_high + LONG_ATR_BUF * atr
            rsk_short = sl_short - close
            if rsk_short > 0:
                active_short = {
                    'entry': close, 'sl': sl_short, 'initial_sl': sl_short,
                    'tp': close - rsk_short * tp_rr_short,
                    'size': risk_short / rsk_short,
                    'direction': 'SHORT', 'session': 'short', 'year': cur_ts.year,
                }
                short_signal = False  # использован

        # ── LONG входы (только если фильтры прошли) ──────────────────────────
        if ema_ok and slope_ok:
            for sn, p in LONG_SESSIONS.items():
                if sn not in session_highs or sn in active_long:
                    continue
                if not (p['entry_start'] <= hour < p['entry_end']):
                    continue
                if close > session_highs[sn]:
                    sl  = session_lows[sn] - LONG_ATR_BUF * atr
                    rsk = close - sl
                    if rsk <= 0: continue
                    active_long[sn] = {
                        'entry': close, 'sl': sl, 'initial_sl': sl,
                        'tp': close + rsk * LONG_TP_RR,
                        'size': LONG_RISK / rsk,
                        'direction': 'LONG', 'session': sn, 'year': cur_ts.year,
                    }

        prev_m15_low = low

    # ── дневной DD последнего дня ─────────────────────────────────────────────
    if prev_date is not None and day_start > 0:
        ddaily = (day_start - balance) / day_start * 100
        if ddaily > max_daily_dd: max_daily_dd = ddaily

    # ── Закрыть оставшиеся ───────────────────────────────────────────────────
    last_close = float(m15_arr[-1, i_c])
    last_year  = int(df.index[-1].year)
    for sn, t in active_long.items():
        t['pnl'] = (last_close - t['entry']) * t['size']
        balance += t['pnl']; t['year'] = last_year; trades.append(t)
    if active_short is not None:
        active_short['pnl'] = (active_short['entry'] - last_close) * active_short['size']
        balance += active_short['pnl']; active_short['year'] = last_year; trades.append(active_short)

    tdf    = pd.DataFrame(trades) if trades else pd.DataFrame(columns=['pnl','direction','year'])
    pnl    = balance - 10_000
    n      = len(tdf)
    wr     = (tdf['pnl'] > 0).sum() / n if n > 0 else 0.0
    yearly = tdf.groupby('year')['pnl'].sum() if n > 0 else pd.Series(dtype=float)

    long_df  = tdf[tdf['direction'] == 'LONG']  if n > 0 else pd.DataFrame()
    short_df = tdf[tdf['direction'] == 'SHORT'] if n > 0 else pd.DataFrame()

    return {
        'n': n, 'wr': wr, 'pnl': pnl,
        'max_dd': max_dd, 'max_daily_dd': max_daily_dd,
        'yearly': yearly,
        'all_pos': all(yearly > 0) if len(yearly) > 0 else False,
        'long_n':   len(long_df),
        'long_pnl': float(long_df['pnl'].sum()) if len(long_df) > 0 else 0.0,
        'short_n':   len(short_df),
        'short_pnl': float(short_df['pnl'].sum()) if len(short_df) > 0 else 0.0,
    }


def main():
    print("=" * 72)
    print("LONG+SHORT КОМБИНИРОВАННЫЙ ТЕСТ — без look-ahead")
    print(f"LONG: slope={LONG_SLOPE_N}, TP={LONG_TP_RR}R, ATRbuf={LONG_ATR_BUF}, Risk=${LONG_RISK:.0f}")
    print(f"SHORT: H1 завершённые бары, сигнал истекает через {SHORT_SIGNAL_EXPIRY_H1} H1 баров")
    print("=" * 72)

    data_path = (
        Path(__file__).parent.parent
        / "data_cache" / "dukascopy" / "m15" / "XAUUSD"
        / "xauusd_m15_2020-01-01_2026-04-18.parquet"
    )
    df = pd.read_parquet(data_path).sort_index()
    df['atr'] = calculate_atr(df, ATR_PERIOD)

    # H4 данные
    df_h4 = df.resample('4h').agg(
        {'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last'}
    ).dropna()
    df_h4['ema20'] = calculate_ema(df_h4, H4_EMA_PERIOD)
    h4_times = df_h4.index.asi8
    h4_ema20 = df_h4['ema20'].to_numpy()

    # H1 данные (для SHORT)
    df_h1 = df.resample('1h').agg(
        {'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last'}
    ).dropna()
    h1_times  = df_h1.index.asi8
    h1_highs  = df_h1['high'].to_numpy()
    h1_closes = df_h1['close'].to_numpy()

    print(f"Данные: {df.index[0].date()} — {df.index[-1].date()}  ({len(df)} M15 баров)")
    print(f"H4 баров: {len(df_h4)},  H1 баров: {len(df_h1)}")

    # LONG-only baseline (для сравнения)
    print("\n[Baseline LONG only]...", end='', flush=True)
    r_long = run(df, h4_times, h4_ema20, h1_times, h1_highs, h1_closes,
                 lookback_h1=999, tp_rr_short=5, risk_short=0)
    print(f"  N={r_long['n']} trades  WR={r_long['wr']:.1%}  PnL=${r_long['pnl']:,.0f}"
          f"  MaxDD={r_long['max_dd']:.2f}%  DlyDD={r_long['max_daily_dd']:.2f}%"
          f"  AllYrs={'YES' if r_long['all_pos'] else 'NO'}")

    # Grid
    lookback_list  = [5, 10, 20]
    tp_short_list  = [3, 5, 7]
    risk_short_list = [50, 100]

    total = len(lookback_list) * len(tp_short_list) * len(risk_short_list)
    print(f"\nGrid {total} комбинаций SHORT параметров:\n")

    results = []
    idx = 0
    for lb in lookback_list:
        for tp_s in tp_short_list:
            for rsk_s in risk_short_list:
                idx += 1
                label = f"lb={lb:2d} TP={tp_s}R Risk=${rsk_s:3d}"
                print(f"  [{idx:2d}/{total}] {label}...", end='', flush=True)
                r = run(df, h4_times, h4_ema20, h1_times, h1_highs, h1_closes,
                        lookback_h1=lb, tp_rr_short=tp_s, risk_short=rsk_s)
                results.append({'lookback': lb, 'tp_short': tp_s, 'risk_short': rsk_s, **r})
                hit = ''
                if r['max_dd'] < 10 and r['all_pos']:  hit = '  *** DD<10% ***'
                elif r['max_dd'] < 10:                  hit = '  [DD<10%]'
                print(f"  N={r['n']:3d}({r['long_n']}L+{r['short_n']}S)"
                      f"  WR={r['wr']:.1%}  PnL=${r['pnl']:,.0f}"
                      f"  DD={r['max_dd']:.2f}%  DlyDD={r['max_daily_dd']:.2f}%{hit}")

    rdf = pd.DataFrame(results)

    # ── Итоговая таблица ──────────────────────────────────────────────────────
    print()
    print("=" * 80)
    print("СВОДНАЯ ТАБЛИЦА (DD<10% + все годы в плюс → первые; остальные по DD)")
    print("=" * 80)
    hdr = f"  {'Параметры':<22} {'N':>4} {'LONG':>6} {'SHORT':>6} {'WR':>6}  {'PnL':>9}  {'MaxDD':>7}  {'DlyDD':>6}  AllY"
    print(hdr)
    print("  " + "-" * 77)

    good = rdf[(rdf['max_dd'] < 10) & rdf['all_pos']].sort_values('pnl', ascending=False)
    rest = rdf[~((rdf['max_dd'] < 10) & rdf['all_pos'])].sort_values('max_dd')

    def print_row(row, mark=''):
        lbl = f"lb={row['lookback']:2d} TP={row['tp_short']}R R=${row['risk_short']:3.0f}"
        dd_s = f"<10%✓" if row['max_dd'] < 10 else f"{row['max_dd']:.2f}%"
        ay   = 'YES' if row['all_pos'] else 'NO '
        print(f"  {lbl:<22} {row['n']:>4} {row['long_n']:>6} {row['short_n']:>6}"
              f" {row['wr']:>5.1%}  ${row['pnl']:>8,.0f}  {dd_s:>7}  {row['max_daily_dd']:>5.2f}%  {ay}{mark}")

    for _, row in good.iterrows():
        print_row(row, '  ***')
    if len(good) == 0:
        print("  (нет комбинаций с DD<10% и все годы в плюс)")
    if len(rest) > 0:
        print("  " + "-" * 77)
        for _, row in rest.iterrows():
            print_row(row)

    # ── Лучший + детали ───────────────────────────────────────────────────────
    best_pool = rdf[rdf['max_dd'] < 10] if len(rdf[rdf['max_dd'] < 10]) > 0 else rdf
    best = best_pool.sort_values('pnl', ascending=False).iloc[0]

    print()
    print("=" * 72)
    print(f"ДЕТАЛЬНО — лучший (DD<10% → max PnL):")
    print(f"  SHORT: lookback={best['lookback']} H1 баров, TP={best['tp_short']}R, Risk=${best['risk_short']:.0f}")
    print(f"  LONG:  slope={LONG_SLOPE_N}, TP={LONG_TP_RR}R, Risk=${LONG_RISK:.0f}")
    print(f"  Trades: {best['n']} ({best['long_n']} LONG + {best['short_n']} SHORT)")
    print(f"  WR={best['wr']:.1%}  PnL=${best['pnl']:,.0f}  MaxDD={best['max_dd']:.2f}%  DlyDD={best['max_daily_dd']:.2f}%")
    print(f"  LONG PnL: ${best['long_pnl']:,.0f}   SHORT PnL: ${best['short_pnl']:,.0f}")
    print(f"  Все годы: {'YES' if best['all_pos'] else 'NO'}")

    # Прогнать ещё раз для годового PnL
    r_best = run(df, h4_times, h4_ema20, h1_times, h1_highs, h1_closes,
                 lookback_h1=int(best['lookback']),
                 tp_rr_short=int(best['tp_short']),
                 risk_short=float(best['risk_short']))
    print(f"  PnL по годам:")
    for yr in sorted(r_best['yearly'].keys()):
        v = r_best['yearly'][yr]
        print(f"    {yr}: ${v:>7,.0f}  {'✓' if v > 0 else '✗'}")

    # ── Сравнение с LONG only ──────────────────────────────────────────────────
    print()
    print("=" * 72)
    print("СРАВНЕНИЕ:")
    print(f"  LONG only:       N={r_long['n']}  WR={r_long['wr']:.1%}  PnL=${r_long['pnl']:,.0f}"
          f"  MaxDD={r_long['max_dd']:.2f}%  DlyDD={r_long['max_daily_dd']:.2f}%")
    print(f"  LONG+SHORT best: N={best['n']}  WR={best['wr']:.1%}  PnL=${best['pnl']:,.0f}"
          f"  MaxDD={best['max_dd']:.2f}%  DlyDD={best['max_daily_dd']:.2f}%")
    delta_pnl = best['pnl'] - r_long['pnl']
    delta_dd  = best['max_dd'] - r_long['max_dd']
    print(f"  Δ PnL={delta_pnl:+,.0f}  Δ MaxDD={delta_dd:+.2f}%")
    print("=" * 72)


if __name__ == "__main__":
    main()
