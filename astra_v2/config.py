"""
AstraAnalyzerPro v2 — Configuration
All secrets via environment variables. All prop-firm rules as named constants.
"""
import os
from dataclasses import dataclass
from typing import Optional

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


# ── API Keys (set in .env or environment) ─────────────────────────────────────
OANDA_API_KEY = os.environ.get("OANDA_API_KEY", "")
OANDA_ACCOUNT_ID = os.environ.get("OANDA_ACCOUNT_ID", "")
OANDA_ENV = os.environ.get("OANDA_ENV", "practice")  # "practice" or "live"

FRED_API_KEY = os.environ.get("FRED_API_KEY", "")

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.0-flash")

OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")
# Comma-separated list of models to try in order (first available wins)
_OPENROUTER_MODELS_RAW = os.environ.get(
    "OPENROUTER_MODELS",
    "google/gemma-3-12b-it:free,meta-llama/llama-3.2-3b-instruct:free,meta-llama/llama-3.3-70b-instruct:free,nvidia/nemotron-3-super-120b-a12b:free",
)
OPENROUTER_MODELS: list[str] = [m.strip() for m in _OPENROUTER_MODELS_RAW.split(",") if m.strip()]
OPENROUTER_MODEL = OPENROUTER_MODELS[0] if OPENROUTER_MODELS else ""  # legacy compat
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1/chat/completions"

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

# ── Instrument ─────────────────────────────────────────────────────────────────
SYMBOL = "XAU_USD"          # OANDA format
MT5_SYMBOL = "XAUUSD"       # MT5 format
YFINANCE_GOLD = "GC=F"      # Gold futures (for historical fallback only)
YFINANCE_DXY = "DX-Y.NYB"
YFINANCE_VIX = "^VIX"
YFINANCE_TNX = "^TNX"

# ── Sessions (UTC) ─────────────────────────────────────────────────────────────
LONDON_OPEN_UTC = 7      # 07:00 UTC
LONDON_CLOSE_UTC = 12    # 12:00 UTC
NY_OPEN_UTC = 13         # 13:00 UTC
NY_CLOSE_UTC = 17        # 17:00 UTC

# ── Signal Gate Thresholds ─────────────────────────────────────────────────────
MACRO_CONFIDENCE_MIN = 0.55      # below this → NEUTRAL treatment
LEVEL_PROXIMITY_USD = 3.0        # price must be within $3 of a key level (~0.15% at $2000)
LEVEL_STRENGTH_MIN = 4.5         # out of 10 — includes PDH/PDL (5.0), round_10 (4.5)
MAX_TRADES_PER_DAY = 2
TRIGGER_LEVEL_TYPES = (
    "pdh",
    "pdl",
    "weekly_high",
    "weekly_low",
    "session_high",
    "session_low",
    "fib_50",
    "fib_618",
)
DEFAULT_STRATEGY_ID = os.environ.get("ASTRA_STRATEGY_ID", "legacy_v1")

# ── Trade Parameters (all in USD, not pips) ────────────────────────────────────
SL_DISTANCE_USD = 7.0            # default SL beyond level ($5–8 range)
TP_RR = 2.0                      # reward:risk ratio
PARTIAL_CLOSE_RR = 1.0           # close 50% of position at 1:1
BE_TRIGGER_RR = 1.0              # move SL to entry at +1R
TRAIL_TRIGGER_RR = 1.5           # start trailing at +1.5R
TRAIL_DISTANCE_ATR = 0.3         # trail by 0.3 ATR (adaptive to volatility)
TRAIL_ATR_PERIOD = 20            # ATR period for trailing stop calculation
SLIPPAGE_USD = 0.75              # assumed slippage per side (for backtest)
ENTRY_LIMIT_OFFSET_USD = 0.50    # limit order offset behind level

