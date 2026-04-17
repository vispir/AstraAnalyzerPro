"""
Technical Engine — compute and score key price levels for XAU/USD.

All distances in USD (not MT5 pips).
1 MT5 pip for XAUUSD = $0.01 — useless for $3000+ gold.

Level types (priority order):
  1. PDH/PDL  — previous day high/low (resets 00:00 UTC)
  2. Weekly   — Monday open high/low (resets Monday 00:00 UTC)
  3. Session  — London/NY session extremes
  4. Round    — $50 steps (3200, 3250, 3300...) and $10 sub-levels
  5. Fib      — 50% and 61.8% of last major swing (min $50 swing)

Data flow:
  OHLCV bars → extract_levels() → [ActiveLevel, ...] → find_nearest()
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Literal, Optional

import pandas as pd
import numpy as np

from astra_v2 import config

logger = logging.getLogger(__name__)


def _to_utc_ts(dt: datetime) -> pd.Timestamp:
    """Convert a datetime (tz-aware or naive) to a UTC pd.Timestamp."""
    ts = pd.Timestamp(dt)
    if ts.tzinfo is None:
        return ts.tz_localize("UTC")
    return ts.tz_convert("UTC")

# ── Data model ─────────────────────────────────────────────────────────────────

LevelType = Literal[
    "pdh", "pdl",
    "weekly_high", "weekly_low",
    "session_high", "session_low",
    "round_50", "round_10",
    "fib_50", "fib_618",
]

Direction = Literal["resistance", "support"]


@dataclass
class KeyLevel:
    price: float
    level_type: LevelType
    direction: Direction
    touches: int = 1             # how many times price has tested this level
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    strength: float = 5.0        # 0-10, elevated by repeated touches

    @property
    def label(self) -> str:
        return f"{self.level_type}@{self.price:.2f}"


@dataclass
class ActiveLevel:
    """A level that price is currently near (within LEVEL_PROXIMITY_USD)."""
    level: KeyLevel
    distance_usd: float          # absolute distance from current price

    @property
    def price(self) -> float:
        return self.level.price

    @property
    def direction(self) -> Direction:
        return self.level.direction

    @property
    def strength(self) -> float:
        return self.level.strength

    @property
    def level_type(self) -> LevelType:
        return self.level.level_type


# ── Level computation ──────────────────────────────────────────────────────────

def compute_pdh_pdl(bars: pd.DataFrame, as_of: datetime) -> list[KeyLevel]:
    """
    Previous Day High/Low — bars before 00:00 UTC today.
    Resets at midnight UTC (not local time).
    """
    today_utc = pd.Timestamp(as_of.date(), tz="UTC")
    prior_bars = bars[bars.index < today_utc]
    if prior_bars.empty:
        return []

    prior_dates = prior_bars.index.normalize()
    prev_day = prior_dates.max()
    if pd.isna(prev_day):
        return []

    prev_day_bars = prior_bars[prior_dates == prev_day]
    if prev_day_bars.empty:
        return []

    return [
        KeyLevel(price=float(prev_day_bars["high"].max()), level_type="pdh", direction="resistance"),
        KeyLevel(price=float(prev_day_bars["low"].min()), level_type="pdl", direction="support"),
    ]


def compute_weekly_levels(bars: pd.DataFrame, as_of: datetime) -> list[KeyLevel]:
    """
    Weekly high/low from Monday open to current bar.
    Resets at 00:00 UTC every Monday.
    """
    as_of_ts = _to_utc_ts(as_of)
    # Find start of current week (Monday 00:00 UTC)
    days_since_monday = as_of_ts.dayofweek  # 0=Mon
    week_start = (as_of_ts - pd.Timedelta(days=days_since_monday)).floor("D")

    this_week = bars[(bars.index >= week_start) & (bars.index < as_of_ts)]
    if this_week.empty:
        return []

    return [
        KeyLevel(price=float(this_week["high"].max()), level_type="weekly_high", direction="resistance"),
        KeyLevel(price=float(this_week["low"].min()),  level_type="weekly_low",  direction="support"),
    ]


def compute_session_levels(bars: pd.DataFrame, as_of: datetime) -> list[KeyLevel]:
    """
    Today's completed session extremes (London or NY high/low).
    Only returns levels for sessions that have already closed.
    """
    today = pd.Timestamp(as_of.date(), tz="UTC")
    current_hour = as_of.hour
    levels = []

    # London session (08:00-11:00 UTC)
    if current_hour >= config.LONDON_CLOSE_UTC:
        london = bars[
            (bars.index >= today + pd.Timedelta(hours=config.LONDON_OPEN_UTC)) &
            (bars.index < today + pd.Timedelta(hours=config.LONDON_CLOSE_UTC))
        ]
        if not london.empty:
            levels.append(KeyLevel(float(london["high"].max()), "session_high", "resistance"))
            levels.append(KeyLevel(float(london["low"].min()),  "session_low",  "support"))

    # NY session (13:00-15:00 UTC)
    if current_hour >= config.NY_CLOSE_UTC:
        ny = bars[
            (bars.index >= today + pd.Timedelta(hours=config.NY_OPEN_UTC)) &
            (bars.index < today + pd.Timedelta(hours=config.NY_CLOSE_UTC))
        ]
        if not ny.empty:
            levels.append(KeyLevel(float(ny["high"].max()), "session_high", "resistance"))
            levels.append(KeyLevel(float(ny["low"].min()),  "session_low",  "support"))

    return levels


def compute_round_levels(current_price: float, window_usd: float = 150.0) -> list[KeyLevel]:
    """
    Round number levels within ±window_usd of current price.
    $50 major levels (3200, 3250...) and $10 sub-levels (3210, 3220...).
    """
    levels = []
    low = current_price - window_usd
    high = current_price + window_usd

    # $50 major round levels
    import math
    start_50 = math.ceil(low / 50) * 50
    price = float(start_50)
    while price <= high:
        if price > 0:
            direction: Direction = "resistance" if price > current_price else "support"
            levels.append(KeyLevel(price=price, level_type="round_50", direction=direction, strength=7.0))
        price += 50.0

    # $10 sub-levels (but not the $50 ones, those are already above)
    start_10 = math.ceil(low / 10) * 10
    price = float(start_10)
    while price <= high:
        if price > 0 and price % 50 != 0:  # skip $50 levels (already added)
            direction = "resistance" if price > current_price else "support"
            levels.append(KeyLevel(price=price, level_type="round_10", direction=direction, strength=4.5))
        price += 10.0

    return levels


def compute_fibonacci_levels(bars: pd.DataFrame, current_price: float, min_swing_usd: float = 50.0) -> list[KeyLevel]:
    """
    Fibonacci 50% and 61.8% retracement of the last major swing.
    A swing must be at least min_swing_usd ($50) to qualify.

    Looks back at the last 200 bars to find the most recent significant swing.
    """
    if len(bars) < 20:
        return []

    recent = bars.tail(200)

    swing_high = float(recent["high"].max())
    swing_low = float(recent["low"].min())
    swing_size = swing_high - swing_low

    if swing_size < min_swing_usd:
        return []

    fib_50 = swing_low + swing_size * 0.50
    fib_618 = swing_low + swing_size * 0.618

    levels = []
    for price, level_type in [(fib_50, "fib_50"), (fib_618, "fib_618")]:
        direction: Direction = "resistance" if price > current_price else "support"
        levels.append(KeyLevel(price=price, level_type=level_type, direction=direction, strength=6.0))

    return levels


def extract_levels(
    bars: pd.DataFrame,
    current_price: float,
    as_of: datetime,
    allowed_level_types: Optional[set[LevelType]] = None,
) -> list[KeyLevel]:
    """
    Compute all key levels given OHLCV bars and current price.

    Args:
        bars: M15 DataFrame (open, high, low, close, volume). UTC index.
              Must only include bars BEFORE as_of (no look-ahead).
        current_price: live/simulated current mid price
        as_of: the timestamp being evaluated (for session/PDH/PDL logic)

    Returns sorted list of KeyLevel by proximity to current_price.
    """
    levels: list[KeyLevel] = []
    wanted = allowed_level_types

    if wanted is None or wanted.intersection({"pdh", "pdl"}):
        levels.extend([
            level
            for level in compute_pdh_pdl(bars, as_of)
            if wanted is None or level.level_type in wanted
        ])

    if wanted is None or wanted.intersection({"weekly_high", "weekly_low"}):
        levels.extend([
            level
            for level in compute_weekly_levels(bars, as_of)
            if wanted is None or level.level_type in wanted
        ])

    if wanted is None or wanted.intersection({"session_high", "session_low"}):
        levels.extend([
            level
            for level in compute_session_levels(bars, as_of)
            if wanted is None or level.level_type in wanted
        ])

    if wanted is None or wanted.intersection({"round_50", "round_10"}):
        levels.extend([
            level
            for level in compute_round_levels(current_price)
            if wanted is None or level.level_type in wanted
        ])

    if wanted is None or wanted.intersection({"fib_50", "fib_618"}):
        levels.extend([
            level
            for level in compute_fibonacci_levels(bars, current_price)
            if wanted is None or level.level_type in wanted
        ])

    # Sort by proximity
    levels.sort(key=lambda lvl: abs(lvl.price - current_price))
    return levels


def find_nearest(
    levels: list[KeyLevel],
    current_price: float,
    proximity_usd: float = None,
) -> Optional[ActiveLevel]:
    """
    Find the nearest key level within proximity_usd of current_price.
    Returns None if no level is close enough.
    """
    proximity_usd = proximity_usd or config.LEVEL_PROXIMITY_USD

    candidates = [
        ActiveLevel(level=lvl, distance_usd=abs(lvl.price - current_price))
        for lvl in levels
        if abs(lvl.price - current_price) <= proximity_usd
    ]
    if not candidates:
        return None
    return min(candidates, key=lambda a: a.distance_usd)


def is_at_level(current_price: float, levels: list[KeyLevel]) -> Optional[ActiveLevel]:
    """Convenience wrapper. Returns ActiveLevel or None."""
    return find_nearest(levels, current_price)
