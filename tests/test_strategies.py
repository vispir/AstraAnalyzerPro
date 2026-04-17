from datetime import datetime, timezone, timedelta

import pandas as pd
import pytest

from astra_v2.backtest.engine import Trade, _should_force_intraday_close
from astra_v2.core.macro_engine import MacroBias
from astra_v2.core.technical_engine import KeyLevel
from astra_v2.strategies import StrategyContext, get_strategy


def _macro(direction: str = "BULLISH") -> MacroBias:
    return MacroBias(
        direction=direction,  # type: ignore[arg-type]
        confidence=0.7,
        reasoning="test",
        tips_spread=0.0,
        dxy=100.0,
        vix=18.0,
        cot_net=0,
        timestamp=datetime.now(timezone.utc),
    )


def _bar(open_: float, high: float, low: float, close: float) -> pd.Series:
    return pd.Series({"open": open_, "high": high, "low": low, "close": close})


def _bars(rows: list[tuple[float, float, float, float]], start: datetime) -> pd.DataFrame:
    idx = [start + timedelta(minutes=15 * i) for i in range(len(rows))]
    return pd.DataFrame(
        [{"open": o, "high": h, "low": l, "close": c, "volume": 1000} for o, h, l, c in rows],
        index=pd.DatetimeIndex(idx, tz="UTC"),
    )


def _bars_with_step(
    rows: list[tuple[float, float, float, float]],
    start: datetime,
    minutes: int,
) -> pd.DataFrame:
    idx = [start + timedelta(minutes=minutes * i) for i in range(len(rows))]
    return pd.DataFrame(
        [{"open": o, "high": h, "low": l, "close": c, "volume": 1000} for o, h, l, c in rows],
        index=pd.DatetimeIndex(idx, tz="UTC"),
    )


def test_strategy_registry_returns_known_strategies():
    assert get_strategy("legacy_v1").strategy_id == "legacy_v1"
    assert get_strategy("sweep_reversal_v1").strategy_id == "sweep_reversal_v1"
    assert get_strategy("sweep_reversal_v2").strategy_id == "sweep_reversal_v2"
    assert get_strategy("sweep_reversal_v3").strategy_id == "sweep_reversal_v3"
    assert get_strategy("sweep_reversal_v4").strategy_id == "sweep_reversal_v4"


def test_strategy_registry_rejects_unknown_strategy():
    with pytest.raises(ValueError):
        get_strategy("nope")


def test_sweep_reversal_generates_bullish_signal():
    strategy = get_strategy("sweep_reversal_v1")
    now = datetime(2024, 3, 5, 8, 0, tzinfo=timezone.utc)
    bars_so_far = _bars(
        [
            (3202.0, 3203.0, 3201.5, 3202.5),
            (3202.5, 3203.0, 3198.2, 3199.8),
        ],
        now - timedelta(minutes=30),
    )
    current_bar = _bar(3199.8, 3201.8, 3199.5, 3201.0)
    levels = [KeyLevel(price=3200.0, level_type="pdl", direction="support", strength=7.0)]
    signal, reason = strategy.generate_signal(
        StrategyContext(
            strategy_id="sweep_reversal_v1",
            now=now,
            current_price=3201.0,
            current_bar=current_bar,
            bars_so_far=bars_so_far,
            levels=levels,
            macro=_macro("BULLISH"),
            local_trade_count=0,
        )
    )
    assert reason == "ok"
    assert signal is not None
    assert signal.direction == "BULLISH"
    assert signal.strategy_id == "sweep_reversal_v1"
    assert signal.sweep_side == "below_support"


def test_sweep_reversal_generates_bearish_signal():
    strategy = get_strategy("sweep_reversal_v1")
    now = datetime(2024, 3, 5, 14, 0, tzinfo=timezone.utc)
    bars_so_far = _bars(
        [
            (3198.0, 3199.0, 3197.5, 3198.5),
            (3198.5, 3202.2, 3198.0, 3201.2),
        ],
        now - timedelta(minutes=30),
    )
    current_bar = _bar(3201.2, 3201.5, 3198.0, 3198.8)
    levels = [KeyLevel(price=3200.0, level_type="pdh", direction="resistance", strength=7.0)]
    signal, reason = strategy.generate_signal(
        StrategyContext(
            strategy_id="sweep_reversal_v1",
            now=now,
            current_price=3198.8,
            current_bar=current_bar,
            bars_so_far=bars_so_far,
            levels=levels,
            macro=_macro("BEARISH"),
            local_trade_count=0,
        )
    )
    assert reason == "ok"
    assert signal is not None
    assert signal.direction == "BEARISH"
    assert signal.sweep_side == "above_resistance"


