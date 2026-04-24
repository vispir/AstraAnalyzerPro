"""
Session Breakout Live Trader v2.1 - LONG + SHORT
==================================================
Интеграция Session Range Breakout (LONG) + Reversal (SHORT) стратегий с MT5 через Supabase

Параметры:
- Risk: $158 (консервативный для баланса $9,950)
- TP: 5.5R
- Step Trailing: Управляется MT5 EA
- Direction: LONG + SHORT
- H4 EMA20 filter: Enabled (LONG above, SHORT below)

LONG: Session Breakout (Asian/London/NY)
SHORT: Reversal (Type1: Historical High, Type2: Local Reversal)

Вызывается через Render Cron каждые 15 минут
"""

import os
import sys
import logging
from datetime import datetime, timedelta, timezone
import pandas as pd

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
# ПАРАМЕТРЫ СТРАТЕГИИ (из бэктеста v2.1)
# ============================================================================

RISK_PER_TRADE = 158  # Консервативный для баланса $9,950
TP_RR = 5.5
USE_H4_EMA_FILTER = True
H4_EMA_PERIOD = 20
ATR_PERIOD = 20

# Session parameters
ASIAN_PARAMS = {
    'tp_rr': 5.5,
    'stop_buffer_atr': 0.1,
    'min_range_atr': 0.7,
    'max_range_atr': 3.0,
    'range_hours': (0, 7),      # UTC
    'breakout_hours': (7, 10)   # UTC
}

LONDON_PARAMS = {
    'tp_rr': 5.5,
    'stop_buffer_atr': 0.3,
    'min_range_atr': 0.3,
    'max_range_atr': 3.0,
    'range_hours': (7, 12),     # UTC
    'breakout_hours': (13, 16)  # UTC
}

NY_PARAMS = {
    'tp_rr': 5.5,
    'stop_buffer_atr': 0.3,
    'min_range_atr': 0.5,
    'max_range_atr': 3.0,
    'range_hours': (13, 17),    # UTC
    'breakout_hours': (18, 21)  # UTC
}

# SHORT parameters
SHORT_TYPE1_LOOKBACK = 5
SHORT_TYPE2_LOOKBACK = 3
SHORT_TYPE2_ATR_MULT = 2.0

# ============================================================================
# HELPER FUNCTIONS
# ============================================================================

def load_candles_from_supabase(symbol='XAUUSD', timeframe='M15', limit=500):
    """
    Загрузить свечи из Supabase (синхронизированные из MT5)

    Args:
        symbol: Символ (XAUUSD)
        timeframe: Таймфрейм (M15)
        limit: Количество свечей (500 = ~31 H4 баров для EMA20)

    Returns:
        pandas.DataFrame или None
    """
    try:
        url = f"{SUPABASE_REST_URL}/mt5_candles?symbol=eq.{symbol}&timeframe=eq.{timeframe}&order=time.desc&limit={limit}"
        response = requests.get(url, headers=HEADERS)
        response.raise_for_status()

        data = response.json()

        if not data or len(data) == 0:
            logger.warning(f"No candles found in Supabase for {symbol} {timeframe}")
            return None

        # Преобразуем в DataFrame
        df = pd.DataFrame(data)
        df['time'] = pd.to_datetime(df['time'])
        df = df.set_index('time')
        df = df.sort_index()  # Сортируем по времени (от старых к новым)

        # Оставляем только нужные колонки
        df = df[['open', 'high', 'low', 'close', 'volume']]

        logger.info(f"✓ Loaded {len(df)} candles from Supabase")
        return df

    except Exception as e:
        logger.error(f"Error loading candles from Supabase: {e}")
        return None

def calculate_atr(df, period=20):
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
# MAIN LOGIC
# ============================================================================

