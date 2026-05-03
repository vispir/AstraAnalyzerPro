"""
EA Mirror LONG only (+ SHORT для комбо 4) — тест 4 комбинаций:

  1. EMA slope 3 бара + Risk=$100  (LONG only)
  2. EMA slope 5 баров + Risk=$120 (LONG only)
  3. EMA slope 5 баров + Risk=$100 (LONG only)
  4. EMA slope 3 бара + Risk=$120  (LONG + SHORT)

Baseline: 699 сделок, WR=42.9%, PnL=$46,721, MaxDD=16.60%  (Risk=$120, без фильтра)
Цель: MaxDD < 10%, PnL максимальный.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from session_breakout_trader import (
    TP_RR, ATR_PERIOD, ATR_BUFFER,
    USE_H4_EMA_FILTER, H4_EMA_PERIOD,
    LONG_SESSIONS,
    SHORT_TYPE1_LOOKBACK_H4_BARS, SHORT_TYPE2_H4_LOOKBACK, SHORT_TYPE2_ATR_MULTIPLIER,
    calculate_atr, calculate_ema,
)
import pandas as pd
import numpy as np
from pathlib import Path

K_EMA  = 2.0 / (H4_EMA_PERIOD + 1.0)
H4_NS  = int(4 * 3600 * 1e9)
MIN_H4 = max(SHORT_TYPE1_LOOKBACK_H4_BARS + 2, SHORT_TYPE2_H4_LOOKBACK + 2, 22)


def floor4h_ns(ts_ns: int) -> int:
    return int(ts_ns // int(14400 * 1e9)) * int(14400 * 1e9)


def apply_step_trailing(t, low, high, is_long):
    risk = t['entry'] - t['initial_sl'] if is_long else t['initial_sl'] - t['entry']
    ref  = low if is_long else high
    rr   = (ref - t['entry']) / risk if is_long else (t['entry'] - ref) / risk
    for trigger, lock in ((5, 4), (4, 3), (3, 2), (2, 1)):
        if rr >= trigger:
            ns = t['entry'] + lock * risk if is_long else t['entry'] - lock * risk
            t['sl'] = max(t['sl'], ns) if is_long else min(t['sl'], ns)
            break


def run_combo(df, h4_times, h4_high, h4_low, h4_close, h4_atr, h4_ema20,
              risk, slope_bars, with_short):
    """
    risk       — риск на сделку в $
    slope_bars — сколько H4 баров назад сравнивать EMA для slope-фильтра
    with_short — включить SHORT state machine
    """
    n_h4 = len(h4_times)

    m15_arr  = df.to_numpy()
    cols     = {c: i for i, c in enumerate(df.columns)}
    i_high   = cols['high']; i_low = cols['low']
    i_close  = cols['close']; i_atr = cols['atr']
    times_ns = df.index.asi8
    hours    = np.array([t.hour for t in df.index])

    ptr_closed = -1

    forming_period = -1
    forming_close  = np.nan
    ema_base       = np.nan

    # SHORT state machine
    t1_active = False;  t1_h4_high = 0.0
    t2_active = False;  t2_h4_high = 0.0
    last_short_h4_ptr = -1

    trades        = []
    active_long   = {}
    active_short  = None
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
        high   = float(m15_arr[i, i_high])
        low    = float(m15_arr[i, i_low])
        close  = float(m15_arr[i, i_close])
        atr    = float(m15_arr[i, i_atr])
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

        # Формирующийся H4 бар
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

        # ── EMA slope фильтр ────────────────────────────────────────────────
        if ptr_closed < slope_bars:
            for sn, p in LONG_SESSIONS.items():
                sh, eh = p['range_hours']
                if sh <= hour < eh:
                    session_highs[sn] = max(session_highs.get(sn, 0), high)
                    session_lows[sn]  = min(session_lows.get(sn, 1e9), low)
            continue
        ema_ago = h4_ema20[ptr_closed - slope_bars]
        slope_ok = not np.isnan(ema_ago) and h4_ema > ema_ago

        # ── Управление LONG сделками ─────────────────────────────────────────
        for sn in list(active_long.keys()):
            t = active_long[sn]
            apply_step_trailing(t, low, high, is_long=True)
            if low <= t['sl']:
                t['pnl'] = (t['sl'] - t['entry']) * t['size']
                balance += t['pnl']; t['year'] = cur_ts.year
                trades.append(t); del active_long[sn]
            elif high >= t['tp']:
                t['pnl'] = (t['tp'] - t['entry']) * t['size']
                balance += t['pnl']; t['year'] = cur_ts.year
                trades.append(t); del active_long[sn]

        # ── Управление SHORT сделкой ─────────────────────────────────────────
        if active_short is not None:
            apply_step_trailing(active_short, low, high, is_long=False)
            if high >= active_short['sl']:
                active_short['pnl'] = (active_short['entry'] - active_short['sl']) * active_short['size']
                balance += active_short['pnl']; active_short['year'] = cur_ts.year
                trades.append(active_short); active_short = None
                t1_active = False; t2_active = False
            elif low <= active_short['tp']:
                active_short['pnl'] = (active_short['entry'] - active_short['tp']) * active_short['size']
                balance += active_short['pnl']; active_short['year'] = cur_ts.year
                trades.append(active_short); active_short = None
                t1_active = False; t2_active = False

        if balance > peak: peak = balance
        dd = (peak - balance) / peak * 100
        if dd > max_dd: max_dd = dd

        # ── Диапазоны сессий ─────────────────────────────────────────────────
        for sn, p in LONG_SESSIONS.items():
            sh, eh = p['range_hours']
            if sh <= hour < eh:
                session_highs[sn] = max(session_highs.get(sn, 0), high)
                session_lows[sn]  = min(session_lows.get(sn, 1e9), low)

        # ── LONG входы (только если slope_ok) ───────────────────────────────
        if slope_ok:
            for sn, p in LONG_SESSIONS.items():
                if sn not in session_highs or sn in active_long:
                    continue
                if not (p['entry_start'] <= hour < p['entry_end']):
                    continue
                if close > session_highs[sn]:
                    if USE_H4_EMA_FILTER:
                        if np.isnan(h4_ema) or forming_close <= h4_ema:
                            continue
                    sl   = session_lows[sn] - ATR_BUFFER * atr
                    rsk  = close - sl
                    if rsk <= 0: continue
                    active_long[sn] = {
                        'entry': close, 'sl': sl, 'initial_sl': sl,
                        'tp': close + rsk * TP_RR,
                        'size': risk / rsk,
                        'direction': 'LONG', 'session': sn,
                    }

        # ── SHORT state machine (закрытый H4, как в EA) ─────────────────────
        if with_short and active_short is None and hour < 21:
            if last_short_h4_ptr != ptr_closed:
                last_short_h4_ptr = ptr_closed

                short_ema   = h4_ema20[ptr_closed]
                short_close = h4_close[ptr_closed]

                if USE_H4_EMA_FILTER:
                    if np.isnan(short_ema) or short_close >= short_ema:
                        t1_active = False; t2_active = False
                    else:
                        cur_h  = h4_high[ptr_closed]
                        cur_c  = h4_close[ptr_closed]
                        prev_c = h4_close[ptr_closed - 1]

                        if not t1_active and ptr_closed >= SHORT_TYPE1_LOOKBACK_H4_BARS + 1:
                            lb_max = h4_high[ptr_closed - SHORT_TYPE1_LOOKBACK_H4_BARS:ptr_closed].max()
                            if cur_h > lb_max and cur_c < prev_c:
                                t1_active = True; t1_h4_high = cur_h

                        if not t2_active and ptr_closed >= SHORT_TYPE2_H4_LOOKBACK + 1:
                            move = cur_h - h4_low[ptr_closed - SHORT_TYPE2_H4_LOOKBACK:ptr_closed].min()
                            h4a  = h4_atr[ptr_closed]
                            if not np.isnan(h4a) and move >= SHORT_TYPE2_ATR_MULTIPLIER * h4a:
                                if cur_c < prev_c:
                                    t2_active = True; t2_h4_high = cur_h
                else:
                    cur_h  = h4_high[ptr_closed]
                    cur_c  = h4_close[ptr_closed]
                    prev_c = h4_close[ptr_closed - 1]
                    if not t1_active and ptr_closed >= SHORT_TYPE1_LOOKBACK_H4_BARS + 1:
                        lb_max = h4_high[ptr_closed - SHORT_TYPE1_LOOKBACK_H4_BARS:ptr_closed].max()
                        if cur_h > lb_max and cur_c < prev_c:
                            t1_active = True; t1_h4_high = cur_h
                    if not t2_active and ptr_closed >= SHORT_TYPE2_H4_LOOKBACK + 1:
                        move = cur_h - h4_low[ptr_closed - SHORT_TYPE2_H4_LOOKBACK:ptr_closed].min()
                        h4a  = h4_atr[ptr_closed]
                        if not np.isnan(h4a) and move >= SHORT_TYPE2_ATR_MULTIPLIER * h4a:
                            if cur_c < prev_c:
                                t2_active = True; t2_h4_high = cur_h

            if i > 0:
                prev_low_m15 = float(m15_arr[i - 1, i_low])
                if t1_active and close < prev_low_m15:
                    sl = t1_h4_high + ATR_BUFFER * atr
                    rsk = sl - close
                    if rsk > 0:
                        active_short = {
                            'entry': close, 'sl': sl, 'initial_sl': sl,
                            'tp': close - rsk * TP_RR,
                            'size': risk / rsk,
                            'direction': 'SHORT', 'session': 'short',
                        }
                        t1_active = False
                elif t2_active and close < prev_low_m15:
                    sl = t2_h4_high + ATR_BUFFER * atr
                    rsk = sl - close
                    if rsk > 0:
                        active_short = {
                            'entry': close, 'sl': sl, 'initial_sl': sl,
                            'tp': close - rsk * TP_RR,
                            'size': risk / rsk,
                            'direction': 'SHORT', 'session': 'short',
                        }
                        t2_active = False

    if prev_date is not None and day_start_bal > 0:
        ddaily = (day_start_bal - balance) / day_start_bal * 100
        if ddaily > max_daily_dd: max_daily_dd = ddaily

    last_close = float(m15_arr[-1, i_close])
    last_year  = int(df.index[-1].year)
    for sn, t in active_long.items():
        t['pnl'] = (last_close - t['entry']) * t['size']
        balance += t['pnl']; t['year'] = last_year; trades.append(t)
    if active_short is not None:
        active_short['pnl'] = (active_short['entry'] - last_close) * active_short['size']
        balance += active_short['pnl']; active_short['year'] = last_year; trades.append(active_short)

    tdf    = pd.DataFrame(trades)
    pnl    = balance - 10_000
    wr     = (tdf['pnl'] > 0).sum() / len(tdf) if len(tdf) > 0 else 0
    yearly = tdf.groupby('year')['pnl'].sum() if len(tdf) > 0 else pd.Series(dtype=float)
    long_df  = tdf[tdf['direction'] == 'LONG']  if len(tdf) > 0 else tdf
    short_df = tdf[tdf['direction'] == 'SHORT'] if len(tdf) > 0 else tdf

    return {
        'n': len(tdf), 'wr': wr, 'pnl': pnl,
        'max_dd': max_dd, 'max_daily_dd': max_daily_dd,
        'yearly': yearly,
        'long_n':  len(long_df),  'long_pnl':  float(long_df['pnl'].sum())  if len(long_df)  > 0 else 0,
        'short_n': len(short_df), 'short_pnl': float(short_df['pnl'].sum()) if len(short_df) > 0 else 0,
        'all_pos': all(yearly > 0) if len(yearly) > 0 else False,
    }


def run():
    print("=" * 70)
    print("EA MIRROR — ТЕСТ КОМБИНАЦИЙ (EMA slope + Risk + SHORT)")
    print("=" * 70)

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
    df_h4['atr']   = calculate_atr(df_h4, ATR_PERIOD)
    df_h4['ema20'] = calculate_ema(df_h4, H4_EMA_PERIOD)

    h4_times = df_h4.index.asi8
    h4_high  = df_h4['high'].to_numpy()
    h4_low   = df_h4['low'].to_numpy()
    h4_close = df_h4['close'].to_numpy()
    h4_atr   = df_h4['atr'].to_numpy()
    h4_ema20 = df_h4['ema20'].to_numpy()

    combos = [
        # (label, risk, slope_bars, with_short)
        ("1. slope-3 + Risk=$100 (LONG only)",  100, 3, False),
        ("2. slope-5 + Risk=$120 (LONG only)",  120, 5, False),
        ("3. slope-5 + Risk=$100 (LONG only)",  100, 5, False),
        ("4. slope-3 + Risk=$120 + SHORT",       120, 3, True),
    ]

    results = []
    for label, risk, slope, short in combos:
        print(f"  Running [{label}]...", flush=True)
        r = run_combo(df, h4_times, h4_high, h4_low, h4_close, h4_atr, h4_ema20,
                      risk=risk, slope_bars=slope, with_short=short)
        results.append((label, risk, slope, short, r))
        print(f"    {r['n']} trades  WR={r['wr']:.1%}  PnL=${r['pnl']:,.0f}  DD={r['max_dd']:.2f}%")

    # ── Детальный вывод ─────────────────────────────────────────────────────
    print()
    BASELINE = {'n': 699, 'wr': 0.429, 'pnl': 46721, 'max_dd': 16.60, 'max_daily_dd': 3.69}

    for label, risk, slope, short, r in results:
        print(f"\n{'='*65}")
        print(f"  {label}")
        print(f"{'='*65}")
        print(f"  Trades:   {r['n']:>4}  (baseline 699)")
        print(f"  WinRate:  {r['wr']:.1%}  (baseline 42.9%)")
        if short:
            print(f"  LONG:     {r['long_n']} trades  ${r['long_pnl']:,.0f}")
            print(f"  SHORT:    {r['short_n']} trades  ${r['short_pnl']:,.0f}")
        print(f"  PnL:     ${r['pnl']:>8,.0f}  (baseline $46,721)")
        print(f"  MaxDD:    {r['max_dd']:.2f}%  {'[OK < 10%]' if r['max_dd'] < 10 else '[FAIL > 10%]'}  (baseline 16.60%)")
        print(f"  DailyDD:  {r['max_daily_dd']:.2f}%")
        print(f"  All years profitable: {'YES' if r['all_pos'] else 'NO'}")
        print(f"  PnL by year:")
        for yr in sorted(r['yearly'].keys()):
            v  = r['yearly'][yr]
            mk = ' ✓' if v > 0 else ' ✗'
            print(f"    {yr}: ${v:>7,.0f}{mk}")

    # ── Сводная таблица ─────────────────────────────────────────────────────
    print()
    print("=" * 70)
    print("СВОДНАЯ ТАБЛИЦА")
    print("=" * 70)
    hdr = f"  {'Комбинация':<42} {'N':>5}  {'WR':>6}  {'PnL':>9}  {'MaxDD':>7}  {'DD<10%':>7}"
    print(hdr)
    print("  " + "-" * 67)
    # baseline
    print(f"  {'Baseline ($120, без фильтра)':<42} {699:>5}  {42.9:>5.1f}%  ${46721:>8,}  {16.60:>6.2f}%  {'NO':>7}")
    for label, risk, slope, short, r in results:
        ok = 'YES ✓' if r['max_dd'] < 10 else 'NO  ✗'
        lbl = label[:42]
        print(f"  {lbl:<42} {r['n']:>5}  {r['wr']:>5.1%}  ${r['pnl']:>8,.0f}  {r['max_dd']:>6.2f}%  {ok}")
    print("=" * 70)


if __name__ == "__main__":
    run()
