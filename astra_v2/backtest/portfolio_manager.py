"""
Portfolio Manager — Combined DD Gate + Position Sizing
=======================================================
Enforces risk rules across all simultaneously open trades.

Rules:
  1. Portfolio DD gate: if combined open drawdown >= PORTFOLIO_MAX_DD_PCT (6%),
     force-close ALL open trades and halt new entries until next UTC day.

  2. Concurrent trade limit: max PORTFOLIO_CONCURRENT_MAX (2) open trades at once.
     If already at max, new signals are discarded (no queue, no delay).

  3. Concurrent size reduction: if PORTFOLIO_CONCURRENT_MAX trades open,
     new trades use PORTFOLIO_CONCURRENT_REDUCE (0.7x) size multiplier.

Data flow:
  open_trades: list[Trade] → check_portfolio_dd(balance, peak_balance) → True/False
                           → get_size_multiplier(n_open) → float
                           → can_open_trade(n_open) → bool
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from astra_v2 import config

if TYPE_CHECKING:
    from astra_v2.backtest.engine import Trade


def check_portfolio_dd(
    open_trades: list["Trade"],
    balance: float,
    peak_balance: float,
) -> bool:
    """
    Check if drawdown from peak balance (high-water mark) exceeds the halt threshold.

    Uses the running peak balance (highest balance achieved so far) as the reference,
    NOT the starting balance. This ensures that once the account recovers to new highs,
    the DD gate resets — allowing continuous strategy evaluation across a full year.

    Args:
        open_trades: list of currently open Trade objects (unused, reserved for future use)
        balance: current realized account balance
        peak_balance: highest balance achieved so far (high-water mark)

    Returns:
        True if drawdown from peak >= threshold (HALT new trades)
        False if within limits
    """
    if peak_balance <= 0:
        return False

    current_dd_pct = (peak_balance - balance) / peak_balance * 100
    return current_dd_pct >= config.PORTFOLIO_MAX_DD_PCT


def can_open_trade(n_open: int) -> bool:
    """
    Return True if a new trade can be opened given n_open concurrent trades.
    """
    return n_open < config.PORTFOLIO_CONCURRENT_MAX


def get_size_multiplier(n_open: int, base_multiplier: float = 1.0) -> float:
    """
    Return position size multiplier based on number of concurrent open trades.

    If already at max concurrent trades (shouldn't reach here — can_open_trade
    guards this), returns 0 to prevent any trade.

    If one trade is already open, apply PORTFOLIO_CONCURRENT_REDUCE.
    If no trades open, use base_multiplier as-is.

    Args:
        n_open: number of currently open trades BEFORE opening the new one
        base_multiplier: the strategy-supplied size multiplier (RVOL, DXY, etc.)

    Returns:
        Combined position size multiplier
    """
    if n_open >= config.PORTFOLIO_CONCURRENT_MAX:
        return 0.0  # should not happen if can_open_trade is checked first
    if n_open > 0:
        return base_multiplier * config.PORTFOLIO_CONCURRENT_REDUCE
    return base_multiplier
