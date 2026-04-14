"""
Trade Manager — execution abstraction for live trading.

Two providers:
  MT5Provider    — full automation via MetaTrader5 Python API (Windows VPS)
  TelegramProvider — semi-auto, sends signal alert, human places the trade

Position sizing: balance * RISK_PCT / sl_distance_usd
Trade lifecycle: open → partial_tp at 1:1 → BE at +1R → trail at +1.5R → close

Kill switch is checked BEFORE every open attempt. If DD exceeds PROP_DAILY_STOP_DD_PCT,
the trade is skipped. If DD exceeds PROP_KILL_SWITCH_DD_PCT, all positions are closed.

                      Signal
                         │
              ┌──────────▼──────────┐
              │   Kill switch check  │ → skip/close all
              └──────────┬──────────┘
                         │
              ┌──────────▼──────────┐
              │   Size (1% / SL)     │
              └──────────┬──────────┘
                         │
          ┌──────────────┴──────────────┐
          │ MT5Provider                  │ TelegramProvider
          │ open_order()                 │ send_alert()
          │ confirm via position check   │ (human executes)
          └──────────────┬──────────────┘
                         │
              ┌──────────▼──────────┐
              │  manage_positions()  │ ← called each tick
              │  BE / trail / close  │
              └─────────────────────┘
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from astra_v2 import config
from astra_v2.core.signal_gate import Signal

logger = logging.getLogger(__name__)


# ── Data models ────────────────────────────────────────────────────────────────

@dataclass
class LiveTrade:
    """State of an open or recently closed live position."""
    direction: str          # BULLISH | BEARISH
    entry_price: float
    stop_loss: float
    take_profit: float
    partial_tp: float
    lot_size: float
    opened_at: datetime
    ticket: Optional[int] = None   # MT5 ticket ID; None for Telegram mode
    partial_closed: bool = False
    be_moved: bool = False
    status: str = "open"   # open | tp | sl | be_sl | partial_tp | forced


@dataclass
class AccountState:
    balance: float
    equity: float
    drawdown_pct: float    # current DD vs peak equity, as a positive %


# ── Abstract provider ──────────────────────────────────────────────────────────

class ExecutionProvider(ABC):
    """
    Abstract execution layer. Concrete providers: MT5Provider, TelegramProvider.
    All methods are synchronous — the scheduler calls them from a single thread.
    """

    @abstractmethod
    def get_account_state(self) -> AccountState:
        """Return live balance, equity, and current drawdown %."""

    @abstractmethod
    def get_current_price(self) -> float:
        """Return mid price of XAUUSD."""

    @abstractmethod
    def open_trade(self, signal: Signal, lot_size: float) -> Optional[LiveTrade]:
        """
        Execute (or announce) the trade.
        Returns LiveTrade if opened, None if rejected/announced only.
        """

    @abstractmethod
    def close_trade(self, trade: LiveTrade, reason: str) -> None:
        """Close the position at market price."""

    @abstractmethod
    def modify_sl(self, trade: LiveTrade, new_sl: float) -> None:
        """Move stop loss in-place (used for BE and trail moves)."""

    @abstractmethod
    def partial_close(self, trade: LiveTrade, pct: float) -> None:
        """Close pct% of the position at market (e.g. 0.5 = 50%)."""

    def send_status(self, message: str) -> None:
        """Optional: broadcast a status message (implemented by TelegramProvider)."""


# ── MT5 provider ───────────────────────────────────────────────────────────────

class MT5Provider(ExecutionProvider):
    """
    Full-automation via MetaTrader5 Python API.
    Requires: MetaTrader5 installed on Windows VPS, MT5 terminal logged in.
    Import is deferred so the module loads on Linux (for testing) without MT5.
    """

    SYMBOL = "XAUUSD"

    def __init__(self) -> None:
        import MetaTrader5 as mt5  # type: ignore
        self._mt5 = mt5
        if not mt5.initialize():
            raise RuntimeError(f"MT5 initialize() failed: {mt5.last_error()}")
        logger.info("MT5Provider connected.")

    def get_account_state(self) -> AccountState:
        mt5 = self._mt5
        info = mt5.account_info()
        if info is None:
            raise RuntimeError(f"MT5 account_info() failed: {mt5.last_error()}")
        peak = max(info.balance, info.equity)  # simplified peak for live check
        dd_pct = (peak - info.equity) / peak * 100 if peak > 0 else 0.0
        return AccountState(
            balance=info.balance,
            equity=info.equity,
            drawdown_pct=max(0.0, dd_pct),
        )

    def get_current_price(self) -> float:
        mt5 = self._mt5
        tick = mt5.symbol_info_tick(self.SYMBOL)
        if tick is None:
            raise RuntimeError(f"MT5 symbol_info_tick() failed: {mt5.last_error()}")
        return (tick.bid + tick.ask) / 2

    def open_trade(self, signal: Signal, lot_size: float) -> Optional[LiveTrade]:
        mt5 = self._mt5
        is_long = signal.direction == "BULLISH"
        order_type = mt5.ORDER_TYPE_BUY if is_long else mt5.ORDER_TYPE_SELL

        tick = mt5.symbol_info_tick(self.SYMBOL)
        if tick is None:
            logger.error("Could not get tick for order — skipping.")
            return None

        price = tick.ask if is_long else tick.bid
        lot_size = round(max(0.01, min(lot_size, config.PROP_MAX_LOT_SIZE)), 2)

        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": self.SYMBOL,
            "volume": lot_size,
            "type": order_type,
            "price": price,
            "sl": signal.stop_loss,
            "tp": signal.take_profit,
            "deviation": 10,
            "magic": 202400,
            "comment": f"astra_v2_{signal.direction[:1]}",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }

        result = mt5.order_send(request)
        if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
            code = result.retcode if result else "None"
            logger.error(f"MT5 order_send rejected: retcode={code}")
            return None

        logger.info(f"MT5 order placed: ticket={result.order} lot={lot_size} {signal.direction} @ {price:.2f}")
        return LiveTrade(
            direction=signal.direction,
            entry_price=price,
            stop_loss=signal.stop_loss,
            take_profit=signal.take_profit,
            partial_tp=signal.partial_tp,
            lot_size=lot_size,
            opened_at=datetime.now(timezone.utc),
            ticket=result.order,
        )

    def close_trade(self, trade: LiveTrade, reason: str) -> None:
        mt5 = self._mt5
        if trade.ticket is None:
            return
        is_long = trade.direction == "BULLISH"
        tick = mt5.symbol_info_tick(self.SYMBOL)
        if tick is None:
            logger.error("close_trade: could not get tick")
            return
        price = tick.bid if is_long else tick.ask
        close_type = mt5.ORDER_TYPE_SELL if is_long else mt5.ORDER_TYPE_BUY

        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": self.SYMBOL,
            "volume": trade.lot_size,
            "type": close_type,
            "position": trade.ticket,
            "price": price,
            "deviation": 10,
            "magic": 202400,
            "comment": f"close_{reason}",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }
        result = mt5.order_send(request)
        if result and result.retcode == mt5.TRADE_RETCODE_DONE:
            trade.status = reason
            logger.info(f"MT5 closed ticket={trade.ticket} reason={reason}")
        else:
            code = result.retcode if result else "None"
            logger.error(f"MT5 close failed: retcode={code}")

    def modify_sl(self, trade: LiveTrade, new_sl: float) -> None:
        mt5 = self._mt5
        if trade.ticket is None:
            return
        request = {
            "action": mt5.TRADE_ACTION_SLTP,
            "symbol": self.SYMBOL,
            "position": trade.ticket,
            "sl": new_sl,
            "tp": trade.take_profit,
        }
        result = mt5.order_send(request)
        if result and result.retcode == mt5.TRADE_RETCODE_DONE:
            trade.stop_loss = new_sl
            logger.debug(f"MT5 SL moved to {new_sl:.2f} ticket={trade.ticket}")
        else:
            code = result.retcode if result else "None"
            logger.warning(f"MT5 modify_sl failed: retcode={code}")

    def partial_close(self, trade: LiveTrade, pct: float) -> None:
        mt5 = self._mt5
        if trade.ticket is None:
            return
        close_vol = round(trade.lot_size * pct, 2)
        close_vol = max(0.01, close_vol)
        is_long = trade.direction == "BULLISH"
        tick = mt5.symbol_info_tick(self.SYMBOL)
        if tick is None:
            return
        price = tick.bid if is_long else tick.ask
        close_type = mt5.ORDER_TYPE_SELL if is_long else mt5.ORDER_TYPE_BUY

        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": self.SYMBOL,
            "volume": close_vol,
            "type": close_type,
            "position": trade.ticket,
            "price": price,
            "deviation": 10,
            "magic": 202400,
            "comment": "partial_tp",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }
        result = mt5.order_send(request)
        if result and result.retcode == mt5.TRADE_RETCODE_DONE:
            trade.partial_closed = True
            trade.lot_size = round(trade.lot_size - close_vol, 2)
            logger.info(f"MT5 partial close {close_vol} lots ticket={trade.ticket}")
        else:
            code = result.retcode if result else "None"
            logger.warning(f"MT5 partial_close failed: retcode={code}")


# ── Telegram provider ──────────────────────────────────────────────────────────

class TelegramProvider(ExecutionProvider):
    """
    Semi-auto: sends signal alerts to Telegram. Human places the trade.
    Tracks the announced trade in memory for BE/trail suggestions.
    Uses OANDA for price and account state.
    """

    def __init__(self) -> None:
        from astra_v2.data.market_data import get_client
        self._oanda = get_client()
        self._bot_token = config.TELEGRAM_BOT_TOKEN
        self._chat_id = config.TELEGRAM_CHAT_ID
        logger.info("TelegramProvider initialized (semi-auto mode).")

    def get_account_state(self) -> AccountState:
        summary = self._oanda.get_account_summary()
        balance = summary.get("balance", 0.0)
        nav = summary.get("NAV", balance)
        unrealized = summary.get("unrealizedPL", 0.0)
        equity = nav
        peak = max(balance, equity)
        dd_pct = (peak - equity) / peak * 100 if peak > 0 else 0.0
        return AccountState(
            balance=balance,
            equity=equity,
            drawdown_pct=max(0.0, dd_pct),
        )

    def get_current_price(self) -> float:
        return self._oanda.get_current_price()

    def open_trade(self, signal: Signal, lot_size: float) -> Optional[LiveTrade]:
        direction = signal.direction
        entry = signal.entry_price
        sl = signal.stop_loss
        tp = signal.take_profit
        partial = signal.partial_tp

        text = (
            f"ASTRA SIGNAL\n"
            f"Direction: {direction}\n"
            f"Entry: {entry:.2f}\n"
            f"SL: {sl:.2f}\n"
            f"TP: {tp:.2f}\n"
            f"Partial TP: {partial:.2f}\n"
            f"Lot: {lot_size:.2f}\n"
            f"Level: {signal.level.level_type.upper()} @ {signal.level.price:.2f}\n"
            f"Macro: {signal.macro_bias.confidence:.0%} {signal.macro_bias.direction}"
        )
        self.send_status(text)

        # Return an in-memory trade for BE/trail tracking
        return LiveTrade(
            direction=direction,
            entry_price=entry,
            stop_loss=sl,
            take_profit=tp,
            partial_tp=partial,
            lot_size=lot_size,
            opened_at=datetime.now(timezone.utc),
            ticket=None,
        )

    def close_trade(self, trade: LiveTrade, reason: str) -> None:
        price = self.get_current_price()
        self.send_status(
            f"CLOSE SIGNAL ({reason})\n"
            f"Close at market ~{price:.2f}\n"
            f"Direction: {trade.direction}"
        )
        trade.status = reason

    def modify_sl(self, trade: LiveTrade, new_sl: float) -> None:
        label = "BREAKEVEN" if not trade.be_moved else "TRAIL"
        self.send_status(
            f"MOVE SL TO {new_sl:.2f} ({label})\n"
            f"Direction: {trade.direction} | Entry: {trade.entry_price:.2f}"
        )
        trade.stop_loss = new_sl

    def partial_close(self, trade: LiveTrade, pct: float) -> None:
        price = self.get_current_price()
        self.send_status(
            f"PARTIAL CLOSE {int(pct*100)}%\n"
            f"At market ~{price:.2f}\n"
            f"Direction: {trade.direction}"
        )
        trade.partial_closed = True

    def send_status(self, message: str) -> None:
        import urllib.request
        import urllib.parse
        url = f"https://api.telegram.org/bot{self._bot_token}/sendMessage"
        data = urllib.parse.urlencode({
            "chat_id": self._chat_id,
            "text": message,
            "parse_mode": "HTML",
        }).encode()
        try:
            with urllib.request.urlopen(url, data=data, timeout=10) as resp:
                if resp.status != 200:
                    logger.warning(f"Telegram sendMessage status={resp.status}")
        except Exception as e:
            logger.error(f"Telegram send failed: {e}")


# ── Trade manager ──────────────────────────────────────────────────────────────

class TradeManager:
    """
    Orchestrates live trading lifecycle.

    Usage:
        mgr = TradeManager(provider)
        # On each signal check tick:
        mgr.attempt_open(signal)
        # On each price tick (every M15 bar close):
        mgr.manage_positions()
    """

    def __init__(self, provider: ExecutionProvider) -> None:
        self._provider = provider
        self._open_trade: Optional[LiveTrade] = None
        self._peak_equity: float = 0.0

    # ── Kill switch ────────────────────────────────────────────────────────────

    def _check_kill_switch(self) -> tuple[bool, str]:
        """
        Returns (should_skip, reason).
        If DD >= KILL_SWITCH level, closes all positions first.
        """
        try:
            state = self._provider.get_account_state()
        except Exception as e:
            logger.error(f"Kill switch: could not get account state: {e}")
            return True, "account_state_error"

        # Update peak equity
        if state.equity > self._peak_equity:
            self._peak_equity = state.equity

        if self._peak_equity > 0:
            current_dd = (self._peak_equity - state.equity) / self._peak_equity * 100
        else:
            current_dd = 0.0

        if current_dd >= config.PROP_KILL_SWITCH_DD_PCT:
            logger.critical(f"KILL SWITCH: DD={current_dd:.2f}% >= {config.PROP_KILL_SWITCH_DD_PCT}% — closing all")
            self._close_all("kill_switch")
            return True, f"kill_switch_dd_{current_dd:.1f}pct"

        if current_dd >= config.PROP_DAILY_STOP_DD_PCT:
            logger.warning(f"Daily stop: DD={current_dd:.2f}% >= {config.PROP_DAILY_STOP_DD_PCT}%")
            return True, f"daily_stop_dd_{current_dd:.1f}pct"

        return False, "ok"

    def _close_all(self, reason: str) -> None:
        if self._open_trade and self._open_trade.status == "open":
            try:
                self._provider.close_trade(self._open_trade, reason)
            except Exception as e:
                logger.error(f"_close_all failed: {e}")
            self._open_trade = None

    # ── Open ───────────────────────────────────────────────────────────────────

    def attempt_open(self, signal: Signal) -> bool:
        """
        Try to open a trade for the given signal.
        Returns True if trade was opened (or announced in Telegram mode).
        """
        if self._open_trade and self._open_trade.status == "open":
            logger.debug("attempt_open: already in a trade")
            return False

        blocked, reason = self._check_kill_switch()
        if blocked:
            logger.info(f"attempt_open blocked by kill switch: {reason}")
            return False

        # Position sizing
        try:
            state = self._provider.get_account_state()
        except Exception as e:
            logger.error(f"attempt_open: account state error: {e}")
            return False

        sl_distance = abs(signal.entry_price - signal.stop_loss)
        if sl_distance < 0.01:
            logger.error("attempt_open: SL distance near zero — skipping")
            return False

        risk_usd = state.balance * config.RISK_PCT
        lot_size = risk_usd / sl_distance

        # Cap at prop firm max lot size; round to nearest 0.01
        lot_size = round(min(lot_size, config.PROP_MAX_LOT_SIZE), 2)
        lot_size = max(0.01, lot_size)

        try:
            trade = self._provider.open_trade(signal, lot_size)
        except Exception as e:
            logger.error(f"attempt_open: open_trade exception: {e}")
            return False

        if trade is None:
            return False

        self._open_trade = trade
        logger.info(
            f"Trade opened: {trade.direction} entry={trade.entry_price:.2f} "
            f"SL={trade.stop_loss:.2f} TP={trade.take_profit:.2f} lot={trade.lot_size}"
        )
        return True

    # ── Manage ─────────────────────────────────────────────────────────────────

    def manage_positions(self) -> None:
        """
        Called once per M15 bar close. Checks BE/trail/partial TP.
        MT5 handles SL/TP hit natively; this manages the dynamic adjustments.
        """
        if not self._open_trade or self._open_trade.status != "open":
            return

        # First, kill switch check on existing position too
        blocked, reason = self._check_kill_switch()
        if blocked:
            return  # _check_kill_switch already closed if needed

        trade = self._open_trade
        is_long = trade.direction == "BULLISH"

        try:
            price = self._provider.get_current_price()
        except Exception as e:
            logger.warning(f"manage_positions: could not get price: {e}")
            return

        sl_dist = abs(trade.entry_price - trade.stop_loss)
        if sl_dist < 0.01:
            return

        profit_dist = (price - trade.entry_price) if is_long else (trade.entry_price - price)

        # 1. Partial TP at 1:1
        if not trade.partial_closed:
            partial_hit = (
                price >= trade.partial_tp if is_long else price <= trade.partial_tp
            )
            if partial_hit:
                try:
                    self._provider.partial_close(trade, 0.5)
                    logger.info(f"Partial TP hit at {price:.2f}")
                except Exception as e:
                    logger.error(f"partial_close failed: {e}")

        # 2. Breakeven at +1R
        if not trade.be_moved and profit_dist >= sl_dist * config.BE_TRIGGER_RR:
            try:
                self._provider.modify_sl(trade, trade.entry_price)
                trade.be_moved = True
                logger.info(f"Breakeven set at {trade.entry_price:.2f}")
            except Exception as e:
                logger.error(f"modify_sl (BE) failed: {e}")

        # 3. Trail SL at +1.5R
        if trade.be_moved and profit_dist >= sl_dist * config.TRAIL_TRIGGER_RR:
            if is_long:
                new_sl = price - config.TRAIL_DISTANCE_USD
                if new_sl > trade.stop_loss:
                    try:
                        self._provider.modify_sl(trade, new_sl)
                    except Exception as e:
                        logger.error(f"modify_sl (trail) failed: {e}")
            else:
                new_sl = price + config.TRAIL_DISTANCE_USD
                if new_sl < trade.stop_loss:
                    try:
                        self._provider.modify_sl(trade, new_sl)
                    except Exception as e:
                        logger.error(f"modify_sl (trail) failed: {e}")

    # ── Status ─────────────────────────────────────────────────────────────────

    @property
    def has_open_trade(self) -> bool:
        return self._open_trade is not None and self._open_trade.status == "open"

    @property
    def open_trade(self) -> Optional[LiveTrade]:
        return self._open_trade


def build_provider() -> ExecutionProvider:
    """
    Factory: returns MT5Provider if MT5 is available and configured,
    otherwise TelegramProvider.
    """
    mode = config.EXECUTION_MODE  # "mt5" | "telegram"
    if mode == "mt5":
        return MT5Provider()
    return TelegramProvider()
