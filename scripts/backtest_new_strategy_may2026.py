# -*- coding: utf-8 -*-
"""
Backtest NEW BEST STRATEGY for May 4-15, 2026
LONG:  H4up, hours 05-09+13-16 UTC, TP=4R, SL=1.2xATR, trail 0.8R/0.3R
SHORT: H4dn, hours 08-09+13-16 UTC, TP=4R, SL=1.2xATR, trail 0.8R/0.3R

No-lookahead: all HTF features use shift(1) before reindex to M15.
"""
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

import pandas as pd
import numpy as np
import warnings
warnings.filterwarnings('ignore')
from datetime import date as date_cls

TP_R        = 4.0
SL_MULT     = 1.2   # SL = SL_MULT * ATR
TRAIL_START = 0.8
TRAIL_STEP  = 0.3
MAX_BARS    = 400
RISK_USD    = 100.0
START_BAL   = 10000.0

LONG_HOURS  = [5, 6, 7, 8, 9, 13, 14, 15, 16]
SHORT_HOURS = [8, 9, 13, 14, 15, 16]

# ── Load and merge data ────────────────────────────────────────────────────
print("Loading data...")
f_main = r"D:\Works\ASTRA ANALYZER CHART\data_cache\dukascopy\m15\XAUUSD\xauusd_m15_2020-01-01_2026-05-12.parquet"
f_new  = r"D:\Works\ASTRA ANALYZER CHART\data_cache\dukascopy\m15\XAUUSD\xauusd_m15_2026-05-12_2026-05-15.parquet"

df_main = pd.read_parquet(f_main)
df_new  = pd.read_parquet(f_new)
df = pd.concat([df_main, df_new])
df.index = pd.to_datetime(df.index, utc=True)
df = df[~df.index.duplicated(keep='last')].sort_index()
df.columns = [c.lower() for c in df.columns]

print(f"  Total bars: {len(df)}")
print(f"  Range: {df.index[0].date()} -> {df.index[-1].date()}")

# ── Features ──────────────────────────────────────────────────────────────
df['tr']  = np.maximum(df['high']-df['low'],
            np.maximum(abs(df['high']-df['close'].shift(1)),
                       abs(df['low'] -df['close'].shift(1))))
df['atr'] = df['tr'].ewm(alpha=1/14, adjust=False).mean()

# H4 with shift(1) — NO LOOKAHEAD
# shift(1) on H4 means: when H4 bar closes at 08:00,
# we can only use that info starting from 08:15 M15 bar
h4 = df.resample('4h', origin='epoch').agg(
    open=('open','first'), high=('high','max'),
    low=('low','min'), close=('close','last')).dropna()
h4['ema20']  = h4['close'].ewm(span=20, adjust=False).mean()
h4['slope3'] = h4['ema20'] - h4['ema20'].shift(3)
h4['h4_up']  = (h4['close'] > h4['ema20']) & (h4['slope3'] > 0)
h4['h4_dn']  = (h4['close'] < h4['ema20']) & (h4['slope3'] < 0)

# CRITICAL: shift(1) before reindex = no lookahead
def mmap(s):
    return s.shift(1).reindex(df.index, method='ffill')

df['h4_up'] = mmap(h4['h4_up'])
df['h4_dn'] = mmap(h4['h4_dn'])
df['h4_ema20'] = mmap(h4['ema20'])

df['hour'] = df.index.hour
df['dow']  = df.index.dayofweek

print("\n=== NO-LOOKAHEAD VALIDATION ===")
print("  H4 EMA20 reindexed with shift(1): last known COMPLETED H4 bar")
# Show an example: at 09:00 UTC on a day, h4_up should reflect the 04:00-08:00 bar
sample = df[df.index.date == date_cls(2026, 5, 5)].head(8)
print(f"  Sample May 5 bars (h4_up = prev completed H4 bar trend):")
for ts, row in sample.iterrows():
    print(f"    {ts.strftime('%H:%M')} UTC | close={row['close']:.2f} | "
          f"h4_ema20={row['h4_ema20']:.2f} | h4_up={bool(row['h4_up'])} | h4_dn={bool(row['h4_dn'])}")

