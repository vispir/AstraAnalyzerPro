from __future__ import annotations

from astra_v2.core.signal_gate import check_signal
from .base import StrategyContext


class LegacyStrategy:
    strategy_id = "legacy_v1"
    required_level_types = None
    required_timeframes = ()
    supports_live_execution = True

    def generate_signal(self, context: StrategyContext, *, supabase_client=None):
        signal, reason = check_signal(
            macro=context.macro,
            levels=context.levels,
            current_price=context.current_price,
            now=context.now,
            supabase_client=supabase_client,
            local_trade_count=context.local_trade_count,
        )
        if signal is not None:
            signal.strategy_id = self.strategy_id
        return signal, reason
