"""Tests for SMC strategies: smc_fvg_v1 + smc_ob_v1"""
import pytest
import pandas as pd
import numpy as np
from datetime import datetime, timezone

from astra_v2.strategies.smc_fvg_v1 import SmcFvgV1
from astra_v2.strategies.smc_ob_v1 import SmcObV1
from astra_v2.strategies.base import StrategyContext
from astra_v2.core.market_structure import MarketStructure, BOS_BULLISH, BOS_BEARISH, Swing
from astra_v2.core.fair_value_gap import FairValueGap, FVG_ACTIVE
from astra_v2.core.order_block import OrderBlock, OB_VALID
from astra_v2.core.macro_engine import MacroBias


def _macro(direction="BULLISH", confidence=0.7):
    return MacroBias(
        direction=direction,
        confidence=confidence,
        reasoning="test",
        tips_spread=0.0,
        dxy=None,
        vix=None,
        cot_net=None,
        timestamp=datetime(2023, 6, 1, tzinfo=timezone.utc),
    )


def _make_bars(n=50):
    idx = pd.date_range("2023-01-01", periods=n, freq="15min", tz="UTC")
    close = np.linspace(1900, 1950, n)
    return pd.DataFrame({
        "open":  close - 1,
        "high":  close + 2,
        "low":   close - 2,
        "close": close,
        "volume": [1000] * n,
    }, index=idx)


def _make_h4_bars(n=30, uptrend=True):
    idx = pd.date_range("2023-01-01", periods=n, freq="4h", tz="UTC")
    if uptrend:
        close = np.linspace(1800, 1950, n)
    else:
        close = np.linspace(1950, 1800, n)
    return pd.DataFrame({
        "open": close - 2, "high": close + 5, "low": close - 5,
        "close": close, "volume": [1000] * n,
    }, index=idx)


def _make_bar_series(price=1930.0, high=None, low=None, open_=None):
    if high is None: high = price + 2
    if low  is None: low  = price - 2
    if open_ is None: open_ = price - 0.5
    return pd.Series({
        "open": open_, "high": high, "low": low, "close": price, "volume": 1000
    }, name=pd.Timestamp("2023-06-01 13:00", tz="UTC"))


def _ms_bullish():
    """Market structure with BOS_BULLISH."""
    ms = MarketStructure()
    ms.last_bos = BOS_BULLISH
    ms.trend = "BULLISH"
    return ms


def _ms_bearish():
    ms = MarketStructure()
    ms.last_bos = BOS_BEARISH
    ms.trend = "BEARISH"
    return ms


def _bull_fvg(entered=False):
    """Active bullish FVG that price is retesting (bar dips into gap)."""
    fvg = FairValueGap(
        direction="BULLISH",
        top=1935.0,   # top of gap
        bottom=1930.0, # bottom of gap
        formed_at=datetime(2023, 6, 1, 10, tzinfo=timezone.utc),
        formed_bar_idx=10,
        status=FVG_ACTIVE,
    )
    return fvg


def _bear_fvg():
    fvg = FairValueGap(
        direction="BEARISH",
        top=1940.0,
        bottom=1935.0,
        formed_at=datetime(2023, 6, 1, 10, tzinfo=timezone.utc),
        formed_bar_idx=10,
        status=FVG_ACTIVE,
    )
    return fvg


def _bull_ob():
    ob = OrderBlock(
        direction="BULLISH",
        top=1932.0,
        bottom=1928.0,
        formed_at=datetime(2023, 6, 1, 10, tzinfo=timezone.utc),
        formed_bar_idx=10,
        status=OB_VALID,
    )
    return ob


def _bear_ob():
    ob = OrderBlock(
        direction="BEARISH",
        top=1940.0,
        bottom=1936.0,
        formed_at=datetime(2023, 6, 1, 10, tzinfo=timezone.utc),
        formed_bar_idx=10,
        status=OB_VALID,
    )
    return ob


