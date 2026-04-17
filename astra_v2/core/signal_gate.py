"""
Signal Gate — 6-gate confirmation system.

All 6 gates must pass to generate a signal.
One NO = no trade. That's it.

Gates:
  1. Macro: direction not NEUTRAL + confidence >= 0.60
  2. Level proximity: price within $0.50 of a key level
  3. Direction alignment: BULLISH at support, BEARISH at resistance
  4. Session window: London (08-11 UTC) or NY (13-15 UTC)
  5. Daily limit: < 2 trades today (Supabase COUNT, not in-memory)
  6. DD safety: current drawdown < PROP_DAILY_STOP_DD_PCT

Data flow:
  MacroBias + [KeyLevel] + current_price + datetime + account_state
    → check_signal()
    → Signal | None
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Literal, Optional

from astra_v2 import config
from astra_v2.core.macro_engine import MacroBias
from astra_v2.core.technical_engine import KeyLevel, ActiveLevel

logger = logging.getLogger(__name__)

Direction = Literal["BULLISH", "BEARISH"]


@dataclass
class Signal:
    direction: Direction
    entry_price: float
    stop_loss: float          # absolute price (not distance)
    take_profit: float        # 1:2 RR take profit price
    partial_tp: float         # 1:1 partial close price
    level: ActiveLevel
    macro_bias: MacroBias
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    combined_confidence: float = 0.0
    strategy_id: str = "legacy_v1"
    setup_family: Optional[str] = None
    session_label: Optional[str] = None
    sweep_side: Optional[str] = None
    sweep_size: Optional[float] = None
    confirmation_at: Optional[datetime] = None
    confirmation_type: Optional[str] = None
    bars_since_sweep: Optional[int] = None
    execution_timeframe: Optional[str] = None
    entry_trigger_price: Optional[float] = None
    size_multiplier: float = 1.0  # position sizing multiplier (e.g. RVOL-based)

    @property
    def sl_distance_usd(self) -> float:
        return abs(self.entry_price - self.stop_loss)

    @property
    def tp_distance_usd(self) -> float:
        return abs(self.entry_price - self.take_profit)

    def to_dict(self) -> dict:
        return {
            "direction": self.direction,
            "entry_price": self.entry_price,
            "stop_loss": self.stop_loss,
            "take_profit": self.take_profit,
            "partial_tp": self.partial_tp,
            "level_type": self.level.level_type,
            "level_price": self.level.price,
            "macro_direction": self.macro_bias.direction,
            "macro_confidence": self.macro_bias.confidence,
            "macro_reasoning": self.macro_bias.reasoning,
            "combined_confidence": self.combined_confidence,
            "strategy_id": self.strategy_id,
            "setup_family": self.setup_family,
            "session_label": self.session_label,
            "sweep_side": self.sweep_side,
            "sweep_size": self.sweep_size,
            "confirmation_at": self.confirmation_at.isoformat() if self.confirmation_at else None,
            "confirmation_type": self.confirmation_type,
            "bars_since_sweep": self.bars_since_sweep,
            "execution_timeframe": self.execution_timeframe,
            "entry_trigger_price": self.entry_trigger_price,
            "timestamp": self.timestamp.isoformat(),
        }


# ── Session check ──────────────────────────────────────────────────────────────

def is_active_session(dt: datetime) -> bool:
    """
    Returns True if dt falls within London (07-12 UTC) or NY (13-17 UTC).
    No trades outside these windows.
    """
    hour = dt.hour
    return (
        config.LONDON_OPEN_UTC <= hour < config.LONDON_CLOSE_UTC or
        config.NY_OPEN_UTC <= hour < config.NY_CLOSE_UTC
    )


# ── Trade count (Supabase, not in-memory) ─────────────────────────────────────

def trades_today(supabase_client, local_count: int = 0) -> int:
    """
    Count trades opened today (UTC) from Supabase.
    Uses Supabase COUNT as authoritative source.

    Circuit breaker: if Supabase query fails, returns max(local_count, MAX_TRADES_PER_DAY)
    so the system fails safe (skips trade rather than allowing it).

    Args:
        supabase_client: Supabase client (or None for backtest)
        local_count: fallback in-memory count (secondary check)

    Returns: count of trades opened today
    """
    if supabase_client is None:
        return local_count  # backtest mode: caller manages count

    try:
        today_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        result = (
            supabase_client.table("trades")
            .select("id", count="exact")
            .gte("opened_at", f"{today_utc}T00:00:00+00:00")
            .not_.in_("status", ["cancelled", "rejected"])
            .execute()
        )
        db_count = result.count or 0
        # Use the HIGHER of Supabase and local count (fail-safe)
        return max(db_count, local_count)

    except Exception as e:
        logger.error(f"Supabase trade count failed (circuit breaker activated): {e}")
        # Fail safe: return max possible so trade is skipped
        return max(config.MAX_TRADES_PER_DAY, local_count)


# ── Drawdown check ─────────────────────────────────────────────────────────────

def current_drawdown_pct(peak_balance: float, current_balance: float) -> float:
    """Drawdown as percentage of peak balance."""
    if peak_balance <= 0:
        return 0.0
    return (peak_balance - current_balance) / peak_balance * 100


def _nearby_levels(levels: list[KeyLevel], current_price: float) -> list[ActiveLevel]:
    return [
        ActiveLevel(level=level, distance_usd=abs(level.price - current_price))
        for level in levels
        if abs(level.price - current_price) <= config.LEVEL_PROXIMITY_USD
    ]


def _trigger_candidates(levels: list[ActiveLevel]) -> list[ActiveLevel]:
    return [level for level in levels if level.level_type in config.TRIGGER_LEVEL_TYPES]


# ── Signal construction ────────────────────────────────────────────────────────

def _build_signal(
    direction: Direction,
    entry: float,
    level: ActiveLevel,
    macro: MacroBias,
    sl_distance_usd: float = None,
) -> Signal:
    sl_distance = sl_distance_usd or config.SL_DISTANCE_USD

    if direction == "BULLISH":
        stop_loss = entry - sl_distance
        take_profit = entry + sl_distance * config.TP_RR
        partial_tp = entry + sl_distance * config.PARTIAL_CLOSE_RR
    else:
        stop_loss = entry + sl_distance
        take_profit = entry - sl_distance * config.TP_RR
        partial_tp = entry - sl_distance * config.PARTIAL_CLOSE_RR

    # Combined confidence: macro * level strength / 10
    combined = macro.confidence * (level.strength / 10.0)

    return Signal(
        direction=direction,
        entry_price=entry,
        stop_loss=stop_loss,
        take_profit=take_profit,
        partial_tp=partial_tp,
        level=level,
        macro_bias=macro,
        combined_confidence=combined,
        strategy_id="legacy_v1",
    )


# ── Main gate ─────────────────────────────────────────────────────────────────

def check_signal(
    macro: MacroBias,
    levels: list[KeyLevel],
    current_price: float,
    now: datetime,
    supabase_client=None,
    local_trade_count: int = 0,
    peak_balance: float = 0.0,
    current_balance: float = 0.0,
    skip_gates: list[int] = None,  # for testing only
) -> tuple[Optional[Signal], str]:
    """
    Run all 6 gates. Returns (Signal, reason) or (None, reason).

    Args:
        macro: current MacroBias
        levels: list of KeyLevel computed by technical_engine
        current_price: live/simulated price in USD
        now: current UTC datetime
        supabase_client: for trade count query (None = backtest mode)
        local_trade_count: in-memory count (secondary, backtest uses this)
        peak_balance: for DD check (0 = skip DD gate)
        current_balance: for DD check (0 = skip DD gate)
        skip_gates: list of gate numbers to skip (testing only)

    Returns:
        (Signal, "ok") if all gates pass
        (None, "gate_N: reason") if any gate fails
    """
    skip = set(skip_gates or [])

    # Gate 1: Macro direction and confidence
    if 1 not in skip:
        if macro.direction == "NEUTRAL":
            return None, "gate_1: macro NEUTRAL"
        if macro.confidence < config.MACRO_CONFIDENCE_MIN:
            return None, f"gate_1: low confidence {macro.confidence:.2f} < {config.MACRO_CONFIDENCE_MIN}"

    direction: Direction = macro.direction  # type: ignore

    # Gate 2: Price at key level
    if 2 not in skip:
        nearby = _nearby_levels(levels, current_price)
        if not nearby:
            return None, f"gate_2: no level within ${config.LEVEL_PROXIMITY_USD} of {current_price:.2f}"
        triggerable = _trigger_candidates(nearby)
        if not triggerable:
            nearest = min(nearby, key=lambda a: a.distance_usd)
            return None, f"gate_2: nearest level {nearest.level_type} is context-only, not a trigger"
        strong_nearby = [level for level in triggerable if level.strength >= config.LEVEL_STRENGTH_MIN]
        if not strong_nearby:
            nearest = min(triggerable, key=lambda a: a.distance_usd)
            return None, f"gate_2: level too weak ({nearest.strength:.1f} < {config.LEVEL_STRENGTH_MIN})"
    else:
        # If gate 2 skipped, still find nearest for signal construction
        nearby = [
            ActiveLevel(level=level, distance_usd=abs(level.price - current_price))
            for level in levels
        ]
        if not nearby:
            return None, "gate_2_skip: no levels at all"
        strong_nearby = nearby

    # Gate 3: Direction alignment
    if 3 not in skip:
        expected = "support" if direction == "BULLISH" else "resistance"
        aligned_levels = [level for level in strong_nearby if level.direction == expected]
        if not aligned_levels:
            nearest = min(strong_nearby, key=lambda a: a.distance_usd)
            return None, f"gate_3: {direction} signal but nearest strong level is {nearest.direction}"
        active = min(aligned_levels, key=lambda a: a.distance_usd)
    else:
        active = min(strong_nearby, key=lambda a: a.distance_usd)

    # Gate 4: Session window
    if 4 not in skip:
        if not is_active_session(now):
            return None, f"gate_4: outside session (hour={now.hour} UTC)"

    # Gate 5: Daily trade limit (Supabase COUNT)
    if 5 not in skip:
        count = trades_today(supabase_client, local_trade_count)
        if count >= config.MAX_TRADES_PER_DAY:
            return None, f"gate_5: daily limit reached ({count}/{config.MAX_TRADES_PER_DAY})"

    # Gate 6: Drawdown safety
    if 6 not in skip and peak_balance > 0 and current_balance > 0:
        dd_pct = current_drawdown_pct(peak_balance, current_balance)
        if dd_pct >= config.PROP_DAILY_STOP_DD_PCT:
            return None, f"gate_6: drawdown {dd_pct:.1f}% >= stop threshold {config.PROP_DAILY_STOP_DD_PCT}%"

    # All gates passed — build signal
    signal = _build_signal(direction, current_price, active, macro)
    return signal, "ok"
