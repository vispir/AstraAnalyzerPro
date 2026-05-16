# -*- coding: utf-8 -*-
"""Data-Driven Strategy Analysis: find filters separating good vs bad entries"""
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

import pandas as pd
import numpy as np
import warnings
warnings.filterwarnings('ignore')

# ── Parameters ────────────────────────────────────────────────────────────
M15_FILE = r"D:\Works\ASTRA ANALYZER CHART\data_cache\dukascopy\m15\XAUUSD\xauusd_m15_2020-01-01_2026-05-12.parquet"
ATR_PERIOD  = 14
ATR_BUF     = 0.5
TP_RR       = 12.0
H4_EMA_PER  = 20
SLOPE_N     = 5
RISK_USD    = 100.0

SESSIONS = {
    "asian":  (3,  6),
    "london": (8,  11),
    "ny":     (15, 18),
}

# ── Load data ─────────────────────────────────────────────────────────────
print("Loading M15...")
df = pd.read_parquet(M15_FILE)
df.index = pd.to_datetime(df.index, utc=True)
df = df.sort_index()
df.columns = [c.lower() for c in df.columns]
print(f"  {len(df)} bars: {df.index[0].date()} to {df.index[-1].date()}")

# ── ATR ───────────────────────────────────────────────────────────────────
df['tr'] = np.maximum(df['high'] - df['low'],
           np.maximum(abs(df['high'] - df['close'].shift(1)),
                      abs(df['low']  - df['close'].shift(1))))
df['atr'] = df['tr'].ewm(alpha=1/ATR_PERIOD, adjust=False).mean()
df['atr_avg'] = df['atr'].rolling(96).mean()
df['atr_regime'] = df['atr'] / df['atr_avg']

# ── Build H4 from M15 ─────────────────────────────────────────────────────
print("Building H4...")
h4 = df.resample('4h', origin='epoch').agg(
    open=('open','first'), high=('high','max'),
    low=('low','min'),  close=('close','last')
).dropna()

h4['ema'] = h4['close'].ewm(span=H4_EMA_PER, adjust=False).mean()
h4['ema_slope'] = h4['ema'] - h4['ema'].shift(SLOPE_N)
h4['h4_up']   = (h4['close'] > h4['ema']) & (h4['ema_slope'] > 0)
h4['h4_down'] = (h4['close'] < h4['ema']) & (h4['ema_slope'] < 0)
h4['hh'] = h4['high'] > h4['high'].shift(1)
h4['hl'] = h4['low']  > h4['low'].shift(1)
h4['lh'] = h4['high'] < h4['high'].shift(1)
h4['ll'] = h4['low']  < h4['low'].shift(1)
h4['h4_bull_st'] = h4['hh'] & h4['hl']
h4['h4_bear_st'] = h4['lh'] & h4['ll']
h4['h4_high20']  = h4['high'].rolling(20).max()
h4['dist_high']  = (h4['h4_high20'] - h4['close']) / h4['close'] * 100

# ── Daily trend ───────────────────────────────────────────────────────────
d1 = df.resample('1D', origin='epoch').agg(close=('close','last')).dropna()
d1['d1_ema20'] = d1['close'].ewm(span=20, adjust=False).mean()
d1['weekly_up'] = d1['close'] > d1['d1_ema20']

# ── Map H4/D1 features to M15 (shift to avoid look-ahead) ─────────────────
def map_to_m15(series, m15_index):
    return series.shift(1).reindex(m15_index, method='ffill')

df['h4_up']      = map_to_m15(h4['h4_up'],       df.index)
df['h4_down']    = map_to_m15(h4['h4_down'],      df.index)
df['h4_bull_st'] = map_to_m15(h4['h4_bull_st'],   df.index)
df['h4_bear_st'] = map_to_m15(h4['h4_bear_st'],   df.index)
df['dist_high']  = map_to_m15(h4['dist_high'],     df.index)
df['weekly_up']  = map_to_m15(d1['weekly_up'],     df.index)

