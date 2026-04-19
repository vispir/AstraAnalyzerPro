"""
Impulse Retest Strategy v1 (Stateless)

Concept:
  1. Detect impulse candle: body > 2.0 ATR (strong directional move)
  2. Wait for retracement: price returns to 30-50% of impulse range (Fib zone)
  3. Confirmation: next candle closes in impulse direction
  4. Entry: on open of candle after confirmation
  5. SL: beyond opposite end of retracement + 1.0 ATR buffer
  6. TP: 2.0R, trailing stop: 0.5 ATR after 1.5R

No state needed — all logic based on recent bars.
"""

from __future__ import annotations

from typing import Optional
from datetime import datetime

import pandas as pd

from astra_v2 import config
from astra_v2.core.signal_gate import Signal
from astra_v2.strategies.base import StrategyContext


class ImpulseRetestStrategyV1:
    strategy_id = "impulse_retest_v1"
    required_level_types = ()
    required_timeframes = ("H4",)
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

    def _detect_impulse(self, bars: pd.DataFrame, atr: float) -> Optional[dict]:
        """
        Detect impulse candle: body > MIN_IMPULSE_BODY_ATR × ATR.
        Returns impulse candle data if found.
        """
        if len(bars) < config.IMPULSE_RETEST_V1_LOOKBACK_BARS:
            return None

        # Check recent bars for impulse
        recent = bars.tail(config.IMPULSE_RETEST_V1_LOOKBACK_BARS)

        for idx in range(len(recent) - 1, -1, -1):
            bar = recent.iloc[idx]
            o = float(bar["open"])
            c = float(bar["close"])
            h = float(bar["high"])
            l = float(bar["low"])

            body = abs(c - o)

            # Check if body > threshold
            if body > config.IMPULSE_RETEST_V1_MIN_IMPULSE_BODY_ATR * atr:
                direction = "bullish" if c > o else "bearish"

                return {
                    "direction": direction,
                    "high": h,
                    "low": l,
                    "open": o,
                    "close": c,
                    "body": body,
                    "bars_ago": len(recent) - 1 - idx,
                }

        return None

    def _check_retracement(self, bars: pd.DataFrame, impulse: dict, atr: float) -> Optional[dict]:
        """
        Check if price has retraced to 30-50% Fib zone of impulse range.
        Returns retracement data if found.
        """
        if impulse["bars_ago"] == 0:
            return None  # Need at least 1 bar after impulse

        # Get bars after impulse
        bars_after_impulse = bars.tail(impulse["bars_ago"])

        if len(bars_after_impulse) < 2:
            return None

        # Calculate Fib zone
        impulse_range = impulse["high"] - impulse["low"]

        if impulse["direction"] == "bullish":
            # For bullish impulse, retracement goes down
            fib_50 = impulse["high"] - (impulse_range * 0.5)
            fib_30 = impulse["high"] - (impulse_range * 0.3)

            # Check if any bar touched the Fib zone
            for idx in range(len(bars_after_impulse)):
                bar = bars_after_impulse.iloc[idx]
                low = float(bar["low"])
                high = float(bar["high"])

                # Check if bar touched Fib zone
                if low <= fib_30 and high >= fib_50:
                    return {
                        "fib_50": fib_50,
                        "fib_30": fib_30,
                        "retracement_bar_idx": idx,
                    }
        else:
            # For bearish impulse, retracement goes up
            fib_50 = impulse["low"] + (impulse_range * 0.5)
            fib_30 = impulse["low"] + (impulse_range * 0.3)

            # Check if any bar touched the Fib zone
            for idx in range(len(bars_after_impulse)):
                bar = bars_after_impulse.iloc[idx]
                low = float(bar["low"])
                high = float(bar["high"])

                # Check if bar touched Fib zone
                if high >= fib_30 and low <= fib_50:
                    return {
                        "fib_50": fib_50,
                        "fib_30": fib_30,
                        "retracement_bar_idx": idx,
                    }

        return None

    def _check_confirmation(self, bars: pd.DataFrame, impulse: dict, retracement: dict) -> bool:
        """
        Check if the bar after retracement closes in impulse direction.
        """
        bars_after_impulse = bars.tail(impulse["bars_ago"])

        if retracement["retracement_bar_idx"] >= len(bars_after_impulse) - 1:
            return False  # Need at least one bar after retracement

        confirmation_bar = bars_after_impulse.iloc[retracement["retracement_bar_idx"] + 1]
        o = float(confirmation_bar["open"])
        c = float(confirmation_bar["close"])

        if impulse["direction"] == "bullish":
            return c > o  # Bullish close
        else:
            return c < o  # Bearish close

    def generate_signal(self, context: StrategyContext, *, supabase_client=None):
        """
        Generate impulse retest signal (stateless — checks recent H4 bars).
        """
        h4_bars = context.h4_bars
        if h4_bars is None or h4_bars.empty:
            return None, "no_h4_data"

        current_price = context.current_price
        now = context.now

        # Need enough H4 bars
        min_bars = config.IMPULSE_RETEST_V1_ATR_PERIOD + config.IMPULSE_RETEST_V1_LOOKBACK_BARS + 5
        if len(h4_bars) < min_bars:
            return None, "insufficient_h4_bars"

        # Compute ATR on H4
        atr = self._compute_atr(h4_bars, config.IMPULSE_RETEST_V1_ATR_PERIOD)
        if not atr or atr <= 0:
            return None, "no_atr"

        # Detect impulse on H4
        impulse = self._detect_impulse(h4_bars, atr)
        if not impulse:
            return None, "no_impulse"

        # Check retracement on H4
        retracement = self._check_retracement(h4_bars, impulse, atr)
        if not retracement:
            return None, "no_retracement"

        # Check confirmation on H4
        if not self._check_confirmation(h4_bars, impulse, retracement):
            return None, "no_confirmation"

        # Generate signal
        direction = "BUY" if impulse["direction"] == "bullish" else "SELL"

        # Entry: last closed H4 bar close
        entry_price = float(h4_bars.iloc[-1]["close"])

        # SL: beyond opposite end of retracement + buffer
        if direction == "BUY":
            # SL below retracement low
            retracement_low = retracement["fib_50"]  # Use Fib 50 as reference
            stop_loss = retracement_low - (config.IMPULSE_RETEST_V1_STOP_BUFFER_ATR * atr)
            risk = entry_price - stop_loss
        else:
            # SL above retracement high
            retracement_high = retracement["fib_50"]
            stop_loss = retracement_high + (config.IMPULSE_RETEST_V1_STOP_BUFFER_ATR * atr)
            risk = stop_loss - entry_price

        if risk <= 0:
            return None, "invalid_risk"

        # TP: 2.0R
        take_profit = entry_price + (risk * config.IMPULSE_RETEST_V1_TP_RR if direction == "BUY" else -risk * config.IMPULSE_RETEST_V1_TP_RR)

        # Partial TP: 1.0R
        partial_tp = entry_price + (risk * config.IMPULSE_RETEST_V1_PARTIAL_CLOSE_RR if direction == "BUY" else -risk * config.IMPULSE_RETEST_V1_PARTIAL_CLOSE_RR)

        # Create dummy level for Signal
        from astra_v2.core.technical_engine import ActiveLevel, KeyLevel
        level = ActiveLevel(
            level=KeyLevel(
                price=retracement["fib_50"],
                level_type="fib_retracement",
                direction="support" if direction == "BUY" else "resistance",
                strength=7.0,
            ),
            distance_usd=abs(entry_price - retracement["fib_50"]),
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
            strategy_id="impulse_retest_v1",
            setup_family="impulse_retest_v1",
            session_label="any",
            size_multiplier=1.0,
        )

        return signal, "impulse_retest_confirmed"
