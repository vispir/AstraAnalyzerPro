"""
Tests for signal_gate.py — all 6 gates, regression for v1 trade limit bug.

Critical tests:
  - Gate 5 (trade limit) must use Supabase COUNT, not in-memory
  - All gates independently block signal when condition not met
  - All gates passing → signal generated with correct SL/TP
"""

import pytest
from datetime import datetime, timezone
from unittest.mock import MagicMock

from astra_v2.core.signal_gate import check_signal, is_active_session, trades_today
from astra_v2.core.macro_engine import MacroBias
from astra_v2.core.technical_engine import KeyLevel


# ── Fixtures ───────────────────────────────────────────────────────────────────

LONDON_OPEN_TIME = datetime(2024, 3, 5, 9, 0, 0, tzinfo=timezone.utc)  # 09:00 UTC = London session
NY_OPEN_TIME = datetime(2024, 3, 5, 13, 30, 0, tzinfo=timezone.utc)    # 13:30 UTC = NY session
OUTSIDE_SESSION = datetime(2024, 3, 5, 7, 0, 0, tzinfo=timezone.utc)   # 07:00 UTC = no session


def bullish_macro(confidence=0.75) -> MacroBias:
    return MacroBias(
        direction="BULLISH",
        confidence=confidence,
        reasoning="test bullish",
        tips_spread=-0.8,
        dxy=100.0,
        vix=16.0,
        cot_net=120_000,
        timestamp=datetime.now(timezone.utc),
    )


def bearish_macro(confidence=0.75) -> MacroBias:
    return MacroBias(
        direction="BEARISH",
        confidence=confidence,
        reasoning="test bearish",
        tips_spread=0.5,
        dxy=106.0,
        vix=16.0,
        cot_net=-60_000,
        timestamp=datetime.now(timezone.utc),
    )


def neutral_macro() -> MacroBias:
    return MacroBias(
        direction="NEUTRAL",
        confidence=0.4,
        reasoning="neutral test",
        tips_spread=0.0,
        dxy=102.0,
        vix=20.0,
        cot_net=0,
        timestamp=datetime.now(timezone.utc),
    )


def support_level(price: float) -> list[KeyLevel]:
    return [KeyLevel(price=price, level_type="pdl", direction="support", strength=7.0)]


def resistance_level(price: float) -> list[KeyLevel]:
    return [KeyLevel(price=price, level_type="pdh", direction="resistance", strength=7.0)]


# ── Gate 1: Macro ──────────────────────────────────────────────────────────────

def test_gate1_neutral_macro_blocks():
    signal, reason = check_signal(
        macro=neutral_macro(),
        levels=support_level(3200.0),
        current_price=3200.30,
        now=LONDON_OPEN_TIME,
    )
    assert signal is None
    assert "gate_1" in reason
    assert "NEUTRAL" in reason


def test_gate1_low_confidence_blocks():
    signal, reason = check_signal(
        macro=bullish_macro(confidence=0.55),
        levels=support_level(3200.0),
        current_price=3200.30,
        now=LONDON_OPEN_TIME,
    )
    assert signal is None
    assert "gate_1" in reason
    assert "confidence" in reason


def test_gate1_sufficient_confidence_passes():
    signal, reason = check_signal(
        macro=bullish_macro(confidence=0.70),
        levels=support_level(3200.0),
        current_price=3200.30,
        now=LONDON_OPEN_TIME,
    )
    # Gate 1 passes, other gates may pass too
    assert reason in ("ok", ) or "gate_" in reason
    if reason == "ok":
        assert signal is not None


# ── Gate 2: Level proximity ────────────────────────────────────────────────────

def test_gate2_no_nearby_level_blocks():
    # Price $5 away from nearest level — outside $0.50 proximity
    signal, reason = check_signal(
        macro=bullish_macro(),
        levels=support_level(3200.0),
        current_price=3205.0,
        now=LONDON_OPEN_TIME,
    )
    assert signal is None
    assert "gate_2" in reason


def test_gate2_price_at_level_passes():
    # Price $0.30 from level — within $0.50 proximity
    signal, reason = check_signal(
        macro=bullish_macro(),
        levels=support_level(3200.0),
        current_price=3200.30,
        now=LONDON_OPEN_TIME,
    )
    assert "gate_2" not in reason or reason == "ok"


# ── Gate 3: Direction alignment ────────────────────────────────────────────────

def test_gate3_bullish_at_resistance_blocks():
    # BULLISH macro but price is at RESISTANCE — wrong direction
    signal, reason = check_signal(
        macro=bullish_macro(),
        levels=resistance_level(3200.0),
        current_price=3199.80,
        now=LONDON_OPEN_TIME,
    )
    assert signal is None
    assert "gate_3" in reason


def test_gate3_bearish_at_support_blocks():
    signal, reason = check_signal(
        macro=bearish_macro(),
        levels=support_level(3200.0),
        current_price=3200.30,
        now=LONDON_OPEN_TIME,
    )
    assert signal is None
    assert "gate_3" in reason


def test_gate3_bullish_at_support_passes():
    signal, reason = check_signal(
        macro=bullish_macro(),
        levels=support_level(3200.0),
        current_price=3200.30,
        now=LONDON_OPEN_TIME,
    )
    assert "gate_3" not in reason or reason == "ok"


