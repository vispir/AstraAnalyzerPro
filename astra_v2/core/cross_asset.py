"""
Cross-asset helpers: align other pairs' M15 history with the primary bar clock
and optionally scale or veto signals from agreement of last closed M15 candles.
"""

from __future__ import annotations

import logging
from typing import Optional

import pandas as pd

from astra_v2 import config
from astra_v2.core.signal_gate import Signal

logger = logging.getLogger(__name__)


def slice_m15_asof(cross_full: dict[str, pd.DataFrame], ts: pd.Timestamp) -> dict[str, pd.DataFrame]:
    """For each symbol, return M15 rows strictly before *ts* (same anti-lookahead as primary bars_so_far)."""
    if not cross_full:
        return {}
    out: dict[str, pd.DataFrame] = {}
    for sym, cdf in cross_full.items():
        if cdf is None or cdf.empty:
            continue
        cdf = cdf.sort_index()
        cut = int(cdf.index.searchsorted(ts, side="left"))
        part = cdf.iloc[:cut]
        if not part.empty:
            out[sym] = part
    return out


def _last_bar_direction(row: pd.Series) -> Optional[str]:
    try:
        o = float(row["open"])
        c = float(row["close"])
    except (TypeError, ValueError, KeyError):
        return None
    return "BULLISH" if c >= o else "BEARISH"


def agreement_ratio(signal_direction: str, cross_views: dict[str, pd.DataFrame]) -> tuple[float, int, int]:
    """Return (agree_count / n_valid, agree_count, n_valid). n_valid==0 → ratio 1.0 for callers."""
    agree = 0
    n = 0
    for _sym, sli in cross_views.items():
        if sli is None or len(sli) < 1:
            continue
        d = _last_bar_direction(sli.iloc[-1])
        if d is None:
            continue
        n += 1
        if d == signal_direction:
            agree += 1
    if n == 0:
        return 1.0, 0, 0
    return agree / n, agree, n


def apply_cross_asset_to_signal(
    signal: Signal,
    cross_views: dict[str, pd.DataFrame],
) -> Optional[Signal]:
    """
    Apply config.CROSS_ASSET_MODE to *signal* using last closed M15 on each cross pair.

    Modes:
      none — no change
      size — scale signal.size_multiplier by agreement (weaker when peers disagree)
      gate — return None if agreement ratio < CROSS_ASSET_GATE_MIN_AGREE_RATIO
    """
    mode = getattr(config, "CROSS_ASSET_MODE", "none").lower()
    if mode == "none" or not cross_views:
        return signal

    ratio, agree, n = agreement_ratio(signal.direction, cross_views)
    if n == 0:
        return signal

    if mode == "gate":
        min_r = float(getattr(config, "CROSS_ASSET_GATE_MIN_AGREE_RATIO", 0.45))
        if ratio < min_r:
            logger.debug(
                f"Cross-asset gate: ratio={ratio:.2f} < {min_r} "
                f"({agree}/{n} agree with {signal.direction})"
            )
            return None
        return signal

    if mode == "size":
        # 0.55 … 1.0: full agreement keeps size; full disagreement still leaves 55% size
        mult = 0.55 + 0.45 * ratio
        signal.size_multiplier *= mult
        return signal

    return signal
