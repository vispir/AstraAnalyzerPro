"""Analyze win/loss breakdown by session for TP=12R strategy."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'astra_v2', 'strategies'))

import pandas as pd
import numpy as np
from pathlib import Path
import importlib.util

# load strategy module
spec = importlib.util.spec_from_file_location(
    "strat",
    Path(__file__).parent.parent / "astra_v2" / "strategies" / "session_long_nolookahead_v1.py"
)
strat = importlib.util.module_from_spec(spec)
spec.loader.exec_module(strat)

data_path = (
    Path(__file__).parent.parent
    / "data_cache" / "dukascopy" / "m15" / "XAUUSD"
    / "xauusd_m15_2020-01-01_2026-04-18.parquet"
)

df = pd.read_parquet(data_path).sort_index()
df['atr'] = strat._atr(df, strat.ATR_PERIOD)
df_h4 = df.resample('4h').agg({'open':'first','high':'max','low':'min','close':'last'}).dropna()
df_h4['ema20'] = strat._ema(df_h4, strat.H4_EMA_PERIOD)
h4_times = df_h4.index.asi8
h4_ema20 = df_h4['ema20'].to_numpy()

r = strat.run(df, h4_times, h4_ema20)
tdf = r['trades_df']

print("=" * 65)
print("Win/Loss analysis by session  (TP=12R, Risk=$100)")
print("=" * 65)

for sn in ['asian', 'london', 'ny']:
    s = tdf[tdf['session'] == sn]
    wins   = s[s['pnl'] > 0]
    losses = s[s['pnl'] <= 0]
    avg_w  = wins['pnl'].mean()   if len(wins)   else 0
    avg_l  = losses['pnl'].mean() if len(losses) else 0
    print(f"\n  {sn.upper()}  N={len(s)}  WR={len(wins)/len(s):.1%}  PnL=${s['pnl'].sum():,.0f}")
    print(f"    Wins  : {len(wins):3d}  avg=+${avg_w:,.0f}")
    print(f"    Losses: {len(losses):3d}  avg=${avg_l:,.0f}  total=${losses['pnl'].sum():,.0f}")
    print(f"    By year:")
    for yr in sorted(s['year'].unique()):
        sy = s[s['year'] == yr]
        w = (sy['pnl'] > 0).sum()
        l = (sy['pnl'] <= 0).sum()
        pnl = sy['pnl'].sum()
        mark = 'OK  ' if pnl >= 0 else 'LOSS'
        print(f"      {yr}: W={w:2d} L={l:2d}  PnL=${pnl:>7,.0f}  [{mark}]")

print()
print("=" * 65)
print("Loss count summary (absolute losses by session):")
print("=" * 65)
for sn in ['asian', 'london', 'ny']:
    s  = tdf[tdf['session'] == sn]
    l  = (s['pnl'] <= 0).sum()
    lt = s[s['pnl'] <= 0]['pnl'].sum()
    print(f"  {sn:<8}: {l:3d} losses  total_loss=${lt:,.0f}")

print()
print("Profit factor by session:")
for sn in ['asian', 'london', 'ny']:
    s  = tdf[tdf['session'] == sn]
    gw = s[s['pnl'] > 0]['pnl'].sum()
    gl = s[s['pnl'] <= 0]['pnl'].abs().sum()
    pf = gw / gl if gl > 0 else float('inf')
    print(f"  {sn:<8}: PF={pf:.2f}  gross_win=${gw:,.0f}  gross_loss=${gl:,.0f}")
