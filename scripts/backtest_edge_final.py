# -*- coding: utf-8 -*-
"""
Final backtest: best edge combos found by find_edge_v2
Tests L3+S1, L3+S3, and more variants
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
df['up6h']   = df['close'] > df['close'].shift(24)
df['dn6h']   = df['close'] < df['close'].shift(24)

df['atr_avg']= df['atr'].rolling(288).mean()
df['atr_hi'] = df['atr'] > 1.3 * df['atr_avg']

print(f"  {len(df)} bars loaded.")

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

def run(cond_fn, direction, max_per_day=1):
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
        pnl, reason = sim(df, i, direction, atr)
        yr  = df.index[i].year
        mo  = df.index[i].to_period('M')
        trades.append({
            'ts': df.index[i], 'date': date, 'year': yr, 'month': mo,
            'hour': row['hour'], 'direction': direction,
            'pnl': pnl, 'win': pnl > 0, 'reason': reason,
        })
        traded[(date, direction)] = traded.get((date, direction), 0) + 1
    return pd.DataFrame(trades)

def stats(T):
    if len(T) == 0:
        return "  No trades."
    n   = len(T)
    wr  = T['win'].mean()
    pnl = T['pnl'].sum()
    aw  = T[T['win']]['pnl'].mean() if T['win'].any() else 0
    al  = T[~T['win']]['pnl'].mean() if (~T['win']).any() else 0
    bal = START_BAL + T['pnl'].cumsum()
    dd  = ((bal.cummax()-bal)/bal.cummax()*100).max()
    mb  = bal.min()
    outcomes = T['win'].astype(int).values
    max_cl = cur_cl = 0
    for o in outcomes:
        cur_cl = cur_cl+1 if o==0 else 0
        max_cl = max(max_cl, cur_cl)

    lines = [
        f"  Trades: {n} ({n/77:.1f}/mo)  |  WR: {wr:.1%}  |  PnL: ${pnl:,.0f}",
        f"  MaxDD: {dd:.2f}%  |  MinBal: ${mb:,.0f}  |  Floor breach: {'YES !!!' if mb<FLOOR else 'NO'}",
        f"  Avg Win: ${aw:.0f}  |  Avg Loss: ${al:.0f}  |  Max consec losses: {max_cl}",
    ]

    # Year-by-year
    yearly = T.groupby('year').agg(n=('pnl','count'), wr=('win','mean'), pnl=('pnl','sum'))
    all_pos = True
    lines.append(f"\n  Year-by-year:")
    for yr, r in yearly.iterrows():
        sign = '+' if r['pnl']>=0 else '-'
        if r['pnl']<0: all_pos = False
        bar = '#'*max(0, int(abs(r['pnl'])/300))
        lines.append(f"    {yr}: N={r['n']:3.0f}  WR={r['wr']:.1%}  {sign}${abs(r['pnl']):,.0f}  {bar}")
    lines.append(f"  All years positive? {'YES' if all_pos else 'NO'}")

    # OOS
    oos = T[T['year']>=2024]
    if len(oos):
        bal_o = START_BAL + oos['pnl'].cumsum()
        dd_o  = ((bal_o.cummax()-bal_o)/bal_o.cummax()*100).max()
        lines.append(f"\n  OOS 2024-2026: N={len(oos)}  WR={oos['win'].mean():.1%}  "
                     f"PnL=${oos['pnl'].sum():,.0f}  MaxDD={dd_o:.2f}%")
        for yr in [2024,2025,2026]:
            s = oos[oos['year']==yr]
            if len(s): lines.append(f"    {yr}: N={len(s)}  WR={s['win'].mean():.1%}  ${s['pnl'].sum():,.0f}")

    # Rolling
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
        lines.append(f"\n  Rolling start: {breach}/{total} breach ({breach/total:.1%})")
        roll.sort(key=lambda x: x[2])
        for m,nt,mb2,br in roll[:5]:
            lines.append(f"    {m}  N={nt}  MinBal=${mb2:,.0f}  {'BREACH' if br else 'OK'}")

    return '\n'.join(lines)

# ── Run strategies ─────────────────────────────────────────────────────────

# LONG candidates
L3 = run(lambda r: r['h4_up'] and r['w1_up'] and r['hour']==5 and r['dow']!=4, 'long')
L3b= run(lambda r: r['h4_up'] and r['w1_up'] and r['hour']==5 and r['bull'] and r['dow']!=4, 'long')
L3c= run(lambda r: r['h4_up'] and r['w1_up'] and r['hour']==5 and r['up3h'] and r['dow']!=4, 'long')
L3d= run(lambda r: r['h4_up'] and r['w1_up'] and r['hour'] in [5,6] and r['dow']!=4, 'long')
L2 = run(lambda r: r['h4_abv'] and r['w1_up'] and r['hour']==16 and r['atr_hi'] and r['dow']!=4, 'long')
L5 = run(lambda r: r['h4_up'] and r['w1_up'] and r['hour'] in [5,6,7] and r['bull'] and r['dow']!=4, 'long')

# SHORT candidates
S1 = run(lambda r: r['h4_dn'] and r['w1_dn'] and r['hour']==16 and r['bear'] and r['dow']!=4, 'short')
S1b= run(lambda r: r['h4_dn'] and r['w1_dn'] and r['hour'] in [15,16] and r['bear'] and r['dow']!=4, 'short')
S3 = run(lambda r: not r['h4_abv'] and r['w1_dn'] and r['hour']==16 and r['bear'] and r['dow']!=4, 'short')
S4 = run(lambda r: r['h4_dn'] and r['w1_dn'] and r['hour']==16 and r['dn6h'] and r['dow']!=4, 'short')
S5 = run(lambda r: r['h4_dn'] and r['w1_dn'] and r['hour'] in [15,16,17] and r['bear'] and r['dow']!=4, 'short')

print(f"\n{'='*65}")
print(f"  INDIVIDUAL RESULTS")
print(f"{'='*65}")
for name, T in [
    ('L3:  H4up+W1up+h05',          L3),
    ('L3b: H4up+W1up+h05+Bull',     L3b),
    ('L3c: H4up+W1up+h05+Up3h',     L3c),
    ('L3d: H4up+W1up+h05-6',        L3d),
    ('L2:  A50+W1up+h16+Vhi',       L2),
    ('L5:  H4up+W1up+h5-7+Bull',    L5),
    ('S1:  H4dn+W1dn+h16+Bear',     S1),
    ('S1b: H4dn+W1dn+h15-16+Bear',  S1b),
    ('S3:  B50+W1dn+h16+Bear',      S3),
    ('S4:  H4dn+W1dn+h16+Dn6h',     S4),
    ('S5:  H4dn+W1dn+h15-17+Bear',  S5),
]:
    if len(T)==0:
        print(f"  {name}: 0 trades")
        continue
    n   = len(T)
    wr  = T['win'].mean()
    pnl = T['pnl'].sum()
    bal = START_BAL+T['pnl'].cumsum()
    dd  = ((bal.cummax()-bal)/bal.cummax()*100).max()
    mb  = bal.min()
    print(f"  {name:<28} N={n:3d} WR={wr:.1%} PnL=${pnl:>7,.0f} DD={dd:5.1f}% {'BREACH' if mb<FLOOR else 'ok'}")

# ── Best combined: L3 + S1 ─────────────────────────────────────────────────
def print_combo(name, TL, TS):
    print(f"\n{'='*65}")
    print(f"  {name}")
    print(f"{'='*65}")
    if len(TL)==0 or len(TS)==0:
        print("  Not enough trades.")
        return
    TC = pd.concat([TL, TS]).sort_values('ts').reset_index(drop=True)
    print(stats(TC))

print_combo("COMBINED: L3 (H4up+W1up+h05) + S1 (H4dn+W1dn+h16+Bear)", L3, S1)
print_combo("COMBINED: L3 (H4up+W1up+h05) + S3 (B50+W1dn+h16+Bear)", L3, S3)
print_combo("COMBINED: L3b (H4up+W1up+h05+Bull) + S1 (H4dn+W1dn+h16+Bear)", L3b, S1)
print_combo("COMBINED: L3 + S1b (H4dn+W1dn+h15-16+Bear)", L3, S1b)
print_combo("COMBINED: L3d (h05-6) + S1", L3d, S1)
print_combo("COMBINED: L5 (H4up+W1up+h5-7+Bull) + S1", L5, S1)

print("\n\nDone.")
