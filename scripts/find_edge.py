# -*- coding: utf-8 -*-
"""
Data-driven edge finder for XAUUSD M15
For every potential entry, measure forward outcome.
Find feature combinations with WR>55% and MaxDD<10%.
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

# ── Load ──────────────────────────────────────────────────────────────────
print("Loading...")
df = pd.read_parquet(M15_FILE)
df.index = pd.to_datetime(df.index, utc=True)
df = df.sort_index()
df.columns = [c.lower() for c in df.columns]

# ── Features ──────────────────────────────────────────────────────────────
print("Computing features...")

# ATR
df['tr']  = np.maximum(df['high']-df['low'],
            np.maximum(abs(df['high']-df['close'].shift(1)),
                       abs(df['low'] -df['close'].shift(1))))
df['atr14'] = df['tr'].ewm(alpha=1/14, adjust=False).mean()

# H4 EMA trend
h4 = df.resample('4h', origin='epoch').agg(
    open=('open','first'), high=('high','max'),
    low=('low','min'), close=('close','last')).dropna()
h4['ema20']    = h4['close'].ewm(span=20, adjust=False).mean()
h4['ema50']    = h4['close'].ewm(span=50, adjust=False).mean()
h4['slope5']   = h4['ema20'] - h4['ema20'].shift(5)
h4['h4_up']    = ((h4['close'] > h4['ema20']) & (h4['slope5'] > 0)).shift(1)
h4['h4_down']  = ((h4['close'] < h4['ema20']) & (h4['slope5'] < 0)).shift(1)
h4['h4_neut']  = ~(h4['h4_up'] | h4['h4_down'])
h4['above_50'] = (h4['close'] > h4['ema50']).shift(1)

# D1 trend
d1 = df.resample('1D', origin='epoch').agg(close=('close','last')).dropna()
d1['d1_ema20'] = d1['close'].ewm(span=20, adjust=False).mean()
d1['d1_ema50'] = d1['close'].ewm(span=50, adjust=False).mean()
d1['d1_up']    = (d1['close'] > d1['d1_ema20']).shift(1)
d1['d1_strong']= ((d1['close'] > d1['d1_ema20']) & (d1['close'] > d1['d1_ema50'])).shift(1)

# Weekly trend
w1 = df.resample('1W', origin='epoch').agg(close=('close','last')).dropna()
w1['w1_ema10'] = w1['close'].ewm(span=10, adjust=False).mean()
w1['w1_up']    = (w1['close'] > w1['w1_ema10']).shift(1)

# Map to M15
def mh4(s): return s.reindex(df.index, method='ffill')
def md1(s): return s.reindex(df.index, method='ffill')
def mw1(s): return s.reindex(df.index, method='ffill')

df['h4_up']    = mh4(h4['h4_up'])
df['h4_down']  = mh4(h4['h4_down'])
df['above_50'] = mh4(h4['above_50'])
df['d1_up']    = md1(d1['d1_up'])
df['d1_strong']= md1(d1['d1_strong'])
df['w1_up']    = mw1(w1['w1_up'])

# ATR regime (current vs 3-day avg)
df['atr_avg']  = df['atr14'].rolling(288).mean()  # 3 days
df['atr_hi']   = df['atr14'] > 1.4 * df['atr_avg']  # high vol
df['atr_lo']   = df['atr14'] < 0.7 * df['atr_avg']  # low vol
df['atr_norm'] = ~df['atr_hi'] & ~df['atr_lo']

# Session
df['hour']     = df.index.hour
df['dow']      = df.index.dayofweek
df['sess_asian']  = df['hour'].between(3,5)
df['sess_london'] = df['hour'].between(8,10)
df['sess_ny']     = df['hour'].between(15,17)
df['sess_early']  = df['hour'].between(5,7)   # pre-London
df['sess_lunch']  = df['hour'].between(12,14)  # London/NY overlap

# M15 momentum: bullish/bearish bar
df['bull_bar'] = (df['close'] > df['open']) & \
                 ((df['close']-df['open']) > 0.4*(df['high']-df['low']))
df['bear_bar'] = (df['close'] < df['open']) & \
                 ((df['open']-df['close']) > 0.4*(df['high']-df['low']))

# Previous session result (simplified): was last session close > open?
df['prev3_up']  = df['close'].shift(1) > df['close'].shift(12)   # last 3h
df['prev6_up']  = df['close'].shift(1) > df['close'].shift(24)   # last 6h

print("  Features ready.")

# ── Forward outcome measurement ───────────────────────────────────────────
print("Measuring forward outcomes (this takes ~3 min)...")

# For each bar, measure:
# LONG: does price hit +2R before -1R in next 100 bars?
# SHORT: does price hit -2R before +1R in next 100 bars?
# R = atr14 at that bar

TARGET_R  = 2.0   # profit target in R
STOP_R    = 1.0   # stop loss in R
MAX_BARS  = 100   # look forward up to 100 bars (25 hours)

long_win  = np.zeros(len(df), dtype=bool)
short_win = np.zeros(len(df), dtype=bool)

close_arr = df['close'].values
high_arr  = df['high'].values
low_arr   = df['low'].values
atr_arr   = df['atr14'].values

for i in range(200, len(df)-MAX_BARS-1):
    atr = atr_arr[i]
    if atr <= 0 or np.isnan(atr):
        continue
    entry = close_arr[i]
    tp_l  = entry + TARGET_R * atr
    sl_l  = entry - STOP_R   * atr
    tp_s  = entry - TARGET_R * atr
    sl_s  = entry + STOP_R   * atr

    lw = ls = False
    for j in range(i+1, min(i+MAX_BARS+1, len(df))):
        h = high_arr[j]; l = low_arr[j]
        if not lw:
            if l <= sl_l: lw = False; break
            if h >= tp_l: lw = True;  break
        if not ls:
            if h >= sl_s: ls = False; break
            if l <= tp_s: ls = True;  break
        if lw or ls: break
    long_win[i]  = lw
    short_win[i] = ls

df['long_win']  = long_win
df['short_win'] = short_win

print("  Done measuring outcomes.")

# ── Filter definitions ────────────────────────────────────────────────────
# Each filter is a boolean Series. True = entry allowed.
# We test all combinations of filters for LONG and SHORT separately.

filters = {
    # Trend
    'H4up':     df['h4_up']==True,
    'H4dn':     df['h4_down']==True,
    'D1up':     df['d1_up']==True,
    'D1dn':     df['d1_up']==False,
    'D1strong': df['d1_strong']==True,
    'W1up':     df['w1_up']==True,
    'W1dn':     df['w1_up']==False,
    'Abv50':    df['above_50']==True,
    'Blw50':    df['above_50']==False,
    # Volatility
    'ATRnorm':  df['atr_norm']==True,
    'ATRhi':    df['atr_hi']==True,
    'ATRlo':    df['atr_lo']==True,
    # Session / time
    'Asian':    df['sess_asian'],
    'London':   df['sess_london'],
    'NY':       df['sess_ny'],
    'PreLdn':   df['sess_early'],
    'Lunch':    df['sess_lunch'],
    'MonThu':   df['dow'] < 4,
    'MonWed':   df['dow'] < 3,
    # Bar type
    'BullBar':  df['bull_bar'],
    'BearBar':  df['bear_bar'],
    # Momentum
    'Prev3up':  df['prev3_up'],
    'Prev3dn':  df['prev3_up']==False,
}

# ── Grid search ───────────────────────────────────────────────────────────
print("Grid searching filter combinations...")

# LONG filters: must include at least one trend filter
long_trend  = ['H4up','D1up','D1strong','W1up','Abv50']
long_time   = ['Asian','London','NY','PreLdn','Lunch','MonThu','MonWed']
long_vol    = ['ATRnorm','ATRhi']
long_bar    = ['BullBar','Prev3up']

# SHORT filters
short_trend = ['H4dn','D1dn','W1dn','Blw50']
short_time  = ['Asian','London','NY','PreLdn','Lunch','MonThu','MonWed']
short_vol   = ['ATRnorm','ATRhi']
short_bar   = ['BearBar','Prev3dn']

def eval_combo(mask, outcome_col, direction):
    """Evaluate a filter combination. Return stats dict."""
    sub = df[mask & df[outcome_col].notna()]
    n   = len(sub)
    if n < 30:
        return None
    wr  = sub[outcome_col].mean()
    # Simulate PnL (win=+TARGET_R*RISK, loss=-STOP_R*RISK)
    pnl_series = sub[outcome_col].map({True: TARGET_R*RISK_USD, False: -STOP_R*RISK_USD})
    pnl = pnl_series.sum()
    # MaxDD
    bal  = START_BAL + pnl_series.cumsum()
    peak = bal.cummax()
    dd   = ((peak - bal)/peak*100).max()
    # Per year
    sub2 = sub.copy()
    sub2['year'] = sub2.index.year
    yearly_pnl = sub2.groupby('year').apply(
        lambda x: x[outcome_col].map({True:TARGET_R*RISK_USD,False:-STOP_R*RISK_USD}).sum()
    )
    all_pos = (yearly_pnl > 0).all()
    n_years = len(yearly_pnl)
    return {
        'n': n, 'wr': wr, 'pnl': pnl, 'dd': dd,
        'all_pos': all_pos, 'n_years': n_years,
        'trades_per_month': n / 77,  # ~77 months in dataset
    }

long_results  = []
short_results = []

# LONG: test combinations of (trend, time_or_none, vol_or_none, bar_or_none)
print("  Testing LONG combos...")
for tf in long_trend:
    base = filters[tf]
    # Test single filter
    r = eval_combo(base & df['bull_bar'], 'long_win', 'long')
    if r and r['wr'] > 0.50 and r['dd'] < 20:
        long_results.append({'filters': tf+'+BullBar', **r})

    for tm in long_time:
        mask = base & filters[tm]
        r = eval_combo(mask, 'long_win', 'long')
        if r and r['wr'] > 0.50 and r['dd'] < 20:
            long_results.append({'filters': tf+'+'+tm, **r})

        r2 = eval_combo(mask & df['bull_bar'], 'long_win', 'long')
        if r2 and r2['wr'] > 0.50 and r2['dd'] < 20:
            long_results.append({'filters': tf+'+'+tm+'+BullBar', **r2})

        for vf in long_vol:
            mask2 = mask & filters[vf]
            r3 = eval_combo(mask2, 'long_win', 'long')
            if r3 and r3['wr'] > 0.50 and r3['dd'] < 20:
                long_results.append({'filters': tf+'+'+tm+'+'+vf, **r3})

# Also test double trend filters
for tf1, tf2 in [('H4up','D1up'),('H4up','W1up'),('D1strong','W1up'),
                  ('H4up','D1strong'),('Abv50','D1up'),('Abv50','W1up')]:
    base = filters[tf1] & filters[tf2]
    for tm in long_time + ['']:
        mask = base & filters[tm] if tm else base
        r = eval_combo(mask, 'long_win', 'long')
        label = tf1+'+'+tf2+('+'+tm if tm else '')
        if r and r['wr'] > 0.50 and r['dd'] < 20:
            long_results.append({'filters': label, **r})
        r2 = eval_combo(mask & df['bull_bar'], 'long_win', 'long')
        if r2 and r2['wr'] > 0.50 and r2['dd'] < 20:
            long_results.append({'filters': label+'+BullBar', **r2})

print(f"  LONG combos found: {len(long_results)}")

# SHORT combos
print("  Testing SHORT combos...")
for tf in short_trend:
    base = filters[tf]
    r = eval_combo(base & df['bear_bar'], 'short_win', 'short')
    if r and r['wr'] > 0.50 and r['dd'] < 20:
        short_results.append({'filters': tf+'+BearBar', **r})

    for tm in short_time:
        mask = base & filters[tm]
        r = eval_combo(mask, 'short_win', 'short')
        if r and r['wr'] > 0.50 and r['dd'] < 20:
            short_results.append({'filters': tf+'+'+tm, **r})

        r2 = eval_combo(mask & df['bear_bar'], 'short_win', 'short')
        if r2 and r2['wr'] > 0.50 and r2['dd'] < 20:
            short_results.append({'filters': tf+'+'+tm+'+BearBar', **r2})

for tf1, tf2 in [('H4dn','D1dn'),('H4dn','W1dn'),('D1dn','W1dn'),
                  ('Blw50','D1dn'),('Blw50','W1dn')]:
    base = filters[tf1] & filters[tf2]
    for tm in short_time + ['']:
        mask = base & filters[tm] if tm else base
        r = eval_combo(mask, 'short_win', 'short')
        label = tf1+'+'+tf2+('+'+tm if tm else '')
        if r and r['wr'] > 0.50 and r['dd'] < 20:
            short_results.append({'filters': label, **r})
        r2 = eval_combo(mask & df['bear_bar'], 'short_win', 'short')
        if r2 and r2['wr'] > 0.50 and r2['dd'] < 20:
            short_results.append({'filters': label+'+BearBar', **r2})

print(f"  SHORT combos found: {len(short_results)}")

# ── Report ────────────────────────────────────────────────────────────────
def show_top(results, direction, top_n=15):
    if not results:
        print(f"  No {direction} combos found with WR>50% and DD<20%")
        return
    df_r = pd.DataFrame(results).drop_duplicates('filters')
    # Score: WR * PnL / DD
    df_r['score'] = df_r['wr'] * df_r['pnl'] / (df_r['dd']+1)
    df_r = df_r.sort_values('score', ascending=False).head(top_n)
    print(f"\n  {'Filter combination':<38} {'N':>4} {'T/mo':>5} {'WR':>6} "
          f"{'PnL':>9} {'MaxDD':>7} {'AllYrs':>7}")
    print(f"  {'-'*80}")
    for _, r in df_r.iterrows():
        ay = 'YES' if r['all_pos'] else 'no'
        print(f"  {r['filters']:<38} {r['n']:>4} {r['trades_per_month']:>5.1f} "
              f"{r['wr']:>6.1%} ${r['pnl']:>8,.0f} {r['dd']:>6.1f}% {ay:>7}")

print(f"\n{'='*60}")
print(f"  TOP LONG ENTRY CONDITIONS (WR>50%, DD<20%)")
print(f"{'='*60}")
show_top(long_results, 'long')

print(f"\n{'='*60}")
print(f"  TOP SHORT ENTRY CONDITIONS (WR>50%, DD<20%)")
print(f"{'='*60}")
show_top(short_results, 'short')

# ── Best LONG+SHORT combined ──────────────────────────────────────────────
print(f"\n{'='*60}")
print(f"  BEST COMBINED LONG+SHORT STRATEGY")
print(f"{'='*60}")

if long_results and short_results:
    df_l = pd.DataFrame(long_results).drop_duplicates('filters')
    df_s = pd.DataFrame(short_results).drop_duplicates('filters')
    df_l['score'] = df_l['wr'] * df_l['pnl'] / (df_l['dd']+1)
    df_s['score'] = df_s['wr'] * df_s['pnl'] / (df_s['dd']+1)

    top_l = df_l.sort_values('score', ascending=False).head(5)
    top_s = df_s.sort_values('score', ascending=False).head(5)

    combos = []
    for _, lr in top_l.iterrows():
        for _, sr in top_s.iterrows():
            lmask = eval(f"filters['{lr['filters'].split('+')[0]}']")
            for part in lr['filters'].split('+')[1:]:
                if part in filters: lmask = lmask & filters[part]

            smask = eval(f"filters['{sr['filters'].split('+')[0]}']")
            for part in sr['filters'].split('+')[1:]:
                if part in filters: smask = smask & filters[part]

            l_pnl = df[lmask]['long_win'].map({True:TARGET_R*RISK_USD,False:-STOP_R*RISK_USD})
            s_pnl = df[smask]['short_win'].map({True:TARGET_R*RISK_USD,False:-STOP_R*RISK_USD})
            combined = pd.concat([l_pnl, s_pnl]).sort_index()
            n   = len(combined)
            pnl = combined.sum()
            wr  = ((combined > 0).mean())
            bal = START_BAL + combined.cumsum()
            dd  = ((bal.cummax()-bal)/bal.cummax()*100).max()
            combos.append({
                'long':  lr['filters'], 'short': sr['filters'],
                'n': n, 'wr': wr, 'pnl': pnl, 'dd': dd,
            })

    combos = sorted(combos, key=lambda x: x['pnl'], reverse=True)
    print(f"\n  {'LONG filters':<30} {'SHORT filters':<30} {'N':>4} {'WR':>6} {'PnL':>9} {'DD':>7}")
    print(f"  {'-'*82}")
    for c in combos[:8]:
        print(f"  {c['long']:<30} {c['short']:<30} {c['n']:>4} "
              f"{c['wr']:>6.1%} ${c['pnl']:>8,.0f} {c['dd']:>6.1f}%")

print("\nDone.")
