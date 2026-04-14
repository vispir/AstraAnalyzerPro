"""
Tests for technical_engine.py

Critical paths:
  - PDH/PDL reset at 00:00 UTC (not carry-over from previous day)
  - Round levels: $50 major levels present, $10 sub-levels within window
  - Fibonacci: computed from actual swing, min $50 range enforced
  - No look-ahead: extract_levels(bars[:N]) uses only past data
  - find_nearest: only levels within LEVEL_PROXIMITY_USD returned
"""

import pytest
import pandas as pd
import numpy as np
from datetime import datetime, timezone, timedelta

from astra_v2.core.technical_engine import (
    extract_levels,
    find_nearest,
    compute_pdh_pdl,
    compute_round_levels,
    KeyLevel,
)
from astra_v2 import config


# ── Helpers ────────────────────────────────────────────────────────────────────

def make_bars(prices: list[tuple], start: datetime = None) -> pd.DataFrame:
    """
    Build a minimal OHLCV DataFrame.
    prices: list of (open, high, low, close) tuples.
    """
    if start is None:
        start = datetime(2024, 3, 4, 0, 0, tzinfo=timezone.utc)
    index = [start + timedelta(minutes=15 * i) for i in range(len(prices))]
    records = []
    for o, h, l, c in prices:
        records.append({"open": o, "high": h, "low": l, "close": c, "volume": 1000})
    df = pd.DataFrame(records, index=pd.DatetimeIndex(index, tz="UTC"))
    return df


def day_bars(date: datetime, high: float, low: float, close: float) -> pd.DataFrame:
    """One full day (96 M15 bars) with given H/L/close."""
    bars = []
    mid = (high + low) / 2
    for i in range(96):
        bars.append((mid, high, low, close))
    return make_bars(bars, start=date)


# ── PDH / PDL ──────────────────────────────────────────────────────────────────

class TestPdHPdL:
    def _get_pdh_pdl(self, levels):
        """Extract PDH and PDL prices from list[KeyLevel]."""
        pdh = next((l.price for l in levels if l.level_type == "pdh"), None)
        pdl = next((l.price for l in levels if l.level_type == "pdl"), None)
        return pdh, pdl

    def test_pdh_pdl_uses_previous_day_only(self):
        """PDH/PDL must use yesterday's data, not today's."""
        yesterday = datetime(2024, 3, 4, 0, 0, tzinfo=timezone.utc)
        today = datetime(2024, 3, 5, 0, 0, tzinfo=timezone.utc)

        bars_yesterday = day_bars(yesterday, high=3250.0, low=3200.0, close=3230.0)
        # Today has different H/L — PDH/PDL must NOT reflect today
        bars_today_partial = day_bars(today, high=3260.0, low=3190.0, close=3240.0)
        bars_today_partial = bars_today_partial.iloc[:10]  # only 10 bars into today

        all_bars = pd.concat([bars_yesterday, bars_today_partial])
        as_of = today + timedelta(hours=9)  # 09:00 UTC today

        levels = compute_pdh_pdl(all_bars, as_of)
        pdh, pdl = self._get_pdh_pdl(levels)

        assert pdh == pytest.approx(3250.0, abs=0.01), "PDH should be yesterday's high"
        assert pdl == pytest.approx(3200.0, abs=0.01), "PDL should be yesterday's low"

    def test_pdh_pdl_resets_at_midnight(self):
        """After midnight UTC, PDH/PDL switches to the new previous day."""
        day1 = datetime(2024, 3, 4, 0, 0, tzinfo=timezone.utc)
        day2 = datetime(2024, 3, 5, 0, 0, tzinfo=timezone.utc)
        day3 = datetime(2024, 3, 6, 0, 0, tzinfo=timezone.utc)

        bars = pd.concat([
            day_bars(day1, high=3200.0, low=3150.0, close=3180.0),
            day_bars(day2, high=3270.0, low=3210.0, close=3250.0),
        ])
        # On day3, PDH/PDL should be day2's H/L
        as_of = day3 + timedelta(hours=9)
        levels = compute_pdh_pdl(bars, as_of)
        pdh, pdl = self._get_pdh_pdl(levels)

        assert pdh == pytest.approx(3270.0, abs=0.01)
        assert pdl == pytest.approx(3210.0, abs=0.01)

    def test_pdh_pdl_returns_empty_on_first_day(self):
        """First bar ever: no previous day exists → empty list."""
        bars = make_bars([(3200, 3210, 3190, 3200)])
        as_of = datetime(2024, 3, 4, 9, 0, tzinfo=timezone.utc)
        levels = compute_pdh_pdl(bars, as_of)
        assert levels == []


# ── Round levels ───────────────────────────────────────────────────────────────

