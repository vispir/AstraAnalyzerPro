# -*- coding: utf-8 -*-
"""
Validation of S/R Swing Bounce strategy:
1. Fix look-ahead bias in swing detection
2. No-lookahead re-run
3. Rolling start analysis (start every month, check $9000 floor)
4. Max consecutive losses
5. Out-of-sample 2024-2026
"""
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

import pandas as pd
import numpy as np
import warnings
warnings.filterwarnings('ignore')

M15_FILE = r"D:\Works\ASTRA ANALYZER CHART\data_cache\dukascopy\m15\XAUUSD\xauusd_m15_2020-01-01_2026-05-12.parquet"
ATR_PERIOD = 14
RISK_USD   = 100.0
FLOOR      = 9000.0
START_BAL  = 10000.0

# ── Load ──────────────────────────────────────────────────────────────────
print("Loading...")
df = pd.read_parquet(M15_FILE)
df.index = pd.to_datetime(df.index, utc=True)
df = df.sort_index()
df.columns = [c.lower() for c in df.columns]

df['tr']  = np.maximum(df['high']-df['low'],
            np.maximum(abs(df['high']-df['close'].shift(1)),
                       abs(df['low'] -df['close'].shift(1))))
df['atr'] = df['tr'].ewm(alpha=1/ATR_PERIOD, adjust=False).mean()

# ── H4 ────────────────────────────────────────────────────────────────────
h4 = df.resample('4h', origin='epoch').agg(
    open=('open','first'), high=('high','max'),
    low=('low','min'),  close=('close','last')).dropna()
h4['ema20']  = h4['close'].ewm(span=20, adjust=False).mean()
h4['slope']  = h4['ema20'] - h4['ema20'].shift(3)
h4['h4_up']  = (h4['close'] > h4['ema20']) & (h4['slope'] > 0)
h4['h4_down']= (h4['close'] < h4['ema20']) & (h4['slope'] < 0)

# NO LOOKAHEAD swing detection:
# A bar is a swing HIGH if it's the highest in the last 5 bars (trailing window, not centered)
# This means we confirm it only AFTER it's already the highest — no future bars needed
h4['swing_high'] = h4['high'].where(
    h4['high'] == h4['high'].rolling(5).max()
).shift(1)  # shift 1 to not use current bar

h4['swing_low'] = h4['low'].where(
    h4['low'] == h4['low'].rolling(5).min()
).shift(1)

# Daily
d1 = df.resample('1D', origin='epoch').agg(close=('close','last')).dropna()
d1['d1_ema20'] = d1['close'].ewm(span=20, adjust=False).mean()
d1['d1_up']    = d1['close'] > d1['d1_ema20']

def map_h4(s): return s.shift(1).reindex(df.index, method='ffill')
def map_d1(s): return s.shift(1).reindex(df.index, method='ffill')

df['h4_up']    = map_h4(h4['h4_up'])
df['h4_down']  = map_h4(h4['h4_down'])
df['d1_up']    = map_d1(d1['d1_up'])
df['dow']      = df.index.dayofweek
df['hour']     = df.index.hour

print("  Data ready.")

# ── Trade simulator ────────────────────────────────────────────────────────
def simulate_trade(df, entry_idx, direction, sl_dist, tp_rr=2.5,
                   trail_start_r=1.2, trail_step_r=0.4, max_bars=400):
    entry      = df['close'].iloc[entry_idx]
    sl         = entry - sl_dist if direction=='long' else entry + sl_dist
    tp         = entry + tp_rr*sl_dist if direction=='long' else entry - tp_rr*sl_dist
    current_sl = sl
    best_r     = 0.0
    future     = df.iloc[entry_idx+1 : entry_idx+1+max_bars]

    for _, bar in future.iterrows():
        if direction == 'long':
            if bar['low']  <= current_sl:
                return (current_sl-entry)/sl_dist * RISK_USD, 'sl'
            if bar['high'] >= tp:
                return tp_rr * RISK_USD, 'tp'
            bar_r = (bar['high']-entry)/sl_dist
            if bar_r > best_r: best_r = bar_r
            if best_r >= trail_start_r:
                new_sl = entry + (best_r - trail_step_r)*sl_dist
                if new_sl > current_sl: current_sl = new_sl
        else:
            if bar['high'] >= current_sl:
                return (entry-current_sl)/sl_dist * RISK_USD, 'sl'
            if bar['low']  <= tp:
                return tp_rr * RISK_USD, 'tp'
            bar_r = (entry-bar['low'])/sl_dist
            if bar_r > best_r: best_r = bar_r
            if best_r >= trail_start_r:
                new_sl = entry - (best_r - trail_step_r)*sl_dist
                if new_sl < current_sl: current_sl = new_sl

    return (current_sl-entry)/sl_dist*RISK_USD if direction=='long' \
           else (entry-current_sl)/sl_dist*RISK_USD, 'timeout'

