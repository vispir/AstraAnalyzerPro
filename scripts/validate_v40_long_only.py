"""
EA Mirror бэктест — LONG ONLY, без SHORT.
Запускается для Risk=$80 и Risk=$100.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from session_breakout_trader import (
    TP_RR, ATR_PERIOD, ATR_BUFFER,
    USE_H4_EMA_FILTER, H4_EMA_PERIOD,
    LONG_SESSIONS,
    calculate_atr, calculate_ema,
)

import pandas as pd
import numpy as np
from pathlib import Path

K_EMA = 2.0 / (H4_EMA_PERIOD + 1.0)


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


def run_long_only(df_m15, h4_times, h4_close, h4_atr, h4_ema20, risk_per_trade):
    H4_NS   = int(4 * 3600 * 1e9)
    MIN_H4  = 22   # для EMA20 нужно ~20 H4 баров

    n_h4      = len(h4_times)
    ptr_closed = -1

    trades       = []
    active_long  = {}
    balance      = 10_000.0
    peak         = 10_000.0
    max_dd       = 0.0
    max_daily_dd = 0.0

    m15_arr  = df_m15.to_numpy()
    cols     = {c: i for i, c in enumerate(df_m15.columns)}
    idx_high  = cols['high']
    idx_low   = cols['low']
    idx_close = cols['close']
    idx_atr   = cols['atr']
    times_ns  = df_m15.index.asi8
    hours_arr = np.array([t.hour for t in df_m15.index])

    forming_period = -1
    forming_close  = np.nan
    ema_base       = np.nan

    prev_date      = None
    day_start_bal  = balance
    session_highs  = {}
    session_lows   = {}

    for i in range(len(df_m15)):
        ts_ns = int(times_ns[i])
        cur_ts = df_m15.index[i]
        hour   = int(hours_arr[i])
        high   = float(m15_arr[i, idx_high])
        low    = float(m15_arr[i, idx_low])
        close  = float(m15_arr[i, idx_close])
        atr    = float(m15_arr[i, idx_atr])
        if np.isnan(atr):
            continue

        # Daily tracking
        cur_date = cur_ts.date()
        if cur_date != prev_date:
            if prev_date is not None and day_start_bal > 0:
                ddaily = (day_start_bal - balance) / day_start_bal * 100
                if ddaily > max_daily_dd: max_daily_dd = ddaily
            day_start_bal = balance
            prev_date     = cur_date
            session_highs = {}
            session_lows  = {}

        # Продвинуть ptr_closed
        while ptr_closed + 1 < n_h4 and h4_times[ptr_closed + 1] + H4_NS <= ts_ns:
            ptr_closed += 1

        if ptr_closed < MIN_H4 - 1:
            continue

        # Формирующийся H4 бар (ea_mirror)
        h4_period = floor4h_ns(ts_ns)
        if h4_period != forming_period:
            forming_period = h4_period
            forming_close  = close
            ema_base = h4_ema20[ptr_closed] if ptr_closed >= 0 else np.nan
        else:
            forming_close = close

        if np.isnan(ema_base):
            continue

        # EMA с формирующимся баром (один шаг от ema_base)
        h4_ema = forming_close * K_EMA + ema_base * (1.0 - K_EMA)

        # Управление LONG сделками
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

        # DD
        if balance > peak: peak = balance
        dd = (peak - balance) / peak * 100
        if dd > max_dd: max_dd = dd

        # Диапазоны сессий
        for sn, p in LONG_SESSIONS.items():
            sh, eh = p['range_hours']
            if sh <= hour < eh:
                session_highs[sn] = max(session_highs.get(sn, 0),   high)
                session_lows[sn]  = min(session_lows.get(sn,  1e9), low)

        # LONG входы
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
                risk = close - sl
                if risk <= 0: continue
                active_long[sn] = {
                    'entry': close, 'sl': sl, 'initial_sl': sl,
                    'tp': close + risk * TP_RR,
                    'size': risk_per_trade / risk,
                    'direction': 'LONG', 'session': sn,
                }

    # Последний день
    if prev_date is not None and day_start_bal > 0:
        ddaily = (day_start_bal - balance) / day_start_bal * 100
        if ddaily > max_daily_dd: max_daily_dd = ddaily

    # Закрыть остатки
    last_close = float(m15_arr[-1, idx_close])
    last_year  = int(df_m15.index[-1].year)
    for sn, t in active_long.items():
        t['pnl'] = (last_close - t['entry']) * t['size']
        balance += t['pnl']; t['year'] = last_year; trades.append(t)

    tdf = pd.DataFrame(trades)
    total_pnl = balance - 10_000
    wr        = (tdf['pnl'] > 0).sum() / len(tdf) if len(tdf) > 0 else 0
    yearly    = tdf.groupby('year')['pnl'].sum() if len(tdf) > 0 else pd.Series(dtype=float)

    return {
        'total': len(tdf), 'wr': wr, 'pnl': total_pnl,
        'max_dd': max_dd, 'max_daily_dd': max_daily_dd,
        'yearly': yearly,
    }


def run():
    print("=" * 62)
    print("EA MIRROR — LONG ONLY  (SHORT отключён)")
    print("=" * 62)

    data_path = (
        Path(__file__).parent.parent
        / "data_cache" / "dukascopy" / "m15" / "XAUUSD"
        / "xauusd_m15_2020-01-01_2026-04-18.parquet"
    )
    df = pd.read_parquet(data_path).sort_index()
    print(f"Period: {df.index[0].date()} -- {df.index[-1].date()}")
    print(f"TP: {TP_RR}R  ATR: {ATR_PERIOD}  H4 EMA{H4_EMA_PERIOD} filter: {USE_H4_EMA_FILTER}")
    print()

    df['atr'] = calculate_atr(df, ATR_PERIOD)

    df_h4 = df.resample('4h').agg(
        {'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last'}
    ).dropna()
    df_h4['atr']   = calculate_atr(df_h4, ATR_PERIOD)
    df_h4['ema20'] = calculate_ema(df_h4, H4_EMA_PERIOD)

    h4_times = df_h4.index.asi8
    h4_close = df_h4['close'].to_numpy()
    h4_atr   = df_h4['atr'].to_numpy()
    h4_ema20 = df_h4['ema20'].to_numpy()

    results = {}
    for risk in (80, 100, 120):
        print(f"  Running Risk=${risk}...", flush=True)
        results[risk] = run_long_only(df, h4_times, h4_close, h4_atr, h4_ema20, risk)

    print()
    print("=" * 62)
    print("РЕЗУЛЬТАТЫ")
    print("=" * 62)

    ref = {
        'total': 884, 'pnl': 79721, 'max_dd': 6.99, 'max_daily_dd': 3.28,
        'yearly': {2020: 12240, 2021: 11580, 2022: 9000,
                   2023: 15300, 2024: 9360, 2025: 16200, 2026: 6821}
    }

    for risk, r in results.items():
        ay      = all(r['yearly'] > 0)
        dd_ok   = r['max_dd'] < 10.0
        ddd_ok  = r['max_daily_dd'] < 5.0
        print(f"\n{'='*50}")
        print(f"  Risk = ${risk}")
        print(f"{'='*50}")
        print(f"  Trades:      {r['total']}")
        print(f"  Win Rate:    {r['wr']:.1%}")
        print(f"  Total PnL:   ${r['pnl']:,.0f}")
        print(f"  Max DD:      {r['max_dd']:.2f}%  {'< 10% OK' if dd_ok else '> 10% FAIL'}")
        print(f"  Max Daily DD:{r['max_daily_dd']:.2f}%  {'< 5% OK' if ddd_ok else '> 5% FAIL'}")
        print(f"  All years profitable: {'YES' if ay else 'NO'}")
        print(f"  PnL by year:")
        for yr in sorted(r['yearly'].keys()):
            v = r['yearly'][yr]
            print(f"    {yr}: ${v:,.0f}")

    print()
    print("=" * 62)
    print("СРАВНЕНИЕ ИТОГОВ")
    print("=" * 62)
    hdr = f"  {'Версия':<35} {'PnL':>9}  {'MaxDD':>7}  {'DailyDD':>8}  {'AllYrs':>7}  {'DD<10%':>7}"
    print(hdr)
    print("  " + "-" * 60)
    ref_r = results[120]
    rows = [
        ("Оригинал $120 (look-ahead+SHORT)", 884, 79721, 6.99, 3.28, True),
        ("ea_mirror $120 (Long+Short)", results[120]['total'],
         results[120]['pnl'], results[120]['max_dd'], results[120]['max_daily_dd'],
         all(results[120]['yearly'] > 0)),
    ]
    for label, n, pnl, dd, ddd, ay in rows:
        print(f"  {label:<35} ${pnl:>8,.0f}  {dd:>6.2f}%  {ddd:>7.2f}%  {'YES' if ay else 'NO ':>7}  {'YES' if dd<10 else 'NO ':>7}")

    for risk in (80, 100):
        r  = results[risk]
        ay = all(r['yearly'] > 0)
        label = f"ea_mirror ${risk} (LONG only)"
        print(f"  {label:<35} ${r['pnl']:>8,.0f}  {r['max_dd']:>6.2f}%  {r['max_daily_dd']:>7.2f}%  {'YES' if ay else 'NO ':>7}  {'YES' if r['max_dd']<10 else 'NO ':>7}")

    print("=" * 62)


if __name__ == "__main__":
    run()
