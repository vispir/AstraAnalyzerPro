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
from astra_v2.core.signal_gate import is_active_session, Signal
from astra_v2.core.macro_engine import MacroBias
from astra_v2.data.macro_features import compute_macro_features
from astra_v2.strategies import StrategyContext, get_strategy
from astra_v2.core.cross_asset import apply_cross_asset_to_signal, slice_m15_asof
from astra_v2.backtest.portfolio_manager import (
    check_portfolio_dd, can_open_trade, get_size_multiplier,
)

logger = logging.getLogger(__name__)

BacktestMode = Literal["proxy", "llm"]


def _levels_slot(hour: int) -> str:
    """Cache slot that invalidates when session levels become available."""
    if hour < config.LONDON_CLOSE_UTC:   # <12: no session levels yet
        return "pre"
    elif hour < config.NY_CLOSE_UTC:     # 12-17: London session levels available
        return "mid"
    return "post"                         # 17+: NY session levels available


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
    signal_at: Optional[datetime] = None
    signal_price: Optional[float] = None
    level_type: Optional[str] = None
    level_direction: Optional[str] = None
    level_price: Optional[float] = None
    level_strength: Optional[float] = None
    macro_direction: Optional[str] = None
    macro_confidence: Optional[float] = None
    macro_reasoning: Optional[str] = None
    strategy_id: str = "legacy_v1"
    setup_family: Optional[str] = None
    session_label: Optional[str] = None
    sweep_side: Optional[str] = None
    sweep_size: Optional[float] = None
    confirmation_at: Optional[datetime] = None
    confirmation_type: Optional[str] = None
    bars_since_sweep: Optional[int] = None
    execution_timeframe: Optional[str] = None
    entry_trigger_price: Optional[float] = None
    intraday_forced_exit: bool = False
    bars_managed: int = 0
    opening_bar_behavior: Optional[str] = None
    first_excursion_side: Optional[str] = None
    bars_to_first_profit: Optional[int] = None
    bars_to_first_drawdown: Optional[int] = None
    max_favorable_excursion_usd: float = 0.0
    max_adverse_excursion_usd: float = 0.0
    size_multiplier: float = 1.0      # from Signal.size_multiplier
    initial_risk_usd: float = 0.0     # |entry - original stop| at trade open (for sizing)


@dataclass
class PendingSignalState:
    signal: Signal
    queued_at: datetime


def _update_trade_diagnostics(trade: Trade, bar: pd.Series) -> None:
    is_long = trade.direction == "BULLISH"
    bar_high = float(bar["high"])
    bar_low = float(bar["low"])
    favorable = max(0.0, (bar_high - trade.entry) if is_long else (trade.entry - bar_low))
    adverse = max(0.0, (trade.entry - bar_low) if is_long else (bar_high - trade.entry))

    trade.max_favorable_excursion_usd = max(trade.max_favorable_excursion_usd, favorable)
    trade.max_adverse_excursion_usd = max(trade.max_adverse_excursion_usd, adverse)

    if trade.bars_to_first_profit is None and favorable > 0:
        trade.bars_to_first_profit = trade.bars_managed
    if trade.bars_to_first_drawdown is None and adverse > 0:
        trade.bars_to_first_drawdown = trade.bars_managed

    if trade.bars_managed == 0:
        if favorable > 0 and adverse > 0:
            trade.opening_bar_behavior = "both_sides"
        elif favorable > 0:
            trade.opening_bar_behavior = "favorable_only"
        elif adverse > 0:
            trade.opening_bar_behavior = "adverse_only"
        else:
            trade.opening_bar_behavior = "flat"

    if trade.first_excursion_side is None:
        if favorable > 0 and adverse > 0:
            trade.first_excursion_side = "both"
        elif favorable > 0:
            trade.first_excursion_side = "favorable"
        elif adverse > 0:
            trade.first_excursion_side = "adverse"


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
    bars_so_far: pd.DataFrame = None,
) -> None:
    """
    Update an open trade against the current bar.
    Modifies trade in-place.

    Order of checks: SL → TP → BE → Trail
    Slippage applied on exit only (already applied on entry).
    """
    if trade.status != "open":
        return

    _update_trade_diagnostics(trade, bar)
    trade.bars_managed += 1

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

    # 5. Trail SL at +1.5R (ATR-based distance)
    if trade.be_moved:
        profit_dist = (bar_high - trade.entry) if is_long else (trade.entry - bar_low)
        if profit_dist >= sl_dist * config.TRAIL_TRIGGER_RR:
            # Calculate ATR for trailing distance
            atr = None
            if bars_so_far is not None and len(bars_so_far) >= config.TRAIL_ATR_PERIOD + 1:
                recent = bars_so_far.tail(config.TRAIL_ATR_PERIOD + 1)
                highs = recent["high"].astype(float)
                lows = recent["low"].astype(float)
                closes = recent["close"].astype(float)
                prev_close = closes.shift(1)
                tr = pd.concat([
                    highs - lows,
                    (highs - prev_close).abs(),
                    (lows - prev_close).abs()
                ], axis=1).max(axis=1)
                atr = tr.iloc[1:].mean()

            # Fallback to fixed distance if ATR unavailable
            trail_distance = atr * config.TRAIL_DISTANCE_ATR if atr and atr > 0 else 5.0

            new_sl = bar_high - trail_distance if is_long else bar_low + trail_distance
            if is_long:
                trade.stop_loss = max(trade.stop_loss, new_sl)
            else:
                trade.stop_loss = min(trade.stop_loss, new_sl)