def _ctx(
    strategy_id,
    direction="BULLISH",
    session_hour=14,           # NY session
    calendar_blackout=False,
    regime="TRENDING",
    fvgs=None,
    order_blocks=None,
    ms=None,
    dxy_trend=None,
    local_trade_count=0,
    price=1930.0,
):
    now = datetime(2023, 6, 1, session_hour, 0, tzinfo=timezone.utc)
    if ms is None:
        ms = _ms_bullish() if direction == "BULLISH" else _ms_bearish()
    bars = _make_bars(50)
    bar = _make_bar_series(
        price=price,
        high=price + 5,
        low=price - 5 if direction == "BULLISH" else price - 10,
    )
    return StrategyContext(
        strategy_id=strategy_id,  # type: ignore
        now=now,
        current_price=price,
        current_bar=bar,
        bars_so_far=bars,
        levels=[],
        macro=_macro(direction=direction),
        local_trade_count=local_trade_count,
        h4_bars=_make_h4_bars(30, uptrend=(direction == "BULLISH")),
        market_structure=ms,
        fvgs=fvgs or [],
        order_blocks=order_blocks or [],
        regime=regime,
        calendar_blackout=calendar_blackout,
        dxy_trend=dxy_trend,
    )


# ── SmcFvgV1 tests ────────────────────────────────────────────────────────────

class TestSmcFvgV1:
    def setup_method(self):
        self.strategy = SmcFvgV1()

    def test_bullish_setup_produces_signal(self):
        """BOS_BULLISH + unfilled bull FVG + price entering FVG + TRENDING → BULLISH signal"""
        fvg = _bull_fvg()
        # Current bar: low=1931 ≤ fvg.top=1935 AND close=1932 ≥ fvg.bottom=1930 → enters FVG
        ctx = _ctx("smc_fvg_v1", direction="BULLISH", fvgs=[fvg], price=1932.0)
        signal, reason = self.strategy.generate_signal(ctx)
        assert signal is not None, f"Expected signal, got: {reason}"
        assert signal.direction == "BULLISH"
        assert signal.strategy_id == "smc_fvg_v1"

    def test_bearish_setup_produces_signal(self):
        """BOS_BEARISH + unfilled bear FVG + price entering FVG → BEARISH signal"""
        fvg = _bear_fvg()
        # Current bar: high=1937 ≥ fvg.bottom=1935 AND close=1937 ≤ fvg.top=1940 → enters FVG
        ctx = _ctx("smc_fvg_v1", direction="BEARISH", fvgs=[fvg], price=1937.0)
        signal, reason = self.strategy.generate_signal(ctx)
        assert signal is not None, f"Expected signal, got: {reason}"
        assert signal.direction == "BEARISH"

    def test_calendar_blackout_blocks_signal(self):
        fvg = _bull_fvg()
        ctx = _ctx("smc_fvg_v1", fvgs=[fvg], calendar_blackout=True)
        signal, reason = self.strategy.generate_signal(ctx)
        assert signal is None
        assert "calendar" in reason

    def test_volatile_regime_blocks_signal(self):
        fvg = _bull_fvg()
        ctx = _ctx("smc_fvg_v1", fvgs=[fvg], regime="VOLATILE")
        signal, reason = self.strategy.generate_signal(ctx)
        assert signal is None
        assert "regime" in reason

    def test_accumulation_regime_allowed(self):
        """ACCUMULATION is now in ALLOWED_REGIMES — signal should pass regime gate"""
        fvg = _bull_fvg()
        ctx = _ctx("smc_fvg_v1", fvgs=[fvg], regime="ACCUMULATION")
        # Should NOT be blocked by regime gate (may fail at a later gate)
        signal, reason = self.strategy.generate_signal(ctx)
        assert "regime" not in reason

    def test_no_active_fvgs_blocks_signal(self):
        ctx = _ctx("smc_fvg_v1", fvgs=[])
        signal, reason = self.strategy.generate_signal(ctx)
        assert signal is None
        assert "fvg" in reason

    def test_daily_limit_blocks_signal(self):
        from astra_v2 import config
        fvg = _bull_fvg()
        ctx = _ctx("smc_fvg_v1", fvgs=[fvg], local_trade_count=config.SMC_FVG_V1_MAX_TRADES_PER_DAY)
        signal, reason = self.strategy.generate_signal(ctx)
        assert signal is None
        assert "trades" in reason

    def test_price_not_in_fvg_no_signal(self):
        """Active FVG exists but price is not entering it → no signal"""
        fvg = _bull_fvg()
        # bar with high=1928, low=1925 — does NOT enter fvg (top=1935, bottom=1930)
        ctx = _ctx("smc_fvg_v1", fvgs=[fvg], price=1926.0)
        signal, reason = self.strategy.generate_signal(ctx)
        assert signal is None
        assert "fvg" in reason

    def test_dxy_falling_boosts_bullish_size_multiplier(self):
        """DXY FALLING + BULLISH FVG → size_multiplier > 1.0"""
        fvg = _bull_fvg()
        ctx = _ctx("smc_fvg_v1", fvgs=[fvg], price=1932.0, dxy_trend="FALLING")
        signal, reason = self.strategy.generate_signal(ctx)
        if signal is not None:
            assert signal.size_multiplier > 1.0

    def test_no_bos_no_signal(self):
        """No BOS → no signal"""
        from astra_v2.core.market_structure import NO_BOS
        ms = MarketStructure()
        ms.last_bos = NO_BOS
        fvg = _bull_fvg()
        ctx = _ctx("smc_fvg_v1", fvgs=[fvg], ms=ms)
        signal, reason = self.strategy.generate_signal(ctx)
        assert signal is None
        assert "ms" in reason or "bos" in reason.lower() or "NO_BOS" in reason

    def test_outside_session_no_signal(self):
        """Outside London/NY → no signal"""
        fvg = _bull_fvg()
        ctx = _ctx("smc_fvg_v1", fvgs=[fvg], session_hour=4)  # Asian session
        signal, reason = self.strategy.generate_signal(ctx)
        assert signal is None
        assert "session" in reason


