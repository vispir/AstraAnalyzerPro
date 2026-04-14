"""
AstraAnalyzerPro v2 — Configuration
All secrets via environment variables. All prop-firm rules as named constants.
"""
import os
from dataclasses import dataclass
from typing import Optional


# ── API Keys (set in .env or environment) ─────────────────────────────────────
OANDA_API_KEY = os.environ.get("OANDA_API_KEY", "")
OANDA_ACCOUNT_ID = os.environ.get("OANDA_ACCOUNT_ID", "")
OANDA_ENV = os.environ.get("OANDA_ENV", "practice")  # "practice" or "live"

FRED_API_KEY = os.environ.get("FRED_API_KEY", "")

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.0-flash")

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
LONDON_OPEN_UTC = 8      # 08:00 UTC
LONDON_CLOSE_UTC = 11    # 11:00 UTC
NY_OPEN_UTC = 13         # 13:00 UTC
NY_CLOSE_UTC = 15        # 15:00 UTC

# ── Signal Gate Thresholds ─────────────────────────────────────────────────────
MACRO_CONFIDENCE_MIN = 0.60      # below this → NEUTRAL treatment
LEVEL_PROXIMITY_USD = 0.50       # price must be within $0.50 of a key level
LEVEL_STRENGTH_MIN = 6.0         # out of 10 (XGBoost score, v2.1)
MAX_TRADES_PER_DAY = 2

# ── Trade Parameters (all in USD, not pips) ────────────────────────────────────
SL_DISTANCE_USD = 7.0            # default SL beyond level ($5–8 range)
TP_RR = 2.0                      # reward:risk ratio
PARTIAL_CLOSE_RR = 1.0           # close 50% of position at 1:1
BE_TRIGGER_RR = 1.0              # move SL to entry at +1R
TRAIL_TRIGGER_RR = 1.5           # start trailing at +1.5R
TRAIL_DISTANCE_USD = 5.0         # trail by $5
SLIPPAGE_USD = 0.75              # assumed slippage per side (for backtest)
ENTRY_LIMIT_OFFSET_USD = 0.50    # limit order offset behind level

# ── Position Sizing ────────────────────────────────────────────────────────────
RISK_PCT = 0.01                  # 1% of account per trade (fixed for v2.0)

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
BACKTEST_START = "2020-01-01"
BACKTEST_END = "2023-12-31"
BACKTEST_HOLDOUT_START = "2024-01-01"   # out-of-sample, never touched during dev
BACKTEST_HOLDOUT_END = "2025-12-31"

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
