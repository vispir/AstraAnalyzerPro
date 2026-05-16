import sys, os, importlib.util
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pandas as pd
from pathlib import Path

_sp = Path(__file__).parent.parent / "astra_v2/strategies/session_long_nolookahead_v1.py"
spec = importlib.util.spec_from_file_location("strat", _sp)
strat = importlib.util.module_from_spec(spec)
spec.loader.exec_module(strat)

df = pd.read_parquet(
    Path(__file__).parent.parent / "data_cache/dukascopy/m15/XAUUSD/xauusd_m15_2020-01-01_2026-05-04.parquet"
)
df.index = pd.to_datetime(df.index, utc=True)
df["atr"] = strat._atr(df, strat.ATR_PERIOD)
df_h4 = df.resample("4h").agg({"open":"first","high":"max","low":"min","close":"last"}).dropna()
df_h4["ema20"] = strat._ema(df_h4, strat.H4_EMA_PERIOD)

r = strat.run(df, df_h4.index.asi8, df_h4["ema20"].to_numpy())
tdf = r["trades_df"]

for label, subset in [("2026 (Jan-May)", tdf[tdf["year"]==2026]), ("2020-2026 ALL", tdf)]:
    print("=" * 60)
    print(f"  {label}  |  Total trades: {len(subset)}")
    print("=" * 60)
    print(f"  {'Session':<10} {'N':>4} {'WR':>6} {'PnL':>9} {'AvgWin':>8} {'AvgLoss':>8}")
    print("  " + "-" * 50)
    for sess in ["asian", "london", "ny"]:
        g = subset[subset["session"] == sess]
        if len(g) == 0:
            continue
        n   = len(g)
        wr  = (g["pnl"] > 0).sum() / n
        pnl = g["pnl"].sum()
        aw  = g.loc[g["pnl"] > 0, "pnl"].mean() if (g["pnl"] > 0).any() else 0
        al  = g.loc[g["pnl"] < 0, "pnl"].mean() if (g["pnl"] < 0).any() else 0
        print(f"  {sess.upper():<10} {n:>4} {wr:>6.1%} ${pnl:>8,.0f} ${aw:>7,.0f} ${al:>7,.0f}")
    print()
