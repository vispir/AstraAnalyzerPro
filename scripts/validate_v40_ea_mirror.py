"""
Бэктест v4.0 — точное зеркало EA (BuildUTCH4Bars).

Три режима для LONG EMA-фильтра (SHORT везде одинаков = закрытый H4):
  original    — df_h4.iloc[-1] включает формирующийся с полными данными (look-ahead)
  closed_only — только закрытый H4 бар (4ч задержка)
  ea_mirror   — формирующийся H4 накапливается из M15 в реальном времени (как EA)

Оптимизация: указатели (ptrs) — не делаем pandas filter на каждом M15 баре.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from session_breakout_trader import (
    RISK_PER_TRADE, TP_RR, ATR_PERIOD, ATR_BUFFER,
    USE_H4_EMA_FILTER, H4_EMA_PERIOD,
    LONG_SESSIONS,
    SHORT_TYPE1_LOOKBACK_H4_BARS, SHORT_TYPE2_H4_LOOKBACK, SHORT_TYPE2_ATR_MULTIPLIER,
    calculate_atr, calculate_ema,
)

import pandas as pd
import numpy as np
from pathlib import Path

K_EMA = 2.0 / (H4_EMA_PERIOD + 1.0)
MIN_H4 = max(SHORT_TYPE1_LOOKBACK_H4_BARS + 2, SHORT_TYPE2_H4_LOOKBACK + 2)


def floor4h(ts: pd.Timestamp) -> pd.Timestamp:
    e = int(ts.timestamp()) // 14400 * 14400
    return pd.Timestamp(e, unit='s', tz='UTC')


def apply_step_trailing(t, low, high, is_long):
    risk = t['entry'] - t['initial_sl'] if is_long else t['initial_sl'] - t['entry']
    ref  = low if is_long else high
    rr   = (ref - t['entry']) / risk if is_long else (t['entry'] - ref) / risk
    for trigger, lock in ((5, 4), (4, 3), (3, 2), (2, 1)):
        if rr >= trigger:
            ns = t['entry'] + lock * risk if is_long else t['entry'] - lock * risk
            t['sl'] = max(t['sl'], ns) if is_long else min(t['sl'], ns)
            break


# ---------------------------------------------------------------------------
def run_mode(df_m15, h4_times, h4_open, h4_high, h4_low, h4_close, h4_atr, h4_ema20, mode):
    """
    Параметры:
      df_m15   — M15 DataFrame (sorted, UTC index, с 'atr')
      h4_*     — numpy arrays H4 баров (oldest-first, выровненных по UTC 4h)
      mode     — 'original' | 'closed_only' | 'ea_mirror'
    """
    n_h4 = len(h4_times)

    # --- Указатели H4 --------------------------------------------------------
    # ptr_orig:   наибольший b, где h4_times[b] <= current_m15_time
    # ptr_closed: наибольший b, где h4_times[b] + 4h <= current_m15_time
    ptr_orig   = -1
    ptr_closed = -1

    # --- Состояние SHORT state machine (использует закрытый H4) -------------
    t1_active = False;  t1_h4_high = 0.0
    t2_active = False;  t2_h4_high = 0.0
    last_short_h4_ptr = -1

    # --- Формирующийся H4 бар (только ea_mirror) ----------------------------
    forming_period = None
    forming_high   = -np.inf
    forming_low    =  np.inf
    forming_close  = np.nan
    ema_base       = np.nan   # ema20 последнего закрытого H4 бара

    # --- Торговое состояние --------------------------------------------------
    trades = []
    active_long  = {}
    active_short = None
    balance      = 10_000.0
    peak         = 10_000.0
    max_dd       = 0.0
    max_daily_dd = 0.0

    m15_arr = df_m15.to_numpy()   # быстрый numpy доступ
    cols = {c: i for i, c in enumerate(df_m15.columns)}
    idx_high  = cols['high']
    idx_low   = cols['low']
    idx_close = cols['close']
    idx_atr   = cols['atr']
    times_ns  = df_m15.index.asi8  # наносекунды — для сравнений
    hours_arr = np.array([t.hour for t in df_m15.index])

    H4_NS = int(4 * 3600 * 1e9)  # 4 часа в наносекундах

    prev_date     = None
    day_start_bal = balance
    session_highs = {}
    session_lows  = {}

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

        # Daily DD tracking
        cur_date = cur_ts.date()
        if cur_date != prev_date:
            if prev_date is not None and day_start_bal > 0:
                ddaily = (day_start_bal - balance) / day_start_bal * 100
                if ddaily > max_daily_dd: max_daily_dd = ddaily
            day_start_bal = balance
            prev_date     = cur_date
            session_highs = {}
            session_lows  = {}

        # ── Продвинуть указатель ptr_orig (h4_times[b] <= cur_ts) ────────────
        while ptr_orig + 1 < n_h4 and h4_times[ptr_orig + 1] <= ts_ns:
            ptr_orig += 1

        # ── Продвинуть ptr_closed (h4_times[b] + 4h <= cur_ts) ───────────────
        while ptr_closed + 1 < n_h4 and h4_times[ptr_closed + 1] + H4_NS <= ts_ns:
            ptr_closed += 1

        # ── Обновить формирующийся H4 бар (ea_mirror) ────────────────────────
        h4_period_start = int(ts_ns // int(14400 * 1e9)) * int(14400 * 1e9)

        if mode == 'ea_mirror':
            if h4_period_start != forming_period:
                forming_period = h4_period_start
                forming_high   = high
                forming_low    = low
                forming_close  = close
                # ema_base = ema20 закрытого H4 бара перед этим периодом
                ema_base = h4_ema20[ptr_closed] if ptr_closed >= 0 else np.nan
            else:
                forming_high  = max(forming_high, high)
                forming_low   = min(forming_low,  low)
                forming_close = close

        # ── Определить H4-контекст для LONG EMA фильтра ──────────────────────
        if mode == 'original':
            if ptr_orig < MIN_H4 - 1:
                continue
            long_h4_close = h4_close[ptr_orig]
            long_h4_ema20 = h4_ema20[ptr_orig]

        elif mode == 'closed_only':
            if ptr_closed < MIN_H4 - 1:
                continue
            long_h4_close = h4_close[ptr_closed]
            long_h4_ema20 = h4_ema20[ptr_closed]

        else:  # ea_mirror
            if ptr_closed < MIN_H4 - 1 or np.isnan(ema_base):
                continue
            long_h4_close = forming_close
            # EMA с одним шагом для формирующегося бара (точно как CalcUTCH4EMA в EA)
            long_h4_ema20 = forming_close * K_EMA + ema_base * (1.0 - K_EMA)

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

        # DD
        if balance > peak: peak = balance
        dd = (peak - balance) / peak * 100
        if dd > max_dd: max_dd = dd

        # ── Диапазоны сессий ─────────────────────────────────────────────────
        for sn, p in LONG_SESSIONS.items():
            sh, eh = p['range_hours']
            if sh <= hour < eh:
                session_highs[sn] = max(session_highs.get(sn, 0),    high)
                session_lows[sn]  = min(session_lows.get(sn,  1e9),  low)

        # ── LONG входы ───────────────────────────────────────────────────────
        for sn, p in LONG_SESSIONS.items():
            if sn not in session_highs or sn in active_long:
                continue
            if not (p['entry_start'] <= hour < p['entry_end']):
                continue
            if close > session_highs[sn]:
                if USE_H4_EMA_FILTER:
                    if np.isnan(long_h4_ema20) or long_h4_close <= long_h4_ema20:
                        continue
                sl   = session_lows[sn] - ATR_BUFFER * atr
                risk = close - sl
                if risk <= 0: continue
                active_long[sn] = {
                    'entry': close, 'sl': sl, 'initial_sl': sl,
                    'tp': close + risk * TP_RR,
                    'size': RISK_PER_TRADE / risk,
                    'direction': 'LONG', 'session': sn,
                }

        # ── SHORT state machine ───────────────────────────────────────────────
        # Всегда используем закрытый H4 бар — как h4bars[1] в EA
        # Режим original тоже переключён на закрытый для SHORT
        # (в оригинальном бэктесте SHORT тоже использовал формирующийся,
        #  но мы хотим сравнение только по LONG EMA фильтру)
        if mode == 'original':
            # Для корректного сравнения: SHORT везде одинаков (закрытый)
            ptr_short = ptr_orig   # original SHORT использовал ptr_orig (формирующийся)
        else:
            ptr_short = ptr_closed

        if active_short is None and hour < 21 and ptr_short >= MIN_H4 - 1:
            if last_short_h4_ptr != ptr_short:
                last_short_h4_ptr = ptr_short

                short_ema = h4_ema20[ptr_short]
                short_close = h4_close[ptr_short]

                if USE_H4_EMA_FILTER:
                    if np.isnan(short_ema) or short_close >= short_ema:
                        t1_active = False; t2_active = False
                        continue

                cur_h = h4_high[ptr_short]
                cur_c = h4_close[ptr_short]
                prev_c = h4_close[ptr_short - 1]

                # Type 1: новый максимум за lookback H4 баров
                if not t1_active and ptr_short >= SHORT_TYPE1_LOOKBACK_H4_BARS + 1:
                    lb_start = ptr_short - SHORT_TYPE1_LOOKBACK_H4_BARS
                    lb_end   = ptr_short   # [lb_start, ptr_short) = lookback без текущего
                    lookback_max = h4_high[lb_start:lb_end].max()
                    if cur_h > lookback_max and cur_c < prev_c:
                        t1_active  = True
                        t1_h4_high = cur_h

                # Type 2: движение >= 2×H4_ATR за последние 3 бара
                if not t2_active and ptr_short >= SHORT_TYPE2_H4_LOOKBACK + 1:
                    lb_start2 = ptr_short - SHORT_TYPE2_H4_LOOKBACK
                    lb_end2   = ptr_short
                    move = cur_h - h4_low[lb_start2:lb_end2].min()
                    h4a  = h4_atr[ptr_short]
                    if not np.isnan(h4a) and move >= SHORT_TYPE2_ATR_MULTIPLIER * h4a:
                        if cur_c < prev_c:
                            t2_active  = True
                            t2_h4_high = cur_h

            # M15 триггер
            if i > 0:
                prev_low_m15 = float(m15_arr[i - 1, idx_low])
                if t1_active and close < prev_low_m15:
                    sl = t1_h4_high + ATR_BUFFER * atr
                    risk = sl - close
                    if risk > 0:
                        active_short = {
                            'entry': close, 'sl': sl, 'initial_sl': sl,
                            'tp': close - risk * TP_RR,
                            'size': RISK_PER_TRADE / risk,
                            'direction': 'SHORT', 'session': 'short',
                        }
                        t1_active = False
                elif t2_active and close < prev_low_m15:
                    sl = t2_h4_high + ATR_BUFFER * atr
                    risk = sl - close
                    if risk > 0:
                        active_short = {
                            'entry': close, 'sl': sl, 'initial_sl': sl,
                            'tp': close - risk * TP_RR,
                            'size': RISK_PER_TRADE / risk,
                            'direction': 'SHORT', 'session': 'short',
                        }
                        t2_active = False

    # Daily DD последний день
    if prev_date is not None and day_start_bal > 0:
        ddaily = (day_start_bal - balance) / day_start_bal * 100
        if ddaily > max_daily_dd: max_daily_dd = ddaily

    # Закрыть остатки
    last_close_m15 = float(m15_arr[-1, idx_close])
    last_year      = int(df_m15.index[-1].year)
    for sn, t in active_long.items():
        t['pnl'] = (last_close_m15 - t['entry']) * t['size']
        balance += t['pnl']; t['year'] = last_year; trades.append(t)
    if active_short is not None:
        active_short['pnl'] = (active_short['entry'] - last_close_m15) * active_short['size']
        balance += active_short['pnl']; active_short['year'] = last_year; trades.append(active_short)

    tdf = pd.DataFrame(trades)
    total_pnl = balance - 10_000
    wr = (tdf['pnl'] > 0).sum() / len(tdf) if len(tdf) > 0 else 0
    long_df  = tdf[tdf['direction'] == 'LONG']
    short_df = tdf[tdf['direction'] == 'SHORT']
    yearly   = tdf.groupby('year')['pnl'].sum() if len(tdf) > 0 else pd.Series(dtype=float)

    return {
        'mode': mode, 'total': len(tdf), 'wr': wr, 'pnl': total_pnl,
        'max_dd': max_dd, 'max_daily_dd': max_daily_dd,
        'long_n': len(long_df),  'long_pnl':  float(long_df['pnl'].sum())  if len(long_df)  > 0 else 0,
        'short_n': len(short_df),'short_pnl': float(short_df['pnl'].sum()) if len(short_df) > 0 else 0,
        'yearly': yearly,
    }


# ---------------------------------------------------------------------------
def run():
    print("=" * 72)
    print("BACKTEST v4.0  --  EA MIRROR (BuildUTCH4Bars exact replication)")
    print("=" * 72)

    data_path = (
        Path(__file__).parent.parent
        / "data_cache" / "dukascopy" / "m15" / "XAUUSD"
        / "xauusd_m15_2020-01-01_2026-04-18.parquet"
    )
    df = pd.read_parquet(data_path).sort_index()
    print(f"Period: {df.index[0]} -- {df.index[-1]}")

    df['atr'] = calculate_atr(df, ATR_PERIOD)

    df_h4 = df.resample('4h').agg(
        {'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last'}
    ).dropna()
    df_h4['atr']  = calculate_atr(df_h4, ATR_PERIOD)
    df_h4['ema20'] = calculate_ema(df_h4, H4_EMA_PERIOD)

    # Numpy arrays для быстрого доступа (oldest-first, UTC aligned)
    h4_times = df_h4.index.asi8              # int64 наносекунды
    h4_open  = df_h4['open'].to_numpy()
    h4_high  = df_h4['high'].to_numpy()
    h4_low   = df_h4['low'].to_numpy()
    h4_close = df_h4['close'].to_numpy()
    h4_atr   = df_h4['atr'].to_numpy()
    h4_ema20 = df_h4['ema20'].to_numpy()

    results = {}
    for mode in ('original', 'closed_only', 'ea_mirror'):
        print(f"\n  Running [{mode}]...", flush=True)
        results[mode] = run_mode(
            df, h4_times, h4_open, h4_high, h4_low, h4_close, h4_atr, h4_ema20, mode
        )
        r = results[mode]
        print(f"    Done: {r['total']} trades  PnL=${r['pnl']:,.0f}"
              f"  LONG=${r['long_pnl']:,.0f}  SHORT=${r['short_pnl']:,.0f}"
              f"  DD={r['max_dd']:.2f}%")

    print("\n")
    print("=" * 72)
    print("SUMMARY")
    print("=" * 72)
    rows = [
        ('original   (look-ahead)',       results['original']),
        ('closed_only (4h delay)',         results['closed_only']),
        ('ea_mirror  (forming H4 RT)',     results['ea_mirror']),
    ]
    hdr = f"  {'Mode':<36} {'N':>5} {'PnL':>9}  {'LONG$':>9}  {'SHORT$':>9}  {'DD%':>6}  {'+YRS'}"
    print(hdr)
    print("  " + "-" * 65)
    for label, r in rows:
        ay = 'YES' if len(r['yearly']) > 0 and all(r['yearly'] > 0) else 'NO '
        print(f"  {label:<36} {r['total']:>5} ${r['pnl']:>8,.0f}"
              f"  ${r['long_pnl']:>8,.0f}  ${r['short_pnl']:>8,.0f}"
              f"  {r['max_dd']:>5.2f}%  {ay}")
    print()
    print("  PnL by year (ea_mirror):")
    r = results['ea_mirror']
    for yr in sorted(r['yearly'].keys()):
        print(f"    {yr}: ${r['yearly'][yr]:,.0f}")
    print("=" * 72)


if __name__ == "__main__":
    run()
