"""
Telegram integration — status broadcasts and command handling.

Two channels:
  SIGNAL channel  — trade alerts (entry/SL/TP/direction)
  STATUS channel  — system heartbeat, daily summary, errors

Bot commands (optional, handled if TELEGRAM_ADMIN_CHAT_ID is set):
  /status  — current trade state + DD
  /pause   — pause signal detection (does not close positions)
  /resume  — resume signal detection
  /stop    — close all + stop scheduler

Uses only stdlib urllib — no extra deps.
"""

from __future__ import annotations

import json
import logging
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Optional

from astra_v2 import config

logger = logging.getLogger(__name__)

_paused = False


def is_paused() -> bool:
    return _paused


def send_message(text: str, chat_id: Optional[str] = None) -> bool:
    """
    Send a text message. Defaults to TELEGRAM_CHAT_ID.
    Returns True on success.
    """
    if not config.TELEGRAM_BOT_TOKEN or not config.TELEGRAM_CHAT_ID:
        logger.debug("Telegram not configured — skipping send")
        return False

    target = chat_id or config.TELEGRAM_CHAT_ID
    url = f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/sendMessage"
    data = urllib.parse.urlencode({
        "chat_id": target,
        "text": text,
        "parse_mode": "HTML",
    }).encode()

    try:
        req = urllib.request.Request(url, data=data, method="POST")
        with urllib.request.urlopen(req, timeout=10) as resp:
            if resp.status == 200:
                return True
            logger.warning(f"Telegram sendMessage status={resp.status}")
            return False
    except Exception as e:
        logger.error(f"Telegram send failed: {e}")
        return False


def send_signal_alert(
    direction: str,
    entry: float,
    sl: float,
    tp: float,
    partial_tp: float,
    lot_size: float,
    level_type: str,
    level_price: float,
    macro_confidence: float,
    macro_direction: str,
) -> None:
    # Support both "LONG"/"SHORT" and "BULLISH"/"BEARISH"
    if direction in ("LONG", "BULLISH"):
        arrow = "🟢 LONG"
    else:
        arrow = "🔴 SHORT"

    rr = abs(tp - entry) / abs(entry - sl) if abs(entry - sl) > 0 else 0
    text = (
        f"<b>ASTRA SIGNAL — {arrow}</b>\n"
        f"Entry: <code>{entry:.2f}</code>\n"
        f"SL:    <code>{sl:.2f}</code>\n"
        f"TP:    <code>{tp:.2f}</code> (1:{rr:.1f}R)\n"
        f"Partial TP: <code>{partial_tp:.2f}</code>\n"
        f"Lot:   {lot_size:.2f}\n"
        f"Level: {level_type.upper()} @ {level_price:.2f}\n"
        f"Macro: {macro_confidence:.0%} {macro_direction}"
    )
    send_message(text)


def send_trade_closed(direction: str, status: str, entry: float, exit_price: float, pnl_usd: float) -> None:
    emoji = "✅" if pnl_usd >= 0 else "❌"

    # Support both "LONG"/"SHORT" and "BULLISH"/"BEARISH"
    if direction in ("LONG", "BULLISH"):
        arrow = "🟢 LONG"
    else:
        arrow = "🔴 SHORT"

    text = (
        f"<b>{emoji} TRADE CLOSED — {arrow}</b>\n"
        f"Status: {status}\n"
        f"Entry: {entry:.2f} | Exit: {exit_price:.2f}\n"
        f"PnL: <code>{pnl_usd:+.2f} USD</code>"
    )
    send_message(text)


def send_daily_summary(
    date_str: str,
    trades_count: int,
    wins: int,
    losses: int,
    pnl_usd: float,
    current_dd_pct: float,
) -> None:
    text = (
        f"<b>Daily Summary — {date_str}</b>\n"
        f"Trades: {trades_count} ({wins}W / {losses}L)\n"
        f"PnL: <code>{pnl_usd:+.2f} USD</code>\n"
        f"DD: {current_dd_pct:.2f}%"
    )
    send_message(text)


def send_kill_switch_alert(dd_pct: float) -> None:
    text = (
        f"<b>KILL SWITCH TRIGGERED</b>\n"
        f"Drawdown: {dd_pct:.2f}%\n"
        f"All positions closed. Trading stopped for the day."
    )
    send_message(text)


def send_heartbeat(status: str, trade_info: Optional[str] = None) -> None:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    text = f"<b>Astra v2 Heartbeat</b> [{now}]\nStatus: {status}"
    if trade_info:
        text += f"\n{trade_info}"
    send_message(text)


def poll_commands(callback) -> None:
    """
    Poll Telegram for bot commands. Call this in a background thread or scheduler.
    callback(command: str) is called with "/status", "/pause", "/resume", "/stop".

    Only processes commands from TELEGRAM_ADMIN_CHAT_ID if set.
    """
    if not config.TELEGRAM_BOT_TOKEN:
        return

    url = f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/getUpdates"

    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            body = json.loads(resp.read())
            if not body.get("ok"):
                return
            for update in body.get("result", []):
                msg = update.get("message", {})
                text = msg.get("text", "")
                chat_id = str(msg.get("chat", {}).get("id", ""))

                admin_id = getattr(config, "TELEGRAM_ADMIN_CHAT_ID", None)
                if admin_id and chat_id != str(admin_id):
                    continue

                cmd = text.strip().split()[0] if text.strip() else ""
                if cmd in ("/status", "/pause", "/resume", "/stop"):
                    callback(cmd)
    except Exception as e:
        logger.debug(f"poll_commands error: {e}")
