"""
Session Breakout Live Trader v3.0 - FINAL
==========================================
LONG: Asian (7-10) + London (13-16) only, simple windows, ATR=14
SHORT: Type1 + Type2 Reversal, ATR=14
NO NY SESSION for LONG (убрана из-за убыточности)

Параметры:
- Risk: $158
- TP: 5.5R
- Step Trailing: Управляется MT5 EA
- H4 EMA20 filter: Enabled

Validated: $69,520 PnL, 557 trades, DD 6.65%

Вызывается через Render Cron каждые 15 минут
"""

import os
import sys
import logging
from datetime import datetime, timedelta, timezone
import pandas as pd
import numpy as np

# Добавляем путь к проекту
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from astra_v2.data.dukascopy import load_timeframe
from astra_v2.mt5.mt5_signal_writer import write_signal, get_active_signal
from services.telegram_service import telegram_service
import requests

logger = logging.getLogger("SessionBreakout")

# Supabase для чтения свечей
SUPABASE_URL = os.getenv("SUPABASE_URL", "https://your-project.supabase.co")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "your-anon-key")
SUPABASE_REST_URL = f"{SUPABASE_URL}/rest/v1"
HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json"
}

# ============================================================================
# VALIDATED PARAMETERS (NO NY SESSION)
# ============================================================================

RISK_PER_TRADE = 158
TP_RR = 5.5
USE_H4_EMA_FILTER = True
H4_EMA_PERIOD = 20
ATR_PERIOD = 14  # Unified ATR for LONG and SHORT
ATR_BUFFER = 0.5

# LONG: Simple session windows (NO NY!)
LONG_SESSIONS = {
    'asian': (7, 10),
    'london': (13, 16)
}

# SHORT: Reversal parameters
SHORT_TYPE1_LOOKBACK_H4_BARS = 5
SHORT_TYPE2_H4_LOOKBACK = 3
SHORT_TYPE2_ATR_MULTIPLIER = 2.0

# SHORT State Machine (persistent across M15 bars)
short_type1_reversal_active = False
short_type1_reversal_h4_high = None
short_type2_reversal_active = False
short_type2_reversal_h4_high = None
last_h4_index = None

# LONG State Machine (persistent across M15 bars)
session_highs = {}
session_lows = {}
last_trading_date = None

# ============================================================================
# HELPER FUNCTIONS
# ============================================================================

def load_candles_from_supabase(symbol='XAUUSD', timeframe='M15', limit=500):
    """Загрузить свечи из Supabase"""
    try:
        url = f"{SUPABASE_REST_URL}/mt5_candles?symbol=eq.{symbol}&timeframe=eq.{timeframe}&order=time.desc&limit={limit}"
        response = requests.get(url, headers=HEADERS)
        response.raise_for_status()

        data = response.json()

        if not data or len(data) == 0:
            logger.warning(f"No candles found in Supabase for {symbol} {timeframe}")
            return None

        df = pd.DataFrame(data)
        df['time'] = pd.to_datetime(df['time'])
        df = df.set_index('time')
        df = df.sort_index()
        df = df[['open', 'high', 'low', 'close', 'volume']]

        logger.info(f"✓ Loaded {len(df)} candles from Supabase")
        return df

    except Exception as e:
        logger.error(f"Error loading candles from Supabase: {e}")
        return None

def calculate_atr(df, period=14):
    """Calculate ATR"""
    high = df['high']
    low = df['low']
    close = df['close']

    tr1 = high - low
    tr2 = abs(high - close.shift(1))
    tr3 = abs(low - close.shift(1))

    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    atr = tr.rolling(window=period).mean()

    return atr

def calculate_ema(df, period):
    """Calculate EMA"""
    return df['close'].ewm(span=period, adjust=False).mean()

# ============================================================================
# LONG STRATEGY (SIMPLE SESSION WINDOWS - NO NY)
# ============================================================================