def check_session_breakout():
    """
    Проверка условий входа для Session Breakout
    Вызывается каждые 15 минут через Render Cron
    """
    try:
        logger.info("="*80)
        logger.info("Session Breakout Trader - Starting check")
        logger.info("="*80)

        # 1. Проверить есть ли активная сделка
        active = get_active_signal()
        if active:
            logger.info(f"✓ Active trade exists (ID: {active['id']}, status: {active['status']})")
            logger.info("Skipping new signal generation")

            # Получаем текущую цену
            df = load_candles_from_supabase('XAUUSD', 'M15', 1)
            current_price = df['close'].iloc[-1] if df is not None and len(df) > 0 else 0

            # Отправляем статус в основной бот
            telegram_service.send_session_breakout_status({
                'active_trade': True,
                'session': active.get('session', 'N/A'),
                'direction': active.get('direction', 'N/A'),
                'current_price': f"{current_price:.2f}"
            })

            return {
                "success": True,
                "message": "Active trade exists",
                "trade_id": active['id']
            }

        logger.info("✓ No active trades - checking for entry conditions")

        # 2. Загрузить M15 данные
        # Сначала пробуем из Supabase (свежие данные из MT5)
        logger.info("Loading M15 data from Supabase (MT5 sync)...")
        df = load_candles_from_supabase('XAUUSD', 'M15', 500)

        # Если нет данных в Supabase - fallback на Dukascopy
        if df is None or len(df) == 0:
            logger.warning("No data in Supabase, falling back to Dukascopy...")
            end_date = datetime.now(timezone.utc)
            start_date = end_date - timedelta(days=3)

            logger.info(f"Loading M15 data from Dukascopy: {start_date.date()} to {end_date.date()}")

            try:
                df = load_timeframe(
                    'M15',
                    start=start_date.strftime('%Y-%m-%d'),
                    end=end_date.strftime('%Y-%m-%d'),
                    symbol='XAUUSD'
                )
            except FileNotFoundError:
                # Fallback для локального теста - используем последние доступные данные
                logger.warning("Current data not available, using latest cached data for testing")
                df = load_timeframe(
                    'M15',
                    start='2026-03-01',
                    end='2026-03-31',
                    symbol='XAUUSD'
                )

        if df is None or len(df) == 0:
            logger.error("Failed to load M15 data")
            return {"success": False, "error": "No M15 data"}

        logger.info(f"✓ Loaded {len(df)} M15 bars")

        # 3. Calculate ATR
        df['atr'] = calculate_atr(df, ATR_PERIOD)

        # 4. Resample to H4 for EMA20 filter
        if USE_H4_EMA_FILTER:
            logger.info("Resampling M15 to H4 for EMA20 filter...")
            df_h4 = df.resample('4h').agg({
                'open': 'first',
                'high': 'max',
                'low': 'min',
                'close': 'last'
            }).dropna()
            df_h4['ema20'] = calculate_ema(df_h4, H4_EMA_PERIOD)
            logger.info(f"✓ Resampled {len(df_h4)} H4 bars, calculated EMA20")
        else:
            df_h4 = None

        # 5. Определить текущую сессию и проверить условия
        current_time = datetime.now(timezone.utc)
        current_hour = current_time.hour

        logger.info(f"Current UTC time: {current_time.strftime('%Y-%m-%d %H:%M:%S')}")
        logger.info(f"Current UTC hour: {current_hour}")

        # Получить данные за сегодня
        today = current_time.date()
        today_data = df[df.index.date == today]

        if len(today_data) == 0:
            logger.warning("No data for today")
            return {"success": False, "error": "No data for today"}

        logger.info(f"✓ Today's data: {len(today_data)} bars")

        # Проверяем каждую сессию
        sessions_to_check = [
            ('asian', ASIAN_PARAMS),
            ('london', LONDON_PARAMS),
            ('ny', NY_PARAMS)
        ]

        for session_name, params in sessions_to_check:
            signal = check_session_entry(
                session_name,
                params,
                today_data,
                df_h4,
                current_hour
            )

            if signal:
                logger.info(f"✓ LONG signal generated for {session_name.upper()} session")
                return signal

        logger.info("✓ No LONG entry conditions met")

        # Check SHORT conditions if no LONG signal
        logger.info("Checking SHORT reversal conditions...")
        short_signal = check_short_reversal(
            today_data,
            df_h4,
            current_hour
        )

        if short_signal:
            logger.info("✓ SHORT signal generated")
            return short_signal

        logger.info("✓ No SHORT entry conditions met")

        # Отправляем статус в основной бот каждые 15 минут
        current_price = df['close'].iloc[-1] if len(df) > 0 else 0
        current_hour = datetime.now(timezone.utc).hour

        # Определяем текущую сессию
        if 0 <= current_hour < 7:
            current_session = "Asian Range"
        elif 7 <= current_hour < 10:
            current_session = "Asian Breakout"
        elif 10 <= current_hour < 13:
            current_session = "London Range"
        elif 13 <= current_hour < 16:
            current_session = "London Breakout"
        elif 16 <= current_hour < 18:
            current_session = "NY Range"
        elif 18 <= current_hour < 21:
            current_session = "NY Breakout"
        else:
            current_session = "None"

        telegram_service.send_session_breakout_status({
            'active_trade': False,
            'signal_generated': False,
            'current_price': f"{current_price:.2f}",
            'current_session': current_session
        })

        return {
            "success": True,
            "message": "No entry conditions met"
        }

    except Exception as e:
        logger.error(f"Error in check_session_breakout: {e}", exc_info=True)
        return {"success": False, "error": str(e)}