# ── Strategy: sweep_reversal_v1 ───────────────────────────────────────────────
SWEEP_REVERSAL_V1_ALLOWED_SESSIONS = ("london", "new_york")
SWEEP_REVERSAL_V1_TRIGGER_LEVEL_TYPES = (
    "pdh",
    "pdl",
    "weekly_high",
    "weekly_low",
)
SWEEP_REVERSAL_V1_SWEEP_MIN_USD = 1.0
SWEEP_REVERSAL_V1_CONFIRMATION_CLOSE_OFFSET_USD = 0.75
SWEEP_REVERSAL_V1_MAX_CONFIRM_BARS = 2
SWEEP_REVERSAL_V1_STOP_BUFFER_USD = 1.0
SWEEP_REVERSAL_V1_TP_RR = 2.0
SWEEP_REVERSAL_V1_PARTIAL_CLOSE_RR = 1.0
SWEEP_REVERSAL_V1_MAX_TRADES_PER_DAY = 2
SWEEP_REVERSAL_V1_FORCE_CLOSE_HOUR_UTC = 21

# -- Strategy: sweep_reversal_v2 ------------------------------------------------
SWEEP_REVERSAL_V2_ALLOWED_SESSIONS = ("london", "new_york")
SWEEP_REVERSAL_V2_TRIGGER_LEVEL_TYPES = (
    "pdh",
    "pdl",
    "weekly_high",
    "weekly_low",
    "session_high",
    "session_low",
)
SWEEP_REVERSAL_V2_CONFLUENCE_LEVEL_TYPES = (
    "fib_50",
    "fib_618",
)
SWEEP_REVERSAL_V2_ATR_PERIOD = 20
SWEEP_REVERSAL_V2_MAX_CONFIRM_BARS = 3
SWEEP_REVERSAL_V2_BASE_SWEEP_ATR = 0.18
SWEEP_REVERSAL_V2_BASE_RECLAIM_ATR = 0.10
SWEEP_REVERSAL_V2_BULLISH_MIN_BODY_ATR = 0.10
SWEEP_REVERSAL_V2_BEARISH_MIN_BODY_ATR = 0.16
SWEEP_REVERSAL_V2_STOP_BUFFER_ATR = 0.18
SWEEP_REVERSAL_V2_TP_RR = 1.8
SWEEP_REVERSAL_V2_PARTIAL_CLOSE_RR = 0.9
SWEEP_REVERSAL_V2_MAX_TRADES_PER_DAY = 3
SWEEP_REVERSAL_V2_FORCE_CLOSE_HOUR_UTC = 20
SWEEP_REVERSAL_V2_ALIGNED_REGIME_MULTIPLIER = 0.95
SWEEP_REVERSAL_V2_COUNTER_REGIME_MULTIPLIER = 1.35
SWEEP_REVERSAL_V2_NY_THRESHOLD_MULTIPLIER = 1.15
SWEEP_REVERSAL_V2_SESSION_LEVEL_MULTIPLIER = 1.10
SWEEP_REVERSAL_V2_WEEKLY_LEVEL_MULTIPLIER = 0.90
SWEEP_REVERSAL_V2_FIB_50_MULTIPLIER = 1.20
SWEEP_REVERSAL_V2_FIB_618_MULTIPLIER = 1.05
SWEEP_REVERSAL_V2_FIB_CONFLUENCE_DISTANCE_ATR = 0.35
SWEEP_REVERSAL_V2_FIB_CONFLUENCE_THRESHOLD_MULTIPLIER = 0.90
SWEEP_REVERSAL_V2_BULLISH_CLOSE_IN_RANGE_MIN = 0.60
SWEEP_REVERSAL_V2_BEARISH_CLOSE_IN_RANGE_MAX = 0.40

