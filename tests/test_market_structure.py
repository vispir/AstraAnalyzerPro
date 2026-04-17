"""Tests for astra_v2.core.market_structure"""
import pytest
import pandas as pd
import numpy as np
from datetime import datetime, timezone

from astra_v2.core.market_structure import (
    detect_swing_highs_lows, detect_bos, detect_choch,
    build_market_structure,
    BOS_BULLISH, BOS_BEARISH, NO_BOS, CHOCH_BULLISH, CHOCH_BEARISH, Swing,
)


def _make_bars(highs, lows, closes=None, opens=None):
    """Helper: build a minimal OHLCV DataFrame."""
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
    """Single bar as a Series."""
    if close is None:
        close = (high + low) / 2
    if open_ is None:
        open_ = close
    return pd.Series({"open": open_, "high": high, "low": low, "close": close, "volume": 100})


# ── detect_swing_highs_lows ────────────────────────────────────────────────────

class TestDetectSwingHighsLows:
    def test_flat_sequence_returns_no_swings(self):
        bars = _make_bars([100] * 30, [99] * 30)
        swings = detect_swing_highs_lows(bars, lookback=3)
        assert swings == []

    def test_insufficient_bars_returns_empty(self):
        bars = _make_bars([100, 101, 102], [99, 98, 97])
        swings = detect_swing_highs_lows(bars, lookback=5)
        assert swings == []

    def test_single_peak_detected(self):
        # Build a clear peak at index 10 with lookback=3
        n = 25
        highs = [100.0] * n
        highs[10] = 120.0  # clear peak
        lows = [95.0] * n
        bars = _make_bars(highs, lows)
        swings = detect_swing_highs_lows(bars, lookback=3)
        high_swings = [s for s in swings if s.swing_type == "high"]
        assert len(high_swings) >= 1
        prices = [s.price for s in high_swings]
        assert 120.0 in prices

    def test_single_trough_detected(self):
        n = 25
        highs = [100.0] * n
        lows = [90.0] * n
        lows[10] = 70.0  # clear trough
        bars = _make_bars(highs, lows)
        swings = detect_swing_highs_lows(bars, lookback=3)
        low_swings = [s for s in swings if s.swing_type == "low"]
        assert len(low_swings) >= 1
        prices = [s.price for s in low_swings]
        assert 70.0 in prices

    def test_bull_sequence_produces_swings(self):
        """Rising sequence: HH at 5, HL at 10, HH at 15, HL at 20 — must detect swings."""
        n = 40
        highs = [100.0 + i * 0.5 for i in range(n)]
        lows  = [99.0  + i * 0.3 for i in range(n)]
        # Add distinct peaks and troughs
        highs[5]  = 110.0; lows[5]  = 99.0
        highs[10] = 105.0; lows[10] = 97.0
        highs[15] = 115.0; lows[15] = 99.5
        highs[20] = 110.0; lows[20] = 98.0
        bars = _make_bars(highs, lows)
        swings = detect_swing_highs_lows(bars, lookback=3)
        assert len(swings) >= 2

    def test_last_lookback_bars_cannot_form_swings(self):
        """The last `lookback` bars should NOT have confirmed swings (right side not yet formed)."""
        n = 30
        highs = [100.0] * n
        lows  = [99.0]  * n
        highs[n - 2] = 120.0  # potential peak at the very end — NOT confirmed
        bars = _make_bars(highs, lows)
        swings = detect_swing_highs_lows(bars, lookback=5)
        high_swings = [s for s in swings if s.swing_type == "high"]
        # The end-of-array peak should NOT appear (no right side)
        assert not any(s.price == 120.0 for s in high_swings)


# ── detect_bos ────────────────────────────────────────────────────────────────

