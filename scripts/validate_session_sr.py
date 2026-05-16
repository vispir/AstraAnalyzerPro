# -*- coding: utf-8 -*-
"""
Session S/R Bounce Strategy
Levels: Previous Session High/Low + Previous Day High/Low
Entry:  Pin bar bounce from level
Filter: H4 EMA trend direction
TP=2.5R, SL=1ATR, trailing from 1.2R
"""
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

import pandas as pd
import numpy as np
import warnings
warnings.filterwarnings('ignore')

M15_FILE  = r"D:\Works\ASTRA ANALYZER CHART\data_cache\dukascopy\m15\XAUUSD\xauusd_m15_2020-01-01_2026-05-12.parquet"
ATR_PER   = 14
RISK_USD  = 100.0
START_BAL = 10000.0
FLOOR     = 9000.0

SESSIONS = {'asian':(3,6), 'london':(8,11), 'ny':(15,18)}

# ── Load ──────────────────────────────────────────────────────────────────
print("Loading data...")
df = pd.read_parquet(M15_FILE)
df.index = pd.to_datetime(df.index, utc=True)
df = df.sort_index()
df.columns = [c.lower() for c in df.columns]

df['tr']  = np.maximum(df['high']-df['low'],
            np.maximum(abs(df['high']-df['close'].shift(1)),
                       abs(df['low'] -df['close'].shift(1))))
df['atr'] = df['tr'].ewm(alpha=1/ATR_PER, adjust=False).mean()

# H4 trend
h4 = df.resample('4h', origin='epoch').agg(
    open=('open','first'), high=('high','max'),
    low=('low','min'),  close=('close','last')).dropna()
h4['ema20'] = h4['close'].ewm(span=20, adjust=False).mean()
h4['slope'] = h4['ema20'] - h4['ema20'].shift(3)
h4['h4_up'] = (h4['close'] > h4['ema20']) & (h4['slope'] > 0)
h4['h4_down']=(h4['close'] < h4['ema20']) & (h4['slope'] < 0)

# Daily trend
d1 = df.resample('1D', origin='epoch').agg(close=('close','last')).dropna()
d1['d1_ema20'] = d1['close'].ewm(span=20, adjust=False).mean()
d1['d1_up']    = d1['close'] > d1['d1_ema20']

df['h4_up']  = h4['h4_up'].shift(1).reindex(df.index, method='ffill')
df['h4_down']= h4['h4_down'].shift(1).reindex(df.index, method='ffill')
df['d1_up']  = d1['d1_up'].shift(1).reindex(df.index, method='ffill')
df['dow']    = df.index.dayofweek
df['hour']   = df.index.hour

print(f"  {len(df)} bars ready")

# ── Build session high/low table (no lookahead) ────────────────────────────
print("Building session levels...")

# For each date and session, compute HIGH/LOW of that session
# Then shift forward so next session can use it
session_levels = []
dates = sorted(set(df.index.normalize()))

for date in dates:
    for sess, (h_s, h_e) in SESSIONS.items():
        mask = (df.index.date == date.date()) & \
               (df.index.hour >= h_s) & (df.index.hour < h_e)
        bars = df[mask]
        if len(bars) < 3:
            continue
        session_levels.append({
            'date':    date.date(),
            'session': sess,
            'high':    bars['high'].max(),
            'low':     bars['low'].min(),
            'end_time':bars.index[-1],
        })

slev = pd.DataFrame(session_levels)

# Previous day high/low
daily_hl = df.resample('1D', origin='epoch').agg(
    high=('high','max'), low=('low','min')).dropna()
daily_hl = daily_hl.shift(1)  # previous day

# ── Trade simulator ────────────────────────────────────────────────────────
def sim_trade(df, idx, direction, sl_dist, tp_rr=2.5,
              trail_r=1.2, trail_step=0.4, max_bars=350):
    entry      = df['close'].iloc[idx]
    sl         = entry - sl_dist if direction=='long' else entry + sl_dist
    tp         = entry + tp_rr*sl_dist if direction=='long' else entry - tp_rr*sl_dist
    current_sl = sl
    best_r     = 0.0

    for _, bar in df.iloc[idx+1:idx+1+max_bars].iterrows():
        if direction == 'long':
            if bar['low']  <= current_sl: return (current_sl-entry)/sl_dist*RISK_USD, 'sl'
            if bar['high'] >= tp:          return tp_rr*RISK_USD, 'tp'
            r = (bar['high']-entry)/sl_dist
            if r > best_r: best_r = r
            if best_r >= trail_r:
                ns = entry + (best_r-trail_step)*sl_dist
                if ns > current_sl: current_sl = ns
        else:
            if bar['high'] >= current_sl: return (entry-current_sl)/sl_dist*RISK_USD, 'sl'
            if bar['low']  <= tp:          return tp_rr*RISK_USD, 'tp'
            r = (entry-bar['low'])/sl_dist
            if r > best_r: best_r = r
            if best_r >= trail_r:
                ns = entry - (best_r-trail_step)*sl_dist
                if ns < current_sl: current_sl = ns

    r = (current_sl-entry)/sl_dist if direction=='long' else (entry-current_sl)/sl_dist
    return r*RISK_USD, 'timeout'

