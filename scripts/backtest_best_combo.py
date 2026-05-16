# -*- coding: utf-8 -*-
"""
Final optimization: best combinations from all research
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

w1 = df.resample('1W', origin='epoch').agg(close=('close','last')).dropna()
w1['w1_ema10']= w1['close'].ewm(span=10, adjust=False).mean()
w1['w1_up']   = w1['close'] > w1['w1_ema10']
w1['w1_dn']   = w1['close'] < w1['w1_ema10']

def mmap(s):
    return s.shift(1).reindex(df.index, method='ffill')

df['h4_up']  = mmap(h4['h4_up'])
df['h4_dn']  = mmap(h4['h4_dn'])
df['h4_abv'] = mmap(h4['abv50'])
df['w1_up']  = mmap(w1['w1_up'])
df['w1_dn']  = mmap(w1['w1_dn'])

df['hour']   = df.index.hour
df['dow']    = df.index.dayofweek
df['bear']   = df['close'] < df['open']
df['bull']   = df['close'] > df['open']
df['up3h']   = df['close'] > df['close'].shift(12)

print(f"  {len(df)} bars ready.")

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
    r_f = (cur_sl-entry)/sl_dist if direction=='long' else (entry-cur_sl)/sl_dist
    return r_f*RISK_USD, 'timeout'

def run(cond_fn, direction, tp_r=2.5, trail_start=1.2, max_per_day=1):
    trades = []
    traded = {}
    for i in range(300, len(df)-MAX_BARS-1):
        row = df.iloc[i]
        atr = row['atr']
        if atr <= 0 or pd.isna(atr): continue
        date = df.index[i].date()
        if traded.get((date, direction), 0) >= max_per_day: continue
        try:
            if not cond_fn(row): continue
        except: continue
        pnl, reason = sim(df, i, direction, atr, tp_r, trail_start)
        yr = df.index[i].year
        mo = df.index[i].to_period('M')
        trades.append({
            'ts': df.index[i], 'date': date, 'year': yr, 'month': mo,
            'direction': direction, 'pnl': pnl, 'win': pnl > 0, 'reason': reason,
        })
        traded[(date, direction)] = traded.get((date, direction), 0) + 1
    return pd.DataFrame(trades)

def full_report(T, name):
    print(f"\n{'='*65}")
    print(f"  {name}")
    print(f"{'='*65}")
    if len(T)==0:
        print("  No trades."); return

    n   = len(T)
    wr  = T['win'].mean()
    pnl = T['pnl'].sum()
    aw  = T[T['win']]['pnl'].mean() if T['win'].any() else 0
    al  = T[~T['win']]['pnl'].mean() if (~T['win']).any() else 0
    pf  = abs(aw*wr / (al*(1-wr))) if al!=0 and (1-wr)>0 else 0
    bal = START_BAL + T['pnl'].cumsum()
    dd  = ((bal.cummax()-bal)/bal.cummax()*100).max()
    mb  = bal.min()
    outcomes = T['win'].astype(int).values
    max_cl = cur_cl = 0
    for o in outcomes:
        cur_cl = cur_cl+1 if o==0 else 0
        max_cl = max(max_cl, cur_cl)

    print(f"  Trades: {n} ({n/77:.1f}/mo)  WR: {wr:.1%}  PF: {pf:.2f}")
    print(f"  PnL: ${pnl:,.0f}  AvgWin: ${aw:.0f}  AvgLoss: ${al:.0f}")
    print(f"  MaxDD: {dd:.2f}%  MinBal: ${mb:,.0f}  Floor: {'BREACH!!!' if mb<FLOOR else 'SAFE'}")
    print(f"  Max consec losses: {max_cl}")

    yearly = T.groupby('year').agg(n=('pnl','count'), wr=('win','mean'), pnl=('pnl','sum'))
    all_pos = True
    print(f"\n  Year-by-year:")
    for yr, r in yearly.iterrows():
        sign = '+' if r['pnl']>=0 else '-'
        if r['pnl'] < 0: all_pos = False
        bar = '#'*max(0, int(abs(r['pnl'])/400))
        print(f"    {yr}: N={r['n']:3.0f}  WR={r['wr']:.1%}  {sign}${abs(r['pnl']):,.0f}  {bar}")
    print(f"  All years positive? {'YES' if all_pos else 'NO'}")

    oos = T[T['year']>=2024]
    if len(oos):
        bal_o = START_BAL + oos['pnl'].cumsum()
        dd_o  = ((bal_o.cummax()-bal_o)/bal_o.cummax()*100).max()
        print(f"\n  OOS 2024-2026: N={len(oos)}  WR={oos['win'].mean():.1%}  "
              f"PnL=${oos['pnl'].sum():,.0f}  MaxDD={dd_o:.2f}%")
        for yr in [2024,2025,2026]:
            s = oos[oos['year']==yr]
            if len(s):
                print(f"    {yr}: N={len(s):3d}  WR={s['win'].mean():.1%}  ${s['pnl'].sum():,.0f}")

    months = sorted(T['month'].unique())
    roll = []
    for m in months:
        sub = T[T['month']>=m]
        if len(sub)<5: continue
        b = START_BAL + sub['pnl'].cumsum()
        mb2 = b.min()
        roll.append((str(m), len(sub), mb2, mb2<FLOOR))
    total  = len(roll)
    breach = sum(1 for _,_,_,b in roll if b)
    print(f"\n  Rolling start: {breach}/{total} breach ({breach/total:.1%})")
    roll.sort(key=lambda x: x[2])
    for m, nt, mb2, br in roll[:5]:
        print(f"    {m}  N={nt}  MinBal=${mb2:,.0f}  {'BREACH' if br else 'OK'}")

# ── Strategy A: Conservative — ALL years positive, DD<10% ────────────────
L_A = run(lambda r: r['h4_up'] and r['w1_up'] and r['hour']==5  and r['dow']!=4, 'long')
S_A = run(lambda r: r['h4_dn'] and r['w1_dn'] and r['hour'] in [15,16] and r['bear'] and r['dow']!=4, 'short')
if len(L_A)>0 and len(S_A)>0:
    full_report(pd.concat([L_A, S_A]).sort_values('ts').reset_index(drop=True),
                "A: CONSERVATIVE (L:h05 + S:h15-16+Bear, TP=2.5R)")

# ── Strategy B: Balanced — h05+h08 LONG, highest PnL for DD<11% ──────────
L_B = run(lambda r: r['h4_up'] and r['w1_up'] and r['hour'] in [5,8] and r['dow']!=4, 'long')
S_B = run(lambda r: r['h4_dn'] and r['w1_dn'] and r['hour'] in [15,16] and r['bear'] and r['dow']!=4, 'short')
if len(L_B)>0 and len(S_B)>0:
    full_report(pd.concat([L_B, S_B]).sort_values('ts').reset_index(drop=True),
                "B: BALANCED (L:h05+h08 + S:h15-16+Bear, TP=2.5R)")

# ── Strategy C: Aggressive — TP=4R ───────────────────────────────────────
L_C = run(lambda r: r['h4_up'] and r['w1_up'] and r['hour']==5 and r['dow']!=4,
          'long', tp_r=4.0, trail_start=2.0)
S_C = run(lambda r: r['h4_dn'] and r['w1_dn'] and r['hour'] in [15,16] and r['bear'] and r['dow']!=4,
          'short', tp_r=4.0, trail_start=2.0)
if len(L_C)>0 and len(S_C)>0:
    full_report(pd.concat([L_C, S_C]).sort_values('ts').reset_index(drop=True),
                "C: AGGRESSIVE TP=4R (L:h05 + S:h15-16+Bear)")

# ── Strategy D: h05+h08 LONG + TP=3.5R ───────────────────────────────────
L_D = run(lambda r: r['h4_up'] and r['w1_up'] and r['hour'] in [5,8] and r['dow']!=4,
          'long', tp_r=3.5, trail_start=1.5)
S_D = run(lambda r: r['h4_dn'] and r['w1_dn'] and r['hour'] in [15,16] and r['bear'] and r['dow']!=4,
          'short', tp_r=3.5, trail_start=1.5)
if len(L_D)>0 and len(S_D)>0:
    full_report(pd.concat([L_D, S_D]).sort_values('ts').reset_index(drop=True),
                "D: BALANCED TP=3.5R (L:h05+h08 + S:h15-16+Bear)")

# ── Strategy E: 2 trades/day + TP=2.5R ───────────────────────────────────
L_E = run(lambda r: r['h4_up'] and r['w1_up'] and r['hour']==5 and r['dow']!=4,
          'long', max_per_day=2)
S_E = run(lambda r: r['h4_dn'] and r['w1_dn'] and r['hour'] in [15,16] and r['bear'] and r['dow']!=4,
          'short', max_per_day=2)
if len(L_E)>0 and len(S_E)>0:
    full_report(pd.concat([L_E, S_E]).sort_values('ts').reset_index(drop=True),
                "E: 2x/day TP=2.5R (L:h05 x2 + S:h15-16+Bear x2)")

# ── Strategy F: h05+h08 + TP=4R ───────────────────────────────────────────
L_F = run(lambda r: r['h4_up'] and r['w1_up'] and r['hour'] in [5,8] and r['dow']!=4,
          'long', tp_r=4.0, trail_start=2.0)
S_F = run(lambda r: r['h4_dn'] and r['w1_dn'] and r['hour'] in [15,16] and r['bear'] and r['dow']!=4,
          'short', tp_r=4.0, trail_start=2.0)
if len(L_F)>0 and len(S_F)>0:
    full_report(pd.concat([L_F, S_F]).sort_values('ts').reset_index(drop=True),
                "F: h05+h08 + TP=4R (max profit search)")

print("\n\nDone.")
