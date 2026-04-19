"""
Monte Carlo simulation for backtest confidence intervals.

Takes a list of closed trade PnL values (in USD) and runs N_RUNS simulations
by randomly shuffling trade order to show the range of possible outcomes.

Produces:
  - 5th / 50th / 95th percentile equity curves
  - Distribution of final balances
  - Probability of reaching max DD threshold
  - Confidence interval for Profit Factor

Why Monte Carlo matters:
  A backtest with 60 trades might show PF=1.8 and MaxDD=3.5%.
  But the order of those trades was fixed by history — good luck or bad luck
  in the sequence. MC shows: "in 10,000 different orderings of those same trades,
  what's the 5th-percentile outcome?" That's your real floor.

Usage:
    from astra_v2.backtest.monte_carlo import run_monte_carlo
    result = run_monte_carlo(pnl_list, start_balance=10_000)
    print(result.summary())
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)

N_RUNS = 10_000
DD_THRESHOLD_PCT = 10.0   # report probability of breaching this (total DD)
DAILY_DD_THRESHOLD_PCT = 5.0  # daily DD threshold


@dataclass
class MonteCarloResult:
    pnl_list: list[float]
    start_balance: float
    n_runs: int

    # Percentile final balances
    p5_balance: float = 0.0
    p50_balance: float = 0.0
    p95_balance: float = 0.0

    # Percentile max drawdowns
    p5_max_dd: float = 0.0
    p50_max_dd: float = 0.0
    p95_max_dd: float = 0.0

    # Daily drawdown percentiles
    p5_daily_dd: float = 0.0
    p50_daily_dd: float = 0.0
    p95_daily_dd: float = 0.0

    # Probability of blowing past DD threshold
    prob_exceed_dd_threshold: float = 0.0
    prob_exceed_daily_dd_threshold: float = 0.0
    dd_threshold_pct: float = DD_THRESHOLD_PCT
    daily_dd_threshold_pct: float = DAILY_DD_THRESHOLD_PCT

    # Profit Factor distribution
    pf_p5: float = 0.0
    pf_p50: float = 0.0
    pf_p95: float = 0.0

    def summary(self) -> dict:
        return {
            "n_runs": self.n_runs,
            "n_trades": len(self.pnl_list),
            "final_balance": {
                "p5": round(self.p5_balance, 2),
                "p50": round(self.p50_balance, 2),
                "p95": round(self.p95_balance, 2),
            },
            "max_drawdown_pct": {
                "p5": round(self.p5_max_dd, 2),
                "p50": round(self.p50_max_dd, 2),
                "p95": round(self.p95_max_dd, 2),
            },
            "max_daily_drawdown_pct": {
                "p5": round(self.p5_daily_dd, 2),
                "p50": round(self.p50_daily_dd, 2),
                "p95": round(self.p95_daily_dd, 2),
            },
            "prob_exceed_dd_10pct": round(self.prob_exceed_dd_threshold * 100, 1),
            "prob_exceed_daily_dd_5pct": round(self.prob_exceed_daily_dd_threshold * 100, 1),
            "profit_factor": {
                "p5": round(self.pf_p5, 3),
                "p50": round(self.pf_p50, 3),
                "p95": round(self.pf_p95, 3),
            },
        }


def _compute_equity_stats(pnl_array: np.ndarray, start_balance: float) -> tuple[float, float]:
    """
    Given a sequence of PnL values, compute final balance and max drawdown %.
    Returns (final_balance, max_dd_pct).
    """
    equity = np.cumsum(pnl_array) + start_balance
    equity = np.insert(equity, 0, start_balance)

    peak = np.maximum.accumulate(equity)
    drawdowns = (peak - equity) / peak * 100
    max_dd = float(drawdowns.max())
    final = float(equity[-1])
    return final, max_dd


def _compute_profit_factor(pnl_array: np.ndarray) -> float:
    wins = pnl_array[pnl_array > 0]
    losses = pnl_array[pnl_array < 0]
    gross_profit = float(wins.sum()) if len(wins) > 0 else 0.0
    gross_loss = float(abs(losses.sum())) if len(losses) > 0 else 0.0
    if gross_loss == 0:
        return float("inf") if gross_profit > 0 else 0.0
    return gross_profit / gross_loss


def _compute_max_daily_dd(pnl_array: np.ndarray, trade_dates: np.ndarray, start_balance: float) -> float:
    """
    Compute maximum daily drawdown %.
    Daily DD = worst drawdown from day's starting balance to any point during the day.
    """
    if len(trade_dates) == 0:
        return 0.0

    # Build full equity curve
    equity = np.cumsum(pnl_array) + start_balance
    equity_with_start = np.insert(equity, 0, start_balance)

    # Group by date
    unique_dates = np.unique(trade_dates)
    max_daily_dd = 0.0

    prev_day_close = start_balance

    for date in unique_dates:
        # Find all trades of this day
        mask = trade_dates == date
        day_indices = np.where(mask)[0]

        if len(day_indices) == 0:
            continue

        # Day starts at prev_day_close
        day_start_balance = prev_day_close
        day_low = day_start_balance

        # Track lowest equity during the day
        for idx in day_indices:
            current_equity = equity_with_start[idx + 1]  # +1 because we prepended start_balance
            day_low = min(day_low, current_equity)

        # Calculate DD from day start to day low
        dd = (day_start_balance - day_low) / day_start_balance * 100 if day_start_balance > 0 else 0.0
        max_daily_dd = max(max_daily_dd, dd)

        # Update for next day
        prev_day_close = equity_with_start[day_indices[-1] + 1]

    return max_daily_dd


def _build_daily_pnl(pnl_array: np.ndarray, trade_dates: np.ndarray) -> np.ndarray:
    """Aggregate trade PnL into daily buckets, preserving intra-day structure as a sum."""
    unique_dates = np.unique(trade_dates)
    daily_pnl = np.array([pnl_array[trade_dates == d].sum() for d in unique_dates])
    return daily_pnl


def _compute_max_daily_dd_from_daily(daily_pnl: np.ndarray, start_balance: float) -> float:
    """
    Compute max daily drawdown from daily PnL buckets.
    Daily DD = (day_start_equity - day_end_equity) / day_start_equity * 100, floored at 0.
    """
    equity = start_balance
    max_dd = 0.0
    for dpnl in daily_pnl:
        if dpnl < 0:
            dd = -dpnl / equity * 100 if equity > 0 else 0.0
            max_dd = max(max_dd, dd)
        equity += dpnl
    return max_dd


def run_monte_carlo(
    pnl_list: list[float],
    start_balance: float = 10_000.0,
    n_runs: int = N_RUNS,
    seed: Optional[int] = None,
    trade_dates: Optional[list] = None,
) -> MonteCarloResult:
    """
    Run Monte Carlo simulation by randomly shuffling trade order N times.

    For total DD: bootstrap resample individual trades (with replacement).
    For daily DD: shuffle daily PnL buckets (preserves intra-day coherence).

    Args:
        pnl_list: list of closed trade PnL in USD (from BacktestResult)
        start_balance: starting account balance
        n_runs: number of simulations (default 10,000)
        seed: optional random seed for reproducibility
        trade_dates: optional list of trade close dates (for daily DD calculation)

    Returns MonteCarloResult with percentile statistics.
    """
    if len(pnl_list) < 5:
        logger.warning(f"Monte Carlo: only {len(pnl_list)} trades — results not meaningful")

    pnl = np.array(pnl_list, dtype=float)
    dates = np.array(trade_dates) if trade_dates is not None else None
    rng = np.random.default_rng(seed)

    # Pre-compute daily PnL buckets for daily DD simulation
    daily_pnl_buckets = None
    if dates is not None:
        daily_pnl_buckets = _build_daily_pnl(pnl, dates)

    final_balances = np.empty(n_runs)
    max_dds = np.empty(n_runs)
    max_daily_dds = np.empty(n_runs)
    profit_factors = np.empty(n_runs)

    logger.info(f"Monte Carlo: {n_runs} runs, {len(pnl)} trades, balance={start_balance:,.0f}")

    for i in range(n_runs):
        # Bootstrap resample individual trades (with replacement) for total DD / PF / balance
        indices = rng.choice(len(pnl), size=len(pnl), replace=True)
        sampled_pnl = pnl[indices]

        final_bal, max_dd = _compute_equity_stats(sampled_pnl, start_balance)
        pf = _compute_profit_factor(sampled_pnl)

        # Daily DD: shuffle daily buckets (preserves intra-day coherence)
        if daily_pnl_buckets is not None:
            shuffled_daily = daily_pnl_buckets.copy()
            rng.shuffle(shuffled_daily)
            max_daily_dd = _compute_max_daily_dd_from_daily(shuffled_daily, start_balance)
        else:
            max_daily_dd = 0.0

        final_balances[i] = final_bal
        max_dds[i] = max_dd
        max_daily_dds[i] = max_daily_dd
        profit_factors[i] = pf

    exceed_dd = np.mean(max_dds >= DD_THRESHOLD_PCT)
    exceed_daily_dd = np.mean(max_daily_dds >= DAILY_DD_THRESHOLD_PCT) if dates is not None else 0.0

    return MonteCarloResult(
        pnl_list=pnl_list,
        start_balance=start_balance,
        n_runs=n_runs,
        p5_balance=float(np.percentile(final_balances, 5)),
        p50_balance=float(np.percentile(final_balances, 50)),
        p95_balance=float(np.percentile(final_balances, 95)),
        p5_max_dd=float(np.percentile(max_dds, 5)),
        p50_max_dd=float(np.percentile(max_dds, 50)),
        p95_max_dd=float(np.percentile(max_dds, 95)),
        p5_daily_dd=float(np.percentile(max_daily_dds, 5)) if dates is not None else 0.0,
        p50_daily_dd=float(np.percentile(max_daily_dds, 50)) if dates is not None else 0.0,
        p95_daily_dd=float(np.percentile(max_daily_dds, 95)) if dates is not None else 0.0,
        prob_exceed_dd_threshold=float(exceed_dd),
        prob_exceed_daily_dd_threshold=float(exceed_daily_dd),
        dd_threshold_pct=DD_THRESHOLD_PCT,
        daily_dd_threshold_pct=DAILY_DD_THRESHOLD_PCT,
        pf_p5=float(np.percentile(profit_factors[np.isfinite(profit_factors)], 5)) if np.any(np.isfinite(profit_factors)) else 0.0,
        pf_p50=float(np.percentile(profit_factors[np.isfinite(profit_factors)], 50)) if np.any(np.isfinite(profit_factors)) else 0.0,
        pf_p95=float(np.percentile(profit_factors[np.isfinite(profit_factors)], 95)) if np.any(np.isfinite(profit_factors)) else 0.0,
    )
