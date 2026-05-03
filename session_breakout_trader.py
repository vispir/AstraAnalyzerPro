"""
Session Breakout Live Trader v5.0 — TG Notifications Only (EA_MODE)
======================================================================
EA AstraSessionBreakout_v5.mq5 — торгует самостоятельно.
Этот скрипт: мониторинг активных сигналов + TG уведомления.

LONG : Asian (3-6 UTC) + London (8-11 UTC) + NY (15-18 UTC)
SHORT: Failed Breakout — london_fb (6-9 UTC) + ny_fb (12-15 UTC)
До 5 позиций одновременно (Asian + London + NY + short_ldn + short_ny)

Параметры:
- Risk: $100 per trade
- TP: LONG 12R | london_fb 3R | ny_fb 10R
- ATR: 14  |  H4 EMA20 slope filter (LONG only)
- Step Trailing: управляется EA

Validated: $50,620 PnL, 1336 trades, MaxDD 9.16%, DlyDD 2.72%, AllYrs YES

Вызывается через Render Cron каждые 15 минут (1/16/31/46)
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
from astra_v2.mt5.mt5_signal_writer import write_signal, get_active_signal, update_signal_status
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

def mark_signal_tg_sent(signal_id):
    try:
        url = f"{SUPABASE_REST_URL}/mt5_signals?id=eq.{signal_id}"
        requests.patch(url, headers=HEADERS, json={"tg_sent": True}, timeout=(5, 10))
    except Exception as e:
        logger.error(f"mark_signal_tg_sent error: {e}")

def mark_close_tg_sent(signal_id):
    try:
        url = f"{SUPABASE_REST_URL}/mt5_signals?id=eq.{signal_id}"
        requests.patch(url, headers=HEADERS, json={"close_tg_sent": True}, timeout=(5, 10))
    except Exception as e:
        logger.error(f"mark_close_tg_sent error: {e}")

# ============================================================================
# v4.0 PARAMETERS - MULTIPLE POSITIONS
# ============================================================================

RISK_PER_TRADE = 100  # Risk per trade (EA v5.0)
TP_RR = 12.0          # LONG TP (for display/legacy sim only)
USE_H4_EMA_FILTER = True
H4_EMA_PERIOD = 20
ATR_PERIOD = 14
ATR_BUFFER = 0.5

# LONG: 3 сессии — точные UTC окна (v5.0)
LONG_SESSIONS = {
    'asian': {
        'range_hours': (3, 6),
        'entry_start': 6,
        'entry_end': 24
    },
    'london': {
        'range_hours': (8, 11),
        'entry_start': 11,
        'entry_end': 24
    },
    'ny': {
        'range_hours': (15, 18),
        'entry_start': 18,
        'entry_end': 24
    }
}

# SHORT Failed Breakout sessions (display only — EA handles logic)
FB_SESSIONS_DISPLAY = {
    'short_ldn': {'range_hours': (6,  9),  'tp': 3,  'label': 'London FB (6-9)'},
    'short_ny':  {'range_hours': (12, 15), 'tp': 10, 'label': 'NY FB (12-15)'},
}

TEST_MODE = os.getenv("TEST_MODE", "true").lower() == "true"
EA_MODE   = os.getenv("EA_MODE",   "true").lower() == "true"  # EA generates signals, Python only monitors

# SHORT: Reversal parameters
SHORT_TYPE1_LOOKBACK_H4_BARS = 5
SHORT_TYPE2_H4_LOOKBACK = 3
SHORT_TYPE2_ATR_MULTIPLIER = 2.0

# SHORT State Machine - REMOVED (now in Supabase for stateless Render)
# LONG State Machine - REMOVED (recalculated from today's data each run)

# ============================================================================
# HELPER FUNCTIONS
# ============================================================================

def get_short_state():
    """Загрузить SHORT state machine из Supabase"""
    try:
        url = f"{SUPABASE_REST_URL}/short_state?select=*&order=updated_at.desc&limit=1"
        response = requests.get(url, headers=HEADERS, timeout=(5, 30))
        response.raise_for_status()

        data = response.json()
        if data and len(data) > 0:
            state = data[0]
            return {
                'type1_active': state.get('type1_active', False),
                'type1_h4_high': state.get('type1_h4_high'),
                'type2_active': state.get('type2_active', False),
                'type2_h4_high': state.get('type2_h4_high'),
                'last_h4_index': state.get('last_h4_index')
            }

        # Default state if no record exists
        return {
            'type1_active': False,
            'type1_h4_high': None,
            'type2_active': False,
            'type2_h4_high': None,
            'last_h4_index': None
        }
    except Exception as e:
        logger.error(f"Error loading SHORT state: {e}")
        return {
            'type1_active': False,
            'type1_h4_high': None,
            'type2_active': False,
            'type2_h4_high': None,
            'last_h4_index': None
        }

def save_short_state(type1_active, type1_h4_high, type2_active, type2_h4_high, last_h4_index):
    """Сохранить SHORT state machine в Supabase"""
    try:
        state_data = {
            'id': 1,  # singleton row for upsert
            'type1_active': type1_active,
            'type1_h4_high': float(type1_h4_high) if type1_h4_high is not None else None,
            'type2_active': type2_active,
            'type2_h4_high': float(type2_h4_high) if type2_h4_high is not None else None,
            'last_h4_index': last_h4_index.isoformat() if last_h4_index is not None else None,
            'updated_at': datetime.now(timezone.utc).isoformat()
        }

        # Atomic upsert on id=1 (singleton row)
        upsert_url = f"{SUPABASE_REST_URL}/short_state?on_conflict=id"
        upsert_headers = HEADERS.copy()
        upsert_headers['Prefer'] = 'resolution=merge-duplicates,return=representation'
        response = requests.post(upsert_url, headers=upsert_headers, json=state_data, timeout=(5, 30))
        response.raise_for_status()

        logger.info(f"SHORT state saved: Type1={type1_active}, Type2={type2_active}")
        return True
    except Exception as e:
        logger.error(f"Error saving SHORT state: {e}")
        return False

def load_candles_from_supabase(symbol='XAUUSD', timeframe='M15', limit=2000):
    """Загрузить свечи из Supabase"""
    try:
        url = f"{SUPABASE_REST_URL}/mt5_candles?symbol=eq.{symbol}&timeframe=eq.{timeframe}&order=time.desc&limit={limit}"

        # Add headers to bypass 1000 row limit
        headers = HEADERS.copy()
        headers['Range'] = f'0-{limit-1}'
        headers['Prefer'] = 'count=exact'

        response = requests.get(url, headers=headers, timeout=(5, 30))
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
# TEST MODE: SL/TP SIMULATION
# ============================================================================

def simulate_test_closures(df, active_signals_data):
    """TEST_MODE: check if active signals have hit SL or TP using candle data.
    Returns list of session names that were closed."""
    closed_sessions = []

    for signal in active_signals_data:
        signal_id = signal['id']
        direction = signal['direction']
        sl = float(signal['sl'])
        tp = float(signal['tp'])
        session = signal['session']
        risk_usd = float(signal['risk_usd'])

        try:
            created_ts = pd.Timestamp(signal['created_at'])
            if created_ts.tzinfo is None:
                created_ts = created_ts.tz_localize('UTC')
            else:
                created_ts = created_ts.tz_convert('UTC')
        except Exception:
            logger.error(f"[TEST] Could not parse created_at for signal {signal_id}")
            continue

        # Only check candles written AFTER the signal was placed
        df_idx = df.index
        if df_idx.tzinfo is None:
            df_idx = df_idx.tz_localize('UTC')
        candles_after = df[df_idx > created_ts]

        if len(candles_after) == 0:
            continue

        for idx, candle in candles_after.iterrows():
            if direction == 'LONG':
                if candle['low'] <= sl:
                    pnl = -risk_usd
                    update_signal_status(signal_id, 'closed', exit_price=sl, pnl=pnl)
                    logger.info(f"[TEST] {session.upper()} LONG SL hit @ {sl:.2f}, PnL: ${pnl:.0f}")
                    closed_sessions.append(session)
                    break
                elif candle['high'] >= tp:
                    pnl = risk_usd * TP_RR
                    update_signal_status(signal_id, 'closed', exit_price=tp, pnl=pnl)
                    logger.info(f"[TEST] {session.upper()} LONG TP hit @ {tp:.2f}, PnL: ${pnl:.0f}")
                    closed_sessions.append(session)
                    break
            elif direction == 'SHORT':
                if candle['high'] >= sl:
                    pnl = -risk_usd
                    update_signal_status(signal_id, 'closed', exit_price=sl, pnl=pnl)
                    logger.info(f"[TEST] {session.upper()} SHORT SL hit @ {sl:.2f}, PnL: ${pnl:.0f}")
                    closed_sessions.append(session)
                    break
                elif candle['low'] <= tp:
                    pnl = risk_usd * TP_RR
                    update_signal_status(signal_id, 'closed', exit_price=tp, pnl=pnl)
                    logger.info(f"[TEST] {session.upper()} SHORT TP hit @ {tp:.2f}, PnL: ${pnl:.0f}")
                    closed_sessions.append(session)
                    break

    return closed_sessions


# ============================================================================
# LONG STRATEGY - MULTIPLE SESSIONS (Asian + London + NY)
# ============================================================================

def check_long_session_breakout(today_data, df_h4, current_hour, current_time):
    """
    LONG Session Breakout - Multiple independent sessions
    Each session can have active trade simultaneously
    STATELESS: Recalculates session ranges from today's data each run
    """
    if len(today_data) == 0:
        return [], {}, {}

    # Recalculate session highs/lows from ALL today's bars
    session_highs = {}
    session_lows = {}

    for idx, row in today_data.iterrows():
        bar_hour = idx.hour
        bar_high = row['high']
        bar_low = row['low']

        # Track each session's range
        for session_name, params in LONG_SESSIONS.items():
            start_hour, end_hour = params['range_hours']
            if start_hour <= bar_hour < end_hour:
                if session_name not in session_highs:
                    session_highs[session_name] = bar_high
                    session_lows[session_name] = bar_low
                else:
                    session_highs[session_name] = max(session_highs[session_name], bar_high)
                    session_lows[session_name] = min(session_lows[session_name], bar_low)

    last_bar = today_data.iloc[-1]
    last_close = last_bar['close']
    last_atr = last_bar['atr']

    if pd.isna(last_atr) or last_atr == 0:
        return []

    signals = []

    # Check for breakout in each session independently
    for session_name, params in LONG_SESSIONS.items():
        if session_name not in session_highs:
            continue

        # Check if active trade exists for this session
        try:
            active = get_active_signal(session=session_name)
            if active:
                logger.info(f"{session_name}: Active trade exists, skipping")
                continue
        except Exception as e:
            logger.error(f"{session_name}: Supabase error checking active signal, skipping session: {e}")
            continue

        entry_start = params['entry_start']
        entry_end = params['entry_end']

        if not (entry_start <= current_hour < entry_end):
            continue

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
                    mark_signal_tg_sent(signal['id'])
                    signals.append(signal)

            except Exception as e:
                logger.error(f"Error writing LONG {session_name} signal: {e}")

    return signals, session_highs, session_lows

# ============================================================================
# SHORT STRATEGY (VALIDATED LOGIC WITH STATE MACHINE)
# ============================================================================

def check_short_reversal(today_data, df_h4, current_hour):
    """
    SHORT Reversal - STATELESS with Supabase persistence
    Type 1: Reversal After Historical High
    Type 2: Local Reversal After Strong Move
    """
    # Load state from Supabase
    state = get_short_state()
    short_type1_reversal_active = state['type1_active']
    short_type1_reversal_h4_high = state['type1_h4_high']
    short_type2_reversal_active = state['type2_active']
    short_type2_reversal_h4_high = state['type2_h4_high']
    last_h4_index = state['last_h4_index']

    # Active hours: 00:00-21:00 UTC
    if current_hour < 0 or current_hour >= 21:
        return None

    # Check if SHORT trade already active
    try:
        active = get_active_signal(session='short')
        if active:
            logger.info("SHORT: Active trade exists, skipping")
            return None
    except Exception as e:
        logger.error(f"SHORT: Supabase error checking active signal, skipping: {e}")
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
    state_updated = False

    if last_h4_index is None or pd.Timestamp(last_h4_index) != current_h4_index:
        # New H4 bar detected
        logger.info(f"New H4 bar detected: {current_h4_index}")

        # H4 EMA20 filter: SHORT only if price BELOW EMA20
        if USE_H4_EMA_FILTER:
            if pd.isna(current_h4['ema20']):
                short_type1_reversal_active = False
                short_type2_reversal_active = False
                save_short_state(False, None, False, None, current_h4_index)
                return None

            if current_h4['close'] >= current_h4['ema20']:
                logger.info(f"H4 close {current_h4['close']:.2f} >= EMA20 {current_h4['ema20']:.2f} - no SHORT")
                short_type1_reversal_active = False
                short_type2_reversal_active = False
                save_short_state(False, None, False, None, current_h4_index)
                return None

        # TYPE 1: Reversal After Historical High
        if not short_type1_reversal_active:
            lookback_highs = h4_bars.iloc[-SHORT_TYPE1_LOOKBACK_H4_BARS-1:-1]['high']
            historical_high = lookback_highs.max()

            if current_h4['high'] > historical_high:
                if current_h4['close'] < prev_h4['close']:
                    short_type1_reversal_active = True
                    short_type1_reversal_h4_high = current_h4['high']
                    state_updated = True
                    logger.info(f"SHORT Type1: Reversal detected! New high {current_h4['high']:.2f}")

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
                        state_updated = True
                        logger.info(f"SHORT Type2: Strong move detected, reversal")

        # Save state if updated
        if state_updated or last_h4_index is None or pd.Timestamp(last_h4_index) != current_h4_index:
            save_short_state(
                short_type1_reversal_active,
                short_type1_reversal_h4_high,
                short_type2_reversal_active,
                short_type2_reversal_h4_high,
                current_h4_index
            )

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

                logger.info(f"SHORT Type1: Entry {entry:.2f}, SL {sl:.2f}, TP {tp:.2f}")

                try:
                    signal = write_signal(
                        direction='SHORT',
                        entry=entry,
                        sl=sl,
                        tp=tp,
                        session='short',
                        risk_usd=RISK_PER_TRADE
                    )

                    if signal:
                        logger.info(f"✓✓✓ SIGNAL WRITTEN: SHORT Type1 @ {entry:.2f}")
                        telegram_service.send_session_breakout_signal(signal, test_mode=False)
                        mark_signal_tg_sent(signal['id'])
                        # Reset state after entry
                        save_short_state(False, None, short_type2_reversal_active, short_type2_reversal_h4_high, current_h4_index)
                        return signal

                except Exception as e:
                    logger.error(f"Error writing SHORT Type1 signal: {e}")

        # Type 2 entry
        elif short_type2_reversal_active and last_close < prev_m15_low:
            entry = last_close
            sl = short_type2_reversal_h4_high + ATR_BUFFER * last_atr
            risk = sl - entry

            if risk > 0:
                tp = entry - risk * TP_RR

                logger.info(f"SHORT Type2: Entry {entry:.2f}, SL {sl:.2f}, TP {tp:.2f}")

                try:
                    signal = write_signal(
                        direction='SHORT',
                        entry=entry,
                        sl=sl,
                        tp=tp,
                        session='short',
                        risk_usd=RISK_PER_TRADE
                    )

                    if signal:
                        logger.info(f"✓✓✓ SIGNAL WRITTEN: SHORT Type2 @ {entry:.2f}")
                        telegram_service.send_session_breakout_signal(signal, test_mode=False)
                        mark_signal_tg_sent(signal['id'])
                        # Reset state after entry
                        save_short_state(short_type1_reversal_active, short_type1_reversal_h4_high, False, None, current_h4_index)
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
    v4.0: Поддержка множественных одновременных позиций
    """
    try:
        logger.info("="*80)
        logger.info("Session Breakout Trader v4.0 - MULTIPLE POSITIONS")
        logger.info("="*80)

        # 1. Check for active trades (может быть несколько)
        active_sessions = []
        active_signals_data = []
        for session in ['asian', 'london', 'ny', 'short_ldn', 'short_ny']:
            try:
                active = get_active_signal(session=session)
                if active:
                    active_sessions.append(session)
                    active_signals_data.append(active)
                    logger.info(f"✓ Active {session} trade (ID: {active['id']})")
            except Exception as e:
                logger.error(f"Supabase error checking {session}: {e}")

        if len(active_sessions) > 0:
            logger.info(f"Active sessions: {', '.join(active_sessions)}")

        # 1.5 Notify TG for signals written by EA (tg_sent=false means not yet notified)
        for signal in active_signals_data:
            if not signal.get('tg_sent', True):
                logger.info(f"Sending TG notification for EA signal: {signal['session']} #{signal['id']}")
                telegram_service.send_session_breakout_signal(signal, test_mode=TEST_MODE)
                mark_signal_tg_sent(signal['id'])

        # 1.6 Notify TG for closed signals not yet notified (close_tg_sent=false)
        try:
            close_url = (f"{SUPABASE_REST_URL}/mt5_signals"
                         f"?status=eq.closed&close_tg_sent=eq.false&select=*")
            close_resp = requests.get(close_url, headers=HEADERS, timeout=(5, 10))
            close_resp.raise_for_status()
            for sig in close_resp.json():
                logger.info(f"Sending close TG for #{sig['id']} {sig['session']} {sig['direction']}")
                telegram_service.send_session_breakout_close(sig, test_mode=TEST_MODE)
                mark_close_tg_sent(sig['id'])
        except Exception as e:
            logger.error(f"Error sending close TG notifications: {e}")

        # 2. Load M15 data
        df = load_candles_from_supabase('XAUUSD', 'M15', limit=2000)
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

        # 5.5 TEST_MODE: simulate SL/TP closures for active positions
        if TEST_MODE and active_signals_data:
            logger.info("[TEST] Checking SL/TP for active positions...")
            closed = simulate_test_closures(df, active_signals_data)
            if closed:
                logger.info(f"[TEST] Closed sessions: {closed}")
                # Refresh active_sessions after simulation
                active_sessions = []
                for session in ['asian', 'london', 'ny', 'short_ldn', 'short_ny']:
                    try:
                        active = get_active_signal(session=session)
                        if active:
                            active_sessions.append(session)
                    except Exception:
                        pass

        # Log H4 EMA20 status
        h4_trend_data = None
        if df_h4 is not None and len(df_h4) > 0:
            h4_bars = df_h4[df_h4.index <= now_utc]
            if len(h4_bars) > 0:
                current_h4 = h4_bars.iloc[-1]
                if not pd.isna(current_h4['ema20']):
                    trend = "UP" if current_h4['close'] > current_h4['ema20'] else "DOWN"
                    logger.info(f"H4 close: {current_h4['close']:.2f}, EMA20: {current_h4['ema20']:.2f}, Trend: {trend}")
                    h4_trend_data = {
                        'close': current_h4['close'],
                        'ema20': current_h4['ema20'],
                        'trend': trend
                    }

        # 6 & 7. Signal generation — skipped in EA_MODE (EA handles this directly in MT5)
        session_highs = {}
        session_lows  = {}
        if EA_MODE:
            logger.info("EA_MODE=true — skipping signal generation (EA generates signals)")
            # Still calculate session ranges for TG status display
            for idx, row in today_data.iterrows():
                bar_hour = idx.hour
                for session_name, params in LONG_SESSIONS.items():
                    start_hour, end_hour = params['range_hours']
                    if start_hour <= bar_hour < end_hour:
                        if session_name not in session_highs:
                            session_highs[session_name] = row['high']
                            session_lows[session_name]  = row['low']
                        else:
                            session_highs[session_name] = max(session_highs[session_name], row['high'])
                            session_lows[session_name]  = min(session_lows[session_name],  row['low'])
        else:
            # Legacy Python signal generation (only when EA_MODE=false)
            logger.info("Checking LONG session breakouts (Asian + London + NY)...")
            long_signals, session_highs, session_lows = check_long_session_breakout(today_data, df_h4, current_hour, now_utc)

            logger.info("Checking SHORT reversal conditions...")
            short_signal = check_short_reversal(today_data, df_h4, current_hour)

            all_signals = list(long_signals) + ([short_signal] if short_signal else [])
            if all_signals:
                logger.info(f"✓ {len(all_signals)} signal(s) generated")
                return {"success": True, "message": f"{len(all_signals)} signals generated", "signals": all_signals}

        logger.info("✓ No new entry conditions met")

        # Send status to Telegram
        current_price = df['close'].iloc[-1] if len(df) > 0 else 0

        # Determine current session (UTC windows, v5.0)
        if 3 <= current_hour < 6:
            current_session = "Asian Range (3-6 UTC)"
        elif 6 <= current_hour < 8:
            current_session = "Asian Breakout | London FB Range (6-9)"
        elif 8 <= current_hour < 9:
            current_session = "London Range (8-11) | London FB Range (6-9)"
        elif 9 <= current_hour < 11:
            current_session = "London Range (8-11 UTC)"
        elif 11 <= current_hour < 12:
            current_session = "London Breakout"
        elif 12 <= current_hour < 15:
            current_session = "London Breakout | NY FB Range (12-15)"
        elif 15 <= current_hour < 18:
            current_session = "NY Range (15-18 UTC)"
        elif 18 <= current_hour < 24:
            current_session = "NY Breakout"
        else:
            current_session = "Pause (0-3 UTC)"

        # Compute next cron time (runs at 01/16/31/46)
        cron_minutes = [1, 16, 31, 46]
        next_cron_min = next((m for m in cron_minutes if m > now_utc.minute), None)
        if next_cron_min is not None:
            next_cron_str = now_utc.replace(minute=next_cron_min, second=0, microsecond=0).strftime('%H:%M UTC')
        else:
            next_cron_str = (now_utc + timedelta(hours=1)).replace(minute=1, second=0, microsecond=0).strftime('%H:%M UTC')

        telegram_service.send_session_breakout_status({
            'active_trade': len(active_sessions) > 0,
            'active_sessions': active_sessions,
            'signal_generated': False,
            'current_price': f"{current_price:.2f}",
            'current_session': current_session,
            'h4_trend': h4_trend_data,
            'session_highs': session_highs,
            'session_lows': session_lows,
            'current_hour': current_hour,
            'next_cron': next_cron_str,
        })

        return {"success": True, "message": "No entry conditions met", "active_sessions": active_sessions}

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
