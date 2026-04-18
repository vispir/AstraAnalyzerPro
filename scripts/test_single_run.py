"""
Single test run to verify backtest works without crashes.
"""

import sys
import os
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from astra_v2 import config
from astra_v2.data.dukascopy import load as load_bars_xau, load_timeframe
from astra_v2.data.fred_client import fetch_all as fetch_fred_bulk
from astra_v2.data.external import fetch_yfinance_bulk, fetch_cot_gold
from astra_v2.backtest.engine import run_backtest
from astra_v2.strategies import get_strategy


print("="*80)
print("SINGLE TEST RUN - breakout_retest_v1 with default params")
print("="*80)

# Test params
test_params = {
    "RETEST_PROXIMITY_ATR": 0.5,
    "TP_RR": 2.0,
    "REJECTION_MIN_BODY_ATR": 0.10,
    "STOP_BUFFER_ATR": 0.20,
    "PARTIAL_CLOSE_RR": 1.0,
}

print("\nTest parameters:")
for k, v in test_params.items():
    print(f"  {k}: {v}")

print("\nLoading data...")
start_time = datetime.now()

fred_df = fetch_fred_bulk("2020-01-01", "2024-12-31")
yfinance_df = fetch_yfinance_bulk("2020-01-01", "2024-12-31", cache_only=True)
cot_df = fetch_cot_gold(cache_only=True)
bars = load_bars_xau(start="2020-01-01", end="2024-12-31")
h4_bars = load_timeframe("H4", start="2020-01-01", end="2024-12-31", symbol="XAUUSD")

load_time = (datetime.now() - start_time).total_seconds()
print(f"  Data loaded in {load_time:.1f}s")
print(f"  M15: {len(bars):,} bars")
print(f"  H4: {len(h4_bars):,} bars")

# Override config
for key, value in test_params.items():
    setattr(config, f"BREAKOUT_RETEST_V1_{key}", value)

print("\nRunning backtest...")
backtest_start = datetime.now()

strategy = get_strategy("breakout_retest_v1")

result = run_backtest(
    bars=bars,
    fred_df=fred_df,
    yfinance_df=yfinance_df,
    cot_df=cot_df,
    h4_bars=h4_bars,
    mode="proxy",
    strategy_id="breakout_retest_v1",
    start_balance=10000.0,
    wf_train_months=6,
    wf_test_months=1,
    primary_symbol="XAUUSD",
)

backtest_time = (datetime.now() - backtest_start).total_seconds()
summary = result.summary()

print(f"\nBacktest completed in {backtest_time:.1f}s ({backtest_time/60:.1f} minutes)")
print("\n" + "="*80)
print("RESULTS")
print("="*80)
print(f"Total trades: {summary['total_trades']}")
print(f"Trades/week: {summary['trades_per_week']:.1f}")
print(f"Win rate: {summary['win_rate']:.1%}")
print(f"Profit factor: {summary['profit_factor']:.3f}")
print(f"Max drawdown: {summary['max_drawdown_pct']:.2f}%")
print(f"Avg RR: {summary['avg_rr']:.2f}")
print(f"Net PnL: ${summary['net_pnl']:.2f}")
print(f"End balance: ${summary['end_balance']:.2f}")

total_time = (datetime.now() - start_time).total_seconds()
print(f"\nTotal time: {total_time:.1f}s ({total_time/60:.1f} minutes)")
print(f"  Load: {load_time:.1f}s")
print(f"  Backtest: {backtest_time:.1f}s")

print("\n✓ Test completed successfully!")
