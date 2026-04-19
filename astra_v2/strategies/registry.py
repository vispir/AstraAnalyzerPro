from __future__ import annotations

from .base import StrategyId
from .legacy import LegacyStrategy
from .sweep_reversal_v1 import SweepReversalStrategy
from .sweep_reversal_v2 import SweepReversalStrategyV2
from .sweep_reversal_v3 import SweepReversalStrategyV3
from .sweep_reversal_v4 import SweepReversalStrategyV4
from .sweep_reversal_v4a import SweepReversalStrategyV4A
from .sweep_reversal_v4b import SweepReversalStrategyV4B
from .breakout_retest_v1 import BreakoutRetestStrategyV1
from .range_breakout_v1 import RangeBreakoutStrategyV1
from .smc_fvg_v1 import SmcFvgV1
from .smc_ob_v1 import SmcObV1
from .impulse_retest_v1 import ImpulseRetestStrategyV1


_STRATEGIES = {
    "legacy_v1": LegacyStrategy(),
    "sweep_reversal_v1": SweepReversalStrategy(),
    "sweep_reversal_v2": SweepReversalStrategyV2(),
    "sweep_reversal_v3": SweepReversalStrategyV3(),
    "sweep_reversal_v4": SweepReversalStrategyV4(),
    "sweep_reversal_v4a": SweepReversalStrategyV4A(),
    "sweep_reversal_v4b": SweepReversalStrategyV4B(),
    "breakout_retest_v1": BreakoutRetestStrategyV1(),
    "range_breakout_v1": RangeBreakoutStrategyV1(),
    "smc_fvg_v1": SmcFvgV1(),
    "smc_ob_v1": SmcObV1(),
    "impulse_retest_v1": ImpulseRetestStrategyV1(),
}


def get_strategy(strategy_id: str):
    try:
        return _STRATEGIES[strategy_id]
    except KeyError as exc:
        valid = ", ".join(sorted(_STRATEGIES))
        raise ValueError(f"Unknown strategy_id '{strategy_id}'. Valid options: {valid}") from exc
