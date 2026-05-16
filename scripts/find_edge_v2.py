# -*- coding: utf-8 -*-
"""
Data-driven edge finder v2 for XAUUSD M15
Fixed: long/short outcomes measured independently.
Shows top combos by WR, no hard cutoff.
"""
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

import pandas as pd
import numpy as np
import warnings
warnings.filterwarnings('ignore')

M15_FILE  = r"D:\Works\ASTRA ANALYZER CHART\data_cache\dukascopy\m15\XAUUSD\xauusd_m15_2020-01-01_2026-05-12.parquet"
RISK_USD  = 100.0
START_BAL = 10000.0
FLOOR     = 9000.0
TARGET_R  = 2.0
STOP_R    = 1.0
MAX_BARS  = 100

# ── Load ──────────────────────────────────────────────────────────────────
print("Loading data...")
df = pd.read_parquet(M15_FILE)
df.index = pd.to_datetime(df.index, utc=True)
df = df.sort_index()
df.columns = [c.lower() for c in df.columns]

# ── Features ──────────────────────────────────────────────────────────────
print("Computing features...")

df['tr']    = np.maximum(df['high']-df['low'],
              np.maximum(abs(df['high']-df['close'].shift(1)),
                         abs(df['low'] -df['close'].shift(1))))
df['atr14'] = df['tr'].ewm(alpha=1/14, adjust=False).mean()

# H4 trend
h4 = df.resample('4h', origin='epoch').agg(
    open=('open','first'), high=('high','max'),
    low=('low','min'), close=('close','last')).dropna()
h4['ema20']   = h4['close'].ewm(span=20, adjust=False).mean()
h4['ema50']   = h4['close'].ewm(span=50, adjust=False).mean()
h4['slope3']  = h4['ema20'] - h4['ema20'].shift(3)
h4['h4_up']   = (h4['close'] > h4['ema20']) & (h4['slope3'] > 0)
h4['h4_dn']   = (h4['close'] < h4['ema20']) & (h4['slope3'] < 0)
h4['abv50']   = h4['close'] > h4['ema50']

# D1 trend
d1 = df.resample('1D', origin='epoch').agg(close=('close','last')).dropna()
d1['d1_ema20']= d1['close'].ewm(span=20, adjust=False).mean()
d1['d1_ema50']= d1['close'].ewm(span=50, adjust=False).mean()
d1['d1_up']   = d1['close'] > d1['d1_ema20']
d1['d1_str']  = (d1['close'] > d1['d1_ema20']) & (d1['close'] > d1['d1_ema50'])

# W1 trend
w1 = df.resample('1W', origin='epoch').agg(close=('close','last')).dropna()
w1['w1_ema10']= w1['close'].ewm(span=10, adjust=False).mean()
w1['w1_up']   = w1['close'] > w1['w1_ema10']

# Map to M15 (shift already in h4/d1/w1 features via .shift(1))
def mmap(s, src):
    return s.shift(1).reindex(df.index, method='ffill')

df['h4_up']  = mmap(h4['h4_up'], h4)
df['h4_dn']  = mmap(h4['h4_dn'], h4)
df['h4_abv'] = mmap(h4['abv50'], h4)
df['d1_up']  = mmap(d1['d1_up'], d1)
df['d1_str'] = mmap(d1['d1_str'], d1)
df['w1_up']  = mmap(w1['w1_up'], w1)

# ATR regime
df['atr_avg']= df['atr14'].rolling(288).mean()
df['atr_hi'] = df['atr14'] > 1.3 * df['atr_avg']
df['atr_lo'] = df['atr14'] < 0.75* df['atr_avg']
df['atr_ok'] = ~df['atr_hi'] & ~df['atr_lo']

df['hour']   = df.index.hour
df['dow']    = df.index.dayofweek

# Bar momentum
df['bull']   = df['close'] > df['open']
df['bear']   = df['close'] < df['open']

# Short-term momentum
df['up3h']   = df['close'] > df['close'].shift(12)   # vs 3h ago
df['up6h']   = df['close'] > df['close'].shift(24)   # vs 6h ago
df['dn3h']   = df['close'] < df['close'].shift(12)
df['dn6h']   = df['close'] < df['close'].shift(24)

print("  Features ready.")

# ── Forward outcome - CORRECT independent loops ────────────────────────────
print("Measuring forward outcomes...")

close_a = df['close'].values
high_a  = df['high'].values
low_a   = df['low'].values
atr_a   = df['atr14'].values
n_bars  = len(df)

long_win  = np.full(n_bars, np.nan)
short_win = np.full(n_bars, np.nan)

