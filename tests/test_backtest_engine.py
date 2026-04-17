"""
Tests for backtest/engine.py

Critical paths:
  - No look-ahead bias (MUST PASS — verified by index slice check)
  - Slippage applied on entry
  - Walk-forward split: test set starts after training window
  - _simulate_trade: SL hit, TP hit, partial TP, BE, trail
  - BacktestResult: computed metrics (WR, PF, MaxDD, avgRR)
  - Daily trade count respected (max 2 per day in default config)
"""

import pytest
import pandas as pd
import numpy as np
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch

from astra_v2.backtest.engine import (
    Trade,
    BacktestResult,
    _simulate_trade,
    run_backtest,
)
from astra_v2 import config
from astra_v2.core.signal_gate import Signal
from astra_v2.core.technical_engine import ActiveLevel, KeyLevel
from astra_v2.core.macro_engine import MacroBias


# ── Helpers ────────────────────────────────────────────────────────────────────

def open_long(entry=3200.0, sl=3193.0, tp=3214.0, partial_tp=3207.0) -> Trade:
    return Trade(
        direction="BULLISH",
        entry=entry,
        stop_loss=sl,
        take_profit=tp,
        partial_tp=partial_tp,
        opened_at=datetime(2024, 3, 5, 9, 0, tzinfo=timezone.utc),
    )


def open_short(entry=3200.0, sl=3207.0, tp=3186.0, partial_tp=3193.0) -> Trade:
    return Trade(
        direction="BEARISH",
        entry=entry,
        stop_loss=sl,
        take_profit=tp,
        partial_tp=partial_tp,
        opened_at=datetime(2024, 3, 5, 9, 0, tzinfo=timezone.utc),
    )


def bar(high: float, low: float) -> pd.Series:
    return pd.Series({"open": (high + low) / 2, "high": high, "low": low, "close": (high + low) / 2})


# ── _simulate_trade: SL hit ────────────────────────────────────────────────────

class TestSimulateTradeSL:
    def test_long_sl_hit(self):
        trade = open_long(entry=3200.0, sl=3193.0)
        _simulate_trade(trade, bar(high=3195.0, low=3192.0))  # low touches SL
        assert trade.status in ("sl", "be_sl")
        assert trade.exit_price is not None
        assert trade.pnl < 0

    def test_short_sl_hit(self):
        trade = open_short(entry=3200.0, sl=3207.0)
        _simulate_trade(trade, bar(high=3208.0, low=3200.0))  # high touches SL
        assert trade.status in ("sl", "be_sl")
        assert trade.pnl < 0

    def test_sl_not_hit_when_low_above_sl(self):
        trade = open_long(entry=3200.0, sl=3193.0)
        _simulate_trade(trade, bar(high=3205.0, low=3195.0))  # low above SL
        assert trade.status == "open"

    def test_trade_logs_immediate_adverse_opening_bar(self):
        trade = open_long(entry=3200.0, sl=3193.0)
        _simulate_trade(trade, bar(high=3200.0, low=3198.0))
        assert trade.opening_bar_behavior == "adverse_only"
        assert trade.first_excursion_side == "adverse"
        assert trade.bars_to_first_drawdown == 0
        assert trade.bars_to_first_profit is None


# ── _simulate_trade: TP hit ────────────────────────────────────────────────────

class TestSimulateTradeTP:
    def test_long_tp_hit(self):
        trade = open_long(entry=3200.0, sl=3193.0, tp=3214.0, partial_tp=3207.0)
        # Partial TP then full TP in same bar
        _simulate_trade(trade, bar(high=3220.0, low=3195.0))
        assert trade.status == "tp"
        assert trade.pnl > 0

    def test_short_tp_hit(self):
        trade = open_short(entry=3200.0, sl=3207.0, tp=3186.0, partial_tp=3193.0)
        _simulate_trade(trade, bar(high=3200.0, low=3182.0))
        assert trade.status == "tp"
        assert trade.pnl > 0

    def test_tp_not_hit_when_high_below_tp(self):
        trade = open_long(entry=3200.0, sl=3193.0, tp=3214.0)
        _simulate_trade(trade, bar(high=3210.0, low=3195.0))
        assert trade.status == "open"

    def test_trade_tracks_favorable_and_adverse_excursions(self):
        trade = open_long(entry=3200.0, sl=3193.0, tp=3214.0)
        _simulate_trade(trade, bar(high=3206.0, low=3198.5))
        assert trade.max_favorable_excursion_usd == pytest.approx(6.0, abs=0.01)
        assert trade.max_adverse_excursion_usd == pytest.approx(1.5, abs=0.01)
        assert trade.bars_to_first_profit == 0
        assert trade.bars_to_first_drawdown == 0


