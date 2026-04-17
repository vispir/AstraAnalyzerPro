"""
SMC FVG Strategy v1 — Fair Value Gap Retest Entry
===================================================
Concept:
  1. Market Structure: BOS_BULLISH → look for bullish FVG setups
                       BOS_BEARISH → look for bearish FVG setups
  2. Find the most recent active (unfilled, unexpired) FVG in BOS direction
  3. Wait for current price to enter the FVG zone (retest)
  4. Enter in the direction of BOS on the next M15 bar

Filters (gates):
  - calendar_blackout → skip (news spike risk)
  - regime == VOLATILE → skip (FVGs fill erratically in volatile markets)
  - regime not in ALLOWED_REGIMES → skip
  - No active FVG in BOS direction → skip
  - DXY divergence: if BULLISH setup, DXY falling = higher conviction (size boost)
  - H4 trend alignment: only trade FVGs aligned with H4 trend

Stop Loss: beyond the FVG's far edge + SMC_FVG_V1_STOP_BUFFER_ATR
Take Profit: 2.0R

Execution: next M15 bar open (queue signal, enter at bar open).
"""

from __future__ import annotations

from typing import Optional

import pandas as pd

from astra_v2 import config
from astra_v2.core.signal_gate import Signal
from astra_v2.core.technical_engine import KeyLevel, ActiveLevel
from astra_v2.core.market_structure import BOS_BULLISH, BOS_BEARISH, MarketStructure
from astra_v2.core.fair_value_gap import FairValueGap, FVG_ACTIVE, get_active_fvgs
from .base import StrategyContext
from .sweep_reversal_v1 import session_label_for


