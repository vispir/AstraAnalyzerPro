# -*- coding: utf-8 -*-
"""
Backtest Strategy B for May 4-15, 2026
LONG:  H4up + W1up, enter at 05:00 or 08:00 UTC
SHORT: H4dn + W1dn + bear bar, enter at 15:00 or 16:00 UTC
SL=1ATR, TP=2.5R, trail from 1.2R step 0.4
"""
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

import pandas as pd
import numpy as np
import warnings
warnings.filterwarnings('ignore')

RISK_USD    = 100.0
START_BAL   = 10000.0
TP_R        = 2.5
TRAIL_START = 1.2
TRAIL_STEP  = 0.4
MAX_BARS    = 350

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

h4 = df.resample('4h', origin='epoch').agg(
    open=('open','first'), high=('high','max'),
    low=('low','min'), close=('close','last')).dropna()
h4['ema20']  = h4['close'].ewm(span=20, adjust=False).mean()
h4['slope3'] = h4['ema20'] - h4['ema20'].shift(3)
h4['h4_up']  = (h4['close'] > h4['ema20']) & (h4['slope3'] > 0)
h4['h4_dn']  = (h4['close'] < h4['ema20']) & (h4['slope3'] < 0)

w1 = df.resample('1W', origin='epoch').agg(close=('close','last')).dropna()
w1['w1_ema10']= w1['close'].ewm(span=10, adjust=False).mean()
w1['w1_up']   = w1['close'] > w1['w1_ema10']
w1['w1_dn']   = w1['close'] < w1['w1_ema10']

def mmap(s):
    return s.shift(1).reindex(df.index, method='ffill')

df['h4_up']  = mmap(h4['h4_up'])
df['h4_dn']  = mmap(h4['h4_dn'])
df['w1_up']  = mmap(w1['w1_up'])
df['w1_dn']  = mmap(w1['w1_dn'])
df['hour']   = df.index.hour
df['dow']    = df.index.dayofweek
df['bear']   = df['close'] < df['open']
df['bull']   = df['close'] > df['open']

# ── Simulator ─────────────────────────────────────────────────────────────
def sim(idx, direction, sl_dist):
    entry  = df['close'].iloc[idx]
    sl     = entry - sl_dist if direction=='long' else entry + sl_dist
    tp     = entry + TP_R*sl_dist if direction=='long' else entry - TP_R*sl_dist
    cur_sl = sl
    best_r = 0.0
    exit_ts= None
    exit_px= None
    exit_reason = 'timeout'

    for i2 in range(idx+1, min(idx+MAX_BARS+1, len(df))):
        h  = df['high'].iloc[i2]
        lo = df['low'].iloc[i2]
        bar_ts = df.index[i2]

        if direction == 'long':
            if lo <= cur_sl:
                exit_px = cur_sl; exit_ts = bar_ts; exit_reason = 'SL'; break
            if h >= tp:
                exit_px = tp; exit_ts = bar_ts; exit_reason = 'TP'; break
            r = (h-entry)/sl_dist
            if r > best_r: best_r = r
            if best_r >= TRAIL_START:
                ns = entry + (best_r-TRAIL_STEP)*sl_dist
                if ns > cur_sl: cur_sl = ns
        else:
            if h >= cur_sl:
                exit_px = cur_sl; exit_ts = bar_ts; exit_reason = 'SL'; break
            if lo <= tp:
                exit_px = tp; exit_ts = bar_ts; exit_reason = 'TP'; break
            r = (entry-lo)/sl_dist
            if r > best_r: best_r = r
            if best_r >= TRAIL_START:
                ns = entry - (best_r-TRAIL_STEP)*sl_dist
                if ns < cur_sl: cur_sl = ns

    if exit_px is None:
        exit_px = cur_sl; exit_ts = df.index[min(idx+MAX_BARS, len(df)-1)]; exit_reason = 'timeout'

    # PnL from actual exit price vs entry
    if direction == 'long':
        pnl = (exit_px - entry) / sl_dist * RISK_USD
    else:
        pnl = (entry - exit_px) / sl_dist * RISK_USD
    return pnl, exit_reason, exit_ts, entry, exit_px, cur_sl

# ── Run full dataset for context (trend needs history) ────────────────────
print("Running backtest (full history for trend context, reporting May 4-15)...")

trades = []
traded = {}

# We need to start from bar 300 for trend context
# But we'll REPORT only trades opened in May 4-15
for i in range(300, len(df)-MAX_BARS-1):
    row  = df.iloc[i]
    ts   = df.index[i]
    date = ts.date()
    dow  = row['dow']

    if dow == 4: continue  # no Friday
    atr = row['atr']
    if atr <= 0 or pd.isna(atr): continue

    # LONG: h4 up + w1 up, hour 5 or 8
    if (row['h4_up'] and row['w1_up'] and row['hour'] in [5, 8]):
        if traded.get((date, 'long'), 0) < 1:
            pnl, reason, exit_ts, entry, exit_px, sl_px = sim(i, 'long', atr)
            trades.append({
                'open_ts': ts, 'close_ts': exit_ts,
                'date': date, 'year': ts.year, 'month': ts.to_period('M'),
                'hour': row['hour'], 'direction': 'LONG',
                'entry': entry, 'exit': exit_px,
                'sl': entry - atr, 'atr': atr,
                'pnl': pnl, 'win': pnl > 0, 'reason': reason,
            })
            traded[(date, 'long')] = traded.get((date, 'long'), 0) + 1

    # SHORT: h4 dn + w1 dn + bear bar, hour 15 or 16
    if (row['h4_dn'] and row['w1_dn'] and row['bear'] and row['hour'] in [15, 16]):
        if traded.get((date, 'short'), 0) < 1:
            pnl, reason, exit_ts, entry, exit_px, sl_px = sim(i, 'short', atr)
            trades.append({
                'open_ts': ts, 'close_ts': exit_ts,
                'date': date, 'year': ts.year, 'month': ts.to_period('M'),
                'hour': row['hour'], 'direction': 'SHORT',
                'entry': entry, 'exit': exit_px,
                'sl': entry + atr, 'atr': atr,
                'pnl': pnl, 'win': pnl > 0, 'reason': reason,
            })
            traded[(date, 'short')] = traded.get((date, 'short'), 0) + 1