# -- Strategy: sweep_reversal_v3 ------------------------------------------------
SWEEP_REVERSAL_V3_ALLOWED_SESSIONS = ("london", "new_york")
SWEEP_REVERSAL_V3_TRIGGER_LEVEL_TYPES = (
    "pdh",
    "pdl",
    "weekly_high",
    "weekly_low",
    "session_high",
    "session_low",
)
SWEEP_REVERSAL_V3_CONFLUENCE_LEVEL_TYPES = (
    "fib_50",
    "fib_618",
)
SWEEP_REVERSAL_V3_ATR_PERIOD = 20
SWEEP_REVERSAL_V3_MAX_CONFIRM_BARS = 3
SWEEP_REVERSAL_V3_BASE_SWEEP_ATR = 0.18
SWEEP_REVERSAL_V3_BASE_RECLAIM_ATR = 0.10
SWEEP_REVERSAL_V3_BULLISH_MIN_BODY_ATR = 0.11
SWEEP_REVERSAL_V3_BEARISH_MIN_BODY_ATR = 0.17
SWEEP_REVERSAL_V3_STOP_BUFFER_ATR = 0.18
SWEEP_REVERSAL_V3_TP_RR = 1.8
SWEEP_REVERSAL_V3_PARTIAL_CLOSE_RR = 0.9
SWEEP_REVERSAL_V3_MAX_TRADES_PER_DAY = 3
SWEEP_REVERSAL_V3_FORCE_CLOSE_HOUR_UTC = 20
SWEEP_REVERSAL_V3_ALIGNED_REGIME_MULTIPLIER = 0.95
SWEEP_REVERSAL_V3_COUNTER_REGIME_MULTIPLIER = 1.35
SWEEP_REVERSAL_V3_NY_THRESHOLD_MULTIPLIER = 1.10
SWEEP_REVERSAL_V3_SESSION_LEVEL_MULTIPLIER = 1.08
SWEEP_REVERSAL_V3_WEEKLY_LEVEL_MULTIPLIER = 0.95
SWEEP_REVERSAL_V3_FIB_50_MULTIPLIER = 1.20
SWEEP_REVERSAL_V3_FIB_618_MULTIPLIER = 1.05
SWEEP_REVERSAL_V3_FIB_CONFLUENCE_DISTANCE_ATR = 0.35
SWEEP_REVERSAL_V3_FIB_CONFLUENCE_THRESHOLD_MULTIPLIER = 0.92
SWEEP_REVERSAL_V3_BULLISH_CLOSE_IN_RANGE_MIN = 0.62
SWEEP_REVERSAL_V3_BEARISH_CLOSE_IN_RANGE_MAX = 0.38
SWEEP_REVERSAL_V3_DISPLACEMENT_ATR = 0.05
SWEEP_REVERSAL_V3_STRICT_BRANCH_MULTIPLIER = 1.18

# -- Strategy: sweep_reversal_v4 ------------------------------------------------
SWEEP_REVERSAL_V4_ALLOWED_SESSIONS = ("london", "new_york")
SWEEP_REVERSAL_V4_TRIGGER_LEVEL_TYPES = (
    "pdh",
    "pdl",
    "weekly_high",
    "weekly_low",
    "session_high",
    "session_low",
)
SWEEP_REVERSAL_V4_CONFLUENCE_LEVEL_TYPES = (
    "fib_50",
    "fib_618",
)
SWEEP_REVERSAL_V4_ATR_PERIOD = 20
SWEEP_REVERSAL_V4_MAX_CONFIRM_BARS = 3
SWEEP_REVERSAL_V4_BASE_SWEEP_ATR = 0.17
SWEEP_REVERSAL_V4_BASE_RECLAIM_ATR = 0.09
SWEEP_REVERSAL_V4_BULLISH_MIN_BODY_ATR = 0.10
SWEEP_REVERSAL_V4_BEARISH_MIN_BODY_ATR = 0.15
SWEEP_REVERSAL_V4_STOP_BUFFER_ATR = 0.16
SWEEP_REVERSAL_V4_TP_RR = 1.7
SWEEP_REVERSAL_V4_PARTIAL_CLOSE_RR = 0.85
SWEEP_REVERSAL_V4_MAX_TRADES_PER_DAY = 5
SWEEP_REVERSAL_V4_FORCE_CLOSE_HOUR_UTC = 22
SWEEP_REVERSAL_V4_ALIGNED_REGIME_MULTIPLIER = 0.95
SWEEP_REVERSAL_V4_COUNTER_REGIME_MULTIPLIER = 1.30
SWEEP_REVERSAL_V4_NY_THRESHOLD_MULTIPLIER = 1.08
SWEEP_REVERSAL_V4_SESSION_LEVEL_MULTIPLIER = 1.05
SWEEP_REVERSAL_V4_WEEKLY_LEVEL_MULTIPLIER = 0.95
SWEEP_REVERSAL_V4_FIB_50_MULTIPLIER = 1.15
SWEEP_REVERSAL_V4_FIB_618_MULTIPLIER = 1.03
SWEEP_REVERSAL_V4_FIB_CONFLUENCE_DISTANCE_ATR = 0.35
SWEEP_REVERSAL_V4_FIB_CONFLUENCE_THRESHOLD_MULTIPLIER = 0.94
SWEEP_REVERSAL_V4_BULLISH_CLOSE_IN_RANGE_MIN = 0.60
SWEEP_REVERSAL_V4_BEARISH_CLOSE_IN_RANGE_MAX = 0.40
SWEEP_REVERSAL_V4_DISPLACEMENT_ATR = 0.05
SWEEP_REVERSAL_V4_STRICT_BRANCH_MULTIPLIER = 1.14
SWEEP_REVERSAL_V4_H4_LOOKBACK_BARS = 20
SWEEP_REVERSAL_V4_H4_TREND_PERIOD = 8
SWEEP_REVERSAL_V4_H4_ALIGNED_MULTIPLIER = 0.94
SWEEP_REVERSAL_V4_H4_COUNTER_MULTIPLIER = 1.10
SWEEP_REVERSAL_V4_H4_FAVORABLE_RANGE_POS = 0.45
SWEEP_REVERSAL_V4_H4_UNFAVORABLE_RANGE_POS = 0.65
SWEEP_REVERSAL_V4_H4_FAVORABLE_LOCATION_MULTIPLIER = 0.92
SWEEP_REVERSAL_V4_H4_UNFAVORABLE_LOCATION_MULTIPLIER = 1.12
SWEEP_REVERSAL_V4_M1_LOOKBACK_BARS = 12
SWEEP_REVERSAL_V4_M1_TRIGGER_LOOKBACK = 3
SWEEP_REVERSAL_V4_M1_RECLAIM_ATR = 0.03
SWEEP_REVERSAL_V4_M1_TRIGGER_BUFFER_ATR = 0.005

