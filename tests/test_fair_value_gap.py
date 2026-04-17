"""Tests for astra_v2.core.fair_value_gap"""
import pytest
import pandas as pd
from datetime import datetime, timezone

from astra_v2.core.fair_value_gap import (
    FairValueGap, detect_fvgs, update_fvg_status, get_active_fvgs,
    FVG_ACTIVE, FVG_FILLED, FVG_EXPIRED,
)


def _make_bars(highs, lows, closes=None, opens=None):
    n = len(highs)
    if closes is None:
        closes = [(h + l) / 2 for h, l in zip(highs, lows)]
    if opens is None:
        opens = closes
    idx = pd.date_range("2023-01-01", periods=n, freq="15min", tz="UTC")
    return pd.DataFrame({
        "open": opens, "high": highs, "low": lows, "close": closes, "volume": [100] * n
    }, index=idx)


def _make_bar(high, low, close=None, open_=None):
    if close is None:
        close = (high + low) / 2
    if open_ is None:
        open_ = close
    return pd.Series({"open": open_, "high": high, "low": low, "close": close, "volume": 100})


# ── detect_fvgs ────────────────────────────────────────────────────────────────

class TestDetectFvgs:
    def test_bullish_fvg_detected(self):
        """bar[i].low > bar[i-2].high → bullish FVG"""
        # bars: [0]=100-101, [1]=100.5-105, [2]=103-108
        # bar[2].low (103) > bar[0].high (101) → bullish FVG
        highs  = [101, 105, 108]
        lows   = [100, 100.5, 103]
        closes = [100.5, 102, 105]
        bars = _make_bars(highs, lows, closes)
        fvgs = detect_fvgs(bars, atr=None, min_size_atr=0.0)
        bull = [f for f in fvgs if f.direction == "BULLISH"]
        assert len(bull) == 1
        assert bull[0].bottom == pytest.approx(101.0)
        assert bull[0].top == pytest.approx(103.0)

    def test_bearish_fvg_detected(self):
        """bar[i].high < bar[i-2].low → bearish FVG"""
        # bars: [0]=99-100, [1]=93-96, [2]=88-92
        # bar[2].high (92) < bar[0].low (99) → bearish FVG
        highs  = [100, 96, 92]
        lows   = [99,  93, 88]
        closes = [99.5, 94, 90]
        bars = _make_bars(highs, lows, closes)
        fvgs = detect_fvgs(bars, atr=None, min_size_atr=0.0)
        bear = [f for f in fvgs if f.direction == "BEARISH"]
        assert len(bear) == 1
        assert bear[0].top == pytest.approx(99.0)
        assert bear[0].bottom == pytest.approx(92.0)

    def test_overlapping_bars_no_fvg(self):
        """Bars overlap → no gap → 0 FVGs"""
        highs  = [101, 102, 100]
        lows   = [99,  100, 98]
        bars = _make_bars(highs, lows)
        fvgs = detect_fvgs(bars, atr=None, min_size_atr=0.0)
        assert fvgs == []

    def test_gap_below_min_size_filtered(self):
        """Gap exists but smaller than min_size_atr * atr → filtered out"""
        # bar[2].low (101.1) > bar[0].high (101.0) → gap = 0.1
        highs  = [101.0, 105, 108]
        lows   = [100.0, 100.5, 101.1]
        closes = [100.5, 102, 105]
        bars = _make_bars(highs, lows, closes)
        # atr=1.0, min_size_atr=0.3 → min_size=0.3, but gap=0.1 → filtered
        fvgs = detect_fvgs(bars, atr=1.0, min_size_atr=0.3)
        assert fvgs == []

    def test_no_look_ahead(self):
        """FVG from the very last 2 bars (i, i-1, i-2) should be detected.
        But if we pass bars_so_far (excludes current bar), current bar's FVG is NOT there."""
        # Build 10 bars where a FVG would only form at the last bar
        n = 10
        highs = [100.0] * n
        lows  = [99.0]  * n
        # Make a FVG at index 9 (last bar): bar[9].low > bar[7].high
        highs[7] = 100.0
        lows[9]  = 102.0  # gap: bar[9].low=102 > bar[7].high=100
        highs[9] = 105.0
        bars = _make_bars(highs, lows)
        # bars_so_far excludes index 9 (current bar)
        bars_so_far = bars.iloc[:9]
        fvgs = detect_fvgs(bars_so_far, atr=None, min_size_atr=0.0)
        # FVG at bar[9] must NOT be in bars_so_far results
        for fvg in fvgs:
            assert fvg.top != 102.0 or fvg.bottom != 100.0


