# -*- coding: utf-8 -*-
"""
Risk scaling test: $100, $120, $150, $180, $200 per trade
Best strategy: H4up/dn, hours 5-9+13-16 LONG / 8-9+13-16 SHORT
TP=4R, SL=1.2xATR, trail 0.8R/0.3R
"""
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

import pandas as pd
import numpy as np
import warnings
warnings.filterwarnings('ignore')

M15_FILE    = r"D:\Works\ASTRA ANALYZER CHART\data_cache\dukascopy\m15\XAUUSD\xauusd_m15_2020-01-01_2026-05-12.parquet"
START_BAL   = 10000.0
FLOOR_PCT   = 0.10   # 10% max DD (Funding Pips rule)
FLOOR       = START_BAL * (1 - FLOOR_PCT)  # $9,000
TP_R        = 4.0
SL_MULT     = 1.2
TRAIL_START = 0.8
TRAIL_STEP  = 0.3
MAX_BARS    = 400
LONG_HOURS  = [5, 6, 7, 8, 9, 13, 14, 15, 16]
SHORT_HOURS = [8, 9, 13, 14, 15, 16]

RISK_LEVELS = [100, 120, 150, 180, 200]

print("Loading data...")
df = pd.read_parquet(M15_FILE)
df.index = pd.to_datetime(df.index, utc=True)
df = df.sort_index()
df.columns = [c.lower() for c in df.columns]
N = len(df)

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

def mmap(s):
    return s.shift(1).reindex(df.index, method='ffill')

df['h4_up'] = mmap(h4['h4_up'])
df['h4_dn'] = mmap(h4['h4_dn'])
df['hour']  = df.index.hour
df['dow']   = df.index.dayofweek

print(f"  {N} bars, features done.")

# ── Simulate, store outcomes in R units ───────────────────────────────────
print("Running strategy (storing R outcomes)...")

hi  = df['high'].values
lo  = df['low'].values
cl  = df['close'].values
atr = df['atr'].values
h4u = df['h4_up'].values
h4d = df['h4_dn'].values
hr  = df['hour'].values
dow = df['dow'].values
dates = df.index.date

trades_r = []   # store R value, year, month
traded = {}

for i in range(300, N - MAX_BARS - 1):
    if dow[i] == 4: continue
    if atr[i] <= 0 or np.isnan(atr[i]): continue
    date = dates[i]

    for direction in ['long', 'short']:
        if direction == 'long':
            if not (h4u[i] and hr[i] in LONG_HOURS): continue
            if traded.get((date, 'long'), 0) >= 1: continue
        else:
            if not (h4d[i] and hr[i] in SHORT_HOURS): continue
            if traded.get((date, 'short'), 0) >= 1: continue

        sl_dist = atr[i] * SL_MULT
        entry   = cl[i]
        cur_sl  = entry - sl_dist if direction == 'long' else entry + sl_dist
        tp_px   = entry + TP_R * sl_dist if direction == 'long' else entry - TP_R * sl_dist
        best_r  = 0.0
        result_r = None

        for j in range(i+1, min(i+MAX_BARS+1, N)):
            if direction == 'long':
                if lo[j] <= cur_sl:
                    result_r = (cur_sl - entry) / sl_dist; break
                if hi[j] >= tp_px:
                    result_r = TP_R; break
                r = (hi[j] - entry) / sl_dist
                if r > best_r: best_r = r
                if best_r >= TRAIL_START:
                    ns = entry + (best_r - TRAIL_STEP) * sl_dist
                    if ns > cur_sl: cur_sl = ns
            else:
                if hi[j] >= cur_sl:
                    result_r = (entry - cur_sl) / sl_dist; break
                if lo[j] <= tp_px:
                    result_r = TP_R; break
                r = (entry - lo[j]) / sl_dist
                if r > best_r: best_r = r
                if best_r >= TRAIL_START:
                    ns = entry - (best_r - TRAIL_STEP) * sl_dist
                    if ns < cur_sl: cur_sl = ns

        if result_r is None:
            result_r = (cur_sl - entry) / sl_dist if direction == 'long' else (entry - cur_sl) / sl_dist

        trades_r.append({
            'r': result_r,
            'win': result_r > 0,
            'year': df.index[i].year,
            'month': df.index[i].to_period('M'),
            'direction': direction,
        })
        traded[(date, direction)] = traded.get((date, direction), 0) + 1

T = pd.DataFrame(trades_r)
r_arr = T['r'].values
n_trades = len(T)
print(f"  Total trades: {n_trades} ({n_trades/77:.1f}/mo)  WR={T['win'].mean():.1%}")