# -- Strategy: breakout_retest_v1 (Break & Retest — trade continuation) ----------
# Concept: detect clean breakout of key structural level, enter on the first retest.
# Trade direction matches the breakout (with H4 trend alignment filter).
BREAKOUT_RETEST_V1_ALLOWED_SESSIONS = ("london", "new_york")
# BULLISH-only: only resistance levels → buy breakout retests (aligns with bull gold trend)
# Removed: "pdl", "session_low", "weekly_low" — weak WR in 2020-2024 bull trend
BREAKOUT_RETEST_V1_TRIGGER_LEVEL_TYPES = (
    "pdh", "session_high", "weekly_high",
)
BREAKOUT_RETEST_V1_ATR_PERIOD = 20
BREAKOUT_RETEST_V1_BREAKOUT_LOOKBACK_BARS = 40    # scan last 40 bars (10 hours) for a breakout
# NOTE: ATR here is M15 ATR (~$1.5-2 for XAUUSD). Multipliers are scaled accordingly.
BREAKOUT_RETEST_V1_BREAKOUT_MIN_ATR = 3.0         # close must be ≥3 ATR beyond level (~$5) to count
BREAKOUT_RETEST_V1_RETEST_PROXIMITY_ATR = 2.0     # retest zone: bar must come within 2 ATR of level (~$3.5)
BREAKOUT_RETEST_V1_REJECTION_MIN_BODY_ATR = 0.4   # rejection candle body must be ≥0.4 ATR (~$0.7)
BREAKOUT_RETEST_V1_CLOSE_IN_RANGE_BULL = 0.65     # bullish rejection: close in upper 35% of bar
BREAKOUT_RETEST_V1_CLOSE_IN_RANGE_BEAR = 0.35     # bearish rejection: close in lower 35% of bar
BREAKOUT_RETEST_V1_STOP_BUFFER_ATR = 2.0          # SL placed 2 ATR beyond the level (~$3.5)
BREAKOUT_RETEST_V1_TP_RR = 2.0
BREAKOUT_RETEST_V1_PARTIAL_CLOSE_RR = 1.0
BREAKOUT_RETEST_V1_MAX_TRADES_PER_DAY = 3
BREAKOUT_RETEST_V1_FORCE_CLOSE_HOUR_UTC = 22
# Block entries during mid/late London (09-12 UTC): PDH retests in this window
# have very poor WR (high SL rate) while NY session PDH retests are excellent.
BREAKOUT_RETEST_V1_BLOCKED_HOURS_UTC = frozenset({9, 10, 11, 12})
BREAKOUT_RETEST_V1_H4_LOOKBACK_BARS = 20
BREAKOUT_RETEST_V1_H4_TREND_PERIOD = 8            # 32h MA → faster H4 trend response
BREAKOUT_RETEST_V1_H4_REQUIRE_ALIGNMENT = True    # block trades counter to H4 trend
# ── Order flow (Same-Hour RVOL) ──────────────────────────────────────────────
# RVOL = current bar volume / avg volume at the same UTC hour over last N days.
# Normalises for intraday seasonality (NY session is naturally 2x louder than Asian).
# RVOL used as position-sizing multiplier (not a binary filter).
# Retest bar RVOL 1.0-2.0 → 67-78% WR (real buyers); <1.0 → 50-52% WR (drift).
# size_multiplier scales the 1% base risk: high conviction = larger position.
BREAKOUT_RETEST_V1_RVOL_LOOKBACK_DAYS = 20
BREAKOUT_RETEST_V1_RVOL_HIGH_THRESH = 1.0    # RVOL >= this → high-conviction multiplier
BREAKOUT_RETEST_V1_RVOL_PANIC_THRESH = 2.0   # RVOL >= this → panic/breakdown (reduce size)
BREAKOUT_RETEST_V1_SIZE_HIGH_RVOL = 1.1      # RVOL 1.0-2.0 → 67-78% WR → slight increase
BREAKOUT_RETEST_V1_SIZE_LOW_RVOL = 0.7       # RVOL < 1.0 → 50-52% WR → reduce position
BREAKOUT_RETEST_V1_SIZE_PANIC_RVOL = 0.5     # RVOL > 2.0 → 40% WR → half position

