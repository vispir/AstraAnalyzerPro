"""
MT5 Bridge - VPS Script
Читает сигналы из Supabase и управляет MT5 через Wine

Требования:
- Python 3.8+
- MetaTrader5 library
- requests
- MT5 запущен через Wine

Установка на VPS:
1. pip install MetaTrader5 requests python-dotenv
2. Создать .env файл с SUPABASE_URL и SUPABASE_KEY
3. Запустить: python mt5_bridge.py

Режимы:
- TEST_MODE=True: только логирует, не открывает сделки
- TEST_MODE=False: открывает реальные сделки
"""

import os
import sys
import time
import logging
import requests
from datetime import datetime, timezone
from dotenv import load_dotenv

# Загружаем переменные окружения
load_dotenv()

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler('mt5_bridge.log', encoding='utf-8')
    ]
)
logger = logging.getLogger("MT5Bridge")

# Конфигурация
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
TEST_MODE = os.getenv("TEST_MODE", "true").lower() == "true"
CHECK_INTERVAL = 5  # Проверка каждые 5 секунд

# Supabase REST API
SUPABASE_REST_URL = f"{SUPABASE_URL}/rest/v1"
HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
    "Prefer": "return=representation"
}

# MT5 параметры
SYMBOL = "XAUUSD"
MAGIC_NUMBER = 20241121  # Уникальный ID для Session Breakout EA
DEVIATION = 20

# Импорт MT5
try:
    import MetaTrader5 as mt5
    MT5_AVAILABLE = True
except ImportError:
    logger.error("MetaTrader5 library not installed. Run: pip install MetaTrader5")
    MT5_AVAILABLE = False

# ============================================================================
# SUPABASE FUNCTIONS
# ============================================================================

def get_new_signals():
    """Получить новые сигналы из Supabase"""
    try:
        url = f"{SUPABASE_REST_URL}/mt5_signals?status=eq.new&select=*&order=created_at.desc"
        response = requests.get(url, headers=HEADERS)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        logger.error(f"Error getting new signals: {e}")
        return []

def update_signal_status(signal_id, status, ticket=None, error=None):
    """Обновить статус сигнала"""
    try:
        update_data = {'status': status}
        if ticket:
            update_data['mt5_ticket'] = ticket
        if error:
            update_data['error'] = error

        url = f"{SUPABASE_REST_URL}/mt5_signals?id=eq.{signal_id}"
        response = requests.patch(url, headers=HEADERS, json=update_data)
        response.raise_for_status()
        logger.info(f"Signal {signal_id} updated to status: {status}")
        return True
    except Exception as e:
        logger.error(f"Error updating signal status: {e}")
        return False

def get_active_signals():
    """Получить активные сигналы"""
    try:
        url = f"{SUPABASE_REST_URL}/mt5_signals?status=eq.active&select=*"
        response = requests.get(url, headers=HEADERS)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        logger.error(f"Error getting active signals: {e}")
        return []

def sync_candles_to_supabase():
    """
    Синхронизация свечей M15 из MT5 в Supabase
    Отправляет последние 300 свечей M15 (3+ дня)
    """
    if TEST_MODE:
        logger.debug("[TEST MODE] Skipping candle sync")
        return True

    if not MT5_AVAILABLE:
        logger.error("MT5 not available for candle sync")
        return False

    try:
        # Получаем последние 300 свечей M15 из MT5
        candles = mt5.copy_rates_from_pos(SYMBOL, mt5.TIMEFRAME_M15, 0, 300)

        if candles is None or len(candles) == 0:
            logger.error("Failed to get candles from MT5")
            return False

        # Преобразуем в формат для Supabase
        candles_data = []
        for candle in candles:
            candles_data.append({
                'symbol': SYMBOL,
                'timeframe': 'M15',
                'time': datetime.fromtimestamp(candle['time'], tz=timezone.utc).isoformat(),
                'open': float(candle['open']),
                'high': float(candle['high']),
                'low': float(candle['low']),
                'close': float(candle['close']),
                'volume': int(candle['tick_volume'])
            })

        # Отправляем в Supabase (upsert - обновляет существующие или создает новые)
        url = f"{SUPABASE_REST_URL}/mt5_candles"
        headers_upsert = HEADERS.copy()
        headers_upsert['Prefer'] = 'resolution=merge-duplicates'

        response = requests.post(url, headers=headers_upsert, json=candles_data)
        response.raise_for_status()

        logger.info(f"✓ Synced {len(candles_data)} M15 candles to Supabase")
        return True

    except Exception as e:
        logger.error(f"Error syncing candles: {e}")
        return False


# ============================================================================
# MT5 FUNCTIONS
# ============================================================================

def init_mt5():
    """Инициализация MT5"""
    if not MT5_AVAILABLE:
        logger.error("MT5 library not available")
        return False

    if not mt5.initialize():
        logger.error(f"MT5 initialization failed: {mt5.last_error()}")
        return False

    # Проверяем подключение
    account_info = mt5.account_info()
    if account_info is None:
        logger.error("Failed to get account info")
        return False

    logger.info(f"✓ MT5 initialized")
    logger.info(f"  Account: {account_info.login}")
    logger.info(f"  Balance: ${account_info.balance:.2f}")
    logger.info(f"  Server: {account_info.server}")

    return True