def check_long_session_breakout(today_data, df_h4, current_hour, current_time):
    """
    LONG Session Breakout - Simple windows WITHOUT NY
    Tracks session high/low during session hours, enters on breakout after session ends
    """
    global session_highs, session_lows, last_trading_date

    # Reset session tracking on new day
    current_date = current_time.date()
    if last_trading_date != current_date:
        session_highs = {}
        session_lows = {}
        last_trading_date = current_date

    if len(today_data) == 0:
        return None

    last_bar = today_data.iloc[-1]
    last_close = last_bar['close']
    last_high = last_bar['high']
    last_low = last_bar['low']
    last_atr = last_bar['atr']

    if pd.isna(last_atr) or last_atr == 0:
        return None

    # Track session highs/lows during session hours
    for session_name, (start_hour, end_hour) in LONG_SESSIONS.items():
        if start_hour <= current_hour < end_hour:
            if session_name not in session_highs:
                session_highs[session_name] = last_high
                session_lows[session_name] = last_low
            else:
                session_highs[session_name] = max(session_highs[session_name], last_high)
                session_lows[session_name] = min(session_lows[session_name], last_low)

    # Check for breakout after session ends
    for session_name, (start_hour, end_hour) in LONG_SESSIONS.items():
        if session_name in session_highs and current_hour >= end_hour:
            session_high = session_highs[session_name]
            session_low = session_lows[session_name]

            # Breakout above session high
            if last_close > session_high:
                # H4 EMA20 filter
                if USE_H4_EMA_FILTER and df_h4 is not None:
                    h4_bars = df_h4[df_h4.index <= current_time]
                    if len(h4_bars) > 0:
                        current_h4 = h4_bars.iloc[-1]
                        if pd.isna(current_h4['ema20']):
                            continue
                        if current_h4['close'] < current_h4['ema20']:
                            logger.info(f"{session_name}: H4 close below EMA20 - no LONG")
                            continue

                entry = last_close
                sl = session_low - ATR_BUFFER * last_atr
                risk = entry - sl

                if risk <= 0:
                    continue

                tp = entry + risk * TP_RR

                logger.info(f"LONG {session_name.upper()}: Entry {entry:.2f}, SL {sl:.2f}, TP {tp:.2f}")

                try:
                    signal = write_signal(
                        direction='LONG',
                        entry=entry,
                        sl=sl,
                        tp=tp,
                        session=session_name,
                        risk_usd=RISK_PER_TRADE
                    )

                    if signal:
                        logger.info(f"✓✓✓ SIGNAL WRITTEN: LONG {session_name.upper()} @ {entry:.2f}")
                        telegram_service.send_session_breakout_signal(signal, test_mode=False)

                        # Clear session tracking
                        del session_highs[session_name]
                        del session_lows[session_name]

                        return signal

                except Exception as e:
                    logger.error(f"Error writing LONG signal: {e}")

    return None

# ============================================================================
# SHORT STRATEGY (VALIDATED LOGIC WITH STATE MACHINE)
# ============================================================================