# ── update_fvg_status ─────────────────────────────────────────────────────────

class TestUpdateFvgStatus:
    def _make_fvg(self, direction, top, bottom, formed_bar_idx=0):
        return FairValueGap(
            direction=direction, top=top, bottom=bottom,
            formed_at=datetime(2023, 1, 1, tzinfo=timezone.utc),
            formed_bar_idx=formed_bar_idx,
        )

    def test_bullish_fvg_filled_when_price_trades_to_midpoint(self):
        """Bar low <= bottom + entry_depth * size → FILLED"""
        fvg = self._make_fvg("BULLISH", top=105.0, bottom=100.0)
        # entry_depth=0.5, size=5, so fill level = 100 + 0.5*5 = 102.5
        # bar low = 102.0 <= 102.5 → FILLED
        bar = _make_bar(106, 102.0, close=104)
        update_fvg_status([fvg], bar, current_bar_idx=5, expiry_bars=100, entry_depth=0.5)
        assert fvg.status == FVG_FILLED

    def test_fvg_not_filled_when_price_stays_above(self):
        """Bar low still above fill level → ACTIVE"""
        fvg = self._make_fvg("BULLISH", top=105.0, bottom=100.0)
        bar = _make_bar(106, 104.0, close=105)  # low=104 > 102.5 → not filled
        update_fvg_status([fvg], bar, current_bar_idx=5, expiry_bars=100, entry_depth=0.5)
        assert fvg.status == FVG_ACTIVE

    def test_fvg_expired_after_expiry_bars(self):
        """age_bars > expiry_bars → EXPIRED"""
        fvg = self._make_fvg("BULLISH", top=105.0, bottom=100.0, formed_bar_idx=0)
        bar = _make_bar(106, 104.0, close=105)
        update_fvg_status([fvg], bar, current_bar_idx=50, expiry_bars=48, entry_depth=0.5)
        assert fvg.status == FVG_EXPIRED

    def test_bearish_fvg_filled_when_price_rallies_in(self):
        """Bearish FVG: bar high >= top - entry_depth * size → FILLED"""
        fvg = self._make_fvg("BEARISH", top=100.0, bottom=95.0)
        # entry_depth=0.5, size=5, fill level = 100 - 2.5 = 97.5
        # bar high = 98 >= 97.5 → FILLED
        bar = _make_bar(98.0, 94, close=96)
        update_fvg_status([fvg], bar, current_bar_idx=5, expiry_bars=100, entry_depth=0.5)
        assert fvg.status == FVG_FILLED

    def test_already_filled_fvg_not_re_evaluated(self):
        """A FILLED FVG should not change status on subsequent calls."""
        fvg = self._make_fvg("BULLISH", top=105.0, bottom=100.0)
        fvg.status = FVG_FILLED
        bar = _make_bar(106, 100.0, close=103)
        update_fvg_status([fvg], bar, current_bar_idx=5, expiry_bars=100, entry_depth=0.5)
        assert fvg.status == FVG_FILLED  # unchanged


# ── get_active_fvgs ───────────────────────────────────────────────────────────

class TestGetActiveFvgs:
    def test_returns_only_active_sorted_newest_first(self):
        base = datetime(2023, 1, 1, tzinfo=timezone.utc)
        f1 = FairValueGap("BULLISH", 105, 100, base, 0, status=FVG_ACTIVE)
        f2 = FairValueGap("BULLISH", 110, 105, base, 1, status=FVG_FILLED)
        f3 = FairValueGap("BULLISH", 115, 110,
                          pd.Timestamp("2023-01-02", tz="UTC").to_pydatetime(), 2, status=FVG_ACTIVE)
        result = get_active_fvgs([f1, f2, f3])
        assert len(result) == 2
        assert result[0].top == 115  # newest first
        assert result[1].top == 105