def open_trade(signal):
    """Открыть сделку по сигналу"""
    if TEST_MODE:
        logger.info(f"[TEST MODE] Would open trade: {signal['direction']} @ {signal['entry']}")
        return 999999  # Fake ticket для теста

    if not MT5_AVAILABLE:
        logger.error("MT5 not available")
        return None

    try:
        direction = signal['direction']
        entry = signal['entry']
        sl = signal['sl']
        tp = signal['tp']
        risk_usd = signal['risk_usd']

        # Определяем тип ордера
        order_type = mt5.ORDER_TYPE_BUY if direction == "LONG" else mt5.ORDER_TYPE_SELL

        # Рассчитываем лот
        symbol_info = mt5.symbol_info(SYMBOL)
        if symbol_info is None:
            logger.error(f"Symbol {SYMBOL} not found")
            return None

        # Риск в пунктах
        risk_points = abs(entry - sl)
        # Стоимость 1 пункта для 1 лота
        point_value = symbol_info.trade_tick_value
        # Лот = риск_в_долларах / (риск_в_пунктах * стоимость_пункта)
        lot = risk_usd / (risk_points * point_value)
        lot = round(lot, 2)

        # Проверяем минимальный/максимальный лот
        if lot < symbol_info.volume_min:
            lot = symbol_info.volume_min
        if lot > symbol_info.volume_max:
            lot = symbol_info.volume_max

        logger.info(f"Opening {direction} trade:")
        logger.info(f"  Entry: {entry}, SL: {sl}, TP: {tp}")
        logger.info(f"  Lot: {lot}, Risk: ${risk_usd}")

        # Формируем запрос
        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": SYMBOL,
            "volume": lot,
            "type": order_type,
            "price": entry,
            "sl": sl,
            "tp": tp,
            "deviation": DEVIATION,
            "magic": MAGIC_NUMBER,
            "comment": f"SessionBreakout_{signal['session']}",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }

        # Отправляем ордер
        result = mt5.order_send(request)

        if result.retcode != mt5.TRADE_RETCODE_DONE:
            logger.error(f"Order failed: {result.retcode} - {result.comment}")
            return None

        logger.info(f"✓ Trade opened: ticket #{result.order}")
        return result.order

    except Exception as e:
        logger.error(f"Error opening trade: {e}")
        return None

def check_active_trades():
    """Проверить активные сделки и обновить статус в Supabase"""
    if TEST_MODE:
        return

    if not MT5_AVAILABLE:
        return

    try:
        # Получаем активные позиции
        positions = mt5.positions_get(symbol=SYMBOL)
        if positions is None:
            return

        # Получаем активные сигналы из Supabase
        active_signals = get_active_signals()

        for signal in active_signals:
            ticket = signal.get('mt5_ticket')
            if not ticket:
                continue

            # Проверяем есть ли позиция
            position_exists = any(p.ticket == ticket for p in positions)

            if not position_exists:
                # Позиция закрыта - обновляем статус
                logger.info(f"Position {ticket} closed, updating Supabase...")
                update_signal_status(signal['id'], 'closed')

    except Exception as e:
        logger.error(f"Error checking active trades: {e}")

# ============================================================================
# MAIN LOOP
# ============================================================================

def main():
    logger.info("="*80)
    logger.info("MT5 Bridge - Starting")
    logger.info("="*80)
    logger.info(f"Mode: {'TEST (no real trades)' if TEST_MODE else 'LIVE (real trades)'}")
    logger.info(f"Check interval: {CHECK_INTERVAL}s")

    # Инициализация MT5
    if not TEST_MODE:
        if not init_mt5():
            logger.error("Failed to initialize MT5. Exiting.")
            return

    logger.info("✓ Bridge ready. Monitoring for signals...")

    # Счетчик для синхронизации свечей (каждые 15 минут = 180 итераций по 5 сек)
    candle_sync_counter = 0
    CANDLE_SYNC_INTERVAL = 180  # 15 минут / 5 секунд = 180 итераций

    try:
        while True:
            # 1. Синхронизация свечей (каждые 15 минут)
            if candle_sync_counter >= CANDLE_SYNC_INTERVAL:
                logger.info("Syncing candles from MT5 to Supabase...")
                sync_candles_to_supabase()
                candle_sync_counter = 0
            else:
                candle_sync_counter += 1

            # 2. Проверяем новые сигналы
            new_signals = get_new_signals()

            for signal in new_signals:
                logger.info(f"New signal detected: {signal['direction']} {signal['session'].upper()}")

                # Открываем сделку
                ticket = open_trade(signal)

                if ticket:
                    # Обновляем статус в Supabase
                    update_signal_status(signal['id'], 'active', ticket=ticket)
                    logger.info(f"✓ Signal {signal['id']} activated with ticket #{ticket}")
                else:
                    # Ошибка открытия
                    update_signal_status(signal['id'], 'error', error="Failed to open trade")

            # 2. Проверяем активные сделки
            check_active_trades()

            # Ждем перед следующей проверкой
            time.sleep(CHECK_INTERVAL)

    except KeyboardInterrupt:
        logger.info("Bridge stopped by user")
    except Exception as e:
        logger.error(f"Unexpected error: {e}", exc_info=True)
    finally:
        if not TEST_MODE and MT5_AVAILABLE:
            mt5.shutdown()
            logger.info("MT5 connection closed")

if __name__ == "__main__":
    main()
