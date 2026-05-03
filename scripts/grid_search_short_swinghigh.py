"""
SHORT Swing High Reversal — сетка параметров.

Концепция:
  Когда H4 бар делает новый максимум за последние N баров (локальный/исторический пик)
  И закрывается медвежьи (close < prev_h4_close) —
  ждём M15 пробоя вниз (close < prev_M15_low) и входим SHORT.

  Нет look-ahead: ptr_closed = последний закрытый H4 (start + 4h <= current_m15_time).
  Сигнал берётся только от h4_highs[ptr_closed], h4_closes[ptr_closed] — закрытые бары.

Grid параметров:
  lookback_h4    : [10, 20, 40, 80]   -- сколько H4 баров назад искать новый пик
  tp_rr          : [3, 5, 7, 10]
  trailing       : [True, False]
  risk_short     : [50, 100]
  signal_expiry  : [5, 12, 24]        -- через сколько H4 баров сигнал устаревает
  require_bearish: [True, False]       -- требовать close < prev_close у пикового бара

Итого: 4 × 4 × 2 × 2 × 3 × 2 = 384 комбинации

Дополнительно: тест комбинации LONG (slope=5, TP=7R, $100) + лучший SHORT swing.
"""
import sys, os, time, itertools
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from session_breakout_trader import (
    ATR_PERIOD, ATR_BUFFER, H4_EMA_PERIOD,
    LONG_SESSIONS, calculate_atr, calculate_ema,
)
import pandas as pd
import numpy as np
from pathlib import Path

K_EMA  = 2.0 / (H4_EMA_PERIOD + 1.0)
H4_NS  = int(4 * 3600 * 1e9)
MIN_H4 = 22

# LONG параметры (для комбинированного теста)
LONG_SLOPE_N = 5
LONG_TP_RR   = 7.0
LONG_ATR_BUF = 0.5
LONG_RISK    = 100.0


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


def run_short_only(df, h4_times, h4_highs, h4_closes, h4_ema20,
                   lookback_h4, tp_rr, trailing, risk_short,
                   signal_expiry, require_bearish):
    """
    Только SHORT swing high.
    Нет look-ahead: сигнал только от ptr_closed (закрытые H4 бары).
    """
    n_h4     = len(h4_times)
    times_ns = df.index.asi8
    m15      = df.to_numpy()
    col      = {c: i for i, c in enumerate(df.columns)}
    i_l = col['low']; i_c = col['close']; i_a = col['atr']; i_h = col['high']

    ptr_closed  = -1
    prev_ptr    = -1   # для отслеживания нового закрытого H4 бара

    short_signal      = False
    short_signal_high = np.nan
    short_signal_bar  = -1  # ptr_closed момента сигнала

    active_short = None
    prev_m15_low = np.nan

    trades       = []
    balance      = 10_000.0
    peak         = 10_000.0
    max_dd       = 0.0
    max_daily_dd = 0.0
    prev_date    = None
    day_start    = balance

    for i in range(len(df)):
        ts_ns  = int(times_ns[i])
        cur_ts = df.index[i]
        high   = float(m15[i, i_h])
        low    = float(m15[i, i_l])
        close  = float(m15[i, i_c])
        atr    = float(m15[i, i_a])

        if np.isnan(atr):
            prev_m15_low = low
            continue

        cur_date = cur_ts.date()
        if cur_date != prev_date:
            if prev_date is not None and day_start > 0:
                dd = (day_start - balance) / day_start * 100
                if dd > max_daily_dd: max_daily_dd = dd
            day_start = balance
            prev_date = cur_date

        # H4 указатель
        prev_ptr = ptr_closed
        while ptr_closed + 1 < n_h4 and h4_times[ptr_closed + 1] + H4_NS <= ts_ns:
            ptr_closed += 1

        new_h4_closed = (ptr_closed > prev_ptr) and (ptr_closed >= lookback_h4)

        # ── управление SHORT (ВСЕГДА) ─────────────────────────────────────────
        if active_short is not None:
            if trailing:
                trail_short(active_short, high)
            if high >= active_short['sl']:
                active_short['pnl'] = (active_short['entry'] - active_short['sl']) * active_short['size']
                balance += active_short['pnl']; active_short['year'] = cur_ts.year
                trades.append(active_short); active_short = None
            elif low <= active_short['tp']:
                active_short['pnl'] = (active_short['entry'] - active_short['tp']) * active_short['size']
                balance += active_short['pnl']; active_short['year'] = cur_ts.year
                trades.append(active_short); active_short = None

        if balance > peak: peak = balance
        dd = (peak - balance) / peak * 100
        if dd > max_dd: max_dd = dd

        # ── новый закрытый H4 — проверяем сигнал ─────────────────────────────
        if new_h4_closed and active_short is None:
            cur_h4_high  = float(h4_highs[ptr_closed])
            cur_h4_close = float(h4_closes[ptr_closed])
            prev_h4_close = float(h4_closes[ptr_closed - 1])

            # Новый максимум за lookback баров
            lookback_slice = h4_highs[ptr_closed - lookback_h4: ptr_closed]
            is_new_high = (len(lookback_slice) > 0) and (cur_h4_high > lookback_slice.max())

            # Медвежье закрытие (опционально)
            is_bearish = (cur_h4_close < prev_h4_close)

            if is_new_high and (not require_bearish or is_bearish):
                short_signal      = True
                short_signal_high = cur_h4_high
                short_signal_bar  = ptr_closed

            # Сброс устаревшего сигнала
            if short_signal and (ptr_closed - short_signal_bar) > signal_expiry:
                short_signal = False

        # ── M15 триггер входа SHORT ──────────────────────────────────────────
        if (short_signal and active_short is None
                and not np.isnan(prev_m15_low)
                and close < prev_m15_low):
            sl_short  = short_signal_high + ATR_BUFFER * atr
            rsk_short = sl_short - close
            if rsk_short > 0:
                active_short = {
                    'entry': close, 'sl': sl_short, 'initial_sl': sl_short,
                    'tp': close - rsk_short * tp_rr,
                    'size': risk_short / rsk_short,
                    'direction': 'SHORT', 'session': 'swing',
                }
                short_signal = False

        prev_m15_low = low

    # последний день
    if prev_date is not None and day_start > 0:
        dd = (day_start - balance) / day_start * 100
        if dd > max_daily_dd: max_daily_dd = dd

    # незавершённые
    last_close = float(m15[-1, i_c])
    last_year  = int(df.index[-1].year)
    if active_short is not None:
        active_short['pnl'] = (active_short['entry'] - last_close) * active_short['size']
        balance += active_short['pnl']; active_short['year'] = last_year; trades.append(active_short)

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


