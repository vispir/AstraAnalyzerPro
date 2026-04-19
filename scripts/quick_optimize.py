"""Quick optimization test - 3 combinations only"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from astra_v2 import config
from astra_v2.data.dukascopy import load_timeframe
from astra_v2.data.fred_client import fetch_all as fetch_fred_bulk
from astra_v2.data.external import fetch_yfinance_bulk, fetch_cot_gold
from astra_v2.backtest.engine import run_backtest

print("Loading data...", flush=True)
bars = load_timeframe("M15", start="2020-01-01", end="2026-04-18", symbol="XAUUSD")
fred_df = fetch_fred_bulk("2020-01-01", "2026-04-18")
yfinance_df = fetch_yfinance_bulk("2020-01-01", "2026-04-18", cache_only=True)
cot_df = fetch_cot_gold(cache_only=True)
print(f"Data loaded: {len(bars)} bars", flush=True)

# Test 3 combinations
tests = [
    {"max_range": 4.0, "stop": 0.5, "tp": 2.0, "lookback": 20},
    {"max_range": 4.0, "stop": 1.0, "tp": 2.5, "lookback": 25},
    {"max_range": 6.0, "stop": 0.5, "tp": 3.0, "lookback": 20},
]

for i, params in enumerate(tests, 1):
    print(f"\n[{i}/3] Testing: {params}", flush=True)

    config.RANGE_BREAKOUT_V1_MAX_RANGE_ATR = params["max_range"]
    config.RANGE_BREAKOUT_V1_STOP_BUFFER_ATR = params["stop"]
    config.RANGE_BREAKOUT_V1_TP_RR = params["tp"]
    config.RANGE_BREAKOUT_V1_CONSOLIDATION_LOOKBACK = params["lookback"]

    result = run_backtest(
        bars=bars,
        fred_df=fred_df,
        yfinance_df=yfinance_df,
        cot_df=cot_df,
        mode="proxy",
        strategy_id="range_breakout_v1",
        start_balance=10000,
        primary_symbol="XAUUSD",
    )

    s = result.summary()
    passes = s["profit_factor"] >= 1.5 and s["max_drawdown_pct"] < 10.0 and s["max_daily_dd_pct"] < 5.0

    print(f"[{i}/3] {'PASS' if passes else 'FAIL'}: "
          f"PF={s['profit_factor']:.3f}, DD={s['max_drawdown_pct']:.2f}%, "
          f"Daily DD={s['max_daily_dd_pct']:.2f}%, PnL=${s['net_pnl']:,.0f}", flush=True)

print("\nDone!", flush=True)
