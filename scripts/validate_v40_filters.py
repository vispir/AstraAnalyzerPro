"""
EA Mirror LONG ONLY — тестирование дополнительных фильтров по одному.

Baseline: 699 сделок, WR=42.9%, PnL=$46,721, MaxDD=16.60%

Фильтр 1: H4 EMA slope > 0  (forming_ema > h4_ema20[ptr_closed-3])
Фильтр 2: Пробой > 0.3*ATR  (close > session_high + 0.3*atr)
Фильтр 3: Пропустить первый час entry window
          (asian: не входить в 10:00-10:59, london: 16:00-16:59, ny: 18:00-18:59)
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
RISK  = 120.0

# Первый час entry window для каждой сессии
ENTRY_SKIP_HOUR = {
    'asian':  10,
    'london': 16,
    'ny':     18,
}


def floor4h_ns(ts_ns: int) -> int:
    return int(ts_ns // int(14400 * 1e9)) * int(14400 * 1e9)


def apply_step_trailing(t, low, high):
    risk = t['entry'] - t['initial_sl']
    rr   = (low - t['entry']) / risk
    for trigger, lock in ((5, 4), (4, 3), (3, 2), (2, 1)):
        if rr >= trigger:
            t['sl'] = max(t['sl'], t['entry'] + lock * risk)
            break


def run_with_filter(df, h4_times, h4_ema20, filter_id):
    """
    filter_id:
      0 = baseline (без доп. фильтра)
      1 = EMA slope
      2 = breakout > 0.3*ATR
      3 = skip first entry hour
    """
    n_h4  = len(h4_times)
    H4_NS = int(4 * 3600 * 1e9)
    MIN_H4 = 22

    m15_arr  = df.to_numpy()
    cols     = {c: i for i, c in enumerate(df.columns)}
    i_high = cols['high']; i_low = cols['low']
    i_close = cols['close']; i_atr = cols['atr']
    times_ns = df.index.asi8
    hours    = np.array([t.hour for t in df.index])

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

        # Управление открытыми сделками
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

        # ── Фильтр 1: EMA slope ────────────────────────────────────────────
        if filter_id == 1:
            if ptr_closed < 3:
                continue
            ema_3ago = h4_ema20[ptr_closed - 3]
            if np.isnan(ema_3ago) or h4_ema <= ema_3ago:
                continue   # нет роста EMA

        # LONG входы
        for sn, p in LONG_SESSIONS.items():
            if sn not in session_highs or sn in active_long:
                continue
            if not (p['entry_start'] <= hour < p['entry_end']):
                continue

            # ── Фильтр 3: пропустить первый час entry window ─────────���─────
            if filter_id == 3:
                if hour == ENTRY_SKIP_HOUR[sn]:
                    continue   # первый час — пропускаем

            if close > session_highs[sn]:
                # ── Фильтр 2: пробой > 0.3*ATR ────────────────────────────
                if filter_id == 2:
                    if close <= session_highs[sn] + 0.3 * atr:
                        continue   # слабый пробой

                if USE_H4_EMA_FILTER:
                    if np.isnan(h4_ema) or forming_close <= h4_ema:
                        continue
                sl   = session_lows[sn] - ATR_BUFFER * atr
                risk = close - sl
                if risk <= 0: continue
                active_long[sn] = {
                    'entry': close, 'sl': sl, 'initial_sl': sl,
                    'tp': close + risk * TP_RR,
                    'size': RISK / risk,
                    'direction': 'LONG', 'session': sn,
                }

    if prev_date is not None and day_start_bal > 0:
        ddaily = (day_start_bal - balance) / day_start_bal * 100
        if ddaily > max_daily_dd: max_daily_dd = ddaily

    last_close = float(m15_arr[-1, i_close])
    last_year  = int(df.index[-1].year)
    for sn, t in active_long.items():
        t['pnl'] = (last_close - t['entry']) * t['size']
        balance += t['pnl']; t['year'] = last_year; trades.append(t)

    tdf    = pd.DataFrame(trades)
    pnl    = balance - 10_000
    wr     = (tdf['pnl'] > 0).sum() / len(tdf) if len(tdf) > 0 else 0
    yearly = tdf.groupby('year')['pnl'].sum() if len(tdf) > 0 else pd.Series(dtype=float)

    return {
        'n': len(tdf), 'wr': wr, 'pnl': pnl,
        'max_dd': max_dd, 'max_daily_dd': max_daily_dd,
        'yearly': yearly,
        'all_pos': all(yearly > 0) if len(yearly) > 0 else False,
    }


def run():
    print("=" * 68)
    print("EA MIRROR LONG ONLY — ТЕСТИРОВАНИЕ ФИЛЬТРОВ  (Risk=$120)")
    print("=" * 68)

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

    labels = {
        0: 'Baseline (без доп. фильтров)',
        1: 'Фильтр 1: EMA slope > 0 (ema > ema[-3])',
        2: 'Фильтр 2: Пробой > 0.3*ATR',
        3: 'Фильтр 3: Пропустить 1й час entry window',
    }

    results = {}
    for fid in range(4):
        print(f"  Running filter {fid}: {labels[fid]}...", flush=True)
        results[fid] = run_with_filter(df, h4_times, h4_ema20, fid)
        r = results[fid]
        print(f"    {r['n']} trades  WR={r['wr']:.1%}  PnL=${r['pnl']:,.0f}  DD={r['max_dd']:.2f}%")

    # ── Детальный вывод ───────────────────────────────────────────────────────
    print()
    for fid in range(4):
        r   = results[fid]
        b   = results[0]
        dN  = r['n']   - b['n']
        dWR = r['wr']  - b['wr']
        dP  = r['pnl'] - b['pnl']
        dDD = r['max_dd'] - b['max_dd']

        print(f"\n{'='*60}")
        print(f"[{fid}] {labels[fid]}")
        print(f"{'='*60}")
        print(f"  Trades:   {r['n']:>4}  (Δ {dN:+d})")
        print(f"  WinRate:  {r['wr']:.1%}  (Δ {dWR:+.1%})")
        print(f"  PnL:     ${r['pnl']:>8,.0f}  (Δ ${dP:+,.0f})")
        print(f"  MaxDD:    {r['max_dd']:.2f}%  (Δ {dDD:+.2f}%)")
        print(f"  DailyDD:  {r['max_daily_dd']:.2f}%")
        print(f"  All years profitable: {'YES' if r['all_pos'] else 'NO'}")
        print(f"  DD < 10%: {'YES ✓' if r['max_dd'] < 10 else 'NO  ✗'}")
        if fid > 0:
            print(f"  PnL by year (vs baseline):")
            for yr in sorted(r['yearly'].keys()):
                v  = r['yearly'][yr]
                bv = b['yearly'].get(yr, 0)
                marker = ' ▲' if v > bv else (' ▼' if v < bv else '  ')
                print(f"    {yr}: ${v:>7,.0f}  (baseline ${bv:>7,.0f}){marker}")
        else:
            print(f"  PnL by year:")
            for yr in sorted(r['yearly'].keys()):
                print(f"    {yr}: ${r['yearly'][yr]:>7,.0f}")

    # ── Сводная таблица ───────────────────────────────────────────────────────
    print()
    print("=" * 68)
    print("СВОДНАЯ ТАБЛИЦА")
    print("=" * 68)
    hdr = f"  {'Версия':<42} {'N':>5}  {'WR':>6}  {'PnL':>9}  {'MaxDD':>7}  {'DD<10%':>7}"
    print(hdr)
    print("  " + "-" * 65)
    for fid in range(4):
        r  = results[fid]
        ay = 'Y' if r['all_pos'] else 'N'
        ok = 'YES ✓' if r['max_dd'] < 10 else 'NO  ✗'
        lbl = labels[fid][:42]
        print(f"  {lbl:<42} {r['n']:>5}  {r['wr']:>5.1%}  ${r['pnl']:>8,.0f}  {r['max_dd']:>6.2f}%  {ok}")
    print("=" * 68)


if __name__ == "__main__":
    run()