# -- Strategy: sweep_reversal_v4a (Variant A: High-R, strict M1, no partial close) ----
# Identical to v4 except: strict M1 trigger restored, TP raised to 2.5R, partial close disabled
SWEEP_REVERSAL_V4A_TP_RR = 2.5
SWEEP_REVERSAL_V4A_PARTIAL_CLOSE_RR = 999.0       # unreachable → effectively disables partial close
SWEEP_REVERSAL_V4A_M1_TRIGGER_LOOKBACK = 5         # strict (original v4 value)
SWEEP_REVERSAL_V4A_M1_RECLAIM_ATR = 0.04           # strict (original v4 value)
SWEEP_REVERSAL_V4A_M1_TRIGGER_BUFFER_ATR = 0.015   # strict (original v4 value)

# -- Strategy: sweep_reversal_v4b (Variant B: Frequency + Tight SL) ----------------
# Identical to v4 except: tighter stop buffer → better dollar R:R, TP raised to 2.0R
SWEEP_REVERSAL_V4B_STOP_BUFFER_ATR = 0.08   # tighter than v4's 0.16
SWEEP_REVERSAL_V4B_TP_RR = 2.0              # higher than v4's 1.7
SWEEP_REVERSAL_V4B_PARTIAL_CLOSE_RR = 1.0   # kept at 1:1
SWEEP_REVERSAL_V4B_M1_TRIGGER_LOOKBACK = 3  # same as current relaxed v4
SWEEP_REVERSAL_V4B_M1_RECLAIM_ATR = 0.03
SWEEP_REVERSAL_V4B_M1_TRIGGER_BUFFER_ATR = 0.005

# -- Strategy: range_breakout_v1 (Range Consolidation Breakout) --------------------
# Concept: Detect consolidation range, wait for breakout with 2-3 candle confirmation,
# enter on confirmation. Trade continuation after range breakout.
RANGE_BREAKOUT_V1_ALLOWED_SESSIONS = ("london", "new_york")
RANGE_BREAKOUT_V1_ATR_PERIOD = 20
RANGE_BREAKOUT_V1_CONSOLIDATION_LOOKBACK = 20  # bars to scan for range formation
RANGE_BREAKOUT_V1_STOP_BUFFER_ATR = 0.5  # SL buffer beyond opposite range boundary
RANGE_BREAKOUT_V1_TP_RR = 2.0
RANGE_BREAKOUT_V1_PARTIAL_CLOSE_RR = 1.0
RANGE_BREAKOUT_V1_MAX_TRADES_PER_DAY = 3
RANGE_BREAKOUT_V1_FORCE_CLOSE_HOUR_UTC = 22

# ── Position Sizing ────────────────────────────────────────────────────────────
RISK_PCT = 0.005                 # 0.5% of account per trade (SMC portfolio — higher frequency)

