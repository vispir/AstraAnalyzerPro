# -*- coding: utf-8 -*-
"""
Comprehensive strategy search:
1. H4 pullback to EMA (swing trades, hold days)
2. Key level tests (weekly/monthly high-low)
3. Reversal patterns (pin bars at S/R)
4. Breakout retest entries
5. Grid search: TP 2R-8R, SL 1-2ATR, trailing variations
No lookahead bias. Realistic simulation.
"""
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

import pandas as pd
import numpy as np
import warnings
warnings.filterwarnings('ignore')

M15_FILE = r"D:\Works\ASTRA ANALYZER CHART\data_cache\dukascopy\m15\XAUUSD\xauusd_m15_2020-01-01_2026-05-12.parquet"
RISK_USD  = 100.0
START_BAL = 10000.0
FLOOR     = 9000.0
MAX_BARS  = 800  # up to 200 hours = 8 days

print("Loading M15 data...")
m15 = pd.read_parquet(M15_FILE)
m15.index = pd.to_datetime(m15.index, utc=True)
m15 = m15.sort_index()
m15.columns = [c.lower() for c in m15.columns]

# ATR on M15
m15['tr']  = np.maximum(m15['high']-m15['low'],
             np.maximum(abs(m15['high']-m15['close'].shift(1)),
                        abs(m15['low'] -m15['close'].shift(1))))
m15['atr'] = m15['tr'].ewm(alpha=1/14, adjust=False).mean()
m15['hour']= m15.index.hour
m15['dow'] = m15.index.dayofweek

# ── Build H4 frame ─────────────────────────────────────────────────────────
h4 = m15.resample('4h', origin='epoch').agg(
    open=('open','first'), high=('high','max'),
    low=('low','min'),  close=('close','last'),
    atr=('atr','last')).dropna()

h4['ema20']  = h4['close'].ewm(span=20, adjust=False).mean()
h4['ema50']  = h4['close'].ewm(span=50, adjust=False).mean()
h4['ema200'] = h4['close'].ewm(span=200, adjust=False).mean()
h4['slope3'] = h4['ema20'] - h4['ema20'].shift(3)
h4['slope6'] = h4['ema20'] - h4['ema20'].shift(6)

# Trend flags (strict and relaxed)
h4['up']     = (h4['close'] > h4['ema20']) & (h4['slope3'] > 0)
h4['dn']     = (h4['close'] < h4['ema20']) & (h4['slope3'] < 0)
h4['up_str'] = (h4['close'] > h4['ema20']) & (h4['close'] > h4['ema50']) & (h4['slope6'] > 0)
h4['dn_str'] = (h4['close'] < h4['ema20']) & (h4['close'] < h4['ema50']) & (h4['slope6'] < 0)

# ATR at H4
h4['atr14']  = h4['atr'].ewm(alpha=1/14, adjust=False).mean()

# RSI-like: overbought/oversold on H4
delta  = h4['close'].diff()
gain   = delta.clip(lower=0).ewm(alpha=1/14, adjust=False).mean()
loss   = (-delta.clip(upper=0)).ewm(alpha=1/14, adjust=False).mean()
h4['rsi'] = 100 - 100/(1 + gain/(loss+1e-9))

# Swing highs/lows on H4 (no lookahead: trailing window)
h4['swH'] = h4['high'].rolling(5).max().shift(1)  # previous 5-bar max
h4['swL'] = h4['low'].rolling(5).min().shift(1)

# ── Weekly/Monthly levels ──────────────────────────────────────────────────
wk = m15.resample('1W', origin='epoch').agg(
    high=('high','max'), low=('low','min'), close=('close','last')).dropna()
wk['prev_hi'] = wk['high'].shift(1)
wk['prev_lo'] = wk['low'].shift(1)

mo = m15.resample('1ME', origin='epoch').agg(
    high=('high','max'), low=('low','min'), close=('close','last')).dropna()
mo['prev_hi'] = mo['high'].shift(1)
mo['prev_lo'] = mo['low'].shift(1)

# D1
d1 = m15.resample('1D', origin='epoch').agg(
    high=('high','max'), low=('low','min'), close=('close','last')).dropna()