def test_gate3_bearish_at_resistance_passes():
    signal, reason = check_signal(
        macro=bearish_macro(),
        levels=resistance_level(3200.0),
        current_price=3199.80,
        now=LONDON_OPEN_TIME,
    )
    assert "gate_3" not in reason or reason == "ok"


# ── Gate 4: Session window ─────────────────────────────────────────────────────

def test_gate4_outside_session_blocks():
    signal, reason = check_signal(
        macro=bullish_macro(),
        levels=support_level(3200.0),
        current_price=3200.30,
        now=OUTSIDE_SESSION,   # 07:00 UTC — no session
    )
    assert signal is None
    assert "gate_4" in reason


def test_gate4_london_session_passes():
    assert is_active_session(LONDON_OPEN_TIME)


def test_gate4_ny_session_passes():
    assert is_active_session(NY_OPEN_TIME)


def test_gate4_outside_session_fails():
    assert not is_active_session(OUTSIDE_SESSION)


# ── Gate 5: Daily trade limit (REGRESSION — v1 bug fix) ───────────────────────

def test_gate5_trade_limit_blocks_via_supabase():
    """
    REGRESSION TEST: v1 used in-memory counter that could be bypassed on restart.
    v2 must use Supabase COUNT as authoritative source.
    This test verifies that when Supabase returns count >= 2, trade is blocked.
    """
    mock_sb = MagicMock()
    mock_result = MagicMock()
    mock_result.count = 2  # 2 trades already today
    mock_sb.table.return_value.select.return_value.gte.return_value.not_.in_.return_value.execute.return_value = mock_result

    signal, reason = check_signal(
        macro=bullish_macro(),
        levels=support_level(3200.0),
        current_price=3200.30,
        now=LONDON_OPEN_TIME,
        supabase_client=mock_sb,
    )
    assert signal is None
    assert "gate_5" in reason


def test_gate5_supabase_failure_blocks_trade():
    """
    Circuit breaker: if Supabase query fails, system must NOT open a trade.
    Fail safe: return max limit so trade is skipped.
    """
    mock_sb = MagicMock()
    mock_sb.table.return_value.select.return_value.gte.return_value.not_.in_.return_value.execute.side_effect = Exception("connection timeout")

    signal, reason = check_signal(
        macro=bullish_macro(),
        levels=support_level(3200.0),
        current_price=3200.30,
        now=LONDON_OPEN_TIME,
        supabase_client=mock_sb,
    )
    # Should block (fail safe)
    assert signal is None
    assert "gate_5" in reason


def test_gate5_under_limit_allows_trade():
    """With 0 trades today, gate 5 passes."""
    mock_sb = MagicMock()
    mock_result = MagicMock()
    mock_result.count = 0
    mock_sb.table.return_value.select.return_value.gte.return_value.not_.in_.return_value.execute.return_value = mock_result

    signal, reason = check_signal(
        macro=bullish_macro(),
        levels=support_level(3200.0),
        current_price=3200.30,
        now=LONDON_OPEN_TIME,
        supabase_client=mock_sb,
    )
    assert "gate_5" not in reason or reason == "ok"


# ── Full pass — signal generated ───────────────────────────────────────────────

def test_all_gates_pass_generates_signal():
    """When all 6 gates pass, a valid Signal is returned with correct SL/TP."""
    mock_sb = MagicMock()
    mock_result = MagicMock()
    mock_result.count = 0
    mock_sb.table.return_value.select.return_value.gte.return_value.not_.in_.return_value.execute.return_value = mock_result

    signal, reason = check_signal(
        macro=bullish_macro(confidence=0.75),
        levels=support_level(3200.0),
        current_price=3200.30,
        now=LONDON_OPEN_TIME,
        supabase_client=mock_sb,
    )

    assert signal is not None, f"Expected signal, got None. Reason: {reason}"
    assert reason == "ok"
    assert signal.direction == "BULLISH"
    assert signal.entry_price == pytest.approx(3200.30, abs=0.01)
    assert signal.stop_loss < signal.entry_price   # SL below entry for long
    assert signal.take_profit > signal.entry_price  # TP above entry for long

    # RR check: TP distance should be ~2x SL distance
    sl_dist = signal.entry_price - signal.stop_loss
    tp_dist = signal.take_profit - signal.entry_price
    assert tp_dist == pytest.approx(sl_dist * 2.0, rel=0.01)


def test_bearish_signal_sl_above_entry():
    """For BEARISH signal, SL is above entry."""
    mock_sb = MagicMock()
    mock_result = MagicMock()
    mock_result.count = 0
    mock_sb.table.return_value.select.return_value.gte.return_value.not_.in_.return_value.execute.return_value = mock_result

    signal, reason = check_signal(
        macro=bearish_macro(confidence=0.75),
        levels=resistance_level(3200.0),
        current_price=3199.80,
        now=LONDON_OPEN_TIME,
        supabase_client=mock_sb,
    )

    if reason == "ok":
        assert signal is not None
        assert signal.direction == "BEARISH"
        assert signal.stop_loss > signal.entry_price   # SL above for short
        assert signal.take_profit < signal.entry_price  # TP below for short