# ── _simulate_trade: Partial TP ────────────────────────────────────────────────

class TestSimulateTradePartial:
    def test_partial_tp_closes_half(self):
        trade = open_long(entry=3200.0, sl=3193.0, tp=3214.0, partial_tp=3207.0)
        _simulate_trade(trade, bar(high=3208.0, low=3195.0))  # hits partial only
        assert trade.partial_closed is True
        assert trade.status == "open"  # still open, full TP not hit
        assert trade.pnl > 0  # partial pnl already booked

    def test_partial_tp_not_hit_again_after_close(self):
        trade = open_long(entry=3200.0, sl=3193.0, tp=3214.0, partial_tp=3207.0)
        trade.partial_closed = True
        pnl_before = trade.pnl
        _simulate_trade(trade, bar(high=3210.0, low=3200.0))  # within partial range
        assert trade.partial_closed is True  # still True, no double close


# ── _simulate_trade: Breakeven ─────────────────────────────────────────────────

class TestSimulateTradeBE:
    def test_be_triggered_at_1r(self):
        """SL moves to entry when profit >= 1R."""
        trade = open_long(entry=3200.0, sl=3193.0, tp=3214.0, partial_tp=3207.0)
        sl_dist = 3200.0 - 3193.0  # $7
        be_trigger_price = 3200.0 + sl_dist * config.BE_TRIGGER_RR

        _simulate_trade(trade, bar(high=be_trigger_price + 0.1, low=3196.0))
        assert trade.be_moved is True
        assert trade.stop_loss == pytest.approx(3200.0, abs=0.01)

    def test_be_not_triggered_before_threshold(self):
        trade = open_long(entry=3200.0, sl=3193.0, tp=3214.0, partial_tp=3207.0)
        _simulate_trade(trade, bar(high=3203.0, low=3196.0))  # small profit
        assert trade.be_moved is False
        assert trade.stop_loss == pytest.approx(3193.0, abs=0.01)


# ── BacktestResult metrics ──────────────────────────────────────────────────────

class TestBacktestResult:
    def _make_result(self, pnl_values: list[float]) -> BacktestResult:
        trades = []
        for i, pnl in enumerate(pnl_values):
            t = open_long()
            t.pnl = pnl
            t.status = "tp" if pnl > 0 else "sl"
            t.exit_price = t.entry + pnl
            trades.append(t)
        equity = [10_000.0]
        balance = 10_000.0
        for pnl in pnl_values:
            balance += pnl
            equity.append(balance)
        return BacktestResult(
            trades=trades,
            equity_curve=equity,
            start_balance=10_000.0,
            end_balance=balance,
        )

    def test_win_rate(self):
        result = self._make_result([100.0, 100.0, -50.0, 100.0])
        assert result.win_rate == pytest.approx(0.75, abs=0.01)

    def test_profit_factor(self):
        result = self._make_result([100.0, 100.0, -50.0])
        # PF = 200 / 50 = 4.0
        assert result.profit_factor == pytest.approx(4.0, abs=0.01)

    def test_profit_factor_infinite_on_no_losses(self):
        result = self._make_result([100.0, 100.0])
        assert result.profit_factor == float("inf")

    def test_max_drawdown_captures_worst(self):
        # Equity: 10000 → 10100 → 10000 → 9950 → 10050
        result = self._make_result([100.0, -100.0, -50.0, 100.0])
        # Peak after first trade: 10100, then drops to 9950 → DD = (10100-9950)/10100 ≈ 1.49%
        assert result.max_drawdown_pct > 0.0
        assert result.max_drawdown_pct < 5.0

    def test_total_trades_excludes_open(self):
        trades = []
        for pnl in [100.0, -50.0]:
            t = open_long()
            t.pnl = pnl
            t.status = "tp" if pnl > 0 else "sl"
            t.exit_price = t.entry + pnl
            trades.append(t)
        # Add one open trade
        open_t = open_long()
        open_t.status = "open"
        trades.append(open_t)

        result = BacktestResult(
            trades=trades,
            equity_curve=[10_000.0, 10_100.0, 10_050.0],
            start_balance=10_000.0,
            end_balance=10_050.0,
        )
        assert result.total_trades == 2  # not 3


# ── No look-ahead bias — structural test ──────────────────────────────────────

