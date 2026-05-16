# -*- coding: utf-8 -*-
"""
Optimize TP ratio and entry hours for L3+S1b strategy.
Also try wider conditions.
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
TRAIL_STEP  = 0.4
MAX_BARS    = 500

print("Loading data...")
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
h4['ema50']  = h4['close'].ewm(span=50, adjust=False).mean()
h4['slope3'] = h4['ema20'] - h4['ema20'].shift(3)
h4['h4_up']  = (h4['close'] > h4['ema20']) & (h4['slope3'] > 0)
h4['h4_dn']  = (h4['close'] < h4['ema20']) & (h4['slope3'] < 0)
h4['abv50']  = h4['close'] > h4['ema50']

d1 = df.resample('1D', origin='epoch').agg(close=('close','last')).dropna()
d1['d1_ema20']= d1['close'].ewm(span=20, adjust=False).mean()
d1['d1_ema50']= d1['close'].ewm(span=50, adjust=False).mean()
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
df['d1_str'] = mmap(d1['d1_str'])
df['w1_up']  = mmap(w1['w1_up'])
df['w1_dn']  = mmap(w1['w1_dn'])

df['hour']   = df.index.hour
df['dow']    = df.index.dayofweek
df['bear']   = df['close'] < df['open']
df['bull']   = df['close'] > df['open']
df['up3h']   = df['close'] > df['close'].shift(12)
df['dn3h']   = df['close'] < df['close'].shift(12)
df['dn6h']   = df['close'] < df['close'].shift(24)

print(f"  {len(df)} bars ready")

def sim(df_full, idx, direction, sl_dist, tp_r, trail_start):
    entry   = df_full['close'].iloc[idx]
    sl      = entry - sl_dist if direction=='long' else entry + sl_dist
    tp      = entry + tp_r*sl_dist if direction=='long' else entry - tp_r*sl_dist
    cur_sl  = sl
    best_r  = 0.0

    for _, bar in df_full.iloc[idx+1:idx+1+MAX_BARS].iterrows():
        if direction == 'long':
            if bar['low']  <= cur_sl: return (cur_sl-entry)/sl_dist*RISK_USD, 'sl'
            if bar['high'] >= tp:     return tp_r*RISK_USD, 'tp'
            r = (bar['high']-entry)/sl_dist
            if r > best_r: best_r = r
            if best_r >= trail_start:
                ns = entry + (best_r-TRAIL_STEP)*sl_dist
                if ns > cur_sl: cur_sl = ns
        else:
            if bar['high'] >= cur_sl: return (entry-cur_sl)/sl_dist*RISK_USD, 'sl'
            if bar['low']  <= tp:     return tp_r*RISK_USD, 'tp'
            r = (entry-bar['low'])/sl_dist
            if r > best_r: best_r = r
            if best_r >= trail_start:
                ns = entry - (best_r-TRAIL_STEP)*sl_dist
                if ns < cur_sl: cur_sl = ns

    r_final = (cur_sl-entry)/sl_dist if direction=='long' else (entry-cur_sl)/sl_dist
    return r_final*RISK_USD, 'timeout'

def run(cond_fn, direction, tp_r=2.5, trail_start=1.2, max_per_day=1):
    trades = []
    traded = {}
    for i in range(300, len(df)-MAX_BARS-1):
        row = df.iloc[i]
        atr = row['atr']
        if atr <= 0 or pd.isna(atr):
            continue
        date = df.index[i].date()
        if traded.get((date, direction), 0) >= max_per_day:
            continue
        try:
            if not cond_fn(row): continue
        except: continue
        pnl, reason = sim(df, i, direction, atr, tp_r, trail_start)
        yr  = df.index[i].year
        mo  = df.index[i].to_period('M')
        trades.append({
            'ts': df.index[i], 'date': date, 'year': yr, 'month': mo,
            'hour': row['hour'], 'direction': direction,
            'pnl': pnl, 'win': pnl > 0, 'reason': reason,
        })
        traded[(date, direction)] = traded.get((date, direction), 0) + 1
    return pd.DataFrame(trades)

def quick_stats(T):
    if len(T)==0: return "0 trades"
    n   = len(T)
    wr  = T['win'].mean()
    pnl = T['pnl'].sum()
    bal = START_BAL + T['pnl'].cumsum()
    dd  = ((bal.cummax()-bal)/bal.cummax()*100).max()
    mb  = bal.min()
    return f"N={n} ({n/77:.1f}/mo)  WR={wr:.1%}  PnL=${pnl:,.0f}  DD={dd:.1f}%  {'BREACH' if mb<FLOOR else 'ok'}"

def full_stats(T, name):
    if len(T)==0:
        print(f"  No trades.")
        return
    n   = len(T)
    wr  = T['win'].mean()
    pnl = T['pnl'].sum()
    aw  = T[T['win']]['pnl'].mean() if T['win'].any() else 0
    al  = T[~T['win']]['pnl'].mean() if (~T['win']).any() else 0
    bal = START_BAL + T['pnl'].cumsum()
    dd  = ((bal.cummax()-bal)/bal.cummax()*100).max()
    mb  = bal.min()
    pf  = abs(aw*wr / (al*(1-wr))) if al!=0 and (1-wr)>0 else 0
    outcomes = T['win'].astype(int).values
    max_cl = cur_cl = 0
    for o in outcomes:
        cur_cl = cur_cl+1 if o==0 else 0
        max_cl = max(max_cl, cur_cl)

    print(f"  Trades: {n} ({n/77:.1f}/mo)  WR: {wr:.1%}  PF: {pf:.2f}")
    print(f"  PnL: ${pnl:,.0f}  |  MaxDD: {dd:.2f}%  |  MinBal: ${mb:,.0f}")
    print(f"  Avg Win: ${aw:.0f}  |  Avg Loss: ${al:.0f}  |  Max consec losses: {max_cl}")
    print(f"  Floor breach: {'YES !!!' if mb<FLOOR else 'NO'}")

    yearly = T.groupby('year').agg(n=('pnl','count'), wr=('win','mean'), pnl=('pnl','sum'))
    all_pos = True
    print(f"\n  Year-by-year:")
    for yr, r in yearly.iterrows():
        sign = '+' if r['pnl']>=0 else '-'
        if r['pnl']<0: all_pos = False
        bar = '#'*max(0, int(abs(r['pnl'])/300))
        print(f"    {yr}: N={r['n']:3.0f}  WR={r['wr']:.1%}  {sign}${abs(r['pnl']):,.0f}  {bar}")
    print(f"  All years positive? {'YES' if all_pos else 'NO'}")

    oos = T[T['year']>=2024]
    if len(oos):
        bal_o = START_BAL + oos['pnl'].cumsum()
        dd_o  = ((bal_o.cummax()-bal_o)/bal_o.cummax()*100).max()
        print(f"\n  OOS 2024-2026: N={len(oos)}  WR={oos['win'].mean():.1%}  "
              f"PnL=${oos['pnl'].sum():,.0f}  MaxDD={dd_o:.2f}%")

    months = sorted(T['month'].unique())
    roll = []
    for m in months:
        sub = T[T['month']>=m]
        if len(sub)<5: continue
        b = START_BAL + sub['pnl'].cumsum()
        roll.append((str(m), len(sub), b.min(), b.min()<FLOOR))
    if roll:
        total = len(roll)
        breach = sum(1 for _,_,_,b in roll if b)
        print(f"\n  Rolling breach: {breach}/{total} ({breach/total:.1%})")

# ── Base conditions ────────────────────────────────────────────────────────
L_BASE = lambda r: r['h4_up'] and r['w1_up'] and r['hour']==5 and r['dow']!=4
S_BASE = lambda r: r['h4_dn'] and r['w1_dn'] and r['hour'] in [15,16] and r['bear'] and r['dow']!=4

# ── 1. Test different TP ratios ────────────────────────────────────────────
print(f"\n{'='*65}")
print(f"  EFFECT OF TP RATIO (L3+S1b, same conditions)")
print(f"{'='*65}")
print(f"  {'TP_R':<8} {'LONG':<45} {'SHORT'}")
print(f"  {'-'*95}")

for tp_r, trail in [(1.5, 0.8), (2.0, 1.2), (2.5, 1.2), (3.0, 1.5), (4.0, 2.0)]:
    TL = run(L_BASE, 'long',  tp_r=tp_r, trail_start=trail)
    TS = run(S_BASE, 'short', tp_r=tp_r, trail_start=trail)
    ls = quick_stats(TL)
    ss = quick_stats(TS)
    print(f"  TP={tp_r}R:  LONG  {ls}")
    print(f"           SHORT {ss}")
    if len(TL)>0 and len(TS)>0:
        TC = pd.concat([TL, TS]).sort_values('ts').reset_index(drop=True)
        print(f"           COMBO {quick_stats(TC)}")
    print()

# ── 2. More LONG hours (wider window) ─────────────────────────────────────
print(f"\n{'='*65}")
print(f"  ADDING MORE LONG ENTRY HOURS (H4up+W1up, TP=2.5R)")
print(f"{'='*65}")
print(f"  {'Hours':<20} {'Stats'}")
print(f"  {'-'*70}")

for hrs in [[5], [5,6], [5,6,7], [0,1,5], [4,5], [5,13], [5,8]]:
    cond = lambda r, h=hrs: r['h4_up'] and r['w1_up'] and r['hour'] in h and r['dow']!=4
    TL = run(cond, 'long')
    print(f"  h{hrs!s:<18} {quick_stats(TL)}")

# ── 3. Add SHORT hours ─────────────────────────────────────────────────────
print(f"\n{'='*65}")
print(f"  WIDER SHORT CONDITIONS (H4dn+W1dn+Bear, TP=2.5R)")
print(f"{'='*65}")

for hrs in [[16], [15,16], [15,16,17], [8,9], [8,9,16], [13,14]]:
    cond = lambda r, h=hrs: r['h4_dn'] and r['w1_dn'] and r['hour'] in h and r['bear'] and r['dow']!=4
    TS = run(cond, 'short')
    print(f"  h{hrs!s:<18} {quick_stats(TS)}")

# ── 4. Two trades per direction per day ───────────────────────────────────
print(f"\n{'='*65}")
print(f"  2 TRADES/DAY (L3+S1b, TP=2.5R)")
print(f"{'='*65}")
TL2 = run(L_BASE, 'long',  max_per_day=2)
TS2 = run(S_BASE, 'short', max_per_day=2)
print(f"  LONG (2/day):  {quick_stats(TL2)}")
print(f"  SHORT (2/day): {quick_stats(TS2)}")
if len(TL2)>0 and len(TS2)>0:
    TC2 = pd.concat([TL2, TS2]).sort_values('ts').reset_index(drop=True)
    print(f"  COMBO (2/day): {quick_stats(TC2)}")

# ── 5. Best combo: L3 wider + S1b + TP=3R ────────────────────────────────
print(f"\n{'='*65}")
print(f"  FULL TEST: L(H4up+W1up+h5-6) + S(H4dn+W1dn+h15-16+Bear), TP=3R")
print(f"{'='*65}")
Lw = run(lambda r: r['h4_up'] and r['w1_up'] and r['hour'] in [5,6] and r['dow']!=4,
         'long', tp_r=3.0, trail_start=1.5)
Sw = run(lambda r: r['h4_dn'] and r['w1_dn'] and r['hour'] in [15,16] and r['bear'] and r['dow']!=4,
         'short', tp_r=3.0, trail_start=1.5)
if len(Lw)>0 and len(Sw)>0:
    TCw = pd.concat([Lw, Sw]).sort_values('ts').reset_index(drop=True)
    full_stats(TCw, "L(h5-6)+S(h15-16+Bear), TP=3R")

# ── 6. Aggressive: more hours + TP=3.5R ──────────────────────────────────
print(f"\n{'='*65}")
print(f"  AGGRESSIVE: L(H4up+W1up+h5-7+Bull) + S(H4dn+W1dn+h14-17+Bear), TP=3.5R")
print(f"{'='*65}")
La = run(lambda r: r['h4_up'] and r['w1_up'] and r['hour'] in [5,6,7] and r['bull'] and r['dow']!=4,
         'long', tp_r=3.5, trail_start=1.5)
Sa = run(lambda r: r['h4_dn'] and r['w1_dn'] and r['hour'] in [14,15,16,17] and r['bear'] and r['dow']!=4,
         'short', tp_r=3.5, trail_start=1.5)
if len(La)>0 and len(Sa)>0:
    TCa = pd.concat([La, Sa]).sort_values('ts').reset_index(drop=True)
    full_stats(TCa, "Aggressive")

print("\nDone.")