class SmcFvgV1:
    strategy_id = "smc_fvg_v1"
    required_level_types = None       # FVG-based, not level-based
    required_timeframes = ("H4",)
    supports_live_execution = False

    def __init__(self):
        self._h4_fvg_cache: dict = {}  # len(h4_bars) -> list[FairValueGap]

    def _get_h4_fvgs(self, context: StrategyContext) -> list:
        """Compute FVGs from H4 bars with length-keyed cache."""
        if context.h4_bars is None or len(context.h4_bars) < 4:
            return []
        from astra_v2.core.fair_value_gap import detect_fvgs, get_active_fvgs
        key = len(context.h4_bars)
        if key not in self._h4_fvg_cache:
            self._h4_fvg_cache[key] = detect_fvgs(context.h4_bars)
        return self._h4_fvg_cache[key]

    def _compute_atr(self, bars: pd.DataFrame) -> Optional[float]:
        period = config.SMC_FVG_V1_ATR_PERIOD
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
        if context.h4_bars is None or len(context.h4_bars) < 50:
            return None
        recent = context.h4_bars.tail(50)
        closes = recent["close"].astype(float)
        last_close = float(closes.iloc[-1])
        # 20-bar H4 MA = 80 hours (~3.3 days) — stable trend filter
        trend_ma = closes.tail(20).mean()
        return "BULLISH" if last_close >= trend_ma else "BEARISH"

    def generate_signal(self, context: StrategyContext, *, supabase_client=None):
        # ── Gate: session ────────────────────────────────────────────────────
        session = session_label_for(context.now)
        if session not in config.SMC_FVG_V1_ALLOWED_SESSIONS:
            return None, f"gate_session: {session or 'none'}"

        # ── Gate: calendar blackout ──────────────────────────────────────────
        if context.calendar_blackout:
            return None, "gate_calendar: news blackout"

        # ── Gate: regime ─────────────────────────────────────────────────────
        if context.regime == "VOLATILE":
            return None, "gate_regime: VOLATILE — FVGs fill erratically"
        if context.regime is not None and context.regime not in config.SMC_FVG_V1_ALLOWED_REGIMES:
            return None, f"gate_regime: {context.regime} not in allowed regimes"

        # ── Gate: daily trade limit ──────────────────────────────────────────
        if context.local_trade_count >= config.SMC_FVG_V1_MAX_TRADES_PER_DAY:
            return None, f"gate_trades: daily limit ({context.local_trade_count}/{config.SMC_FVG_V1_MAX_TRADES_PER_DAY})"

        # ── Gate: market structure ───────────────────────────────────────────
        # Use ms.trend (HH+HL / LH+LL swing pattern) for direction — more stable
        # than point-in-time BOS which fires only on the exact breakout bar.
        ms: Optional[MarketStructure] = context.market_structure  # type: ignore[assignment]
        if ms is None or ms.trend not in ("BULLISH", "BEARISH"):
            return None, "gate_ms: no clear trend"

        direction = ms.trend  # "BULLISH" or "BEARISH"

        # ── Gate: active FVGs ────────────────────────────────────────────────
        fvg_timeframe = getattr(config, "SMC_FVG_V1_FVG_TIMEFRAME", "M15")
        if fvg_timeframe == "H4":
            # Use H4 FVGs for higher-quality, larger supply/demand zones
            all_fvgs = self._get_h4_fvgs(context)
        else:
            all_fvgs = context.fvgs or []

        active = get_active_fvgs(all_fvgs)
        if not active:
            return None, "gate_fvg: no active FVGs"

        # Filter FVGs to match trend direction
        directional_fvgs = [f for f in active if f.direction == direction]
        if not directional_fvgs:
            return None, f"gate_fvg: no active {direction} FVGs (trend={direction})"

        # ── ATR ──────────────────────────────────────────────────────────────
        atr = self._compute_atr(context.bars_so_far)
        if atr is None:
            return None, "gate_atr: insufficient bars"

        # ── H4 trend alignment ───────────────────────────────────────────────
        h4_trend = self._h4_trend(context)
        if h4_trend is not None and h4_trend != direction:
            return None, f"gate_h4: {direction} FVG but H4 trend is {h4_trend}"

        # ── Check if current price is entering any FVG ───────────────────────
        c_high  = float(context.current_bar["high"])
        c_low   = float(context.current_bar["low"])
        c_close = float(context.current_bar["close"])
        c_open  = float(context.current_bar["open"])

        stop_buffer = atr * config.SMC_FVG_V1_STOP_BUFFER_ATR
        entry_mode  = getattr(config, "SMC_FVG_V1_ENTRY_MODE", "touch_boundary")

        triggered_fvg: Optional[FairValueGap] = None
        candle_stop: Optional[float] = None   # used by wick_rejection mode only

        for fvg in directional_fvgs:
            if entry_mode == "wick_rejection":
                # Wick-rejection: candle enters the FVG zone but CLOSES back outside it.
                # Stop = candle extreme (not FVG edge) → much tighter risk.
                if direction == "BULLISH":
                    # Candle wicked into FVG (low <= fvg.top) but closed above fvg.top
                    # Minimum wick body: close must be above open by a small amount
                    if (c_low <= fvg.top
                            and c_close >= fvg.top
                            and c_close > c_open):  # bullish candle
                        triggered_fvg = fvg
                        candle_stop = c_low - stop_buffer
                        break
                else:
                    # Candle wicked into FVG (high >= fvg.bottom) but closed below fvg.bottom
                    if (c_high >= fvg.bottom
                            and c_close <= fvg.bottom
                            and c_close < c_open):  # bearish candle
                        triggered_fvg = fvg
                        candle_stop = c_high + stop_buffer
                        break
            else:
                # touch_boundary mode (original): enter when close is near FVG boundary.
                entry_depth_limit = atr * config.SMC_FVG_V1_ENTRY_DEPTH_ATR
                if direction == "BULLISH":
                    if (c_low <= fvg.top
                            and fvg.bottom <= c_close <= fvg.bottom + entry_depth_limit):
                        triggered_fvg = fvg
                        break
                else:
                    if (c_high >= fvg.bottom
                            and fvg.top - entry_depth_limit <= c_close <= fvg.top):
                        triggered_fvg = fvg
                        break

        if triggered_fvg is None:
            return None, "gate_fvg: price not entering any active FVG"

        # ── Build signal ─────────────────────────────────────────────────────
        if direction == "BULLISH":
            entry_price = c_close
            if candle_stop is not None:
                stop_loss = candle_stop
            else:
                stop_loss = triggered_fvg.bottom - stop_buffer
            risk = entry_price - stop_loss
            if risk <= 0:
                return None, "gate_risk: zero or negative risk"
            take_profit = entry_price + risk * config.SMC_FVG_V1_TP_RR
            partial_tp  = entry_price + risk * config.SMC_FVG_V1_PARTIAL_CLOSE_RR
        else:
            entry_price = c_close
            if candle_stop is not None:
                stop_loss = candle_stop
            else:
                stop_loss = triggered_fvg.top + stop_buffer
            risk = stop_loss - entry_price
            if risk <= 0:
                return None, "gate_risk: zero or negative risk"
            take_profit = entry_price - risk * config.SMC_FVG_V1_TP_RR
            partial_tp  = entry_price - risk * config.SMC_FVG_V1_PARTIAL_CLOSE_RR

        # DXY divergence → conviction sizing
        size_mult = 1.0
        if direction == "BULLISH" and context.dxy_trend == "FALLING":
            size_mult = 1.2   # DXY falling + gold BOS bullish = high conviction
        elif direction == "BEARISH" and context.dxy_trend == "RISING":
            size_mult = 1.2   # DXY rising + gold BOS bearish = high conviction

        # Dummy ActiveLevel (FVG-based, no key level required)
        from astra_v2.core.technical_engine import KeyLevel, ActiveLevel
        dummy_level = KeyLevel(
            price=triggered_fvg.midpoint,
            level_type="fvg",
            direction="support" if direction == "BULLISH" else "resistance",
            strength=6.0,
            created_at=triggered_fvg.formed_at,
        )
        active_level = ActiveLevel(
            level=dummy_level,
            distance_usd=abs(triggered_fvg.midpoint - context.current_price),
        )

        confidence = 0.60
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
            setup_family="smc_fvg_v1",
            session_label=session,
            sweep_side=f"fvg_{'long' if direction == 'BULLISH' else 'short'}",
            sweep_size=triggered_fvg.size,
            confirmation_at=context.now,
            confirmation_type=(
                f"fvg_{direction.lower()}"
                f"_trend_{direction}"
                f"_regime_{context.regime or 'none'}"
                f"_h4_{h4_trend or 'na'}"
            ),
            bars_since_sweep=triggered_fvg.age_bars,
            execution_timeframe="M15",
            entry_trigger_price=entry_price,
            size_multiplier=size_mult,
        )
        return signal, "ok"
