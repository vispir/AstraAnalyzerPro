"""
Break & Retest Strategy v1

Concept:
  1. A key level is broken during the CURRENT SESSION/DAY (fresh breakout only).
  2. Price returns to retest the broken level within the same day.
  3. On rejection, enter in the direction of the breakout (trend continuation).

Key edge: trades WITH momentum. Broken resistance → support (buy retest).
          Broken support → resistance (sell retest).

Freshness rule (anti-stale-setup):
  - PDH/PDL: breakout must occur in TODAY's bars (after midnight UTC).
  - weekly_high/low: breakout must occur in THIS WEEK's bars.
  - session_high/low: breakout must occur AFTER the session level was set.

H4 filter: block trades counter to H4 trend.
Entry: next M15 bar open. Stop: 0.20 ATR beyond the level. TP: 2.0R.
"""

from __future__ import annotations

from typing import Optional

import pandas as pd

from astra_v2 import config
from astra_v2.core.signal_gate import Signal
from astra_v2.core.technical_engine import ActiveLevel, KeyLevel
from astra_v2.core.volume_engine import same_hour_rvol
from .base import StrategyContext
from .sweep_reversal_v1 import session_label_for


class BreakoutRetestStrategyV1:
    strategy_id = "breakout_retest_v1"
    required_level_types = config.BREAKOUT_RETEST_V1_TRIGGER_LEVEL_TYPES
    required_timeframes = ("H4",)
    supports_live_execution = False

    def _compute_atr(self, bars: pd.DataFrame) -> Optional[float]:
        period = config.BREAKOUT_RETEST_V1_ATR_PERIOD
        if len(bars) < period + 1:
            return None
        recent = bars.tail(period + 1)
        highs = recent["high"].astype(float)
        lows = recent["low"].astype(float)
        closes = recent["close"].astype(float)
        prev_close = closes.shift(1)
        tr = pd.concat(
            [highs - lows, (highs - prev_close).abs(), (lows - prev_close).abs()],
            axis=1,
        ).max(axis=1)
        atr = tr.tail(period).mean()
        if pd.isna(atr) or atr <= 0:
            return None
        return float(atr)

    def _compute_h4_trend(self, context: StrategyContext) -> Optional[str]:
        if context.h4_bars is None or len(context.h4_bars) < config.BREAKOUT_RETEST_V1_H4_LOOKBACK_BARS:
            return None
        recent = context.h4_bars.tail(config.BREAKOUT_RETEST_V1_H4_LOOKBACK_BARS)
        closes = recent["close"].astype(float)
        last_close = float(closes.iloc[-1])
        trend_ma = closes.tail(config.BREAKOUT_RETEST_V1_H4_TREND_PERIOD).mean()
        return "BULLISH" if last_close >= trend_ma else "BEARISH"

    def _close_in_range(self, bar: pd.Series) -> float:
        rng = max(float(bar["high"]) - float(bar["low"]), 1e-9)
        return (float(bar["close"]) - float(bar["low"])) / rng

    def _breakout_window(
        self, level_type: str, bars_so_far: pd.DataFrame, now
    ) -> pd.DataFrame:
        """
        Return the bars slice in which a 'fresh' breakout must have occurred.
          PDH/PDL         → today's bars only (after 00:00 UTC)
          weekly_high/low → this week's bars (Monday 00:00 UTC onwards)
          session_high/low→ today's bars (session level was set in the same day)
        """
        if level_type in ("pdh", "pdl", "session_high", "session_low"):
            cutoff = pd.Timestamp(now.date(), tz="UTC")
        elif level_type in ("weekly_high", "weekly_low"):
            days_since_monday = now.weekday()  # 0 = Mon
            cutoff = (
                pd.Timestamp(now.date(), tz="UTC") - pd.Timedelta(days=days_since_monday)
            )
        else:
            cutoff = pd.Timestamp(now.date(), tz="UTC")
        return bars_so_far[bars_so_far.index >= cutoff]

    def _find_breakout_bar(
        self,
        level_price: float,
        level_direction: str,
        window: pd.DataFrame,
        breakout_min: float,
    ) -> Optional[pd.Series]:
        """
        Return the first bar in window that confirms a clean breakout of the level,
        or None if no such bar exists.
        """
        if window.empty:
            return None
        closes = window["close"].astype(float)
        if level_direction == "resistance":
            mask = closes >= level_price + breakout_min
        else:
            mask = closes <= level_price - breakout_min
        valid = window[mask]
        return valid.iloc[0] if not valid.empty else None

    def generate_signal(self, context: StrategyContext, *, supabase_client=None):
        session_label = session_label_for(context.now)
        if session_label not in config.BREAKOUT_RETEST_V1_ALLOWED_SESSIONS:
            return None, f"gate_session: {session_label or 'none'}"

        # For M15 execution the actual entry is the NEXT bar (15 min later).
        # Block if the entry bar's hour falls in the restricted window.
        from datetime import timedelta
        entry_hour = (context.now + timedelta(minutes=15)).hour
        if entry_hour in config.BREAKOUT_RETEST_V1_BLOCKED_HOURS_UTC:
            return None, f"gate_hour_block: entry at {entry_hour:02d}:xx UTC"

        if context.local_trade_count >= config.BREAKOUT_RETEST_V1_MAX_TRADES_PER_DAY:
            return None, (
                f"gate_trades: daily limit "
                f"({context.local_trade_count}/{config.BREAKOUT_RETEST_V1_MAX_TRADES_PER_DAY})"
            )

        atr = self._compute_atr(context.bars_so_far)
        if atr is None:
            return None, "gate_atr: insufficient bars"

        h4_trend = self._compute_h4_trend(context)
        # Require H4 data when alignment is enforced
        if config.BREAKOUT_RETEST_V1_H4_REQUIRE_ALIGNMENT and h4_trend is None:
            return None, "gate_h4: no H4 data for alignment check"

        allowed_types = set(config.BREAKOUT_RETEST_V1_TRIGGER_LEVEL_TYPES)
        trigger_levels = [l for l in context.levels if l.level_type in allowed_types]
        if not trigger_levels:
            return None, "gate_levels: no trigger levels"

        breakout_min = atr * config.BREAKOUT_RETEST_V1_BREAKOUT_MIN_ATR
        prox = atr * config.BREAKOUT_RETEST_V1_RETEST_PROXIMITY_ATR
        min_body = atr * config.BREAKOUT_RETEST_V1_REJECTION_MIN_BODY_ATR
        stop_buffer = atr * config.BREAKOUT_RETEST_V1_STOP_BUFFER_ATR

        c_high = float(context.current_bar["high"])
        c_low = float(context.current_bar["low"])
        c_close = float(context.current_bar["close"])
        c_open = float(context.current_bar["open"])
        body = abs(c_close - c_open)
        cir = self._close_in_range(context.current_bar)

        candidates: list[tuple[float, Signal]] = []

        # Retest bar RVOL — computed once, reused for all levels.
        # Maps to a position size_multiplier: high conviction → larger position.
        retest_rvol = same_hour_rvol(
            context.bars_so_far,
            context.now,
            lookback_days=config.BREAKOUT_RETEST_V1_RVOL_LOOKBACK_DAYS,
        )
        if retest_rvol >= config.BREAKOUT_RETEST_V1_RVOL_PANIC_THRESH:
            size_mult = config.BREAKOUT_RETEST_V1_SIZE_PANIC_RVOL
        elif retest_rvol >= config.BREAKOUT_RETEST_V1_RVOL_HIGH_THRESH:
            size_mult = config.BREAKOUT_RETEST_V1_SIZE_HIGH_RVOL
        else:
            size_mult = config.BREAKOUT_RETEST_V1_SIZE_LOW_RVOL

        for level in trigger_levels:
            # Determine window for fresh-breakout check
            window = self._breakout_window(level.level_type, context.bars_so_far, context.now)

            if level.direction == "resistance":
                # ── BULLISH: buy retest of broken resistance ─────────────────
                trade_direction = "BULLISH"

                if config.BREAKOUT_RETEST_V1_H4_REQUIRE_ALIGNMENT:
                    if h4_trend != "BULLISH":
                        continue

                # Current price must be above the level (breakout state)
                if c_close <= level.price:
                    continue

                # Fresh breakout must have occurred in the current day/week
                breakout_bar = self._find_breakout_bar(
                    level.price, "resistance", window, breakout_min
                )
                if breakout_bar is None:
                    continue

                # (RVOL is used as a position size_multiplier, not a binary filter)

                # Retest: bar dipped back into the zone
                if c_low > level.price + prox:
                    continue
                # Rejection: bar closed back above the level
                if c_close <= level.price:
                    continue
                # Bullish candle quality
                if c_close <= c_open:
                    continue
                if body < min_body:
                    continue
                if cir < config.BREAKOUT_RETEST_V1_CLOSE_IN_RANGE_BULL:
                    continue

                stop_loss = level.price - stop_buffer
                risk = c_close - stop_loss
                if risk <= 0:
                    continue
                take_profit = c_close + risk * config.BREAKOUT_RETEST_V1_TP_RR
                partial_tp = c_close + risk * config.BREAKOUT_RETEST_V1_PARTIAL_CLOSE_RR

            else:  # support
                # ── BEARISH: sell retest of broken support ────────────────────
                trade_direction = "BEARISH"

                if config.BREAKOUT_RETEST_V1_H4_REQUIRE_ALIGNMENT:
                    if h4_trend != "BEARISH":
                        continue

                # Current price must be below the level (breakout state)
                if c_close >= level.price:
                    continue

                # Fresh breakout must have occurred in the current day/week
                breakout_bar = self._find_breakout_bar(
                    level.price, "support", window, breakout_min
                )
                if breakout_bar is None:
                    continue

                # (RVOL is used as a position size_multiplier, not a binary filter)

                # Retest: bar rose back into the zone
                if c_high < level.price - prox:
                    continue
                # Rejection: bar closed back below the level
                if c_close >= level.price:
                    continue
                # Bearish candle quality
                if c_close >= c_open:
                    continue
                if body < min_body:
                    continue
                if cir > config.BREAKOUT_RETEST_V1_CLOSE_IN_RANGE_BEAR:
                    continue

                stop_loss = level.price + stop_buffer
                risk = stop_loss - c_close
                if risk <= 0:
                    continue
                take_profit = c_close - risk * config.BREAKOUT_RETEST_V1_TP_RR
                partial_tp = c_close - risk * config.BREAKOUT_RETEST_V1_PARTIAL_CLOSE_RR

            combined_confidence = min(
                0.95,
                level.strength / 10.0
                + (0.10 if h4_trend == trade_direction else 0.0),
            )

            active_level = ActiveLevel(
                level=level, distance_usd=abs(level.price - context.current_price)
            )

            signal = Signal(
                direction=trade_direction,  # type: ignore[arg-type]
                entry_price=c_close,
                stop_loss=stop_loss,
                take_profit=take_profit,
                partial_tp=partial_tp,
                level=active_level,
                macro_bias=context.macro,
                timestamp=context.now,
                combined_confidence=combined_confidence,
                strategy_id=self.strategy_id,
                setup_family="breakout_retest_v1",
                session_label=session_label,
                sweep_side=f"breakout_{'long' if trade_direction == 'BULLISH' else 'short'}",
                sweep_size=0.0,
                confirmation_at=context.now,
                confirmation_type=(
                    f"retest_{'bull' if trade_direction == 'BULLISH' else 'bear'}"
                    f"_h4_{h4_trend or 'na'}"
                    f"_{level.level_type}"
                ),
                bars_since_sweep=0,
                execution_timeframe="M15",
                entry_trigger_price=c_close,
                size_multiplier=size_mult,
            )
            candidates.append((active_level.distance_usd, signal))

        if not candidates:
            return None, "gate_retest: no confirmed same-day breakout+retest"

        _, signal = min(candidates, key=lambda x: x[0])
        return signal, "ok"