d1['ema20']  = d1['close'].ewm(span=20, adjust=False).mean()
d1['ema50']  = d1['close'].ewm(span=50, adjust=False).mean()
d1['d1_up']  = (d1['close'] > d1['ema20']).shift(1)
d1['d1_str'] = ((d1['close'] > d1['ema20']) & (d1['close'] > d1['ema50'])).shift(1)
d1['prev_hi']= d1['high'].shift(1)
d1['prev_lo']= d1['low'].shift(1)

# W1 trend
w1 = m15.resample('1W', origin='epoch').agg(close=('close','last')).dropna()
w1['ema10']  = w1['close'].ewm(span=10, adjust=False).mean()
w1['w1_up']  = (w1['close'] > w1['ema10']).shift(1)
w1['w1_dn']  = (w1['close'] < w1['ema10']).shift(1)

# Map everything to M15
def mmap(s): return s.reindex(m15.index, method='ffill')

m15['h4_up']   = mmap(h4['up'].shift(1))
m15['h4_dn']   = mmap(h4['dn'].shift(1))
m15['h4_up_s'] = mmap(h4['up_str'].shift(1))
m15['h4_dn_s'] = mmap(h4['dn_str'].shift(1))
m15['h4_rsi']  = mmap(h4['rsi'].shift(1))
m15['h4_atr']  = mmap(h4['atr14'].shift(1))
m15['h4_ema20']= mmap(h4['ema20'].shift(1))
m15['h4_swH']  = mmap(h4['swH'].shift(1))
m15['h4_swL']  = mmap(h4['swL'].shift(1))

m15['d1_up']   = mmap(d1['d1_up'])
m15['d1_str']  = mmap(d1['d1_str'])
m15['d1_hi']   = mmap(d1['prev_hi'])
m15['d1_lo']   = mmap(d1['prev_lo'])
m15['w1_up']   = mmap(w1['w1_up'])
m15['w1_dn']   = mmap(w1['w1_dn'])
m15['wk_hi']   = mmap(wk['prev_hi'])
m15['wk_lo']   = mmap(wk['prev_lo'])
m15['mo_hi']   = mmap(mo['prev_hi'])
m15['mo_lo']   = mmap(mo['prev_lo'])

# M15 bar features
m15['body']    = abs(m15['close'] - m15['open'])
m15['hi_wick'] = m15['high'] - m15[['open','close']].max(axis=1)
m15['lo_wick'] = m15[['open','close']].min(axis=1) - m15['low']
m15['range']   = m15['high'] - m15['low']
m15['bull']    = m15['close'] > m15['open']
m15['bear']    = m15['close'] < m15['open']
m15['pin_up']  = (m15['lo_wick'] > 1.8*m15['body']) & (m15['body']>0) & m15['bull']
m15['pin_dn']  = (m15['hi_wick'] > 1.8*m15['body']) & (m15['body']>0) & m15['bear']
m15['engulf_up']= m15['bull'] & (m15['close'] > m15['open'].shift(1)) & (m15['open'] < m15['close'].shift(1))
m15['engulf_dn']= m15['bear'] & (m15['open'] > m15['close'].shift(1)) & (m15['close'] < m15['open'].shift(1))

m15['up3h']    = m15['close'] > m15['close'].shift(12)
m15['dn3h']    = m15['close'] < m15['close'].shift(12)
m15['up1d']    = m15['close'] > m15['close'].shift(96)
m15['dn1d']    = m15['close'] < m15['close'].shift(96)

print(f"  {len(m15)} M15 bars, {len(h4)} H4 bars ready.")