# ── Macro Cache ────────────────────────────────────────────────────────────────
MACRO_CACHE_TTL_MINUTES = 60     # refresh macro bias every 60 minutes

# ── Scheduler ─────────────────────────────────────────────────────────────────
CYCLE_INTERVAL_MINUTES = 15      # run analysis every 15 minutes

# ── Prop Firm Rules (edit per firm) ───────────────────────────────────────────
# These are enforced in signal_gate.py and scheduler.py
PROP_MAX_DAILY_LOSS_PCT = 2.0    # % of account, daily loss limit
PROP_TRAILING_DD_PCT = 5.0       # % from peak (trailing drawdown — lose challenge)
PROP_KILL_SWITCH_DD_PCT = 4.5    # emergency: close all positions
PROP_DAILY_STOP_DD_PCT = 3.5     # stop new trades for the rest of the day
PROP_MAX_LOT_SIZE = 1.0          # max lots per trade
PROP_NEWS_BLACKOUT_MINUTES = 30  # no trades N minutes before/after high-impact news
PROP_WEEKEND_HOLD_ALLOWED = False # if False: close all positions Friday 21:00 UTC

# ── Dukascopy / Backtest Data ──────────────────────────────────────────────────
DUKASCOPY_CACHE_DIR = os.environ.get("DUKASCOPY_CACHE_DIR", "data_cache/dukascopy")
FRED_CACHE_PATH = os.environ.get("FRED_CACHE_PATH", "data_cache/fred_daily.parquet")
YFINANCE_CACHE_PATH = os.environ.get("YFINANCE_CACHE_PATH", "data_cache/yfinance_daily.parquet")
COT_CACHE_PATH = os.environ.get("COT_CACHE_PATH", "data_cache/cot_gold.parquet")

# Primary M15 stream for levels / execution; cross pairs are extra M15 context in backtests.
BACKTEST_PRIMARY_SYMBOL = os.environ.get("BACKTEST_PRIMARY_SYMBOL", "XAUUSD").strip().upper()
_BACKTEST_CROSS_RAW = os.environ.get("BACKTEST_CROSS_SYMBOLS", "BTCUSD,XAGUSD,EURUSD")
BACKTEST_CROSS_SYMBOLS: tuple[str, ...] = tuple(
    x.strip().upper() for x in _BACKTEST_CROSS_RAW.split(",") if x.strip()
)
# Full set for `--all-symbols` backtests (one run per pair; others feed cross M15).
_MULTI_UNIVERSE_RAW = os.environ.get(
    "BACKTEST_MULTI_SYMBOL_UNIVERSE", "XAUUSD,BTCUSD,XAGUSD,EURUSD"
)
BACKTEST_MULTI_SYMBOL_UNIVERSE: tuple[str, ...] = tuple(
    x.strip().upper() for x in _MULTI_UNIVERSE_RAW.split(",") if x.strip()
)
# none = only StrategyContext.cross_symbol_m15; size = scale size by M15 agreement; gate = veto weak agreement
CROSS_ASSET_MODE = os.environ.get("CROSS_ASSET_MODE", "size").strip().lower()
CROSS_ASSET_GATE_MIN_AGREE_RATIO = float(os.environ.get("CROSS_ASSET_GATE_MIN_AGREE_RATIO", "0.45"))

BACKTEST_START = "2020-01-01"
BACKTEST_END = "2023-12-31"
BACKTEST_HOLDOUT_START = "2024-01-01"   # out-of-sample, never touched during dev
BACKTEST_HOLDOUT_END = "2025-12-31"

# ── SMC: Market Structure ──────────────────────────────────────────────────────
MS_SWING_LOOKBACK = 10           # bars to left/right to confirm swing high/low
MS_BOS_MIN_ATR = 1.0             # min close distance beyond swing (in ATR) to confirm BOS

# ── SMC: Fair Value Gap ────────────────────────────────────────────────────────
FVG_MIN_SIZE_ATR = 3.0           # min gap size in ATR units — M15 ATR ~$1.8, so min $5.4 gap; best-found for quality setups
FVG_EXPIRY_BARS = 96             # expire FVG after N M15 bars (~24h at M15)
FVG_ENTRY_DEPTH = 0.5            # enter at 50% into the gap