df['hour_utc'] = df.index.hour
df['dow']      = df.index.dayofweek
df['month']    = df.index.month

# ── Simulate entries ──────────────────────────────────────────────────────
print("Simulating entries...")

trades = []
dates = sorted(set(df.index.normalize()))

for date in dates:
    for sess, (h_start, h_end) in SESSIONS.items():
        sess_mask = (
            (df.index.date == date.date()) &
            (df.index.hour >= h_start) &
            (df.index.hour < h_end)
        )
        sess_bars = df[sess_mask]
        if len(sess_bars) < 4:
            continue

        s_high = sess_bars['high'].max()
        s_low  = sess_bars['low'].min()
        if (s_high - s_low) < 1.0:
            continue

        last_bar = sess_bars.index[-1]
        last_atr = sess_bars['atr'].iloc[-1]
        if last_atr <= 0:
            continue

        feat = df.loc[last_bar]

        entry_long  = s_high + ATR_BUF * last_atr
        entry_short = s_low  - ATR_BUF * last_atr
        sl_dist = last_atr

        # Future bars for outcome simulation (up to 5 days)
        future = df[df.index > last_bar].head(480)

        for direction in ['long', 'short']:
            entry = entry_long  if direction == 'long' else entry_short
            sl    = entry - sl_dist if direction == 'long' else entry + sl_dist
            tp    = entry + TP_RR * sl_dist if direction == 'long' else entry - TP_RR * sl_dist

            outcome = None
            triggered = False
            bars_held = 0

            for _, bar in future.iterrows():
                if not triggered:
                    if direction == 'long'  and bar['high'] >= entry: triggered = True
                    elif direction == 'short' and bar['low'] <= entry: triggered = True
                    if not triggered: continue

                bars_held += 1
                if direction == 'long':
                    if bar['low']  <= sl: outcome = 'loss'; break
                    if bar['high'] >= tp: outcome = 'win';  break
                else:
                    if bar['high'] >= sl: outcome = 'loss'; break
                    if bar['low']  <= tp: outcome = 'win';  break

            if not triggered or outcome is None:
                continue

            trades.append({
                'date': date, 'session': sess, 'direction': direction,
                'outcome': outcome, 'pnl': RISK_USD if outcome=='win' else -RISK_USD,
                'bars_held': bars_held,
                'h4_up': feat['h4_up'], 'h4_down': feat['h4_down'],
                'h4_bull_st': feat['h4_bull_st'], 'h4_bear_st': feat['h4_bear_st'],
                'weekly_up': feat['weekly_up'], 'atr_regime': feat['atr_regime'],
                'dist_high': feat['dist_high'], 'dow': feat['dow'], 'month': feat['month'],
                'atr': last_atr, 's_range': s_high - s_low,
            })

T = pd.DataFrame(trades)
print(f"Total entries: {len(T)}  LONG={len(T[T.direction=='long'])}  SHORT={len(T[T.direction=='short'])}")

def stats(df_):
    n = len(df_)
    if n == 0: return "no trades"
    wr  = (df_['outcome']=='win').mean()
    pnl = df_['pnl'].sum()
    return f"N={n:4d}  WR={wr:.1%}  PnL=${pnl:,.0f}"

# ── Base results ──────────────────────────────────────────────────────────
print("\n" + "="*60)
print("  BASE RESULTS (no filters)")
print("="*60)
for sess in ['asian','london','ny','ALL']:
    for d in ['long','short']:
        mask = (T['session']==sess) & (T['direction']==d) if sess!='ALL' else T['direction']==d
        print(f"  {sess:8s} {d:6s}: {stats(T[mask])}")

# ── Feature impact ────────────────────────────────────────────────────────
print("\n" + "="*60)
print("  FEATURE IMPACT ON WIN RATE")
print("="*60)
print(f"  {'Feature':<28} {'YES':>22} {'NO':>22}")
print("  " + "-"*72)