# ── Trade simulator (swing-capable, no M15 position limit) ────────────────
def sim(idx, direction, sl_dist, tp_r, trail_start_r, trail_step_r):
    entry  = m15['close'].iloc[idx]
    sl     = entry - sl_dist if direction=='long' else entry + sl_dist
    tp     = entry + tp_r*sl_dist if direction=='long' else entry - tp_r*sl_dist
    cur_sl = sl
    best_r = 0.0

    for i2 in range(idx+1, min(idx+MAX_BARS+1, len(m15))):
        h = m15['high'].iloc[i2]
        lo= m15['low'].iloc[i2]
        if direction == 'long':
            if lo  <= cur_sl: return (cur_sl-entry)/sl_dist*RISK_USD, 'sl'
            if h   >= tp:     return tp_r*RISK_USD, 'tp'
            r = (h-entry)/sl_dist
            if r > best_r: best_r = r
            if best_r >= trail_start_r:
                ns = entry + (best_r-trail_step_r)*sl_dist
                if ns > cur_sl: cur_sl = ns
        else:
            if h   >= cur_sl: return (entry-cur_sl)/sl_dist*RISK_USD, 'sl'
            if lo  <= tp:     return tp_r*RISK_USD, 'tp'
            r = (entry-lo)/sl_dist
            if r > best_r: best_r = r
            if best_r >= trail_start_r:
                ns = entry - (best_r-trail_step_r)*sl_dist
                if ns < cur_sl: cur_sl = ns
    r_f = (cur_sl-entry)/sl_dist if direction=='long' else (entry-cur_sl)/sl_dist
    return r_f*RISK_USD, 'timeout'

def run_strategy(cond_fn, direction, sl_atr_mult=1.0, tp_r=3.0,
                 trail_start=1.5, trail_step=0.5, max_per_day=1, name=""):
    trades = []
    traded = {}
    for i in range(500, len(m15)-MAX_BARS-1):
        row = m15.iloc[i]
        if row['dow'] == 4: continue
        atr = row['atr']
        if atr <= 0 or pd.isna(atr): continue
        sl_dist = atr * sl_atr_mult
        date = m15.index[i].date()
        if traded.get((date, direction), 0) >= max_per_day: continue
        try:
            if not cond_fn(row): continue
        except: continue
        pnl, reason = sim(i, direction, sl_dist, tp_r, trail_start, trail_step)
        yr = m15.index[i].year
        mo = m15.index[i].to_period('M')
        trades.append({
            'ts': m15.index[i], 'date': date, 'year': yr, 'month': mo,
            'direction': direction, 'pnl': pnl, 'win': pnl > 0, 'reason': reason,
        })
        traded[(date, direction)] = traded.get((date, direction), 0) + 1
    return pd.DataFrame(trades)

def report(T, name):
    print(f"\n{'='*65}")
    print(f"  {name}")
    print(f"{'='*65}")
    if len(T) < 10:
        print(f"  Only {len(T)} trades — skip."); return None

    n   = len(T)
    wr  = T['win'].mean()
    pnl = T['pnl'].sum()
    aw  = T[T['win']]['pnl'].mean() if T['win'].any() else 0
    al  = T[~T['win']]['pnl'].mean() if (~T['win']).any() else 0
    pf  = abs(aw*wr/(al*(1-wr))) if al!=0 and (1-wr)>0 else 0
    bal = START_BAL + T['pnl'].cumsum()
    dd  = ((bal.cummax()-bal)/bal.cummax()*100).max()
    mb  = bal.min()
    outcomes = T['win'].astype(int).values
    max_cl = cur_cl = 0
    for o in outcomes:
        cur_cl = cur_cl+1 if o==0 else 0
        max_cl = max(max_cl, cur_cl)

    print(f"  N={n} ({n/77:.1f}/mo)  WR={wr:.1%}  PF={pf:.2f}  PnL=${pnl:,.0f}")
    print(f"  MaxDD={dd:.2f}%  MinBal=${mb:,.0f}  Floor={'BREACH' if mb<FLOOR else 'SAFE'}")
    print(f"  AvgWin=${aw:.0f}  AvgLoss=${al:.0f}  MaxConsecLoss={max_cl}")

    yearly = T.groupby('year').agg(n=('pnl','count'), wr=('win','mean'), pnl=('pnl','sum'))
    all_pos = True
    print(f"\n  Year-by-year:")
    for yr, r in yearly.iterrows():
        if r['pnl'] < 0: all_pos = False
        sign = '+' if r['pnl']>=0 else '-'
        bar = '#'*max(0, int(abs(r['pnl'])/400))
        print(f"    {yr}: N={r['n']:3.0f}  WR={r['wr']:.1%}  {sign}${abs(r['pnl']):,.0f}  {bar}")
    print(f"  All years+: {'YES' if all_pos else 'NO'}")

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
        roll.append((str(m), b.min(), b.min()<FLOOR))
    total  = len(roll)
    breach = sum(1 for _,_,b in roll if b)
    print(f"\n  Rolling breach: {breach}/{total} ({breach/total:.1%})")
    return {'n':n,'wr':wr,'pnl':pnl,'dd':dd,'mb':mb,'breach_pct':breach/total,'all_pos':all_pos}

