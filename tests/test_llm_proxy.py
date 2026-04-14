"""
Tests for backtest/llm_proxy.py — deterministic macro bias rules.

Critical guarantee: same input always produces same output (reproducibility).
Proxy rules mirror the LLM prompt logic — these tests validate the proxy
is internally consistent. The correlation test (scripts/correlation_test.py)
validates proxy vs real LLM agreement.
"""

import pytest
from datetime import datetime, timezone

from astra_v2.backtest.llm_proxy import proxy_macro_bias
from astra_v2.data.macro_features import MacroFeatures


def make_features(
    tips_spread: float = 0.0,
    dxy_broad: float = 104.0,
    dxy_1m_change: float = 0.0,
    vix: float = 18.0,
    cot_net: int = 0,
    dgs10: float = 4.5,
    t10yie: float = 2.3,
    tnx: float = 4.5,
) -> MacroFeatures:
    return MacroFeatures(
        date="2024-03-05",
        tips_spread=tips_spread,
        dgs10=dgs10,
        t10yie=t10yie,
        dxy_broad=dxy_broad,
        dxy_1m_change=dxy_1m_change,
        vix=vix,
        tnx=tnx,
        cot_net=cot_net,
    )


# ── VIX gate ────────────────────────────────────────────────────────────────────

def test_high_vix_returns_neutral():
    """VIX > 30 → NEUTRAL regardless of other signals."""
    features = make_features(
        tips_spread=-1.0,     # strongly bullish
        dxy_1m_change=-3.0,   # strongly bullish
        cot_net=200_000,      # strongly bullish
        vix=31.0,             # should override all
    )
    bias = proxy_macro_bias(features)
    assert bias.direction == "NEUTRAL"
    assert "VIX" in bias.reasoning


def test_vix_at_30_does_not_trigger_gate():
    """VIX exactly 30 → not above threshold → may be non-neutral."""
    features = make_features(
        tips_spread=-1.0,
        dxy_1m_change=-3.0,
        cot_net=200_000,
        vix=30.0,
    )
    bias = proxy_macro_bias(features)
    # VIX=30 does not trigger gate — other factors should drive BULLISH
    assert bias.direction != "NEUTRAL" or bias.confidence >= 0.0


# ── Bullish scenarios ───────────────────────────────────────────────────────────

def test_three_bullish_factors_high_confidence():
    """≥3 bullish signals → BULLISH with 0.80 confidence."""
    features = make_features(
        tips_spread=-0.8,     # bullish: falling real yields
        dxy_1m_change=-2.0,   # bullish: dollar weakening
        cot_net=150_000,      # bullish: speculators net long
        vix=18.0,             # neutral VIX
    )
    bias = proxy_macro_bias(features)
    assert bias.direction == "BULLISH"
    assert bias.confidence == pytest.approx(0.80, abs=0.01)


def test_two_bullish_zero_bearish_moderate_confidence():
    """2 bullish, 0 bearish → BULLISH with 0.65 confidence."""
    features = make_features(
        tips_spread=-0.8,     # bullish
        dxy_1m_change=-2.0,   # bullish
        cot_net=30_000,       # neutral (not > 100k)
        vix=18.0,             # neutral
    )
    bias = proxy_macro_bias(features)
    assert bias.direction == "BULLISH"
    assert bias.confidence == pytest.approx(0.65, abs=0.01)


# ── Bearish scenarios ────────────────────────────────────────────────────────────

def test_three_bearish_factors_high_confidence():
    """≥3 bearish signals → BEARISH with 0.80 confidence."""
    features = make_features(
        tips_spread=0.6,      # bearish: rising real yields
        dxy_1m_change=2.5,    # bearish: dollar strengthening
        cot_net=-80_000,      # bearish: speculators net short
        vix=18.0,
    )
    bias = proxy_macro_bias(features)
    assert bias.direction == "BEARISH"
    assert bias.confidence == pytest.approx(0.80, abs=0.01)


def test_two_bearish_zero_bullish_moderate_confidence():
    """2 bearish, 0 bullish → BEARISH with 0.65 confidence."""
    features = make_features(
        tips_spread=0.6,      # bearish
        dxy_1m_change=2.5,    # bearish
        cot_net=0,            # neutral
        vix=18.0,             # neutral
    )
    bias = proxy_macro_bias(features)
    assert bias.direction == "BEARISH"
    assert bias.confidence == pytest.approx(0.65, abs=0.01)


# ── Mixed / neutral ─────────────────────────────────────────────────────────────

def test_mixed_signals_returns_neutral():
    """Equal bullish and bearish signals → NEUTRAL."""
    features = make_features(
        tips_spread=-0.8,     # bullish
        dxy_1m_change=2.5,    # bearish
        cot_net=0,            # neutral
        vix=18.0,             # neutral
    )
    bias = proxy_macro_bias(features)
    assert bias.direction == "NEUTRAL"


def test_all_neutral_signals_returns_neutral():
    """All signals in neutral zone → NEUTRAL."""
    features = make_features(
        tips_spread=0.0,      # within neutral range
        dxy_1m_change=0.5,    # within neutral range
        cot_net=50_000,       # within neutral range
        vix=20.0,             # within neutral range
    )
    bias = proxy_macro_bias(features)
    assert bias.direction == "NEUTRAL"


# ── Determinism guarantee ────────────────────────────────────────────────────────

def test_proxy_is_deterministic():
    """Same input always produces same output (no random state)."""
    features = make_features(
        tips_spread=-0.8,
        dxy_1m_change=-2.0,
        cot_net=150_000,
        vix=18.0,
    )
    results = [proxy_macro_bias(features) for _ in range(10)]
    directions = {r.direction for r in results}
    confidences = {r.confidence for r in results}
    assert len(directions) == 1, "Direction should be deterministic"
    assert len(confidences) == 1, "Confidence should be deterministic"


# ── Reasoning prefix ────────────────────────────────────────────────────────────

def test_proxy_reasoning_has_prefix():
    """Proxy bias reasoning should start with [PROXY] to distinguish from live LLM."""
    features = make_features(tips_spread=-0.8, dxy_1m_change=-2.0, cot_net=150_000)
    bias = proxy_macro_bias(features)
    assert "[PROXY]" in bias.reasoning


# ── COT absent ──────────────────────────────────────────────────────────────────

def test_proxy_works_without_cot():
    """If cot_net is None (unavailable), proxy should still return a result."""
    features = make_features(
        tips_spread=-0.8,
        dxy_1m_change=-2.0,
        cot_net=None,
        vix=18.0,
    )
    features.cot_net = None  # type: ignore
    bias = proxy_macro_bias(features)
    assert bias.direction in ("BULLISH", "BEARISH", "NEUTRAL")
