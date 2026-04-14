"""
LLM Proxy — deterministic macro bias rules for backtesting.

Replaces real LLM calls with rule-based logic so backtests are:
  - Free (no API cost)
  - Instant (no network latency)
  - Deterministic (same input → same output, reproducible)
  - Fast (120,000 bars without rate limits)

Uses the SAME MacroFeatures computed by macro_features.py as the real LLM does.
This is the DRY guarantee: proxy and live share one feature computation path.

IMPORTANT: Proxy mode validates the rules, not the real LLM.
Before going live, run the correlation test (scripts/correlation_test.py) to
verify that proxy and real LLM agree >= 75% of the time on historical data.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Literal

from astra_v2.core.macro_engine import MacroBias
from astra_v2.data.macro_features import MacroFeatures
from astra_v2 import config

logger = logging.getLogger(__name__)


def proxy_macro_bias(features: MacroFeatures) -> MacroBias:
    """
    Deterministic macro bias from structured rules.
    Mirrors the logic described in the LLM prompt — same rules, no LLM.

    Rule logic:
      1. VIX > 30 → NEUTRAL (too much uncertainty)
      2. Count how many factors agree on direction
         BULLISH factors: falling TIPS spread, falling DXY, COT net long
         BEARISH factors: rising TIPS spread, rising DXY, COT net short
      3. >= 3 factors agree → high confidence
         2 factors agree → moderate confidence
         < 2 → NEUTRAL

    Thresholds are calibrated against the LLM prompt's stated rules.
    """
    ts = datetime.now(timezone.utc)

    # Gate: extreme uncertainty
    if features.vix > 30:
        return MacroBias(
            direction="NEUTRAL",
            confidence=0.3,
            reasoning=f"VIX={features.vix:.1f} > 30, too much uncertainty",
            tips_spread=features.tips_spread,
            dxy=features.dxy_broad,
            vix=features.vix,
            cot_net=features.cot_net,
            timestamp=ts,
        )

    # Score each factor: +1 = BULLISH, -1 = BEARISH, 0 = neutral
    scores: list[int] = []
    reasons: list[str] = []

    # TIPS spread signal (primary gold driver)
    if features.tips_spread < -0.5:
        scores.append(1)
        reasons.append(f"TIPS={features.tips_spread:+.2f}% (falling real yields)")
    elif features.tips_spread > 0.3:
        scores.append(-1)
        reasons.append(f"TIPS={features.tips_spread:+.2f}% (rising real yields)")
    else:
        scores.append(0)

    # DXY 1-month change (secondary driver)
    if features.dxy_1m_change < -1.5:
        scores.append(1)
        reasons.append(f"DXY 1m change={features.dxy_1m_change:+.1f}% (dollar weakening)")
    elif features.dxy_1m_change > 1.5:
        scores.append(-1)
        reasons.append(f"DXY 1m change={features.dxy_1m_change:+.1f}% (dollar strengthening)")
    else:
        scores.append(0)

    # COT net positioning (regime indicator, stale but directional)
    if features.cot_net is not None:
        if features.cot_net > 100_000:
            scores.append(1)
            reasons.append(f"COT net={features.cot_net:+,} (speculators net long)")
        elif features.cot_net < -50_000:
            scores.append(-1)
            reasons.append(f"COT net={features.cot_net:+,} (speculators net short)")
        else:
            scores.append(0)

    # VIX level (risk sentiment)
    if features.vix < 15:
        # Low fear: risk-on, slightly bearish for gold (safe haven less needed)
        scores.append(-1)
        reasons.append(f"VIX={features.vix:.1f} (low fear, risk-on)")
    elif features.vix > 25:
        scores.append(1)
        reasons.append(f"VIX={features.vix:.1f} (elevated fear, gold bid)")
    else:
        scores.append(0)

    total = sum(scores)
    bullish_count = sum(1 for s in scores if s > 0)
    bearish_count = sum(1 for s in scores if s < 0)

    if bullish_count >= 3:
        direction: Literal["BULLISH", "BEARISH", "NEUTRAL"] = "BULLISH"
        confidence = 0.80
    elif bullish_count == 2 and bearish_count == 0:
        direction = "BULLISH"
        confidence = 0.65
    elif bearish_count >= 3:
        direction = "BEARISH"
        confidence = 0.80
    elif bearish_count == 2 and bullish_count == 0:
        direction = "BEARISH"
        confidence = 0.65
    else:
        direction = "NEUTRAL"
        confidence = 0.40

    # Apply minimum confidence threshold
    if confidence < config.MACRO_CONFIDENCE_MIN:
        direction = "NEUTRAL"

    reasoning = "; ".join(reasons) if reasons else "mixed signals"

    return MacroBias(
        direction=direction,
        confidence=confidence,
        reasoning=f"[PROXY] {reasoning}",
        tips_spread=features.tips_spread,
        dxy=features.dxy_broad,
        vix=features.vix,
        cot_net=features.cot_net,
        timestamp=ts,
    )
