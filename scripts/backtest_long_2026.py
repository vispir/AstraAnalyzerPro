"""LONG-only backtest for 2026 Jan-May."""
import sys, os, importlib.util
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
from pathlib import Path

_sp = Path(__file__).parent.parent / "astra_v2" / "strategies" / "session_long_nolookahead_v1.py"
spec = importlib.util.spec_from_file_location("strat", _sp)
strat = importlib.util.module_from_spec(spec)
spec.loader.exec_module(strat)

df = pd.read_parquet(
    Path(__file__).parent.parent / "data_cache" / "dukascopy" / "m15" / "XAUUSD"
    / "xauusd_m15_2020-01-01_2026-05-04.parquet"
)
df.index = pd.to_datetime(df.index, utc=True)

# Slice to 2026 only, but keep 2020+ for H4 EMA warmup
df_full = df.copy()
df_2026 = df[df.index >= pd.Timestamp("2026-01-01", tz="UTC")].copy()

print(f"Data 2026: {df_2026.index[0].date()} -- {df_2026.index[-1].date()}  ({len(df_2026):,} bars)")

# Run on full data then filter trades to 2026
r = strat.run(df_full)
tl = r["trades"]

# Filter to 2026 trades only
import numpy as np
tl["date"] = pd.to_datetime(tl.get("year", tl.index), utc=True) if "year" in tl.columns else None

# Use year column if available, else filter by index position
if "year" in tl.columns:
    tl_2026 = tl[tl["year"] == 2026]
else:
    tl_2026 = tl  # fallback

n = len(tl_2026)
wr = (tl_2026["pnl"] > 0).sum() / n if n else 0
pnl = tl_2026["pnl"].sum() if n else 0

print()
print("=" * 50)
print("  LONG-ONLY  |  2026 (Jan - May)")
print("=" * 50)
print(f"  Trades     : {n}")
print(f"  Win Rate   : {wr:.1%}")
print(f"  PnL        : ${pnl:,.0f}")
print(f"  MaxDD      : {r['max_dd']:.2f}%  (full period)")
print(f"  MaxDailyDD : {r['max_dly_dd']:.2f}%  (full period)")
print()

if n and "session" in tl_2026.columns:
    print("  By session:")
    for sess, grp in tl_2026.groupby("session"):
        ns = len(grp)
        ws = (grp["pnl"] > 0).sum() / ns
        ps = grp["pnl"].sum()
        print(f"    {sess:<10}  N={ns:3d}  WR={ws:.1%}  PnL=${ps:>7,.0f}")

print()
print("  All 2026 trades:")
print(f"  {'Session':<12}  {'PnL':>8}  {'W?':>4}")
print("  " + "-" * 30)
for _, t in tl_2026.iterrows():
    w = "WIN" if t["pnl"] > 0 else "loss"
    sess = t.get("session", "?")
    print(f"  {sess:<12}  ${t['pnl']:>7,.0f}  {w}")
print("=" * 50)
