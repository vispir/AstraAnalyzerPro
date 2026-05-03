"""
Joint grid search: ALL 3 sessions simultaneously.
Tests every combination of (asian_window x london_window x ny_window).
Overlapping windows allowed — flagged in output.
TP=12R, Risk=$100, trailing LOW-trigger.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
import numpy as np
from pathlib import Path
import importlib.util
import time

# ── Load strategy ─────────────────────────────────────────────────────────────
_strat_path = Path(__file__).parent.parent / "astra_v2" / "strategies" / "session_long_nolookahead_v1.py"
spec = importlib.util.spec_from_file_location("strat", _strat_path)
strat = importlib.util.module_from_spec(spec)
spec.loader.exec_module(strat)

RISK = strat.RISK; ATR_PERIOD = strat.ATR_PERIOD; H4_EMA_PERIOD = strat.H4_EMA_PERIOD
SLOPE_N = strat.SLOPE_N; MIN_H4_BARS = strat.MIN_H4_BARS; ATR_BUFFER = strat.ATR_BUFFER
TP_RR = strat.TP_RR; K_EMA = strat.K_EMA; H4_NS = strat.H4_NS

# ── Candidate windows (UTC) ───────────────────────────────────────────────────
# Asian — Tokyo peaks 0-9 UTC, Sydney 21-6 UTC, pre-London overlap 6-10
ASIAN_CANDS = [
    (0, 3),   # deep Tokyo
    (1, 4),   # Tokyo core
    (3, 6),   # late Tokyo
    (5, 8),   # Tokyo/London overlap
    (6, 9),   # pre-London
    (7, 10),  # baseline
    (7, 11),  # wider
    (8, 11),  # greedy best
]

# London — opens 8 UTC, peak 8-17 UTC
LONDON_CANDS = [
    (7, 10),   # London open
    (7, 11),   # greedy best
    (8, 11),   # standard open
    (9, 12),   # morning
    (10, 13),  # midday
    (11, 14),  # afternoon
    (12, 15),  # late London
    (13, 16),  # baseline (London/NY overlap)
]

# NY — opens 13 UTC, active 13-22 UTC
# Format: (range_start, range_end, entry_end)
NY_CANDS = [
    (13, 16, 21),  # baseline-ish
    (13, 16, 24),  # baseline extended
    (14, 17, 21),  # standard NY open
    (14, 17, 24),
    (15, 18, 21),  # greedy best
    (15, 18, 24),  # greedy top
    (16, 18, 21),  # late NY
    (16, 18, 24),
]

N_COMBOS = len(ASIAN_CANDS) * len(LONDON_CANDS) * len(NY_CANDS)


# ── Core backtest (self-contained) ────────────────────────────────────────────
def run_sessions(df, h4_times, h4_ema20, sessions):
    trail_steps = [(i, i-1) for i in range(int(TP_RR)-1, 1, -1)]
    n_h4 = len(h4_times)
    times_ns = df.index.asi8
    m15 = df.to_numpy()
    col = {c: i for i, c in enumerate(df.columns)}
    i_h = col['high']; i_l = col['low']; i_c = col['close']; i_a = col['atr']

    ptr_closed = -1; forming_period = -1
    forming_close = np.nan; ema_base = np.nan
    trades = []; active_long = {}
    balance = 10_000.0; peak = 10_000.0
    max_dd = 0.0; max_daily_dd = 0.0
    prev_date = None; day_start = balance
    s_highs = {}; s_lows = {}

    for i in range(len(df)):
        ts_ns = int(times_ns[i]); cur_ts = df.index[i]
        high = float(m15[i, i_h]); low = float(m15[i, i_l])
        close = float(m15[i, i_c]); atr = float(m15[i, i_a])
        hour = cur_ts.hour
        if np.isnan(atr): continue

        cur_date = cur_ts.date()
        if cur_date != prev_date:
            if prev_date is not None and day_start > 0:
                dd = (day_start - balance) / day_start * 100
                if dd > max_daily_dd: max_daily_dd = dd
            day_start = balance; prev_date = cur_date; s_highs = {}; s_lows = {}

        while ptr_closed + 1 < n_h4 and h4_times[ptr_closed+1] + H4_NS <= ts_ns:
            ptr_closed += 1
        if ptr_closed < MIN_H4_BARS - 1:
            for sn, p in sessions.items():
                sh, eh = p['range_hours']
                if sh <= hour < eh:
                    s_highs[sn] = max(s_highs.get(sn, 0), high)
                    s_lows[sn]  = min(s_lows.get(sn, 1e9), low)
            continue

        h4p = int(ts_ns // int(14400*1e9)) * int(14400*1e9)
        if h4p != forming_period:
            forming_period = h4p; forming_close = close
            ema_base = h4_ema20[ptr_closed] if ptr_closed >= 0 else np.nan
        else:
            forming_close = close
        if np.isnan(ema_base): continue

        h4_ema = forming_close * K_EMA + ema_base * (1.0 - K_EMA)
        ema_ok   = forming_close > ema_base
        slope_ok = (ptr_closed >= SLOPE_N and not np.isnan(h4_ema20[ptr_closed - SLOPE_N])
                    and h4_ema > h4_ema20[ptr_closed - SLOPE_N])

        for sn in list(active_long.keys()):
            t = active_long[sn]
            # trailing: LOW trigger
            risk = t['entry'] - t['initial_sl']
            rr = (low - t['entry']) / risk
            for trig, lock in trail_steps:
                if rr >= trig:
                    t['sl'] = max(t['sl'], t['entry'] + lock * risk); break
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
                s_highs[sn] = max(s_highs.get(sn, 0), high)
                s_lows[sn]  = min(s_lows.get(sn, 1e9), low)

        if not (ema_ok and slope_ok): continue

        for sn, p in sessions.items():
            if sn not in s_highs or sn in active_long: continue
            if not (p['entry_start'] <= hour < p['entry_end']): continue
            if close > s_highs[sn]:
                sl = s_lows[sn] - ATR_BUFFER * atr; rsk = close - sl
                if rsk <= 0: continue
                active_long[sn] = {
                    'entry': close, 'sl': sl, 'initial_sl': sl,
                    'tp': close + rsk * TP_RR, 'size': RISK / rsk, 'session': sn,
                }

    if prev_date is not None and day_start > 0:
        dd = (day_start - balance) / day_start * 100
        if dd > max_daily_dd: max_daily_dd = dd

    last_close = float(m15[-1, i_c]); last_year = int(df.index[-1].year)
    for sn, t in active_long.items():
        t['pnl'] = (last_close - t['entry']) * t['size']
        balance += t['pnl']; t['year'] = last_year; trades.append(t)

    tdf = pd.DataFrame(trades) if trades else pd.DataFrame(columns=['pnl', 'year'])
    pnl = balance - 10_000; n = len(tdf)
    wr = (tdf['pnl'] > 0).sum() / n if n > 0 else 0.0
    yearly = tdf.groupby('year')['pnl'].sum() if n > 0 else pd.Series(dtype=float)
    return {
        'n': n, 'wr': wr, 'pnl': pnl,
        'max_dd': max_dd, 'max_daily_dd': max_daily_dd,
        'yearly': yearly,
        'all_pos': bool(all(yearly > 0)) if len(yearly) > 0 else False,
    }


def overlaps(rng1, rng2):
    """True if two range windows overlap."""
    return rng1[0] < rng2[1] and rng2[0] < rng1[1]


def passes(r):
    return r['max_dd'] < 10.0 and r['max_daily_dd'] < 5.0 and r['all_pos']


def make_sess(ar, lr, nr, nee):
    return {
        'asian':  {'range_hours': ar, 'entry_start': ar[1], 'entry_end': 24},
        'london': {'range_hours': lr, 'entry_start': lr[1], 'entry_end': 24},
        'ny':     {'range_hours': nr, 'entry_start': nr[1], 'entry_end': nee},
    }


def main():
    data_path = (
        Path(__file__).parent.parent
        / "data_cache" / "dukascopy" / "m15" / "XAUUSD"
        / "xauusd_m15_2020-01-01_2026-04-18.parquet"
    )

    print("=" * 80)
    print("JOINT Session Window Grid Search  (TP=12R, Risk=$100, UTC)")
    print(f"  Asian   : {len(ASIAN_CANDS)} candidates")
    print(f"  London  : {len(LONDON_CANDS)} candidates")
    print(f"  NY      : {len(NY_CANDS)} candidates")
    print(f"  Total   : {N_COMBOS} combinations")
    print("=" * 80)

    df = pd.read_parquet(data_path).sort_index()
    df['atr'] = strat._atr(df, ATR_PERIOD)
    df_h4 = df.resample('4h').agg({'open':'first','high':'max','low':'min','close':'last'}).dropna()
    df_h4['ema20'] = strat._ema(df_h4, H4_EMA_PERIOD)
    h4_times = df_h4.index.asi8; h4_ema20 = df_h4['ema20'].to_numpy()

    print(f"Data: {df.index[0].date()} - {df.index[-1].date()}  ({len(df):,} M15 bars)\n")

    results = []
    t0 = time.time(); done = 0

    for ar in ASIAN_CANDS:
        for lr in LONDON_CANDS:
            for (nrs, nre, nee) in NY_CANDS:
                nr = (nrs, nre)
                sess = make_sess(ar, lr, nr, nee)
                r = run_sessions(df, h4_times, h4_ema20, sess)
                r['ar'] = ar; r['lr'] = lr; r['nr'] = nr; r['nee'] = nee
                r['ov_al'] = overlaps(ar, lr)
                r['ov_an'] = overlaps(ar, nr)
                r['ov_ln'] = overlaps(lr, nr)
                r['any_overlap'] = r['ov_al'] or r['ov_an'] or r['ov_ln']
                results.append(r)
                done += 1
                elapsed = time.time() - t0
                eta = elapsed / done * (N_COMBOS - done)
                ok = '*' if passes(r) else ' '
                ov = 'OV' if r['any_overlap'] else '  '
                print(f"  {ok}{ov} [{done:3d}/{N_COMBOS}] "
                      f"a({ar[0]}-{ar[1]}) l({lr[0]}-{lr[1]}) n({nrs}-{nre},{nee})  "
                      f"N={r['n']:3d} WR={r['wr']:.0%} PnL=${r['pnl']:>7,.0f} "
                      f"DD={r['max_dd']:.1f}% dDD={r['max_daily_dd']:.1f}% "
                      f"AllY={'Y' if r['all_pos'] else 'N'}  "
                      f"ETA={eta:.0f}s", flush=True)

    total = time.time() - t0
    print(f"\nDone in {total:.0f}s\n")

    # ── Results ───────────────────────────────────────────────────────────────
    passed   = [r for r in results if passes(r)]
    all_years = sorted(set(yr for r in results for yr in r['yearly'].index))

    def print_result(r, rank=None):
        ov = ''
        if r['ov_al']: ov += 'A+L '
        if r['ov_an']: ov += 'A+N '
        if r['ov_ln']: ov += 'L+N '
        ov = ov.strip() or 'no-overlap'
        prefix = f"  #{rank:3d}" if rank else "      "
        print(f"{prefix}  a({r['ar'][0]}-{r['ar'][1]}) l({r['lr'][0]}-{r['lr'][1]}) "
              f"n({r['nr'][0]}-{r['nr'][1]},{r['nee']})  "
              f"N={r['n']:4d}  WR={r['wr']:.1%}  PnL=${r['pnl']:>8,.0f}  "
              f"MaxDD={r['max_dd']:.2f}%  DlyDD={r['max_daily_dd']:.2f}%  "
              f"AllY={'Y' if r['all_pos'] else 'N'}  [{ov}]")

    print("=" * 80)
    print(f"TOP 20  (filter: DD<10%, DlyDD<5%, AllYrs=YES)  [{len(passed)} passed / {N_COMBOS} total]")
    print("=" * 80)
    print(f"   {'#':>3}  {'windows (asian/london/ny)':40s}  {'N':>4}  {'WR':>6}  "
          f"{'PnL':>9}  {'MaxDD':>7}  {'DlyDD':>6}  AllY  overlap")
    print("  " + "-" * 105)

    top = sorted(passed, key=lambda x: -x['pnl'])
    for i, r in enumerate(top[:20], 1):
        print_result(r, i)

    if len(top) == 0:
        print("  (none passed all filters)")
        print("\n  Top 10 by PnL (no filter):")
        for i, r in enumerate(sorted(results, key=lambda x: -x['pnl'])[:10], 1):
            print_result(r, i)

    # ── No-overlap only ───────────────────────────────────────────────────────
    no_ov = [r for r in passed if not r['any_overlap']]
    print(f"\n--- No-overlap only  [{len(no_ov)} results] ---")
    for i, r in enumerate(sorted(no_ov, key=lambda x: -x['pnl'])[:10], 1):
        print_result(r, i)

    # ── Baseline ──────────────────────────────────────────────────────────────
    print(f"\n--- Baseline (for reference) ---")
    bl = next((r for r in results
               if r['ar']==(7,10) and r['lr']==(13,16) and r['nr']==(13,17)), None)
    # baseline NY is (13,17) which is not in our NY_CANDS, find closest
    if bl is None:
        bl = next((r for r in results
                   if r['ar']==(7,10) and r['lr']==(13,16)), None)
    if bl:
        print_result(bl)

    # ── PnL by year for top 5 ─────────────────────────────────────────────────
    print(f"\n--- PnL by year: Top 5 (filtered) + Baseline ---")
    yr_hdr = "  ".join(str(y) for y in all_years)
    print(f"  {'Label':<42}  {yr_hdr}")
    print("  " + "-" * (44 + 8 * len(all_years)))

    show = top[:5]
    if bl and bl not in show:
        show = show + [bl]

    for r in show:
        label = f"a({r['ar'][0]}-{r['ar'][1]}) l({r['lr'][0]}-{r['lr'][1]}) n({r['nr'][0]}-{r['nr'][1]},{r['nee']})"
        yr_vals = "  ".join(f"${r['yearly'].get(y, 0):>6,.0f}" for y in all_years)
        print(f"  {label:<42}  {yr_vals}")

    # ── Best summary ──────────────────────────────────────────────────────────
    if top:
        best = top[0]
        print(f"\n{'='*80}")
        print(f"BEST COMBINATION:")
        print(f"  Asian  : {best['ar'][0]:02d}:00-{best['ar'][1]:02d}:00 range  |  {best['ar'][1]:02d}:00-24:00 entry  (UTC)")
        print(f"  London : {best['lr'][0]:02d}:00-{best['lr'][1]:02d}:00 range  |  {best['lr'][1]:02d}:00-24:00 entry  (UTC)")
        print(f"  NY     : {best['nr'][0]:02d}:00-{best['nr'][1]:02d}:00 range  |  {best['nr'][1]:02d}:00-{best['nee']:02d}:00 entry  (UTC)")
        ov = 'YES ('+('A+L ' if best['ov_al'] else '')+('A+N ' if best['ov_an'] else '')+('L+N ' if best['ov_ln'] else '')+')' if best['any_overlap'] else 'NO'
        print(f"  Overlap: {ov}")
        print(f"  N={best['n']}  WR={best['wr']:.1%}  PnL=${best['pnl']:,.0f}  MaxDD={best['max_dd']:.2f}%  DlyDD={best['max_daily_dd']:.2f}%  AllYrs=YES")
        print('='*80)


if __name__ == "__main__":
    main()