# ── SmcObV1 tests ─────────────────────────────────────────────────────────────

class TestSmcObV1:
    def setup_method(self):
        self.strategy = SmcObV1()

    def test_bullish_ob_produces_signal(self):
        """BOS_BULLISH + valid bullish OB + price entering OB → BULLISH signal"""
        ob = _bull_ob()
        # Current bar: low=1929 ≤ ob.top=1932 AND close=1930 ≥ ob.bottom=1928 → in OB
        ctx = _ctx("smc_ob_v1", direction="BULLISH", order_blocks=[ob], price=1930.0)
        signal, reason = self.strategy.generate_signal(ctx)
        assert signal is not None, f"Expected signal, got: {reason}"
        assert signal.direction == "BULLISH"
        assert signal.strategy_id == "smc_ob_v1"

    def test_bearish_ob_produces_signal(self):
        """BOS_BEARISH + valid bearish OB + price entering OB → BEARISH signal"""
        ob = _bear_ob()
        # Current bar: high=1938 ≥ ob.bottom=1936 AND close=1937 ≤ ob.top=1940 → in OB
        ctx = _ctx("smc_ob_v1", direction="BEARISH", order_blocks=[ob], price=1937.0)
        signal, reason = self.strategy.generate_signal(ctx)
        assert signal is not None, f"Expected signal, got: {reason}"
        assert signal.direction == "BEARISH"

    def test_calendar_blackout_blocks_signal(self):
        ob = _bull_ob()
        ctx = _ctx("smc_ob_v1", order_blocks=[ob], calendar_blackout=True)
        signal, reason = self.strategy.generate_signal(ctx)
        assert signal is None
        assert "calendar" in reason

    def test_volatile_regime_blocks_signal(self):
        ob = _bull_ob()
        ctx = _ctx("smc_ob_v1", order_blocks=[ob], regime="VOLATILE")
        signal, reason = self.strategy.generate_signal(ctx)
        assert signal is None

    def test_no_valid_obs_blocks_signal(self):
        ctx = _ctx("smc_ob_v1", order_blocks=[])
        signal, reason = self.strategy.generate_signal(ctx)
        assert signal is None
        assert "ob" in reason

    def test_price_not_in_ob_no_signal(self):
        """Valid OB exists but price is far away"""
        ob = _bull_ob()
        # OB is at 1928-1932, price=1950 → not inside
        ctx = _ctx("smc_ob_v1", direction="BULLISH", order_blocks=[ob], price=1950.0)
        signal, reason = self.strategy.generate_signal(ctx)
        assert signal is None
        assert "ob" in reason

    def test_stop_loss_below_ob_bottom(self):
        """SL must be below OB bottom for bullish trade"""
        ob = _bull_ob()
        ctx = _ctx("smc_ob_v1", direction="BULLISH", order_blocks=[ob], price=1930.0)
        signal, reason = self.strategy.generate_signal(ctx)
        if signal is not None:
            assert signal.stop_loss < ob.bottom

    def test_take_profit_at_correct_rr(self):
        """TP should be ~2R from entry"""
        ob = _bull_ob()
        ctx = _ctx("smc_ob_v1", direction="BULLISH", order_blocks=[ob], price=1930.0)
        signal, reason = self.strategy.generate_signal(ctx)
        if signal is not None:
            risk = signal.entry_price - signal.stop_loss
            assert risk > 0
            tp_rr = (signal.take_profit - signal.entry_price) / risk
            assert tp_rr == pytest.approx(2.0, abs=0.1)