for i in range(300, n_bars - MAX_BARS - 1):
    atr = atr_a[i]
    if atr <= 0 or np.isnan(atr):
        continue
    entry = close_a[i]

    # LONG outcome (independent)
    tp_l = entry + TARGET_R * atr
    sl_l = entry - STOP_R   * atr
    lw = np.nan
    for j in range(i+1, i+MAX_BARS+1):
        h = high_a[j]; lo = low_a[j]
        if lo <= sl_l:  lw = 0.0; break
        if h  >= tp_l:  lw = 1.0; break
    long_win[i] = lw

    # SHORT outcome (independent)
    tp_s = entry - TARGET_R * atr
    sl_s = entry + STOP_R   * atr
    sw = np.nan
    for j in range(i+1, i+MAX_BARS+1):
        h = high_a[j]; lo = low_a[j]
        if h  >= sl_s:  sw = 0.0; break
        if lo <= tp_s:  sw = 1.0; break
    short_win[i] = sw

df['long_win']  = long_win
df['short_win'] = short_win

# baseline
bl = df['long_win'].dropna()
bs = df['short_win'].dropna()
print(f"  Baseline long  WR: {bl.mean():.1%}  (N={len(bl):,})")
print(f"  Baseline short WR: {bs.mean():.1%}  (N={len(bs):,})")

# ── Filter library ─────────────────────────────────────────────────────────
F = {
    # Trend
    'H4up':  df['h4_up']==True,
    'H4dn':  df['h4_dn']==True,
    'D1up':  df['d1_up']==True,
    'D1dn':  df['d1_up']==False,
    'D1str': df['d1_str']==True,
    'W1up':  df['w1_up']==True,
    'W1dn':  df['w1_up']==False,
    'A50':   df['h4_abv']==True,
    'B50':   df['h4_abv']==False,
    # Volatility
    'Vok':   df['atr_ok']==True,
    'Vhi':   df['atr_hi']==True,
    # Time - sessions
    'As':    df['hour'].between(3,5),
    'Ldn':   df['hour'].between(8,10),
    'NY':    df['hour'].between(15,17),
    'PreL':  df['hour'].between(5,7),
    'Lun':   df['hour'].between(12,14),
    # Specific hours
    'h05':   df['hour']==5,
    'h08':   df['hour']==8,
    'h09':   df['hour']==9,
    'h13':   df['hour']==13,
    'h15':   df['hour']==15,
    'h16':   df['hour']==16,
    # Day of week
    'MTh':   df['dow'] < 4,
    'MTu':   df['dow'] < 2,
    'noFri': df['dow'] != 4,
    # Bar
    'Bull':  df['bull']==True,
    'Bear':  df['bear']==True,
    # Momentum
    'U3h':   df['up3h']==True,
    'D3h':   df['dn3h']==True,
    'U6h':   df['up6h']==True,
    'D6h':   df['dn6h']==True,
}

# ── Evaluator ─────────────────────────────────────────────────────────────
def eval_combo(mask, col, min_n=50):
    sub = df[mask & df[col].notna()]
    n   = len(sub)
    if n < min_n:
        return None
    wr  = sub[col].mean()
    pnl_s = sub[col].map({1.0: TARGET_R*RISK_USD, 0.0: -STOP_R*RISK_USD})
    pnl = pnl_s.sum()
    bal  = START_BAL + pnl_s.cumsum()
    peak = bal.cummax()
    dd   = ((peak-bal)/peak*100).max()
    y_pnl= sub.copy()
    y_pnl['yr'] = y_pnl.index.year
    yg = y_pnl.groupby('yr').apply(
        lambda x: x[col].map({1.0:TARGET_R*RISK_USD,0.0:-STOP_R*RISK_USD}).sum())
    all_pos = (yg > 0).all()
    n_pos   = (yg > 0).sum()
    return {
        'n': n, 'wr': wr, 'pnl': pnl, 'dd': dd,
        'all_pos': all_pos, 'n_pos': n_pos, 'n_yrs': len(yg),
        'tpm': n / 77,
    }

# ── Grid search ────────────────────────────────────────────────────────────
print("Grid searching...")

long_results  = []
short_results = []

# LONG: trend + time + optional (vol, bar, momentum)
L_trend  = ['H4up','D1up','D1str','W1up','A50']
L_time   = ['As','Ldn','NY','PreL','Lun','h05','h08','h09','h13','h15','h16','MTh','noFri']
L_extra  = ['Vok','Vhi','Bull','U3h','U6h']

S_trend  = ['H4dn','D1dn','W1dn','B50']
S_time   = ['As','Ldn','NY','PreL','Lun','h05','h08','h09','h13','h15','h16','MTh','noFri']
S_extra  = ['Vok','Vhi','Bear','D3h','D6h']

L_dual   = [('H4up','D1up'),('H4up','W1up'),('D1str','W1up'),
            ('H4up','D1str'),('A50','D1up'),('A50','W1up'),('D1up','W1up')]
S_dual   = [('H4dn','D1dn'),('H4dn','W1dn'),('D1dn','W1dn'),
            ('B50','D1dn'),('B50','W1dn'),('H4dn','B50')]

def add_result(results, label, mask, col):
    r = eval_combo(mask, col)
    if r:
        results.append({'label': label, **r})

