"""
Volume Engine — Order Flow Utilities
=====================================
Builds relative-volume (RVOL) metrics from Dukascopy tick-volume data.

Key concept: Same-Hour RVOL
  Raw RVOL (current / rolling-mean) is biased by intraday seasonality.
  NY session (13-14 UTC) is ~2x the 24h average, so every NY bar looks
  "high volume". Same-Hour RVOL normalises by comparing a bar only to
  other bars at the *same UTC hour*, giving a clean signal regardless of
  session time.

Usage:
  rvol = same_hour_rvol(bars_so_far, current_bar_time, lookback_days=20)
  breakout_rvol = bar_same_hour_rvol(breakout_bar_ts, bars_so_far, lookback_days=20)
"""
from __future__ import annotations

import pandas as pd


def same_hour_rvol(
    bars: pd.DataFrame,
    bar_time,
    lookback_days: int = 20,
) -> float:
    """
    Relative volume for ``bar_time`` vs historical average at the same UTC hour.

    Args:
        bars:         Full bars DataFrame with a 'volume' column and UTC index.
        bar_time:     Timestamp of the bar to evaluate (must be in bars.index).
        lookback_days: How many same-hour historical bars to average over.

    Returns:
        Ratio ≥ 0. 1.0 = exactly average. 2.0 = twice the norm. 0 if no data.
    """
    if "volume" not in bars.columns or len(bars) < 5:
        return 1.0

    hour = getattr(bar_time, "hour", None)
    if hour is None:
        return 1.0

    same_hour_bars = bars[bars.index.hour == hour]

    # Exclude the current bar itself from the average
    historical = same_hour_bars[same_hour_bars.index < bar_time]
    if len(historical) < 5:
        return 1.0

    avg = float(historical["volume"].tail(lookback_days).mean())
    if avg <= 1e-9:
        return 1.0

    # Get current bar volume
    if bar_time in bars.index:
        current_vol = float(bars.at[bar_time, "volume"])
    else:
        current_vol = float(bars.iloc[-1]["volume"])

    return current_vol / avg


def bar_same_hour_rvol(
    bar_time,
    bars: pd.DataFrame,
    lookback_days: int = 20,
) -> float:
    """
    Same-Hour RVOL for an arbitrary historical bar (e.g. a detected breakout bar).
    Looks back through ``bars`` at rows sharing the same UTC hour as ``bar_time``.
    """
    return same_hour_rvol(bars, bar_time, lookback_days=lookback_days)


def intraday_vwap(bars: pd.DataFrame, as_of: pd.Timestamp) -> float | None:
    """
    Compute intraday VWAP from midnight UTC up to (and including) ``as_of``.

    Returns None if there are fewer than 4 bars in the current day.
    """
    if "volume" not in bars.columns:
        return None

    day_start = pd.Timestamp(as_of.date(), tz="UTC")
    day_bars = bars[(bars.index >= day_start) & (bars.index <= as_of)]

    if len(day_bars) < 4:
        return None

    typical = (
        day_bars["high"].astype(float)
        + day_bars["low"].astype(float)
        + day_bars["close"].astype(float)
    ) / 3.0
    vol = day_bars["volume"].astype(float)
    total_vol = vol.sum()
    if total_vol <= 1e-9:
        return None
    return float((typical * vol).sum() / total_vol)