feats = [
    ("H4_UP (long)",       'h4_up',      True,  'long'),
    ("H4_DOWN (short)",    'h4_down',    True,  'short'),
    ("H4 Bull Structure",  'h4_bull_st', True,  'long'),
    ("H4 Bear Structure",  'h4_bear_st', True,  'short'),
    ("Weekly UP (long)",   'weekly_up',  True,  'long'),
    ("Weekly DOWN (short)",'weekly_up',  False, 'short'),
]
for name, col, val, dir_ in feats:
    sub  = T[T['direction']==dir_]
    yes  = sub[sub[col]==val]
    no   = sub[sub[col]!=val]
    yw   = (yes['outcome']=='win').mean() if len(yes) else 0
    nw   = (no['outcome']=='win').mean()  if len(no)  else 0
    yp   = yes['pnl'].sum()
    np_  = no['pnl'].sum()
    print(f"  {name:<28} WR={yw:.1%} ${yp:>8,.0f}   WR={nw:.1%} ${np_:>8,.0f}")

# ── Day of week ───────────────────────────────────────────────────────────
print("\n  Day of week:")
dow_names = ['Mon','Tue','Wed','Thu','Fri']
for dir_ in ['long','short']:
    sub = T[T['direction']==dir_]
    print(f"  {dir_.upper()}:")
    for d in range(5):
        s = sub[sub['dow']==d]
        if len(s)==0: continue
        wr = (s['outcome']=='win').mean()
        bar = '#' * int(wr*20)
        print(f"    {dow_names[d]}: WR={wr:.1%} N={len(s):3d} PnL=${s['pnl'].sum():>7,.0f}  {bar}")

# ── ATR regime ───────────────────────────────────────────────────────────
print("\n  ATR regime (low < 0.8, normal 0.8-1.3, high > 1.3):")
for dir_ in ['long','short']:
    sub = T[T['direction']==dir_].dropna(subset=['atr_regime'])
    lo  = sub[sub['atr_regime'] <  0.8]
    nm  = sub[sub['atr_regime'].between(0.8,1.3)]
    hi  = sub[sub['atr_regime'] >= 1.3]
    print(f"  {dir_:6s}: Low={( lo['outcome']=='win').mean():.1%}(N={len(lo):3d}) "
          f"Norm={(nm['outcome']=='win').mean():.1%}(N={len(nm):3d}) "
          f"High={(hi['outcome']=='win').mean():.1%}(N={len(hi):3d})")

# ── Long filter combos ────────────────────────────────────────────────────
print("\n" + "="*60)
print("  LONG FILTER COMBINATIONS")
print("="*60)
L = T[T['direction']=='long'].copy()
base_l = L['pnl'].sum()
print(f"  Base (no filter): N={len(L)} WR={(L['outcome']=='win').mean():.1%} PnL=${base_l:,.0f}")
print(f"\n  {'Filter':<40} {'N':>4} {'WR':>6} {'PnL':>9} {'vs base':>9}")
print("  " + "-"*70)

combos_l = [
    ("H4_UP",                     L['h4_up']==True),
    ("H4_UP + Weekly_UP",        (L['h4_up']==True)&(L['weekly_up']==True)),
    ("H4_UP + BullSt",           (L['h4_up']==True)&(L['h4_bull_st']==True)),
    ("H4_UP + BullSt + Weekly",  (L['h4_up']==True)&(L['h4_bull_st']==True)&(L['weekly_up']==True)),
    ("Weekly_UP only",            L['weekly_up']==True),
    ("BullSt only",               L['h4_bull_st']==True),
    ("No Friday",                 L['dow']<4),
    ("H4_UP + No Friday",        (L['h4_up']==True)&(L['dow']<4)),
    ("H4_UP + ATR norm",         (L['h4_up']==True)&(L['atr_regime'].between(0.7,1.5))),
    ("Weekly_UP + No Friday",    (L['weekly_up']==True)&(L['dow']<4)),
]
for name, mask in combos_l:
    s = L[mask]
    n = len(s); wr = (s['outcome']=='win').mean() if n else 0; pnl = s['pnl'].sum()
    print(f"  {name:<40} {n:>4} {wr:>6.1%} ${pnl:>8,.0f} {pnl-base_l:>+9,.0f}")