class TestDetectBos:
    def _swings_from_list(self, data):
        """[(ts_offset, price, type)] → list[Swing]"""
        base = datetime(2023, 1, 1, tzinfo=timezone.utc)
        return [
            Swing(
                timestamp=pd.Timestamp(base).to_pydatetime(),
                price=price,
                swing_type=stype,
            )
            for (_, price, stype) in data
        ]

    def test_no_swings_returns_no_bos(self):
        bar = _make_bar(105, 99, close=103)
        assert detect_bos([], bar) == NO_BOS

    def test_close_above_swing_high_returns_bullish_bos(self):
        swings = [Swing(datetime(2023,1,1,tzinfo=timezone.utc), 100.0, "high")]
        bar = _make_bar(106, 101, close=105.0)
        assert detect_bos(swings, bar, atr=1.0) == BOS_BULLISH

    def test_close_below_swing_low_returns_bearish_bos(self):
        swings = [Swing(datetime(2023,1,1,tzinfo=timezone.utc), 90.0, "low")]
        bar = _make_bar(89, 85, close=86.0)
        assert detect_bos(swings, bar, atr=1.0) == BOS_BEARISH

    def test_close_inside_range_returns_no_bos(self):
        swings = [
            Swing(datetime(2023,1,1,tzinfo=timezone.utc), 105.0, "high"),
            Swing(datetime(2023,1,1,tzinfo=timezone.utc), 95.0,  "low"),
        ]
        bar = _make_bar(102, 98, close=100.0)
        assert detect_bos(swings, bar, atr=1.0) == NO_BOS

    def test_atr_filter_blocks_small_break(self):
        """Close is above swing high but only by 0.5, ATR=2.0, min_atr=1.0 → NO_BOS"""
        swings = [Swing(datetime(2023,1,1,tzinfo=timezone.utc), 100.0, "high")]
        bar = _make_bar(101, 100, close=100.5)
        # min_move = 2.0 * 1.0 = 2.0, but close is only 0.5 above → NO_BOS
        assert detect_bos(swings, bar, atr=2.0) == NO_BOS


# ── detect_choch ─────────────────────────────────────────────────────────────

class TestDetectChoch:
    def _build_swings(self, highs_prices, lows_prices):
        """Build alternating swings from high/low price lists."""
        base = pd.Timestamp("2023-01-01", tz="UTC")
        swings = []
        for i, p in enumerate(highs_prices):
            swings.append(Swing((base + pd.Timedelta(hours=i*4)).to_pydatetime(), p, "high"))
        for i, p in enumerate(lows_prices):
            swings.append(Swing((base + pd.Timedelta(hours=i*4 + 2)).to_pydatetime(), p, "low"))
        swings.sort(key=lambda s: s.timestamp)
        return swings

    def test_fewer_than_lookback_swings_returns_none(self):
        swings = [Swing(datetime(2023,1,1,tzinfo=timezone.utc), 100.0, "high")]
        bar = _make_bar(90, 85, close=86.0)
        result = detect_choch(swings, bar)
        assert result is None

    def test_bullish_trend_then_break_below_hl_returns_bearish_choch(self):
        """After bull BOS: strictly rising HH and HL → close below last HL → CHoCH_BEARISH"""
        # Monotonically rising highs and lows so last-2 always show HH + HL
        highs = [100.0, 102.0, 104.0, 106.0, 108.0, 110.0, 112.0, 114.0, 116.0, 118.0]
        lows  = [95.0,  96.0,  97.0,  98.0,  99.0,  100.0, 101.0, 102.0, 103.0, 104.0]
        swings = self._build_swings(highs, lows)
        # Close well below the last higher low (104) → structural shift bearish
        bar = _make_bar(101, 98, close=99.0)
        result = detect_choch(swings, bar)
        assert result == CHOCH_BEARISH

    def test_choch_with_insufficient_data_returns_none(self):
        swings = [
            Swing(datetime(2023,1,1,tzinfo=timezone.utc), 100.0, "high"),
            Swing(datetime(2023,1,2,tzinfo=timezone.utc), 95.0,  "low"),
        ]
        bar = _make_bar(90, 85, close=86.0)
        result = detect_choch(swings, bar)
        assert result is None  # fewer than 2 highs + 2 lows


# ── build_market_structure ─────────────────────────────────────────────────────

class TestBuildMarketStructure:
    def test_returns_market_structure_object(self):
        n = 60
        highs = [100.0 + i * 0.1 for i in range(n)]
        lows  = [99.0  + i * 0.1 for i in range(n)]
        bars = _make_bars(highs, lows)
        bar  = _make_bar(106, 103, close=105)
        ms = build_market_structure(bars, bar)
        assert ms is not None
        assert hasattr(ms, "swings")
        assert hasattr(ms, "last_bos")
        assert hasattr(ms, "trend")

    def test_no_look_ahead(self):
        """Swings must only use bars_so_far — current bar excluded."""
        n = 30
        highs = [100.0] * n
        lows  = [99.0]  * n
        bars = _make_bars(highs, lows)
        # Current bar with extreme high — should NOT be a detected swing
        bar = _make_bar(200, 99, close=150)
        ms = build_market_structure(bars, bar, lookback=3)
        for swing in ms.swings:
            assert swing.price != 200.0, "Current bar high leaked into swing detection"