T = pd.DataFrame(trades)
print(f"  Total trades all history: {len(T)}")

# ── Filter to May 4-15 ─────────────────────────────────────────────────────
from datetime import date as date_cls
may_start = date_cls(2026, 5, 4)
may_end   = date_cls(2026, 5, 15)
T_may = T[(T['date'] >= may_start) & (T['date'] <= may_end)].copy()

print(f"\n{'='*60}")
print(f"  BACKTEST: 4 MAY – 15 MAY 2026  (Strategy B)")
print(f"{'='*60}")

if len(T_may) == 0:
    print("  No trades in this period.")
else:
    # Show H4 and W1 state for context
    may4_bar = df[df.index.date == may_start]
    if len(may4_bar) > 0:
        r = may4_bar.iloc[0]
        print(f"\n  Market context on May 4:")
        print(f"    H4 uptrend: {bool(r['h4_up'])}  H4 downtrend: {bool(r['h4_dn'])}")
        print(f"    W1 uptrend: {bool(r['w1_up'])}  W1 downtrend: {bool(r['w1_dn'])}")
        print(f"    Gold price: ${r['close']:.2f}")

    print(f"\n  Individual trades:")
    print(f"  {'Date':<12} {'Dir':<6} {'Hr':>3} {'Entry':>8} {'Exit':>8} {'SL':>8} {'Reason':<8} {'PnL':>9}")
    print(f"  {'-'*65}")

    running_pnl = 0
    for _, t in T_may.sort_values('open_ts').iterrows():
        running_pnl += t['pnl']
        icon = '+' if t['pnl'] > 0 else '-'
        print(f"  {str(t['date']):<12} {t['direction']:<6} {t['hour']:>3}  "
              f"${t['entry']:>7.2f} ${t['exit']:>7.2f} ${t['sl']:>7.2f}  "
              f"{t['reason']:<8} {icon}${abs(t['pnl']):>7.2f}  bal=${START_BAL+running_pnl:,.0f}")

    # Summary
    n   = len(T_may)
    wr  = T_may['win'].mean()
    pnl = T_may['pnl'].sum()
    aw  = T_may[T_may['win']]['pnl'].mean() if T_may['win'].any() else 0
    al  = T_may[~T_may['win']]['pnl'].mean() if (~T_may['win']).any() else 0

    print(f"\n  {'='*55}")
    print(f"  SUMMARY May 4–15, 2026")
    print(f"  {'='*55}")
    print(f"  Trades:   {n}")
    print(f"  Win rate: {wr:.1%}")
    print(f"  Total P&L: {'+'if pnl>=0 else ''}${pnl:,.2f}")
    print(f"  Avg Win:  ${aw:.2f}  |  Avg Loss: ${al:.2f}")
    print(f"  Balance:  ${START_BAL+pnl:,.2f}  (started $10,000)")

    # By direction
    print(f"\n  By direction:")
    for d in ['LONG', 'SHORT']:
        s = T_may[T_may['direction']==d]
        if len(s):
            print(f"    {d}: N={len(s)}  WR={s['win'].mean():.1%}  PnL=${s['pnl'].sum():,.2f}")

    # By day
    print(f"\n  Day-by-day:")
    print(f"  {'Date':<12} {'N':>3} {'PnL':>9} {'Cumul':>9}")
    print(f"  {'-'*38}")
    cumul = 0
    for day, grp in T_may.groupby('date'):
        day_pnl = grp['pnl'].sum()
        cumul  += day_pnl
        icon = '+' if day_pnl >= 0 else ''
        print(f"  {str(day):<12} {len(grp):>3}  {icon}${day_pnl:>7.2f}  ${cumul:>+8.2f}")

# ── Also show what trades were in April-May for context ───────────────────
print(f"\n{'='*60}")
print(f"  CONTEXT: All 2026 trades with Strategy B")
print(f"{'='*60}")
T_2026 = T[T['year']==2026].copy()
if len(T_2026):
    pnl_26 = T_2026['pnl'].sum()
    wr_26  = T_2026['win'].mean()
    print(f"  2026: N={len(T_2026)}  WR={wr_26:.1%}  PnL=${pnl_26:,.2f}")
    print(f"\n  Month-by-month 2026:")
    for mo, grp in T_2026.groupby('month'):
        mp = grp['pnl'].sum()
        print(f"    {mo}: N={len(grp)}  WR={grp['win'].mean():.1%}  ${mp:,.2f}")

print("\nDone.")
