from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Optional, Protocol

import pandas as pd

from astra_v2.core.macro_engine import MacroBias
from astra_v2.core.signal_gate import Signal
from astra_v2.core.technical_engine import KeyLevel

StrategyId = Literal[
    "legacy_v1",
    "sweep_reversal_v1",
    "sweep_reversal_v2",
    "sweep_reversal_v3",
    "sweep_reversal_v4",
    "sweep_reversal_v4a",
    "sweep_reversal_v4b",
    "breakout_retest_v1",
    "smc_fvg_v1",
    "smc_ob_v1",
]


@dataclass
class StrategyContext:
    strategy_id: StrategyId
    now: datetime
    current_price: float
    current_bar: pd.Series
    bars_so_far: pd.DataFrame
    levels: list[KeyLevel]
    macro: MacroBias
    local_trade_count: int = 0
    bar_end: Optional[datetime] = None
    h4_bars: Optional[pd.DataFrame] = None
    m1_bars: Optional[pd.DataFrame] = None
    # Multi-pair: primary chart symbol + other pairs' M15 history strictly before current bar (UTC index)
    primary_symbol: str = "XAUUSD"
    cross_symbol_m15: Optional[dict[str, pd.DataFrame]] = None
    # SMC fields — populated by engine for SMC strategies
    market_structure: Optional[object] = None   # MarketStructure from core/market_structure.py
    fvgs: Optional[list] = None                 # list[FairValueGap]
    order_blocks: Optional[list] = None         # list[OrderBlock]
    regime: Optional[str] = None                # "TRENDING"|"ACCUMULATION"|"DISTRIBUTION"|"VOLATILE"
    calendar_blackout: bool = False             # True if within news blackout window
    dxy_trend: Optional[str] = None             # "RISING"|"FALLING"|None


class Strategy(Protocol):
    strategy_id: StrategyId
    required_level_types: tuple[str, ...] | None
    required_timeframes: tuple[str, ...]
    supports_live_execution: bool

    def generate_signal(
        self,
        context: StrategyContext,
        *,
        supabase_client=None,
    ) -> tuple[Optional[Signal], str]:
        ...
