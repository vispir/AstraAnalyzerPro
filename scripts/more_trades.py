# -*- coding: utf-8 -*-
"""
Test various ways to get more trades per day
without destroying the edge
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
TP_R      = 2.5
TRAIL_START = 1.2
TRAIL_STEP  = 0.4
MAX_BARS    = 350

print("Loading...")
df = pd.read_parquet(M15_FILE)
df.index = pd.to_datetime(df.index, utc=True)
df = df.sort_index()
df.columns = [c.lower() for c in df.columns]

df['tr']  = np.maximum(df['high']-df['low'],
            np.maximum(abs(df['high']-df['close'].shift(1)),
                       abs(df['low'] -df['close'].shift(1))))
df['atr'] = df['tr'].ewm(alpha=1/14, adjust=False).mean()

h4 = df.resample('4h', origin='epoch').agg(
    open=('open','first'), high=('high','max'),
    low=('low','min'), close=('close','last')).dropna()
h4['ema20']  = h4['close'].ewm(span=20, adjust=False).mean()
h4['slope3'] = h4['ema20'] - h4['ema20'].shift(3)
h4['h4_up']  = (h4['close'] > h4['ema20']) & (h4['slope3'] > 0)
h4['h4_dn']  = (h4['close'] < h4['ema20']) & (h4['slope3'] < 0)
h4['ema50']  = h4['close'].ewm(span=50, adjust=False).mean()
h4['abv50']  = h4['close'] > h4['ema50']

d1 = df.resample('1D', origin='epoch').agg(close=('close','last')).dropna()
d1['d1_ema20']= d1['close'].ewm(span=20, adjust=False).mean()
d1['d1_ema50']= d1['close'].ewm(span=50, adjust=False).mean()
d1['d1_up']   = d1['close'] > d1['d1_ema20']
d1['d1_str']  = (d1['close'] > d1['d1_ema20']) & (d1['close'] > d1['d1_ema50'])

w1 = df.resample('1W', origin='epoch').agg(close=('close','last')).dropna()
w1['w1_ema10']= w1['close'].ewm(span=10, adjust=False).mean()
w1['w1_up']   = w1['close'] > w1['w1_ema10']
w1['w1_dn']   = w1['close'] < w1['w1_ema10']

def mmap(s):
    return s.shift(1).reindex(df.index, method='ffill')

df['h4_up']  = mmap(h4['h4_up'])
df['h4_dn']  = mmap(h4['h4_dn'])
df['h4_abv'] = mmap(h4['abv50'])
df['d1_up']  = mmap(d1['d1_up'])
df['d1_str'] = mmap(d1['d1_str'])
df['w1_up']  = mmap(w1['w1_up'])
df['w1_dn']  = mmap(w1['w1_dn'])

df['hour']   = df.index.hour
df['dow']    = df.index.dayofweek
df['bear']   = df['close'] < df['open']
df['bull']   = df['close'] > df['open']
df['up3h']   = df['close'] > df['close'].shift(12)
df['dn3h']   = df['close'] < df['close'].shift(12)

print(f"  {len(df)} bars ready.")

def sim(idx, direction, sl_dist):
    entry   = df['close'].iloc[idx]
    sl      = entry - sl_dist if direction=='long' else entry + sl_dist
    tp      = entry + TP_R*sl_dist if direction=='long' else entry - TP_R*sl_dist
    cur_sl  = sl
    best_r  = 0.0
    for i2 in range(idx+1, min(idx+MAX_BARS+1, len(df))):
        h  = df['high'].iloc[i2]
        lo = df['low'].iloc[i2]
        if direction == 'long':
            if lo <= cur_sl: return (cur_sl-entry)/sl_dist*RISK_USD, 'sl'
            if h  >= tp:     return TP_R*RISK_USD, 'tp'
            r = (h-entry)/sl_dist
            if r > best_r: best_r = r
            if best_r >= TRAIL_START:
                ns = entry + (best_r-TRAIL_STEP)*sl_dist
                if ns > cur_sl: cur_sl = ns
        else:
            if h  >= cur_sl: return (entry-cur_sl)/sl_dist*RISK_USD, 'sl'
            if lo <= tp:     return TP_R*RISK_USD, 'tp'
            r = (entry-lo)/sl_dist
            if r > best_r: best_r = r
            if best_r >= TRAIL_START:
                ns = entry - (best_r-TRAIL_STEP)*sl_dist
                if ns < cur_sl: cur_sl = ns
    r_f = (cur_sl-entry)/sl_dist if direction=='long' else (entry-cur_sl)/sl_dist
    return r_f*RISK_USD, 'timeout'

def run(cond_l, cond_s, max_per_day=1, label=""):
    trades = []
    traded = {}
    for i in range(300, len(df)-MAX_BARS-1):
        row = df.iloc[i]
        atr = row['atr']
        if atr <= 0 or pd.isna(atr): continue
        date = df.index[i].date()
        if row['dow'] == 4: continue

        if cond_l and traded.get((date,'long'),0) < max_per_day:
            try:
                if cond_l(row):
                    pnl, reason = sim(i, 'long', atr)
                    trades.append({'ts':df.index[i],'date':date,'year':df.index[i].year,
                        'month':df.index[i].to_period('M'),'direction':'long',
                        'pnl':pnl,'win':pnl>0,'reason':reason})
                    traded[(date,'long')] = traded.get((date,'long'),0)+1
            except: pass

        if cond_s and traded.get((date,'short'),0) < max_per_day:
            try:
                if cond_s(row):
                    pnl, reason = sim(i, 'short', atr)
                    trades.append({'ts':df.index[i],'date':date,'year':df.index[i].year,
                        'month':df.index[i].to_period('M'),'direction':'short',
                        'pnl':pnl,'win':pnl>0,'reason':reason})
                    traded[(date,'short')] = traded.get((date,'short'),0)+1
            except: pass
    return pd.DataFrame(trades)

def stats(T, label):
    if len(T)==0:
        print(f"  {label}: 0 trades")
        return
    n   = len(T)
    wr  = T['win'].mean()
    pnl = T['pnl'].sum()
    bal = START_BAL + T['pnl'].cumsum()
    dd  = ((bal.cummax()-bal)/bal.cummax()*100).max()
    mb  = bal.min()
    outcomes = T['win'].astype(int).values
    max_cl = cur_cl = 0
    for o in outcomes:
        cur_cl = cur_cl+1 if o==0 else 0
        max_cl = max(max_cl, cur_cl)

    yearly = T.groupby('year').agg(n=('pnl','count'), wr=('win','mean'), pnl=('pnl','sum'))
    all_pos = (yearly['pnl'] > 0).all()

    months = sorted(T['month'].unique())
    roll = []
    for m in months:
        sub = T[T['month']>=m]
        if len(sub)<5: continue
        b = START_BAL+sub['pnl'].cumsum()
        roll.append(b.min() < FLOOR)
    breach_pct = sum(roll)/len(roll) if roll else 0

    print(f"\n  {label}")
    print(f"    N={n} ({n/77:.1f}/mo)  WR={wr:.1%}  PnL=${pnl:,.0f}  DD={dd:.1f}%  "
          f"Floor={'BREACH' if mb<FLOOR else 'SAFE'}  AllYrs+={'YES' if all_pos else 'NO'}  "
          f"RollBreach={breach_pct:.0%}")
    print(f"    MaxConsecLoss={max_cl}  MinBal=${mb:,.0f}")
    print(f"    Year-by-year:", end="")
    for yr, r in yearly.iterrows():
        sign='+' if r['pnl']>=0 else ''
        print(f"  {yr}:{sign}${r['pnl']:,.0f}(WR {r['wr']:.0%})", end="")
    print()

print(f"\n{'='*70}")
print(f"  BASELINE — Strategy B (reference)")
print(f"{'='*70}")
cL_B = lambda r: r['h4_up'] and r['w1_up'] and r['hour'] in [5,8]
cS_B = lambda r: r['h4_dn'] and r['w1_dn'] and r['bear'] and r['hour'] in [15,16]
stats(run(cL_B, cS_B), "Strategy B (H4+W1, h05+h08 L / h15-16+Bear S)")

print(f"\n{'='*70}")
print(f"  OPTION 1: Remove W1 filter — use H4 trend only")
print(f"{'='*70}")
cL1 = lambda r: r['h4_up'] and r['hour'] in [5,8]
cS1 = lambda r: r['h4_dn'] and r['bear'] and r['hour'] in [15,16]
stats(run(cL1, cS1), "H4 only (no W1), h05+h08 L / h15-16+Bear S")

# Also without bear bar for SHORT
cS1b = lambda r: r['h4_dn'] and r['hour'] in [15,16]
stats(run(cL1, cS1b), "H4 only (no W1, no bear bar req), h05+h08 L / h15-16 S")

print(f"\n{'='*70}")
print(f"  OPTION 2: Add more entry hours")
print(f"{'='*70}")
# LONG: Asian (00-02), pre-London (05-06), London open (08-09)
cL2 = lambda r: r['h4_up'] and r['w1_up'] and r['hour'] in [0,1,2,5,6,8,9]
cS2 = lambda r: r['h4_dn'] and r['w1_dn'] and r['bear'] and r['hour'] in [13,14,15,16,17]
stats(run(cL2, cS2), "B + more hours: L h0-2+5-6+8-9, S h13-17+Bear")

cL2b = lambda r: r['h4_up'] and r['w1_up'] and r['hour'] in [0,1,5,6,8,9,13,14,15,16]
cS2b = lambda r: r['h4_dn'] and r['w1_dn'] and r['bear'] and r['hour'] in [0,1,8,9,13,14,15,16,17]
stats(run(cL2b, cS2b), "B + all sessions: L many hours, S many hours")

print(f"\n{'='*70}")
print(f"  OPTION 3: Remove W1 + Add more hours")
print(f"{'='*70}")
cL3 = lambda r: r['h4_up'] and r['hour'] in [0,1,5,6,8,9]
cS3 = lambda r: r['h4_dn'] and r['bear'] and r['hour'] in [13,14,15,16,17]
stats(run(cL3, cS3), "H4 only + more hours: L h0-1+5-6+8-9, S h13-17+Bear")

cL3b = lambda r: r['h4_up'] and r['d1_up'] and r['hour'] in [0,1,5,6,8,9]
cS3b = lambda r: r['h4_dn'] and not df['d1_up'].iloc[0] and r['bear'] and r['hour'] in [13,14,15,16,17]
# Better: use D1 instead of W1
cS3c = lambda r: r['h4_dn'] and not r['d1_up'] and r['bear'] and r['hour'] in [13,14,15,16,17]
stats(run(cL3b, cS3c), "H4+D1 (no W1) + more hours: L h0-1+5-6+8-9, S h13-17+Bear")

print(f"\n{'='*70}")
print(f"  OPTION 4: 2 trades per day (same Strategy B conditions)")
print(f"{'='*70}")
stats(run(cL_B, cS_B, max_per_day=2), "Strategy B x2/day")

print(f"\n{'='*70}")
print(f"  OPTION 5: Both directions when H4 only (no W1) — any hour in session")
print(f"{'='*70}")
cL5 = lambda r: r['h4_up'] and r['d1_up'] and r['hour'] in [0,1,2,5,6,8,9]
cS5 = lambda r: r['h4_dn'] and not r['d1_up'] and r['bear'] and r['hour'] in [8,9,13,14,15,16,17]
stats(run(cL5, cS5), "H4+D1, wide hours, max 1/day")
stats(run(cL5, cS5, max_per_day=2), "H4+D1, wide hours, max 2/day")

print(f"\n{'='*70}")
print(f"  OPTION 6: No bear/bull bar filter + H4 only + wide hours")
print(f"{'='*70}")
cL6 = lambda r: r['h4_up'] and r['d1_up'] and r['hour'] in [5,6,8,9]
cS6 = lambda r: r['h4_dn'] and not r['d1_up'] and r['hour'] in [13,14,15,16]
stats(run(cL6, cS6), "H4+D1 clean: L h5-6+8-9, S h13-16, no bar filter")
stats(run(cL6, cS6, max_per_day=2), "H4+D1 clean x2/day")

print(f"\n{'='*70}")
print(f"  OPTION 7: MOST TRADES — H4 only, all session hours, 2/day")
print(f"{'='*70}")
cL7 = lambda r: r['h4_up'] and r['hour'] in [0,1,2,3,4,5,6,7,8,9,13,14,15,16]
cS7 = lambda r: r['h4_dn'] and r['hour'] in [0,1,2,3,4,5,6,7,8,9,13,14,15,16]
stats(run(cL7, cS7), "H4 only, any hour, 1/day each dir")
stats(run(cL7, cS7, max_per_day=2), "H4 only, any hour, 2/day each dir")

print("\nDone.")