def _should_force_intraday_close(trade: Trade, now: datetime) -> bool:
    if trade.status != "open":
        return False
    if trade.strategy_id == "sweep_reversal_v1":
        cutoff_hour = config.SWEEP_REVERSAL_V1_FORCE_CLOSE_HOUR_UTC
    elif trade.strategy_id == "sweep_reversal_v2":
        cutoff_hour = config.SWEEP_REVERSAL_V2_FORCE_CLOSE_HOUR_UTC
    elif trade.strategy_id == "sweep_reversal_v3":
        cutoff_hour = config.SWEEP_REVERSAL_V3_FORCE_CLOSE_HOUR_UTC
    elif trade.strategy_id in ("sweep_reversal_v4", "sweep_reversal_v4a", "sweep_reversal_v4b"):
        cutoff_hour = config.SWEEP_REVERSAL_V4_FORCE_CLOSE_HOUR_UTC
    elif trade.strategy_id == "breakout_retest_v1":
        cutoff_hour = config.BREAKOUT_RETEST_V1_FORCE_CLOSE_HOUR_UTC
    elif trade.strategy_id == "smc_fvg_v1":
        cutoff_hour = config.SMC_FVG_V1_FORCE_CLOSE_HOUR_UTC
    elif trade.strategy_id == "smc_ob_v1":
        cutoff_hour = config.SMC_OB_V1_FORCE_CLOSE_HOUR_UTC
    else:
        return False
    if trade.opened_at.date() != now.date():
        return True
    return now.hour >= cutoff_hour


def _trade_from_signal(sig: Signal, *, entry_price: float, opened_at: datetime) -> Trade:
    # For non-M1 execution (e.g. M15 next-bar-open), actual entry can differ from signal
    # close price. Recalculate TP/partial_tp to maintain intended R:R from actual entry.
    stop_loss = sig.stop_loss
    signal_risk = abs(sig.entry_price - stop_loss)
    if sig.execution_timeframe != "M1" and signal_risk > 1e-9:
        if sig.direction == "BULLISH":
            tp_rr = (sig.take_profit - sig.entry_price) / signal_risk
            partial_rr = (sig.partial_tp - sig.entry_price) / signal_risk
            actual_risk = entry_price - stop_loss
            take_profit = entry_price + actual_risk * tp_rr
            partial_tp = entry_price + actual_risk * partial_rr
        else:
            tp_rr = (sig.entry_price - sig.take_profit) / signal_risk
            partial_rr = (sig.entry_price - sig.partial_tp) / signal_risk
            actual_risk = stop_loss - entry_price
            take_profit = entry_price - actual_risk * tp_rr
            partial_tp = entry_price - actual_risk * partial_rr
    else:
        take_profit = sig.take_profit
        partial_tp = sig.partial_tp
    # initial_risk_usd: distance from actual entry to original stop, used for position
    # sizing throughout the trade's life (avoids trailing-stop distortion).
    if sig.direction == "BULLISH":
        initial_risk_usd = max(entry_price - stop_loss, 0.0)
    else:
        initial_risk_usd = max(stop_loss - entry_price, 0.0)

    return Trade(
        direction=sig.direction,
        entry=entry_price,
        stop_loss=stop_loss,
        take_profit=take_profit,
        partial_tp=partial_tp,
        opened_at=opened_at,
        signal_at=sig.timestamp,
        signal_price=sig.entry_price,
        level_type=sig.level.level_type,
        level_direction=sig.level.direction,
        level_price=sig.level.price,
        level_strength=sig.level.strength,
        macro_direction=sig.macro_bias.direction,
        macro_confidence=sig.macro_bias.confidence,
        macro_reasoning=sig.macro_bias.reasoning,
        strategy_id=sig.strategy_id,
        setup_family=sig.setup_family,
        session_label=sig.session_label,
        sweep_side=sig.sweep_side,
        sweep_size=sig.sweep_size,
        confirmation_at=sig.confirmation_at,
        confirmation_type=sig.confirmation_type,
        bars_since_sweep=sig.bars_since_sweep,
        execution_timeframe=sig.execution_timeframe,
        entry_trigger_price=sig.entry_trigger_price,
        size_multiplier=sig.size_multiplier,
        initial_risk_usd=initial_risk_usd,
    )


def _finalize_closed_trade(trade: Trade, *, balance: float, closed_at: datetime) -> tuple[float, Trade]:
    # Use initial_risk_usd (set at trade open) so trailing-stop movement
    # doesn't distort position sizing. Fall back to current sl_dist if missing.
    sl_dist = trade.initial_risk_usd if trade.initial_risk_usd > 0 else abs(trade.entry - trade.stop_loss)
    risk_usd = balance * config.RISK_PCT * trade.size_multiplier
    position_units = risk_usd / sl_dist if sl_dist > 0 else 0
    trade_pnl = trade.pnl * position_units
    trade.dollar_pnl = trade_pnl
    balance += trade_pnl
    trade.closed_at = closed_at
    return balance, trade


