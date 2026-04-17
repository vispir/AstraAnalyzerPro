from __future__ import annotations

from datetime import datetime
from typing import Optional

import pandas as pd

from astra_v2 import config
from astra_v2.core.signal_gate import Signal
from astra_v2.core.technical_engine import ActiveLevel, KeyLevel
from .base import StrategyContext
from .sweep_reversal_v1 import session_label_for


class SweepReversalStrategyV2:
    strategy_id = "sweep_reversal_v2"
    required_level_types = (
        config.SWEEP_REVERSAL_V2_TRIGGER_LEVEL_TYPES
        + config.SWEEP_REVERSAL_V2_CONFLUENCE_LEVEL_TYPES
    )
    required_timeframes = ()
    supports_live_execution = True

    def _trigger_levels(self, levels: list[KeyLevel]) -> list[KeyLevel]:
        return [
            level for level in levels
            if level.level_type in config.SWEEP_REVERSAL_V2_TRIGGER_LEVEL_TYPES
        ]

    def _fib_levels(self, levels: list[KeyLevel]) -> list[KeyLevel]:
        return [
            level for level in levels
            if level.level_type in config.SWEEP_REVERSAL_V2_CONFLUENCE_LEVEL_TYPES
        ]

    def _compute_atr(self, bars: pd.DataFrame) -> Optional[float]:
        period = config.SWEEP_REVERSAL_V2_ATR_PERIOD
        if len(bars) < period + 1:
            return None
        recent = bars.tail(period + 1)
        highs = recent["high"].astype(float)
        lows = recent["low"].astype(float)
        closes = recent["close"].astype(float)
        prev_close = closes.shift(1)
        tr = pd.concat(
            [
                highs - lows,
                (highs - prev_close).abs(),
                (lows - prev_close).abs(),
            ],
            axis=1,
        ).max(axis=1)
        atr = tr.tail(period).mean()
        if pd.isna(atr) or atr <= 0:
            return None
        return float(atr)

    def _regime_multiplier(self, direction: str, context: StrategyContext) -> float:
        macro = context.macro
        if not macro.is_actionable():
            return 1.0
        if macro.direction == direction:
            return config.SWEEP_REVERSAL_V2_ALIGNED_REGIME_MULTIPLIER
        if macro.direction == "NEUTRAL":
            return 1.0
        return config.SWEEP_REVERSAL_V2_COUNTER_REGIME_MULTIPLIER

    def _level_multiplier(self, level: KeyLevel) -> float:
        if level.level_type.startswith("session_"):
            return config.SWEEP_REVERSAL_V2_SESSION_LEVEL_MULTIPLIER
        if level.level_type.startswith("weekly_"):
            return config.SWEEP_REVERSAL_V2_WEEKLY_LEVEL_MULTIPLIER
        if level.level_type == "fib_50":
            return config.SWEEP_REVERSAL_V2_FIB_50_MULTIPLIER
        if level.level_type == "fib_618":
            return config.SWEEP_REVERSAL_V2_FIB_618_MULTIPLIER
        return 1.0

    def _session_multiplier(self, session_label: str) -> float:
        if session_label == "new_york":
            return config.SWEEP_REVERSAL_V2_NY_THRESHOLD_MULTIPLIER
        return 1.0

    def _close_in_range(self, bar: pd.Series) -> float:
        high = float(bar["high"])
        low = float(bar["low"])
        rng = max(high - low, 1e-9)
        return (float(bar["close"]) - low) / rng

    def _confirm_bullish(self, bar: pd.Series, level_price: float, atr: float, threshold_mult: float) -> bool:
        body = abs(float(bar["close"]) - float(bar["open"]))
        reclaim = config.SWEEP_REVERSAL_V2_BASE_RECLAIM_ATR * atr * threshold_mult
        min_body = config.SWEEP_REVERSAL_V2_BULLISH_MIN_BODY_ATR * atr * threshold_mult
        return (
            float(bar["close"]) >= level_price + reclaim
            and float(bar["close"]) > float(bar["open"])
            and body >= min_body
            and self._close_in_range(bar) >= config.SWEEP_REVERSAL_V2_BULLISH_CLOSE_IN_RANGE_MIN
        )

    def _confirm_bearish(self, bar: pd.Series, level_price: float, atr: float, threshold_mult: float) -> bool:
        body = abs(float(bar["close"]) - float(bar["open"]))
        reclaim = config.SWEEP_REVERSAL_V2_BASE_RECLAIM_ATR * atr * threshold_mult
        min_body = config.SWEEP_REVERSAL_V2_BEARISH_MIN_BODY_ATR * atr * threshold_mult
        return (
            float(bar["close"]) <= level_price - reclaim
            and float(bar["close"]) < float(bar["open"])
            and body >= min_body
            and self._close_in_range(bar) <= config.SWEEP_REVERSAL_V2_BEARISH_CLOSE_IN_RANGE_MAX
        )

    def _build_signal(
        self,
        *,
        direction: str,
        entry_price: float,
        active_level: ActiveLevel,
        context: StrategyContext,
        sweep_side: str,
        sweep_size: float,
        confirmation_at: datetime,
        confirmation_type: str,
        bars_since_sweep: int,
        sweep_extreme: float,
        session_label: str,
        atr: float,
        fib_confluence: Optional[KeyLevel] = None,
    ) -> Optional[Signal]:
        stop_buffer = atr * config.SWEEP_REVERSAL_V2_STOP_BUFFER_ATR
        if direction == "BULLISH":
            stop_loss = sweep_extreme - stop_buffer
            risk = entry_price - stop_loss
            if risk <= 0:
                return None
            take_profit = entry_price + risk * config.SWEEP_REVERSAL_V2_TP_RR
            partial_tp = entry_price + risk * config.SWEEP_REVERSAL_V2_PARTIAL_CLOSE_RR
        else:
            stop_loss = sweep_extreme + stop_buffer
            risk = stop_loss - entry_price
            if risk <= 0:
                return None
            take_profit = entry_price - risk * config.SWEEP_REVERSAL_V2_TP_RR
            partial_tp = entry_price - risk * config.SWEEP_REVERSAL_V2_PARTIAL_CLOSE_RR

        combined_confidence = min(
            0.95,
            active_level.strength / 10.0
            + min(sweep_size / max(atr, 1e-9), 2.0) * 0.10
            + (0.05 if fib_confluence is not None else 0.0),
        )

        return Signal(
            direction=direction,  # type: ignore[arg-type]
            entry_price=entry_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            partial_tp=partial_tp,
            level=active_level,
            macro_bias=context.macro,
            timestamp=context.now,
            combined_confidence=combined_confidence,
            strategy_id=self.strategy_id,
            setup_family="sweep_reversal_v2",
            session_label=session_label,
            sweep_side=sweep_side,
            sweep_size=round(sweep_size, 4),
            confirmation_at=confirmation_at,
            confirmation_type=confirmation_type,
            bars_since_sweep=bars_since_sweep,
        )

    def _fib_confluence_for_level(
        self,
        trigger_level: KeyLevel,
        all_levels: list[KeyLevel],
        atr: float,
    ) -> Optional[KeyLevel]:
        max_distance = atr * config.SWEEP_REVERSAL_V2_FIB_CONFLUENCE_DISTANCE_ATR
        candidates = []
        for fib_level in self._fib_levels(all_levels):
            if fib_level.direction != trigger_level.direction:
                continue
            distance = abs(fib_level.price - trigger_level.price)
            if distance <= max_distance:
                candidates.append((distance, fib_level))
        if not candidates:
            return None
        _, fib_level = min(candidates, key=lambda item: item[0])
        return fib_level

    def generate_signal(self, context: StrategyContext, *, supabase_client=None):
        session_label = session_label_for(context.now)
        if session_label not in config.SWEEP_REVERSAL_V2_ALLOWED_SESSIONS:
            return None, f"gate_session: session {session_label or 'none'} not enabled"

        if context.local_trade_count >= config.SWEEP_REVERSAL_V2_MAX_TRADES_PER_DAY:
            return None, (
                f"gate_trades: daily limit reached "
                f"({context.local_trade_count}/{config.SWEEP_REVERSAL_V2_MAX_TRADES_PER_DAY})"
            )

        atr = self._compute_atr(context.bars_so_far)
        if atr is None:
            return None, "gate_atr: insufficient bars for ATR"

        trigger_levels = self._trigger_levels(context.levels)
        if not trigger_levels:
            return None, "gate_levels: no structural trigger levels available"

        window = context.bars_so_far.tail(config.SWEEP_REVERSAL_V2_MAX_CONFIRM_BARS)
        confirmation_rows = [(ts.to_pydatetime(), historical_bar) for ts, historical_bar in window.iterrows()]
        confirmation_rows.append((context.now, context.current_bar))

        candidates: list[tuple[float, Signal]] = []
        for level in trigger_levels:
            direction = "BEARISH" if level.direction == "resistance" else "BULLISH"
            active_level = ActiveLevel(level=level, distance_usd=abs(level.price - context.current_price))
            fib_confluence = self._fib_confluence_for_level(level, context.levels, atr)
            threshold_mult = (
                self._regime_multiplier(direction, context)
                * self._session_multiplier(session_label)
                * self._level_multiplier(level)
            )
            if fib_confluence is not None:
                threshold_mult *= config.SWEEP_REVERSAL_V2_FIB_CONFLUENCE_THRESHOLD_MULTIPLIER
            required_sweep = atr * config.SWEEP_REVERSAL_V2_BASE_SWEEP_ATR * threshold_mult

            for idx, (_ts, bar) in enumerate(confirmation_rows):
                bars_since_sweep = len(confirmation_rows) - idx - 1
                if bars_since_sweep > config.SWEEP_REVERSAL_V2_MAX_CONFIRM_BARS:
                    continue

                bar_high = float(bar["high"])
                bar_low = float(bar["low"])
                if direction == "BEARISH":
                    sweep_size = bar_high - level.price
                    if sweep_size < required_sweep:
                        continue
                    if not self._confirm_bearish(context.current_bar, level.price, atr, threshold_mult):
                        continue
                    signal = self._build_signal(
                        direction=direction,
                        entry_price=context.current_price,
                        active_level=active_level,
                        context=context,
                        sweep_side="above_resistance",
                        sweep_size=sweep_size,
                        confirmation_at=context.now,
                        confirmation_type=(
                            "bearish_reclaim_with_displacement+fib"
                            if fib_confluence is not None
                            else "bearish_reclaim_with_displacement"
                        ),
                        bars_since_sweep=bars_since_sweep,
                        sweep_extreme=bar_high,
                        session_label=session_label,
                        atr=atr,
                        fib_confluence=fib_confluence,
                    )
                else:
                    sweep_size = level.price - bar_low
                    if sweep_size < required_sweep:
                        continue
                    if not self._confirm_bullish(context.current_bar, level.price, atr, threshold_mult):
                        continue
                    signal = self._build_signal(
                        direction=direction,
                        entry_price=context.current_price,
                        active_level=active_level,
                        context=context,
                        sweep_side="below_support",
                        sweep_size=sweep_size,
                        confirmation_at=context.now,
                        confirmation_type=(
                            "bullish_reclaim_with_displacement+fib"
                            if fib_confluence is not None
                            else "bullish_reclaim_with_displacement"
                        ),
                        bars_since_sweep=bars_since_sweep,
                        sweep_extreme=bar_low,
                        session_label=session_label,
                        atr=atr,
                        fib_confluence=fib_confluence,
                    )

                if signal is not None:
                    candidates.append((active_level.distance_usd, signal))
                    break

        if not candidates:
            return None, "gate_sweep: no confirmed sweep reversal setup"

        _, signal = min(candidates, key=lambda item: item[0])
        return signal, "ok"
