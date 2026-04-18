"""
Debug script for range_breakout_v1 strategy.
Shows why no trades are generated.
"""

import sys
import os
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from astra_v2 import config
from astra_v2.data.dukascopy import load as load_bars_xau, load_timeframe
from astra_v2.data.fred_client import fetch_all as fetch_fred_bulk
from astra_v2.data.external import fetch_yfinance_bulk, fetch_cot_gold
from astra_v2.strategies import get_strategy
from astra_v2.strategies.base import StrategyContext
from astra_v2.core.macro_engine import MacroBias

print("Loading data...")
bars = load_bars_xau(start="2024-01-01", end="2024-12-31")
h4_bars = load_timeframe("H4", start="2024-01-01", end="2024-12-31", symbol="XAUUSD")
print(f"M15: {len(bars):,} bars, H4: {len(h4_bars):,} bars")

strategy = get_strategy("range_breakout_v1")

# Test on a few sample bars
test_indices = [1000, 2000, 3000, 4000, 5000]
reasons = {}

for idx in test_indices:
    if idx >= len(bars):
        continue

    bars_so_far = bars.iloc[:idx]
    current_bar = bars.iloc[idx]
    current_price = float(current_bar["close"])
    now = current_bar.name.to_pydatetime()

    ctx = StrategyContext(
        strategy_id="range_breakout_v1",
        now=now,
        current_price=current_price,
        current_bar=current_bar,
        bars_so_far=bars_so_far,
        levels=[],
        macro=MacroBias(direction="NEUTRAL", confidence=0.5, reasoning="test"),
        h4_bars=h4_bars.iloc[:idx//16] if len(h4_bars) > idx//16 else None,
    )

    signal, reason = strategy.generate_signal(ctx)

    if reason not in reasons:
        reasons[reason] = 0
    reasons[reason] += 1

    if signal:
        print(f"\n✅ SIGNAL at bar {idx} ({now}): {signal.direction} @ {signal.entry:.2f}")
        print(f"   Reason: {reason}")
        print(f"   SL: {signal.stop_loss:.2f}, TP: {signal.take_profit:.2f}")

print("\n" + "="*80)
print("REJECTION REASONS:")
print("="*80)
for reason, count in sorted(reasons.items(), key=lambda x: -x[1]):
    print(f"{reason:30s}: {count:5d}")