def run_backtest(
    bars: pd.DataFrame,
    fred_df: pd.DataFrame,
    yfinance_df: pd.DataFrame,
    cot_df: pd.DataFrame,
    m1_bars: Optional[pd.DataFrame] = None,
    h4_bars: Optional[pd.DataFrame] = None,
    mode: BacktestMode = "proxy",
    strategy_id: str = config.DEFAULT_STRATEGY_ID,
    start_balance: float = 10_000.0,
    wf_train_months: int = 6,
    wf_test_months: int = 1,
    primary_symbol: Optional[str] = None,
    cross_symbol_m15: Optional[dict[str, pd.DataFrame]] = None,
) -> BacktestResult:
    """
    Walk-forward backtest.

    Args:
        bars: M15 Dukascopy OHLCV (UTC index). Must be pre-validated.
        fred_df: bulk FRED data (daily)
        yfinance_df: bulk yfinance data (daily)
        cot_df: COT data (weekly)
        mode: "proxy" or "llm"
        strategy_id: strategy registry id
        start_balance: starting account balance in USD
        wf_train_months: walk-forward training window
        wf_test_months: walk-forward test window

    Returns BacktestResult with all trades and equity curve.
    """
    from astra_v2.backtest.llm_proxy import proxy_macro_bias
    strategy = get_strategy(strategy_id)
    sym_primary = (primary_symbol or config.BACKTEST_PRIMARY_SYMBOL).strip().upper()

    # SMC strategies (smc_fvg_v1, smc_ob_v1, etc.) require the portfolio engine
    # which builds market structure, FVG, OB, and regime context per bar.
    # Delegate automatically so callers don't need to know which engine to use.
    smc_ids = {"smc_fvg_v1", "smc_ob_v1"}
    if strategy_id in smc_ids:
        return run_backtest_portfolio(
            bars=bars, fred_df=fred_df, yfinance_df=yfinance_df, cot_df=cot_df,
            m1_bars=m1_bars, h4_bars=h4_bars, mode=mode,
            strategy_ids=[strategy_id], start_balance=start_balance,
            wf_train_months=wf_train_months, wf_test_months=wf_test_months,
            primary_symbol=sym_primary,
            cross_symbol_m15=cross_symbol_m15,
        )
    required_level_types = (
        set(strategy.required_level_types)
        if getattr(strategy, "required_level_types", None) is not None
        else None
    )

    bars.index = pd.to_datetime(bars.index, utc=True)
    bars = bars.sort_index()
    if m1_bars is not None:
        m1_bars = m1_bars.copy()
        m1_bars.index = pd.to_datetime(m1_bars.index, utc=True)
        m1_bars = m1_bars.sort_index()
    if h4_bars is not None:
        h4_bars = h4_bars.copy()
        h4_bars.index = pd.to_datetime(h4_bars.index, utc=True)
        h4_bars = h4_bars.sort_index()

    balance = start_balance
    equity_curve = [balance]
    all_trades: list[Trade] = []
    open_trade: Optional[Trade] = None
    pending_signal: Optional[PendingSignalState] = None  # enter at next execution window

    # Walk-forward windows
    test_start = bars.index[0] + pd.DateOffset(months=wf_train_months)
    test_bars = bars[bars.index >= test_start]

    # Daily macro cache (recomputed once per day to avoid redundant calls)
    daily_macro_cache: dict = {}   # {date_str: MacroBias}
    # Levels cache keyed by session slot — invalidates when new session closes
    # slot: "pre" (before London close), "mid" (after London close), "post" (after NY close)
    levels_cache: dict = {}        # {f"{date_str}_{slot}": list[KeyLevel]}
    daily_trade_count: dict = {}   # {date_str: int}

    logger.info(
        f"Backtest: {len(test_bars)} test bars, mode={mode}, "
        f"strategy={strategy_id}, balance=${start_balance:,.0f}"
    )

    start_idx = int(bars.index.searchsorted(test_start))

    for offset in range(len(test_bars)):
        pos = start_idx + offset
        ts = bars.index[pos]
        bar = bars.iloc[pos]
        now: datetime = ts.to_pydatetime()
        interval_end = (ts + pd.Timedelta(minutes=15)).to_pydatetime()
        date_str = now.strftime("%Y-%m-%d")
        # Equivalent to `bars[bars.index < ts]` but much faster on sorted indexes.
        bars_so_far = bars.iloc[:pos]
        cross_views = {}  # TODO: enable slice_m15_asof only for strategies that need cross-asset
        ctx_cross = None
        h4_so_far = None
        if h4_bars is not None:
            h4_cutoff = int(h4_bars.index.searchsorted(pd.Timestamp(interval_end)))
            h4_so_far = h4_bars.iloc[:h4_cutoff]
        m1_recent = None
        m1_interval = None
        if m1_bars is not None:
            interval_end_ts = pd.Timestamp(interval_end)
            m1_end_idx = int(m1_bars.index.searchsorted(interval_end_ts))
            m1_start_idx = int(m1_bars.index.searchsorted(ts))
            m1_recent_start_idx = int(
                m1_bars.index.searchsorted(interval_end_ts - pd.Timedelta(minutes=30))
            )
            m1_recent = m1_bars.iloc[m1_recent_start_idx:m1_end_idx]
            m1_interval = m1_bars.iloc[m1_start_idx:m1_end_idx]
        opened_and_managed_on_m1 = False

        # ── Open pending signal on the next execution window ──
        if pending_signal is not None and open_trade is None:
            # Discard stale signals: if a data gap caused the pending signal to age
            # more than 30 min, the setup has likely invalidated.
            signal_age_minutes = (now - pending_signal.queued_at).total_seconds() / 60
            if signal_age_minutes > 30:
                pending_signal = None
            else:
                pass  # proceed below
        if pending_signal is not None and open_trade is None:
            sig = pending_signal.signal
            pending_signal = None
            if sig.execution_timeframe == "M1":
                if m1_interval is not None and not m1_interval.empty:
                    trigger_price = sig.entry_trigger_price or sig.entry_price
                    if sig.direction == "BULLISH":
                        trigger_rows = m1_interval[m1_interval["high"] >= trigger_price]
                    else:
                        trigger_rows = m1_interval[m1_interval["low"] <= trigger_price]
                    if not trigger_rows.empty:
                        trigger_ts = trigger_rows.index[0].to_pydatetime()
                        entry_with_slippage = (
                            trigger_price + config.SLIPPAGE_USD
                            if sig.direction == "BULLISH"
                            else trigger_price - config.SLIPPAGE_USD
                        )
                        open_trade = _trade_from_signal(sig, entry_price=entry_with_slippage, opened_at=trigger_ts)
                        logger.debug(
                            f"{trigger_ts} | ENTER {sig.direction} @ {entry_with_slippage:.2f} "
                            f"(M1 trigger) SL={sig.stop_loss:.2f} TP={sig.take_profit:.2f}"
                        )
                        for minute_ts, minute_bar in m1_interval[m1_interval.index >= pd.Timestamp(trigger_ts)].iterrows():
                            _simulate_trade(open_trade, minute_bar, bars_so_far)
                            if open_trade.status != "open":
                                balance, open_trade = _finalize_closed_trade(
                                    open_trade,
                                    balance=balance,
                                    closed_at=minute_ts.to_pydatetime(),
                                )
                                equity_curve.append(balance)
                                all_trades.append(open_trade)
                                open_trade = None
                                break
                        opened_and_managed_on_m1 = True
            else:
                entry_price = float(bar["open"])
                entry_with_slippage = (
                    entry_price + config.SLIPPAGE_USD
                    if sig.direction == "BULLISH"
                    else entry_price - config.SLIPPAGE_USD
                )
                # Guard: discard if entry gapped past the stop loss (e.g. weekend/news gap).
                # This would create an inverted trade structure with negative actual risk.
                gap_invalid = (
                    sig.direction == "BULLISH" and entry_with_slippage <= sig.stop_loss
                ) or (
                    sig.direction == "BEARISH" and entry_with_slippage >= sig.stop_loss
                )
                if gap_invalid:
                    logger.debug(
                        f"{now} | DISCARD {sig.direction} signal: entry {entry_with_slippage:.2f} "
                        f"gapped past SL {sig.stop_loss:.2f}"
                    )
                else:
                    open_trade = _trade_from_signal(sig, entry_price=entry_with_slippage, opened_at=now)
                    logger.debug(
                        f"{now} | ENTER {sig.direction} @ {entry_with_slippage:.2f} "
                        f"(next-bar open) SL={sig.stop_loss:.2f} TP={sig.take_profit:.2f}"
                    )

        # ── Manage open trade ───────────────────────────────────────────────
        if open_trade and open_trade.status == "open":
            if opened_and_managed_on_m1:
                pass
            elif open_trade.execution_timeframe == "M1" and m1_interval is not None and not m1_interval.empty:
                minute_slice = m1_interval[m1_interval.index > pd.Timestamp(open_trade.opened_at)]
                for minute_ts, minute_bar in minute_slice.iterrows():
                    _simulate_trade(open_trade, minute_bar, bars_so_far)
                    if open_trade.status != "open":
                        balance, open_trade = _finalize_closed_trade(
                            open_trade,
                            balance=balance,
                            closed_at=minute_ts.to_pydatetime(),
                        )
                        equity_curve.append(balance)
                        all_trades.append(open_trade)
                        open_trade = None
                        logger.debug(f"Trade closed: {all_trades[-1].dollar_pnl:+.2f} | Balance: {balance:.2f}")
                        break
            else:
                _simulate_trade(open_trade, bar, bars_so_far)
            if open_trade is None:
                continue
            if open_trade.status != "open":
                # Trade closed this bar
                balance, open_trade = _finalize_closed_trade(
                    open_trade,
                    balance=balance,
                    closed_at=now,
                )
                equity_curve.append(balance)
                all_trades.append(open_trade)
                open_trade = None
                logger.debug(f"Trade closed: {all_trades[-1].dollar_pnl:+.2f} | Balance: {balance:.2f}")
                continue
            if _should_force_intraday_close(open_trade, now):
                open_trade.exit_price = float(bar["close"])
                sl_dist = open_trade.initial_risk_usd if open_trade.initial_risk_usd > 0 else abs(open_trade.entry - open_trade.stop_loss)
                if sl_dist > 0:
                    exit_p = open_trade.exit_price
                    entry = open_trade.entry
                    pnl_per_unit = (exit_p - entry) if open_trade.direction == "BULLISH" else (entry - exit_p)
                    risk_usd = balance * config.RISK_PCT * open_trade.size_multiplier
                    position_units = risk_usd / sl_dist
                    open_trade.pnl += pnl_per_unit * (0.5 if open_trade.partial_closed else 1.0)
                    open_trade.dollar_pnl = open_trade.pnl * position_units
                    balance += open_trade.dollar_pnl
                open_trade.status = "forced"
                open_trade.intraday_forced_exit = True
                open_trade.closed_at = now
                equity_curve.append(balance)
                all_trades.append(open_trade)
                open_trade = None
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

        # ── Key levels (cached per session slot, not per bar) ───────────────
        # ANTI-LOOK-AHEAD: bars up to but not including current bar.
        # Invalidates when a session closes and new session levels become available.
        levels_key = f"{date_str}_{_levels_slot(now.hour)}"
        if levels_key not in levels_cache:
            levels_cache[levels_key] = extract_levels(
                bars_so_far,
                float(bar["close"]),
                now,
                allowed_level_types=required_level_types,
            )
        levels = levels_cache[levels_key]
        current_price = float(bar["close"])

        # ── Signal gate ────────────────────────────────────────────────────
        signal, reason = strategy.generate_signal(
            StrategyContext(
                strategy_id=strategy_id,  # type: ignore[arg-type]
                now=now,
                current_price=current_price,
                current_bar=bar,
                bars_so_far=bars_so_far,
                levels=levels,
                macro=macro,
                local_trade_count=today_count,
                bar_end=interval_end,
                h4_bars=h4_so_far,
                m1_bars=m1_recent,
                primary_symbol=sym_primary,
                cross_symbol_m15=ctx_cross,
            ),
            supabase_client=None,
        )

        if signal is None:
            continue
        if cross_views:
            signal = apply_cross_asset_to_signal(signal, cross_views)
        if signal is None:
            continue

        # ── Queue entry at next bar open (avoids bar-close entry bias) ─────
        pending_signal = PendingSignalState(signal=signal, queued_at=now)
        daily_trade_count[date_str] = today_count + 1
        logger.debug(f"{now} | SIGNAL {signal.direction} detected @ close {current_price:.2f} → entering next bar open")

    # Force-close any open trade at end of backtest
    pending_signal = None  # discard any pending entry at end of data
    if open_trade and open_trade.status == "open":
        last_bar = test_bars.iloc[-1]
        open_trade.exit_price = float(last_bar["close"])
        sl_dist = open_trade.initial_risk_usd if open_trade.initial_risk_usd > 0 else abs(open_trade.entry - open_trade.stop_loss)
        if sl_dist > 0:
            exit_p = open_trade.exit_price
            entry = open_trade.entry
            pnl_per_unit = (exit_p - entry) if open_trade.direction == "BULLISH" else (entry - exit_p)
            risk_usd = balance * config.RISK_PCT * open_trade.size_multiplier
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


