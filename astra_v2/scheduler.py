"""
Astra v2 Main Scheduler

Runs the 3-layer signal detection loop every M15 bar.

Signal loop (runs during active sessions only):
  1. Kill switch check
  2. Compute/cache macro bias (once per day)
  3. Compute/cache key levels (once per day)
  4. check_signal() — all 6 gates
  5. attempt_open() if signal found

Management loop (runs every M15 regardless of session):
  - manage_positions() — BE / trail / partial TP

Daily summary at 22:00 UTC.
Heartbeat every 30 min.

News blackout: ±PROP_NEWS_BLACKOUT_MINUTES around configured news events.
Weekend hold: governed by PROP_WEEKEND_HOLD_ALLOWED.

Usage:
    python -m astra_v2.scheduler            # live mode (default)
    python -m astra_v2.scheduler --dry-run  # print signals, no trades
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import datetime, timezone

from apscheduler.schedulers.blocking import BlockingScheduler  # type: ignore
from apscheduler.triggers.cron import CronTrigger  # type: ignore

from astra_v2 import config
from astra_v2.core.signal_gate import check_signal, is_active_session
from astra_v2.core.macro_engine import get_bias
from astra_v2.core.technical_engine import extract_levels
from astra_v2.core.trade_manager import TradeManager, build_provider
from astra_v2.data.market_data import get_client as get_oanda
from astra_v2.integrations import supabase_client as supa
from astra_v2.integrations import telegram

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


# ── State ──────────────────────────────────────────────────────────────────────

_daily_macro_cache: dict = {}   # {date_str: MacroBias}
_daily_level_cache: dict = {}   # {date_str: list[KeyLevel]}
_daily_trade_count: dict = {}   # {date_str: int} — local fallback only
_trade_manager: TradeManager = None  # type: ignore
_dry_run: bool = False


# ── Core tick ──────────────────────────────────────────────────────────────────

def _signal_tick() -> None:
    """Called every M15. Runs gates, opens trades, manages positions."""
    global _daily_macro_cache, _daily_level_cache, _daily_trade_count

    now = datetime.now(timezone.utc)
    date_str = now.strftime("%Y-%m-%d")

    # ── Manage existing positions (always, regardless of session) ────────────
    try:
        _trade_manager.manage_positions()
    except Exception as e:
        logger.error(f"manage_positions error: {e}")

    # ── Only look for new signals in active sessions ─────────────────────────
    if not is_active_session(now):
        return

    # ── Skip if already in a trade ───────────────────────────────────────────
    if _trade_manager.has_open_trade:
        return

    # ── Weekend hold check ───────────────────────────────────────────────────
    if not config.PROP_WEEKEND_HOLD_ALLOWED and now.weekday() >= 4:  # Fri after NY close
        if now.weekday() == 4 and now.hour >= 20:
            logger.debug("Weekend hold: Friday post-NY — skipping")
            return
        if now.weekday() in (5, 6):
            return

    # ── Macro bias (once per day) ────────────────────────────────────────────
    if date_str not in _daily_macro_cache:
        # Try Supabase cache first (survives restarts)
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

            supa.save_macro_cache(date_str, {
                "direction": macro.direction,
                "confidence": macro.confidence,
                "reasoning": macro.reasoning,
                "tips_spread": macro.tips_spread,
                "dxy": macro.dxy,
                "vix": macro.vix,
                "cot_net": macro.cot_net,
            })

        _daily_macro_cache[date_str] = macro
        logger.info(f"Macro: {macro.direction} {macro.confidence:.0%} — {macro.reasoning[:60]}")

    macro = _daily_macro_cache[date_str]

    # ── Key levels (once per day, using recent bars) ──────────────────────────
    if date_str not in _daily_level_cache:
        try:
            oanda = get_oanda()
            bars = oanda.get_candles("XAU_USD", granularity="M15", count=500)
            current_price = oanda.get_current_price()
            levels = extract_levels(bars, current_price, now)
            _daily_level_cache[date_str] = levels
            logger.info(f"Levels computed: {len(levels)} key levels for {date_str}")
        except Exception as e:
            logger.error(f"extract_levels failed: {e}")
            return

    levels = _daily_level_cache[date_str]

    # ── Current price ─────────────────────────────────────────────────────────
    try:
        current_price = get_oanda().get_current_price()
    except Exception as e:
        logger.error(f"get_current_price failed: {e}")
        return

    # ── Local trade count (Supabase is authoritative via signal_gate) ─────────
    local_count = _daily_trade_count.get(date_str, 0)

    # ── Signal gate ───────────────────────────────────────────────────────────
    try:
        sb = supa.get_client()
    except Exception:
        sb = None

    signal, reason = check_signal(
        macro=macro,
        levels=levels,
        current_price=current_price,
        now=now,
        supabase_client=sb,
        local_trade_count=local_count,
    )

    if signal is None:
        logger.debug(f"No signal: {reason}")
        return

    logger.info(f"Signal: {signal.direction} @ {signal.entry_price:.2f} SL={signal.stop_loss:.2f} TP={signal.take_profit:.2f}")

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

    # ── Open trade ────────────────────────────────────────────────────────────
    opened = _trade_manager.attempt_open(signal)
    if opened:
        _daily_trade_count[date_str] = local_count + 1


def _daily_summary() -> None:
    """Called at 22:00 UTC. Sends daily summary to Telegram."""
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    try:
        count = supa.count_trades_today(date_str) or 0
        telegram.send_daily_summary(
            date_str=date_str,
            trades_count=count,
            wins=0,   # TODO: query from trades table
            losses=0,
            pnl_usd=0.0,
            current_dd_pct=0.0,
        )
    except Exception as e:
        logger.error(f"daily_summary failed: {e}")

    # Clear daily caches for the new day
    _daily_macro_cache.clear()
    _daily_level_cache.clear()


def _heartbeat() -> None:
    trade_info = None
    if _trade_manager and _trade_manager.has_open_trade:
        t = _trade_manager.open_trade
        trade_info = f"Open trade: {t.direction} @ {t.entry_price:.2f} SL={t.stop_loss:.2f}"
    status = "TRADING" if not _dry_run else "DRY RUN"
    telegram.send_heartbeat(status, trade_info)


# ── Entry point ────────────────────────────────────────────────────────────────

def main() -> None:
    global _trade_manager, _dry_run

    parser = argparse.ArgumentParser(description="Astra v2 Scheduler")
    parser.add_argument("--dry-run", action="store_true", help="Print signals without executing trades")
    args = parser.parse_args()
    _dry_run = args.dry_run

    # Validate config
    try:
        config.validate()
    except ValueError as e:
        logger.critical(f"Config validation failed: {e}")
        sys.exit(1)

    # Build execution provider
    provider = build_provider()
    _trade_manager = TradeManager(provider)

    logger.info(f"Astra v2 starting. Mode: {'DRY RUN' if _dry_run else 'LIVE'}")
    telegram.send_heartbeat("STARTING", f"Mode: {'dry-run' if _dry_run else 'live'}")

    # APScheduler
    scheduler = BlockingScheduler(timezone="UTC")

    # M15 signal tick at :00, :15, :30, :45 of every hour
    # Offset by 30s to allow bar to close
    scheduler.add_job(
        _signal_tick,
        CronTrigger(minute="0,15,30,45", second=30),
        id="signal_tick",
        max_instances=1,
        coalesce=True,
    )

    # Daily summary + cache clear
    scheduler.add_job(
        _daily_summary,
        CronTrigger(hour=22, minute=0),
        id="daily_summary",
    )

    # Heartbeat every 30 min
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