def check_short_reversal(today_data, df_h4, current_hour):
    """
    SHORT Reversal - VALIDATED LOGIC WITH STATE MACHINE
    Type 1: Reversal After Historical High (new high over 5 H4 bars, then reversal)
    Type 2: Local Reversal After Strong Move (2+ ATR move in 3 H4 bars, then reversal)
    """
    global short_type1_reversal_active, short_type1_reversal_h4_high
    global short_type2_reversal_active, short_type2_reversal_h4_high
    global last_h4_index

    # Active hours: 00:00-21:00 UTC
    if current_hour < 0 or current_hour >= 21:
        return None

    if len(today_data) == 0:
        return None

    # Get current bar
    last_bar = today_data.iloc[-1]
    last_close = last_bar['close']
    last_atr = last_bar['atr']

    if pd.isna(last_atr) or last_atr == 0:
        return None

    # Get H4 data
    if df_h4 is None or len(df_h4) < SHORT_TYPE1_LOOKBACK_H4_BARS + 2:
        return None

    current_time = today_data.index[-1]
    h4_bars = df_h4[df_h4.index <= current_time]

    if len(h4_bars) < SHORT_TYPE1_LOOKBACK_H4_BARS + 2:
        return None

    current_h4 = h4_bars.iloc[-1]
    prev_h4 = h4_bars.iloc[-2]

    # Check if we're on a new H4 bar
    current_h4_index = current_h4.name
    if last_h4_index != current_h4_index:
        last_h4_index = current_h4_index

        # H4 EMA20 filter: SHORT only if price BELOW EMA20
        if USE_H4_EMA_FILTER:
            if pd.isna(current_h4['ema20']):
                short_type1_reversal_active = False
                short_type2_reversal_active = False
                return None

            if current_h4['close'] >= current_h4['ema20']:
                # Reset reversal flags if price is above EMA20
                short_type1_reversal_active = False
                short_type2_reversal_active = False
                return None

        # TYPE 1: Reversal After Historical High
        if not short_type1_reversal_active:
            lookback_highs = h4_bars.iloc[-SHORT_TYPE1_LOOKBACK_H4_BARS-1:-1]['high']
            historical_high = lookback_highs.max()

            if current_h4['high'] > historical_high:
                if current_h4['close'] < prev_h4['close']:
                    short_type1_reversal_active = True
                    short_type1_reversal_h4_high = current_h4['high']
                    logger.info(f"SHORT Type1: Reversal detected! New high {current_h4['high']:.2f} > historical {historical_high:.2f}")

        # TYPE 2: Local Reversal After Strong Move
        if not short_type2_reversal_active:
            if len(h4_bars) >= SHORT_TYPE2_H4_LOOKBACK + 1:
                lookback_bars = h4_bars.iloc[-SHORT_TYPE2_H4_LOOKBACK-1:-1]
                price_change = current_h4['high'] - lookback_bars['low'].min()
                h4_atr = current_h4.get('atr', last_atr)

                if not np.isnan(h4_atr) and price_change >= SHORT_TYPE2_ATR_MULTIPLIER * h4_atr:
                    if current_h4['close'] < prev_h4['close']:
                        short_type2_reversal_active = True
                        short_type2_reversal_h4_high = current_h4['high']
                        logger.info(f"SHORT Type2: Strong move {price_change:.2f} > {SHORT_TYPE2_ATR_MULTIPLIER}*ATR, reversal detected")

    # M15 ENTRY LOGIC
    if len(today_data) > 1:
        prev_m15_low = today_data.iloc[-2]['low']

        # Type 1 entry (priority)
        if short_type1_reversal_active and last_close < prev_m15_low:
            entry = last_close
            sl = short_type1_reversal_h4_high + ATR_BUFFER * last_atr
            risk = sl - entry

            if risk > 0:
                tp = entry - risk * TP_RR

                logger.info(f"SHORT Type1: Entry {entry:.2f}, SL {sl:.2f}, TP {tp:.2f}, Risk {risk:.2f}")

                try:
                    signal = write_signal(
                        direction='SHORT',
                        entry=entry,
                        sl=sl,
                        tp=tp,
                        session='Type1_HistoricalHigh',
                        risk_usd=RISK_PER_TRADE
                    )

                    if signal:
                        logger.info(f"✓✓✓ SIGNAL WRITTEN: SHORT Type1 @ {entry:.2f}")
                        telegram_service.send_session_breakout_signal(signal, test_mode=False)
                        short_type1_reversal_active = False
                        return signal

                except Exception as e:
                    logger.error(f"Error writing SHORT Type1 signal: {e}")

        # Type 2 entry (if Type 1 didn't trigger)
        elif short_type2_reversal_active and last_close < prev_m15_low:
            entry = last_close
            sl = short_type2_reversal_h4_high + ATR_BUFFER * last_atr
            risk = sl - entry

            if risk > 0:
                tp = entry - risk * TP_RR

                logger.info(f"SHORT Type2: Entry {entry:.2f}, SL {sl:.2f}, TP {tp:.2f}, Risk {risk:.2f}")

                try:
                    signal = write_signal(
                        direction='SHORT',
                        entry=entry,
                        sl=sl,
                        tp=tp,
                        session='Type2_LocalReversal',
                        risk_usd=RISK_PER_TRADE
                    )

                    if signal:
                        logger.info(f"✓✓✓ SIGNAL WRITTEN: SHORT Type2 @ {entry:.2f}")
                        telegram_service.send_session_breakout_signal(signal, test_mode=False)
                        short_type2_reversal_active = False
                        return signal

                except Exception as e:
                    logger.error(f"Error writing SHORT Type2 signal: {e}")

    return None

# ============================================================================
# MAIN LOGIC
# ============================================================================

