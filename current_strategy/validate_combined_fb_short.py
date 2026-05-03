"""
Validation: LONG + london_fb SHORT + ny_fb SHORT — joint backtest.

LONG  (fixed): asian(3-6) london(8-11) ny(15-18)  TP=12R  Risk=$100
SHORT london_fb: range 06-09  TP=3R  ATRbuf=0.3  N_confirm=4  Risk=$100
SHORT ny_fb    : range 12-15  TP=10R ATRbuf=0.3  N_confirm=4  Risk=$100

All 3 share one balance -> combined DD.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
import numpy as np
from pathlib import Path
import importlib.util

_sp = Path(__file__).parent.parent / "astra_v2" / "strategies" / "session_long_nolookahead_v1.py"
spec = importlib.util.spec_from_file_location("strat", _sp)
strat = importlib.util.module_from_spec(spec)
spec.loader.exec_module(strat)

RISK_L        = strat.RISK
TP_RR_L       = strat.TP_RR
ATR_BUFFER_L  = strat.ATR_BUFFER
ATR_PERIOD    = strat.ATR_PERIOD
H4_EMA_PERIOD = strat.H4_EMA_PERIOD
SLOPE_N       = strat.SLOPE_N
MIN_H4_BARS   = strat.MIN_H4_BARS
K_EMA         = strat.K_EMA
H4_NS         = strat.H4_NS

LONG_SESSIONS = {
    'asian':  {'range_hours': (3,  6),  'entry_start':  6, 'entry_end': 24},
    'london': {'range_hours': (8,  11), 'entry_start': 11, 'entry_end': 24},
    'ny':     {'range_hours': (15, 18), 'entry_start': 18, 'entry_end': 24},
}

# Best failed-breakout SHORT params
FB_SESSIONS = {
    'london_fb': {'range': (6,  9),  'tp': 3,  'buf': 0.3, 'nc': 4, 'risk': 100.0},
    'ny_fb':     {'range': (12, 15), 'tp': 10, 'buf': 0.3, 'nc': 4, 'risk': 100.0},
}


def _trail_short(t, high):
    risk = t['initial_sl'] - t['entry']
    rr   = (t['entry'] - high) / risk
    for trigger, lock in t['trail_steps']:
        if rr >= trigger:
            t['sl'] = min(t['sl'], t['entry'] - lock * risk)
            break


def run(df, h4_times, h4_ema20):
    n_h4 = len(h4_times)
    times_ns = df.index.asi8
    m15 = df.to_numpy()
    col = {c: i for i, c in enumerate(df.columns)}
    i_h = col['high']; i_l = col['low']; i_c = col['close']; i_a = col['atr']

    ptr_closed     = -1
    forming_period = -1
    forming_close  = np.nan
    ema_base       = np.nan

    active_long  = {}
    active_short = {}   # fb_sess_name -> trade

    balance    = 10_000.0
    peak       = 10_000.0
    max_dd     = 0.0
    max_dly_dd = 0.0
    prev_date  = None
    day_start  = balance

    ls_highs = {}; ls_lows = {}

    # Per-fb-session state
    fb_state = {
        sn: {
            'sh': 0.0, 'bars_above': 0,
            'ok': False, 'peak': 0.0, 'done': False,
        }
        for sn in FB_SESSIONS
    }

    trades_l = []
    trades_s = []

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
            ls_highs = {}; ls_lows = {}
            for sn in fb_state:
                fb_state[sn] = {'sh': 0.0, 'bars_above': 0, 'ok': False, 'peak': 0.0, 'done': False}

        while ptr_closed + 1 < n_h4 and h4_times[ptr_closed + 1] + H4_NS <= ts_ns:
            ptr_closed += 1

        if ptr_closed < MIN_H4_BARS - 1:
            for sn, p in LONG_SESSIONS.items():
                sh, eh = p['range_hours']
                if sh <= hour < eh:
                    ls_highs[sn] = max(ls_highs.get(sn, 0),   high)
                    ls_lows[sn]  = min(ls_lows.get(sn, 1e9),  low)
            for sn, cfg in FB_SESSIONS.items():
                rs, re = cfg['range']
                if rs <= hour < re:
                    fb_state[sn]['sh'] = max(fb_state[sn]['sh'], high)
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

        h4_ema = forming_close * K_EMA + ema_base * (1.0 - K_EMA)

        ema_ok_l   = forming_close > ema_base
        slope_ok_l = (ptr_closed >= SLOPE_N
                      and not np.isnan(h4_ema20[ptr_closed - SLOPE_N])
                      and h4_ema > h4_ema20[ptr_closed - SLOPE_N])

        # Manage LONG
        for sn in list(active_long.keys()):
            t = active_long[sn]
            strat._trail(t, low)
            if low <= t['sl']:
                t['pnl'] = (t['sl'] - t['entry']) * t['size']
                balance += t['pnl']; t['year'] = cur_ts.year
                trades_l.append(t); del active_long[sn]
            elif high >= t['tp']:
                t['pnl'] = (t['tp'] - t['entry']) * t['size']
                balance += t['pnl']; t['year'] = cur_ts.year
                trades_l.append(t); del active_long[sn]

        # Manage SHORT
        for sn in list(active_short.keys()):
            t = active_short[sn]
            _trail_short(t, high)
            if high >= t['sl']:
                t['pnl'] = (t['entry'] - t['sl']) * t['size']
                balance += t['pnl']; t['year'] = cur_ts.year
                trades_s.append(t); del active_short[sn]
            elif low <= t['tp']:
                t['pnl'] = (t['entry'] - t['tp']) * t['size']
                balance += t['pnl']; t['year'] = cur_ts.year
                trades_s.append(t); del active_short[sn]

        if balance > peak: peak = balance
        dd = (peak - balance) / peak * 100
        if dd > max_dd: max_dd = dd

        # Build LONG ranges
        for sn, p in LONG_SESSIONS.items():
            sh, eh = p['range_hours']
            if sh <= hour < eh:
                ls_highs[sn] = max(ls_highs.get(sn, 0),   high)
                ls_lows[sn]  = min(ls_lows.get(sn, 1e9),  low)

        # Build FB ranges + entries
        for sn, cfg in FB_SESSIONS.items():
            rs, re = cfg['range']
            if rs <= hour < re:
                fb_state[sn]['sh'] = max(fb_state[sn]['sh'], high)
                continue

            st = fb_state[sn]
            if (st['sh'] > 0 and hour >= re
                    and not st['done'] and sn not in active_short):
                if not st['ok']:
                    if close > st['sh']:
                        st['bars_above'] += 1
                        st['peak'] = max(st['peak'], high)
                        if st['bars_above'] >= cfg['nc']:
                            st['ok'] = True
                else:
                    st['peak'] = max(st['peak'], high)
                    if close < st['sh']:
                        sl_s  = st['peak'] + cfg['buf'] * atr
                        rsk_s = sl_s - close
                        if rsk_s > 0:
                            trail_steps = [(j, j-1) for j in range(int(cfg['tp'])-1, 1, -1)]
                            tp_price    = close - cfg['tp'] * rsk_s
                            active_short[sn] = {
                                'entry':       close,
                                'sl':          sl_s,
                                'initial_sl':  sl_s,
                                'tp':          tp_price,
                                'size':        cfg['risk'] / rsk_s,
                                'session':     sn,
                                'trail_steps': trail_steps,
                            }
                            st['done'] = True

        # LONG entries
        if ema_ok_l and slope_ok_l:
            for sn, p in LONG_SESSIONS.items():
                if sn not in ls_highs or sn in active_long:
                    continue
                if not (p['entry_start'] <= hour < p['entry_end']):
                    continue
                if close > ls_highs[sn]:
                    sl_l  = ls_lows[sn] - ATR_BUFFER_L * atr
                    rsk_l = close - sl_l
                    if rsk_l <= 0: continue
                    active_long[sn] = {
                        'entry':      close,
                        'sl':         sl_l,
                        'initial_sl': sl_l,
                        'tp':         close + rsk_l * TP_RR_L,
                        'size':       RISK_L / rsk_l,
                        'session':    sn,
                    }

    # Final daily DD
    if prev_date is not None and day_start > 0:
        dd = (day_start - balance) / day_start * 100
        if dd > max_dly_dd: max_dly_dd = dd

    last_close = float(m15[-1, i_c])
    last_year  = int(df.index[-1].year)
    for sn, t in active_long.items():
        t['pnl'] = (last_close - t['entry']) * t['size']
        balance += t['pnl']; t['year'] = last_year; trades_l.append(t)
    for sn, t in active_short.items():
        t['pnl'] = (t['entry'] - last_close) * t['size']
        balance += t['pnl']; t['year'] = last_year; trades_s.append(t)

    def stats(trades, label):
        if not trades:
            return pd.DataFrame()
        tdf = pd.DataFrame(trades)
        tdf['direction'] = label
        return tdf

    tdf_l = stats(trades_l, 'LONG')
    tdf_s = stats(trades_s, 'SHORT')
    tdf   = pd.concat([tdf_l, tdf_s], ignore_index=True) if len(tdf_l) and len(tdf_s) else (tdf_l if len(tdf_l) else tdf_s)

    yearly_l = tdf_l.groupby('year')['pnl'].sum() if len(tdf_l) else pd.Series(dtype=float)
    yearly_s = tdf_s.groupby('year')['pnl'].sum() if len(tdf_s) else pd.Series(dtype=float)
    all_years = sorted(set(list(yearly_l.index) + list(yearly_s.index)))
    yearly_combined = pd.Series({y: yearly_l.get(y, 0) + yearly_s.get(y, 0) for y in all_years})

    return {
        'balance':   balance,
        'pnl':       balance - 10_000,
        'max_dd':    max_dd,
        'max_dly_dd': max_dly_dd,
        'trades_l':  tdf_l,
        'trades_s':  tdf_s,
        'yearly_l':  yearly_l,
        'yearly_s':  yearly_s,
        'yearly':    yearly_combined,
        'all_pos':   bool(all(yearly_combined > 0)) if len(yearly_combined) else False,
    }


def main():
    data_path = (
        Path(__file__).parent.parent
        / "data_cache" / "dukascopy" / "m15" / "XAUUSD"
        / "xauusd_m15_2020-01-01_2026-04-18.parquet"
    )

    print("=" * 68)
    print("JOINT VALIDATION: LONG + london_fb SHORT + ny_fb SHORT")
    print("=" * 68)
    print("LONG  : asian(3-6) london(8-11) ny(15-18)  TP=12R  Risk=$100")
    for sn, cfg in FB_SESSIONS.items():
        rs, re = cfg['range']
        print(f"SHORT {sn:<12}: range {rs}-{re}  TP={cfg['tp']}R  buf={cfg['buf']}  nc={cfg['nc']}  Risk=${cfg['risk']:.0f}")
    print("=" * 68)

    df = pd.read_parquet(data_path).sort_index()
    df['atr'] = strat._atr(df, ATR_PERIOD)
    df_h4 = df.resample('4h').agg(
        {'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last'}
    ).dropna()
    df_h4['ema20'] = strat._ema(df_h4, H4_EMA_PERIOD)
    h4_times = df_h4.index.asi8
    h4_ema20 = df_h4['ema20'].to_numpy()

    print(f"Data: {df.index[0].date()} -- {df.index[-1].date()}  ({len(df):,} bars)\n")

    r = run(df, h4_times, h4_ema20)

    tl = r['trades_l']; ts = r['trades_s']
    n_l  = len(tl); n_s  = len(ts)
    wr_l = (tl['pnl'] > 0).sum() / n_l if n_l else 0
    wr_s = (ts['pnl'] > 0).sum() / n_s if n_s else 0
    pnl_l = tl['pnl'].sum() if n_l else 0
    pnl_s = ts['pnl'].sum() if n_s else 0

    print(f"{'':30}  {'N':>5}  {'WR':>6}  {'PnL':>9}")
    print(f"  {'LONG (asian+london+ny)':<28}  {n_l:5d}  {wr_l:.1%}  ${pnl_l:>8,.0f}")
    print(f"  {'SHORT (london_fb+ny_fb)':<28}  {n_s:5d}  {wr_s:.1%}  ${pnl_s:>8,.0f}")
    print(f"  {'':-<28}  {'-----'}  {'------'}  {'---------'}")
    print(f"  {'COMBINED':<28}  {n_l+n_s:5d}         ${r['pnl']:>8,.0f}")
    print()
    print(f"  MaxDD      = {r['max_dd']:.2f}%   {'OK' if r['max_dd'] < 10 else 'FAIL (>10%)'}")
    print(f"  MaxDailyDD = {r['max_dly_dd']:.2f}%   {'OK' if r['max_dly_dd'] < 5 else 'FAIL (>5%)'}")
    print(f"  AllYrs     = {'YES' if r['all_pos'] else 'NO'}")
    print()

    # Yearly breakdown
    yl = r['yearly_l']; ys = r['yearly_s']; yc = r['yearly']
    all_years = sorted(yc.index)
    print(f"  {'Year':<6}  {'LONG':>8}  {'SHORT':>8}  {'COMBINED':>10}  {'OK?':>4}")
    print("  " + "-" * 46)
    for y in all_years:
        vl = yl.get(y, 0); vs = ys.get(y, 0); vc = yc.get(y, 0)
        ok = 'OK' if vc > 0 else 'LOSS'
        print(f"  {y:<6}  ${vl:>7,.0f}  ${vs:>7,.0f}  ${vc:>9,.0f}  {ok:>4}")
    print()

    # By SHORT session
    if n_s > 0:
        print("  SHORT by session:")
        for sn in FB_SESSIONS:
            sub = ts[ts['session'] == sn] if 'session' in ts.columns else pd.DataFrame()
            if len(sub) == 0: continue
            ns = len(sub); ws = (sub['pnl'] > 0).sum() / ns; ps = sub['pnl'].sum()
            print(f"    {sn:<14}  N={ns:4d}  WR={ws:.1%}  PnL=${ps:>7,.0f}")
    print()
    print("=" * 68)
    verdict = ('PASS' if r['max_dd'] < 10 and r['max_dly_dd'] < 5 and r['all_pos'] else 'FAIL')
    print(f"  VERDICT: {verdict}")
    print("=" * 68)


if __name__ == "__main__":
    main()