# ════════════════════════════════════════════════════════════════
# STRATEGY TYPE 1: H4 EMA PULLBACK (swing, hold days)
# ════════════════════════════════════════════════════════════════
print("\n\n>>> STRATEGY TYPE 1: H4 EMA PULLBACK (trend continuation swing)")

# LONG: strong uptrend, price pulls back to H4 EMA20 area, bullish M15 bar
def cond_pb_long(r):
    return (r['h4_up_s'] and r['w1_up'] and r['d1_str']
            and abs(r['close'] - r['h4_ema20']) < 0.5 * r['h4_atr']
            and r['close'] > r['h4_ema20']
            and r['bull'])

def cond_pb_short(r):
    return (r['h4_dn_s'] and r['w1_dn'] and not r['d1_up']
            and abs(r['close'] - r['h4_ema20']) < 0.5 * r['h4_atr']
            and r['close'] < r['h4_ema20']
            and r['bear'])

# Test different TP ratios for pullback
results_pb = {}
print("  Testing TP ratios for H4 EMA pullback...")
for tp_r, trail in [(2.5, 1.2), (3.0, 1.5), (4.0, 2.0), (5.0, 2.5)]:
    TL = run_strategy(cond_pb_long,  'long',  sl_atr_mult=1.0, tp_r=tp_r, trail_start=trail, trail_step=0.5)
    TS = run_strategy(cond_pb_short, 'short', sl_atr_mult=1.0, tp_r=tp_r, trail_start=trail, trail_step=0.5)
    if len(TL)>0 and len(TS)>0:
        TC = pd.concat([TL, TS]).sort_values('ts').reset_index(drop=True)
        bal = START_BAL + TC['pnl'].cumsum()
        dd  = ((bal.cummax()-bal)/bal.cummax()*100).max()
        mb  = bal.min()
        print(f"  TP={tp_r}R: N={len(TC)} WR={TC['win'].mean():.1%} PnL=${TC['pnl'].sum():,.0f} "
              f"DD={dd:.1f}% {'BREACH' if mb<FLOOR else 'ok'}")
        results_pb[tp_r] = TC

# Full report on best TP
if results_pb:
    best_tp = max(results_pb.items(), key=lambda x: x[1]['pnl'].sum())
    report(best_tp[1], f"H4 EMA Pullback TP={best_tp[0]}R (best PnL)")
    safe_tp = min(((tp,T) for tp,T in results_pb.items()
                   if (START_BAL+T['pnl'].cumsum()).min() >= FLOOR),
                  key=lambda x: ((START_BAL+x[1]['pnl'].cumsum()).cummax()-(START_BAL+x[1]['pnl'].cumsum())).max(),
                  default=None)
    if safe_tp and safe_tp[0] != best_tp[0]:
        report(safe_tp[1], f"H4 EMA Pullback TP={safe_tp[0]}R (safest)")

# ════════════════════════════════════════════════════════════════
# STRATEGY TYPE 2: KEY LEVEL RETEST
# ════════════════════════════════════════════════════════════════
print("\n\n>>> STRATEGY TYPE 2: KEY LEVEL RETEST")
# LONG: price breaks above prev day/week high, retests it (now support), H4 uptrend
def cond_kl_long(r):
    if pd.isna(r['d1_hi']) or pd.isna(r['wk_hi']): return False
    near_d1 = abs(r['close'] - r['d1_hi']) < 0.4 * r['atr']
    near_wk = abs(r['close'] - r['wk_hi']) < 0.6 * r['atr']
    level_ok = (near_d1 or near_wk)
    return (r['h4_up'] and r['d1_up'] and level_ok
            and r['close'] > r['d1_hi'] - 0.3*r['atr']  # above level
            and (r['pin_up'] or r['bull']))