# ── Main backtest ──────────────────────────────────────────────────────────
print("Running backtest...")
trades = []

for i in range(50, len(df)-350):
    row  = df.iloc[i]
    dow  = row['dow']
    if dow == 4: continue  # skip Friday

    atr      = row['atr']
    cur_time = df.index[i]
    date     = cur_time.date()

    if atr <= 0 or pd.isna(atr): continue

    # Collect S/R levels available right now (all confirmed before cur_time)
    levels_long  = []  # support levels for long
    levels_short = []  # resistance levels for short

    # 1. Previous session highs/lows (strictly before current bar)
    past_sess = slev[slev['end_time'] < cur_time]
    if len(past_sess) > 0:
        # Last 4 sessions (2 days roughly)
        recent = past_sess.tail(4)
        for _, sr in recent.iterrows():
            levels_long.append(sr['low'])    # prev session low = support
            levels_short.append(sr['high'])   # prev session high = resistance

    # 2. Previous day high/low
    prev_day = daily_hl[daily_hl.index.date <= date]
    if len(prev_day) > 0:
        pd_row = prev_day.iloc[-1]
        if not pd.isna(pd_row['high']): levels_short.append(pd_row['high'])
        if not pd.isna(pd_row['low']):  levels_long.append(pd_row['low'])

    if not levels_long and not levels_short:
        continue

    # Pin bar detection
    body   = abs(row['close'] - row['open'])
    hi_wick = row['high'] - max(row['open'], row['close'])
    lo_wick = min(row['open'], row['close']) - row['low']
    total  = row['high'] - row['low']
    if total <= 0: continue

    already_today = [t for t in trades if t['date'] == date]
    long_today  = sum(1 for t in already_today if t['direction']=='long')
    short_today = sum(1 for t in already_today if t['direction']=='short')

    # ── LONG: bullish pin at support ─────────────────────────────────────
    if long_today < 2 and (row['h4_up'] or row['d1_up']):
        # Bullish pin: long lower wick, small body, price closed above low
        if (lo_wick > 1.8 * body and body > 0 and
            lo_wick > 0.4 * total and
            row['close'] > row['open']):  # bullish close

            # Check if any support level is near the wick
            for level in levels_long:
                dist = abs(row['low'] - level)
                if dist < 0.4 * atr and row['close'] > level:
                    pnl, reason = sim_trade(df, i, 'long', atr)
                    trades.append({
                        'date': date, 'direction': 'long',
                        'pnl': pnl, 'outcome': 'win' if pnl>0 else 'loss',
                        'reason': reason, 'hour': row['hour'], 'dow': dow,
                        'year': cur_time.year, 'month': cur_time.to_period('M'),
                        'level': level, 'atr': atr,
                    })
                    break

    # ── SHORT: bearish pin at resistance ─────────────────────────────────
    if short_today < 2 and (row['h4_down'] or not row['d1_up']):
        # Bearish pin: long upper wick, small body, price closed below high
        if (hi_wick > 1.8 * body and body > 0 and
            hi_wick > 0.4 * total and
            row['close'] < row['open']):  # bearish close

            for level in levels_short:
                dist = abs(row['high'] - level)
                if dist < 0.4 * atr and row['close'] < level:
                    pnl, reason = sim_trade(df, i, 'short', atr)
                    trades.append({
                        'date': date, 'direction': 'short',
                        'pnl': pnl, 'outcome': 'win' if pnl>0 else 'loss',
                        'reason': reason, 'hour': row['hour'], 'dow': dow,
                        'year': cur_time.year, 'month': cur_time.to_period('M'),
                        'level': level, 'atr': atr,
                    })
                    break

T = pd.DataFrame(trades)
print(f"  Total trades: {len(T)}")

# ── Stats ──────────────────────────────────────────────────────────────────
n   = len(T)
wr  = (T['outcome']=='win').mean()
pnl = T['pnl'].sum()
aw  = T[T['outcome']=='win']['pnl'].mean()
al  = T[T['outcome']=='loss']['pnl'].mean()
pf  = abs(aw*wr / (al*(1-wr))) if al and (1-wr)>0 else 0

balance = START_BAL + T['pnl'].cumsum()
peak    = balance.cummax()
dd      = ((peak-balance)/peak*100)
max_dd  = dd.max()
min_bal = balance.min()

