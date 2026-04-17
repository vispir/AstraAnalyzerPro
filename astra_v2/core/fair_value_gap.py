"""
Fair Value Gap (FVG) Detection + Tracking
==========================================
An FVG is a price imbalance zone created when a strong move leaves a gap between
bar[i-2] and bar[i]:

  Bullish FVG: bar[i].low > bar[i-2].high  → price moved up so fast it left a gap below
  Bearish FVG: bar[i].high < bar[i-2].low  → price moved down so fast it left a gap above

Price often returns to fill these gaps (institutional re-balancing). The edge:
enter when price re-enters the FVG in the direction of the impulse.

Anti-look-ahead: detect_fvgs() receives bars_so_far. The FVG formed by bar[i]
is only detectable after bar[i] closes — never the current bar.

FVG lifecycle:
  ACTIVE   → freshly formed, not yet filled
  FILLED   → price traded through >= 50% of the gap (FVG_ENTRY_DEPTH)
  EXPIRED  → age > FVG_EXPIRY_BARS bars without being filled
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

import pandas as pd

from astra_v2 import config


# ── FVG Status ─────────────────────────────────────────────────────────────────

FVG_ACTIVE  = "ACTIVE"
FVG_FILLED  = "FILLED"
FVG_EXPIRED = "EXPIRED"


@dataclass
class FairValueGap:
    direction: str          # "BULLISH" | "BEARISH"
    top: float              # upper boundary of the gap
    bottom: float           # lower boundary of the gap
    formed_at: datetime     # timestamp of bar[i] (the bar that created the gap)
    formed_bar_idx: int     # position of bar[i] in the bar series
    status: str = FVG_ACTIVE
    age_bars: int = 0       # bars since formation

    @property
    def midpoint(self) -> float:
        return (self.top + self.bottom) / 2.0

    @property
    def size(self) -> float:
        return self.top - self.bottom

    def entry_price(self) -> float:
        """Entry price at FVG_ENTRY_DEPTH (50%) into the gap from the trade direction."""
        if self.direction == "BULLISH":
            # Enter from below — price falling into gap, so entry at bottom + depth * size
            return self.bottom + config.FVG_ENTRY_DEPTH * self.size
        else:
            # Enter from above — price rising into gap, so entry at top - depth * size
            return self.top - config.FVG_ENTRY_DEPTH * self.size


# ── Detection ─────────────────────────────────────────────────────────────────

def detect_fvgs(
    bars: pd.DataFrame,
    atr: Optional[float] = None,
    min_size_atr: Optional[float] = None,
) -> list[FairValueGap]:
    """
    Detect all Fair Value Gaps in bars_so_far.

    Scans every triplet [i-2, i-1, i] in the bar series.
    Only includes FVGs >= FVG_MIN_SIZE_ATR * atr (filtered if atr is provided).

    Anti-look-ahead: bars = bars_so_far (current bar excluded).

    Args:
        bars: OHLCV bars_so_far
        atr: current ATR for minimum-size filter (None = no filter)
        min_size_atr: ATR multiplier threshold (defaults to config.FVG_MIN_SIZE_ATR)

    Returns:
        List of FairValueGap objects sorted by formation time ascending.
        Only ACTIVE FVGs (not filled/expired) are returned.
    """
    if min_size_atr is None:
        min_size_atr = config.FVG_MIN_SIZE_ATR

    if len(bars) < 3:
        return []

    min_size = (atr * min_size_atr) if atr and atr > 0 else 0.0

    highs  = bars["high"].astype(float).values
    lows   = bars["low"].astype(float).values
    n      = len(bars)
    fvgs: list[FairValueGap] = []

    # Scan all completed triplets — bar[i] is the most recent confirmed bar
    for i in range(2, n):
        bar_i_low   = lows[i]
        bar_i2_high = highs[i - 2]
        bar_i_high  = highs[i]
        bar_i2_low  = lows[i - 2]

        # Bullish FVG: gap between bar[i-2].high and bar[i].low
        if bar_i_low > bar_i2_high:
            gap_size = bar_i_low - bar_i2_high
            if gap_size >= min_size:
                fvgs.append(FairValueGap(
                    direction="BULLISH",
                    top=bar_i_low,
                    bottom=bar_i2_high,
                    formed_at=bars.index[i].to_pydatetime(),
                    formed_bar_idx=i,
                ))

        # Bearish FVG: gap between bar[i].high and bar[i-2].low
        elif bar_i_high < bar_i2_low:
            gap_size = bar_i2_low - bar_i_high
            if gap_size >= min_size:
                fvgs.append(FairValueGap(
                    direction="BEARISH",
                    top=bar_i2_low,
                    bottom=bar_i_high,
                    formed_at=bars.index[i].to_pydatetime(),
                    formed_bar_idx=i,
                ))

    return fvgs


# ── Status Update ──────────────────────────────────────────────────────────────

def update_fvg_status(
    fvgs: list[FairValueGap],
    current_bar: pd.Series,
    current_bar_idx: int,
    expiry_bars: Optional[int] = None,
    entry_depth: Optional[float] = None,
) -> list[FairValueGap]:
    """
    Update status of all FVGs against the current bar.

    Fills: price trades to at least entry_depth into the FVG.
      Bullish FVG: bar low <= bottom + entry_depth * size
      Bearish FVG: bar high >= top - entry_depth * size

    Expires: age > expiry_bars since formation.

    Args:
        fvgs: list of FairValueGap to update (mutates in place)
        current_bar: current M15 bar
        current_bar_idx: absolute bar index in the series
        expiry_bars: bars before expiry (defaults to config.FVG_EXPIRY_BARS)
        entry_depth: fill depth threshold (defaults to config.FVG_ENTRY_DEPTH)

    Returns:
        The same list with statuses updated (mutated in place, also returned).
    """
    if expiry_bars is None:
        expiry_bars = config.FVG_EXPIRY_BARS
    if entry_depth is None:
        entry_depth = config.FVG_ENTRY_DEPTH

    bar_high = float(current_bar["high"])
    bar_low  = float(current_bar["low"])

    for fvg in fvgs:
        if fvg.status != FVG_ACTIVE:
            continue

        fvg.age_bars = current_bar_idx - fvg.formed_bar_idx

        # Expiry check first
        if fvg.age_bars > expiry_bars:
            fvg.status = FVG_EXPIRED
            continue

        # Fill check: price must reach entry_depth into the gap
        fill_level = entry_depth * fvg.size
        if fvg.direction == "BULLISH":
            # Price pulled back into the gap from above
            if bar_low <= fvg.bottom + fill_level:
                fvg.status = FVG_FILLED
        else:
            # Price rallied back into the gap from below
            if bar_high >= fvg.top - fill_level:
                fvg.status = FVG_FILLED

    return fvgs


def get_active_fvgs(fvgs: list[FairValueGap]) -> list[FairValueGap]:
    """Return only ACTIVE FVGs, sorted newest first."""
    return sorted(
        [f for f in fvgs if f.status == FVG_ACTIVE],
        key=lambda f: f.formed_at,
        reverse=True,
    )
