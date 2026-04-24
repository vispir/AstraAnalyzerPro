"""
MT5 Bridge - VPS Script (File Exchange Version)
Работает через файловый обмен с MT5 EA (без MetaTrader5 library)

Требования:
- Python 3.8+
- requests
- python-dotenv
- MT5 EA (AstraSessionBreakout.mq5) установлен в MT5

Файловый обмен:
- signals.json: Python → MT5 (новые сигналы)
- candles.json: MT5 → Python (свежие свечи)
- trades.json: MT5 → Python (статус сделок)
"""

import os
import sys
import time
import json
import logging
import requests
from datetime import datetime, timezone
from pathlib import Path
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

# Путь к MT5 файлам (Wine)
MT5_DATA_PATH = Path.home() / ".wine/drive_c/Program Files/MetaTrader 5/MQL5/Files"

# Файлы обмена
SIGNALS_FILE = MT5_DATA_PATH / "astra_signals.json"
CANDLES_FILE = MT5_DATA_PATH / "astra_candles.json"
TRADES_FILE = MT5_DATA_PATH / "astra_trades.json"

# Supabase REST API
SUPABASE_REST_URL = f"{SUPABASE_URL}/rest/v1"
HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
    "Prefer": "return=representation"
}

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

def sync_candles_to_supabase(candles_data):
    """Отправить свечи в Supabase"""
    try:
        if not candles_data or len(candles_data) == 0:
            return False

        url = f"{SUPABASE_REST_URL}/mt5_candles"
        headers_upsert = HEADERS.copy()
        headers_upsert['Prefer'] = 'resolution=merge-duplicates'

        response = requests.post(url, headers=headers_upsert, json=candles_data)
        response.raise_for_status()

        logger.info(f"✓ Synced {len(candles_data)} candles to Supabase")
        return True

    except Exception as e:
        logger.error(f"Error syncing candles: {e}")
        return False

# ============================================================================
# FILE EXCHANGE FUNCTIONS
# ============================================================================

def write_signals_to_file(signals):
    """Записать сигналы в файл для MT5 EA"""
    try:
        # Создаем директорию если не существует
        MT5_DATA_PATH.mkdir(parents=True, exist_ok=True)

        # Преобразуем сигналы в формат для EA
        signals_for_ea = []
        for signal in signals:
            signals_for_ea.append({
                'id': signal['id'],
                'direction': signal['direction'],
                'entry': float(signal['entry']),
                'sl': float(signal['sl']),
                'tp': float(signal['tp']),
                'session': signal['session'],
                'risk_usd': float(signal['risk_usd']),
                'test_mode': TEST_MODE
            })

        # Записываем в файл
        with open(SIGNALS_FILE, 'w') as f:
            json.dump(signals_for_ea, f, indent=2)

        logger.info(f"✓ Wrote {len(signals_for_ea)} signals to {SIGNALS_FILE}")
        return True

    except Exception as e:
        logger.error(f"Error writing signals to file: {e}")
        return False

def read_candles_from_file():
    """Прочитать свечи из файла (записанные MT5 EA)"""
    try:
        if not CANDLES_FILE.exists():
            return None

        with open(CANDLES_FILE, 'r') as f:
            candles = json.load(f)

        if not candles or len(candles) == 0:
            return None

        # Преобразуем в формат для Supabase
        candles_data = []
        for candle in candles:
            candles_data.append({
                'symbol': 'XAUUSD',
                'timeframe': 'M15',
                'time': candle['time'],
                'open': float(candle['open']),
                'high': float(candle['high']),
                'low': float(candle['low']),
                'close': float(candle['close']),
                'volume': int(candle['volume'])
            })

        return candles_data

    except Exception as e:
        logger.error(f"Error reading candles from file: {e}")
        return None

def read_trades_from_file():
    """Прочитать статус сделок из файла (записанные MT5 EA)"""
    try:
        if not TRADES_FILE.exists():
            return None

        with open(TRADES_FILE, 'r') as f:
            trades = json.load(f)

        return trades

    except Exception as e:
        logger.error(f"Error reading trades from file: {e}")
        return None

# ============================================================================
# MAIN LOOP
# ============================================================================

def main():
    logger.info("="*80)
    logger.info("MT5 Bridge - Starting (File Exchange Mode)")
    logger.info("="*80)
    logger.info(f"Mode: {'TEST (no real trades)' if TEST_MODE else 'LIVE (real trades)'}")
    logger.info(f"Check interval: {CHECK_INTERVAL}s")
    logger.info(f"MT5 Files Path: {MT5_DATA_PATH}")

    # Проверяем доступ к директории MT5
    if not MT5_DATA_PATH.exists():
        logger.warning(f"MT5 Files directory not found: {MT5_DATA_PATH}")
        logger.warning("Creating directory...")
        MT5_DATA_PATH.mkdir(parents=True, exist_ok=True)

    logger.info("✓ Bridge ready. Monitoring for signals...")

    candle_sync_counter = 0
    CANDLE_SYNC_INTERVAL = 180  # 15 минут

    try:
        while True:
            # 1. Синхронизация свечей (каждые 15 минут)
            if candle_sync_counter >= CANDLE_SYNC_INTERVAL:
                candles = read_candles_from_file()
                if candles:
                    logger.info(f"Found {len(candles)} candles from MT5 EA")
                    sync_candles_to_supabase(candles)
                candle_sync_counter = 0
            else:
                candle_sync_counter += 1

            # 2. Проверяем новые сигналы из Supabase
            new_signals = get_new_signals()

            if new_signals and len(new_signals) > 0:
                logger.info(f"Found {len(new_signals)} new signals from Supabase")

                # Записываем в файл для MT5 EA
                if write_signals_to_file(new_signals):
                    # Обновляем статус в Supabase (EA обработает)
                    for signal in new_signals:
                        update_signal_status(signal['id'], 'pending')

            # 3. Проверяем статус сделок из MT5 EA
            trades = read_trades_from_file()
            if trades:
                for trade in trades:
                    signal_id = trade.get('signal_id')
                    status = trade.get('status')
                    ticket = trade.get('ticket')

                    if signal_id and status:
                        update_signal_status(signal_id, status, ticket=ticket)

            # Ждем перед следующей проверкой
            time.sleep(CHECK_INTERVAL)

    except KeyboardInterrupt:
        logger.info("Bridge stopped by user")
    except Exception as e:
        logger.error(f"Unexpected error: {e}", exc_info=True)

if __name__ == "__main__":
    main()
