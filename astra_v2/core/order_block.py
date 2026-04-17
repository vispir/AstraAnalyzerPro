"""
Order Block (OB) Detection + Validation
=========================================
An Order Block is the last candle before a strong impulse move — it marks where
institutional orders were placed (accumulation/distribution zone).

Rules:
  Bullish OB: last bearish candle before a strong bullish impulse of OB_IMPULSE_ATR
  Bearish OB: last bullish candle before a strong bearish impulse of OB_IMPULSE_ATR

An OB is VALID as long as:
  - Price has NOT traded through more than OB_MITIGATED_PCT (75%) of its range
  - Age < OB_MAX_AGE_BARS bars

Anti-look-ahead: detect_obs() receives bars_so_far. The impulse that confirms
an OB must have already fully closed — the current bar is never the impulse bar.

OB lifecycle:
  VALID      → fresh OB, entry zone intact
  MITIGATED  → price traded > 75% through the OB range (weakened)
  EXPIRED    → too old
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional

import numpy as np
import pandas as pd

from astra_v2 import config


# ── OB Status ─────────────────────────────────────────────────────────────────

OB_VALID     = "VALID"
OB_MITIGATED = "MITIGATED"
OB_EXPIRED   = "EXPIRED"


@dataclass
class OrderBlock:
    direction: str          # "BULLISH" | "BEARISH"
    top: float              # upper boundary of the OB candle range
    bottom: float           # lower boundary of the OB candle range
    formed_at: datetime     # timestamp of the OB candle itself
    formed_bar_idx: int     # position of OB candle in the bar series
    impulse_size_atr: float = 0.0  # size of the impulse that confirmed this OB
    status: str = OB_VALID
    age_bars: int = 0

    @property
    def midpoint(self) -> float:
        return (self.top + self.bottom) / 2.0

    @property
    def size(self) -> float:
        return self.top - self.bottom

    def entry_price(self) -> float:
        """Best entry: 50% of the OB range from the trading side."""
        return self.midpoint

    def stop_price(self, atr: float = 0.0) -> float:
        """SL beyond the OB edge + small buffer."""
        buf = atr * config.SMC_OB_V1_STOP_BUFFER_ATR if atr > 0 else 0.0
        if self.direction == "BULLISH":
            return self.bottom - buf   # SL below bottom
        else:
            return self.top + buf      # SL above top


# ── Detection ─────────────────────────────────────────────────────────────────

def _compute_atr(bars: pd.DataFrame, period: int = 20) -> Optional[float]:
    if len(bars) < period + 1:
        return None
    high  = bars["high"].astype(float).values
    low   = bars["low"].astype(float).values
    close = bars["close"].astype(float).values
    prev_close = np.roll(close, 1)
    prev_close[0] = close[0]
    tr = np.maximum(high - low,
         np.maximum(np.abs(high - prev_close), np.abs(low - prev_close)))
    return float(tr[-period:].mean())


def detect_obs(
    bars: pd.DataFrame,
    atr: Optional[float] = None,
    impulse_atr: Optional[float] = None,
    min_body_atr: Optional[float] = None,
) -> list[OrderBlock]:
    """
    Detect Order Blocks in bars_so_far.

    For each bar i, check if it is an OB candle:
    - Bullish OB: bars[i] is bearish (close < open), followed by an impulse
                  move up of >= impulse_atr * ATR in the next bars
    - Bearish OB: bars[i] is bullish (close > open), followed by an impulse
                  move down of >= impulse_atr * ATR

    The impulse is measured as the distance from bar[i].close to the extreme
    (high or low) of the next N bars where the move is uninterrupted.

    Args:
        bars: OHLCV bars_so_far (current bar excluded)
        atr: current ATR (computed internally if None)
        impulse_atr: min impulse in ATR to qualify (defaults to config.OB_IMPULSE_ATR)
        min_body_atr: min OB candle body in ATR (defaults to config.OB_MIN_BODY_ATR)

    Returns:
        List of OrderBlock objects with VALID status.
    """
    if impulse_atr is None:
        impulse_atr = config.OB_IMPULSE_ATR
    if min_body_atr is None:
        min_body_atr = config.OB_MIN_BODY_ATR

    if atr is None:
        atr = _compute_atr(bars)
    if atr is None or atr <= 0:
        return []

    min_impulse = atr * impulse_atr
    min_body    = atr * min_body_atr

    opens  = bars["open"].astype(float).values
    closes = bars["close"].astype(float).values
    highs  = bars["high"].astype(float).values
    lows   = bars["low"].astype(float).values
    n = len(bars)

    obs: list[OrderBlock] = []

    # Need at least 2 bars after the OB candle to confirm impulse
    for i in range(0, n - 2):
        body = abs(closes[i] - opens[i])
        if body < min_body:
            continue

        # Look at the next few bars for an impulse (max 5 bars)
        look_ahead = min(i + 6, n)
        future_highs = highs[i + 1: look_ahead]
        future_lows  = lows[i + 1:  look_ahead]

        # Bullish OB: bearish candle (close < open), then bullish impulse
        if closes[i] < opens[i]:
            if len(future_highs) == 0:
                continue
            impulse_up = max(future_highs) - closes[i]
            if impulse_up >= min_impulse:
                obs.append(OrderBlock(
                    direction="BULLISH",
                    top=max(opens[i], closes[i]),   # top of OB candle
                    bottom=min(opens[i], closes[i]), # body bottom (for bearish candle: close)
                    formed_at=bars.index[i].to_pydatetime(),
                    formed_bar_idx=i,
                    impulse_size_atr=impulse_up / atr,
                ))

        # Bearish OB: bullish candle (close > open), then bearish impulse
        elif closes[i] > opens[i]:
            if len(future_lows) == 0:
                continue
            impulse_down = closes[i] - min(future_lows)
            if impulse_down >= min_impulse:
                obs.append(OrderBlock(
                    direction="BEARISH",
                    top=max(opens[i], closes[i]),    # body top (for bullish: close)
                    bottom=min(opens[i], closes[i]), # bottom of OB candle
                    formed_at=bars.index[i].to_pydatetime(),
                    formed_bar_idx=i,
                    impulse_size_atr=impulse_down / atr,
                ))

    return obs


# ── Validation ────────────────────────────────────────────────────────────────

def update_ob_status(
    obs: list[OrderBlock],
    current_bar: pd.Series,
    current_bar_idx: int,
    mitigated_pct: Optional[float] = None,
    max_age_bars: Optional[int] = None,
) -> list[OrderBlock]:
    """
    Update status of all OBs against the current bar.

    Mitigated: price traded through >= OB_MITIGATED_PCT of the OB range.
      Bullish OB: bar low <= bottom + mitigated_pct * size
      Bearish OB: bar high >= top - mitigated_pct * size

    Expired: age > OB_MAX_AGE_BARS.

    Args:
        obs: list of OrderBlock (mutated in place)
        current_bar: current M15 bar
        current_bar_idx: absolute bar index
        mitigated_pct: mitigation threshold (defaults to config.OB_MITIGATED_PCT)
        max_age_bars: expiry threshold (defaults to config.OB_MAX_AGE_BARS)

    Returns:
        Same list with statuses updated.
    """
    if mitigated_pct is None:
        mitigated_pct = config.OB_MITIGATED_PCT
    if max_age_bars is None:
        max_age_bars = config.OB_MAX_AGE_BARS

    bar_high = float(current_bar["high"])
    bar_low  = float(current_bar["low"])

    for ob in obs:
        if ob.status != OB_VALID:
            continue

        ob.age_bars = current_bar_idx - ob.formed_bar_idx

        # Expiry check
        if ob.age_bars > max_age_bars:
            ob.status = OB_EXPIRED
            continue

        # Mitigation check
        mitigation_level = mitigated_pct * ob.size
        if ob.direction == "BULLISH":
            # Price trading through 75% of OB from top down
            if bar_low <= ob.top - mitigation_level:
                ob.status = OB_MITIGATED
        else:
            # Price trading through 75% of OB from bottom up
            if bar_high >= ob.bottom + mitigation_level:
                ob.status = OB_MITIGATED

    return obs


def get_valid_obs(obs: list[OrderBlock]) -> list[OrderBlock]:
    """Return only VALID OBs, sorted newest first."""
    return sorted(
        [ob for ob in obs if ob.status == OB_VALID],
        key=lambda ob: ob.formed_at,
        reverse=True,
    )


def is_price_in_ob(ob: OrderBlock, price: float) -> bool:
    """Check if price is within the OB range."""
    return ob.bottom <= price <= ob.top
