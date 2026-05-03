"""
EA Mirror LONG ONLY + дневной лимит потерь 2%.
Risk=$120, без SHORT.
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

K_EMA         = 2.0 / (H4_EMA_PERIOD + 1.0)
RISK          = 120.0
DAILY_LOSS_PCT = 0.02   # 2%


def floor4h_ns(ts_ns: int) -> int:
    return int(ts_ns // int(14400 * 1e9)) * int(14400 * 1e9)


def apply_step_trailing(t, low, high):
    risk = t['entry'] - t['initial_sl']
    rr   = (low - t['entry']) / risk
    for trigger, lock in ((5, 4), (4, 3), (3, 2), (2, 1)):
        if rr >= trigger:
            t['sl'] = max(t['sl'], t['entry'] + lock * risk)
            break


def run():
    print("=" * 62)
    print("EA MIRROR — LONG ONLY + Daily Loss Limit 2%")
    print(f"Risk=${RISK:.0f}  TP={TP_RR}R  Daily limit={DAILY_LOSS_PCT*100:.0f}%")
    print("=" * 62)

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
    n_h4     = len(h4_times)
    H4_NS    = int(4 * 3600 * 1e9)
    MIN_H4   = 22

    m15_arr  = df.to_numpy()
    cols     = {c: i for i, c in enumerate(df.columns)}
    i_high   = cols['high']; i_low = cols['low']
    i_close  = cols['close']; i_atr = cols['atr']
    times_ns = df.index.asi8
    hours    = np.array([t.hour for t in df.index])

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

    prev_date      = None
    day_start_bal  = balance
    day_blocked    = False      # флаг: дневной лимит сработал
    days_blocked   = 0          # сколько дней сработал лимит
    session_highs  = {}
    session_lows   = {}

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

        # ── Новый день ────────────────────────────────────────────────────
        cur_date = cur_ts.date()
        if cur_date != prev_date:
            if prev_date is not None and day_start_bal > 0:
                ddaily = (day_start_bal - balance) / day_start_bal * 100
                if ddaily > max_daily_dd:
                    max_daily_dd = ddaily
            day_start_bal = balance
            day_blocked   = False
            prev_date     = cur_date
            session_highs = {}
            session_lows  = {}

        # ── Продвинуть ptr_closed ─────────────────────────────────────────
        while ptr_closed + 1 < n_h4 and h4_times[ptr_closed + 1] + H4_NS <= ts_ns:
            ptr_closed += 1

        if ptr_closed < MIN_H4 - 1:
            for sn, p in LONG_SESSIONS.items():
                sh, eh = p['range_hours']
                if sh <= hour < eh:
                    session_highs[sn] = max(session_highs.get(sn, 0), high)
                    session_lows[sn]  = min(session_lows.get(sn, 1e9), low)
            continue

        # ── Формирующийся H4 ─────────────────────────────────────────────
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

        # ── Управление открытыми LONG сделками ───────────────────────────
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

        # ── Проверить дневной лимит ───────────────────────────────────────
        if not day_blocked and day_start_bal > 0:
            day_loss_pct = (day_start_bal - balance) / day_start_bal
            if day_loss_pct >= DAILY_LOSS_PCT:
                day_blocked = True
                days_blocked += 1

        # ── DD ────────────────────────────────────────────────────────────
        if balance > peak: peak = balance
        dd = (peak - balance) / peak * 100
        if dd > max_dd: max_dd = dd

        # ── Диапазоны сессий ─────────────────────────────────────────────
        for sn, p in LONG_SESSIONS.items():
            sh, eh = p['range_hours']
            if sh <= hour < eh:
                session_highs[sn] = max(session_highs.get(sn, 0), high)
                session_lows[sn]  = min(session_lows.get(sn, 1e9), low)

        # ── LONG входы (только если день не заблокирован) ─────────────────
        if day_blocked:
            continue

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
                    'size': RISK / risk,
                    'direction': 'LONG', 'session': sn,
                }

    # Последний день DD
    if prev_date is not None and day_start_bal > 0:
        ddaily = (day_start_bal - balance) / day_start_bal * 100
        if ddaily > max_daily_dd: max_daily_dd = ddaily

    # Закрыть остатки
    last_close = float(m15_arr[-1, i_close])
    last_year  = int(df.index[-1].year)
    for sn, t in active_long.items():
        t['pnl'] = (last_close - t['entry']) * t['size']
        balance += t['pnl']; t['year'] = last_year; trades.append(t)

    tdf    = pd.DataFrame(trades)
    pnl    = balance - 10_000
    wr     = (tdf['pnl'] > 0).sum() / len(tdf) if len(tdf) > 0 else 0
    yearly = tdf.groupby('year')['pnl'].sum() if len(tdf) > 0 else pd.Series(dtype=float)

    all_pos = all(yearly > 0)
    dd_ok   = max_dd < 10.0
    ddd_ok  = max_daily_dd < 5.0

    print(f"\n  Trades:      {len(tdf)}")
    print(f"  Win Rate:    {wr:.1%}")
    print(f"  Total PnL:   ${pnl:,.0f}")
    print(f"  Max DD:      {max_dd:.2f}%  {'✓ < 10%' if dd_ok  else '✗ > 10%'}")
    print(f"  Max Daily DD:{max_daily_dd:.2f}%  {'✓ < 5%'  if ddd_ok else '✗ > 5%'}")
    print(f"  All years profitable: {'YES ✓' if all_pos else 'NO  ✗'}")
    print(f"  Days blocked by limit: {days_blocked}")
    print(f"\n  PnL by year:")
    for yr in sorted(yearly.keys()):
        v = yearly[yr]
        print(f"    {yr}: ${v:,.0f}")

    print()
    print("=" * 62)
    print("СРАВНЕНИЕ")
    print("=" * 62)
    rows = [
        ("Оригинал $120 look-ahead+SHORT", 884, 79721, 6.99, 3.28, True),
        ("ea_mirror $120 LONG only (без лимита)", 699, 46721, 16.60, 3.69, True),
        (f"ea_mirror $120 LONG only + limit {DAILY_LOSS_PCT*100:.0f}%",
         len(tdf), int(pnl), round(max_dd, 2), round(max_daily_dd, 2), all_pos),
    ]
    print(f"  {'Версия':<42} {'PnL':>9}  {'MaxDD':>7}  {'DlyDD':>7}  {'AllYrs':>7}  {'DD<10%':>7}")
    print("  " + "-" * 65)
    for label, n, p, dd, ddd, ay in rows:
        print(f"  {label:<42} ${p:>8,}  {dd:>6.2f}%  {ddd:>6.2f}%"
              f"  {'YES' if ay else 'NO ':>7}  {'YES' if dd < 10 else 'NO ':>7}")
    print("=" * 62)


if __name__ == "__main__":
    run()