def run_combined(df, h4_times, h4_highs, h4_closes, h4_ema20,
                 best_short_params):
    """LONG (slope=5, TP=7R, $100) + лучший SHORT swing — общий баланс."""
    n_h4     = len(h4_times)
    times_ns = df.index.asi8
    m15      = df.to_numpy()
    col      = {c: i for i, c in enumerate(df.columns)}
    i_h = col['high']; i_l = col['low']; i_c = col['close']; i_a = col['atr']

    lookback_h4   = best_short_params['lookback_h4']
    tp_rr_s       = best_short_params['tp_rr']
    trailing_s    = best_short_params['trailing']
    risk_short    = best_short_params['risk_short']
    signal_expiry = best_short_params['signal_expiry']
    req_bearish   = best_short_params['require_bearish']

    ptr_closed     = -1
    prev_ptr       = -1
    forming_period = -1
    forming_close  = np.nan
    ema_base       = np.nan

    short_signal      = False
    short_signal_high = np.nan
    short_signal_bar  = -1
    active_short      = None
    active_long       = {}
    prev_m15_low      = np.nan

    trades       = []
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
            prev_m15_low = low
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

        prev_ptr = ptr_closed
        while ptr_closed + 1 < n_h4 and h4_times[ptr_closed + 1] + H4_NS <= ts_ns:
            ptr_closed += 1

        new_h4_closed = (ptr_closed > prev_ptr) and (ptr_closed >= lookback_h4)

        if ptr_closed < MIN_H4 - 1:
            for sn, p in LONG_SESSIONS.items():
                sh, eh = p['range_hours']
                if sh <= hour < eh:
                    s_highs[sn] = max(s_highs.get(sn, 0),   high)
                    s_lows[sn]  = min(s_lows.get(sn, 1e9),  low)
            prev_m15_low = low
            continue

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
        ema_ok   = (forming_close > ema_base)
        slope_ok = (ptr_closed >= LONG_SLOPE_N) \
                   and (not np.isnan(h4_ema20[ptr_closed - LONG_SLOPE_N])) \
                   and (h4_ema > h4_ema20[ptr_closed - LONG_SLOPE_N])

        # ── управление LONG (ВСЕГДА) ─────────────────────────────────────────
        for sn in list(active_long.keys()):
            t = active_long[sn]
            trail_long(t, low)
            if low <= t['sl']:
                t['pnl'] = (t['sl'] - t['entry']) * t['size']
                balance += t['pnl']; t['year'] = cur_ts.year
                trades.append(dict(t, direction='LONG')); del active_long[sn]
            elif high >= t['tp']:
                t['pnl'] = (t['tp'] - t['entry']) * t['size']
                balance += t['pnl']; t['year'] = cur_ts.year
                trades.append(dict(t, direction='LONG')); del active_long[sn]

        # ── управление SHORT (ВСЕГДА) ─────────────────────────────────────────
        if active_short is not None:
            if trailing_s:
                trail_short(active_short, high)
            if high >= active_short['sl']:
                active_short['pnl'] = (active_short['entry'] - active_short['sl']) * active_short['size']
                balance += active_short['pnl']; active_short['year'] = cur_ts.year
                trades.append(active_short); active_short = None
            elif low <= active_short['tp']:
                active_short['pnl'] = (active_short['entry'] - active_short['tp']) * active_short['size']
                balance += active_short['pnl']; active_short['year'] = cur_ts.year
                trades.append(active_short); active_short = None

        if balance > peak: peak = balance
        dd = (peak - balance) / peak * 100
        if dd > max_dd: max_dd = dd

        # ── диапазоны сессий ──────────────────────────────────────────────────
        for sn, p in LONG_SESSIONS.items():
            sh, eh = p['range_hours']
            if sh <= hour < eh:
                s_highs[sn] = max(s_highs.get(sn, 0),   high)
                s_lows[sn]  = min(s_lows.get(sn, 1e9),  low)

        # ── SHORT swing high сигнал ───────────────────────────────────────────
        if new_h4_closed and active_short is None:
            cur_h4_high   = float(h4_highs[ptr_closed])
            cur_h4_close  = float(h4_closes[ptr_closed])
            prev_h4_close = float(h4_closes[ptr_closed - 1])
            lookback_slice = h4_highs[ptr_closed - lookback_h4: ptr_closed]
            is_new_high  = (len(lookback_slice) > 0) and (cur_h4_high > lookback_slice.max())
            is_bearish   = (cur_h4_close < prev_h4_close)
            if is_new_high and (not req_bearish or is_bearish):
                short_signal = True; short_signal_high = cur_h4_high; short_signal_bar = ptr_closed
            if short_signal and (ptr_closed - short_signal_bar) > signal_expiry:
                short_signal = False

        # ── SHORT M15 вход ───────────────────────────────────────────────────
        if (short_signal and active_short is None
                and not np.isnan(prev_m15_low) and close < prev_m15_low):
            sl_s  = short_signal_high + ATR_BUFFER * atr
            rsk_s = sl_s - close
            if rsk_s > 0:
                active_short = {
                    'entry': close, 'sl': sl_s, 'initial_sl': sl_s,
                    'tp': close - rsk_s * tp_rr_s,
                    'size': risk_short / rsk_s,
                    'direction': 'SHORT', 'session': 'swing',
                }
                short_signal = False

        # ── LONG входы ────────────────────────────────────────────────────────
        if ema_ok and slope_ok:
            for sn, p in LONG_SESSIONS.items():
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
                        'size': LONG_RISK / rsk, 'session': sn,
                    }

        prev_m15_low = low

    if prev_date is not None and day_start > 0:
        dd = (day_start - balance) / day_start * 100
        if dd > max_daily_dd: max_daily_dd = dd

    last_close = float(m15[-1, i_c])
    last_year  = int(df.index[-1].year)
    for sn, t in active_long.items():
        t['pnl'] = (last_close - t['entry']) * t['size']
        balance += t['pnl']; t['year'] = last_year; trades.append(dict(t, direction='LONG'))
    if active_short is not None:
        active_short['pnl'] = (active_short['entry'] - last_close) * active_short['size']
        balance += active_short['pnl']; active_short['year'] = last_year; trades.append(active_short)

    tdf    = pd.DataFrame(trades) if trades else pd.DataFrame(columns=['pnl','year','direction'])
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
        'all_pos': bool(all(yearly > 0)) if len(yearly) > 0 else False,
        'long_n': len(long_df), 'long_pnl': float(long_df['pnl'].sum()) if len(long_df) > 0 else 0.0,
        'short_n': len(short_df), 'short_pnl': float(short_df['pnl'].sum()) if len(short_df) > 0 else 0.0,
    }