def run_backtest_portfolio(
    bars: pd.DataFrame,
    fred_df: pd.DataFrame,
    yfinance_df: pd.DataFrame,
    cot_df: pd.DataFrame,
    m1_bars: Optional[pd.DataFrame] = None,
    h4_bars: Optional[pd.DataFrame] = None,
    mode: BacktestMode = "proxy",
    strategy_ids: list[str] = None,
    start_balance: float = 10_000.0,
    wf_train_months: int = 6,
    wf_test_months: int = 1,
    holdout_year: Optional[int] = None,
    primary_symbol: Optional[str] = None,
    cross_symbol_m15: Optional[dict[str, pd.DataFrame]] = None,
) -> BacktestResult:
    """
    Multi-strategy portfolio backtest.

    Runs multiple strategies simultaneously. Each bar, all strategies are evaluated.
    Up to PORTFOLIO_CONCURRENT_MAX trades can be open at once (across all strategies).
    Portfolio DD gate halts all new entries if combined DD >= PORTFOLIO_MAX_DD_PCT.

    SMC strategies receive pre-computed market structure, FVG, OB, regime, and
    calendar blackout via StrategyContext. These are computed once per bar (cached
    by session slot) and passed to all strategies.

    Args:
        bars: M15 Dukascopy OHLCV (UTC index)
        fred_df: bulk FRED data (daily)
        yfinance_df: bulk yfinance data (daily)
        cot_df: COT data (weekly)
        m1_bars: M1 bars (optional, for M1 execution strategies)
        h4_bars: H4 bars (optional, for regime + H4 trend filter)
        mode: "proxy" or "llm"
        strategy_ids: list of strategy_id strings to run simultaneously
        start_balance: starting account balance in USD
        wf_train_months: walk-forward training window
        wf_test_months: walk-forward test window
        holdout_year: if set, only backtest bars in this year (out-of-sample)

    Returns BacktestResult with all trades from all strategies combined.
    """
    from astra_v2.backtest.llm_proxy import proxy_macro_bias
    from astra_v2.core.market_structure import (
        detect_swing_highs_lows, detect_bos, detect_choch, MarketStructure,
    )
    from astra_v2.core.fair_value_gap import detect_fvgs, update_fvg_status, FairValueGap
    from astra_v2.core.order_block import detect_obs, update_ob_status, OrderBlock
    from astra_v2.core.regime_detector import detect_regime
    from astra_v2.data.calendar import is_blackout
    from astra_v2.data.external import get_yfinance_for_date as get_yfinance_row

    if strategy_ids is None:
        strategy_ids = [config.DEFAULT_STRATEGY_ID]

    strategies = [get_strategy(sid) for sid in strategy_ids]
    sym_primary = (primary_symbol or config.BACKTEST_PRIMARY_SYMBOL).strip().upper()

    # SMC strategies need H4 for regime — just warn if missing
    smc_ids = {"smc_fvg_v1", "smc_ob_v1"}
    has_smc = any(sid in smc_ids for sid in strategy_ids)
    if has_smc and h4_bars is None:
        logger.warning("SMC strategies requested but no H4 bars provided — regime will be None")

    bars.index = pd.to_datetime(bars.index, utc=True)
    bars = bars.sort_index()
    if m1_bars is not None:
        m1_bars = m1_bars.copy()
        m1_bars.index = pd.to_datetime(m1_bars.index, utc=True)
        m1_bars = m1_bars.sort_index()
    if h4_bars is not None:
        h4_bars = h4_bars.copy()
        h4_bars.index = pd.to_datetime(h4_bars.index, utc=True)
        h4_bars = h4_bars.sort_index()

    balance = start_balance
    peak_balance = start_balance  # high-water mark for portfolio DD gate
    equity_curve = [balance]
    all_trades: list[Trade] = []
    open_trades: list[Trade] = []
    pending_signals: list[PendingSignalState] = []

    # Walk-forward window
    test_start = bars.index[0] + pd.DateOffset(months=wf_train_months)
    test_bars = bars[bars.index >= test_start]

    if test_bars.empty:
        logger.warning(
            f"No test bars after {wf_train_months}-month warm-up. "
            "Returning empty result. Provide more data or reduce wf_train_months."
        )
        return BacktestResult(
            trades=[], equity_curve=[start_balance],
            start_balance=start_balance, end_balance=start_balance,
        )

    # Holdout filter: restrict to a single year
    if holdout_year is not None:
        test_bars = test_bars[test_bars.index.year == holdout_year]
        if test_bars.empty:
            logger.warning(f"No bars found for holdout year {holdout_year}")
            return BacktestResult(
                trades=[], equity_curve=[start_balance],
                start_balance=start_balance, end_balance=start_balance,
            )

    # Cache structures
    daily_macro_cache: dict = {}
    levels_cache: dict = {}
    daily_trade_count: dict[str, dict[str, int]] = {}  # {date: {strategy_id: count}}
    # SMC caches — keyed by session slot (same invalidation as levels_cache)
    ms_cache: dict = {}       # {date_slot: MarketStructure}
    fvg_cache: dict = {}      # {date_slot: list[FairValueGap]}
    ob_cache: dict = {}       # {date_slot: list[OrderBlock]}
    regime_cache: dict = {}   # {date_str: str | None}

    start_idx = int(bars.index.searchsorted(test_start))
    if holdout_year is not None:
        start_idx = int(bars.index.searchsorted(test_bars.index[0]))

    logger.info(
        f"Portfolio backtest: {len(test_bars)} test bars, mode={mode}, "
        f"strategies={strategy_ids}, balance=${start_balance:,.0f}"
        + (f", holdout={holdout_year}" if holdout_year else "")
    )

    # ── Instrumentation counters (debug bar-filter funnel) ─────────────────
    _dbg_total = 0
    _dbg_session = 0
    _dbg_portfolio_dd = 0
    _dbg_concurrent = 0
    _dbg_macro_ok = 0
    _dbg_macro_fail = 0
    _dbg_strategy_call = 0

    for offset in range(len(test_bars)):
        ts = test_bars.index[offset]
        bar = test_bars.iloc[offset]
        now: datetime = ts.to_pydatetime()
        interval_end = (ts + pd.Timedelta(minutes=15)).to_pydatetime()
        date_str = now.strftime("%Y-%m-%d")

        # bars_so_far = all M15 bars before current bar (anti-look-ahead)
        global_pos = int(bars.index.searchsorted(ts))
        bars_so_far = bars.iloc[:global_pos]
        cross_views = slice_m15_asof(cross_symbol_m15 or {}, ts)
        ctx_cross = cross_views if cross_views else None

        h4_so_far = None
        if h4_bars is not None:
            h4_cutoff = int(h4_bars.index.searchsorted(pd.Timestamp(interval_end)))
            h4_so_far = h4_bars.iloc[:h4_cutoff]

        m1_recent = None
        m1_interval = None
        if m1_bars is not None:
            interval_end_ts = pd.Timestamp(interval_end)
            m1_end_idx = int(m1_bars.index.searchsorted(interval_end_ts))
            m1_start_idx = int(m1_bars.index.searchsorted(ts))
            m1_recent_start_idx = int(
                m1_bars.index.searchsorted(interval_end_ts - pd.Timedelta(minutes=30))
            )
            m1_recent = m1_bars.iloc[m1_recent_start_idx:m1_end_idx]
            m1_interval = m1_bars.iloc[m1_start_idx:m1_end_idx]

        # ── Open pending signals at next bar ─────────────────────────────────
        new_pending: list[PendingSignalState] = []
        for ps in pending_signals:
            signal_age = (now - ps.queued_at).total_seconds() / 60
            if signal_age > 30:
                continue  # stale — discard

            # Portfolio concurrent gate
            if not can_open_trade(len(open_trades)):
                continue  # too many concurrent trades

            # Portfolio DD gate
            if check_portfolio_dd(open_trades, balance, peak_balance):
                continue  # portfolio DD exceeded — halt

            sig = ps.signal
            if sig.execution_timeframe == "M1":
                if m1_interval is not None and not m1_interval.empty:
                    trigger_price = sig.entry_trigger_price or sig.entry_price
                    if sig.direction == "BULLISH":
                        trigger_rows = m1_interval[m1_interval["high"] >= trigger_price]
                    else:
                        trigger_rows = m1_interval[m1_interval["low"] <= trigger_price]
                    if not trigger_rows.empty:
                        trigger_ts = trigger_rows.index[0].to_pydatetime()
                        entry_with_slippage = (
                            trigger_price + config.SLIPPAGE_USD
                            if sig.direction == "BULLISH"
                            else trigger_price - config.SLIPPAGE_USD
                        )
                        trade = _trade_from_signal(sig, entry_price=entry_with_slippage, opened_at=trigger_ts)
                        open_trades.append(trade)
                        # Manage on M1 within this interval
                        for minute_ts, minute_bar in m1_interval[m1_interval.index >= pd.Timestamp(trigger_ts)].iterrows():
                            _simulate_trade(trade, minute_bar, bars_so_far)
                            if trade.status != "open":
                                balance, trade = _finalize_closed_trade(trade, balance=balance, closed_at=minute_ts.to_pydatetime())
                                equity_curve.append(balance)
                                all_trades.append(trade)
                                open_trades.remove(trade)
                                break
            else:
                entry_price = float(bar["open"])
                entry_with_slippage = (
                    entry_price + config.SLIPPAGE_USD if sig.direction == "BULLISH"
                    else entry_price - config.SLIPPAGE_USD
                )
                gap_invalid = (
                    sig.direction == "BULLISH" and entry_with_slippage <= sig.stop_loss
                ) or (
                    sig.direction == "BEARISH" and entry_with_slippage >= sig.stop_loss
                )
                if not gap_invalid:
                    trade = _trade_from_signal(sig, entry_price=entry_with_slippage, opened_at=now)
                    open_trades.append(trade)
                    logger.debug(
                        f"{now} | ENTER {sig.direction} @ {entry_with_slippage:.2f} "
                        f"[{sig.strategy_id}] SL={sig.stop_loss:.2f} TP={sig.take_profit:.2f}"
                    )

        pending_signals = new_pending  # all consumed

        # ── Manage all open trades ────────────────────────────────────────────
        still_open: list[Trade] = []
        for trade in open_trades:
            if trade.status != "open":
                balance, trade = _finalize_closed_trade(trade, balance=balance, closed_at=now)
                equity_curve.append(balance)
                all_trades.append(trade)
                continue

            if trade.execution_timeframe == "M1" and m1_interval is not None and not m1_interval.empty:
                minute_slice = m1_interval[m1_interval.index > pd.Timestamp(trade.opened_at)]
                for minute_ts, minute_bar in minute_slice.iterrows():
                    _simulate_trade(trade, minute_bar, bars_so_far)
                    if trade.status != "open":
                        balance, trade = _finalize_closed_trade(trade, balance=balance, closed_at=minute_ts.to_pydatetime())
                        equity_curve.append(balance)
                        all_trades.append(trade)
                        break
                if trade.status != "open":
                    continue
            else:
                _simulate_trade(trade, bar, bars_so_far)

            if trade.status != "open":
                balance, trade = _finalize_closed_trade(trade, balance=balance, closed_at=now)
                equity_curve.append(balance)
                all_trades.append(trade)
                logger.debug(f"Trade closed [{trade.strategy_id}]: {trade.dollar_pnl:+.2f} | Balance: {balance:.2f}")
                continue

            if _should_force_intraday_close(trade, now):
                trade.exit_price = float(bar["close"])
                sl_dist = trade.initial_risk_usd if trade.initial_risk_usd > 0 else abs(trade.entry - trade.stop_loss)
                if sl_dist > 0:
                    exit_p = trade.exit_price
                    entry = trade.entry
                    pnl_per_unit = (exit_p - entry) if trade.direction == "BULLISH" else (entry - exit_p)
                    risk_usd = balance * config.RISK_PCT * trade.size_multiplier
                    position_units = risk_usd / sl_dist
                    trade.pnl += pnl_per_unit * (0.5 if trade.partial_closed else 1.0)
                    trade.dollar_pnl = trade.pnl * position_units
                    balance += trade.dollar_pnl
                trade.status = "forced"
                trade.intraday_forced_exit = True
                trade.closed_at = now
                equity_curve.append(balance)
                all_trades.append(trade)
                continue

            still_open.append(trade)

        open_trades = still_open
        equity_curve.append(balance)
        if balance > peak_balance:
            peak_balance = balance  # update high-water mark

        _dbg_total += 1

        # ── Only look for new signals during active sessions ──────────────────
        if not is_active_session(now):
            continue

        _dbg_session += 1

        # ── Portfolio DD gate ─────────────────────────────────────────────────
        if check_portfolio_dd(open_trades, balance, peak_balance):
            _dbg_portfolio_dd += 1
            continue

        # ── Concurrent trade limit ────────────────────────────────────────────
        if not can_open_trade(len(open_trades)):
            _dbg_concurrent += 1
            continue

        # ── Macro bias (once per day) ──────────────────────────────────────────
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
                _dbg_macro_fail += 1
                continue
            daily_macro_cache[date_str] = macro
        macro = daily_macro_cache[date_str]
        _dbg_macro_ok += 1

        # ── Key levels (cached per session slot) ──────────────────────────────
        slot = _levels_slot(now.hour)
        levels_key = f"{date_str}_{slot}"
        if levels_key not in levels_cache:
            levels_cache[levels_key] = extract_levels(
                bars_so_far, float(bar["close"]), now,
            )
        levels = levels_cache[levels_key]
        current_price = float(bar["close"])

        # ── SMC context (cached per session slot) ─────────────────────────────
        ms_obj = None
        fvg_list: list = []
        ob_list: list = []
        regime = None
        calendar_blackout = False
        dxy_trend = None

        if has_smc:
            # ATR for SMC (computed once per bar from bars_so_far)
            atr = None
            if len(bars_so_far) >= 21:
                import numpy as _np
                recent_bars = bars_so_far.tail(21)
                h = recent_bars["high"].astype(float).values
                l = recent_bars["low"].astype(float).values
                c = recent_bars["close"].astype(float).values
                pc = _np.roll(c, 1); pc[0] = c[0]
                tr = _np.maximum(h - l, _np.maximum(_np.abs(h - pc), _np.abs(l - pc)))
                atr = float(tr[-20:].mean())

            # Market Structure
            # Cache only swings (expensive, stable per slot).
            # BOS/CHoCH must be recomputed each bar with the actual current_bar.
            if levels_key not in ms_cache:
                ms_cache[levels_key] = detect_swing_highs_lows(bars_so_far)
            swings = ms_cache[levels_key]
            bos = detect_bos(swings, bar, atr=atr)
            choch = detect_choch(swings, bar, atr=atr)
            # Infer trend from swings
            _highs = [s for s in swings if s.swing_type == "high"]
            _lows  = [s for s in swings if s.swing_type == "low"]
            _trend = "RANGING"
            if len(_highs) >= 2 and len(_lows) >= 2:
                if _highs[-1].price > _highs[-2].price and _lows[-1].price > _lows[-2].price:
                    _trend = "BULLISH"
                elif _highs[-1].price < _highs[-2].price and _lows[-1].price < _lows[-2].price:
                    _trend = "BEARISH"
            ms_obj = MarketStructure(
                swings=swings, last_bos=bos, last_choch=choch, trend=_trend
            )

            # FVGs
            if levels_key not in fvg_cache:
                fvg_cache[levels_key] = detect_fvgs(bars_so_far, atr=atr)
            fvg_list = fvg_cache[levels_key]
            # NOTE: update_fvg_status is called AFTER strategy signals (below)
            # so the strategy can react to price entering an FVG on this bar.

            # Order Blocks
            if levels_key not in ob_cache:
                ob_cache[levels_key] = detect_obs(bars_so_far, atr=atr)
            ob_list = ob_cache[levels_key]
            # NOTE: update_ob_status is called AFTER strategy signals (below)

            # Regime (once per day, uses H4)
            if date_str not in regime_cache:
                regime_cache[date_str] = detect_regime(h4_so_far) if h4_so_far is not None else None
            regime = regime_cache[date_str]

            # Calendar blackout
            calendar_blackout = is_blackout(now)

            # DXY trend (from yfinance, daily)
            try:
                yf_row = get_yfinance_row(now, yfinance_df)
                if yf_row and "dxy" in yf_row and yf_row["dxy"] is not None:
                    # Compare DXY today vs 5-day ago
                    yf_prev = get_yfinance_row(
                        now - timedelta(days=5),
                        yfinance_df,
                    )
                    if yf_prev and "dxy" in yf_prev and yf_prev["dxy"] is not None:
                        dxy_now  = float(yf_row["dxy"])
                        dxy_prev = float(yf_prev["dxy"])
                        if dxy_now > dxy_prev * 1.001:
                            dxy_trend = "RISING"
                        elif dxy_now < dxy_prev * 0.999:
                            dxy_trend = "FALLING"
            except Exception:
                pass

        # ── Run each strategy ─────────────────────────────────────────────────
        _dbg_strategy_call += 1
        for strategy in strategies:
            sid = strategy.strategy_id
            if date_str not in daily_trade_count:
                daily_trade_count[date_str] = {}
            today_count = daily_trade_count[date_str].get(sid, 0)

            # Build context
            ctx = StrategyContext(
                strategy_id=sid,  # type: ignore[arg-type]
                now=now,
                current_price=current_price,
                current_bar=bar,
                bars_so_far=bars_so_far,
                levels=levels,
                macro=macro,
                local_trade_count=today_count,
                bar_end=interval_end,
                h4_bars=h4_so_far,
                m1_bars=m1_recent,
                market_structure=ms_obj,
                fvgs=fvg_list,
                order_blocks=ob_list,
                regime=regime,
                calendar_blackout=calendar_blackout,
                dxy_trend=dxy_trend,
                primary_symbol=sym_primary,
                cross_symbol_m15=ctx_cross,
            )

            signal, reason = strategy.generate_signal(ctx, supabase_client=None)
            if signal is None:
                continue
            if cross_views:
                signal = apply_cross_asset_to_signal(signal, cross_views)
            if signal is None:
                continue

            # Apply portfolio size multiplier
            n_open = len(open_trades)
            portfolio_mult = get_size_multiplier(n_open, signal.size_multiplier)
            signal.size_multiplier = portfolio_mult

            # Queue entry at next bar open
            pending_signals.append(PendingSignalState(signal=signal, queued_at=now))
            daily_trade_count[date_str][sid] = today_count + 1
            logger.debug(
                f"{now} | SIGNAL {signal.direction} [{sid}] @ {current_price:.2f} → queued"
            )

            # Only allow one signal per bar across all strategies
            # (prevents opening 2 trades on the same bar when already at max-1)
            if len(open_trades) + len(pending_signals) >= config.PORTFOLIO_CONCURRENT_MAX:
                break

        # Update FVG/OB status AFTER strategy signals so strategies can react
        # to price entering an FVG/OB zone on the current bar.
        if has_smc:
            update_fvg_status(fvg_list, bar, global_pos)
            update_ob_status(ob_list, bar, global_pos)

    # Force-close all remaining open trades at end of backtest
    pending_signals = []
    last_bar = test_bars.iloc[-1]
    for trade in open_trades:
        if trade.status == "open":
            trade.exit_price = float(last_bar["close"])
            sl_dist = trade.initial_risk_usd if trade.initial_risk_usd > 0 else abs(trade.entry - trade.stop_loss)
            if sl_dist > 0:
                exit_p = trade.exit_price
                entry = trade.entry
                pnl_per_unit = (exit_p - entry) if trade.direction == "BULLISH" else (entry - exit_p)
                risk_usd = balance * config.RISK_PCT * trade.size_multiplier
                position_units = risk_usd / sl_dist
                balance += pnl_per_unit * position_units
            trade.status = "forced"
            trade.closed_at = test_bars.index[-1].to_pydatetime()
            all_trades.append(trade)

    # ── Bar-filter funnel summary ──────────────────────────────────────────
    logger.info(
        f"BAR FUNNEL: total={_dbg_total} | session_pass={_dbg_session} "
        f"| portDD_skip={_dbg_portfolio_dd} | concurrent_skip={_dbg_concurrent} "
        f"| macro_ok={_dbg_macro_ok} | macro_fail={_dbg_macro_fail} "
        f"| strategy_calls={_dbg_strategy_call}"
    )

    return BacktestResult(
        trades=all_trades,
        equity_curve=equity_curve,
        start_balance=start_balance,
        end_balance=balance,
    )
