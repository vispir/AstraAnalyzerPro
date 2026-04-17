"""
SMC Order Block Strategy v1 — Order Block Retest Entry
========================================================
Concept:
  1. Market Structure: BOS_BULLISH → look for bullish OB (last bearish candle before impulse)
                       BOS_BEARISH → look for bearish OB (last bullish candle before impulse)
  2. Find the most recent VALID (not mitigated, not expired) OB in BOS direction
  3. Wait for current price to trade INTO the OB zone (retest)
  4. Enter at OB midpoint on the next M15 bar

Filters (gates):
  - calendar_blackout → skip
  - regime == VOLATILE → skip
  - regime not in ALLOWED_REGIMES → skip
  - No valid OB in BOS direction → skip
  - DXY divergence: size boost if confirmed
  - H4 trend alignment

Stop Loss: beyond OB far edge + SMC_OB_V1_STOP_BUFFER_ATR
Take Profit: 2.0R

Execution: next M15 bar open.
"""

from __future__ import annotations

from typing import Optional

import pandas as pd

from astra_v2 import config
from astra_v2.core.signal_gate import Signal
from astra_v2.core.technical_engine import KeyLevel, ActiveLevel
from astra_v2.core.market_structure import BOS_BULLISH, BOS_BEARISH, MarketStructure
from astra_v2.core.order_block import OrderBlock, OB_VALID, get_valid_obs, is_price_in_ob
from .base import StrategyContext
from .sweep_reversal_v1 import session_label_for


