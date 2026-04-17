import inspect

from astra_v2.backtest import engine
from astra_v2 import scheduler


def test_backtest_recomputes_levels_each_bar():
    source = inspect.getsource(engine.run_backtest)
    assert "daily_level_cache" not in source
    assert "levels = extract_levels(" in source
    assert "bars_so_far" in source
    assert "allowed_level_types=required_level_types" in source


def test_scheduler_recomputes_levels_each_cycle():
    source = inspect.getsource(scheduler._signal_tick)
    assert "_daily_level_cache" not in source
    assert "levels = extract_levels(" in source
    assert "bars_so_far" in source
    assert "allowed_level_types=required_level_types" in source
