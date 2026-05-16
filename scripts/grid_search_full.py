# -*- coding: utf-8 -*-
"""
Comprehensive grid search: ALL parameters
- TP RR: 2.0, 2.5, 3.0, 3.5, 4.0
- SL: 0.8, 1.0, 1.2, 1.5 x ATR
- Trailing: 5 variants
- Entry hours: 12 LONG sets, 12 SHORT sets
- Trend filters: 7 LONG, 7 SHORT
- Bar filters: 4 LONG, 4 SHORT
- Pullback buffer
Total: 100 param combos x many condition combos
"""
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

import pandas as pd
import numpy as np
import warnings
warnings.filterwarnings('ignore')
from itertools import product

M15_FILE  = r"D:\Works\ASTRA ANALYZER CHART\data_cache\dukascopy\m15\XAUUSD\xauusd_m15_2020-01-01_2026-05-12.parquet"
RISK_USD  = 100.0
START_BAL = 10000.0
FLOOR     = 9000.0
MAX_BARS  = 400

TP_VALS    = [2.0, 2.5, 3.0, 3.5, 4.0]
SL_VALS    = [0.8, 1.0, 1.2, 1.5]
TRAIL_VALS = [
    (0.0, 0.0),   # no trail
    (0.8, 0.3),   # early tight trail
    (1.0, 0.4),   # medium
    (1.2, 0.4),   # standard
    (1.5, 0.5),   # late wide
]

# ── Load data ──────────────────────────────────────────────────────────────
print("Loading M15 data...")
df = pd.read_parquet(M15_FILE)
df.index = pd.to_datetime(df.index, utc=True)
df = df.sort_index()
df.columns = [c.lower() for c in df.columns]
N = len(df)
print(f"  {N} bars loaded")

# ── Compute ATR ────────────────────────────────────────────────────────────
df['tr']  = np.maximum(df['high']-df['low'],
            np.maximum(abs(df['high']-df['close'].shift(1)),
                       abs(df['low'] -df['close'].shift(1))))
df['atr'] = df['tr'].ewm(alpha=1/14, adjust=False).mean()

# ── Compute H4 features ───────────────────────────────────────────────────
h4 = df.resample('4h', origin='epoch').agg(
    open=('open','first'), high=('high','max'),
    low=('low','min'), close=('close','last')).dropna()
h4['ema20']  = h4['close'].ewm(span=20, adjust=False).mean()
h4['ema50']  = h4['close'].ewm(span=50, adjust=False).mean()
h4['slope3'] = h4['ema20'] - h4['ema20'].shift(3)
h4['h4_up']  = (h4['close'] > h4['ema20']) & (h4['slope3'] > 0)
h4['h4_dn']  = (h4['close'] < h4['ema20']) & (h4['slope3'] < 0)
h4['abv50']  = h4['close'] > h4['ema50']

# ── Compute D1 features ───────────────────────────────────────────────────
d1 = df.resample('1D', origin='epoch').agg(close=('close','last')).dropna()
d1['d1_ema20'] = d1['close'].ewm(span=20, adjust=False).mean()
d1['d1_ema50'] = d1['close'].ewm(span=50, adjust=False).mean()
d1['d1_up']    = d1['close'] > d1['d1_ema20']
d1['d1_str']   = (d1['close'] > d1['d1_ema20']) & (d1['close'] > d1['d1_ema50'])

# ── Compute W1 features ───────────────────────────────────────────────────
w1 = df.resample('1W', origin='epoch').agg(close=('close','last')).dropna()
w1['w1_ema10'] = w1['close'].ewm(span=10, adjust=False).mean()
w1['w1_up']    = w1['close'] > w1['w1_ema10']
w1['w1_dn']    = w1['close'] < w1['w1_ema10']

# ── Map higher timeframe to M15 (shift(1) = no lookahead) ─────────────────
def mmap(s):
    return s.shift(1).reindex(df.index, method='ffill')

df['h4_up']  = mmap(h4['h4_up']).astype(bool)
df['h4_dn']  = mmap(h4['h4_dn']).astype(bool)
df['h4_abv'] = mmap(h4['abv50']).astype(bool)
df['d1_up']  = mmap(d1['d1_up']).astype(bool)
df['d1_str'] = mmap(d1['d1_str']).astype(bool)
df['w1_up']  = mmap(w1['w1_up']).astype(bool)
df['w1_dn']  = mmap(w1['w1_dn']).astype(bool)

# H4 EMA20 mapped to M15 for pullback buffer
h4_ema20_m15 = mmap(h4['ema20'])

