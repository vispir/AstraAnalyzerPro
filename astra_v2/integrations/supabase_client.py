"""
Supabase integration — state persistence for live trading.

Tables used:
  macro_cache   — cached LLM macro bias (60-min TTL)
  trades        — trade log (opened, closed, PnL)
  system_state  — peak equity tracker for DD calculation

All writes are best-effort: a Supabase failure never blocks trading.
Reads use a circuit breaker: failure → return None (caller decides).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from astra_v2 import config

logger = logging.getLogger(__name__)

_client = None


def get_client():
    """Module-level singleton. Lazy-init on first call."""
    global _client
    if _client is None:
        from supabase import create_client  # type: ignore
        _client = create_client(config.SUPABASE_URL, config.SUPABASE_KEY)
    return _client


# ── Macro cache ────────────────────────────────────────────────────────────────

def load_macro_cache(date_str: str) -> Optional[dict]:
    """
    Load cached macro bias for a given date.
    Returns dict with direction/confidence/reasoning or None if cache miss/error.
    """
    try:
        sb = get_client()
        result = (
            sb.table("macro_cache")
            .select("*")
            .eq("date", date_str)
            .limit(1)
            .execute()
        )
        if result.data:
            return result.data[0]
        return None
    except Exception as e:
        logger.warning(f"macro_cache load failed for {date_str}: {e}")
        return None


def save_macro_cache(date_str: str, bias_data: dict) -> None:
    """Upsert macro bias for date_str. Best-effort."""
    try:
        sb = get_client()
        sb.table("macro_cache").upsert({
            "date": date_str,
            **bias_data,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }).execute()
    except Exception as e:
        logger.warning(f"macro_cache save failed for {date_str}: {e}")


# ── Trade log ──────────────────────────────────────────────────────────────────

def log_trade_open(
    direction: str,
    entry_price: float,
    stop_loss: float,
    take_profit: float,
    lot_size: float,
    opened_at: datetime,
    ticket: Optional[int] = None,
) -> Optional[str]:
    """
    Insert a new trade record. Returns the Supabase row ID or None on error.
    """
    try:
        sb = get_client()
        result = sb.table("trades").insert({
            "direction": direction,
            "entry_price": entry_price,
            "stop_loss": stop_loss,
            "take_profit": take_profit,
            "lot_size": lot_size,
            "opened_at": opened_at.isoformat(),
            "ticket": ticket,
            "status": "open",
        }).execute()
        if result.data:
            return result.data[0].get("id")
        return None
    except Exception as e:
        logger.error(f"log_trade_open failed: {e}")
        return None


def log_trade_close(
    trade_id: str,
    exit_price: float,
    closed_at: datetime,
    status: str,
    pnl_usd: Optional[float] = None,
) -> None:
    """Update trade record with close info. Best-effort."""
    try:
        sb = get_client()
        update = {
            "exit_price": exit_price,
            "closed_at": closed_at.isoformat(),
            "status": status,
        }
        if pnl_usd is not None:
            update["pnl_usd"] = pnl_usd
        sb.table("trades").update(update).eq("id", trade_id).execute()
    except Exception as e:
        logger.error(f"log_trade_close failed for {trade_id}: {e}")


def count_trades_today(date_str: str) -> Optional[int]:
    """
    Count closed (non-cancelled) trades for today using Supabase COUNT.
    Returns None on error (caller applies circuit breaker).

    This is the authoritative source for Gate 5 — never use in-memory counters.
    """
    try:
        sb = get_client()
        result = (
            sb.table("trades")
            .select("*", count="exact")
            .gte("opened_at", f"{date_str}T00:00:00+00:00")
            .not_.in_("status", ["open", "cancelled"])
            .execute()
        )
        return result.count
    except Exception as e:
        logger.error(f"count_trades_today failed for {date_str}: {e}")
        return None


# ── System state ───────────────────────────────────────────────────────────────

def load_peak_equity() -> Optional[float]:
    """Load persisted peak equity for DD tracking. Returns None if not set."""
    try:
        sb = get_client()
        result = (
            sb.table("system_state")
            .select("value")
            .eq("key", "peak_equity")
            .limit(1)
            .execute()
        )
        if result.data:
            return float(result.data[0]["value"])
        return None
    except Exception as e:
        logger.warning(f"load_peak_equity failed: {e}")
        return None


def save_peak_equity(value: float) -> None:
    """Persist peak equity for DD tracking across restarts. Best-effort."""
    try:
        sb = get_client()
        sb.table("system_state").upsert({
            "key": "peak_equity",
            "value": str(value),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }).execute()
    except Exception as e:
        logger.warning(f"save_peak_equity failed: {e}")