# ── Test each risk level ───────────────────────────────────────────────────
print(f"\n{'='*90}")
print(f"  RISK SCALING ANALYSIS  (2020-2026, {n_trades} trades, WR={T['win'].mean():.1%})")
print(f"  Strategy: H4up/dn | TP=4R | SL=1.2xATR | trail@0.8R step 0.3R")
print(f"  Funding Pips: $10,000 account | Floor=${FLOOR:,.0f} (10% static DD)")
print(f"{'='*90}")
print(f"  {'Risk':>6}  {'PnL':>9}  {'PnL/yr':>8}  {'PnL/mo':>8}  {'MaxDD%':>7}  "
      f"{'MinBal':>9}  {'Floor':>7}  {'AllYrs+':>8}  {'MaxConsec':>10}")
print(f"  {'-'*88}")

results = {}
for risk in RISK_LEVELS:
    pnl_arr = r_arr * risk
    bal     = START_BAL + np.cumsum(pnl_arr)
    peak    = np.maximum.accumulate(bal)
    dd_arr  = (peak - bal) / peak * 100
    max_dd  = dd_arr.max()
    min_bal = bal.min()
    total_pnl = pnl_arr.sum()
    n_years   = 6.4  # 2020-mid2026
    n_months  = 77.0

    # All years positive
    yearly = {}
    for i, row in T.iterrows():
        yr = row['year']
        yearly[yr] = yearly.get(yr, 0) + row['r'] * risk
    all_pos = all(v > 0 for v in yearly.values())

    # Max consecutive losses
    outcomes = (r_arr > 0).astype(int)
    max_cl = cur_cl = 0
    for o in outcomes:
        cur_cl = cur_cl + 1 if o == 0 else 0
        max_cl = max(max_cl, cur_cl)

    floor_ok = min_bal >= FLOOR
    results[risk] = {
        'pnl': total_pnl, 'max_dd': max_dd, 'min_bal': min_bal,
        'floor_ok': floor_ok, 'all_pos': all_pos, 'max_cl': max_cl,
        'pnl_yr': total_pnl / n_years, 'pnl_mo': total_pnl / n_months,
        'yearly': yearly,
    }

    floor_str = 'SAFE   ' if floor_ok else 'BREACH!'
    allpos_str = 'YES' if all_pos else 'NO '
    print(f"  ${risk:>5}  ${total_pnl:>8,.0f}  ${total_pnl/n_years:>7,.0f}  "
          f"${total_pnl/n_months:>7,.0f}  {max_dd:>6.1f}%  "
          f"${min_bal:>8,.0f}  {floor_str}  {allpos_str}      {max_cl:>5} losses")

# ── Year-by-year for each risk ────────────────────────────────────────────
print(f"\n{'='*90}")
print(f"  YEAR-BY-YEAR BREAKDOWN")
print(f"{'='*90}")

years = sorted(set(T['year']))
header = f"  {'Year':<6}"
for risk in RISK_LEVELS:
    header += f"  ${risk:>5}/trade"
print(header)
print(f"  {'-'*80}")

for yr in years:
    row_str = f"  {yr:<6}"
    for risk in RISK_LEVELS:
        p = results[risk]['yearly'][yr]
        sign = '+' if p >= 0 else ''
        row_str += f"  {sign}${p:>7,.0f}  "
    print(row_str)

print(f"\n  {'TOTAL':<6}", end="")
for risk in RISK_LEVELS:
    p = results[risk]['pnl']
    print(f"  +${p:>7,.0f}  ", end="")
print()

# ── Rolling start breach analysis ────────────────────────────────────────
print(f"\n{'='*90}")
print(f"  ROLLING START ANALYSIS (risk of breaching $9,000 floor from any start month)")
print(f"{'='*90}")
print(f"  {'Risk':>6}  {'Breaches':>10}  {'Total':>8}  {'Breach%':>9}  {'MaxConsecLoss':>14}")
print(f"  {'-'*60}")

months = sorted(T['month'].unique())
for risk in RISK_LEVELS:
    pnl_arr = r_arr * risk
    breach_count = 0
    total_count  = 0
    for m in months:
        sub_r = r_arr[T['month'].values >= m]
        if len(sub_r) < 5: continue
        bal = START_BAL + np.cumsum(sub_r * risk)
        total_count += 1
        if bal.min() < FLOOR:
            breach_count += 1
    pct = breach_count / total_count * 100 if total_count > 0 else 0
    mcl = results[risk]['max_cl']
    print(f"  ${risk:>5}  {breach_count:>6}/{total_count:<3}    {total_count:>6}  {pct:>8.1f}%  {mcl:>10} consec")

# ── Recommendation ────────────────────────────────────────────────────────
print(f"\n{'='*90}")
print(f"  RECOMMENDATION")
print(f"{'='*90}")

for risk in RISK_LEVELS:
    r = results[risk]
    safe = '✓ SAFE' if r['floor_ok'] else '✗ FLOOR BREACH'
    allpos = '✓ all years+' if r['all_pos'] else '✗ some years negative'
    print(f"  ${risk}/trade: PnL=${r['pnl']:,.0f}  DD={r['max_dd']:.1f}%  {safe}  {allpos}")

print("\nDone.")