class TestRoundLevels:
    def test_major_round_levels_present(self):
        """$50 levels (3200, 3250, 3300) must appear near current price."""
        levels = compute_round_levels(current_price=3225.0)
        prices = [l.price for l in levels]

        assert 3200.0 in prices, "$50 level 3200 missing"
        assert 3250.0 in prices, "$50 level 3250 missing"

    def test_round_levels_within_window(self):
        """All returned levels should be within ±$150 of current price."""
        levels = compute_round_levels(current_price=3225.0)
        for level in levels:
            assert abs(level.price - 3225.0) <= 150.0, f"Level {level.price} outside ±$150 window"

    def test_round_levels_have_correct_direction(self):
        """Levels below current price → support; above → resistance."""
        levels = compute_round_levels(current_price=3225.0)
        for level in levels:
            if level.price < 3225.0:
                assert level.direction == "support", f"Level {level.price} below price should be support"
            elif level.price > 3225.0:
                assert level.direction == "resistance", f"Level {level.price} above price should be resistance"

    def test_sub_levels_present(self):
        """$10 sub-levels (3210, 3220, 3230, ...) should be included."""
        levels = compute_round_levels(current_price=3225.0)
        prices = {l.price for l in levels}
        # At least one $10 sub-level near current price
        assert any(p % 10 == 0 and abs(p - 3225.0) <= 20 for p in prices), \
            "No $10 sub-levels found near current price"


# ── No look-ahead bias ─────────────────────────────────────────────────────────

class TestNoLookAhead:
    def test_extract_levels_uses_only_past_bars(self):
        """
        If we add bars with a new extreme high/low AFTER as_of,
        extract_levels must not include those future levels.
        """
        yesterday = datetime(2024, 3, 4, 0, 0, tzinfo=timezone.utc)
        today_london = datetime(2024, 3, 5, 9, 0, tzinfo=timezone.utc)
        future = datetime(2024, 3, 5, 15, 0, tzinfo=timezone.utc)

        past_bars = day_bars(yesterday, high=3250.0, low=3200.0, close=3230.0)

        # Future bars with a spike — must not appear in levels
        future_bars = make_bars(
            [(3230, 3400.0, 3220, 3380)],  # massive spike
            start=future,
        )
        all_bars = pd.concat([past_bars, future_bars])

        # extract_levels as of London open — only past bars should count
        levels = extract_levels(
            bars=all_bars[all_bars.index < today_london],
            current_price=3230.0,
            as_of=today_london,
        )

        prices = [l.price for l in levels]
        assert 3400.0 not in prices, "Future spike price leaked into levels"
        assert 3250.0 in prices or any(abs(p - 3250.0) < 1.0 for p in prices), \
            "Past high should appear as resistance level"


# ── find_nearest ───────────────────────────────────────────────────────────────

class TestFindNearest:
    def _make_level(self, price: float, direction: str = "support") -> KeyLevel:
        return KeyLevel(
            price=price,
            level_type="pdl",
            direction=direction,
            strength=7.0,
        )

    def test_returns_none_when_no_level_within_proximity(self):
        levels = [self._make_level(3200.0), self._make_level(3250.0)]
        result = find_nearest(levels=levels, current_price=3225.0)
        # 3225 is $25 away from nearest level — outside $0.50 proximity
        assert result is None

    def test_returns_active_level_within_proximity(self):
        levels = [self._make_level(3200.30)]
        result = find_nearest(levels=levels, current_price=3200.0)
        assert result is not None
        assert result.level.price == pytest.approx(3200.30, abs=0.01)
        assert result.distance_usd == pytest.approx(0.30, abs=0.01)

    def test_returns_closest_level_when_multiple_within_proximity(self):
        levels = [
            self._make_level(3200.40),
            self._make_level(3200.20),
        ]
        result = find_nearest(levels=levels, current_price=3200.0)
        assert result is not None
        assert result.level.price == pytest.approx(3200.20, abs=0.01)  # closer

    def test_proximity_threshold_is_config_value(self):
        """Proximity is exactly LEVEL_PROXIMITY_USD from config."""
        threshold = config.LEVEL_PROXIMITY_USD
        # Exactly at threshold — should be included
        levels = [self._make_level(3200.0 + threshold)]
        result = find_nearest(levels=levels, current_price=3200.0)
        assert result is not None

        # Just beyond threshold — should be excluded
        levels_beyond = [self._make_level(3200.0 + threshold + 0.01)]
        result_beyond = find_nearest(levels=levels_beyond, current_price=3200.0)
        assert result_beyond is None


# ── extract_levels integration ─────────────────────────────────────────────────

class TestExtractLevels:
    def test_returns_list_of_key_levels(self):
        bars = day_bars(
            datetime(2024, 3, 4, 0, 0, tzinfo=timezone.utc),
            high=3250.0, low=3180.0, close=3220.0,
        )
        as_of = datetime(2024, 3, 5, 9, 0, tzinfo=timezone.utc)
        levels = extract_levels(bars, current_price=3220.0, as_of=as_of)
        assert isinstance(levels, list)
        assert all(isinstance(l, KeyLevel) for l in levels)

    def test_levels_sorted_by_proximity(self):
        bars = day_bars(
            datetime(2024, 3, 4, 0, 0, tzinfo=timezone.utc),
            high=3250.0, low=3180.0, close=3220.0,
        )
        as_of = datetime(2024, 3, 5, 9, 0, tzinfo=timezone.utc)
        levels = extract_levels(bars, current_price=3220.0, as_of=as_of)
        if len(levels) > 1:
            distances = [abs(l.price - 3220.0) for l in levels]
            assert distances == sorted(distances), "Levels should be sorted by proximity"
