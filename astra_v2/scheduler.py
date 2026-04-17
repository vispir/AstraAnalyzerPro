"""
Astra v2 Main Scheduler

Runs the strategy loop every M15 bar and uses a pluggable strategy registry.
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timezone

from apscheduler.schedulers.blocking import BlockingScheduler  # type: ignore
from apscheduler.triggers.cron import CronTrigger  # type: ignore

from astra_v2 import config
from astra_v2.core.signal_gate import is_active_session
from astra_v2.core.macro_engine import get_bias
from astra_v2.core.technical_engine import extract_levels
from astra_v2.core.trade_manager import TradeManager, build_provider
from astra_v2.data.market_data import get_client as get_oanda
from astra_v2.integrations import supabase_client as supa
from astra_v2.integrations import telegram
from astra_v2.strategies import StrategyContext, get_strategy

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


_daily_macro_cache: dict = {}
_daily_trade_count: dict = {}
_trade_manager: TradeManager = None  # type: ignore
_dry_run: bool = False
_strategy_id: str = config.DEFAULT_STRATEGY_ID


def _signal_tick() -> None:
    global _daily_macro_cache, _daily_trade_count, _strategy_id
    strategy = get_strategy(_strategy_id)
    if not getattr(strategy, "supports_live_execution", True):
        logger.warning(f"Strategy {_strategy_id} is currently backtest-only and will not run in live scheduler.")
        return
    required_level_types = (
        set(strategy.required_level_types)
        if getattr(strategy, "required_level_types", None) is not None
        else None
    )
    now = datetime.now(timezone.utc)

    try:
        _trade_manager.manage_positions(now=now)
    except Exception as e:
        logger.error(f"manage_positions error: {e}")

    if _trade_manager.has_open_trade:
        return

    if not config.PROP_WEEKEND_HOLD_ALLOWED and now.weekday() >= 4:
        if now.weekday() == 4 and now.hour >= 20:
            logger.debug("Weekend hold: Friday post-NY - skipping")
            return
        if now.weekday() in (5, 6):
            return

    date_str = now.strftime("%Y-%m-%d")
    if date_str not in _daily_macro_cache:
        cached = supa.load_macro_cache(date_str)
        if cached:
            from astra_v2.core.macro_engine import MacroBias

            macro = MacroBias(
                direction=cached["direction"],
                confidence=cached["confidence"],
                reasoning=cached.get("reasoning", ""),
                tips_spread=cached.get("tips_spread", 0.0),
                dxy=cached.get("dxy", 0.0),
                vix=cached.get("vix", 0.0),
                cot_net=cached.get("cot_net"),
                timestamp=now,
            )
        else:
            try:
                macro = get_bias()
            except Exception as e:
                logger.error(f"get_bias failed: {e}")
                return

            supa.save_macro_cache(
                date_str,
                {
                    "direction": macro.direction,
                    "confidence": macro.confidence,
                    "reasoning": macro.reasoning,
                    "tips_spread": macro.tips_spread,
                    "dxy": macro.dxy,
                    "vix": macro.vix,
                    "cot_net": macro.cot_net,
                },
            )

        _daily_macro_cache[date_str] = macro
        logger.info(f"Macro: {macro.direction} {macro.confidence:.0%} - {macro.reasoning[:60]}")

    macro = _daily_macro_cache[date_str]

    try:
        oanda = get_oanda()
        bars = oanda.get_candles("XAU_USD", granularity="M15", count=500)
    except Exception as e:
        logger.error(f"get_candles failed: {e}")
        return

    if bars.empty:
        logger.error("No candles returned from market data provider")
        return

    signal_bar = bars.iloc[-1]
    signal_time = bars.index[-1].to_pydatetime()
    if not is_active_session(signal_time):
        return

    date_str = signal_time.strftime("%Y-%m-%d")
    bars_so_far = bars.iloc[:-1]
    current_price = float(signal_bar["close"])
    levels = extract_levels(
        bars_so_far,
        current_price,
        signal_time,
        allowed_level_types=required_level_types,
    )
    logger.info(f"Levels computed: {len(levels)} key levels for {signal_time.isoformat()}")

    local_count = _daily_trade_count.get(date_str, 0)

    try:
        sb = supa.get_client()
    except Exception:
        sb = None

    signal, reason = strategy.generate_signal(
        StrategyContext(
            strategy_id=_strategy_id,  # type: ignore[arg-type]
            now=signal_time,
            current_price=current_price,
            current_bar=signal_bar,
            bars_so_far=bars_so_far,
            levels=levels,
            macro=macro,
            local_trade_count=local_count,
        ),
        supabase_client=sb,
    )

    if signal is None:
        logger.debug(f"No signal: {reason}")
        return

    logger.info(
        f"Signal[{signal.strategy_id}]: {signal.direction} @ {signal.entry_price:.2f} "
        f"SL={signal.stop_loss:.2f} TP={signal.take_profit:.2f}"
    )

    if _dry_run:
        logger.info("[DRY RUN] Signal not executed.")
        telegram.send_signal_alert(
            direction=signal.direction,
            entry=signal.entry_price,
            sl=signal.stop_loss,
            tp=signal.take_profit,
            partial_tp=signal.partial_tp,
            lot_size=0.0,
            level_type=signal.level.level_type,
            level_price=signal.level.price,
            macro_confidence=signal.macro_bias.confidence,
            macro_direction=signal.macro_bias.direction,
        )
        return

    opened = _trade_manager.attempt_open(signal)
    if opened:
        _daily_trade_count[date_str] = local_count + 1


def _daily_summary() -> None:
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    try:
        count = supa.count_trades_today(date_str) or 0
        telegram.send_daily_summary(
            date_str=date_str,
            trades_count=count,
            wins=0,
            losses=0,
            pnl_usd=0.0,
            current_dd_pct=0.0,
        )
    except Exception as e:
        logger.error(f"daily_summary failed: {e}")

    _daily_macro_cache.clear()


def _heartbeat() -> None:
    trade_info = None
    if _trade_manager and _trade_manager.has_open_trade:
        t = _trade_manager.open_trade
        trade_info = f"Open trade: {t.direction} @ {t.entry_price:.2f} SL={t.stop_loss:.2f}"
    status = "TRADING" if not _dry_run else "DRY RUN"
    telegram.send_heartbeat(status, trade_info)


def main() -> None:
    global _trade_manager, _dry_run, _strategy_id

    parser = argparse.ArgumentParser(description="Astra v2 Scheduler")
    parser.add_argument("--dry-run", action="store_true", help="Print signals without executing trades")
    parser.add_argument("--strategy", default=config.DEFAULT_STRATEGY_ID, help="Strategy ID")
    args = parser.parse_args()
    _dry_run = args.dry_run
    _strategy_id = args.strategy

    try:
        get_strategy(_strategy_id)
    except ValueError as e:
        logger.critical(str(e))
        sys.exit(1)

    try:
        config.validate()
    except ValueError as e:
        logger.critical(f"Config validation failed: {e}")
        sys.exit(1)

    provider = build_provider()
    _trade_manager = TradeManager(provider)

    logger.info(f"Astra v2 starting. Mode: {'DRY RUN' if _dry_run else 'LIVE'} strategy={_strategy_id}")
    telegram.send_heartbeat("STARTING", f"Mode: {'dry-run' if _dry_run else 'live'} strategy={_strategy_id}")

    scheduler = BlockingScheduler(timezone="UTC")
    scheduler.add_job(
        _signal_tick,
        CronTrigger(minute="0,15,30,45", second=30),
        id="signal_tick",
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        _daily_summary,
        CronTrigger(hour=22, minute=0),
        id="daily_summary",
    )
    scheduler.add_job(
        _heartbeat,
        CronTrigger(minute="0,30"),
        id="heartbeat",
    )

    try:
        scheduler.start()
    except KeyboardInterrupt:
        logger.info("Scheduler stopped.")
        telegram.send_heartbeat("STOPPED", "Manual shutdown")


if __name__ == "__main__":
    main()