# Consecutive losses
outcomes = (T['outcome']=='win').astype(int).values
max_cl = cur_cl = 0
for o in outcomes:
    cur_cl = cur_cl+1 if o==0 else 0
    max_cl = max(max_cl, cur_cl)

print(f"\n{'='*58}")
print(f"  SESSION S/R BOUNCE - NO LOOKAHEAD")
print(f"{'='*58}")
print(f"  Trades : {n}  |  WR: {wr:.1%}  |  PF: {pf:.2f}")
print(f"  PnL    : ${pnl:,.0f}")
print(f"  MaxDD  : {max_dd:.2f}%  |  Min Balance: ${min_bal:,.0f}")
print(f"  Avg Win: ${aw:.0f}  |  Avg Loss: ${al:.0f}")
print(f"  Max consecutive losses: {max_cl}")
print(f"  Floor $9,000 breached?  {'YES !!!' if min_bal < FLOOR else 'NO - safe'}")

for d in ['long','short']:
    s = T[T['direction']==d]
    if len(s)==0: continue
    w = (s['outcome']=='win').mean(); p = s['pnl'].sum()
    print(f"  {d:6s}: N={len(s):4d}  WR={w:.1%}  PnL=${p:,.0f}")

print(f"\n  Year-by-year:")
yearly = T.groupby('year').agg(n=('pnl','count'),
    wr=('outcome',lambda x:(x=='win').mean()), pnl=('pnl','sum'))
all_positive = True
for yr, r in yearly.iterrows():
    sign = '+' if r['pnl']>=0 else '-'
    if r['pnl'] < 0: all_positive = False
    bar = '#'*int(abs(r['pnl'])/300)
    print(f"    {yr}: N={r['n']:3.0f}  WR={r['wr']:.1%}  {sign}${abs(r['pnl']):,.0f}  {bar}")
print(f"  All years positive? {'YES' if all_positive else 'NO'}")

# ── Out-of-sample ─────────────────────────────────────────────────────────
print(f"\n  Out-of-sample 2024-2026:")
oos = T[T['year']>=2024]
if len(oos):
    wr_o = (oos['outcome']=='win').mean()
    dd_o = ((START_BAL+oos['pnl'].cumsum()).cummax()-(START_BAL+oos['pnl'].cumsum()))
    dd_o = (dd_o/(START_BAL+oos['pnl'].cumsum()).cummax()*100).max()
    print(f"    N={len(oos)}  WR={wr_o:.1%}  PnL=${oos['pnl'].sum():,.0f}  MaxDD={dd_o:.2f}%")
    for yr in [2024,2025,2026]:
        s = oos[oos['year']==yr]
        if len(s): print(f"    {yr}: N={len(s):3d}  WR={(s['outcome']=='win').mean():.1%}  ${s['pnl'].sum():,.0f}")

# ── Rolling start ─────────────────────────────────────────────────────────
print(f"\n{'='*58}")
print(f"  ROLLING START (each month, $10k start, $9k floor)")
print(f"{'='*58}")
months = sorted(T['month'].unique())
roll_res = []
for m in months:
    sub = T[T['month']>=m]
    if len(sub) < 5: continue
    bal = START_BAL + sub['pnl'].cumsum()
    mb  = bal.min()
    roll_res.append((str(m), len(sub), mb, mb < FLOOR))

total  = len(roll_res)
breach = sum(1 for _,_,_,b in roll_res if b)
print(f"  Start months tested : {total}")
print(f"  Breach $9,000       : {breach} ({breach/total:.1%})")

roll_res.sort(key=lambda x: x[2])
print(f"\n  Worst 5 starts:")
print(f"  {'Month':<12} {'N':>5} {'Min Bal':>12} {'Safe?':>8}")
print(f"  {'-'*42}")
for m, n_, mb, br in roll_res[:5]:
    print(f"  {m:<12} {n_:>5} ${mb:>11,.0f}  {'BREACH' if br else 'OK':>8}")

# ── Best hours ────────────────────────────────────────────────────────────
print(f"\n  Best hours (combined L+S, min 5 trades):")
hourly = T.groupby('hour').agg(n=('pnl','count'),
    wr=('outcome',lambda x:(x=='win').mean()), pnl=('pnl','sum'))
good = hourly[hourly['n']>=5].sort_values('pnl',ascending=False).head(8)
for h, r in good.iterrows():
    bar = '#'*int(r['wr']*20)
    print(f"    {h:02d}:00  N={r['n']:3.0f}  WR={r['wr']:.1%}  ${r['pnl']:,.0f}  {bar}")

# Save
T.to_csv(r"D:\Works\ASTRA ANALYZER CHART\scripts\session_sr_bounce_validated.csv", index=False)
print(f"\nSaved. Done.")