def test_sweep_reversal_rejects_proximity_without_sweep():
    strategy = get_strategy("sweep_reversal_v1")
    now = datetime(2024, 3, 5, 8, 0, tzinfo=timezone.utc)
    bars_so_far = _bars([(3200.2, 3200.4, 3200.0, 3200.3)], now - timedelta(minutes=15))
    current_bar = _bar(3200.3, 3200.6, 3200.1, 3200.5)
    levels = [KeyLevel(price=3200.0, level_type="pdl", direction="support", strength=7.0)]
    signal, reason = strategy.generate_signal(
        StrategyContext(
            strategy_id="sweep_reversal_v1",
            now=now,
            current_price=3200.5,
            current_bar=current_bar,
            bars_so_far=bars_so_far,
            levels=levels,
            macro=_macro(),
            local_trade_count=0,
        )
    )
    assert signal is None
    assert "no confirmed sweep" in reason


def test_sweep_reversal_rejects_sweep_without_confirmation():
    strategy = get_strategy("sweep_reversal_v1")
    now = datetime(2024, 3, 5, 14, 0, tzinfo=timezone.utc)
    bars_so_far = _bars([(3199.0, 3202.0, 3198.5, 3201.5)], now - timedelta(minutes=15))
    current_bar = _bar(3201.5, 3201.9, 3199.7, 3200.2)
    levels = [KeyLevel(price=3200.0, level_type="pdh", direction="resistance", strength=7.0)]
    signal, reason = strategy.generate_signal(
        StrategyContext(
            strategy_id="sweep_reversal_v1",
            now=now,
            current_price=3200.2,
            current_bar=current_bar,
            bars_so_far=bars_so_far,
            levels=levels,
            macro=_macro("BEARISH"),
            local_trade_count=0,
        )
    )
    assert signal is None
    assert "no confirmed sweep" in reason


def test_sweep_reversal_rejects_confirmation_that_is_too_late():
    strategy = get_strategy("sweep_reversal_v1")
    now = datetime(2024, 3, 5, 8, 0, tzinfo=timezone.utc)
    bars_so_far = _bars(
        [
            (3201.0, 3201.5, 3198.2, 3199.8),
            (3200.1, 3200.8, 3199.9, 3200.3),
            (3200.3, 3200.7, 3200.1, 3200.4),
        ],
        now - timedelta(minutes=45),
    )
    current_bar = _bar(3200.4, 3201.3, 3200.1, 3201.1)
    levels = [KeyLevel(price=3200.0, level_type="pdl", direction="support", strength=7.0)]
    signal, reason = strategy.generate_signal(
        StrategyContext(
            strategy_id="sweep_reversal_v1",
            now=now,
            current_price=3201.1,
            current_bar=current_bar,
            bars_so_far=bars_so_far,
            levels=levels,
            macro=_macro(),
            local_trade_count=0,
        )
    )
    assert signal is None
    assert "no confirmed sweep" in reason


def test_sweep_reversal_rejects_round_levels():
    strategy = get_strategy("sweep_reversal_v1")
    now = datetime(2024, 3, 5, 8, 0, tzinfo=timezone.utc)
    bars_so_far = _bars([(3201.0, 3201.4, 3198.5, 3199.7)], now - timedelta(minutes=15))
    current_bar = _bar(3199.7, 3201.1, 3199.6, 3201.0)
    levels = [KeyLevel(price=3200.0, level_type="round_10", direction="support", strength=7.0)]
    signal, reason = strategy.generate_signal(
        StrategyContext(
            strategy_id="sweep_reversal_v1",
            now=now,
            current_price=3201.0,
            current_bar=current_bar,
            bars_so_far=bars_so_far,
            levels=levels,
            macro=_macro(),
            local_trade_count=0,
        )
    )
    assert signal is None
    assert "no structural trigger levels" in reason


def test_intraday_force_close_triggers_for_sweep_strategy():
    trade = Trade(
        direction="BULLISH",
        entry=3200.0,
        stop_loss=3195.0,
        take_profit=3210.0,
        partial_tp=3205.0,
        opened_at=datetime(2024, 3, 5, 14, 0, tzinfo=timezone.utc),
        strategy_id="sweep_reversal_v1",
    )
    assert _should_force_intraday_close(trade, datetime(2024, 3, 5, 21, 0, tzinfo=timezone.utc))


