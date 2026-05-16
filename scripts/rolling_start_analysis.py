"""
Rolling start analysis: simulate starting with $10,000 at every month from 2020-2026.
Find worst-case starting points and check if any would breach $9,000 floor.
"""
import sys, os, importlib.util
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
import numpy as np
from pathlib import Path

# Load strategy
_sp = Path(__file__).parent.parent / "astra_v2/strategies/session_long_nolookahead_v1.py"
spec = importlib.util.spec_from_file_location("strat", _sp)
strat = importlib.util.module_from_spec(spec)
spec.loader.exec_module(strat)

# Patch run() to capture close dates
import types

def run_with_dates(df, h4_times, h4_ema20):
    """Same as strat.run() but saves close_date on each trade."""
    n_h4     = len(h4_times)
    times_ns = df.index.asi8
    m15      = df.to_numpy()
    col      = {c: i for i, c in enumerate(df.columns)}
    i_h = col['high']; i_l = col['low']; i_c = col['close']; i_a = col['atr']

    K_EMA    = strat.K_EMA
    H4_NS    = strat.H4_NS
    SLOPE_N  = strat.SLOPE_N
    MIN_H4   = strat.MIN_H4_BARS
    ATR_BUF  = strat.ATR_BUFFER
    TP_RR    = strat.TP_RR
    RISK     = strat.RISK
    SESSIONS = strat.SESSIONS

    ptr_closed     = -1
    forming_period = -1
    forming_close  = np.nan
    ema_base       = np.nan

    trades      = []
    active_long = {}
    balance     = 10_000.0
    s_highs     = {}
    s_lows      = {}
    prev_date   = None

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
            prev_date = cur_date
            s_highs = {}; s_lows = {}

        while ptr_closed + 1 < n_h4 and h4_times[ptr_closed + 1] + H4_NS <= ts_ns:
            ptr_closed += 1

        if ptr_closed < MIN_H4 - 1:
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
                t['close_date'] = cur_ts
                trades.append(t); del active_long[sn]
            elif high >= t['tp']:
                t['pnl'] = (t['tp'] - t['entry']) * t['size']
                t['close_date'] = cur_ts
                trades.append(t); del active_long[sn]

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
                sl  = s_lows[sn] - ATR_BUF * atr
                rsk = close - sl
                if rsk <= 0: continue
                active_long[sn] = {
                    'entry': close, 'sl': sl, 'initial_sl': sl,
                    'tp': close + rsk * TP_RR,
                    'size': RISK / rsk,
                    'session': sn,
                }

    last_close = float(m15[-1, i_c])
    last_ts    = df.index[-1]
    for sn, t in active_long.items():
        t['pnl'] = (last_close - t['entry']) * t['size']
        t['close_date'] = last_ts
        trades.append(t)

    return pd.DataFrame(trades)


# Load data
print("Loading data...")
df = pd.read_parquet(
    Path(__file__).parent.parent / "data_cache/dukascopy/m15/XAUUSD/xauusd_m15_2020-01-01_2026-05-08.parquet"
)
df.index = pd.to_datetime(df.index, utc=True)
df["atr"] = strat._atr(df, strat.ATR_PERIOD)
df_h4 = df.resample("4h").agg({"open":"first","high":"max","low":"min","close":"last"}).dropna()
df_h4["ema20"] = strat._ema(df_h4, strat.H4_EMA_PERIOD)

print("Running backtest with date tracking...")
tdf = run_with_dates(df, df_h4.index.asi8, df_h4["ema20"].to_numpy())
tdf["close_date"] = pd.to_datetime(tdf["close_date"], utc=True)
tdf = tdf.sort_values("close_date").reset_index(drop=True)

pnls = tdf["pnl"].values
dates = tdf["close_date"].values
n = len(tdf)

print(f"Total trades: {n}")
print()

# For each monthly start, simulate $10,000 account
print("=" * 65)
print("  Rolling start: $10,000 at each month — worst case analysis")
print("=" * 65)
print(f"  {'Start':12} {'Trades':>7} {'MinBal':>9} {'EndBal':>9} {'Status'}")
print("  " + "-" * 55)

worst_min = 10_000.0
worst_start = None
breach_count = 0

import datetime
start_date = datetime.date(2020, 1, 1)
end_date   = datetime.date(2026, 3, 1)

cur = start_date
while cur <= end_date:
    # Find trades from this start date onwards
    cur_ts = pd.Timestamp(cur, tz="UTC")
    mask = tdf["close_date"] >= cur_ts
    if mask.sum() < 5:
        cur = (cur.replace(day=1) + datetime.timedelta(days=32)).replace(day=1)
        continue

    sub_pnl = pnls[mask]
    equity  = 10_000 + np.cumsum(sub_pnl)
    min_bal = equity.min()
    end_bal = equity[-1]
    n_tr    = len(sub_pnl)

    if min_bal < worst_min:
        worst_min   = min_bal
        worst_start = cur

    status = ""
    if min_bal < 9_000:
        status = "<<< BREACH $9k"
        breach_count += 1
    elif min_bal < 9_200:
        status = "< DANGER"
    elif min_bal < 9_500:
        status = "< CAUTION"

    if status or cur.month == 1:  # print all Januaries + flagged months
        print(f"  {str(cur):12} {n_tr:>7} ${min_bal:>8,.0f} ${end_bal:>8,.0f}  {status}")

    cur = (cur.replace(day=1) + datetime.timedelta(days=32)).replace(day=1)

print("  " + "-" * 55)
print()
print(f"  Абсолютный минимум баланса: ${worst_min:,.0f}  (старт {worst_start})")
print(f"  Нарушений флора $9,000:     {breach_count}")
print("=" * 65)
