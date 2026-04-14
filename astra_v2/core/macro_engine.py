"""
Macro Engine — synthesize macro data into directional bias via LLM.

Data flow:
  FRED (TIPS/10Y) + yfinance (DXY/VIX/TNX) + COT + news headlines
    → structured prompt
    → Gemini Flash
    → MacroBias(direction, confidence, reasoning)
    → cached in Supabase macro_cache (60-min TTL)

LLM reads TEXT and NUMBERS, not OHLCV candles.
This is the correct use of an LLM in trading.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, asdict
from datetime import datetime, timezone, timedelta
from typing import Literal, Optional
try:
    import google.genai as genai  # new SDK (google-genai)
    _GENAI_NEW = True
except ImportError:
    import google.generativeai as genai  # type: ignore  # legacy fallback
    _GENAI_NEW = False

from astra_v2 import config
from astra_v2.data.macro_features import compute_macro_features, MacroFeatures

logger = logging.getLogger(__name__)

Direction = Literal["BULLISH", "BEARISH", "NEUTRAL"]


@dataclass
class MacroBias:
    direction: Direction
    confidence: float          # 0.0 – 1.0
    reasoning: str
    tips_spread: float
    dxy: Optional[float]
    vix: Optional[float]
    cot_net: Optional[int]
    timestamp: datetime

    def is_actionable(self) -> bool:
        return self.direction != "NEUTRAL" and self.confidence >= config.MACRO_CONFIDENCE_MIN

    def to_dict(self) -> dict:
        d = asdict(self)
        d["timestamp"] = self.timestamp.isoformat()
        return d


# ── Neutral fallback ───────────────────────────────────────────────────────────

def _neutral_bias(reason: str = "fallback") -> MacroBias:
    return MacroBias(
        direction="NEUTRAL",
        confidence=0.0,
        reasoning=reason,
        tips_spread=0.0,
        dxy=None,
        vix=None,
        cot_net=None,
        timestamp=datetime.now(timezone.utc),
    )


# ── LLM call ──────────────────────────────────────────────────────────────────

def _build_prompt(features: MacroFeatures) -> str:
    cot_desc = "unknown"
    if features.cot_net is not None:
        cot_desc = f"{features.cot_net:+,} contracts ({'net long' if features.cot_net > 0 else 'net short'} speculators)"

    return f"""You are a macro analyst for XAUUSD (spot gold). Analyze the following macro data and provide a directional bias.

MACRO DATA ({features.date}):
- TIPS spread (10Y nominal - 10Y breakeven): {features.tips_spread:+.2f}%
  * Falling TIPS spread = falling real yields = BULLISH gold
  * Rising TIPS spread = rising real yields = BEARISH gold
- US Dollar (DXY broad): {features.dxy_broad:.1f} (1-month change: {features.dxy_1m_change:+.1f}%)
  * Rising dollar = BEARISH gold
  * Falling dollar = BULLISH gold
- VIX (fear index): {features.vix:.1f}
  * VIX > 25 = elevated uncertainty (NEUTRAL bias, wait for clarity)
  * VIX 15-25 = normal (bias other factors)
- 10Y Treasury yield: {features.dgs10:.2f}%
- COT non-commercial net positioning: {cot_desc}
  * Note: COT data is 3-10 days stale. Use as directional regime only.

Provide your assessment as JSON with these exact fields:
{{
  "direction": "BULLISH" | "BEARISH" | "NEUTRAL",
  "confidence": 0.0-1.0,
  "reasoning": "1-2 sentence explanation of the key factors"
}}

Rules:
- NEUTRAL if VIX > 30 or conflicting signals with no clear dominant factor
- Confidence > 0.75 only when 3+ factors agree
- Confidence 0.60-0.75 when 2 factors agree
- Confidence < 0.60 → use NEUTRAL instead