# ── Bar features ──────────────────────────────────────────────────────────
df['hour']  = df.index.hour
df['dow']   = df.index.dayofweek
df['date']  = df.index.date
df['bear']  = (df['close'] < df['open']).astype(bool)
df['bull']  = (df['close'] > df['open']).astype(bool)
df['pb_dist'] = (abs(df['close'] - h4_ema20_m15) / df['atr'].clip(lower=1e-6)).fillna(999)
df['pb_05'] = (df['pb_dist'] < 0.5).astype(bool)
df['pb_10'] = (df['pb_dist'] < 1.0).astype(bool)

# Valid bars: not Friday, ATR > 0, warmup done
df['valid'] = (df['dow'] != 4) & (df['atr'] > 0) & df['atr'].notna()
WARMUP = 300

print(f"  Features computed. Valid bars: {df['valid'].sum()}")

# ── Pre-compute outcomes per bar for each (tp, sl_mult, trail) ────────────
print(f"\nPre-computing outcomes for {len(TP_VALS)*len(SL_VALS)*len(TRAIL_VALS)} param combos...")

hi  = df['high'].values.astype(np.float64)
lo  = df['low'].values.astype(np.float64)
cl  = df['close'].values.astype(np.float64)
atr = df['atr'].values.astype(np.float64)
valid_arr = df['valid'].values

def compute_outcomes(tp_r, sl_mult, trail_start, trail_step):
    long_pnl  = np.full(N, np.nan)
    short_pnl = np.full(N, np.nan)

    for i in range(WARMUP, N - MAX_BARS - 1):
        if not valid_arr[i]:
            continue
        sl_dist = atr[i] * sl_mult
        if sl_dist <= 0:
            continue
        entry = cl[i]

        # LONG
        tp_price = entry + tp_r * sl_dist
        sl_price = entry - sl_dist
        cur_sl   = sl_price
        best_r   = 0.0
        result_l = None
        for j in range(i+1, min(i+MAX_BARS+1, N)):
            if lo[j] <= cur_sl:
                result_l = (cur_sl - entry) / sl_dist * RISK_USD
                break
            if hi[j] >= tp_price:
                result_l = tp_r * RISK_USD
                break
            r = (hi[j] - entry) / sl_dist
            if r > best_r:
                best_r = r
            if trail_start > 0 and best_r >= trail_start:
                ns = entry + (best_r - trail_step) * sl_dist
                if ns > cur_sl:
                    cur_sl = ns
        if result_l is None:
            r_f = (cur_sl - entry) / sl_dist
            result_l = r_f * RISK_USD
        long_pnl[i] = result_l

        # SHORT
        tp_price = entry - tp_r * sl_dist
        sl_price = entry + sl_dist
        cur_sl   = sl_price
        best_r   = 0.0
        result_s = None
        for j in range(i+1, min(i+MAX_BARS+1, N)):
            if hi[j] >= cur_sl:
                result_s = (entry - cur_sl) / sl_dist * RISK_USD
                break
            if lo[j] <= tp_price:
                result_s = tp_r * RISK_USD
                break
            r = (entry - lo[j]) / sl_dist
            if r > best_r:
                best_r = r
            if trail_start > 0 and best_r >= trail_start:
                ns = entry - (best_r - trail_step) * sl_dist
                if ns < cur_sl:
                    cur_sl = ns
        if result_s is None:
            r_f = (entry - cur_sl) / sl_dist
            result_s = r_f * RISK_USD
        short_pnl[i] = result_s

    return long_pnl, short_pnl

outcomes = {}
total_combos = len(TP_VALS) * len(SL_VALS) * len(TRAIL_VALS)
done = 0
for tp_r, sl_mult, (ts, tstep) in product(TP_VALS, SL_VALS, TRAIL_VALS):
    key = (tp_r, sl_mult, ts, tstep)
    outcomes[key] = compute_outcomes(tp_r, sl_mult, ts, tstep)
    done += 1
    if done % 10 == 0:
        print(f"  {done}/{total_combos} param combos done...")

print(f"  All {total_combos} param combos computed.")

# ── Define condition masks ────────────────────────────────────────────────
h4u  = df['h4_up'].values
h4d  = df['h4_dn'].values
h4a  = df['h4_abv'].values
d1u  = df['d1_up'].values
d1s  = df['d1_str'].values
w1u  = df['w1_up'].values
w1d  = df['w1_dn'].values
bear = df['bear'].values
bull = df['bull'].values
pb05 = df['pb_05'].values
pb10 = df['pb_10'].values
hr   = df['hour'].values
val  = df['valid'].values