# ── Simulator ─────────────────────────────────────────────────────────────
def sim(idx):
    row   = df.iloc[idx]
    entry = df['close'].iloc[idx]
    atr_v = row['atr']
    sl_dist = atr_v * SL_MULT

    # Determine direction
    if row['h4_up'] and row['hour'] in LONG_HOURS:
        direction = 'LONG'
        tp_price  = entry + TP_R * sl_dist
        sl_price  = entry - sl_dist
    elif row['h4_dn'] and row['hour'] in SHORT_HOURS:
        direction = 'SHORT'
        tp_price  = entry - TP_R * sl_dist
        sl_price  = entry + sl_dist
    else:
        return None

    cur_sl = sl_price
    best_r = 0.0
    exit_ts = None
    exit_px = None
    exit_reason = 'timeout'

    for i2 in range(idx+1, min(idx+MAX_BARS+1, len(df))):
        h  = df['high'].iloc[i2]
        lo = df['low'].iloc[i2]

        if direction == 'LONG':
            if lo <= cur_sl:
                exit_px = cur_sl; exit_ts = df.index[i2]; exit_reason = 'SL'; break
            if h >= tp_price:
                exit_px = tp_price; exit_ts = df.index[i2]; exit_reason = 'TP'; break
            r = (h - entry) / sl_dist
            if r > best_r: best_r = r
            if best_r >= TRAIL_START:
                ns = entry + (best_r - TRAIL_STEP) * sl_dist
                if ns > cur_sl: cur_sl = ns
        else:
            if h >= cur_sl:
                exit_px = cur_sl; exit_ts = df.index[i2]; exit_reason = 'SL'; break
            if lo <= tp_price:
                exit_px = tp_price; exit_ts = df.index[i2]; exit_reason = 'TP'; break
            r = (entry - lo) / sl_dist
            if r > best_r: best_r = r
            if best_r >= TRAIL_START:
                ns = entry - (best_r - TRAIL_STEP) * sl_dist
                if ns < cur_sl: cur_sl = ns

    if exit_px is None:
        exit_px = cur_sl; exit_ts = df.index[min(idx+MAX_BARS, len(df)-1)]; exit_reason = 'timeout'

    if direction == 'LONG':
        pnl = (exit_px - entry) / sl_dist * RISK_USD
    else:
        pnl = (entry - exit_px) / sl_dist * RISK_USD

    return {
        'open_ts': df.index[idx], 'close_ts': exit_ts,
        'date': df.index[idx].date(), 'hour': row['hour'],
        'direction': direction,
        'entry': entry, 'exit': exit_px,
        'sl_init': sl_price, 'sl_final': cur_sl,
        'atr': atr_v, 'sl_dist': sl_dist,
        'pnl': pnl, 'win': pnl > 0, 'reason': exit_reason,
        'h4_up': bool(row['h4_up']), 'h4_dn': bool(row['h4_dn']),
        'h4_ema20': row['h4_ema20'],
    }

# ── Run full history (needs warmup for H4 EMA to stabilize) ──────────────
print("\nRunning backtest (full 2020-2026 history for warmup)...")
trades = []
traded = {}

for i in range(300, len(df)-MAX_BARS-1):
    row = df.iloc[i]
    ts  = df.index[i]
    date = ts.date()

    if row['dow'] == 4: continue
    atr = row['atr']
    if atr <= 0 or pd.isna(atr): continue

    # LONG: H4up + hour in LONG_HOURS, max 1/day
    if row['h4_up'] and row['hour'] in LONG_HOURS:
        if traded.get((date, 'long'), 0) < 1:
            t = sim(i)
            if t:
                trades.append(t)
                traded[(date, 'long')] = traded.get((date, 'long'), 0) + 1

    # SHORT: H4dn + hour in SHORT_HOURS, max 1/day
    if row['h4_dn'] and row['hour'] in SHORT_HOURS:
        if traded.get((date, 'short'), 0) < 1:
            t = sim(i)
            if t:
                trades.append(t)
                traded[(date, 'short')] = traded.get((date, 'short'), 0) + 1

T = pd.DataFrame(trades)
print(f"  Total trades all history: {len(T)}")

# ── Filter May 4-15 ───────────────────────────────────────────────────────
may_start = date_cls(2026, 5, 4)
may_end   = date_cls(2026, 5, 15)
T_may = T[(T['date'] >= may_start) & (T['date'] <= may_end)].copy()