# ── Short filter combos ───────────────────────────────────────────────────
print("\n" + "="*60)
print("  SHORT FILTER COMBINATIONS")
print("="*60)
S = T[T['direction']=='short'].copy()
base_s = S['pnl'].sum()
print(f"  Base (no filter): N={len(S)} WR={(S['outcome']=='win').mean():.1%} PnL=${base_s:,.0f}")
print(f"\n  {'Filter':<40} {'N':>4} {'WR':>6} {'PnL':>9} {'vs base':>9}")
print("  " + "-"*70)

combos_s = [
    ("H4_DOWN",                   S['h4_down']==True),
    ("H4_DOWN + Weekly_DOWN",    (S['h4_down']==True)&(S['weekly_up']==False)),
    ("H4_DOWN + BearSt",         (S['h4_down']==True)&(S['h4_bear_st']==True)),
    ("H4_DOWN + BearSt + Wkly", (S['h4_down']==True)&(S['h4_bear_st']==True)&(S['weekly_up']==False)),
    ("Weekly_DOWN only",          S['weekly_up']==False),
    ("BearSt only",               S['h4_bear_st']==True),
    ("No Friday",                 S['dow']<4),
    ("H4_DOWN + No Friday",      (S['h4_down']==True)&(S['dow']<4)),
    ("Weekly_DOWN + No Friday",  (S['weekly_up']==False)&(S['dow']<4)),
]
for name, mask in combos_s:
    s = S[mask]
    n = len(s); wr = (s['outcome']=='win').mean() if n else 0; pnl = s['pnl'].sum()
    print(f"  {name:<40} {n:>4} {wr:>6.1%} ${pnl:>8,.0f} {pnl-base_s:>+9,.0f}")

# ── Best LONG+SHORT combinations ─────────────────────────────────────────
print("\n" + "="*60)
print("  BEST LONG+SHORT COMBINATIONS")
print("="*60)

results = []
long_opts  = [
    ("H4_UP",          T[(T.direction=='long')&(T.h4_up==True)]),
    ("H4_UP+Wkly",     T[(T.direction=='long')&(T.h4_up==True)&(T.weekly_up==True)]),
    ("H4_UP+BullSt+W", T[(T.direction=='long')&(T.h4_up==True)&(T.h4_bull_st==True)&(T.weekly_up==True)]),
    ("Wkly_UP",        T[(T.direction=='long')&(T.weekly_up==True)]),
]
short_opts = [
    ("H4_DOWN",        T[(T.direction=='short')&(T.h4_down==True)]),
    ("H4_DOWN+Wkly",   T[(T.direction=='short')&(T.h4_down==True)&(T.weekly_up==False)]),
    ("H4_DOWN+BrSt+W", T[(T.direction=='short')&(T.h4_down==True)&(T.h4_bear_st==True)&(T.weekly_up==False)]),
    ("Wkly_DOWN",      T[(T.direction=='short')&(T.weekly_up==False)]),
]

for ln, lf in long_opts:
    for sn, sf in short_opts:
        c = pd.concat([lf, sf])
        results.append((c['pnl'].sum(), len(c), (c['outcome']=='win').mean(), ln, sn))

results.sort(reverse=True)
print(f"\n  {'LONG':<18} {'SHORT':<18} {'N':>4} {'WR':>6} {'PnL':>9}")
print("  " + "-"*58)
for pnl, n, wr, ln, sn in results[:8]:
    print(f"  {ln:<18} {sn:<18} {n:>4} {wr:.1%} ${pnl:>8,.0f}")

# ── Save ──────────────────────────────────────────────────────────────────
out = r"D:\Works\ASTRA ANALYZER CHART\scripts\all_trades_analysis.csv"
T.to_csv(out, index=False)
print(f"\nSaved: {out}")
print("Done.")
