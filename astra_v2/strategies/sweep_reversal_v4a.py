from __future__ import annotations

from typing import Optional

from astra_v2 import config
from astra_v2.core.signal_gate import Signal
from astra_v2.core.technical_engine import ActiveLevel, KeyLevel
from .base import StrategyContext
from .sweep_reversal_v4 import SweepReversalStrategyV4


class SweepReversalStrategyV4A(SweepReversalStrategyV4):
    """
    Variant A — High-R, Low-Frequency.

    Changes vs v4:
      - Strict M1 trigger restored (lookback=5, buffer=0.015 ATR)
      - TP raised to 2.5R
      - Partial close disabled (partial_tp set to unreachable 999R)
    """

    strategy_id = "sweep_reversal_v4a"

    def _m1_trigger(
        self,
        direction: str,
        level_price: float,
        m1_bars,
        atr: float,
    ) -> Optional[float]:
        if m1_bars is None or len(m1_bars) < config.SWEEP_REVERSAL_V4_M1_LOOKBACK_BARS:
            return None
        recent = m1_bars.tail(config.SWEEP_REVERSAL_V4_M1_LOOKBACK_BARS).copy()
        latest = recent.iloc[-1]
        prior = recent.iloc[:-1]
        if prior.empty:
            return None
        reclaim = atr * config.SWEEP_REVERSAL_V4A_M1_RECLAIM_ATR
        buffer = atr * config.SWEEP_REVERSAL_V4A_M1_TRIGGER_BUFFER_ATR
        swing_lookback = min(config.SWEEP_REVERSAL_V4A_M1_TRIGGER_LOOKBACK, len(prior))
        prior_slice = prior.tail(swing_lookback)

        if direction == "BULLISH":
            micro_high = float(prior_slice["high"].max())
            if not (
                float(latest["close"]) >= micro_high + buffer
                and float(latest["close"]) > float(latest["open"])
                and float(latest["close"]) >= level_price + reclaim
            ):
                return None
            return max(float(latest["high"]), micro_high + buffer)

        micro_low = float(prior_slice["low"].min())
        if not (
            float(latest["close"]) <= micro_low - buffer
            and float(latest["close"]) < float(latest["open"])
            and float(latest["close"]) <= level_price - reclaim
        ):
            return None
        return min(float(latest["low"]), micro_low - buffer)

    def _build_signal(
        self,
        *,
        direction: str,
        trigger_price: float,
        active_level: ActiveLevel,
        context: StrategyContext,
        sweep_side: str,
        sweep_size: float,
        confirmation_at,
        confirmation_type: str,
        bars_since_sweep: int,
        sweep_extreme: float,
        session_label: str,
        atr: float,
        execution_timeframe: str = "M1",
        fib_confluence: Optional[KeyLevel] = None,
    ) -> Optional[Signal]:
        stop_buffer = atr * config.SWEEP_REVERSAL_V4_STOP_BUFFER_ATR
        if direction == "BULLISH":
            stop_loss = sweep_extreme - stop_buffer
            risk = trigger_price - stop_loss
            if risk <= 0:
                return None
            take_profit = trigger_price + risk * config.SWEEP_REVERSAL_V4A_TP_RR
            partial_tp = trigger_price + risk * config.SWEEP_REVERSAL_V4A_PARTIAL_CLOSE_RR
        else:
            stop_loss = sweep_extreme + stop_buffer
            risk = stop_loss - trigger_price
            if risk <= 0:
                return None
            take_profit = trigger_price - risk * config.SWEEP_REVERSAL_V4A_TP_RR
            partial_tp = trigger_price - risk * config.SWEEP_REVERSAL_V4A_PARTIAL_CLOSE_RR

        combined_confidence = min(
            0.95,
            active_level.strength / 10.0
            + min(sweep_size / max(atr, 1e-9), 2.0) * 0.10
            + (0.04 if fib_confluence is not None else 0.0),
        )

        return Signal(
            direction=direction,  # type: ignore[arg-type]
            entry_price=trigger_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            partial_tp=partial_tp,
            level=active_level,
            macro_bias=context.macro,
            timestamp=context.now,
            combined_confidence=combined_confidence,
            strategy_id=self.strategy_id,
            setup_family="sweep_reversal_v4a",
            session_label=session_label,
            sweep_side=sweep_side,
            sweep_size=round(sweep_size, 4),
            confirmation_at=confirmation_at,
            confirmation_type=confirmation_type,
            bars_since_sweep=bars_since_sweep,
            execution_timeframe=execution_timeframe,
            entry_trigger_price=trigger_price,
        )
