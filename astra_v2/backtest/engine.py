"""
Backtester — walk-forward validation of the 3-layer signal strategy.

Two modes:
  proxy  — deterministic LLM proxy (free, instant, reproducible)
  llm    — real FRED + LLM calls (slow, costs ~$13 for 5yr, use for validation)

Anti-look-ahead guarantee:
  For every bar N, only bars[0:N] are passed to extract_levels().
  Daily level precomputation uses only data available before market open.

Data flow:
  Dukascopy M15 bars + FRED parquet + yfinance bulk
    → for each bar:
        [no look-ahead] levels = extract_levels(bars[:N], ...)
        macro = proxy_macro_bias(features) | real LLM
        signal = check_signal(macro, levels, price, now)
        simulate trade → log PnL
    → metrics: PF, MaxDD, WR, avg_RR, trades_per_day
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Literal, Optional

import numpy as np
import pandas as pd

from astra_v2 import config
from astra_v2.core.technical_engine import extract_levels, KeyLevel
from astra_v2.core.signal_gate import check_signal, is_active_session, Signal
from astra_v2.core.macro_engine import MacroBias
from astra_v2.data.macro_features import compute_macro_features

logger = logging.getLogger(__name__)

BacktestMode = Literal["proxy", "llm"]


@dataclass
class Trade:
    direction: str
    entry: float
    stop_loss: float
    take_profit: float
    partial_tp: float
    opened_at: datetime
    closed_at: Optional[datetime] = None
    exit_price: Optional[float] = None
    pnl: float = 0.0           # in USD per unit (1 oz gold = 1 unit)
    dollar_pnl: float = 0.0   # scaled by position size — used by Monte Carlo
    status: str = "open"       # open, tp, sl, partial_tp, be_sl, trail_sl, forced
    partial_closed: bool = False
    be_moved: bool = False


@dataclass
class BacktestResult:
    trades: list[Trade]
    equity_curve: list[float]
    start_balance: float
    end_balance: float

    @property
    def total_trades(self) -> int:
        return len([t for t in self.trades if t.status != "open"])

    @property
    def wins(self) -> list[Trade]:
        return [t for t in self.trades if t.pnl > 0]

    @property
    def losses(self) -> list[Trade]:
        return [t for t in self.trades if t.pnl < 0]

    @property
    def win_rate(self) -> float:
        if not self.total_trades:
            return 0.0
        return len(self.wins) / self.total_trades

    @property
    def profit_factor(self) -> float:
        gross_profit = sum(t.pnl for t in self.wins)
        gross_loss = abs(sum(t.pnl for t in self.losses))
        if gross_loss == 0:
            return float("inf") if gross_profit > 0 else 0.0
        return gross_profit / gross_loss

    @property
    def max_drawdown_pct(self) -> float:
        if not self.equity_curve:
            return 0.0
        eq = np.array(self.equity_curve)
        peaks = np.maximum.accumulate(eq)
        dd = (peaks - eq) / peaks * 100
        return float(dd.max())

    @property
    def avg_rr(self) -> float:
        closed = [t for t in self.trades if t.status != "open" and t.exit_price is not None]
        if not closed:
            return 0.0
        rrs = []
        for t in closed:
            sl_dist = abs(t.entry - t.stop_loss)
            if sl_dist > 0:
                rrs.append(t.pnl / sl_dist)
        return float(np.mean(rrs)) if rrs else 0.0

    def summary(self) -> dict:
        return {
            "total_trades": self.total_trades,
            "win_rate": round(self.win_rate, 3),
            "profit_factor": round(self.profit_factor, 3),
            "max_drawdown_pct": round(self.max_drawdown_pct, 2),
            "avg_rr": round(self.avg_rr, 2),
            "end_balance": round(self.end_balance, 2),
            "net_pnl": round(self.end_balance - self.start_balance, 2),
        }


def _simulate_trade(
    trade: Trade,
    bar: pd.Series,
) -> None:
    """
    Update an open trade against the current bar.
    Modifies trade in-place.

    Order of checks: SL → TP → BE → Trail
    Slippage applied on exit only (already applied on entry).
    """
    if trade.status != "open":
        return

    is_long = trade.direction == "BULLISH"
    bar_high = float(bar["high"])
    bar_low = float(bar["low"])

    # 1. Check stop loss hit
    sl_hit = bar_low <= trade.stop_loss if is_long else bar_high >= trade.stop_loss
    if sl_hit:
        exit_price = trade.stop_loss
        # Slippage is adverse on SL exit — applied once to exit_price
        trade.exit_price = exit_price - config.SLIPPAGE_USD if is_long else exit_price + config.SLIPPAGE_USD
        if trade.partial_closed:
            # Half position already closed at partial_tp — only remaining 50% hits SL
            trade.pnl += (trade.exit_price - trade.entry) * (0.5 if is_long else -0.5)
        else:
            trade.pnl = (trade.exit_price - trade.entry) * (1 if is_long else -1)
        trade.status = "be_sl" if trade.be_moved else "sl"
        return

    # 2. Check partial take profit (1:1 close 50%)
    if not trade.partial_closed:
        partial_hit = bar_high >= trade.partial_tp if is_long else bar_low <= trade.partial_tp
        if partial_hit:
            exit_price = trade.partial_tp
            trade.pnl += (exit_price - trade.entry) * (0.5 if is_long else -0.5)
            trade.partial_closed = True

    # 3. Check full take profit
    tp_hit = bar_high >= trade.take_profit if is_long else bar_low <= trade.take_profit
    if tp_hit:
        exit_price = trade.take_profit
        trade.exit_price = exit_price
        remaining_pct = 0.5 if trade.partial_closed else 1.0
        trade.pnl += (exit_price - trade.entry) * (remaining_pct if is_long else -remaining_pct)
        trade.status = "tp"
        return

    # 4. Breakeven: move SL to entry at +1R
    sl_dist = abs(trade.entry - trade.stop_loss)
    if not trade.be_moved:
        profit_dist = (bar_high - trade.entry) if is_long else (trade.entry - bar_low)
        if profit_dist >= sl_dist * config.BE_TRIGGER_RR:
            trade.stop_loss = trade.entry
            trade.be_moved = True

    # 5. Trail SL at +1.5R
    if trade.be_moved:
        profit_dist = (bar_high - trade.entry) if is_long else (trade.entry - bar_low)
        if profit_dist >= sl_dist * config.TRAIL_TRIGGER_RR:
            new_sl = bar_high - config.TRAIL_DISTANCE_USD if is_long else bar_low + config.TRAIL_DISTANCE_USD
            if is_long:
                trade.stop_loss = max(trade.stop_loss, new_sl)
            else:
                trade.stop_loss = min(trade.stop_loss, new_sl)


def run_backtest(
    bars: pd.DataFrame,
    fred_df: pd.DataFrame,
    yfinance_df: pd.DataFrame,
    cot_df: pd.DataFrame,
    mode: BacktestMode = "proxy",
    start_balance: float = 10_000.0,
    wf_train_months: int = 6,
    wf_test_months: int = 1,
) -> BacktestResult:
    """
    Walk-forward backtest.

    Args:
        bars: M15 Dukascopy OHLCV (UTC index). Must be pre-validated.
        fred_df: bulk FRED data (daily)
        yfinance_df: bulk yfinance data (daily)
        cot_df: COT data (weekly)
        mode: "proxy" or "llm"
        start_balance: starting account balance in USD
        wf_train_months: walk-forward training window
        wf_test_months: walk-forward test window

    Returns BacktestResult with all trades and equity curve.
    """
    from astra_v2.backtest.llm_proxy import proxy_macro_bias

    bars.index = pd.to_datetime(bars.index, utc=True)
    bars = bars.sort_index()

    balance = start_balance
    equity_curve = [balance]
    all_trades: list[Trade] = []
    open_trade: Optional[Trade] = None
    pending_signal: Optional[Signal] = None  # enter at next bar open

    # Walk-forward windows
    test_start = bars.index[0] + pd.DateOffset(months=wf_train_months)
    test_bars = bars[bars.index >= test_start]

    # Daily macro cache (recomputed once per day to avoid redundant calls)
    daily_macro_cache: dict = {}   # {date_str: MacroBias}
    daily_level_cache: dict = {}   # {date_str: list[KeyLevel]}
    daily_trade_count: dict = {}   # {date_str: int}

    logger.info(f"Backtest: {len(test_bars)} test bars, mode={mode}, balance=${start_balance:,.0f}")

    for i, (ts, bar) in enumerate(test_bars.iterrows()):
        now: datetime = ts.to_pydatetime()
        date_str = now.strftime("%Y-%m-%d")

        # ── Open pending signal at this bar's open (next bar after signal) ──
        if pending_signal is not None and open_trade is None:
            sig = pending_signal
            pending_signal = None
            entry_price = float(bar["open"])
            entry_with_slippage = (entry_price + config.SLIPPAGE_USD
                                   if sig.direction == "BULLISH"
                                   else entry_price - config.SLIPPAGE_USD)
            open_trade = Trade(
                direction=sig.direction,
                entry=entry_with_slippage,
                stop_loss=sig.stop_loss,
                take_profit=sig.take_profit,
                partial_tp=sig.partial_tp,
                opened_at=now,
            )
            logger.debug(f"{now} | ENTER {sig.direction} @ {entry_with_slippage:.2f} (next-bar open) SL={sig.stop_loss:.2f} TP={sig.take_profit:.2f}")

        # ── Manage open trade ───────────────────────────────────────────────
        if open_trade and open_trade.status == "open":
            _simulate_trade(open_trade, bar)
            if open_trade.status != "open":
                # Trade closed this bar
                sl_dist = abs(open_trade.entry - open_trade.stop_loss)
                risk_usd = balance * config.RISK_PCT
                position_units = risk_usd / sl_dist if sl_dist > 0 else 0

                trade_pnl = open_trade.pnl * position_units
                open_trade.dollar_pnl = trade_pnl
                balance += trade_pnl
                equity_curve.append(balance)
                open_trade.closed_at = now
                all_trades.append(open_trade)
                open_trade = None
                logger.debug(f"Trade closed: {trade_pnl:+.2f} | Balance: {balance:.2f}")
                continue

        equity_curve.append(balance)

        # ── Only look for new trades during active sessions ─────────────────
        if not is_active_session(now):
            continue

        # ── Skip if already in a trade or pending entry ─────────────────────
        if open_trade or pending_signal is not None:
            continue

        # ── Daily trade limit (per-day counter) ────────────────────────────
        today_count = daily_trade_count.get(date_str, 0)
        if today_count >= config.MAX_TRADES_PER_DAY:
            continue

        # ── Macro bias (once per day) ──────────────────────────────────────
        if date_str not in daily_macro_cache:
            try:
                features = compute_macro_features(
                    now, fred_df=fred_df, yfinance_df=yfinance_df, cot_df=cot_df
                )
                if mode == "proxy":
                    macro = proxy_macro_bias(features)
                else:
                    from astra_v2.core.macro_engine import get_bias
                    macro = get_bias(fred_df=fred_df, yfinance_df=yfinance_df, cot_df=cot_df, dt=now)
            except Exception as e:
                logger.debug(f"Macro failed for {date_str}: {e}")
                continue
            daily_macro_cache[date_str] = macro

        macro = daily_macro_cache[date_str]

        # ── Key levels (once per day, precomputed before session open) ──────
        # ANTI-LOOK-AHEAD: bars up to but not including current bar
        bars_so_far = bars[bars.index < ts]
        if date_str not in daily_level_cache:
            levels = extract_levels(bars_so_far, float(bar["close"]), now)
            daily_level_cache[date_str] = levels

        levels = daily_level_cache[date_str]
        current_price = float(bar["close"])

        # ── Signal gate ────────────────────────────────────────────────────
        signal, reason = check_signal(
            macro=macro,
            levels=levels,
            current_price=current_price,
            now=now,
            supabase_client=None,    # backtest: no Supabase
            local_trade_count=today_count,
        )

        if signal is None:
            continue

        # ── Queue entry at next bar open (avoids bar-close entry bias) ─────
        pending_signal = signal
        daily_trade_count[date_str] = today_count + 1
        logger.debug(f"{now} | SIGNAL {signal.direction} detected @ close {current_price:.2f} → entering next bar open")

    # Force-close any open trade at end of backtest
    pending_signal = None  # discard any pending entry at end of data
    if open_trade and open_trade.status == "open":
        last_bar = test_bars.iloc[-1]
        open_trade.exit_price = float(last_bar["close"])
        sl_dist = abs(open_trade.entry - open_trade.stop_loss)
        if sl_dist > 0:
            exit_p = open_trade.exit_price
            entry = open_trade.entry
            pnl_per_unit = (exit_p - entry) if open_trade.direction == "BULLISH" else (entry - exit_p)
            risk_usd = balance * config.RISK_PCT
            position_units = risk_usd / sl_dist
            balance += pnl_per_unit * position_units
        open_trade.status = "forced"
        open_trade.closed_at = test_bars.index[-1].to_pydatetime()
        all_trades.append(open_trade)

    return BacktestResult(
        trades=all_trades,
        equity_curve=equity_curve,
        start_balance=start_balance,
        end_balance=balance,
    )