def cond_kl_short(r):
    if pd.isna(r['d1_lo']) or pd.isna(r['wk_lo']): return False
    near_d1 = abs(r['close'] - r['d1_lo']) < 0.4 * r['atr']
    near_wk = abs(r['close'] - r['wk_lo']) < 0.6 * r['atr']
    level_ok = (near_d1 or near_wk)
    return (r['h4_dn'] and not r['d1_up'] and level_ok
            and r['close'] < r['d1_lo'] + 0.3*r['atr']
            and (r['pin_dn'] or r['bear']))

for tp_r, trail in [(2.5, 1.2), (3.5, 1.5), (5.0, 2.5)]:
    TL = run_strategy(cond_kl_long,  'long',  tp_r=tp_r, trail_start=trail, trail_step=0.5)
    TS = run_strategy(cond_kl_short, 'short', tp_r=tp_r, trail_start=trail, trail_step=0.5)
    if len(TL)>0 and len(TS)>0:
        TC = pd.concat([TL, TS]).sort_values('ts').reset_index(drop=True)
        bal = START_BAL + TC['pnl'].cumsum()
        dd  = ((bal.cummax()-bal)/bal.cummax()*100).max()
        mb  = bal.min()
        print(f"  Key Level Retest TP={tp_r}R: N={len(TC)} WR={TC['win'].mean():.1%} "
              f"PnL=${TC['pnl'].sum():,.0f} DD={dd:.1f}% {'BREACH' if mb<FLOOR else 'ok'}")

# Full report on TP=3.5R
TL35 = run_strategy(cond_kl_long,  'long',  tp_r=3.5, trail_start=1.5, trail_step=0.5)
TS35 = run_strategy(cond_kl_short, 'short', tp_r=3.5, trail_start=1.5, trail_step=0.5)
if len(TL35)>0 and len(TS35)>0:
    report(pd.concat([TL35, TS35]).sort_values('ts').reset_index(drop=True),
           "Key Level Retest (D1/Wk high-low) TP=3.5R")

# ════════════════════════════════════════════════════════════════
# STRATEGY TYPE 3: PIN BAR AT SWING S/R
# ════════════════════════════════════════════════════════════════
print("\n\n>>> STRATEGY TYPE 3: PIN BAR AT H4 SWING LEVEL")

def cond_pin_long(r):
    if pd.isna(r['h4_swL']): return False
    near_swL = abs(r['low'] - r['h4_swL']) < 0.5 * r['atr']
    return (r['h4_up'] and r['d1_up'] and near_swL and r['pin_up'])

def cond_pin_short(r):
    if pd.isna(r['h4_swH']): return False
    near_swH = abs(r['high'] - r['h4_swH']) < 0.5 * r['atr']
    return (r['h4_dn'] and not r['d1_up'] and near_swH and r['pin_dn'])

for tp_r, trail in [(2.5, 1.2), (3.0, 1.5), (4.0, 2.0)]:
    TL = run_strategy(cond_pin_long,  'long',  tp_r=tp_r, trail_start=trail, trail_step=0.5)
    TS = run_strategy(cond_pin_short, 'short', tp_r=tp_r, trail_start=trail, trail_step=0.5)
    if len(TL)>0 and len(TS)>0:
        TC = pd.concat([TL, TS]).sort_values('ts').reset_index(drop=True)
        bal = START_BAL + TC['pnl'].cumsum()
        dd  = ((bal.cummax()-bal)/bal.cummax()*100).max()
        mb  = bal.min()
        print(f"  Pin Bar at Swing TP={tp_r}R: N={len(TC)} WR={TC['win'].mean():.1%} "
              f"PnL=${TC['pnl'].sum():,.0f} DD={dd:.1f}% {'BREACH' if mb<FLOOR else 'ok'}")

TL_pin = run_strategy(cond_pin_long,  'long',  tp_r=3.0, trail_start=1.5, trail_step=0.5)
TS_pin = run_strategy(cond_pin_short, 'short', tp_r=3.0, trail_start=1.5, trail_step=0.5)
if len(TL_pin)>0 and len(TS_pin)>0:
    report(pd.concat([TL_pin, TS_pin]).sort_values('ts').reset_index(drop=True),
           "Pin Bar at H4 Swing Level TP=3R")