LONG_TRENDS = {
    'H4up':           h4u,
    'H4up+D1up':      h4u & d1u,
    'H4up+D1str':     h4u & d1s,
    'H4up+W1up':      h4u & w1u,
    'H4up+D1up+W1up': h4u & d1u & w1u,
    'H4abv+D1up':     h4a & d1u,
    'H4abv+W1up':     h4a & w1u,
}

SHORT_TRENDS = {
    'H4dn':           h4d,
    'H4dn+D1dn':      h4d & ~d1u,
    'H4dn+D1str_dn':  h4d & ~d1s,
    'H4dn+W1dn':      h4d & w1d,
    'H4dn+D1dn+W1dn': h4d & ~d1u & w1d,
    'H4blw+D1dn':     ~h4a & ~d1u,
    'H4blw+W1dn':     ~h4a & w1d,
}

def hmask(hours):
    m = np.zeros(N, dtype=bool)
    for h in hours:
        m |= (hr == h)
    return m

LONG_HOURS = {
    'h05':              hmask([5]),
    'h08':              hmask([8]),
    'h05+h08':          hmask([5,8]),
    'h00-02':           hmask([0,1,2]),
    'h00-02+h05':       hmask([0,1,2,5]),
    'h05-06':           hmask([5,6]),
    'h05-09':           hmask([5,6,7,8,9]),
    'h00-02+h05-09':    hmask([0,1,2,5,6,7,8,9]),
    'h13-16':           hmask([13,14,15,16]),
    'h05-09+h13-16':    hmask([5,6,7,8,9,13,14,15,16]),
    'h00-09':           hmask([0,1,2,3,4,5,6,7,8,9]),
    'h00-09+h13-16':    hmask([0,1,2,3,4,5,6,7,8,9,13,14,15,16]),
}

SHORT_HOURS = {
    'h15':              hmask([15]),
    'h16':              hmask([16]),
    'h15+h16':          hmask([15,16]),
    'h13-16':           hmask([13,14,15,16]),
    'h13-17':           hmask([13,14,15,16,17]),
    'h08-09':           hmask([8,9]),
    'h08-09+h13-16':    hmask([8,9,13,14,15,16]),
    'h08-09+h13-17':    hmask([8,9,13,14,15,16,17]),
    'h20-22':           hmask([20,21,22]),
    'h13-16+h20-22':    hmask([13,14,15,16,20,21,22]),
    'h00-02+h13-17':    hmask([0,1,2,13,14,15,16,17]),
    'h05-09+h13-17':    hmask([5,6,7,8,9,13,14,15,16,17]),
}

LONG_BARS = {
    'any':  np.ones(N, dtype=bool),
    'bull': bull,
    'pb05': pb05,
    'pb10': pb10,
}

SHORT_BARS = {
    'any':  np.ones(N, dtype=bool),
    'bear': bear,
    'pb05': pb05,
    'pb10': pb10,
}

print(f"\nCondition grid:")
print(f"  LONG: {len(LONG_TRENDS)} x {len(LONG_HOURS)} x {len(LONG_BARS)} = {len(LONG_TRENDS)*len(LONG_HOURS)*len(LONG_BARS)} combos")
print(f"  SHORT: {len(SHORT_TRENDS)} x {len(SHORT_HOURS)} x {len(SHORT_BARS)} = {len(SHORT_TRENDS)*len(SHORT_HOURS)*len(SHORT_BARS)} combos")

# ── Evaluation ───────────────────────────────────────────────────────────
dates = df['date'].values

def eval_condition(cond_mask, pnl_arr):
    active = cond_mask & val & ~np.isnan(pnl_arr)
    if active.sum() == 0:
        return None

    trades_pnl = []
    day_count = {}
    for i in np.where(active)[0]:
        d = dates[i]
        if day_count.get(d, 0) < 1:
            trades_pnl.append(pnl_arr[i])
            day_count[d] = 1

    if len(trades_pnl) < 10:
        return None

    pnl_a = np.array(trades_pnl)
    n    = len(pnl_a)
    wr   = (pnl_a > 0).mean()
    pnl  = pnl_a.sum()
    bal  = START_BAL + np.cumsum(pnl_a)
    dd   = ((np.maximum.accumulate(bal) - bal) / np.maximum.accumulate(bal) * 100).max()
    mb   = bal.min()
    n_mo = n / 77.0

    if wr < 0.35 or pnl < 500 or dd > 15 or n_mo < 2.0:
        return None

    return {
        'n': n, 'wr': wr, 'pnl': pnl, 'dd': dd, 'mb': mb,
        'n_mo': n_mo, 'floor': mb >= FLOOR,
        'score': pnl / (dd + 1),
    }