# ── Portfolio multi-strategy tests ────────────────────────────────────────────

class TestPortfolioEngine:
    """Smoke tests for run_backtest_portfolio (fast proxy mode, minimal data)."""

    def _minimal_data(self):
        import numpy as np
        bars_n = 500
        idx = pd.date_range("2022-01-01", periods=bars_n, freq="15min", tz="UTC")
        close = np.linspace(1800, 1900, bars_n) + np.random.randn(bars_n) * 2
        bars = pd.DataFrame({
            "open": close - 1, "high": close + 3, "low": close - 3,
            "close": close, "volume": np.ones(bars_n) * 1000,
        }, index=idx)

        # Minimal FRED, yfinance, COT stubs
        dates = pd.date_range("2022-01-01", periods=365, freq="D", tz="UTC")
        fred = pd.DataFrame({"DFF": 0.5, "T10YFF": 1.0, "TEDRATE": 0.3}, index=dates)

        yf_close = pd.DataFrame({
            "DX-Y.NYB_close": 100.0,
            "^VIX_close": 20.0,
            "^TNX_close": 2.0,
        }, index=dates)

        cot = pd.DataFrame({
            "noncommercial_long": 100000,
            "noncommercial_short": 80000,
        }, index=pd.date_range("2022-01-01", periods=52, freq="W", tz="UTC"))

        return bars, fred, yf_close, cot

    def test_single_strategy_backward_compat(self):
        """run_backtest_portfolio with single strategy_id produces a result."""
        from astra_v2.backtest.engine import run_backtest_portfolio
        bars, fred, yf, cot = self._minimal_data()
        result = run_backtest_portfolio(
            bars=bars, fred_df=fred, yfinance_df=yf, cot_df=cot,
            strategy_ids=["breakout_retest_v1"],
            start_balance=10_000.0,
            wf_train_months=0,
        )
        assert result is not None
        assert result.start_balance == 10_000.0
        assert isinstance(result.trades, list)
        assert len(result.equity_curve) > 0

    def test_multi_strategy_runs_without_error(self):
        """Two strategies simultaneously — no crashes."""
        from astra_v2.backtest.engine import run_backtest_portfolio
        bars, fred, yf, cot = self._minimal_data()
        result = run_backtest_portfolio(
            bars=bars, fred_df=fred, yfinance_df=yf, cot_df=cot,
            strategy_ids=["smc_fvg_v1", "smc_ob_v1"],
            start_balance=10_000.0,
            wf_train_months=0,
        )
        assert result is not None
        assert isinstance(result.trades, list)

    def test_portfolio_dd_gate_prevents_new_trades(self):
        """When balance drops 6%+ below start, no new trades should open."""
        from astra_v2.backtest.portfolio_manager import check_portfolio_dd
        # 10000 start, balance = 9350 → DD = 6.5% → halt
        assert check_portfolio_dd([], balance=9350.0, start_balance=10_000.0) is True
        # 10000 start, balance = 9500 → DD = 5% → OK
        assert check_portfolio_dd([], balance=9500.0, start_balance=10_000.0) is False