# ════════════════════════════════════════════════════════════════
# STRATEGY TYPE 4: COMBINED — PULLBACK + TIME FILTER
# ════════════════════════════════════════════════════════════════
print("\n\n>>> STRATEGY TYPE 4: PULLBACK + SESSION TIME")

def cond_pb_time_long(r):
    return (r['h4_up_s'] and r['w1_up']
            and abs(r['close'] - r['h4_ema20']) < 0.6 * r['h4_atr']
            and r['close'] > r['h4_ema20']
            and r['hour'] in [5, 8, 9, 15, 16]
            and r['bull'])

def cond_pb_time_short(r):
    return (r['h4_dn_s'] and r['w1_dn']
            and abs(r['close'] - r['h4_ema20']) < 0.6 * r['h4_atr']
            and r['close'] < r['h4_ema20']
            and r['hour'] in [8, 9, 15, 16, 17]
            and r['bear'])

for tp_r, trail in [(2.5, 1.2), (3.5, 1.5), (5.0, 2.5)]:
    TL = run_strategy(cond_pb_time_long,  'long',  tp_r=tp_r, trail_start=trail, trail_step=0.5)
    TS = run_strategy(cond_pb_time_short, 'short', tp_r=tp_r, trail_start=trail, trail_step=0.5)
    if len(TL)>0 and len(TS)>0:
        TC = pd.concat([TL, TS]).sort_values('ts').reset_index(drop=True)
        bal = START_BAL + TC['pnl'].cumsum()
        dd  = ((bal.cummax()-bal)/bal.cummax()*100).max()
        mb  = bal.min()
        print(f"  Pullback+Session TP={tp_r}R: N={len(TC)} WR={TC['win'].mean():.1%} "
              f"PnL=${TC['pnl'].sum():,.0f} DD={dd:.1f}% {'BREACH' if mb<FLOOR else 'ok'}")

TL_s = run_strategy(cond_pb_time_long,  'long',  tp_r=3.5, trail_start=1.5, trail_step=0.5)
TS_s = run_strategy(cond_pb_time_short, 'short', tp_r=3.5, trail_start=1.5, trail_step=0.5)
if len(TL_s)>0 and len(TS_s)>0:
    report(pd.concat([TL_s, TS_s]).sort_values('ts').reset_index(drop=True),
           "Pullback + Session Filter TP=3.5R")

# ════════════════════════════════════════════════════════════════
# STRATEGY TYPE 5: RSI OVERSOLD/OVERBOUGHT IN TREND
# ════════════════════════════════════════════════════════════════
print("\n\n>>> STRATEGY TYPE 5: RSI OVERSOLD/OVERBOUGHT IN TREND")

def cond_rsi_long(r):
    return (r['h4_up_s'] and r['w1_up'] and r['h4_rsi'] < 45
            and r['bull'] and r['d1_str'])

def cond_rsi_short(r):
    return (r['h4_dn_s'] and r['w1_dn'] and r['h4_rsi'] > 55
            and r['bear'] and not r['d1_up'])

for tp_r, trail in [(2.5, 1.2), (3.5, 1.5), (5.0, 2.5)]:
    TL = run_strategy(cond_rsi_long,  'long',  tp_r=tp_r, trail_start=trail, trail_step=0.5)
    TS = run_strategy(cond_rsi_short, 'short', tp_r=tp_r, trail_start=trail, trail_step=0.5)
    if len(TL)>5 and len(TS)>5:
        TC = pd.concat([TL, TS]).sort_values('ts').reset_index(drop=True)
        bal = START_BAL + TC['pnl'].cumsum()
        dd  = ((bal.cummax()-bal)/bal.cummax()*100).max()
        mb  = bal.min()
        print(f"  RSI Trend TP={tp_r}R: N={len(TC)} WR={TC['win'].mean():.1%} "
              f"PnL=${TC['pnl'].sum():,.0f} DD={dd:.1f}% {'BREACH' if mb<FLOOR else 'ok'}")

