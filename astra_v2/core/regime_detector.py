"""
Regime Detector — H4-based Market Regime Classification
=========================================================
Classifies the current market regime using H4 bars only.
Regime changes are macro-level phenomena — M15 noise is irrelevant.

Regimes:
  TRENDING:     ADX > REGIME_ADX_TRENDING (25) → directional, trend-following strategies
  ACCUMULATION: ADX < REGIME_ADX_WEAK (15) and ATR normal → range/mean-reversion strategies
  DISTRIBUTION: ADX < REGIME_ADX_WEAK (15) but price near multi-day high → potential reversal
  VOLATILE:     ATR > REGIME_ATR_VOLATILE (2.5x) 20-bar avg → avoid or reduce size

ADX uses Wilder's smoothing (standard). ATR uses Wilder's ATR.

Returns None if < REGIME_MIN_H4_BARS available.
"""

from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd

from astra_v2 import config

MarketRegime = str  # "TRENDING" | "ACCUMULATION" | "DISTRIBUTION" | "VOLATILE"


def _compute_adx(bars: pd.DataFrame, period: int = 14) -> Optional[float]:
    """
    Compute ADX (Average Directional Index) using Wilder smoothing.
    Returns None if insufficient data.
    """
    n = len(bars)
    if n < period * 2 + 1:
        return None

    high  = bars["high"].astype(float).values
    low   = bars["low"].astype(float).values
    close = bars["close"].astype(float).values

    # True Range
    prev_close = np.roll(close, 1)
    prev_close[0] = close[0]
    tr = np.maximum(high - low,
         np.maximum(np.abs(high - prev_close), np.abs(low - prev_close)))

    # Directional Movement
    up_move   = high[1:] - high[:-1]
    down_move = low[:-1] - low[1:]

    plus_dm  = np.where((up_move > down_move) & (up_move > 0),   up_move,   0.0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)

    tr_arr     = tr[1:]
    plus_dm    = plus_dm
    minus_dm   = minus_dm

    # Wilder's Smoothed Moving Average (RMA): init = mean of first p bars,
    # then: rma[i] = rma[i-1] * (p-1)/p + arr[i] / p
    def wilder_rma(arr: np.ndarray, p: int) -> np.ndarray:
        result = np.zeros(len(arr))
        result[p - 1] = arr[:p].mean()
        for i in range(p, len(arr)):
            result[i] = result[i - 1] * (p - 1) / p + arr[i] / p
        return result

    sm_tr  = wilder_rma(tr_arr, period)
    sm_pdm = wilder_rma(plus_dm, period)
    sm_mdm = wilder_rma(minus_dm, period)

    # Avoid division by zero
    with np.errstate(divide="ignore", invalid="ignore"):
        pdi = np.where(sm_tr > 0, 100 * sm_pdm / sm_tr, 0.0)
        mdi = np.where(sm_tr > 0, 100 * sm_mdm / sm_tr, 0.0)
        dx  = np.where((pdi + mdi) > 0, 100 * np.abs(pdi - mdi) / (pdi + mdi), 0.0)

    # ADX = Wilder RMA of DX
    adx = wilder_rma(dx, period)
    # Return last value (skip the first period warm-up bars)
    valid = adx[period - 1:]
    return float(valid[-1]) if len(valid) > 0 else None


def _compute_atr(bars: pd.DataFrame, period: int = 20) -> tuple[Optional[float], Optional[float]]:
    """
    Compute current ATR and its rolling mean over `period` bars.
    Returns (current_atr, rolling_mean_atr) or (None, None) if insufficient data.
    """
    n = len(bars)
    if n < period + 1:
        return None, None

    high  = bars["high"].astype(float).values
    low   = bars["low"].astype(float).values
    close = bars["close"].astype(float).values

    prev_close = np.roll(close, 1)
    prev_close[0] = close[0]
    tr = np.maximum(high - low,
         np.maximum(np.abs(high - prev_close), np.abs(low - prev_close)))

    recent_tr = tr[-period:]
    current_atr = float(tr[-1])
    rolling_mean = float(recent_tr.mean())
    return current_atr, rolling_mean


def detect_regime(h4_bars: pd.DataFrame) -> Optional[MarketRegime]:
    """
    Classify current market regime from H4 bars.

    Args:
        h4_bars: H4 OHLCV bars up to (but not including) current bar.
                 Must have 'high', 'low', 'close' columns.

    Returns:
        "TRENDING" | "ACCUMULATION" | "DISTRIBUTION" | "VOLATILE" | None
        Returns None if insufficient data (< REGIME_MIN_H4_BARS).
    """
    if h4_bars is None or len(h4_bars) < config.REGIME_MIN_H4_BARS:
        return None

    adx = _compute_adx(h4_bars, period=14)
    current_atr, mean_atr = _compute_atr(h4_bars, period=20)

    # VOLATILE takes priority — high ATR means skip or reduce size regardless of ADX
    if current_atr is not None and mean_atr is not None and mean_atr > 0:
        if current_atr > mean_atr * config.REGIME_ATR_VOLATILE:
            return "VOLATILE"

    if adx is None:
        return None

    if adx >= config.REGIME_ADX_TRENDING:
        return "TRENDING"

    if adx < config.REGIME_ADX_WEAK:
        # Distinguish ACCUMULATION (building up) from DISTRIBUTION (topping/bottoming)
        # Use position of close within 20-bar range as a proxy:
        # close near top of range → DISTRIBUTION, near bottom → ACCUMULATION
        recent = h4_bars.tail(20)
        period_high = float(recent["high"].max())
        period_low  = float(recent["low"].min())
        rng = period_high - period_low
        if rng < 1e-9:
            return "ACCUMULATION"
        last_close = float(h4_bars["close"].iloc[-1])
        pos = (last_close - period_low) / rng
        if pos > 0.7:
            return "DISTRIBUTION"
        return "ACCUMULATION"

    # ADX between REGIME_ADX_WEAK and REGIME_ADX_TRENDING → mixed regime
    # Treat as ACCUMULATION (lower volatility strategies preferred)
    return "ACCUMULATION"
