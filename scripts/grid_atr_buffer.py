"""Grid search over ATR buffer values for SL placement."""
import sys, os, importlib.util
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
import numpy as np
from pathlib import Path

_sp = Path(__file__).parent.parent / "astra_v2/strategies/session_long_nolookahead_v1.py"
spec = importlib.util.spec_from_file_location("strat", _sp)
strat = importlib.util.module_from_spec(spec)
spec.loader.exec_module(strat)

DATA = Path(__file__).parent.parent / "data_cache/dukascopy/m15/XAUUSD/xauusd_m15_2020-01-01_2026-05-08.parquet"

K_EMA       = strat.K_EMA
H4_NS       = strat.H4_NS
SLOPE_N     = strat.SLOPE_N
MIN_H4_BARS = strat.MIN_H4_BARS
TP_RR       = strat.TP_RR
RISK        = strat.RISK
SESSIONS    = strat.SESSIONS

def run_backtest(df, h4_times, h4_ema20, atr_buffer):
    n_h4     = len(h4_times)
    times_ns = df.index.asi8
    m15      = df.to_numpy()
    col      = {c: i for i, c in enumerate(df.columns)}
    i_h = col['high']; i_l = col['low']; i_c = col['close']; i_a = col['atr']

    ptr_closed     = -1
    forming_period = -1
    forming_close  = np.nan
    ema_base       = np.nan

    trades      = []
    active_long = {}
    balance     = 10_000.0
    peak        = 10_000.0
    max_dd      = 0.0
    max_dly_dd  = 0.0
    prev_date   = None
    day_start   = balance
    s_highs     = {}
    s_lows      = {}

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
                if dd > max_dly_dd: max_dly_dd = dd
            day_start = balance
            prev_date = cur_date
            s_highs = {}; s_lows = {}

        while ptr_closed + 1 < n_h4 and h4_times[ptr_closed + 1] + H4_NS <= ts_ns:
            ptr_closed += 1

        if ptr_closed < MIN_H4_BARS - 1:
            for sn, p in SESSIONS.items():
                sh, eh = p['range_hours']
                if sh <= hour < eh:
                    s_highs[sn] = max(s_highs.get(sn, 0),   high)
                    s_lows[sn]  = min(s_lows.get(sn, 1e9),  low)
            continue

        h4p = int(ts_ns // int(14400 * 1e9)) * int(14400 * 1e9)
        if h4p != forming_period:
            forming_period = h4p
            forming_close  = close
            ema_base = h4_ema20[ptr_closed] if ptr_closed >= 0 else np.nan
        else:
            forming_close = close

        if np.isnan(ema_base):
            continue

        h4_ema   = forming_close * K_EMA + ema_base * (1.0 - K_EMA)
        ema_ok   = forming_close > ema_base
        slope_ok = (ptr_closed >= SLOPE_N) and \
                   not np.isnan(h4_ema20[ptr_closed - SLOPE_N]) and \
                   h4_ema > h4_ema20[ptr_closed - SLOPE_N]

        for sn in list(active_long.keys()):
            t = active_long[sn]
            strat._trail(t, low)
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

        for sn, p in SESSIONS.items():
            sh, eh = p['range_hours']
            if sh <= hour < eh:
                s_highs[sn] = max(s_highs.get(sn, 0),   high)
                s_lows[sn]  = min(s_lows.get(sn, 1e9),  low)

        if not (ema_ok and slope_ok):
            continue

        for sn, p in SESSIONS.items():
            if sn not in s_highs or sn in active_long:
                continue
            if not (p['entry_start'] <= hour < p['entry_end']):
                continue
            if close > s_highs[sn]:
                sl  = s_lows[sn] - atr_buffer * atr
                rsk = close - sl
                if rsk <= 0: continue
                active_long[sn] = {
                    'entry': close, 'sl': sl, 'initial_sl': sl,
                    'tp': close + rsk * TP_RR,
                    'size': RISK / rsk,
                    'session': sn,
                }

    if prev_date is not None and day_start > 0:
        dd = (day_start - balance) / day_start * 100
        if dd > max_dly_dd: max_dly_dd = dd

    last_close = float(m15[-1, i_c])
    last_year  = int(df.index[-1].year)
    for sn, t in active_long.items():
        t['pnl'] = (last_close - t['entry']) * t['size']
        balance += t['pnl']; t['year'] = last_year
        trades.append(t)

    tdf = pd.DataFrame(trades) if trades else pd.DataFrame(columns=['pnl','year'])
    n   = len(tdf)
    wr  = (tdf['pnl'] > 0).sum() / n if n > 0 else 0
    pnl = balance - 10_000
    yearly = tdf.groupby('year')['pnl'].sum() if n > 0 else pd.Series(dtype=float)
    all_pos = bool(all(yearly > 0)) if len(yearly) > 0 else False

    return {'n': n, 'wr': wr, 'pnl': pnl,
            'max_dd': max_dd, 'max_dly_dd': max_dly_dd,
            'all_pos': all_pos}

# Load data
print("Loading data...")
df = pd.read_parquet(DATA)
df.index = pd.to_datetime(df.index, utc=True)
df["atr"] = strat._atr(df, strat.ATR_PERIOD)
df_h4 = df.resample("4h").agg({"open":"first","high":"max","low":"min","close":"last"}).dropna()
df_h4["ema20"] = strat._ema(df_h4, strat.H4_EMA_PERIOD)
h4_times = df_h4.index.asi8
h4_ema20 = df_h4["ema20"].to_numpy()

ATR_BUFFERS = [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 1.0, 1.2, 1.5, 2.0]

print(f"\nТестируем {len(ATR_BUFFERS)} значений ATR buffer...\n")
print("=" * 72)
print(f"  {'Buf':>5} {'N':>5} {'WR':>6} {'PnL':>9} {'MaxDD':>7} {'DlyDD':>7} {'AllYrs'} {'Status'}")
print("  " + "-" * 65)

CURRENT_PNL = 47_620
results = []

for buf in ATR_BUFFERS:
    r = run_backtest(df, h4_times, h4_ema20, buf)
    ok_dd  = r['max_dd'] <= 10.0
    ok_dly = r['max_dly_dd'] <= 5.0
    ok_pnl = r['pnl'] > CURRENT_PNL
    ok_yrs = r['all_pos']
    cur    = " <-- ТЕКУЩИЙ" if buf == 0.5 else ""
    flag   = ""
    if ok_dd and ok_dly and ok_pnl and ok_yrs:
        flag = " BETTER!"
    elif ok_dd and ok_dly and ok_yrs:
        flag = " ok"
    else:
        flag = " --"
    print(f"  {buf:>5.1f} {r['n']:>5} {r['wr']:>6.1%} ${r['pnl']:>8,.0f} "
          f"{r['max_dd']:>6.2f}% {r['max_dly_dd']:>6.2f}% "
          f"{'YES' if r['all_pos'] else 'NO ':>6}{flag}{cur}")
    results.append({**r, 'buf': buf})

print("=" * 72)
print()
print("  Критерии: MaxDD<=10%, DlyDD<=5%, AllYrs=YES, PnL>$47,620")
better = [r for r in results if r['max_dd']<=10 and r['max_dly_dd']<=5 and r['all_pos'] and r['pnl']>CURRENT_PNL]
if better:
    best = max(better, key=lambda x: x['pnl'])
    print(f"  Лучший: ATR buffer={best['buf']}  PnL=${best['pnl']:,.0f}  MaxDD={best['max_dd']:.2f}%")
else:
    print("  Нет вариантов лучше текущего по всем критериям.")