print(f"\n{'='*70}")
print(f"  BACKTEST: 4 MAY – 15 MAY 2026")
print(f"  NEW STRATEGY: H4 trend, TP=4R, SL=1.2xATR, trail@0.8R/0.3R")
print(f"{'='*70}")

if len(T_may) == 0:
    print("  No trades in this period.")
else:
    # Show market context
    print(f"\n  Market context (H4 trend state each day):")
    for day in pd.date_range(str(may_start), str(may_end)):
        day_bars = df[df.index.date == day.date()]
        if len(day_bars) == 0: continue
        r = day_bars.iloc[0]
        trend = 'UP' if r['h4_up'] else ('DN' if r['h4_dn'] else 'FLAT')
        dow_name = ['Mon','Tue','Wed','Thu','Fri','Sat','Sun'][day.dayofweek]
        print(f"    {day.date()} {dow_name}: H4={trend}  close=${r['close']:.2f}  EMA20=${r['h4_ema20']:.2f}")

    print(f"\n  Individual trades (1 LONG + 1 SHORT max per day):")
    print(f"  {'Date':<12} {'Dir':<6} {'Hr':>3} {'Entry':>8} {'Exit':>8} {'SL_init':>8} {'Reason':<8} {'R':>5} {'PnL':>9}  Balance")
    print(f"  {'-'*80}")

    running_pnl = 0
    for _, t in T_may.sort_values('open_ts').iterrows():
        running_pnl += t['pnl']
        r_val = t['pnl'] / RISK_USD
        icon = '+' if t['pnl'] > 0 else ''
        print(f"  {str(t['date']):<12} {t['direction']:<6} {t['hour']:>3}  "
              f"${t['entry']:>7.2f} ${t['exit']:>7.2f} ${t['sl_init']:>7.2f}  "
              f"{t['reason']:<8} {r_val:>+5.2f}R  {icon}${abs(t['pnl']):>7.2f}  "
              f"bal=${START_BAL+running_pnl:,.0f}")

    n   = len(T_may)
    wr  = T_may['win'].mean()
    pnl = T_may['pnl'].sum()
    aw  = T_may[T_may['win']]['pnl'].mean() if T_may['win'].any() else 0
    al  = T_may[~T_may['win']]['pnl'].mean() if (~T_may['win']).any() else 0

    print(f"\n  {'='*55}")
    print(f"  SUMMARY May 4–15, 2026")
    print(f"  {'='*55}")
    print(f"  Trades:    {n}")
    print(f"  Win rate:  {wr:.1%}")
    print(f"  Total P&L: {'+'if pnl>=0 else ''}${pnl:,.2f}")
    print(f"  Avg Win:   ${aw:.2f}  |  Avg Loss: ${al:.2f}")
    print(f"  Balance:   ${START_BAL+pnl:,.2f}  (started $10,000)")

    print(f"\n  By direction:")
    for d in ['LONG', 'SHORT']:
        s = T_may[T_may['direction']==d]
        if len(s):
            print(f"    {d}: N={len(s)}  WR={s['win'].mean():.1%}  PnL=${s['pnl'].sum():,.2f}")

    print(f"\n  Day-by-day:")
    print(f"  {'Date':<12} {'N':>3} {'PnL':>9} {'Cumul':>9}")
    print(f"  {'-'*38}")
    cumul = 0
    for day, grp in T_may.groupby('date'):
        day_pnl = grp['pnl'].sum()
        cumul += day_pnl
        icon = '+' if day_pnl >= 0 else ''
        print(f"  {str(day):<12} {len(grp):>3}  {icon}${day_pnl:>7.2f}  ${cumul:>+8.2f}")

# ── 2026 context ──────────────────────────────────────────────────────────
print(f"\n{'='*70}")
print(f"  CONTEXT: All 2026 trades")
print(f"{'='*70}")
T_2026 = T[T['date'].apply(lambda d: d.year) == 2026].copy()
if len(T_2026):
    print(f"  2026: N={len(T_2026)}  WR={T_2026['win'].mean():.1%}  PnL=${T_2026['pnl'].sum():,.2f}")
    T_2026['month'] = pd.to_datetime(T_2026['date'].astype(str)).dt.to_period('M')
    for mo, grp in T_2026.groupby('month'):
        mp = grp['pnl'].sum()
        print(f"    {mo}: N={len(grp)}  WR={grp['win'].mean():.1%}  PnL=${mp:,.2f}")

print("\nDone.")
