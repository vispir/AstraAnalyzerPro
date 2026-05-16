"""Compare: hold over weekend vs force-close Friday 20:00 UTC."""
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

RISK        = strat.RISK
TP_RR       = strat.TP_RR
ATR_BUFFER  = strat.ATR_BUFFER
ATR_PERIOD  = strat.ATR_PERIOD
H4_EMA_PERIOD = strat.H4_EMA_PERIOD
SLOPE_N     = strat.SLOPE_N
MIN_H4_BARS = strat.MIN_H4_BARS
K_EMA       = strat.K_EMA
H4_NS       = strat.H4_NS

SESSIONS = {
    'asian':  {'range_hours': (3,  6),  'entry_start':  6, 'entry_end': 24},
    'london': {'range_hours': (8,  11), 'entry_start': 11, 'entry_end': 24},
    'ny':     {'range_hours': (15, 18), 'entry_start': 18, 'entry_end': 24},
}

FRIDAY_CLOSE_HOUR = 20  # UTC

def run_backtest(df, h4_times, h4_ema20, friday_close=False):
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

        # Friday force-close
        is_friday = (cur_ts.weekday() == 4)
        if friday_close and is_friday and hour >= FRIDAY_CLOSE_HOUR:
            for sn in list(active_long.keys()):
                t = active_long[sn]
                t['pnl'] = (close - t['entry']) * t['size']
                balance += t['pnl']
                t['year'] = cur_ts.year
                t['closed_by'] = 'friday'
                trades.append(t)
                del active_long[sn]

        # Manage positions
        for sn in list(active_long.keys()):
            t = active_long[sn]
            strat._trail(t, low)
            if low <= t['sl']:
                t['pnl'] = (t['sl'] - t['entry']) * t['size']
                balance += t['pnl']; t['year'] = cur_ts.year
                t['closed_by'] = 'sl'
                trades.append(t); del active_long[sn]
            elif high >= t['tp']:
                t['pnl'] = (t['tp'] - t['entry']) * t['size']
                balance += t['pnl']; t['year'] = cur_ts.year
                t['closed_by'] = 'tp'
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

        # No new entries on Friday after close hour
        if friday_close and is_friday and hour >= FRIDAY_CLOSE_HOUR:
            continue

        for sn, p in SESSIONS.items():
            if sn not in s_highs or sn in active_long:
                continue
            if not (p['entry_start'] <= hour < p['entry_end']):
                continue
            if close > s_highs[sn]:
                sl  = s_lows[sn] - ATR_BUFFER * atr
                rsk = close - sl
                if rsk <= 0: continue
                active_long[sn] = {
                    'entry': close, 'sl': sl, 'initial_sl': sl,
                    'tp': close + rsk * TP_RR,
                    'size': RISK / rsk,
                    'session': sn, 'closed_by': 'open',
                }

    if prev_date is not None and day_start > 0:
        dd = (day_start - balance) / day_start * 100
        if dd > max_dly_dd: max_dly_dd = dd

    last_close = float(m15[-1, i_c])
    last_year  = int(df.index[-1].year)
    for sn, t in active_long.items():
        t['pnl'] = (last_close - t['entry']) * t['size']
        balance += t['pnl']; t['year'] = last_year
        t['closed_by'] = 'end'
        trades.append(t)

    tdf = pd.DataFrame(trades)
    return {
        'pnl':       balance - 10_000,
        'max_dd':    max_dd,
        'max_dly_dd': max_dly_dd,
        'n':         len(tdf),
        'wr':        (tdf['pnl'] > 0).sum() / len(tdf) if len(tdf) else 0,
        'yearly':    tdf.groupby('year')['pnl'].sum() if len(tdf) else pd.Series(dtype=float),
        'trades':    tdf,
    }


def main():
    df = pd.read_parquet(DATA)
    df.index = pd.to_datetime(df.index, utc=True)
    df['atr'] = strat._atr(df, ATR_PERIOD)
    df_h4 = df.resample('4h').agg({'open':'first','high':'max','low':'min','close':'last'}).dropna()
    df_h4['ema20'] = strat._ema(df_h4, H4_EMA_PERIOD)
    h4_times = df_h4.index.asi8
    h4_ema20 = df_h4['ema20'].to_numpy()

    r_hold   = run_backtest(df, h4_times, h4_ema20, friday_close=False)
    r_friday = run_backtest(df, h4_times, h4_ema20, friday_close=True)

    print("=" * 60)
    print("  HOLD over weekend  vs  CLOSE Friday 20:00 UTC")
    print("=" * 60)
    print(f"  {'':25}  {'HOLD':>10}  {'FRI CLOSE':>10}")
    print(f"  {'Trades':25}  {r_hold['n']:>10}  {r_friday['n']:>10}")
    print(f"  {'Win Rate':25}  {r_hold['wr']:>10.1%}  {r_friday['wr']:>10.1%}")
    print(f"  {'Total PnL':25}  ${r_hold['pnl']:>9,.0f}  ${r_friday['pnl']:>9,.0f}")
    print(f"  {'MaxDD':25}  {r_hold['max_dd']:>9.2f}%  {r_friday['max_dd']:>9.2f}%")
    print(f"  {'MaxDailyDD':25}  {r_hold['max_dly_dd']:>9.2f}%  {r_friday['max_dly_dd']:>9.2f}%")
    print()

    yl = r_hold['yearly']; yf = r_friday['yearly']
    all_years = sorted(set(list(yl.index) + list(yf.index)))
    print(f"  {'Year':<6}  {'HOLD':>10}  {'FRI CLOSE':>10}  {'Diff':>8}")
    print("  " + "-" * 40)
    for y in all_years:
        vl = yl.get(y, 0); vf = yf.get(y, 0)
        diff = vf - vl
        sign = "+" if diff >= 0 else ""
        print(f"  {y:<6}  ${vl:>9,.0f}  ${vf:>9,.0f}  {sign}${diff:>6,.0f}")

    print()
    diff_total = r_friday['pnl'] - r_hold['pnl']
    sign = "+" if diff_total >= 0 else ""
    print(f"  VERDICT: Friday close gives {sign}${diff_total:,.0f} vs hold")
    print("=" * 60)


if __name__ == "__main__":
    main()