def check_session_entry(session_name, params, today_data, df_h4, current_hour):
    """
    Проверка условий входа для конкретной сессии

    Returns:
        dict or None: Signal data if conditions met, None otherwise
    """
    # Проверяем что мы в breakout window
    breakout_start, breakout_end = params['breakout_hours']

    if not (breakout_start <= current_hour < breakout_end):
        logger.debug(f"{session_name}: Not in breakout window ({breakout_start}-{breakout_end}h)")
        return None

    logger.info(f"Checking {session_name.upper()} session (breakout window: {breakout_start}-{breakout_end}h)")

    # Определяем range за сессию
    range_start, range_end = params['range_hours']
    range_data = today_data[
        (today_data.index.hour >= range_start) &
        (today_data.index.hour < range_end)
    ]

    if len(range_data) == 0:
        logger.debug(f"{session_name}: No range data")
        return None

    range_high = range_data['high'].max()
    range_low = range_data['low'].min()
    range_size = range_high - range_low

    logger.info(f"{session_name}: Range {range_low:.2f} - {range_high:.2f} (size: {range_size:.2f})")

    # Получаем последнюю свечу
    last_bar = today_data.iloc[-1]
    last_close = last_bar['close']
    last_atr = last_bar['atr']

    if pd.isna(last_atr) or last_atr == 0:
        logger.debug(f"{session_name}: Invalid ATR")
        return None

    logger.info(f"{session_name}: Last close: {last_close:.2f}, ATR: {last_atr:.2f}")

    # Проверяем размер range
    min_range = params['min_range_atr'] * last_atr
    max_range = params['max_range_atr'] * last_atr

    if not (min_range <= range_size <= max_range):
        logger.info(f"{session_name}: Range size {range_size:.2f} outside limits ({min_range:.2f} - {max_range:.2f})")
        return None

    logger.info(f"{session_name}: ✓ Range size valid")

    # Проверяем H4 EMA20 filter
    if USE_H4_EMA_FILTER and df_h4 is not None:
        current_time = today_data.index[-1]
        h4_bar = df_h4[df_h4.index <= current_time].iloc[-1] if len(df_h4[df_h4.index <= current_time]) > 0 else None

        if h4_bar is None or pd.isna(h4_bar['ema20']):
            logger.info(f"{session_name}: No H4 EMA20 data")
            return None

        h4_close = h4_bar['close']
        h4_ema20 = h4_bar['ema20']

        logger.info(f"{session_name}: H4 close: {h4_close:.2f}, H4 EMA20: {h4_ema20:.2f}")

        # LONG only: H4 close должен быть выше EMA20
        if h4_close <= h4_ema20:
            logger.info(f"{session_name}: H4 close below EMA20 - no LONG signal")
            return None

        logger.info(f"{session_name}: ✓ H4 EMA20 filter passed (uptrend)")

    # Проверяем breakout
    if last_close > range_high:
        logger.info(f"{session_name}: ✓ BREAKOUT detected! Close {last_close:.2f} > Range High {range_high:.2f}")

        # Рассчитываем параметры сделки
        entry = last_close
        sl = range_low - params['stop_buffer_atr'] * last_atr
        risk = entry - sl
        tp = entry + risk * params['tp_rr']

        logger.info(f"{session_name}: Entry: {entry:.2f}, SL: {sl:.2f}, TP: {tp:.2f}")
        logger.info(f"{session_name}: Risk: {risk:.2f}, R:R: {params['tp_rr']}")

        # Генерируем сигнал
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

                # Отправляем сигнал в Signal Bot
                telegram_service.send_session_breakout_signal(signal, test_mode=False)

                # Отправляем статус в основной бот
                telegram_service.send_session_breakout_status({
                    'signal_generated': True,
                    'session': session_name,
                    'direction': 'LONG'
                })

                return {
                    "success": True,
                    "message": f"Signal generated for {session_name}",
                    "signal": signal
                }
            else:
                logger.error(f"Failed to write signal for {session_name}")
                return None

        except Exception as e:
            logger.error(f"Error writing signal for {session_name}: {e}")
            return None

    logger.debug(f"{session_name}: No breakout (close {last_close:.2f} <= range high {range_high:.2f})")
    return None