# ── S3 NO-LOOKAHEAD ────────────────────────────────────────────────────────
print("Running S3 (no-lookahead)...")

trades = []
h4_swings = h4[['swing_high','swing_low']].copy()

for i in range(100, len(df)-400):
    row  = df.iloc[i]
    dow  = row['dow']
    if dow == 4: continue  # skip Friday

    atr = row['atr']
    if atr <= 0 or pd.isna(atr): continue

    cur_time = df.index[i]
    date     = cur_time.date()

    # Recent confirmed swing levels (strictly before current bar)
    recent_h4   = h4_swings[h4_swings.index < cur_time].tail(20)
    rec_highs   = recent_h4['swing_high'].dropna().values
    rec_lows    = recent_h4['swing_low'].dropna().values

    already_long  = any(t['date']==date and t['direction']=='long'  for t in trades[-10:])
    already_short = any(t['date']==date and t['direction']=='short' for t in trades[-10:])

    # --- LONG: pin bar bouncing from swing low (support) ---
    if not already_long and (row['h4_up'] or row['d1_up']):
        for level in rec_lows:
            if abs(row['low'] - level) < 0.3 * atr and row['close'] > level:
                body   = abs(row['close'] - row['open'])
                l_wick = min(row['open'],row['close']) - row['low']
                if body > 0 and l_wick > 1.5 * body:
                    pnl, reason = simulate_trade(df, i, 'long', atr)
                    trades.append({
                        'date':      date,
                        'direction': 'long',
                        'pnl':       pnl,
                        'outcome':   'win' if pnl > 0 else 'loss',
                        'reason':    reason,
                        'hour':      row['hour'],
                        'dow':       dow,
                        'year':      cur_time.year,
                        'month':     cur_time.to_period('M'),
                    })
                    break

    # --- SHORT: pin bar bouncing from swing high (resistance) ---
    if not already_short and (row['h4_down'] or not row['d1_up']):
        for level in rec_highs:
            if abs(row['high'] - level) < 0.3 * atr and row['close'] < level:
                body   = abs(row['close'] - row['open'])
                u_wick = row['high'] - max(row['open'],row['close'])
                if body > 0 and u_wick > 1.5 * body:
                    pnl, reason = simulate_trade(df, i, 'short', atr)
                    trades.append({
                        'date':      date,
                        'direction': 'short',
                        'pnl':       pnl,
                        'outcome':   'win' if pnl > 0 else 'loss',
                        'reason':    reason,
                        'hour':      row['hour'],
                        'dow':       dow,
                        'year':      cur_time.year,
                        'month':     cur_time.to_period('M'),
                    })
                    break

T = pd.DataFrame(trades)
print(f"  Total trades: {len(T)}")

# ── Main stats ─────────────────────────────────────────────────────────────
n   = len(T)
wr  = (T['outcome']=='win').mean()
pnl = T['pnl'].sum()
aw  = T[T['outcome']=='win']['pnl'].mean()
al  = T[T['outcome']=='loss']['pnl'].mean()
pf  = abs(aw * wr / (al * (1-wr))) if al != 0 else 0

balance = START_BAL + T['pnl'].cumsum()
peak    = balance.cummax()
dd_pct  = ((peak - balance)/peak*100)
max_dd  = dd_pct.max()
min_bal = balance.min()

print(f"\n{'='*58}")
print(f"  S3 S/R BOUNCE - NO LOOKAHEAD VALIDATION")
print(f"{'='*58}")
print(f"  Trades : {n}  |  WR: {wr:.1%}  |  PF: {pf:.2f}")
print(f"  PnL    : ${pnl:,.0f}")
print(f"  MaxDD  : {max_dd:.2f}%  |  Min Balance: ${min_bal:,.0f}")
print(f"  Avg Win: ${aw:.0f}  |  Avg Loss: ${al:.0f}")

# Per direction
for d in ['long','short']:
    s = T[T['direction']==d]
    w = (s['outcome']=='win').mean()
    p = s['pnl'].sum()
    print(f"  {d:6s}: N={len(s):4d}  WR={w:.1%}  PnL=${p:,.0f}")

