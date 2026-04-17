"""Tests for astra_v2.core.order_block"""
import pytest
import pandas as pd
from datetime import datetime, timezone

from astra_v2.core.order_block import (
    OrderBlock, detect_obs, update_ob_status, get_valid_obs, is_price_in_ob,
    OB_VALID, OB_MITIGATED, OB_EXPIRED,
)


def _make_bars(opens, highs, lows, closes):
    n = len(highs)
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


def _make_ob(direction, top, bottom, formed_bar_idx=0):
    return OrderBlock(
        direction=direction, top=top, bottom=bottom,
        formed_at=datetime(2023, 1, 1, tzinfo=timezone.utc),
        formed_bar_idx=formed_bar_idx,
    )


# ── detect_obs ────────────────────────────────────────────────────────────────

class TestDetectObs:
    def test_bearish_candle_followed_by_bullish_impulse_creates_bullish_ob(self):
        """Bearish candle at index 5, then 3 bullish bars with large range → bullish OB"""
        n = 15
        opens  = [100.0] * n
        closes = [100.0] * n
        highs  = [101.0] * n
        lows   = [99.0]  * n

        # OB candle at index 5: bearish (close < open), body=2
        opens[5]  = 102.0
        closes[5] = 100.0
        highs[5]  = 102.5
        lows[5]   = 99.5

        # Impulse: bars 6, 7, 8 → big upward move
        closes[6] = 105.0; highs[6] = 106.0; lows[6] = 100.0
        closes[7] = 110.0; highs[7] = 111.0; lows[7] = 104.0
        closes[8] = 115.0; highs[8] = 116.0; lows[8] = 109.0

        bars = _make_bars(opens, highs, lows, closes)
        # Use fixed atr so we know the thresholds
        obs = detect_obs(bars, atr=2.0, impulse_atr=1.5, min_body_atr=0.5)
        bull = [ob for ob in obs if ob.direction == "BULLISH"]
        assert len(bull) >= 1

    def test_no_impulse_after_ob_candle_returns_no_ob(self):
        """Bearish candle at index 5, but flat bars after → no OB"""
        n = 15
        opens  = [100.0] * n
        closes = [99.5]  * n
        highs  = [100.5] * n
        lows   = [99.0]  * n

        opens[5]  = 100.5
        closes[5] = 99.5   # small bearish candle, body=1
        highs[5]  = 101.0
        lows[5]   = 99.0

        # No impulse: flat bars
        bars = _make_bars(opens, highs, lows, closes)
        obs = detect_obs(bars, atr=2.0, impulse_atr=3.0, min_body_atr=0.3)
        assert obs == []

    def test_bullish_candle_followed_by_bearish_impulse_creates_bearish_ob(self):
        """Bullish candle at index 5, then big bearish move → bearish OB"""
        n = 15
        opens  = [100.0] * n
        closes = [100.0] * n
        highs  = [101.0] * n
        lows   = [99.0]  * n

        opens[5]  = 100.0
        closes[5] = 104.0   # bullish
        highs[5]  = 105.0
        lows[5]   = 99.5

        closes[6] = 99.0;  highs[6] = 103.0; lows[6] = 98.0
        closes[7] = 95.0;  highs[7] = 99.0;  lows[7] = 94.0
        closes[8] = 91.0;  highs[8] = 95.0;  lows[8] = 90.0

        bars = _make_bars(opens, highs, lows, closes)
        obs = detect_obs(bars, atr=2.0, impulse_atr=1.5, min_body_atr=0.3)
        bear = [ob for ob in obs if ob.direction == "BEARISH"]
        assert len(bear) >= 1


# ── update_ob_status ─────────────────────────────────────────────────────────

class TestUpdateObStatus:
    def test_bullish_ob_mitigated_when_price_trades_through_75pct(self):
        """Bullish OB: bar low <= top - 0.75 * size → MITIGATED"""
        ob = _make_ob("BULLISH", top=102.0, bottom=100.0)
        # size=2, 75% mitigation from top = 102 - 0.75*2 = 100.5
        # bar low = 100.4 <= 100.5 → MITIGATED
        bar = _make_bar(103.0, 100.4, close=101.0)
        update_ob_status([ob], bar, current_bar_idx=5, mitigated_pct=0.75, max_age_bars=100)
        assert ob.status == OB_MITIGATED

    def test_bullish_ob_stays_valid_when_price_only_clips_top(self):
        """Price clips the OB top but doesn't reach 75% → VALID"""
        ob = _make_ob("BULLISH", top=102.0, bottom=100.0)
        bar = _make_bar(103.0, 101.0, close=102.0)
        update_ob_status([ob], bar, current_bar_idx=5, mitigated_pct=0.75, max_age_bars=100)
        assert ob.status == OB_VALID

    def test_ob_expired_after_max_age_bars(self):
        ob = _make_ob("BULLISH", top=102.0, bottom=100.0, formed_bar_idx=0)
        bar = _make_bar(103.0, 101.0, close=102.0)
        update_ob_status([ob], bar, current_bar_idx=101, mitigated_pct=0.75, max_age_bars=100)
        assert ob.status == OB_EXPIRED

    def test_bearish_ob_mitigated_when_price_rallies_through_75pct(self):
        """Bearish OB: bar high >= bottom + 0.75 * size → MITIGATED"""
        ob = _make_ob("BEARISH", top=102.0, bottom=100.0)
        # size=2, 75% from bottom = 100 + 0.75*2 = 101.5
        # bar high = 101.6 >= 101.5 → MITIGATED
        bar = _make_bar(101.6, 99.0, close=100.0)
        update_ob_status([ob], bar, current_bar_idx=5, mitigated_pct=0.75, max_age_bars=100)
        assert ob.status == OB_MITIGATED


# ── get_valid_obs / is_price_in_ob ────────────────────────────────────────────

class TestHelpers:
    def test_get_valid_obs_filters_correctly(self):
        ob1 = _make_ob("BULLISH", top=102.0, bottom=100.0)
        ob2 = _make_ob("BULLISH", top=105.0, bottom=103.0)
        ob2.status = OB_MITIGATED
        ob3 = _make_ob("BEARISH", top=110.0, bottom=108.0)
        result = get_valid_obs([ob1, ob2, ob3])
        assert len(result) == 2
        assert all(ob.status == OB_VALID for ob in result)

    def test_is_price_in_ob(self):
        ob = _make_ob("BULLISH", top=102.0, bottom=100.0)
        assert is_price_in_ob(ob, 101.0)
        assert is_price_in_ob(ob, 100.0)
        assert is_price_in_ob(ob, 102.0)
        assert not is_price_in_ob(ob, 99.9)
        assert not is_price_in_ob(ob, 102.1)