print("  LONG single-trend combos...")
for tf in L_trend:
    base = F[tf]
    add_result(long_results, tf, base, 'long_win')
    for tm in L_time:
        m = base & F[tm]
        add_result(long_results, f'{tf}+{tm}', m, 'long_win')
        for ex in L_extra:
            add_result(long_results, f'{tf}+{tm}+{ex}', m & F[ex], 'long_win')
    for ex in L_extra:
        add_result(long_results, f'{tf}+{ex}', base & F[ex], 'long_win')

print("  LONG dual-trend combos...")
for tf1, tf2 in L_dual:
    base = F[tf1] & F[tf2]
    add_result(long_results, f'{tf1}+{tf2}', base, 'long_win')
    for tm in L_time:
        m = base & F[tm]
        add_result(long_results, f'{tf1}+{tf2}+{tm}', m, 'long_win')
        for ex in L_extra:
            add_result(long_results, f'{tf1}+{tf2}+{tm}+{ex}', m & F[ex], 'long_win')

print("  SHORT single-trend combos...")
for tf in S_trend:
    base = F[tf]
    add_result(short_results, tf, base, 'short_win')
    for tm in S_time:
        m = base & F[tm]
        add_result(short_results, f'{tf}+{tm}', m, 'short_win')
        for ex in S_extra:
            add_result(short_results, f'{tf}+{tm}+{ex}', m & F[ex], 'short_win')
    for ex in S_extra:
        add_result(short_results, f'{tf}+{ex}', base & F[ex], 'short_win')

print("  SHORT dual-trend combos...")
for tf1, tf2 in S_dual:
    base = F[tf1] & F[tf2]
    add_result(short_results, f'{tf1}+{tf2}', base, 'short_win')
    for tm in S_time:
        m = base & F[tm]
        add_result(short_results, f'{tf1}+{tf2}+{tm}', m, 'short_win')
        for ex in S_extra:
            add_result(short_results, f'{tf1}+{tf2}+{tm}+{ex}', m & F[ex], 'short_win')

print(f"  Long combos: {len(long_results)},  Short combos: {len(short_results)}")

# ── Report ─────────────────────────────────────────────────────────────────
def show_top(results, title, top_n=20):
    print(f"\n{'='*70}")
    print(f"  {title}")
    print(f"{'='*70}")
    if not results:
        print("  No results.")
        return
    df_r = pd.DataFrame(results).drop_duplicates('label')
    df_r = df_r.sort_values('wr', ascending=False)
    print(f"  {'Filter':<36} {'N':>5} {'T/mo':>5} {'WR':>6} "
          f"{'PnL':>9} {'DD':>7} {'PosYrs':>7}")
    print(f"  {'-'*73}")
    for _, r in df_r.head(top_n).iterrows():
        py = f"{r['n_pos']:.0f}/{r['n_yrs']:.0f}"
        print(f"  {r['label']:<36} {r['n']:>5} {r['tpm']:>5.1f} "
              f"{r['wr']:>6.1%} ${r['pnl']:>8,.0f} {r['dd']:>6.1f}% {py:>7}")

show_top(long_results,  "TOP LONG  (sorted by WR, N>=50)")
show_top(short_results, "TOP SHORT (sorted by WR, N>=50)")

# ── Also: top by PnL ──────────────────────────────────────────────────────
def show_top_pnl(results, title, top_n=15):
    print(f"\n{'='*70}")
    print(f"  {title}")
    print(f"{'='*70}")
    if not results:
        return
    df_r = pd.DataFrame(results).drop_duplicates('label')
    df_r = df_r[df_r['dd'] < 25].sort_values('pnl', ascending=False)
    print(f"  {'Filter':<36} {'N':>5} {'T/mo':>5} {'WR':>6} "
          f"{'PnL':>9} {'DD':>7} {'PosYrs':>7}")
    print(f"  {'-'*73}")
    for _, r in df_r.head(top_n).iterrows():
        py = f"{r['n_pos']:.0f}/{r['n_yrs']:.0f}"
        print(f"  {r['label']:<36} {r['n']:>5} {r['tpm']:>5.1f} "
              f"{r['wr']:>6.1%} ${r['pnl']:>8,.0f} {r['dd']:>6.1f}% {py:>7}")

show_top_pnl(long_results,  "TOP LONG  by PnL (DD<25%)")
show_top_pnl(short_results, "TOP SHORT by PnL (DD<25%)")

# ── Hour-by-hour breakdown of baseline ────────────────────────────────────
print(f"\n{'='*70}")
print(f"  HOUR-BY-HOUR BASELINE (all bars, min N=200)")
print(f"{'='*70}")
print(f"  {'Hour':>4} {'N_L':>6} {'LongWR':>7} {'N_S':>6} {'ShortWR':>8}")
print(f"  {'-'*36}")
for h in range(24):
    hm = df['hour'] == h
    ls = df[hm & df['long_win'].notna()]['long_win']
    ss = df[hm & df['short_win'].notna()]['short_win']
    if len(ls) >= 100 and len(ss) >= 100:
        print(f"  {h:>4}  {len(ls):>6}  {ls.mean():>6.1%}  {len(ss):>6}  {ss.mean():>7.1%}")

print("\nDone.")
