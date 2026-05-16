# -*- coding: utf-8 -*-
"""
Full backtest of best conditions found by find_edge_v2:
LONG:  D1 strong up + hour 15 + 3h momentum up
SHORT: H4 down + W1 down + hour 16 + bear bar
Trailing stop from 1.2R, TP 2.5R, SL 1ATR
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
SL_R      = 1.0
TRAIL_START = 1.2
TRAIL_STEP  = 0.4
MAX_BARS    = 350

print("Loading data...")
df = pd.read_parquet(M15_FILE)
df.index = pd.to_datetime(df.index, utc=True)
df = df.sort_index()
df.columns = [c.lower() for c in df.columns]

# ATR
df['tr']  = np.maximum(df['high']-df['low'],
            np.maximum(abs(df['high']-df['close'].shift(1)),
                       abs(df['low'] -df['close'].shift(1))))
df['atr'] = df['tr'].ewm(alpha=1/14, adjust=False).mean()

# H4 trend
h4 = df.resample('4h', origin='epoch').agg(
    open=('open','first'), high=('high','max'),
    low=('low','min'), close=('close','last')).dropna()
h4['ema20']  = h4['close'].ewm(span=20, adjust=False).mean()
h4['slope3'] = h4['ema20'] - h4['ema20'].shift(3)
h4['h4_dn']  = (h4['close'] < h4['ema20']) & (h4['slope3'] < 0)
h4['h4_up']  = (h4['close'] > h4['ema20']) & (h4['slope3'] > 0)
h4['ema50']  = h4['close'].ewm(span=50, adjust=False).mean()
h4['abv50']  = h4['close'] > h4['ema50']

# D1 trend
d1 = df.resample('1D', origin='epoch').agg(close=('close','last')).dropna()
d1['d1_ema20']= d1['close'].ewm(span=20, adjust=False).mean()
d1['d1_ema50']= d1['close'].ewm(span=50, adjust=False).mean()
d1['d1_str']  = (d1['close'] > d1['d1_ema20']) & (d1['close'] > d1['d1_ema50'])
d1['d1_up']   = d1['close'] > d1['d1_ema20']

# W1 trend
w1 = df.resample('1W', origin='epoch').agg(close=('close','last')).dropna()
w1['w1_ema10']= w1['close'].ewm(span=10, adjust=False).mean()
w1['w1_dn']   = w1['close'] < w1['w1_ema10']
w1['w1_up']   = w1['close'] > w1['w1_ema10']

def mmap(s):
    return s.shift(1).reindex(df.index, method='ffill')

df['h4_dn']  = mmap(h4['h4_dn'])
df['h4_up']  = mmap(h4['h4_up'])
df['h4_abv'] = mmap(h4['abv50'])
df['d1_str'] = mmap(d1['d1_str'])
df['d1_up']  = mmap(d1['d1_up'])
df['w1_dn']  = mmap(w1['w1_dn'])
df['w1_up']  = mmap(w1['w1_up'])

df['hour']   = df.index.hour
df['dow']    = df.index.dayofweek
df['bear']   = df['close'] < df['open']
df['bull']   = df['close'] > df['open']
df['up3h']   = df['close'] > df['close'].shift(12)
df['dn3h']   = df['close'] < df['close'].shift(12)
df['up6h']   = df['close'] > df['close'].shift(24)

# ATR regime
df['atr_avg']= df['atr'].rolling(288).mean()
df['atr_hi'] = df['atr'] > 1.3 * df['atr_avg']

print(f"  {len(df)} bars loaded.")

# ── Trade simulator with trailing stop ─────────────────────────────────────
def sim(df_full, idx, direction, sl_dist):
    entry   = df_full['close'].iloc[idx]
    sl      = entry - sl_dist if direction=='long' else entry + sl_dist
    tp      = entry + TP_R*sl_dist if direction=='long' else entry - TP_R*sl_dist
    cur_sl  = sl
    best_r  = 0.0

    for _, bar in df_full.iloc[idx+1:idx+1+MAX_BARS].iterrows():
        if direction == 'long':
            if bar['low']  <= cur_sl: return (cur_sl-entry)/sl_dist*RISK_USD, 'sl'
            if bar['high'] >= tp:     return TP_R*RISK_USD, 'tp'
            r = (bar['high']-entry)/sl_dist
            if r > best_r: best_r = r
            if best_r >= TRAIL_START:
                ns = entry + (best_r-TRAIL_STEP)*sl_dist
                if ns > cur_sl: cur_sl = ns
        else:
            if bar['high'] >= cur_sl: return (entry-cur_sl)/sl_dist*RISK_USD, 'sl'
            if bar['low']  <= tp:     return TP_R*RISK_USD, 'tp'
            r = (entry-bar['low'])/sl_dist
            if r > best_r: best_r = r
            if best_r >= TRAIL_START:
                ns = entry - (best_r-TRAIL_STEP)*sl_dist
                if ns < cur_sl: cur_sl = ns

    r_final = (cur_sl-entry)/sl_dist if direction=='long' else (entry-cur_sl)/sl_dist
    return r_final*RISK_USD, 'timeout'

# ── Strategy definitions ───────────────────────────────────────────────────
# Test multiple variants and combos
STRATEGIES = {
    'L1: D1str+h15+U3h': {
        'direction': 'long',
        'cond': lambda r: r['d1_str'] and r['hour']==15 and r['up3h'] and r['dow']!=4
    },
    'L2: A50+W1up+h16+Vhi': {
        'direction': 'long',
        'cond': lambda r: r['h4_abv'] and r['w1_up'] and r['hour']==16 and r['atr_hi'] and r['dow']!=4
    },
    'L3: H4up+W1up+h05': {
        'direction': 'long',
        'cond': lambda r: r['h4_up'] and r['w1_up'] and r['hour']==5 and r['dow']!=4
    },
    'L4: D1str+h15+U3h OR A50+W1up+h16': {
        'direction': 'long',
        'cond': lambda r: r['dow']!=4 and (
            (r['d1_str'] and r['hour']==15 and r['up3h']) or
            (r['h4_abv'] and r['w1_up'] and r['hour']==16 and r['atr_hi'])
        )
    },
    'S1: H4dn+W1dn+h16+Bear': {
        'direction': 'short',
        'cond': lambda r: r['h4_dn'] and r['w1_dn'] and r['hour']==16 and r['bear'] and r['dow']!=4
    },
    'S2: H4dn+W1dn+h16': {
        'direction': 'short',
        'cond': lambda r: r['h4_dn'] and r['w1_dn'] and r['hour']==16 and r['dow']!=4
    },
    'S3: B50+W1dn+h16+Bear': {
        'direction': 'short',
        'cond': lambda r: not r['h4_abv'] and r['w1_dn'] and r['hour']==16 and r['bear'] and r['dow']!=4
    },
}

def run_strategy(name, direction, cond_fn):
    trades = []
    traded_today = {}  # date -> count

    for i in range(300, len(df)-MAX_BARS-1):
        row = df.iloc[i]
        atr = row['atr']
        if atr <= 0 or pd.isna(atr):
            continue

        date = df.index[i].date()
        if traded_today.get((date, direction), 0) >= 1:
            continue  # 1 trade per direction per day

        try:
            if not cond_fn(row):
                continue
        except:
            continue

        pnl, reason = sim(df, i, direction, atr)
        yr  = df.index[i].year
        mo  = df.index[i].to_period('M')
        hr  = row['hour']

        trades.append({
            'ts': df.index[i], 'date': date, 'year': yr, 'month': mo,
            'hour': hr, 'direction': direction,
            'pnl': pnl, 'win': pnl > 0, 'reason': reason, 'atr': atr,
        })
        traded_today[(date, direction)] = traded_today.get((date, direction), 0) + 1

    return pd.DataFrame(trades)

# ── Run each strategy ──────────────────────────────────────────────────────
results = {}
for name, cfg in STRATEGIES.items():
    T = run_strategy(name, cfg['direction'], cfg['cond'])
    results[name] = T

print(f"\n{'='*65}")
print(f"  INDIVIDUAL STRATEGY RESULTS")
print(f"{'='*65}")
print(f"  {'Strategy':<36} {'N':>5} {'T/mo':>5} {'WR':>6} {'PnL':>9} {'DD':>7}")
print(f"  {'-'*65}")

for name, T in results.items():
    if len(T) == 0:
        print(f"  {name:<36}     0  ---    ---       ---     ---")
        continue
    n   = len(T)
    wr  = T['win'].mean()
    pnl = T['pnl'].sum()
    bal = START_BAL + T['pnl'].cumsum()
    dd  = ((bal.cummax()-bal)/bal.cummax()*100).max()
    tpm = n / 77
    print(f"  {name:<36} {n:>5} {tpm:>5.1f} {wr:>6.1%} ${pnl:>8,.0f} {dd:>6.1f}%")

# ── Best Combined: L1 + S1 ─────────────────────────────────────────────────
print(f"\n{'='*65}")
print(f"  COMBINED: L1 (D1str+h15+U3h) + S1 (H4dn+W1dn+h16+Bear)")
print(f"{'='*65}")

TL = results['L1: D1str+h15+U3h']
TS = results['S1: H4dn+W1dn+h16+Bear']

if len(TL) > 0 and len(TS) > 0:
    TC = pd.concat([TL, TS]).sort_values('ts').reset_index(drop=True)
    n   = len(TC)
    wr  = TC['win'].mean()
    pnl = TC['pnl'].sum()
    bal = START_BAL + TC['pnl'].cumsum()
    dd  = ((bal.cummax()-bal)/bal.cummax()*100).max()
    mb  = bal.min()

    outcomes = TC['win'].astype(int).values
    max_cl = cur_cl = 0
    for o in outcomes:
        cur_cl = cur_cl+1 if o==0 else 0
        max_cl = max(max_cl, cur_cl)

    print(f"  Trades: {n}  |  WR: {wr:.1%}  |  PnL: ${pnl:,.0f}")
    print(f"  MaxDD: {dd:.2f}%  |  Min Balance: ${mb:,.0f}")
    print(f"  Max consecutive losses: {max_cl}")
    print(f"  Floor $9,000 breached? {'YES !!!' if mb < FLOOR else 'NO - safe'}")

    yearly = TC.groupby('year').agg(n=('pnl','count'),
        wr=('win','mean'), pnl=('pnl','sum'))
    all_pos = True
    print(f"\n  Year-by-year:")
    for yr, r in yearly.iterrows():
        sign = '+' if r['pnl']>=0 else '-'
        if r['pnl']<0: all_pos = False
        bar = '#'*int(abs(r['pnl'])/300)
        print(f"    {yr}: N={r['n']:3.0f}  WR={r['wr']:.1%}  {sign}${abs(r['pnl']):,.0f}  {bar}")
    print(f"  All years positive? {'YES' if all_pos else 'NO'}")

    print(f"\n  Out-of-sample 2024-2026:")
    oos = TC[TC['year']>=2024]
    if len(oos):
        wr_o = oos['win'].mean()
        bal_o = START_BAL + oos['pnl'].cumsum()
        dd_o  = ((bal_o.cummax()-bal_o)/bal_o.cummax()*100).max()
        print(f"    N={len(oos)}  WR={wr_o:.1%}  PnL=${oos['pnl'].sum():,.0f}  MaxDD={dd_o:.2f}%")
        for yr in [2024,2025,2026]:
            s = oos[oos['year']==yr]
            if len(s): print(f"    {yr}: N={len(s)}  WR={s['win'].mean():.1%}  ${s['pnl'].sum():,.0f}")

    # Rolling start
    print(f"\n  Rolling start analysis:")
    months = sorted(TC['month'].unique())
    roll = []
    for m in months:
        sub = TC[TC['month']>=m]
        if len(sub) < 5: continue
        b = START_BAL + sub['pnl'].cumsum()
        mb2 = b.min()
        roll.append((str(m), len(sub), mb2, mb2<FLOOR))
    total  = len(roll)
    breach = sum(1 for _,_,_,b in roll if b)
    print(f"    Start months tested: {total}")
    print(f"    Breach $9,000: {breach} ({breach/total:.1%})")
    roll.sort(key=lambda x: x[2])
    print(f"    Worst 5 starts:")
    for m, nt, mb2, br in roll[:5]:
        print(f"      {m}  N={nt}  MinBal=${mb2:,.0f}  {'BREACH' if br else 'OK'}")

# ── Alternative: L1 + S2 ──────────────────────────────────────────────────
print(f"\n{'='*65}")
print(f"  COMBINED: L1 (D1str+h15+U3h) + S2 (H4dn+W1dn+h16, no Bear)")
print(f"{'='*65}")

TL = results['L1: D1str+h15+U3h']
TS2 = results['S2: H4dn+W1dn+h16']

if len(TL) > 0 and len(TS2) > 0:
    TC2 = pd.concat([TL, TS2]).sort_values('ts').reset_index(drop=True)
    n   = len(TC2)
    wr  = TC2['win'].mean()
    pnl = TC2['pnl'].sum()
    bal = START_BAL + TC2['pnl'].cumsum()
    dd  = ((bal.cummax()-bal)/bal.cummax()*100).max()
    mb  = bal.min()

    outcomes = TC2['win'].astype(int).values
    max_cl = cur_cl = 0
    for o in outcomes:
        cur_cl = cur_cl+1 if o==0 else 0
        max_cl = max(max_cl, cur_cl)

    print(f"  Trades: {n}  |  WR: {wr:.1%}  |  PnL: ${pnl:,.0f}")
    print(f"  MaxDD: {dd:.2f}%  |  Min Balance: ${mb:,.0f}")
    print(f"  Max consecutive losses: {max_cl}")
    print(f"  Floor $9,000 breached? {'YES !!!' if mb < FLOOR else 'NO - safe'}")

    yearly = TC2.groupby('year').agg(n=('pnl','count'),
        wr=('win','mean'), pnl=('pnl','sum'))
    all_pos = True
    print(f"\n  Year-by-year:")
    for yr, r in yearly.iterrows():
        sign = '+' if r['pnl']>=0 else '-'
        if r['pnl']<0: all_pos = False
        bar = '#'*int(abs(r['pnl'])/300)
        print(f"    {yr}: N={r['n']:3.0f}  WR={r['wr']:.1%}  {sign}${abs(r['pnl']):,.0f}  {bar}")
    print(f"  All years positive? {'YES' if all_pos else 'NO'}")

    # Rolling
    months = sorted(TC2['month'].unique())
    roll = []
    for m in months:
        sub = TC2[TC2['month']>=m]
        if len(sub) < 5: continue
        b = START_BAL + sub['pnl'].cumsum()
        mb2 = b.min()
        roll.append((str(m), len(sub), mb2, mb2<FLOOR))
    total  = len(roll)
    breach = sum(1 for _,_,_,b in roll if b)
    print(f"\n  Rolling start: {breach}/{total} breach ({breach/total:.1%})")

# ── Best combined variant ──────────────────────────────────────────────────
print(f"\n{'='*65}")
print(f"  COMBINED: L4 (D1str+h15 OR A50+W1up+h16) + S1 (H4dn+W1dn+h16+Bear)")
print(f"{'='*65}")

TL4 = results['L4: D1str+h15+U3h OR A50+W1up+h16']
TS  = results['S1: H4dn+W1dn+h16+Bear']

if len(TL4)>0 and len(TS)>0:
    TC4 = pd.concat([TL4, TS]).sort_values('ts').reset_index(drop=True)
    n   = len(TC4)
    wr  = TC4['win'].mean()
    pnl = TC4['pnl'].sum()
    bal = START_BAL + TC4['pnl'].cumsum()
    dd  = ((bal.cummax()-bal)/bal.cummax()*100).max()
    mb  = bal.min()

    outcomes = TC4['win'].astype(int).values
    max_cl = cur_cl = 0
    for o in outcomes:
        cur_cl = cur_cl+1 if o==0 else 0
        max_cl = max(max_cl, cur_cl)

    print(f"  Trades: {n}  |  WR: {wr:.1%}  |  PnL: ${pnl:,.0f}")
    print(f"  MaxDD: {dd:.2f}%  |  Min Balance: ${mb:,.0f}")
    print(f"  Max consecutive losses: {max_cl}")
    print(f"  Floor $9,000 breached? {'YES !!!' if mb < FLOOR else 'NO - safe'}")

    yearly = TC4.groupby('year').agg(n=('pnl','count'),
        wr=('win','mean'), pnl=('pnl','sum'))
    all_pos = True
    print(f"\n  Year-by-year:")
    for yr, r in yearly.iterrows():
        sign = '+' if r['pnl']>=0 else '-'
        if r['pnl']<0: all_pos = False
        bar = '#'*int(abs(r['pnl'])/300)
        print(f"    {yr}: N={r['n']:3.0f}  WR={r['wr']:.1%}  {sign}${abs(r['pnl']):,.0f}  {bar}")
    print(f"  All years positive? {'YES' if all_pos else 'NO'}")

    months = sorted(TC4['month'].unique())
    roll = []
    for m in months:
        sub = TC4[TC4['month']>=m]
        if len(sub) < 5: continue
        b = START_BAL + sub['pnl'].cumsum()
        mb2 = b.min()
        roll.append((str(m), len(sub), mb2, mb2<FLOOR))
    total  = len(roll)
    breach = sum(1 for _,_,_,b in roll if b)
    print(f"\n  Rolling start: {breach}/{total} breach ({breach/total:.1%})")
    roll.sort(key=lambda x: x[2])
    for m, nt, mb2, br in roll[:5]:
        print(f"    {m}  N={nt}  MinBal=${mb2:,.0f}  {'BREACH' if br else 'OK'}")

print("\nDone.")
