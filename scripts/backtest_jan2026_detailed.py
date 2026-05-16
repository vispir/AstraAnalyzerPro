"""Detailed equity curve for 2026 starting from Jan 1."""
import sys, os, importlib.util
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
import numpy as np
from pathlib import Path

_sp = Path(__file__).parent.parent / "astra_v2/strategies/session_long_nolookahead_v1.py"
spec = importlib.util.spec_from_file_location("strat", _sp)
strat = importlib.util.module_from_spec(spec)
spec.loader.exec_module(strat)

df = pd.read_parquet(
    Path(__file__).parent.parent / "data_cache/dukascopy/m15/XAUUSD/xauusd_m15_2020-01-01_2026-05-08.parquet"
)
df.index = pd.to_datetime(df.index, utc=True)
df["atr"] = strat._atr(df, strat.ATR_PERIOD)
df_h4 = df.resample("4h").agg({"open":"first","high":"max","low":"min","close":"last"}).dropna()
df_h4["ema20"] = strat._ema(df_h4, strat.H4_EMA_PERIOD)

r = strat.run(df, df_h4.index.asi8, df_h4["ema20"].to_numpy())
tdf = r["trades_df"]

# 2026 trades only, running equity from $10,000
t26 = tdf[tdf["year"] == 2026].copy()
t26 = t26.reset_index(drop=True)
t26["equity"] = 10_000 + t26["pnl"].cumsum()
t26["trade_num"] = range(1, len(t26)+1)

print("=" * 70)
print("  EQUITY CURVE 2026 — каждая сделка с $10,000 старта")
print("=" * 70)
print(f"  {'#':<3} {'Sess':<8} {'PnL':>8} {'Баланс':>10} {'MinBal':>8} {'Result'}")
print("  " + "-" * 55)

min_balance = 10_000.0
for _, t in t26.iterrows():
    eq = t["equity"]
    if eq < min_balance:
        min_balance = eq
    result = "WIN  " if t["pnl"] > 0 else "loss "
    flag = " <<<" if eq < 9500 else (" <" if eq < 9700 else "")
    print(f"  {int(t['trade_num']):<3} {t['session']:<8} ${t['pnl']:>7,.0f} ${eq:>9,.0f} {result}{flag}")

print("  " + "-" * 55)
print(f"  Минимальный баланс за 2026: ${min_balance:,.0f}")
print(f"  Итоговый баланс:            ${t26['equity'].iloc[-1]:,.0f}")
print(f"  Флор Funding Pips:          $9,000")
print(f"  Минимальный буфер до флора: ${min_balance - 9000:,.0f}")
print("=" * 70)