# ── Grid search ───────────────────────────────────────────────────────────
print("\nRunning grid search...")
best_long  = []
best_short = []
param_keys = list(outcomes.keys())

print("  Searching LONG...")
for li, (trend_name, trend_mask) in enumerate(LONG_TRENDS.items()):
    for hours_name, hours_mask in LONG_HOURS.items():
        for bar_name, bar_mask in LONG_BARS.items():
            cond = trend_mask & hours_mask & bar_mask
            for key in param_keys:
                tp_r, sl_mult, ts, tstep = key
                long_pnl, _ = outcomes[key]
                res = eval_condition(cond, long_pnl)
                if res is not None:
                    trail_label = f"tr{ts:.1f}s{tstep:.1f}" if ts > 0 else "notrail"
                    best_long.append({**res,
                        'trend': trend_name, 'hours': hours_name, 'bar': bar_name,
                        'tp': tp_r, 'sl': sl_mult, 'trail': trail_label})
    print(f"    {li+1}/{len(LONG_TRENDS)}: {trend_name} | {len(best_long)} candidates")

print("  Searching SHORT...")
for si, (trend_name, trend_mask) in enumerate(SHORT_TRENDS.items()):
    for hours_name, hours_mask in SHORT_HOURS.items():
        for bar_name, bar_mask in SHORT_BARS.items():
            cond = trend_mask & hours_mask & bar_mask
            for key in param_keys:
                tp_r, sl_mult, ts, tstep = key
                _, short_pnl = outcomes[key]
                res = eval_condition(cond, short_pnl)
                if res is not None:
                    trail_label = f"tr{ts:.1f}s{tstep:.1f}" if ts > 0 else "notrail"
                    best_short.append({**res,
                        'trend': trend_name, 'hours': hours_name, 'bar': bar_name,
                        'tp': tp_r, 'sl': sl_mult, 'trail': trail_label})
    print(f"    {si+1}/{len(SHORT_TRENDS)}: {trend_name} | {len(best_short)} candidates")

print(f"\n  LONG candidates: {len(best_long)}")
print(f"  SHORT candidates: {len(best_short)}")

# ── Sort and report ───────────────────────────────────────────────────────
df_long  = pd.DataFrame(best_long).sort_values('score', ascending=False).drop_duplicates(
    subset=['trend','hours','bar','tp','sl','trail']).head(30)
df_short = pd.DataFrame(best_short).sort_values('score', ascending=False).drop_duplicates(
    subset=['trend','hours','bar','tp','sl','trail']).head(30)

def print_top(df_res, title, n=25):
    print(f"\n{'='*100}")
    print(f"  TOP {n} {title}")
    print(f"{'='*100}")
    hdr = f"  {'#':<3} {'Trend':<22} {'Hours':<20} {'Bar':<6} {'TP':>4} {'SL':>4} {'Trail':<14} {'N':>5} {'N/mo':>5} {'WR':>6} {'PnL':>9} {'DD':>6} {'Floor':<6} {'Score':>7}"
    print(hdr)
    print(f"  {'-'*108}")
    for rank, (_, row) in enumerate(df_res.head(n).iterrows(), 1):
        floor = 'SAFE' if row['floor'] else 'BREACH'
        print(f"  {rank:<3} {row['trend']:<22} {row['hours']:<20} {row['bar']:<6} "
              f"{row['tp']:>4.1f} {row['sl']:>4.1f} {row['trail']:<14} "
              f"{row['n']:>5.0f} {row['n_mo']:>5.1f} {row['wr']:>6.1%} "
              f"${row['pnl']:>8,.0f} {row['dd']:>5.1f}% {floor:<6} {row['score']:>7.0f}")

print_top(df_long,  "LONG strategies")
print_top(df_short, "SHORT strategies")

# ── Parameter dominance ───────────────────────────────────────────────────
print(f"\n{'='*100}")
print(f"  PARAMETER DOMINANCE (top 30 each direction)")
print(f"{'='*100}")
for label, df_res in [("LONG", df_long), ("SHORT", df_short)]:
    print(f"\n  {label}:")
    for col in ['tp', 'sl', 'trail']:
        counts = df_res[col].value_counts().to_dict()
        print(f"    {col}: {counts}")

