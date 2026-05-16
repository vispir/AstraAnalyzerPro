"""FULL PERIOD BACKTEST | 2020-01 to 2026-05 | Fixed Lot 0.02

Formula:
  Dynamic sizing: size = RISK / rsk  (RISK=$100, rsk=entry-sl in USD)
  Price move:     move = pnl / size
  Fixed 0.02 lot: new_pnl = move * 0.02 * 100  ($2 per $1 XAUUSD move)
"""
import sys, os, importlib.util
import pandas as pd
import numpy as np
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DATA_FILE  = Path(__file__).parent.parent / "data_cache" / "dukascopy" / "m15" / "XAUUSD" / "xauusd_m15_2020-01-01_2026-05-04.parquet"
STRAT_PATH = Path(__file__).parent.parent / "astra_v2" / "strategies" / "session_long_nolookahead_v1.py"

if not DATA_FILE.exists():
    raise FileNotFoundError(f"Data not found: {DATA_FILE}")

spec = importlib.util.spec_from_file_location("strat", STRAT_PATH)
strat = importlib.util.module_from_spec(spec)
spec.loader.exec_module(strat)

FIXED_LOT          = 0.02
TICK_VALUE_PER_LOT = 100.0   # $100 per 1 USD move per 1.0 lot
DOLLAR_PER_MOVE    = FIXED_LOT * TICK_VALUE_PER_LOT  # = $2 per $1 XAUUSD move
INITIAL_BALANCE    = 10_000.0
STATIC_FLOOR       = 9_000.0


def main():
    print("Loading data (2020-01 to 2026-05)...")
    df = pd.read_parquet(DATA_FILE)
    df.index = pd.to_datetime(df.index, utc=True)
    print(f"  Range: {df.index[0].date()} -> {df.index[-1].date()} ({len(df):,} bars)")

    print("Computing ATR & H4 EMA...")
    df['atr'] = strat._atr(df, strat.ATR_PERIOD)
    df_h4 = df.resample('4h').agg({'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last'}).dropna()
    df_h4['ema20'] = strat._ema(df_h4, strat.H4_EMA_PERIOD)
    h4_times = df_h4.index.asi8
    h4_ema20 = df_h4['ema20'].to_numpy()

    print("Running backtest (dynamic sizing for trade signals)...")
    results = strat.run(df, h4_times, h4_ema20)
    trades = results.get("trades_df", pd.DataFrame())

    if trades.empty:
        print("No trades returned.")
        return

    # ── Recalculate PnL for fixed lot 0.02 ────────────────────────────────────
    # price_move = pnl / size  (exact: same move that triggered TP/SL/trail)
    # new_pnl    = price_move * FIXED_LOT * TICK_VALUE_PER_LOT
    trades = trades.copy()
    trades['price_move'] = trades['pnl'] / trades['size']
    trades['pnl_fixed']  = trades['price_move'] * DOLLAR_PER_MOVE

    # also compute risk per trade for 0.02 lot (= sl distance * $2)
    trades['rsk_pts']    = trades['entry'] - trades['initial_sl']
    trades['risk_fixed'] = trades['rsk_pts'] * DOLLAR_PER_MOVE

    n        = len(trades)
    wr       = (trades['pnl_fixed'] > 0).sum() / n
    total    = trades['pnl_fixed'].sum()
    avg_win  = trades.loc[trades['pnl_fixed'] > 0, 'pnl_fixed'].mean()
    avg_loss = trades.loc[trades['pnl_fixed'] < 0, 'pnl_fixed'].mean()
    avg_risk = trades['risk_fixed'].mean()
    max_risk = trades['risk_fixed'].max()

    # Equity curve
    equity        = INITIAL_BALANCE + trades['pnl_fixed'].cumsum()
    min_balance   = equity.min()
    peak_eq       = equity.cummax()
    dd_series     = (peak_eq - equity) / peak_eq * 100
    max_dd        = dd_series.max()

    # Static floor check (Funding Pips: floor = 90% of initial, never moves)
    static_dd_pct = (INITIAL_BALANCE - equity) / INITIAL_BALANCE * 100
    max_static_dd = static_dd_pct.max()
    breached      = min_balance < STATIC_FLOOR
    all_pos       = results.get("all_pos", False)

    # ── Report ─────────────────────────────────────────────────────────────────
    print()
    print("=" * 65)
    print(f"  FIXED LOT {FIXED_LOT} BACKTEST  |  2020-01 to 2026-05")
    print(f"  ${DOLLAR_PER_MOVE:.0f} per $1 XAUUSD move  |  Funding Pips 2-Step")
    print("=" * 65)
    print(f"  Trades         : {n}")
    print(f"  Win Rate       : {wr:.1%}")
    print(f"  Total PnL      : ${total:,.0f}")
    print(f"  Avg Win        : ${avg_win:,.0f}")
    print(f"  Avg Loss       : ${avg_loss:,.0f}")
    print(f"  Avg Risk/trade : ${avg_risk:.0f}  (max ${max_risk:.0f})")
    print(f"  Min Balance    : ${min_balance:,.0f}")
    print(f"  Static floor   : ${STATIC_FLOOR:,.0f}  {'OK' if not breached else 'BREACHED!'}")
    print(f"  MaxDD (peak)   : {max_dd:.2f}%   {'OK' if max_dd < 10 else 'FAIL'}")
    print(f"  MaxDD (static) : {max_static_dd:.2f}%   {'OK' if max_static_dd < 10 else 'FAIL'}")
    print(f"  All years +    : {'YES' if all_pos else 'NO'}")
    print("=" * 65)

    # Yearly
    yearly = trades.groupby('year')['pnl_fixed'].sum()
    print()
    print(f"  {'Year':<6}  {'PnL':>10}  {'Status':>8}")
    print("  " + "-" * 28)
    for yr in sorted(yearly.index):
        v = yearly[yr]
        print(f"  {yr:<6}  ${v:>9,.0f}  {'PROFIT' if v > 0 else 'LOSS'}")

    # By session
    if 'session' in trades.columns:
        print()
        print(f"  {'Session':<10}  {'N':>4}  {'WR':>6}  {'PnL':>10}  {'AvgRisk':>9}")
        print("  " + "-" * 45)
        for sess, grp in trades.groupby('session'):
            ns = len(grp)
            ws = (grp['pnl_fixed'] > 0).sum() / ns
            ps = grp['pnl_fixed'].sum()
            ar = grp['risk_fixed'].mean()
            print(f"  {sess:<10}  {ns:>4}  {ws:>6.1%}  ${ps:>9,.0f}  ${ar:>7.0f}")

    # Verdict
    passed = (not breached) and (max_dd < 10) and (all_pos) and (total > 0)
    print()
    print("=" * 65)
    print(f"  VERDICT: {'PASS - READY FOR FUNDING PIPS' if passed else 'FAIL - NEEDS REVIEW'}")
    print("=" * 65)

    # CSV
    csv_out = Path(__file__).parent / "fixed_lot_0.02_backtest_2020_2026.csv"
    trades.assign(
        equity=equity.values,
        dd_pct=dd_series.values,
        static_dd_pct=static_dd_pct.values,
    ).to_csv(csv_out, index=False, encoding="utf-8-sig")
    print(f"\nSaved: {csv_out}")


if __name__ == "__main__":
    main()
