"""
Range Breakout Strategy v1 (Stateless)

Concept:
  1. Detect consolidation range (local high/low over 20 bars with size < 1.5 ATR).
  2. Check last 2-3 bars for breakout confirmation:
     - Bar[-2] closed beyond range boundary (first breakout)
     - Bar[-1] also closed beyond boundary (confirmation)
     - If bar[-1] is doji, check bar[0] (current) for third confirmation
  3. Entry on current bar if confirmed.

No state needed — all logic based on recent bars.
"""

from __future__ import annotations

from typing import Optional
from datetime import datetime

import pandas as pd

from astra_v2 import config
from astra_v2.core.signal_gate import Signal
from astra_v2.strategies.base import StrategyContext


class RangeBreakoutStrategyV1:
    strategy_id = "range_breakout_v1"
    required_level_types = ()
    required_timeframes = ()
    supports_live_execution = False

    def _compute_atr(self, bars: pd.DataFrame, period: int = 20) -> Optional[float]:
        if len(bars) < period + 1:
            return None
        recent = bars.tail(period + 1)
        highs = recent["high"].astype(float)
        lows = recent["low"].astype(float)
        closes = recent["close"].astype(float)
        prev_close = closes.shift(1)
        tr = pd.concat([
            highs - lows,
            (highs - prev_close).abs(),
            (lows - prev_close).abs()
        ], axis=1).max(axis=1)
        atr = tr.iloc[1:].mean()
        if pd.isna(atr) or atr <= 0:
            return None
        return float(atr)

    def _detect_range(self, bars: pd.DataFrame, atr: float) -> Optional[dict]:
        """
        Detect consolidation range: local high/low over LOOKBACK bars
        where range size < 4.0 × ATR (tight consolidation).

        Optional quality filters (if USE_CONSOLIDATION_FILTER=True):
        - Min 2 touches of each boundary
        - Min 70% bars closed inside range
        - No large candles (body > 1.5 ATR) inside range
        """
        lookback = config.RANGE_BREAKOUT_V1_CONSOLIDATION_LOOKBACK
        if len(bars) < lookback:
            return None

        recent = bars.tail(lookback)
        range_high = float(recent["high"].max())
        range_low = float(recent["low"].min())
        range_size = range_high - range_low

        # Range must be tight (< 4.0 ATR)
        if range_size > 4.0 * atr:
            return None

        # Optional consolidation quality filters
        if config.RANGE_BREAKOUT_V1_USE_CONSOLIDATION_FILTER:
            # 1. Count boundary touches (high within 0.1 ATR of range_high, low within 0.1 ATR of range_low)
            touch_threshold = 0.1 * atr
            high_touches = ((recent["high"] >= range_high - touch_threshold)).sum()
            low_touches = ((recent["low"] <= range_low + touch_threshold)).sum()

            if high_touches < config.RANGE_BREAKOUT_V1_MIN_BOUNDARY_TOUCHES:
                return None
            if low_touches < config.RANGE_BREAKOUT_V1_MIN_BOUNDARY_TOUCHES:
                return None

            # 2. Check bars closed inside range
            bars_inside = ((recent["close"] > range_low) & (recent["close"] < range_high)).sum()
            bars_inside_pct = bars_inside / len(recent)
            if bars_inside_pct < config.RANGE_BREAKOUT_V1_MIN_BARS_INSIDE_PCT:
                return None

            # 3. Check for large candles inside range
            for _, bar in recent.iterrows():
                body = abs(float(bar["close"]) - float(bar["open"]))
                if body > config.RANGE_BREAKOUT_V1_MAX_CANDLE_BODY_ATR * atr:
                    return None

        return {
            "range_high": range_high,
            "range_low": range_low,
            "range_size": range_size,
        }

    def _is_doji(self, bar: pd.Series, atr: float) -> bool:
        """Check if bar is doji (body < 0.15 ATR)."""
        o = float(bar.get("open", 0))
        c = float(bar.get("close", 0))
        body = abs(c - o)
        return body < 0.15 * atr

    def _get_session_label(self, now: datetime) -> str:
        hour = now.hour
        if 7 <= hour < 12:
            return "london"
        elif 13 <= hour < 17:
            return "new_york"
        else:
            return "other"

    def generate_signal(self, context: StrategyContext, *, supabase_client=None):
        """
        Generate range breakout signal (stateless — checks last 2-3 bars).
        """
        bars = context.bars_so_far
        current_price = context.current_price
        now = context.now

        # Check session
        session_label = self._get_session_label(now)
        if session_label not in config.RANGE_BREAKOUT_V1_ALLOWED_SESSIONS:
            return None, f"gate_session: {session_label}"

        # Need enough bars (lookback + 3 for confirmation)
        min_bars = config.RANGE_BREAKOUT_V1_CONSOLIDATION_LOOKBACK + 3
        if len(bars) < min_bars:
            return None, "insufficient_bars"

        # Compute ATR
        atr = self._compute_atr(bars)
        if not atr or atr <= 0:
            return None, "no_atr"

        # Detect range (excluding last 3 bars to avoid look-ahead)
        range_bars = bars.iloc[:-3]
        range_data = self._detect_range(range_bars, atr)
        if not range_data:
            return None, "no_range"

        range_high = range_data["range_high"]
        range_low = range_data["range_low"]

        # Get last 3 closed bars
        b1 = bars.iloc[-3]  # first breakout bar
        b2 = bars.iloc[-2]  # second bar (confirmation or doji)
        b3 = bars.iloc[-1]  # third bar (if doji case)

        c1 = float(b1["close"])
        c2 = float(b2["close"])
        c3 = float(b3["close"])

        # Check for two-candle breakout
        breakout_up = c1 > range_high and c2 > range_high
        breakout_down = c1 < range_low and c2 < range_low

        if not (breakout_up or breakout_down):
            return None, "no_breakout"

        direction = "BUY" if breakout_up else "SELL"

        # Check if b2 is doji — need third candle confirmation
        if self._is_doji(b2, atr):
            # Check b3 for confirmation
            if breakout_up and c3 <= range_high:
                return None, "doji_third_failed"
            if breakout_down and c3 >= range_low:
                return None, "doji_third_failed"

        # Confirmed! Calculate entry, stop and target
        # FIX: Use last closed bar price (bars[-1]) instead of current_price to avoid look-ahead bias
        entry_price = float(bars.iloc[-1]["close"])

        if direction == "BUY":
            stop_loss = range_low - config.RANGE_BREAKOUT_V1_STOP_BUFFER_ATR * atr
            risk = entry_price - stop_loss
        else:
            stop_loss = range_high + config.RANGE_BREAKOUT_V1_STOP_BUFFER_ATR * atr
            risk = stop_loss - entry_price

        if risk <= 0:
            return None, "invalid_risk"

        take_profit = entry_price + (risk * config.RANGE_BREAKOUT_V1_TP_RR if direction == "BUY" else -risk * config.RANGE_BREAKOUT_V1_TP_RR)
        partial_tp = entry_price + (risk * config.RANGE_BREAKOUT_V1_PARTIAL_CLOSE_RR if direction == "BUY" else -risk * config.RANGE_BREAKOUT_V1_PARTIAL_CLOSE_RR)

        # Create dummy level for Signal
        from astra_v2.core.technical_engine import ActiveLevel, KeyLevel
        level = ActiveLevel(
            level=KeyLevel(
                price=range_high if direction == "BUY" else range_low,
                level_type="range_boundary",
                direction="support" if direction == "BUY" else "resistance",
                strength=7.0,
            ),
            distance_usd=abs(entry_price - (range_high if direction == "BUY" else range_low)),
        )

        signal = Signal(
            direction=direction,
            entry_price=entry_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            partial_tp=partial_tp,
            level=level,
            macro_bias=context.macro,
            combined_confidence=0.70,
            strategy_id="range_breakout_v1",
            setup_family="range_breakout_v1",
            session_label=session_label,
        )

        return signal, "range_breakout_confirmed"