class TestNoLookAhead:
    def test_bars_slice_is_strictly_before_current_bar(self):
        """
        Verify the backtest engine slices bars with `bars[bars.index < ts]`
        not `bars[bars.index <= ts]`, ensuring the current bar is excluded.
        """
        import inspect
        from astra_v2.backtest import engine
        source = inspect.getsource(engine.run_backtest)
        assert "bars.index < ts" in source, \
            "LOOK-AHEAD BUG: backtest must use `bars.index < ts` not `<=`"

    def test_level_cache_uses_slice_before_bar(self):
        """Levels are computed once per day from bars BEFORE the current bar."""
        import inspect
        from astra_v2.backtest import engine
        source = inspect.getsource(engine.run_backtest)
        # Both the condition and the extract_levels call should use bars_so_far
        assert "bars_so_far" in source, "Expected bars_so_far variable for anti-look-ahead slice"
        assert "bars[bars.index < ts]" in source, "Level computation must use strict past slice"


def test_run_backtest_executes_v4_signal_on_m1_trigger():
    idx = pd.date_range("2024-01-01", periods=30, freq="15min", tz="UTC")
    bars = pd.DataFrame(
        {
            "open": np.linspace(3200.0, 3203.0, len(idx)),
            "high": np.linspace(3200.5, 3203.5, len(idx)),
            "low": np.linspace(3199.5, 3202.5, len(idx)),
            "close": np.linspace(3200.2, 3203.2, len(idx)),
            "volume": 1000.0,
        },
        index=idx,
    )
    m1_idx = pd.date_range("2024-01-01 07:15:00", periods=15, freq="1min", tz="UTC")
    m1_bars = pd.DataFrame(
        {
            "open": [3201.0] * 15,
            "high": [3201.1, 3201.2, 3202.6] + [3202.7] * 12,
            "low": [3200.9] * 15,
            "close": [3201.0, 3201.1, 3202.4] + [3202.5] * 12,
            "volume": 100.0,
        },
        index=m1_idx,
    )
    h4_idx = pd.date_range("2023-12-20", periods=30, freq="4h", tz="UTC")
    h4_bars = pd.DataFrame(
        {
            "open": np.linspace(3180.0, 3200.0, len(h4_idx)),
            "high": np.linspace(3182.0, 3202.0, len(h4_idx)),
            "low": np.linspace(3178.0, 3198.0, len(h4_idx)),
            "close": np.linspace(3181.0, 3201.0, len(h4_idx)),
            "volume": 5000.0,
        },
        index=h4_idx,
    )
    macro = MacroBias(
        direction="BULLISH",
        confidence=0.7,
        reasoning="test",
        tips_spread=0.0,
        dxy=100.0,
        vix=18.0,
        cot_net=0,
        timestamp=datetime.now(timezone.utc),
    )
    level = ActiveLevel(level=KeyLevel(price=3200.0, level_type="pdl", direction="support", strength=7.0), distance_usd=0.5)
    signal = Signal(
        direction="BULLISH",
        entry_price=3202.5,
        stop_loss=3199.0,
        take_profit=3208.0,
        partial_tp=3205.0,
        level=level,
        macro_bias=macro,
        timestamp=idx[28].to_pydatetime(),
        strategy_id="sweep_reversal_v4",
        execution_timeframe="M1",
        entry_trigger_price=3202.5,
    )

    class FakeStrategy:
        strategy_id = "sweep_reversal_v4"
        required_level_types = ()
        required_timeframes = ("M1", "H4")

        def __init__(self):
            self.called = False

        def generate_signal(self, context, *, supabase_client=None):
            if not self.called:
                self.called = True
                return signal, "ok"
            return None, "nope"

    with patch("astra_v2.backtest.engine.get_strategy", return_value=FakeStrategy()):
        with patch("astra_v2.backtest.engine.compute_macro_features", return_value={}):
            with patch("astra_v2.backtest.llm_proxy.proxy_macro_bias", return_value=macro):
                with patch("astra_v2.backtest.engine.extract_levels", return_value=[]):
                    result = run_backtest(
                        bars=bars,
                        fred_df=pd.DataFrame(),
                        yfinance_df=pd.DataFrame(),
                        cot_df=pd.DataFrame(),
                        m1_bars=m1_bars,
                        h4_bars=h4_bars,
                        mode="proxy",
                        strategy_id="sweep_reversal_v4",
                        start_balance=10_000.0,
                        wf_train_months=0,
                        wf_test_months=1,
                    )

    assert result.trades
    trade = result.trades[0]
    assert trade.strategy_id == "sweep_reversal_v4"
    assert trade.execution_timeframe == "M1"
    assert trade.opened_at == datetime(2024, 1, 1, 7, 17, tzinfo=timezone.utc)
    assert trade.entry_trigger_price == pytest.approx(3202.5, abs=0.01)