Return ONLY the JSON object, no other text."""


def _call_llm(prompt: str) -> Optional[dict]:
    """Call Gemini Flash and parse JSON response."""
    if not config.GEMINI_API_KEY:
        raise EnvironmentError("GEMINI_API_KEY not set")

    try:
        if _GENAI_NEW:
            # google-genai SDK (current)
            client = genai.Client(api_key=config.GEMINI_API_KEY)
            response = client.models.generate_content(
                model=config.GEMINI_MODEL,
                contents=prompt,
                config=genai.types.GenerateContentConfig(
                    temperature=0.1,
                    max_output_tokens=256,
                ),
            )
            text = response.text.strip()
        else:
            # Legacy google-generativeai fallback
            genai.configure(api_key=config.GEMINI_API_KEY)
            model = genai.GenerativeModel(config.GEMINI_MODEL)
            response = model.generate_content(
                prompt,
                generation_config=genai.types.GenerationConfig(
                    temperature=0.1,
                    max_output_tokens=256,
                ),
            )
            text = response.text.strip()

        # Strip markdown fences if present
        if text.startswith("```"):
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]

        return json.loads(text)

    except json.JSONDecodeError as e:
        logger.warning(f"LLM returned invalid JSON: {e}")
        return None
    except Exception as e:
        logger.warning(f"LLM call failed: {e}")
        return None


def _parse_llm_response(data: dict, features: MacroFeatures) -> MacroBias:
    """Parse and validate LLM JSON response into MacroBias."""
    direction = data.get("direction", "NEUTRAL").upper()
    if direction not in ("BULLISH", "BEARISH", "NEUTRAL"):
        direction = "NEUTRAL"

    confidence = float(data.get("confidence", 0.0))
    confidence = max(0.0, min(1.0, confidence))

    if confidence < config.MACRO_CONFIDENCE_MIN:
        direction = "NEUTRAL"

    return MacroBias(
        direction=direction,
        confidence=confidence,
        reasoning=data.get("reasoning", ""),
        tips_spread=features.tips_spread,
        dxy=features.dxy_broad,
        vix=features.vix,
        cot_net=features.cot_net,
        timestamp=datetime.now(timezone.utc),
    )


# ── Supabase cache ─────────────────────────────────────────────────────────────

def _load_from_cache(supabase_client) -> Optional[MacroBias]:
    """Read latest macro bias from Supabase. Returns None if stale or missing."""
    try:
        result = (
            supabase_client.table("macro_cache")
            .select("*")
            .order("timestamp", desc=True)
            .limit(1)
            .execute()
        )
        if not result.data:
            return None

        row = result.data[0]
        ts = datetime.fromisoformat(row["timestamp"])
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)

        age_minutes = (datetime.now(timezone.utc) - ts).total_seconds() / 60
        if age_minutes > config.MACRO_CACHE_TTL_MINUTES:
            logger.info(f"Macro cache expired ({age_minutes:.0f} min old)")
            return None

        logger.info(f"Macro cache hit: {row['direction']} ({row['confidence']:.2f}) from {ts}")
        return MacroBias(
            direction=row["direction"],
            confidence=float(row["confidence"]),
            reasoning=row.get("reasoning", ""),
            tips_spread=float(row.get("tips_spread", 0)),
            dxy=row.get("dxy"),
            vix=row.get("vix"),
            cot_net=row.get("cot_net"),
            timestamp=ts,
        )
    except Exception as e:
        logger.warning(f"Macro cache load failed: {e}")
        return None


def _save_to_cache(bias: MacroBias, supabase_client) -> None:
    """Save macro bias to Supabase macro_cache."""
    try:
        supabase_client.table("macro_cache").insert({
            "timestamp": bias.timestamp.isoformat(),
            "direction": bias.direction,
            "confidence": bias.confidence,
            "reasoning": bias.reasoning,
            "tips_spread": bias.tips_spread,
            "dxy": bias.dxy,
            "vix": bias.vix,
            "cot_net": bias.cot_net,
        }).execute()
    except Exception as e:
        logger.warning(f"Macro cache save failed: {e}")


# ── Public API ─────────────────────────────────────────────────────────────────

def get_bias(
    supabase_client=None,
    fred_df: "pd.DataFrame" = None,
    yfinance_df: "pd.DataFrame" = None,
    cot_df: "pd.DataFrame" = None,
    force_refresh: bool = False,
) -> MacroBias:
    """
    Get current macro bias. Uses Supabase cache (60-min TTL).

    Args:
        supabase_client: Supabase client instance (for cache). Optional.
        fred_df: Pre-loaded FRED DataFrame (for backtest). If None, fetches live.
        yfinance_df: Pre-loaded yfinance DataFrame (for backtest).
        cot_df: Pre-loaded COT DataFrame (for backtest).
        force_refresh: bypass cache

    Returns MacroBias. Never raises — falls back to NEUTRAL on any error.
    """
    now = datetime.now(timezone.utc)

    # Try cache first
    if supabase_client and not force_refresh:
        cached = _load_from_cache(supabase_client)
        if cached:
            return cached

    # Compute macro features
    try:
        features = compute_macro_features(now, fred_df=fred_df, yfinance_df=yfinance_df, cot_df=cot_df)
    except Exception as e:
        logger.warning(f"Macro features computation failed: {e}")
        return _neutral_bias(f"features error: {e}")

    # Build prompt and call LLM
    prompt = _build_prompt(features)
    raw = _call_llm(prompt)
    if raw is None:
        return _neutral_bias("LLM unavailable")

    bias = _parse_llm_response(raw, features)

    # Save to cache
    if supabase_client:
        _save_to_cache(bias, supabase_client)

    logger.info(f"Macro bias: {bias.direction} ({bias.confidence:.2f}) — {bias.reasoning[:80]}")
    return bias