# ── Combined pairs ────────────────────────────────────────────────────────
print(f"\n{'='*100}")
print(f"  TOP COMBINED (Long + Short, approximate)")
print(f"{'='*100}")

TOP_L = df_long.head(15).reset_index(drop=True)
TOP_S = df_short.head(15).reset_index(drop=True)

combos = []
for _, lr in TOP_L.iterrows():
    for _, sr in TOP_S.iterrows():
        comb_pnl = lr['pnl'] + sr['pnl']
        comb_n   = lr['n'] + sr['n']
        comb_wr  = (lr['wr']*lr['n'] + sr['wr']*sr['n']) / comb_n
        est_dd   = max(lr['dd'], sr['dd'])
        combos.append({
            'l_setup': f"{lr['trend']} {lr['hours']} tp{lr['tp']:.1f} sl{lr['sl']:.1f} {lr['trail']}",
            's_setup': f"{sr['trend']} {sr['hours']} tp{sr['tp']:.1f} sl{sr['sl']:.1f} {sr['trail']}",
            'total_pnl': comb_pnl, 'total_n': comb_n, 'avg_wr': comb_wr,
            'est_dd': est_dd, 'score': comb_pnl / (est_dd + 1),
        })

df_combos = pd.DataFrame(combos).sort_values('score', ascending=False).head(20)
print(f"\n  {'#':<4} {'Total PnL':>10} {'DD':>6} {'N':>5} {'WR':>6}")
print(f"  {'-'*110}")
for rank, (_, row) in enumerate(df_combos.iterrows(), 1):
    print(f"  {rank:<4} ${row['total_pnl']:>9,.0f}  {row['est_dd']:>5.1f}% {row['total_n']:>5.0f} {row['avg_wr']:>6.1%}")
    print(f"       LONG:  {row['l_setup']}")
    print(f"       SHORT: {row['s_setup']}")

# ── Year-by-year for best LONG and SHORT ──────────────────────────────────
year_arr = np.array([df.index[i].year for i in range(N)])

def year_breakdown(df_res, direction_trends, direction_hours, direction_bars, outcomes_key, is_long):
    if len(df_res) == 0:
        return
    best = df_res.iloc[0]
    print(f"\n  Setup: {best['trend']} | {best['hours']} | {best['bar']} | TP={best['tp']} SL={best['sl']} {best['trail']}")

    trend_mask = direction_trends[best['trend']]
    hours_mask = direction_hours[best['hours']]
    bar_mask   = direction_bars[best['bar']]
    cond = trend_mask & hours_mask & bar_mask

    for key in outcomes_key:
        tp_r, sl_mult, ts, tstep = key
        tl = f"tr{ts:.1f}s{tstep:.1f}" if ts > 0 else "notrail"
        if abs(tp_r - best['tp']) < 0.01 and abs(sl_mult - best['sl']) < 0.01 and tl == best['trail']:
            long_pnl, short_pnl = outcomes[key]
            pnl_arr = long_pnl if is_long else short_pnl
            break

    active = cond & val & ~np.isnan(pnl_arr)
    pby, nby, wrby = {}, {}, {}
    day_count = {}
    for i in np.where(active)[0]:
        d = dates[i]
        if day_count.get(d, 0) < 1:
            day_count[d] = 1
            yr = year_arr[i]
            p  = pnl_arr[i]
            pby[yr] = pby.get(yr, 0) + p
            nby[yr] = nby.get(yr, 0) + 1
            wrby[yr] = wrby.get(yr, [])
            wrby[yr].append(p > 0)

    all_pos = True
    for yr in sorted(pby.keys()):
        p  = pby[yr]
        n  = nby[yr]
        wr = np.mean(wrby[yr])
        s  = '+' if p >= 0 else '-'
        if p < 0: all_pos = False
        bar = '#' * max(0, int(abs(p)/300))
        print(f"    {yr}: N={n:3d}  WR={wr:.1%}  {s}${abs(p):,.0f}  {bar}")
    print(f"  All years positive? {'YES' if all_pos else 'NO'}")

print(f"\n{'='*100}")
print(f"  YEAR-BY-YEAR: BEST LONG")
print(f"{'='*100}")
year_breakdown(df_long, LONG_TRENDS, LONG_HOURS, LONG_BARS, param_keys, True)

print(f"\n{'='*100}")
print(f"  YEAR-BY-YEAR: BEST SHORT")
print(f"{'='*100}")
year_breakdown(df_short, SHORT_TRENDS, SHORT_HOURS, SHORT_BARS, param_keys, False)

print("\n\nDone.")