def check_short_reversal(today_data, df_h4, current_hour):
    """
    Проверка условий SHORT Reversal
    Type 1: Historical High Reversal (5 H4 bars)
    Type 2: Local Reversal (2+ ATR move in 3 H4 bars)

    Returns:
        dict or None: Signal data if conditions met, None otherwise
    """
    # Active hours: 00:00-21:00 UTC
    if current_hour < 0 or current_hour >= 21:
        logger.debug("SHORT: Outside active hours (0-21h)")
        return None

    if len(today_data) == 0:
        logger.debug("SHORT: No today data")
        return None

    # Get current bar
    last_bar = today_data.iloc[-1]
    last_close = last_bar['close']

    # Get H4 data
    if len(df_h4) < SHORT_TYPE1_LOOKBACK + 2:
        logger.debug("SHORT: Not enough H4 data")
        return None

    current_h4 = df_h4.iloc[-1]
    h4_ema20 = current_h4.get('ema20')
    h4_atr = current_h4.get('atr')

    # H4 EMA20 filter: SHORT only below EMA20
    if pd.isna(h4_ema20) or last_close > h4_ema20:
        logger.debug(f"SHORT: Price {last_close:.2f} above H4 EMA20 {h4_ema20:.2f}")
        return None

    logger.info(f"SHORT: Price {last_close:.2f} below H4 EMA20 {h4_ema20:.2f} ✓")

    signal_type = None

    # Type 1: Historical High Reversal
    last_n_h4 = df_h4.tail(SHORT_TYPE1_LOOKBACK)
    if len(last_n_h4) >= SHORT_TYPE1_LOOKBACK:
        last_h4_close = last_n_h4['close'].iloc[-1]
        prev_h4_close = last_n_h4['close'].iloc[-2]

        if last_h4_close < prev_h4_close:
            # Check M15 break below previous M15 low
            last_3_m15 = today_data.tail(3)
            if len(last_3_m15) >= 3:
                m15_low = last_3_m15['low'].min()
                if last_close < m15_low:
                    signal_type = 'Type1_HistoricalHigh'
                    logger.info(f"SHORT Type1: H4 reversal detected, M15 break below {m15_low:.2f}")

    # Type 2: Local Reversal after strong move
    if signal_type is None:
        last_n_h4 = df_h4.tail(SHORT_TYPE2_LOOKBACK + 1)
        if len(last_n_h4) >= SHORT_TYPE2_LOOKBACK + 1 and not pd.isna(h4_atr):
            price_move = last_n_h4['close'].iloc[-1] - last_n_h4['close'].iloc[0]

            if price_move > SHORT_TYPE2_ATR_MULT * h4_atr:
                last_h4_close = last_n_h4['close'].iloc[-1]
                prev_h4_close = last_n_h4['close'].iloc[-2]

                if last_h4_close < prev_h4_close:
                    # Check M15 break below previous M15 low
                    last_3_m15 = today_data.tail(3)
                    if len(last_3_m15) >= 3:
                        m15_low = last_3_m15['low'].min()
                        if last_close < m15_low:
                            signal_type = 'Type2_LocalReversal'
                            logger.info(f"SHORT Type2: Strong move {price_move:.2f} > {SHORT_TYPE2_ATR_MULT}*ATR, M15 break below {m15_low:.2f}")

    if signal_type is None:
        logger.debug("SHORT: No reversal signal detected")
        return None

    # Calculate trade parameters
    entry = last_close
    sl = entry + h4_atr
    risk = sl - entry
    tp = entry - risk * TP_RR

    logger.info(f"SHORT {signal_type}: Entry: {entry:.2f}, SL: {sl:.2f}, TP: {tp:.2f}")
    logger.info(f"SHORT: Risk: {risk:.2f}, R:R: {TP_RR}")

    # Generate signal
    try:
        signal = write_signal(
            direction='SHORT',
            entry=entry,
            sl=sl,
            tp=tp,
            session=signal_type,
            risk_usd=RISK_PER_TRADE
        )

        if signal:
            logger.info(f"✓✓✓ SIGNAL WRITTEN: SHORT {signal_type} @ {entry:.2f}")

            # Send to Signal Bot
            telegram_service.send_session_breakout_signal(signal, test_mode=False)

            # Send status to main bot
            telegram_service.send_session_breakout_status({
                'signal_generated': True,
                'session': signal_type,
                'direction': 'SHORT'
            })

            return {
                "success": True,
                "message": f"SHORT signal generated: {signal_type}",
                "signal": signal
            }
        else:
            logger.error(f"Failed to write SHORT signal")
            return None

    except Exception as e:
        logger.error(f"Error writing SHORT signal: {e}")
        return None

# ============================================================================
# ENTRY POINT
# ============================================================================

if __name__ == "__main__":
    # Настройка логирования для тестирования
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )

    result = check_session_breakout()
    print("\n" + "="*80)
    print("RESULT:")
    print(result)
    print("="*80)
