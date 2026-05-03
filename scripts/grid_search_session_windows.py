"""
Grid search: session time windows (range formation + entry hours).
Optimizes each session independently, then combines best.
Baseline: asian(7-10, 10-24), london(13-16, 16-24), ny(13-17, 18-21)
Data: UTC timezone.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
import numpy as np
from pathlib import Path
import importlib.util
import time
from itertools import product

# ── Load strategy functions ───────────────────────────────────────────────────
spec = importlib.util.spec_from_file_location(
    "strat",
    Path(__file__).parent.parent / "astra_v2" / "strategies" / "session_long_nolookahead_v1.py"
)
strat = importlib.util.module_from_spec(spec)
spec.loader.exec_module(strat)

RISK          = strat.RISK
ATR_PERIOD    = strat.ATR_PERIOD
H4_EMA_PERIOD = strat.H4_EMA_PERIOD
SLOPE_N       = strat.SLOPE_N
MIN_H4_BARS   = strat.MIN_H4_BARS
ATR_BUFFER    = strat.ATR_BUFFER
TP_RR         = strat.TP_RR
K_EMA         = strat.K_EMA
H4_NS         = strat.H4_NS

# ── Backtest with custom sessions ─────────────────────────────────────────────
def run_custom(df, h4_times, h4_ema20, sessions):
    """Same logic as strat.run() but with custom session windows."""
    n_h4     = len(h4_times)
    times_ns = df.index.asi8
    m15      = df.to_numpy()
    col      = {c: i for i, c in enumerate(df.columns)}
    i_h = col['high']; i_l = col['low']; i_c = col['close']; i_a = col['atr']

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
    prev_date    = None
    day_start    = balance
    s_highs      = {}
    s_lows       = {}

    trail_steps = [(i, i-1) for i in range(int(TP_RR)-1, 1, -1)]

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
                if dd > max_daily_dd: max_daily_dd = dd
            day_start = balance
            prev_date = cur_date
            s_highs   = {}
            s_lows    = {}

        while ptr_closed + 1 < n_h4 and h4_times[ptr_closed + 1] + H4_NS <= ts_ns:
            ptr_closed += 1

        if ptr_closed < MIN_H4_BARS - 1:
            for sn, p in sessions.items():
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
        slope_ok = (ptr_closed >= SLOPE_N) \
                   and not np.isnan(h4_ema20[ptr_closed - SLOPE_N]) \
                   and (h4_ema > h4_ema20[ptr_closed - SLOPE_N])

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

        for sn, p in sessions.items():
            sh, eh = p['range_hours']
            if sh <= hour < eh:
                s_highs[sn] = max(s_highs.get(sn, 0),   high)
                s_lows[sn]  = min(s_lows.get(sn, 1e9),  low)

        if not (ema_ok and slope_ok):
            continue

        for sn, p in sessions.items():
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
                    'session': sn,
                    'steps': trail_steps,
                }

    if prev_date is not None and day_start > 0:
        dd = (day_start - balance) / day_start * 100
        if dd > max_daily_dd: max_daily_dd = dd

    last_close = float(m15[-1, i_c])
    last_year  = int(df.index[-1].year)
    for sn, t in active_long.items():
        t['pnl'] = (last_close - t['entry']) * t['size']
        balance += t['pnl']; t['year'] = last_year; trades.append(t)

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


def passes(r):
    return r['max_dd'] < 10.0 and r['max_daily_dd'] < 5.0 and r['all_pos']


def print_row(label, r):
    allyr = 'YES' if r['all_pos'] else 'NO '
    star  = ' ***' if passes(r) else ''
    print(f"  {label:<28} N={r['n']:4d}  WR={r['wr']:.1%}  PnL=${r['pnl']:>8,.0f}  "
          f"MaxDD={r['max_dd']:.2f}%  DlyDD={r['max_daily_dd']:.2f}%  AllYrs={allyr}{star}")


def make_sessions(asian_rng, asian_ent,
                  london_rng, london_ent,
                  ny_rng, ny_ent):
    return {
        'asian':  {'range_hours': asian_rng,  'entry_start': asian_ent[0],  'entry_end': asian_ent[1]},
        'london': {'range_hours': london_rng, 'entry_start': london_ent[0], 'entry_end': london_ent[1]},
        'ny':     {'range_hours': ny_rng,     'entry_start': ny_ent[0],     'entry_end': ny_ent[1]},
    }


# ── Baseline sessions ─────────────────────────────────────────────────────────
BASELINE = make_sessions(
    (7, 10), (10, 24),
    (13, 16), (16, 24),
    (13, 17), (18, 21),
)

# ── Candidate time windows ────────────────────────────────────────────────────
# Asian: real Tokyo = 0-9 UTC; pre-London overlap = 6-10
ASIAN_RANGES   = [(s, s+w) for s in range(0, 10) for w in [2, 3, 4] if s+w <= 13]
# London: opens 8 UTC; peak 8-16
LONDON_RANGES  = [(s, s+w) for s in range(6, 14) for w in [2, 3, 4] if s+w <= 17]
# NY: opens 13 UTC
NY_RANGES      = [(s, s+w) for s in range(12, 18) for w in [2, 3, 4] if s+w <= 21]
NY_ENTRY_ENDS  = [20, 21, 22, 24]


def main():
    data_path = (
        Path(__file__).parent.parent
        / "data_cache" / "dukascopy" / "m15" / "XAUUSD"
        / "xauusd_m15_2020-01-01_2026-04-18.parquet"
    )

    df = pd.read_parquet(data_path).sort_index()
    df['atr'] = strat._atr(df, ATR_PERIOD)
    df_h4 = df.resample('4h').agg(
        {'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last'}
    ).dropna()
    df_h4['ema20'] = strat._ema(df_h4, H4_EMA_PERIOD)
    h4_times = df_h4.index.asi8
    h4_ema20 = df_h4['ema20'].to_numpy()

    print("=" * 80)
    print("Session Time Window Grid Search  (TP=12R, Risk=$100, UTC)")
    print(f"  Asian candidates   : {len(ASIAN_RANGES)} range windows")
    print(f"  London candidates  : {len(LONDON_RANGES)} range windows")
    print(f"  NY candidates      : {len(NY_RANGES)} x {len(NY_ENTRY_ENDS)} entry_ends")
    print("=" * 80)

    # ── Baseline ──────────────────────────────────────────────────────────────
    print("\n[Baseline]")
    rb = run_custom(df, h4_times, h4_ema20, BASELINE)
    print_row("asian(7-10) lon(13-16) ny(13-17,18-21)", rb)

    # ── 1. Optimize ASIAN (keep london+ny fixed) ──────────────────────────────
    print(f"\n[1/3] Optimizing ASIAN  ({len(ASIAN_RANGES)} combos)...")
    best_asian = []; t0 = time.time()
    for rs, re in ASIAN_RANGES:
        sess = make_sessions((rs,re),(re,24), (13,16),(16,24), (13,17),(18,21))
        r = run_custom(df, h4_times, h4_ema20, sess)
        r['rng'] = (rs, re)
        best_asian.append(r)
    print(f"  Done in {time.time()-t0:.0f}s. Top 8 by PnL (filter: DD<10%, Dly<5%, AllYrs):")
    passed = [x for x in best_asian if passes(x)]
    src = passed if passed else best_asian
    for r in sorted(src, key=lambda x: -x['pnl'])[:8]:
        rs, re = r['rng']
        print_row(f"asian({rs}-{re}, entry {re}-24)", r)

    best_asian_rng = sorted(src, key=lambda x: -x['pnl'])[0]['rng']
    print(f"  >> Best asian range: {best_asian_rng}")

    # ── 2. Optimize LONDON (keep best asian + ny fixed) ───────────────────────
    print(f"\n[2/3] Optimizing LONDON  ({len(LONDON_RANGES)} combos)...")
    best_london = []; t0 = time.time()
    for rs, re in LONDON_RANGES:
        sess = make_sessions(best_asian_rng,(best_asian_rng[1],24),
                             (rs,re),(re,24),
                             (13,17),(18,21))
        r = run_custom(df, h4_times, h4_ema20, sess)
        r['rng'] = (rs, re)
        best_london.append(r)
    print(f"  Done in {time.time()-t0:.0f}s. Top 8 by PnL (filter):")
    passed = [x for x in best_london if passes(x)]
    src = passed if passed else best_london
    for r in sorted(src, key=lambda x: -x['pnl'])[:8]:
        rs, re = r['rng']
        print_row(f"london({rs}-{re}, entry {re}-24)", r)

    best_london_rng = sorted(src, key=lambda x: -x['pnl'])[0]['rng']
    print(f"  >> Best london range: {best_london_rng}")

    # ── 3. Optimize NY (keep best asian + london) ─────────────────────────────
    ny_combos = [(rs, re, ee)
                 for (rs, re) in NY_RANGES
                 for ee in NY_ENTRY_ENDS
                 if re < ee]   # entry_end must be after range_end
    print(f"\n[3/3] Optimizing NY  ({len(ny_combos)} combos)...")
    best_ny = []; t0 = time.time()
    for rs, re, ee in ny_combos:
        es = re  # entry starts right after range
        sess = make_sessions(best_asian_rng,(best_asian_rng[1],24),
                             best_london_rng,(best_london_rng[1],24),
                             (rs,re),(es,ee))
        r = run_custom(df, h4_times, h4_ema20, sess)
        r['rng'] = (rs, re); r['ee'] = ee
        best_ny.append(r)
    print(f"  Done in {time.time()-t0:.0f}s. Top 8 by PnL (filter):")
    passed = [x for x in best_ny if passes(x)]
    src = passed if passed else best_ny
    for r in sorted(src, key=lambda x: -x['pnl'])[:8]:
        rs, re = r['rng']; ee = r['ee']
        print_row(f"ny({rs}-{re}, entry {re}-{ee})", r)

    best_ny_r    = sorted(src, key=lambda x: -x['pnl'])[0]
    best_ny_rng  = best_ny_r['rng']
    best_ny_ee   = best_ny_r['ee']
    print(f"  >> Best ny: range={best_ny_rng} entry={best_ny_rng[1]}-{best_ny_ee}")

    # ── Final combined ────────────────────────────────────────────────────────
    print("\n" + "=" * 80)
    print("FINAL: Baseline vs Best combined windows")
    print("=" * 80)

    best_sess = make_sessions(
        best_asian_rng,  (best_asian_rng[1], 24),
        best_london_rng, (best_london_rng[1], 24),
        best_ny_rng,     (best_ny_rng[1], best_ny_ee),
    )
    rf = run_custom(df, h4_times, h4_ema20, best_sess)

    print_row("BASELINE", rb)
    ar, ae = best_asian_rng;  lr, le = best_london_rng
    nr, ne = best_ny_rng
    label = f"BEST asian({ar}-{ae}) lon({lr}-{le}) ny({nr}-{ne},{ne}-{best_ny_ee})"
    print_row(label, rf)

    print()
    print("PnL by year:")
    all_years = sorted(set(list(rb['yearly'].index) + list(rf['yearly'].index)))
    print(f"  {'Window':<12}  " + "  ".join(str(y) for y in all_years))
    print("  " + "-" * 70)
    for label2, res in [("Baseline", rb), ("Best", rf)]:
        yr_vals = "  ".join(f"${res['yearly'].get(y, 0):>6,.0f}" for y in all_years)
        print(f"  {label2:<12}  {yr_vals}")

    print()
    print("Session windows summary:")
    print(f"  Asian  : {best_asian_rng[0]:02d}:00-{best_asian_rng[1]:02d}:00 range  |  {best_asian_rng[1]:02d}:00-24:00 entry")
    print(f"  London : {best_london_rng[0]:02d}:00-{best_london_rng[1]:02d}:00 range  |  {best_london_rng[1]:02d}:00-24:00 entry")
    print(f"  NY     : {best_ny_rng[0]:02d}:00-{best_ny_rng[1]:02d}:00 range  |  {best_ny_rng[1]:02d}:00-{best_ny_ee:02d}:00 entry")


if __name__ == "__main__":
    main()
