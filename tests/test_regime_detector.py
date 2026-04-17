"""Tests for astra_v2.core.regime_detector"""
import pytest
import pandas as pd
import numpy as np

from astra_v2.core.regime_detector import detect_regime


def _make_h4_bars(n=40, trend="flat", atr_mult=1.0):
    """Build synthetic H4 bars with controlled ADX/ATR characteristics."""
    np.random.seed(42)
    idx = pd.date_range("2023-01-01", periods=n, freq="4h", tz="UTC")

    if trend == "strong_up":
        close = np.linspace(1800, 2000, n) + np.random.randn(n) * 2
    elif trend == "strong_down":
        close = np.linspace(2000, 1800, n) + np.random.randn(n) * 2
    elif trend == "flat":
        close = np.ones(n) * 1900 + np.random.randn(n) * 3
    else:
        close = np.ones(n) * 1900 + np.random.randn(n) * 3

    # ATR multiplier: use larger swings if volatile
    swing = 5.0 * atr_mult
    high  = close + swing
    low   = close - swing
    open_ = np.roll(close, 1)
    open_[0] = close[0]

    return pd.DataFrame({
        "open": open_, "high": high, "low": low, "close": close, "volume": 1000
    }, index=idx)


class TestDetectRegime:
    def test_insufficient_data_returns_none(self):
        bars = _make_h4_bars(n=5)
        assert detect_regime(bars) is None

    def test_none_input_returns_none(self):
        assert detect_regime(None) is None

    def test_strong_trend_returns_trending(self):
        """Strong directional move → ADX should be high → TRENDING"""
        # Build bars with very strong directional move (minimal noise)
        n = 50
        idx = pd.date_range("2023-01-01", periods=n, freq="4h", tz="UTC")
        # Very clean trend: each bar moves exactly +2
        close = np.array([1800 + i * 3.0 for i in range(n)], dtype=float)
        high  = close + 1.0
        low   = close - 0.5
        open_ = np.roll(close, 1); open_[0] = close[0]
        bars = pd.DataFrame({
            "open": open_, "high": high, "low": low, "close": close, "volume": 1000
        }, index=idx)
        result = detect_regime(bars)
        assert result == "TRENDING"

    def test_flat_market_returns_accumulation_or_distribution(self):
        """Very flat market → ADX low → ACCUMULATION or DISTRIBUTION"""
        n = 50
        idx = pd.date_range("2023-01-01", periods=n, freq="4h", tz="UTC")
        # Truly flat: constant price ± tiny stationary noise (no cumulative drift)
        np.random.seed(7)
        close = 1900.0 + np.random.randn(n) * 0.05   # stationary, not cumsum
        high  = close + 0.1
        low   = close - 0.1
        open_ = np.roll(close, 1); open_[0] = close[0]
        bars = pd.DataFrame({
            "open": open_, "high": high, "low": low, "close": close, "volume": 1000
        }, index=idx)
        result = detect_regime(bars)
        assert result in ("ACCUMULATION", "DISTRIBUTION")

    def test_high_atr_returns_volatile(self):
        """ATR spike → VOLATILE, overrides ADX"""
        n = 50
        idx = pd.date_range("2023-01-01", periods=n, freq="4h", tz="UTC")
        close = np.ones(n) * 1900.0
        # Normal ATR for first 45 bars, then huge spike for last 5
        high = close + 5.0
        low  = close - 5.0
        # Last bar: enormous range → ATR spike
        high[-1] = close[-1] + 200.0
        low[-1]  = close[-1] - 200.0
        open_ = np.roll(close, 1); open_[0] = close[0]
        bars = pd.DataFrame({
            "open": open_, "high": high, "low": low, "close": close, "volume": 1000
        }, index=idx)
        result = detect_regime(bars)
        assert result == "VOLATILE"

    def test_returns_string_not_none_for_valid_data(self):
        bars = _make_h4_bars(n=40, trend="flat")
        result = detect_regime(bars)
        assert result is not None
        assert isinstance(result, str)
        assert result in ("TRENDING", "ACCUMULATION", "DISTRIBUTION", "VOLATILE")