def test_sweep_reversal_v2_generates_bullish_signal():
    strategy = get_strategy("sweep_reversal_v2")
    now = datetime(2024, 3, 5, 8, 0, tzinfo=timezone.utc)
    bars_so_far = _bars(
        [
            (3203.0, 3204.0, 3202.0, 3203.2),
            (3203.2, 3204.0, 3202.7, 3203.0),
            (3203.0, 3203.4, 3202.5, 3203.1),
            (3203.1, 3203.8, 3202.6, 3203.3),
            (3203.3, 3203.9, 3202.4, 3203.0),
            (3203.0, 3204.2, 3198.0, 3199.6),
        ]
        + [(3200.2, 3201.1, 3199.9, 3200.8)] * 20,
        now - timedelta(minutes=390),
    )
    current_bar = _bar(3200.0, 3202.6, 3199.7, 3202.4)
    levels = [KeyLevel(price=3200.0, level_type="pdl", direction="support", strength=7.0)]
    signal, reason = strategy.generate_signal(
        StrategyContext(
            strategy_id="sweep_reversal_v2",
            now=now,
            current_price=3202.4,
            current_bar=current_bar,
            bars_so_far=bars_so_far,
            levels=levels,
            macro=_macro("BULLISH"),
            local_trade_count=0,
        )
    )
    assert reason == "ok"
    assert signal is not None
    assert signal.direction == "BULLISH"
    assert signal.strategy_id == "sweep_reversal_v2"


def test_sweep_reversal_v2_counter_regime_bearish_is_stricter():
    strategy = get_strategy("sweep_reversal_v2")
    now = datetime(2024, 3, 5, 14, 0, tzinfo=timezone.utc)
    bars_so_far = _bars(
        [(3199.5, 3200.7, 3199.0, 3200.2)] * 20
        + [
            (3200.2, 3201.1, 3199.9, 3200.8),
            (3200.8, 3201.6, 3200.4, 3201.2),
            (3201.2, 3202.0, 3200.8, 3201.6),
        ],
        now - timedelta(minutes=345),
    )
    current_bar = _bar(3201.6, 3201.8, 3199.4, 3199.8)
    levels = [KeyLevel(price=3200.0, level_type="pdh", direction="resistance", strength=7.0)]
    signal, reason = strategy.generate_signal(
        StrategyContext(
            strategy_id="sweep_reversal_v2",
            now=now,
            current_price=3199.8,
            current_bar=current_bar,
            bars_so_far=bars_so_far,
            levels=levels,
            macro=_macro("BULLISH"),
            local_trade_count=0,
        )
    )
    assert signal is None
    assert "no confirmed sweep" in reason


def test_sweep_reversal_v2_uses_fib_as_confluence():
    strategy = get_strategy("sweep_reversal_v2")
    now = datetime(2024, 3, 5, 8, 0, tzinfo=timezone.utc)
    bars_so_far = _bars(
        [(3200.2, 3201.0, 3199.8, 3200.6)] * 20
        + [
            (3200.6, 3201.4, 3199.6, 3200.2),
            (3200.2, 3201.0, 3198.8, 3199.4),
            (3199.4, 3200.4, 3199.0, 3199.8),
        ],
        now - timedelta(minutes=345),
    )
    current_bar = _bar(3199.8, 3201.8, 3199.6, 3201.5)
    levels = [
        KeyLevel(price=3200.0, level_type="pdl", direction="support", strength=7.0),
        KeyLevel(price=3200.1, level_type="fib_618", direction="support", strength=6.0),
    ]
    signal, reason = strategy.generate_signal(
        StrategyContext(
            strategy_id="sweep_reversal_v2",
            now=now,
            current_price=3201.5,
            current_bar=current_bar,
            bars_so_far=bars_so_far,
            levels=levels,
            macro=_macro("BULLISH"),
            local_trade_count=0,
        )
    )
    assert reason == "ok"
    assert signal is not None
    assert signal.level.level_type == "pdl"
    assert signal.confirmation_type.endswith("+fib")