class SmcObV1:
    strategy_id = "smc_ob_v1"
    required_level_types = None
    required_timeframes = ("H4",)
    supports_live_execution = False

    def _compute_atr(self, bars: pd.DataFrame) -> Optional[float]:
        period = config.SMC_OB_V1_ATR_PERIOD
        if len(bars) < period + 1:
            return None
        recent = bars.tail(period + 1)
        highs  = recent["high"].astype(float)
        lows   = recent["low"].astype(float)
        closes = recent["close"].astype(float)
        prev_close = closes.shift(1)
        import pandas as _pd
        tr = _pd.concat(
            [highs - lows, (highs - prev_close).abs(), (lows - prev_close).abs()],
            axis=1,
        ).max(axis=1)
        atr = tr.tail(period).mean()
        if _pd.isna(atr) or atr <= 0:
            return None
        return float(atr)

    def _h4_trend(self, context: StrategyContext) -> Optional[str]:
        if context.h4_bars is None or len(context.h4_bars) < 20:
            return None
        recent = context.h4_bars.tail(20)
        closes = recent["close"].astype(float)
        last_close = float(closes.iloc[-1])
        trend_ma = closes.tail(8).mean()
        return "BULLISH" if last_close >= trend_ma else "BEARISH"

    def generate_signal(self, context: StrategyContext, *, supabase_client=None):
        # ── Gate: session ────────────────────────────────────────────────────
        session = session_label_for(context.now)
        if session not in config.SMC_OB_V1_ALLOWED_SESSIONS:
            return None, f"gate_session: {session or 'none'}"

        # ── Gate: calendar blackout ──────────────────────────────────────────
        if context.calendar_blackout:
            return None, "gate_calendar: news blackout"

        # ── Gate: regime ─────────────────────────────────────────────────────
        if context.regime == "VOLATILE":
            return None, "gate_regime: VOLATILE"
        if context.regime is not None and context.regime not in config.SMC_OB_V1_ALLOWED_REGIMES:
            return None, f"gate_regime: {context.regime} not in allowed regimes"

        # ── Gate: daily trade limit ──────────────────────────────────────────
        if context.local_trade_count >= config.SMC_OB_V1_MAX_TRADES_PER_DAY:
            return None, f"gate_trades: daily limit ({context.local_trade_count}/{config.SMC_OB_V1_MAX_TRADES_PER_DAY})"

        # ── Gate: market structure ───────────────────────────────────────────
        ms: Optional[MarketStructure] = context.market_structure  # type: ignore[assignment]
        if ms is None or ms.last_bos is None or ms.last_bos == "NO_BOS":
            return None, "gate_ms: no clear BOS"

        bos = ms.last_bos
        direction = "BULLISH" if bos == BOS_BULLISH else "BEARISH"

        # ── Gate: valid OBs ──────────────────────────────────────────────────
        obs: list[OrderBlock] = context.order_blocks or []
        valid = get_valid_obs(obs)
        if not valid:
            return None, "gate_ob: no valid OBs"

        directional_obs = [ob for ob in valid if ob.direction == direction]
        if not directional_obs:
            return None, f"gate_ob: no valid {direction} OBs (BOS={bos})"

        # ── ATR ──────────────────────────────────────────────────────────────
        atr = self._compute_atr(context.bars_so_far)
        if atr is None:
            return None, "gate_atr: insufficient bars"

        # ── H4 trend alignment ───────────────────────────────────────────────
        h4_trend = self._h4_trend(context)
        if h4_trend is not None and h4_trend != direction:
            return None, f"gate_h4: {direction} OB but H4 trend is {h4_trend}"

        # ── Check if price is inside any OB ──────────────────────────────────
        c_high  = float(context.current_bar["high"])
        c_low   = float(context.current_bar["low"])
        c_close = float(context.current_bar["close"])
        c_open  = float(context.current_bar["open"])

        triggered_ob: Optional[OrderBlock] = None
        for ob in directional_obs:
            if direction == "BULLISH":
                # Price dipping into the bullish OB zone
                if c_low <= ob.top and c_close >= ob.bottom:
                    triggered_ob = ob
                    break
            else:
                # Price rallying into the bearish OB zone
                if c_high >= ob.bottom and c_close <= ob.top:
                    triggered_ob = ob
                    break

        if triggered_ob is None:
            return None, "gate_ob: price not in any valid OB"

        # ── Build signal ─────────────────────────────────────────────────────
        stop_buffer = atr * config.SMC_OB_V1_STOP_BUFFER_ATR

        if direction == "BULLISH":
            entry_price = c_close
            stop_loss   = triggered_ob.bottom - stop_buffer
            risk        = entry_price - stop_loss
            if risk <= 0:
                return None, "gate_risk: zero or negative risk"
            take_profit = entry_price + risk * config.SMC_OB_V1_TP_RR
            partial_tp  = entry_price + risk * config.SMC_OB_V1_PARTIAL_CLOSE_RR
        else:
            entry_price = c_close
            stop_loss   = triggered_ob.top + stop_buffer
            risk        = stop_loss - entry_price
            if risk <= 0:
                return None, "gate_risk: zero or negative risk"
            take_profit = entry_price - risk * config.SMC_OB_V1_TP_RR
            partial_tp  = entry_price - risk * config.SMC_OB_V1_PARTIAL_CLOSE_RR

        # DXY conviction sizing
        size_mult = 1.0
        if direction == "BULLISH" and context.dxy_trend == "FALLING":
            size_mult = 1.2
        elif direction == "BEARISH" and context.dxy_trend == "RISING":
            size_mult = 1.2

        # Dummy ActiveLevel (OB-based)
        dummy_level = KeyLevel(
            price=triggered_ob.midpoint,
            level_type="order_block",
            direction="support" if direction == "BULLISH" else "resistance",
            strength=7.0,
            created_at=triggered_ob.formed_at,
        )
        active_level = ActiveLevel(
            level=dummy_level,
            distance_usd=abs(triggered_ob.midpoint - context.current_price),
        )

        confidence = 0.65
        if h4_trend == direction:
            confidence += 0.10
        if context.dxy_trend is not None:
            confidence += 0.05
        if context.regime == "TRENDING":
            confidence += 0.10
        confidence = min(0.95, confidence)

        signal = Signal(
            direction=direction,  # type: ignore[arg-type]
            entry_price=entry_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            partial_tp=partial_tp,
            level=active_level,
            macro_bias=context.macro,
            timestamp=context.now,
            combined_confidence=confidence,
            strategy_id=self.strategy_id,
            setup_family="smc_ob_v1",
            session_label=session,
            sweep_side=f"ob_{'long' if direction == 'BULLISH' else 'short'}",
            sweep_size=triggered_ob.size,
            confirmation_at=context.now,
            confirmation_type=(
                f"ob_{direction.lower()}"
                f"_bos_{bos}"
                f"_impulse_{triggered_ob.impulse_size_atr:.1f}atr"
                f"_regime_{context.regime or 'none'}"
                f"_h4_{h4_trend or 'na'}"
            ),
            bars_since_sweep=triggered_ob.age_bars,
            execution_timeframe="M15",
            entry_trigger_price=entry_price,
            size_multiplier=size_mult,
        )
        return signal, "ok"