def check_session_breakout():
    """
    Main entry point - вызывается каждые 15 минут через Render Cron
    """
    try:
        logger.info("="*80)
        logger.info("Session Breakout Trader v3.0 - NO NY SESSION")
        logger.info("="*80)

        # 1. Check for active trade
        active = get_active_signal()
        if active:
            logger.info(f"✓ Active trade exists (ID: {active['id']}, status: {active['status']})")
            logger.info("Skipping new signal generation")
            return {"success": True, "message": "Active trade exists", "active_signal": active}

        # 2. Load M15 data
        df = load_candles_from_supabase('XAUUSD', 'M15', limit=500)
        if df is None or len(df) == 0:
            logger.error("Failed to load M15 data")
            return {"success": False, "message": "No M15 data"}

        # 3. Calculate indicators
        logger.info(f"✓ Loaded {len(df)} M15 bars")
        df['atr'] = calculate_atr(df, ATR_PERIOD)

        # 4. Resample to H4
        logger.info("Resampling M15 to H4 for EMA20 filter...")
        df_h4 = df.resample('4h').agg({
            'open': 'first',
            'high': 'max',
            'low': 'min',
            'close': 'last'
        }).dropna()

        df_h4['atr'] = calculate_atr(df_h4, ATR_PERIOD)
        df_h4['ema20'] = calculate_ema(df_h4, H4_EMA_PERIOD)
        logger.info(f"✓ Resampled {len(df_h4)} H4 bars, calculated EMA20")

        # 5. Get today's data
        now_utc = datetime.now(timezone.utc)
        today_date = now_utc.date()
        today_data = df[df.index.date == today_date]

        if len(today_data) == 0:
            logger.warning("No data for today")
            return {"success": False, "message": "No data for today"}

        current_hour = now_utc.hour

        logger.info(f"Current UTC time: {now_utc.strftime('%Y-%m-%d %H:%M:%S')}")
        logger.info(f"Current UTC hour: {current_hour}")
        logger.info(f"✓ Today's data: {len(today_data)} bars")

        # Log H4 EMA20 status
        if df_h4 is not None and len(df_h4) > 0:
            h4_bars = df_h4[df_h4.index <= now_utc]
            if len(h4_bars) > 0:
                current_h4 = h4_bars.iloc[-1]
                if not pd.isna(current_h4['ema20']):
                    trend = "UP" if current_h4['close'] > current_h4['ema20'] else "DOWN"
                    logger.info(f"H4 close: {current_h4['close']:.2f}, EMA20: {current_h4['ema20']:.2f}, Trend: {trend}")

        # 6. Check LONG conditions (Asian + London only, NO NY)
        logger.info("Checking LONG session breakout (Asian + London only)...")
        long_signal = check_long_session_breakout(today_data, df_h4, current_hour, now_utc)

        if long_signal:
            logger.info("✓ LONG signal generated")
            return {"success": True, "message": "LONG signal generated", "signal": long_signal}

        # 7. Check SHORT conditions
        logger.info("Checking SHORT reversal conditions...")
        short_signal = check_short_reversal(today_data, df_h4, current_hour)

        if short_signal:
            logger.info("✓ SHORT signal generated")
            return {"success": True, "message": "SHORT signal generated", "signal": short_signal}

        logger.info("✓ No entry conditions met for any session")

        # Отправляем статус в Telegram
        current_price = df['close'].iloc[-1] if len(df) > 0 else 0

        # Определяем текущую сессию для LONG (только Asian и London)
        if 7 <= current_hour < 10:
            current_session = "Asian (7-10 UTC)"
            long_reason = "Tracking session range"
        elif 10 <= current_hour < 13:
            current_session = "Asian Breakout"
            long_reason = "Waiting for breakout"
        elif 13 <= current_hour < 16:
            current_session = "London (13-16 UTC)"
            long_reason = "Tracking session range"
        elif 16 <= current_hour < 18:
            current_session = "London Breakout"
            long_reason = "Waiting for breakout"
        else:
            current_session = "Pause"
            long_reason = "NY session disabled (no LONG 18-21 UTC)"

        # Определяем статус SHORT
        if current_hour >= 21:
            short_reason = "Outside active hours (0-21h)"
            short_ema_status = "Market closed"
        else:
            # Проверяем H4 EMA20
            if df_h4 is not None and len(df_h4) > 0:
                h4_bars = df_h4[df_h4.index <= now_utc]
                if len(h4_bars) > 0:
                    current_h4 = h4_bars.iloc[-1]
                    if not pd.isna(current_h4['ema20']):
                        if current_h4['close'] >= current_h4['ema20']:
                            short_ema_status = "Above EMA20 (no SHORT)"
                            short_reason = "Waiting for downtrend"
                        else:
                            short_ema_status = "Below EMA20 ✓"
                            short_reason = "Monitoring for reversal"
                    else:
                        short_ema_status = "N/A"
                        short_reason = "Calculating EMA20"
                else:
                    short_ema_status = "N/A"
                    short_reason = "Insufficient H4 data"
            else:
                short_ema_status = "N/A"
                short_reason = "Loading data"

        telegram_service.send_session_breakout_status({
            'active_trade': False,
            'signal_generated': False,
            'current_price': f"{current_price:.2f}",
            'current_session': current_session,
            'long_reason': long_reason,
            'short_reason': short_reason,
            'short_ema_status': short_ema_status
        })

        return {"success": True, "message": "No entry conditions met"}

    except Exception as e:
        logger.error(f"Error in check_session_breakout: {e}", exc_info=True)
        return {"success": False, "message": str(e)}

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    result = check_session_breakout()
    print(f"\nResult: {result}")