# ── SMC: Order Block ───────────────────────────────────────────────────────────
OB_MIN_BODY_ATR = 0.5            # min candle body size for valid OB (in ATR)
OB_MITIGATED_PCT = 0.75          # OB invalidated when 75% of range traded through
OB_MAX_AGE_BARS = 100            # expire OB after N M15 bars (~25 hours)
OB_IMPULSE_ATR = 1.5             # min impulse move after OB candle to confirm OB

# ── SMC: Regime Detector ───────────────────────────────────────────────────────
REGIME_ADX_TRENDING = 25         # ADX > 25 → TRENDING
REGIME_ADX_WEAK = 15             # ADX < 15 → ACCUMULATION (or DISTRIBUTION)
REGIME_ATR_VOLATILE = 2.5        # ATR > 2.5x 20-period avg → VOLATILE
REGIME_MIN_H4_BARS = 20          # minimum H4 bars needed to compute regime

# ── SMC: Portfolio Manager ─────────────────────────────────────────────────────
PORTFOLIO_MAX_DD_PCT = 6.0       # halt all strategies if combined DD >= 6%
PORTFOLIO_CONCURRENT_MAX = 2     # max simultaneous open trades across all strategies
PORTFOLIO_CONCURRENT_REDUCE = 0.7  # size multiplier when PORTFOLIO_CONCURRENT_MAX trades open

# ── SMC: Strategies ────────────────────────────────────────────────────────────
SMC_FVG_V1_ALLOWED_SESSIONS = ("london", "new_york")
SMC_FVG_V1_MAX_TRADES_PER_DAY = 4
SMC_FVG_V1_FORCE_CLOSE_HOUR_UTC = 21
SMC_FVG_V1_ATR_PERIOD = 20
SMC_FVG_V1_TP_RR = 2.0
SMC_FVG_V1_PARTIAL_CLOSE_RR = 1.0
SMC_FVG_V1_STOP_BUFFER_ATR = 1.5  # SL beyond FVG edge — M15 ATR ~$1.8, so ~$2.7 buffer
SMC_FVG_V1_ENTRY_DEPTH_ATR = 1.0  # max entry depth into FVG from boundary (ATR units); used in touch_boundary mode
SMC_FVG_V1_ENTRY_MODE = "touch_boundary"  # "touch_boundary" or "wick_rejection"
SMC_FVG_V1_FVG_TIMEFRAME = "M15"  # "M15" or "H4" — which timeframe to detect FVGs on
# Best found: FVG_MIN_SIZE_ATR=3.0, touch_boundary, TRENDING+ACCUM → PF=0.953 (below 1.4 target)
# Strategy does not independently meet PF≥1.4 on M15 XAUUSD (2020-2024).
# Viable path: H1 data (not available) or fundamental redesign.
SMC_FVG_V1_ALLOWED_REGIMES = ("TRENDING", "ACCUMULATION")  # FVGs form in both trending and accumulation

SMC_OB_V1_ALLOWED_SESSIONS = ("london", "new_york")
SMC_OB_V1_MAX_TRADES_PER_DAY = 4
SMC_OB_V1_FORCE_CLOSE_HOUR_UTC = 21
SMC_OB_V1_ATR_PERIOD = 20
SMC_OB_V1_TP_RR = 2.0
SMC_OB_V1_PARTIAL_CLOSE_RR = 1.0
SMC_OB_V1_STOP_BUFFER_ATR = 0.3  # SL beyond OB edge (in ATR)
SMC_OB_V1_ALLOWED_REGIMES = ("TRENDING",)  # only take OB trades in trending markets

# ── Validation ─────────────────────────────────────────────────────────────────
def validate():
    """Raise on missing required keys for live trading."""
    required = {
        "OANDA_API_KEY": OANDA_API_KEY,
        "OANDA_ACCOUNT_ID": OANDA_ACCOUNT_ID,
        "FRED_API_KEY": FRED_API_KEY,
        "GEMINI_API_KEY": GEMINI_API_KEY,
        "SUPABASE_URL": SUPABASE_URL,
        "SUPABASE_KEY": SUPABASE_KEY,
        "TELEGRAM_BOT_TOKEN": TELEGRAM_BOT_TOKEN,
        "TELEGRAM_CHAT_ID": TELEGRAM_CHAT_ID,
    }
    missing = [k for k, v in required.items() if not v]
    if missing:
        raise EnvironmentError(f"Missing required env vars: {', '.join(missing)}")
