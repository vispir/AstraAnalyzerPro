"""
Market Structure — BOS / CHoCH Detection
=========================================
Foundation for all SMC analysis. Detects swing highs/lows, Break of Structure,
and Change of Character from M15 OHLCV bars.

Key concepts:
  Swing High: bar[i].high > all bars in [i-lookback, i+lookback]
  Swing Low:  bar[i].low  < all bars in [i-lookback, i+lookback]
  BOS (Break of Structure): close beyond last confirmed swing → trend continuation
  CHoCH (Change of Character): close beyond last opposing swing → potential reversal

Anti-look-ahead: all functions receive bars_so_far (bars before current bar).
The current bar is never included in swing detection.

Data flow:
  bars_so_far → detect_swing_highs_lows() → [(ts, price, type)]
              → detect_bos(swings, current_bar) → BOS enum
              → detect_choch(swings, current_bar) → CHoCH enum | None
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional
import pandas as pd

from astra_v2 import config


# ── Types ──────────────────────────────────────────────────────────────────────

BOS_BULLISH = "BOS_BULLISH"
BOS_BEARISH = "BOS_BEARISH"
NO_BOS = "NO_BOS"

CHOCH_BULLISH = "CHoCH_BULLISH"
CHOCH_BEARISH = "CHoCH_BEARISH"


@dataclass
class Swing:
    timestamp: datetime
    price: float
    swing_type: str  # "high" or "low"


@dataclass
class MarketStructure:
    """Snapshot of market structure at a given bar."""
    swings: list[Swing] = field(default_factory=list)
    last_bos: Optional[str] = None        # BOS_BULLISH | BOS_BEARISH | NO_BOS
    last_choch: Optional[str] = None      # CHoCH_BULLISH | CHoCH_BEARISH | None
    trend: Optional[str] = None           # "BULLISH" | "BEARISH" | "RANGING"

    @property
    def last_swing_high(self) -> Optional[Swing]:
        highs = [s for s in self.swings if s.swing_type == "high"]
        return highs[-1] if highs else None

    @property
    def last_swing_low(self) -> Optional[Swing]:
        lows = [s for s in self.swings if s.swing_type == "low"]
        return lows[-1] if lows else None


# ── Swing Detection ────────────────────────────────────────────────────────────

def detect_swing_highs_lows(
    bars: pd.DataFrame,
    lookback: int = None,
) -> list[Swing]:
    """
    Detect swing highs and lows using a pivot-point approach.

    A swing high at index i: bars.high[i] > bars.high[i-lookback : i] and
                                             bars.high[i] > bars.high[i+1 : i+lookback+1]
    A swing low at index i:  bars.low[i]  < bars.low[i-lookback : i] and
                                             bars.low[i]  < bars.low[i+1 : i+lookback+1]

    Uses only confirmed pivots (right side already closed), so no look-ahead.
    The last `lookback` bars cannot produce confirmed swings — they are still
    forming their right side.

    Args:
        bars: OHLCV bars (bars_so_far — no current bar included)
        lookback: bars on each side to confirm swing. Defaults to MS_SWING_LOOKBACK.

    Returns:
        List of Swing objects sorted by timestamp ascending.
    """
    if lookback is None:
        lookback = config.MS_SWING_LOOKBACK

    if len(bars) < 2 * lookback + 1:
        return []

    highs = bars["high"].astype(float).values
    lows = bars["low"].astype(float).values
    n = len(bars)
    swings: list[Swing] = []

    # Only scan up to n-lookback: bars after that haven't confirmed their right side.
    for i in range(lookback, n - lookback):
        # Swing high: strictly greater than all neighbors on both sides
        left_h = highs[i - lookback: i]
        right_h = highs[i + 1: i + lookback + 1]
        if highs[i] > max(left_h) and highs[i] > max(right_h):
            swings.append(Swing(
                timestamp=bars.index[i].to_pydatetime(),
                price=float(highs[i]),
                swing_type="high",
            ))

        # Swing low: strictly less than all neighbors on both sides
        left_l = lows[i - lookback: i]
        right_l = lows[i + 1: i + lookback + 1]
        if lows[i] < min(left_l) and lows[i] < min(right_l):
            swings.append(Swing(
                timestamp=bars.index[i].to_pydatetime(),
                price=float(lows[i]),
                swing_type="low",
            ))

    swings.sort(key=lambda s: s.timestamp)
    return swings


# ── BOS Detection ─────────────────────────────────────────────────────────────

def detect_bos(
    swings: list[Swing],
    current_bar: pd.Series,
    atr: Optional[float] = None,
) -> str:
    """
    Detect a Break of Structure on the current bar.

    BOS_BULLISH: current close > last confirmed swing high (by at least MS_BOS_MIN_ATR)
    BOS_BEARISH: current close < last confirmed swing low  (by at least MS_BOS_MIN_ATR)

    Args:
        swings: confirmed swings from detect_swing_highs_lows()
        current_bar: the current M15 bar (not included in swings)
        atr: current ATR for minimum-move filter (None = skip ATR filter)

    Returns:
        BOS_BULLISH, BOS_BEARISH, or NO_BOS
    """
    if not swings:
        return NO_BOS

    c_close = float(current_bar["close"])
    min_move = (atr * config.MS_BOS_MIN_ATR) if atr and atr > 0 else 0.0

    highs = [s for s in swings if s.swing_type == "high"]
    lows  = [s for s in swings if s.swing_type == "low"]

    last_high = highs[-1].price if highs else None
    last_low  = lows[-1].price  if lows  else None

    if last_high is not None and c_close > last_high + min_move:
        return BOS_BULLISH
    if last_low is not None and c_close < last_low - min_move:
        return BOS_BEARISH
    return NO_BOS


# ── CHoCH Detection ───────────────────────────────────────────────────────────

def detect_choch(
    swings: list[Swing],
    current_bar: pd.Series,
    atr: Optional[float] = None,
) -> Optional[str]:
    """
    Detect a Change of Character (potential trend reversal) on the current bar.

    CHoCH occurs when price breaks the last swing that is OPPOSITE to the current trend:
      After BOS_BULLISH trend: CHoCH_BEARISH if close < last swing LOW
      After BOS_BEARISH trend: CHoCH_BULLISH if close > last swing HIGH

    Returns None if fewer than MS_SWING_LOOKBACK swings or pattern is ambiguous.

    Args:
        swings: confirmed swings (need at least MS_SWING_LOOKBACK to confirm)
        current_bar: the current M15 bar
        atr: current ATR for minimum-move filter

    Returns:
        CHOCH_BULLISH, CHOCH_BEARISH, or None
    """
    if len(swings) < config.MS_SWING_LOOKBACK:
        return None

    c_close = float(current_bar["close"])
    min_move = (atr * config.MS_BOS_MIN_ATR) if atr and atr > 0 else 0.0

    # Determine the dominant trend from the last 2 swing highs/lows
    highs = [s for s in swings if s.swing_type == "high"]
    lows  = [s for s in swings if s.swing_type == "low"]

    if len(highs) < 2 or len(lows) < 2:
        return None

    hh = highs[-1].price > highs[-2].price   # higher high = bullish
    hl = lows[-1].price  > lows[-2].price    # higher low  = bullish
    lh = highs[-1].price < highs[-2].price   # lower high  = bearish
    ll = lows[-1].price  < lows[-2].price    # lower low   = bearish

    is_bullish_trend = hh and hl
    is_bearish_trend = lh and ll

    if is_bullish_trend:
        # CHoCH: close breaks last Higher Low → structural shift to bearish
        if c_close < lows[-1].price - min_move:
            return CHOCH_BEARISH

    elif is_bearish_trend:
        # CHoCH: close breaks last Lower High → structural shift to bullish
        if c_close > highs[-1].price + min_move:
            return CHOCH_BULLISH

    return None


# ── Full Market Structure Snapshot ────────────────────────────────────────────

def build_market_structure(
    bars: pd.DataFrame,
    current_bar: pd.Series,
    atr: Optional[float] = None,
    lookback: int = None,
) -> MarketStructure:
    """
    Build a complete MarketStructure snapshot for the current bar.

    Args:
        bars: bars_so_far (no current bar — anti-look-ahead)
        current_bar: the bar being evaluated
        atr: current ATR (used for BOS/CHoCH minimum-move filter)
        lookback: swing detection lookback (defaults to config.MS_SWING_LOOKBACK)

    Returns:
        MarketStructure with swings, last_bos, last_choch, trend
    """
    swings = detect_swing_highs_lows(bars, lookback=lookback)
    bos = detect_bos(swings, current_bar, atr=atr)
    choch = detect_choch(swings, current_bar, atr=atr)

    # Infer trend from BOS + swing structure
    highs = [s for s in swings if s.swing_type == "high"]
    lows  = [s for s in swings if s.swing_type == "low"]
    trend = "RANGING"
    if len(highs) >= 2 and len(lows) >= 2:
        hh = highs[-1].price > highs[-2].price
        hl = lows[-1].price  > lows[-2].price
        lh = highs[-1].price < highs[-2].price
        ll = lows[-1].price  < lows[-2].price
        if hh and hl:
            trend = "BULLISH"
        elif lh and ll:
            trend = "BEARISH"

    return MarketStructure(
        swings=swings,
        last_bos=bos,
        last_choch=choch,
        trend=trend,
    )