# Per year
print(f"\n  Year-by-year:")
yearly = T.groupby('year').agg(
    n=('pnl','count'),
    wr=('outcome', lambda x: (x=='win').mean()),
    pnl=('pnl','sum')
)
for yr, row2 in yearly.iterrows():
    bar = '#' * int(abs(row2['pnl'])/500)
    sign = '+' if row2['pnl'] >= 0 else '-'
    print(f"    {yr}: N={row2['n']:3.0f}  WR={row2['wr']:.1%}  {sign}${abs(row2['pnl']):,.0f}  {bar}")

# Consecutive losses
outcomes = (T['outcome']=='win').astype(int).values
max_consec_loss = 0
cur_loss = 0
for o in outcomes:
    if o == 0:
        cur_loss += 1
        max_consec_loss = max(max_consec_loss, cur_loss)
    else:
        cur_loss = 0
print(f"\n  Max consecutive losses: {max_consec_loss}")
print(f"  Floor $9,000 reached?   {'YES !!!' if min_bal < FLOOR else 'NO (safe)'}")

# ── Out-of-sample 2024-2026 ────────────────────────────────────────────────
print(f"\n{'='*58}")
print(f"  OUT-OF-SAMPLE: 2024-2026")
print(f"{'='*58}")
T_oos = T[T['year'] >= 2024]
if len(T_oos) > 0:
    wr_oos  = (T_oos['outcome']=='win').mean()
    pnl_oos = T_oos['pnl'].sum()
    bal_oos = START_BAL + T_oos['pnl'].cumsum()
    dd_oos  = ((bal_oos.cummax()-bal_oos)/bal_oos.cummax()*100).max()
    print(f"  Trades: {len(T_oos)}  WR: {wr_oos:.1%}  PnL: ${pnl_oos:,.0f}  MaxDD: {dd_oos:.2f}%")
    for yr in [2024,2025,2026]:
        s = T_oos[T_oos['year']==yr]
        if len(s): print(f"    {yr}: N={len(s):3d}  WR={(s['outcome']=='win').mean():.1%}  ${s['pnl'].sum():,.0f}")
else:
    print("  No 2024+ trades found.")

# ── Rolling start analysis ─────────────────────────────────────────────────
print(f"\n{'='*58}")
print(f"  ROLLING START (start $10k at each month, check $9k floor)")
print(f"{'='*58}")

T['month_str'] = T['month'].astype(str)
months = sorted(T['month'].unique())
breaches = []
results_roll = []

for start_month in months:
    subset = T[T['month'] >= start_month].copy()
    if len(subset) < 5:
        continue
    bal = START_BAL + subset['pnl'].cumsum()
    min_b = bal.min()
    breached = min_b < FLOOR
    if breached:
        breaches.append(str(start_month))
    results_roll.append((str(start_month), len(subset), min_b, breached))

total = len(results_roll)
n_breach = len(breaches)
print(f"  Total start months tested: {total}")
print(f"  Months that breach $9,000: {n_breach} ({n_breach/total:.1%})")

if breaches:
    print(f"  Breach months: {', '.join(breaches)}")
else:
    print(f"  NO breach months found! All starts are safe.")

# Show worst 5 starts
results_roll.sort(key=lambda x: x[2])
print(f"\n  Worst 5 start months (lowest balance reached):")
print(f"  {'Month':<12} {'Trades':>7} {'Min Balance':>13} {'Safe?':>7}")
print(f"  {'-'*42}")
for month, n_t, min_b, br in results_roll[:5]:
    safe = 'BREACH!' if br else 'OK'
    print(f"  {month:<12} {n_t:>7} ${min_b:>11,.0f}  {safe:>7}")

# ── Hour of day analysis ──────────────────────────────────────────────────
print(f"\n{'='*58}")
print(f"  BEST HOURS FOR ENTRIES")
print(f"{'='*58}")
for d in ['long','short']:
    sub = T[T['direction']==d]
    print(f"  {d.upper()}:")
    hourly = sub.groupby('hour').agg(n=('pnl','count'), wr=('outcome',lambda x:(x=='win').mean()), pnl=('pnl','sum'))
    for h, row2 in hourly.sort_values('pnl',ascending=False).head(8).iterrows():
        bar = '#' * int(row2['wr']*20)
        print(f"    {h:02d}:00  N={row2['n']:3.0f}  WR={row2['wr']:.1%}  ${row2['pnl']:,.0f}  {bar}")

# ── Save ──────────────────────────────────────────────────────────────────
T.to_csv(r"D:\Works\ASTRA ANALYZER CHART\scripts\s3_nolookahead_validated.csv", index=False)
print(f"\nSaved to s3_nolookahead_validated.csv")
print("Done.")
