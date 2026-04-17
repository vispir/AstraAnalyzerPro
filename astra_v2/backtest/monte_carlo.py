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
DD_THRESHOLD_PCT = 5.0   # report probability of breaching this


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

    # Probability of blowing past DD threshold
    prob_exceed_dd_threshold: float = 0.0
    dd_threshold_pct: float = DD_THRESHOLD_PCT

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
            "prob_exceed_dd_5pct": round(self.prob_exceed_dd_threshold * 100, 1),
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


def run_monte_carlo(
    pnl_list: list[float],
    start_balance: float = 10_000.0,
    n_runs: int = N_RUNS,
    seed: Optional[int] = None,
) -> MonteCarloResult:
    """
    Run Monte Carlo simulation by randomly shuffling trade order N times.

    Args:
        pnl_list: list of closed trade PnL in USD (from BacktestResult)
        start_balance: starting account balance
        n_runs: number of simulations (default 10,000)
        seed: optional random seed for reproducibility

    Returns MonteCarloResult with percentile statistics.
    """
    if len(pnl_list) < 5:
        logger.warning(f"Monte Carlo: only {len(pnl_list)} trades — results not meaningful")

    pnl = np.array(pnl_list, dtype=float)
    rng = np.random.default_rng(seed)

    final_balances = np.empty(n_runs)
    max_dds = np.empty(n_runs)
    profit_factors = np.empty(n_runs)

    logger.info(f"Monte Carlo: {n_runs} runs, {len(pnl)} trades, balance={start_balance:,.0f}")

    for i in range(n_runs):
        # Bootstrap resample (with replacement) so final balance and PF vary across runs.
        # Pure permutation keeps sum(pnl) constant — all percentiles collapse to one value.
        sampled = rng.choice(pnl, size=len(pnl), replace=True)
        final_bal, max_dd = _compute_equity_stats(sampled, start_balance)
        pf = _compute_profit_factor(sampled)

        final_balances[i] = final_bal
        max_dds[i] = max_dd
        profit_factors[i] = pf

    exceed_dd = np.mean(max_dds >= DD_THRESHOLD_PCT)

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
        prob_exceed_dd_threshold=float(exceed_dd),
        dd_threshold_pct=DD_THRESHOLD_PCT,
        pf_p5=float(np.percentile(profit_factors[np.isfinite(profit_factors)], 5)) if np.any(np.isfinite(profit_factors)) else 0.0,
        pf_p50=float(np.percentile(profit_factors[np.isfinite(profit_factors)], 50)) if np.any(np.isfinite(profit_factors)) else 0.0,
        pf_p95=float(np.percentile(profit_factors[np.isfinite(profit_factors)], 95)) if np.any(np.isfinite(profit_factors)) else 0.0,
    )