def test_sweep_reversal_v3_accepts_strong_bullish_pdl_london():
    strategy = get_strategy("sweep_reversal_v3")
    now = datetime(2024, 3, 5, 8, 0, tzinfo=timezone.utc)
    bars_so_far = _bars(
        [(3200.2, 3201.0, 3199.8, 3200.6)] * 20
        + [
            (3200.6, 3201.4, 3199.6, 3200.2),
            (3200.2, 3200.8, 3198.8, 3199.3),
            (3199.3, 3200.0, 3199.0, 3199.5),
        ],
        now - timedelta(minutes=345),
    )
    current_bar = _bar(3199.5, 3202.0, 3199.4, 3201.9)
    levels = [KeyLevel(price=3200.0, level_type="pdl", direction="support", strength=7.0)]
    signal, reason = strategy.generate_signal(
        StrategyContext(
            strategy_id="sweep_reversal_v3",
            now=now,
            current_price=3201.9,
            current_bar=current_bar,
            bars_so_far=bars_so_far,
            levels=levels,
            macro=_macro("BULLISH"),
            local_trade_count=0,
        )
    )
    assert reason == "ok"
    assert signal is not None
    assert signal.strategy_id == "sweep_reversal_v3"


def test_sweep_reversal_v3_rejects_weak_bullish_pdl_london_without_displacement():
    strategy = get_strategy("sweep_reversal_v3")
    now = datetime(2024, 3, 5, 8, 0, tzinfo=timezone.utc)
    bars_so_far = _bars(
        [(3200.2, 3201.0, 3199.8, 3200.6)] * 20
        + [
            (3200.6, 3201.4, 3199.6, 3200.2),
            (3200.2, 3200.8, 3198.8, 3199.3),
            (3199.3, 3200.0, 3199.0, 3199.5),
        ],
        now - timedelta(minutes=345),
    )
    current_bar = _bar(3199.5, 3200.7, 3199.4, 3200.25)
    levels = [KeyLevel(price=3200.0, level_type="pdl", direction="support", strength=7.0)]
    signal, reason = strategy.generate_signal(
        StrategyContext(
            strategy_id="sweep_reversal_v3",
            now=now,
            current_price=3200.25,
            current_bar=current_bar,
            bars_so_far=bars_so_far,
            levels=levels,
            macro=_macro("BULLISH"),
            local_trade_count=0,
        )
    )
    assert signal is None
    assert "no confirmed sweep" in reason


def test_sweep_reversal_v4_generates_signal_with_h4_and_m1_context():
    strategy = get_strategy("sweep_reversal_v4")
    now = datetime(2024, 3, 5, 8, 0, tzinfo=timezone.utc)
    bars_so_far = _bars(
        [(3200.2, 3201.0, 3199.8, 3200.6)] * 20
        + [
            (3200.6, 3201.4, 3199.6, 3200.2),
            (3200.2, 3200.8, 3198.8, 3199.3),
            (3199.3, 3200.0, 3199.0, 3199.5),
        ],
        now - timedelta(minutes=345),
    )
    current_bar = _bar(3199.5, 3202.2, 3199.4, 3201.9)
    levels = [KeyLevel(price=3200.0, level_type="pdl", direction="support", strength=7.0)]
    h4_bars = _bars_with_step(
        [(3180 + i, 3182 + i, 3178 + i, 3181 + i) for i in range(24)],
        now - timedelta(hours=96),
        240,
    )
    m1_bars = _bars_with_step(
        [
            (3200.0, 3200.2, 3199.8, 3200.0),
            (3200.0, 3200.3, 3199.9, 3200.1),
            (3200.1, 3200.4, 3200.0, 3200.2),
            (3200.2, 3200.45, 3200.1, 3200.25),
            (3200.25, 3200.5, 3200.2, 3200.3),
            (3200.3, 3200.55, 3200.25, 3200.35),
            (3200.35, 3200.6, 3200.3, 3200.4),
            (3200.4, 3200.65, 3200.35, 3200.45),
            (3200.45, 3200.7, 3200.4, 3200.5),
            (3200.5, 3200.75, 3200.45, 3200.55),
            (3200.55, 3200.8, 3200.5, 3200.6),
            (3200.6, 3202.1, 3200.55, 3201.95),
        ],
        now - timedelta(minutes=11),
        1,
    )
    signal, reason = strategy.generate_signal(
        StrategyContext(
            strategy_id="sweep_reversal_v4",
            now=now,
            current_price=3201.9,
            current_bar=current_bar,
            bars_so_far=bars_so_far,
            levels=levels,
            macro=_macro("BULLISH"),
            local_trade_count=0,
            bar_end=now + timedelta(minutes=15),
            h4_bars=h4_bars,
            m1_bars=m1_bars,
        )
    )
    assert reason == "ok"
    assert signal is not None
    assert signal.strategy_id == "sweep_reversal_v4"
    assert signal.execution_timeframe == "M1"
    assert signal.entry_trigger_price is not None