def main():
    print("=" * 72)
    print("SHORT Swing High Reversal — Grid Search")
    print("Сигнал: новый H4 максимум за N баров + (опц.) медвежье закрытие")
    print("Вход: M15 пробой предыдущего минимума вниз")
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
    h4_times  = df_h4.index.asi8
    h4_highs  = df_h4['high'].to_numpy()
    h4_closes = df_h4['close'].to_numpy()
    h4_ema20  = df_h4['ema20'].to_numpy()

    print(f"Данные: {df.index[0].date()} -- {df.index[-1].date()}  ({len(df):,} M15 баров)")
    print(f"H4 баров: {len(df_h4)}")

    param_grid = {
        'lookback_h4':    [10, 20, 40, 80],
        'tp_rr':          [3, 5, 7, 10],
        'trailing':       [True, False],
        'risk_short':     [50, 100],
        'signal_expiry':  [5, 12, 24],
        'require_bearish':[True, False],
    }
    combos = list(itertools.product(*param_grid.values()))
    keys   = list(param_grid.keys())
    total  = len(combos)

    print(f"\nВсего комбинаций: {total}\n")

    results   = []
    t0        = time.time()
    best_pnl  = -1e9
    best_row  = None

    for idx, vals in enumerate(combos, 1):
        params = dict(zip(keys, vals))
        r = run_short_only(df, h4_times, h4_highs, h4_closes, h4_ema20, **params)
        row = {**params, **r}
        results.append(row)

        if r['max_dd'] < 10 and r['all_pos'] and r['pnl'] > best_pnl:
            best_pnl = r['pnl']
            best_row = row

        if idx % 30 == 0 or idx == total:
            elapsed = time.time() - t0
            eta     = elapsed / idx * (total - idx)
            best_s  = f"  best_DD<10%=${best_pnl:,.0f}" if best_pnl > -1e9 else ""
            print(f"  [{idx:3d}/{total}]  elapsed={elapsed:.0f}s  ETA={eta:.0f}s{best_s}",
                  flush=True)

    rdf = pd.DataFrame(results)
    elapsed = time.time() - t0
    print(f"\nГотово за {elapsed:.0f} сек.\n")

    # ── ТОП результаты ────────────────────────────────────────────────────────
    good = rdf[(rdf['max_dd'] < 10) & (rdf['max_daily_dd'] < 5) & rdf['all_pos']]
    print("=" * 80)
    print(f"SHORT ONLY — ТОП (DD<10%, DlyDD<5%, все годы)  [{len(good)} комб.]")
    print("=" * 80)
    hdr = f"  {'Lb':>4} {'TP':>3} {'Trl':>4} {'Risk':>5} {'Exp':>4} {'Brsh':>5}  {'N':>4}  {'WR':>6}  {'PnL':>9}  {'MaxDD':>7}  {'DlyDD':>6}  AllY"
    print(hdr)
    print("  " + "-" * 78)

    def print_row(row, mark=''):
        dd_s = "<10%+" if row['max_dd'] < 10 else f"{row['max_dd']:.2f}%"
        ay   = 'YES' if row['all_pos'] else 'NO '
        t_s  = 'Y' if row['trailing'] else 'N'
        b_s  = 'Y' if row['require_bearish'] else 'N'
        print(f"  {row['lookback_h4']:>4.0f} {row['tp_rr']:>3.0f}R  {t_s:>3}"
              f"  ${row['risk_short']:>4.0f}  {row['signal_expiry']:>4.0f}  {b_s:>4}"
              f"  {row['n']:>4.0f}  {row['wr']:>5.1%}  ${row['pnl']:>8,.0f}"
              f"  {dd_s:>7}  {row['max_daily_dd']:>5.2f}%  {ay}{mark}")

    if len(good) > 0:
        for _, row in good.sort_values('pnl', ascending=False).head(20).iterrows():
            print_row(row, '  ***')
    else:
        print("  (нет комбинаций с DD<10%, DlyDD<5%, все годы в плюс)")
        print("  Лучшие по PnL:")
        for _, row in rdf.sort_values('pnl', ascending=False).head(20).iterrows():
            print_row(row)

    # ── Влияние параметров ────────────────────────────────────────────────────
    print()
    print("=" * 72)
    print("ВЛИЯНИЕ КАЖДОГО ПАРАМЕТРА")
    print("=" * 72)
    for param in keys:
        print(f"\n  {param}:")
        for val, grp in rdf.groupby(param):
            good_cnt = ((grp['max_dd'] < 10) & grp['all_pos']).sum()
            print(f"    {str(val):>8}:  PnL_med=${grp['pnl'].median():>8,.0f}"
                  f"  DD_med={grp['max_dd'].median():>6.2f}%"
                  f"  PnL_max=${grp['pnl'].max():>8,.0f}"
                  f"  DD<10%: {good_cnt} комб.")

    # ── Детально лучший ───────────────────────────────────────────────────────
    pool = good if len(good) > 0 else rdf.sort_values('pnl', ascending=False)
    best = pool.sort_values('pnl', ascending=False).iloc[0]

    print()
    print("=" * 72)
    print("ДЕТАЛЬНО SHORT ONLY — лучший:")
    print(f"  lookback={best['lookback_h4']:.0f} H4 баров"
          f"  TP={best['tp_rr']:.0f}R  Trailing={'Y' if best['trailing'] else 'N'}"
          f"  Risk=${best['risk_short']:.0f}  Expiry={best['signal_expiry']:.0f}  Bearish={'Y' if best['require_bearish'] else 'N'}")
    print(f"  N={best['n']:.0f}  WR={best['wr']:.1%}  PnL=${best['pnl']:,.0f}"
          f"  MaxDD={best['max_dd']:.2f}%  DlyDD={best['max_daily_dd']:.2f}%"
          f"  AllYrs={'YES' if best['all_pos'] else 'NO'}")

    r_best = run_short_only(df, h4_times, h4_highs, h4_closes, h4_ema20,
                            lookback_h4=int(best['lookback_h4']),
                            tp_rr=float(best['tp_rr']),
                            trailing=bool(best['trailing']),
                            risk_short=float(best['risk_short']),
                            signal_expiry=int(best['signal_expiry']),
                            require_bearish=bool(best['require_bearish']))
    print("  PnL по годам:")
    for yr in sorted(r_best['yearly'].index):
        v = r_best['yearly'][yr]
        print(f"    {yr}: ${v:>8,.0f}  {'OK' if v > 0 else 'LOSS'}")

    # ── Комбинированный тест LONG + лучший SHORT ──────────────────────────────
    print()
    print("=" * 72)
    print("КОМБИНИРОВАННЫЙ ТЕСТ: LONG (slope=5, TP=7R, $100) + SHORT swing")
    print("=" * 72)
    best_params = {
        'lookback_h4':    int(best['lookback_h4']),
        'tp_rr':          float(best['tp_rr']),
        'trailing':       bool(best['trailing']),
        'risk_short':     float(best['risk_short']),
        'signal_expiry':  int(best['signal_expiry']),
        'require_bearish':bool(best['require_bearish']),
    }
    rc = run_combined(df, h4_times, h4_highs, h4_closes, h4_ema20, best_params)
    print(f"  Trades: {rc['n']} ({rc['long_n']} LONG + {rc['short_n']} SHORT)")
    print(f"  WR={rc['wr']:.1%}  PnL=${rc['pnl']:,.0f}  MaxDD={rc['max_dd']:.2f}%  DlyDD={rc['max_daily_dd']:.2f}%")
    print(f"  LONG PnL: ${rc['long_pnl']:,.0f}   SHORT PnL: ${rc['short_pnl']:,.0f}")
    print(f"  AllYrs: {'YES' if rc['all_pos'] else 'NO'}")
    print("  PnL по годам:")
    for yr in sorted(rc['yearly'].index):
        v = rc['yearly'][yr]
        print(f"    {yr}: ${v:>8,.0f}  {'OK' if v > 0 else 'LOSS'}")
    print()
    print(f"  LONG only:    PnL=$35,928  MaxDD=9.88%")
    print(f"  LONG+SHORT:   PnL=${rc['pnl']:,.0f}  MaxDD={rc['max_dd']:.2f}%")
    print(f"  SHORT добавил: ${rc['pnl'] - 35928:+,.0f}")
    print("=" * 72)


if __name__ == "__main__":
    main()
