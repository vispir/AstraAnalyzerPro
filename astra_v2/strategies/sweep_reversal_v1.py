from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

import pandas as pd

from astra_v2 import config
from astra_v2.core.signal_gate import Signal
from astra_v2.core.technical_engine import ActiveLevel, KeyLevel
from .base import StrategyContext


def session_label_for(dt: datetime) -> Optional[str]:
    hour = dt.hour
    if config.LONDON_OPEN_UTC <= hour < config.LONDON_CLOSE_UTC:
        return "london"
    if config.NY_OPEN_UTC <= hour < config.NY_CLOSE_UTC:
        return "new_york"
    return None


class SweepReversalStrategy:
    strategy_id = "sweep_reversal_v1"
    required_level_types = config.SWEEP_REVERSAL_V1_TRIGGER_LEVEL_TYPES
    required_timeframes = ()
    supports_live_execution = True

    def _trigger_levels(self, levels: list[KeyLevel]) -> list[KeyLevel]:
        return [
            level for level in levels
            if level.level_type in config.SWEEP_REVERSAL_V1_TRIGGER_LEVEL_TYPES
        ]

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
    ) -> Signal:
        stop_buffer = config.SWEEP_REVERSAL_V1_STOP_BUFFER_USD
        if direction == "BULLISH":
            stop_loss = sweep_extreme - stop_buffer
            risk = entry_price - stop_loss
            take_profit = entry_price + risk * config.SWEEP_REVERSAL_V1_TP_RR
            partial_tp = entry_price + risk * config.SWEEP_REVERSAL_V1_PARTIAL_CLOSE_RR
        else:
            stop_loss = sweep_extreme + stop_buffer
            risk = stop_loss - entry_price
            take_profit = entry_price - risk * config.SWEEP_REVERSAL_V1_TP_RR
            partial_tp = entry_price - risk * config.SWEEP_REVERSAL_V1_PARTIAL_CLOSE_RR

        combined_confidence = active_level.strength / 10.0

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
            setup_family="sweep_reversal",
            session_label=session_label,
            sweep_side=sweep_side,
            sweep_size=round(sweep_size, 4),
            confirmation_at=confirmation_at,
            confirmation_type=confirmation_type,
            bars_since_sweep=bars_since_sweep,
        )

    def _confirm_bearish(self, bar: pd.Series, level_price: float) -> bool:
        return (
            float(bar["close"]) <= level_price - config.SWEEP_REVERSAL_V1_CONFIRMATION_CLOSE_OFFSET_USD
            and float(bar["close"]) < float(bar["open"])
        )

    def _confirm_bullish(self, bar: pd.Series, level_price: float) -> bool:
        return (
            float(bar["close"]) >= level_price + config.SWEEP_REVERSAL_V1_CONFIRMATION_CLOSE_OFFSET_USD
            and float(bar["close"]) > float(bar["open"])
        )

    def generate_signal(self, context: StrategyContext, *, supabase_client=None):
        session_label = session_label_for(context.now)
        if session_label not in config.SWEEP_REVERSAL_V1_ALLOWED_SESSIONS:
            return None, f"gate_session: session {session_label or 'none'} not enabled"

        if context.local_trade_count >= config.SWEEP_REVERSAL_V1_MAX_TRADES_PER_DAY:
            return None, (
                f"gate_trades: daily limit reached "
                f"({context.local_trade_count}/{config.SWEEP_REVERSAL_V1_MAX_TRADES_PER_DAY})"
            )

        trigger_levels = self._trigger_levels(context.levels)
        if not trigger_levels:
            return None, "gate_levels: no structural trigger levels available"

        window = context.bars_so_far.tail(config.SWEEP_REVERSAL_V1_MAX_CONFIRM_BARS)
        confirmation_rows = [
            (ts.to_pydatetime(), historical_bar)
            for ts, historical_bar in window.iterrows()
        ]
        confirmation_rows.append((context.now, context.current_bar))

        candidates: list[tuple[float, Signal]] = []
        for level in trigger_levels:
            direction = "BEARISH" if level.direction == "resistance" else "BULLISH"
            active_level = ActiveLevel(level=level, distance_usd=abs(level.price - context.current_price))

            for idx, (_ts, bar) in enumerate(confirmation_rows):
                bars_since_sweep = len(confirmation_rows) - idx - 1
                if bars_since_sweep > config.SWEEP_REVERSAL_V1_MAX_CONFIRM_BARS:
                    continue

                bar_high = float(bar["high"])
                bar_low = float(bar["low"])
                if direction == "BEARISH":
                    sweep_size = bar_high - level.price
                    if sweep_size < config.SWEEP_REVERSAL_V1_SWEEP_MIN_USD:
                        continue
                    if not self._confirm_bearish(context.current_bar, level.price):
                        continue
                    signal = self._build_signal(
                        direction=direction,
                        entry_price=context.current_price,
                        active_level=active_level,
                        context=context,
                        sweep_side="above_resistance",
                        sweep_size=sweep_size,
                        confirmation_at=context.now,
                        confirmation_type="bearish_close_back_below_level",
                        bars_since_sweep=bars_since_sweep,
                        sweep_extreme=bar_high,
                        session_label=session_label,
                    )
                else:
                    sweep_size = level.price - bar_low
                    if sweep_size < config.SWEEP_REVERSAL_V1_SWEEP_MIN_USD:
                        continue
                    if not self._confirm_bullish(context.current_bar, level.price):
                        continue
                    signal = self._build_signal(
                        direction=direction,
                        entry_price=context.current_price,
                        active_level=active_level,
                        context=context,
                        sweep_side="below_support",
                        sweep_size=sweep_size,
                        confirmation_at=context.now,
                        confirmation_type="bullish_close_back_above_level",
                        bars_since_sweep=bars_since_sweep,
                        sweep_extreme=bar_low,
                        session_label=session_label,
                    )

                candidates.append((active_level.distance_usd, signal))
                break

        if not candidates:
            return None, "gate_sweep: no confirmed sweep reversal setup"

        _, signal = min(candidates, key=lambda item: item[0])
        return signal, "ok"