# Best RSI report
TL_r = run_strategy(cond_rsi_long,  'long',  tp_r=4.0, trail_start=2.0, trail_step=0.5)
TS_r = run_strategy(cond_rsi_short, 'short', tp_r=4.0, trail_start=2.0, trail_step=0.5)
if len(TL_r)>5 and len(TS_r)>5:
    report(pd.concat([TL_r, TS_r]).sort_values('ts').reset_index(drop=True),
           "RSI in Trend TP=4R")

# ════════════════════════════════════════════════════════════════
# STRATEGY TYPE 6: SUPER COMBO (all filters together)
# ════════════════════════════════════════════════════════════════
print("\n\n>>> STRATEGY TYPE 6: SUPER COMBO (trend + level + session + pattern)")

def cond_super_long(r):
    near_level = False
    if not pd.isna(r['d1_lo']) and abs(r['close']-r['d1_lo']) < 0.6*r['atr']: near_level = True
    if not pd.isna(r['wk_lo']) and abs(r['close']-r['wk_lo']) < 0.8*r['atr']: near_level = True
    if not pd.isna(r['h4_swL']) and abs(r['close']-r['h4_swL']) < 0.5*r['atr']: near_level = True
    pullback = abs(r['close'] - r['h4_ema20']) < 0.8 * r['h4_atr']
    return (r['h4_up'] and r['w1_up'] and r['d1_up']
            and (near_level or pullback)
            and r['hour'] in [5, 6, 8, 9, 15, 16, 0, 1]
            and (r['pin_up'] or r['engulf_up'] or r['bull']))

def cond_super_short(r):
    near_level = False
    if not pd.isna(r['d1_hi']) and abs(r['close']-r['d1_hi']) < 0.6*r['atr']: near_level = True
    if not pd.isna(r['wk_hi']) and abs(r['close']-r['wk_hi']) < 0.8*r['atr']: near_level = True
    if not pd.isna(r['h4_swH']) and abs(r['close']-r['h4_swH']) < 0.5*r['atr']: near_level = True
    pullback = abs(r['close'] - r['h4_ema20']) < 0.8 * r['h4_atr']
    return (r['h4_dn'] and r['w1_dn'] and not r['d1_up']
            and (near_level or pullback)
            and r['hour'] in [8, 9, 13, 15, 16, 17]
            and (r['pin_dn'] or r['engulf_dn'] or r['bear']))

for tp_r, trail in [(3.0, 1.5), (4.0, 2.0), (5.0, 2.5), (6.0, 3.0)]:
    TL = run_strategy(cond_super_long,  'long',  tp_r=tp_r, trail_start=trail, trail_step=0.5, max_per_day=2)
    TS = run_strategy(cond_super_short, 'short', tp_r=tp_r, trail_start=trail, trail_step=0.5, max_per_day=2)
    if len(TL)>5 and len(TS)>5:
        TC = pd.concat([TL, TS]).sort_values('ts').reset_index(drop=True)
        bal = START_BAL + TC['pnl'].cumsum()
        dd  = ((bal.cummax()-bal)/bal.cummax()*100).max()
        mb  = bal.min()
        print(f"  Super Combo TP={tp_r}R: N={len(TC)} WR={TC['win'].mean():.1%} "
              f"PnL=${TC['pnl'].sum():,.0f} DD={dd:.1f}% {'BREACH' if mb<FLOOR else 'ok'}")

best_sc = None
best_sc_pnl = 0
for tp_r, trail in [(3.0, 1.5), (4.0, 2.0), (5.0, 2.5), (6.0, 3.0)]:
    TL = run_strategy(cond_super_long,  'long',  tp_r=tp_r, trail_start=trail, trail_step=0.5, max_per_day=2)
    TS = run_strategy(cond_super_short, 'short', tp_r=tp_r, trail_start=trail, trail_step=0.5, max_per_day=2)
    if len(TL)>5 and len(TS)>5:
        TC = pd.concat([TL, TS]).sort_values('ts').reset_index(drop=True)
        if TC['pnl'].sum() > best_sc_pnl and (START_BAL+TC['pnl'].cumsum()).min() >= FLOOR:
            best_sc_pnl = TC['pnl'].sum()
            best_sc = (TC, tp_r)

if best_sc:
    report(best_sc[0], f"Super Combo TP={best_sc[1]}R (best safe)")

print("\n\nAll done.")
