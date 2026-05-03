"""
Grid search: FAILED BREAKOUT SHORT -- complement to LONG.

Logic (no look-ahead, M15 only, no H4):
  1. Build session range bar-by-bar during range_hours
  2. Entry window (hour >= range_end):
     - count M15 bars where close > session_high
     - once count >= N_CONFIRM: breakout confirmed, track peak_high
  3. After confirmation: if close < session_high --> SHORT (failed breakout)
  4. SL: peak_high + ATR_BUFFER * atr  (above highest point reached)
  5. TP: entry - rsk * TP_RR
  6. Trailing: HIGH-trigger (conservative), steps (TP-1)R -> (TP-2)R -> ... -> 1R
  7. One SHORT per session per day

LONG fixed: asian(3-6), london(8-11), ny(15-18), TP=12R, Risk=$100.
Combined balance --> DD is joint LONG+SHORT.

Grid parameters:
  Sessions    : asian_fb, london_fb, ny_fb (searched one at a time)
  range_hours : per-session candidates
  TP_RR       : [3, 5, 7, 10]
  ATR_BUFFER  : [0.3, 0.5, 1.0]
  N_CONFIRM   : [1, 2, 4]   -- M15 bars above session_high to confirm breakout
  SHORT_RISK  : $100 (fixed)
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
import numpy as np
from pathlib import Path
import importlib.util
import time

# -- Load LONG strategy module -------------------------------------------------
_sp = Path(__file__).parent.parent / "astra_v2" / "strategies" / "session_long_nolookahead_v1.py"
spec = importlib.util.spec_from_file_location("strat", _sp)
strat = importlib.util.module_from_spec(spec)
spec.loader.exec_module(strat)

# -- LONG constants (fixed) ---------------------------------------------------
RISK_L        = strat.RISK          # 100
TP_RR_L       = strat.TP_RR         # 12
ATR_BUFFER_L  = strat.ATR_BUFFER    # 0.5
ATR_PERIOD    = strat.ATR_PERIOD    # 14
H4_EMA_PERIOD = strat.H4_EMA_PERIOD # 20
SLOPE_N       = strat.SLOPE_N       # 5
MIN_H4_BARS   = strat.MIN_H4_BARS   # 22
K_EMA         = strat.K_EMA
H4_NS         = strat.H4_NS

LONG_SESSIONS = {
    'asian':  {'range_hours': (3,  6),  'entry_start':  6, 'entry_end': 24},
    'london': {'range_hours': (8,  11), 'entry_start': 11, 'entry_end': 24},
    'ny':     {'range_hours': (15, 18), 'entry_start': 18, 'entry_end': 24},
}

# -- SHORT failed-breakout grid ------------------------------------------------
SHORT_RISK = 100.0

# Range candidates per session (hour_start, hour_end UTC)
FB_ASIAN_RANGES  = [(0,3), (2,5), (3,6), (5,8), (7,10), (8,11)]
FB_LONDON_RANGES = [(6,9), (7,10), (8,11), (10,13), (13,16)]
FB_NY_RANGES     = [(12,15), (13,16), (14,17), (15,18), (16,19)]

SESSION_RANGES = {
    'asian_fb':  FB_ASIAN_RANGES,
    'london_fb': FB_LONDON_RANGES,
    'ny_fb':     FB_NY_RANGES,
}

TP_VALUES   = [3, 5, 7, 10]
ATR_BUFFERS = [0.3, 0.5, 1.0]
N_CONFIRMS  = [1, 2, 4]


# -- Trailing SHORT (HIGH-trigger) --------------------------------------------
def _trail_short(t: dict, high: float) -> None:
    risk = t['initial_sl'] - t['entry']   # positive: SL above entry
    rr   = (t['entry'] - high) / risk     # >0 when price moved below entry (profit)
    for trigger, lock in t['trail_steps']:
        if rr >= trigger:
            t['sl'] = min(t['sl'], t['entry'] - lock * risk)
            break


# -- Combined backtest LONG + Failed Breakout SHORT ---------------------------
def run_combined(df: pd.DataFrame,
                 h4_times: np.ndarray,
                 h4_ema20: np.ndarray,
                 fb_sess_name: str,
                 fb_range: tuple,
                 tp_s: float,
                 atrbuf_s: float,
                 n_confirm: int) -> dict:
    """
    LONG (fixed) + Failed Breakout SHORT (one session) on shared balance.

    P&L signs:
      LONG SL hit:  (sl - entry)*size  --> sl < entry --> negative (loss)   OK
      LONG TP hit:  (tp - entry)*size  --> tp > entry --> positive (profit)  OK
      SHORT SL hit: (entry - sl)*size  --> sl > entry --> negative (loss)    OK
      SHORT TP hit: (entry - tp)*size  --> tp < entry --> positive (profit)  OK
    """
    trail_steps_s = [(i, i - 1) for i in range(int(tp_s) - 1, 1, -1)]

    n_h4 = len(h4_times)
    times_ns = df.index.asi8
    m15 = df.to_numpy()
    col = {c: i for i, c in enumerate(df.columns)}
    i_h = col['high']; i_l = col['low']; i_c = col['close']; i_a = col['atr']

    ptr_closed     = -1
    forming_period = -1
    forming_close  = np.nan
    ema_base       = np.nan

    active_long  = {}   # sn -> trade dict
    active_short = {}   # fb_sess_name -> trade dict (at most 1)

    balance    = 10_000.0
    peak       = 10_000.0
    max_dd     = 0.0
    max_dly_dd = 0.0
    prev_date  = None
    day_start  = balance

    # LONG session ranges
    ls_highs = {}; ls_lows = {}

    # Failed-breakout state (reset daily)
    fb_sh         = 0.0    # session range high
    fb_bars_above = 0      # M15 bars closed above fb_sh in entry window
    fb_ok         = False  # breakout confirmed (>= n_confirm bars above)
    fb_peak       = 0.0    # max HIGH seen since first close > fb_sh
    fb_done       = False  # one SHORT per session per day

    trades_l = []
    trades_s = []

    fb_rs, fb_re = fb_range          # range start/end hour
    fb_entry_start = fb_re           # entry window opens when range closes

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

        # -- Daily reset -------------------------------------------------------
        cur_date = cur_ts.date()
        if cur_date != prev_date:
            if prev_date is not None and day_start > 0:
                dd = (day_start - balance) / day_start * 100
                if dd > max_dly_dd: max_dly_dd = dd
            day_start = balance
            prev_date = cur_date
            ls_highs = {}; ls_lows = {}
            fb_sh = 0.0; fb_bars_above = 0
            fb_ok = False; fb_peak = 0.0; fb_done = False

        # -- H4 pointer (only closed bars) ------------------------------------
        while ptr_closed + 1 < n_h4 and h4_times[ptr_closed + 1] + H4_NS <= ts_ns:
            ptr_closed += 1

        # -- Warmup: collect ranges, no trades --------------------------------
        if ptr_closed < MIN_H4_BARS - 1:
            for sn, p in LONG_SESSIONS.items():
                sh, eh = p['range_hours']
                if sh <= hour < eh:
                    ls_highs[sn] = max(ls_highs.get(sn, 0),   high)
                    ls_lows[sn]  = min(ls_lows.get(sn, 1e9),  low)
            if fb_rs <= hour < fb_re:
                fb_sh = max(fb_sh, high)
            continue

        # -- H4 EMA forming bar -----------------------------------------------
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

        # -- LONG filters (bullish H4) ----------------------------------------
        ema_ok_l   = forming_close > ema_base
        slope_ok_l = (ptr_closed >= SLOPE_N
                      and not np.isnan(h4_ema20[ptr_closed - SLOPE_N])
                      and h4_ema > h4_ema20[ptr_closed - SLOPE_N])

        # -- Manage LONG trades (always) --------------------------------------
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

        # -- Manage SHORT trades (always) -------------------------------------
        for sn in list(active_short.keys()):
            t = active_short[sn]
            _trail_short(t, high)
            if high >= t['sl']:
                # SL hit (price rose to SL)
                t['pnl'] = (t['entry'] - t['sl']) * t['size']
                balance += t['pnl']; t['year'] = cur_ts.year
                trades_s.append(t); del active_short[sn]
            elif low <= t['tp']:
                # TP hit (price fell to TP)
                t['pnl'] = (t['entry'] - t['tp']) * t['size']
                balance += t['pnl']; t['year'] = cur_ts.year
                trades_s.append(t); del active_short[sn]

        # -- Combined DD ------------------------------------------------------
        if balance > peak: peak = balance
        dd = (peak - balance) / peak * 100
        if dd > max_dd: max_dd = dd

        # -- Build LONG ranges ------------------------------------------------
        for sn, p in LONG_SESSIONS.items():
            sh, eh = p['range_hours']
            if sh <= hour < eh:
                ls_highs[sn] = max(ls_highs.get(sn, 0),   high)
                ls_lows[sn]  = min(ls_lows.get(sn, 1e9),  low)

        # -- Build failed-breakout range --------------------------------------
        if fb_rs <= hour < fb_re:
            fb_sh = max(fb_sh, high)

        # -- LONG entries -----------------------------------------------------
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

        # -- Failed Breakout SHORT entry --------------------------------------
        sn = fb_sess_name
        if (fb_sh > 0
                and hour >= fb_entry_start
                and not fb_done
                and sn not in active_short):

            if not fb_ok:
                # Phase 1: count bars above range high
                if close > fb_sh:
                    fb_bars_above += 1
                    fb_peak = max(fb_peak, high)
                    if fb_bars_above >= n_confirm:
                        fb_ok = True
            else:
                # Phase 2: breakout confirmed, track peak, wait for failure
                fb_peak = max(fb_peak, high)
                if close < fb_sh:
                    # Failed breakout! Enter SHORT
                    sl_s  = fb_peak + atrbuf_s * atr
                    rsk_s = sl_s - close   # sl_s > close --> positive
                    if rsk_s > 0:
                        tp_s_price = close - tp_s * rsk_s   # below entry
                        active_short[sn] = {
                            'entry':       close,
                            'sl':          sl_s,
                            'initial_sl':  sl_s,
                            'tp':          tp_s_price,
                            'size':        SHORT_RISK / rsk_s,
                            'session':     sn,
                            'trail_steps': trail_steps_s,
                        }
                        fb_done = True

    # -- Final daily DD -------------------------------------------------------
    if prev_date is not None and day_start > 0:
        dd = (day_start - balance) / day_start * 100
        if dd > max_dly_dd: max_dly_dd = dd

    # -- Close open trades at last price --------------------------------------
    last_close = float(m15[-1, i_c])
    last_year  = int(df.index[-1].year)
    for sn, t in active_long.items():
        t['pnl'] = (last_close - t['entry']) * t['size']
        balance += t['pnl']; t['year'] = last_year; trades_l.append(t)
    for sn, t in active_short.items():
        t['pnl'] = (t['entry'] - last_close) * t['size']
        balance += t['pnl']; t['year'] = last_year; trades_s.append(t)

    # -- Stats ----------------------------------------------------------------
    def stats(trades):
        if not trades:
            return {'n': 0, 'wr': 0.0, 'pnl': 0.0, 'yearly': pd.Series(dtype=float)}
        tdf = pd.DataFrame(trades)
        n   = len(tdf)
        wr  = (tdf['pnl'] > 0).sum() / n
        pnl = tdf['pnl'].sum()
        yr  = tdf.groupby('year')['pnl'].sum()
        return {'n': n, 'wr': wr, 'pnl': pnl, 'yearly': yr}

    sl = stats(trades_l)
    ss = stats(trades_s)

    total_pnl = balance - 10_000
    all_years = sorted(set(list(sl['yearly'].index) + list(ss['yearly'].index)))
    combined_yearly = pd.Series({
        yr: sl['yearly'].get(yr, 0) + ss['yearly'].get(yr, 0)
        for yr in all_years
    })
    all_pos = bool(all(combined_yearly > 0)) if len(combined_yearly) > 0 else False

    return {
        'pnl':        total_pnl,
        'pnl_l':      sl['pnl'],
        'pnl_s':      ss['pnl'],
        'n_l':        sl['n'],
        'n_s':        ss['n'],
        'wr_l':       sl['wr'],
        'wr_s':       ss['wr'],
        'max_dd':     max_dd,
        'max_dly_dd': max_dly_dd,
        'all_pos':    all_pos,
        'yearly':     combined_yearly,
    }


def passes(r):
    return r['max_dd'] < 10.0 and r['max_dly_dd'] < 5.0 and r['all_pos']


def main():
    data_path = (
        Path(__file__).parent.parent
        / "data_cache" / "dukascopy" / "m15" / "XAUUSD"
        / "xauusd_m15_2020-01-01_2026-04-18.parquet"
    )

    n_asian  = len(FB_ASIAN_RANGES)  * len(TP_VALUES) * len(ATR_BUFFERS) * len(N_CONFIRMS)
    n_london = len(FB_LONDON_RANGES) * len(TP_VALUES) * len(ATR_BUFFERS) * len(N_CONFIRMS)
    n_ny     = len(FB_NY_RANGES)     * len(TP_VALUES) * len(ATR_BUFFERS) * len(N_CONFIRMS)

    print("=" * 80)
    print("LONG + Failed Breakout SHORT  Grid Search")
    print(f"LONG (fixed): asian(3-6) london(8-11) ny(15-18)  TP={TP_RR_L}R  Risk=${RISK_L:.0f}")
    print(f"SHORT risk=${SHORT_RISK:.0f}  TP:{TP_VALUES}  ATRbuf:{ATR_BUFFERS}  N_confirm:{N_CONFIRMS}")
    print(f"Combos: asian={n_asian}  london={n_london}  ny={n_ny}  total={n_asian+n_london+n_ny}")
    print("=" * 80)

    df = pd.read_parquet(data_path).sort_index()
    df['atr'] = strat._atr(df, ATR_PERIOD)
    df_h4 = df.resample('4h').agg(
        {'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last'}
    ).dropna()
    df_h4['ema20'] = strat._ema(df_h4, H4_EMA_PERIOD)
    h4_times = df_h4.index.asi8
    h4_ema20 = df_h4['ema20'].to_numpy()

    print(f"Data: {df.index[0].date()} - {df.index[-1].date()}  ({len(df):,} bars M15)\n")

    # Baseline: LONG only (dummy fb session outside valid hours)
    print("[Baseline: LONG only]")
    bl = run_combined(df, h4_times, h4_ema20, '_dummy', (99, 100), 1, 0.5, 1)
    print(f"  N={bl['n_l']}  WR={bl['wr_l']:.1%}  PnL=${bl['pnl_l']:,.0f}  "
          f"MaxDD={bl['max_dd']:.2f}%  DlyDD={bl['max_dly_dd']:.2f}%  "
          f"AllYrs={'YES' if bl['all_pos'] else 'NO'}\n")

    all_results = {}

    for sess_name, range_cands in SESSION_RANGES.items():
        n_combos = len(range_cands) * len(TP_VALUES) * len(ATR_BUFFERS) * len(N_CONFIRMS)
        print("=" * 80)
        print(f"Failed Breakout SHORT: {sess_name}  ({n_combos} combos)")
        print("=" * 80)

        results = []
        t0   = time.time()
        done = 0

        for rng in range_cands:
            for tp_s in TP_VALUES:
                for abuf in ATR_BUFFERS:
                    for nc in N_CONFIRMS:
                        r = run_combined(df, h4_times, h4_ema20,
                                         sess_name, rng, tp_s, abuf, nc)
                        r.update({'rng': rng, 'tp_s': tp_s, 'abuf': abuf, 'nc': nc})
                        results.append(r)
                        done += 1
                        elapsed = time.time() - t0
                        eta = elapsed / done * (n_combos - done) if done < n_combos else 0
                        ok  = '*' if passes(r) else ' '
                        print(f"  {ok} [{done:3d}/{n_combos}] "
                              f"rng={rng[0]}-{rng[1]} TP={tp_s}R buf={abuf} nc={nc}  "
                              f"N_s={r['n_s']:4d} WR_s={r['wr_s']:.0%}  "
                              f"PnL_s=${r['pnl_s']:>7,.0f}  "
                              f"PnL_tot=${r['pnl']:>8,.0f}  "
                              f"DD={r['max_dd']:.1f}% dDD={r['max_dly_dd']:.1f}%  "
                              f"AllY={'Y' if r['all_pos'] else 'N'}  ETA={eta:.0f}s",
                              flush=True)

        all_results[sess_name] = results

        # TOP for this session
        passed = [r for r in results if passes(r)]
        print(f"\n  --- TOP {sess_name}  (DD<10%, dDD<5%, AllYrs)  [{len(passed)}/{n_combos}] ---")
        if passed:
            print(f"  {'#':>3}  {'rng':>6}  {'TP':>4}  {'buf':>5}  {'nc':>3}  "
                  f"{'N_s':>5}  {'WR_s':>6}  {'PnL_s':>8}  {'PnL_tot':>9}  "
                  f"{'MaxDD':>7}  {'DlyDD':>6}")
            print("  " + "-" * 82)
            for k, r in enumerate(sorted(passed, key=lambda x: -x['pnl'])[:15], 1):
                rng = r['rng']
                print(f"  #{k:2d}  {rng[0]}-{rng[1]:>3}  {r['tp_s']:2d}R  "
                      f"{r['abuf']:.1f}  {r['nc']:2d}  "
                      f"{r['n_s']:5d}  {r['wr_s']:.1%}  "
                      f"${r['pnl_s']:>7,.0f}  ${r['pnl']:>8,.0f}  "
                      f"{r['max_dd']:.2f}%  {r['max_dly_dd']:.2f}%")
        else:
            print("  (no combos passed all filters)")
            print("  Best by PnL_total:")
            for k, r in enumerate(sorted(results, key=lambda x: -x['pnl'])[:5], 1):
                rng = r['rng']
                print(f"  #{k:2d}  {rng[0]}-{rng[1]}  TP={r['tp_s']}R  "
                      f"buf={r['abuf']}  nc={r['nc']}  "
                      f"N_s={r['n_s']}  WR_s={r['wr_s']:.0%}  "
                      f"PnL_s=${r['pnl_s']:,.0f}  PnL_tot=${r['pnl']:,.0f}  "
                      f"DD={r['max_dd']:.1f}%  dDD={r['max_dly_dd']:.1f}%")
        print()

    # SUMMARY
    print("=" * 80)
    print("SUMMARY: best Failed Breakout SHORT per session")
    print("=" * 80)
    print(f"  Baseline LONG only: PnL=${bl['pnl_l']:,.0f}  MaxDD={bl['max_dd']:.2f}%\n")

    all_years    = None
    best_per_sess = {}
    for sess_name, results in all_results.items():
        passed = [r for r in results if passes(r)]
        src    = passed if passed else results
        best   = sorted(src, key=lambda x: -x['pnl'])[0]
        best_per_sess[sess_name] = best
        rng  = best['rng']
        mark = '***' if best in (passed or []) and passed else '(no filter)'
        print(f"  {sess_name:<12} rng={rng[0]}-{rng[1]}  TP={best['tp_s']}R  "
              f"buf={best['abuf']}  nc={best['nc']}  "
              f"N_s={best['n_s']}  WR_s={best['wr_s']:.0%}  "
              f"PnL_s=${best['pnl_s']:,.0f}  PnL_tot=${best['pnl']:,.0f}  "
              f"MaxDD={best['max_dd']:.2f}%  DlyDD={best['max_dly_dd']:.2f}%  {mark}")
        if all_years is None:
            all_years = sorted(best['yearly'].index)

    if all_years:
        print(f"\n  PnL by year (best per session vs LONG only):")
        hdr = "  ".join(str(y) for y in all_years)
        print(f"  {'Label':<22}  {hdr}")
        print("  " + "-" * (24 + 10 * len(all_years)))
        print(f"  {'LONG only':<22}  " + "  ".join(
            f"${bl['yearly'].get(y, 0):>6,.0f}" for y in all_years))
        for sn, best in best_per_sess.items():
            yr_vals = "  ".join(f"${best['yearly'].get(y, 0):>6,.0f}" for y in all_years)
            print(f"  {sn:<22}  {yr_vals}")


if __name__ == "__main__":
    main()
