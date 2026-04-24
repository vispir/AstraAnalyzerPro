"""
MT5 Bridge - File Exchange Version
"""
import os
import time
import json
import logging
import requests
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler('mt5_bridge.log')
    ]
)
logger = logging.getLogger("MT5Bridge")

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
TEST_MODE = os.getenv("TEST_MODE", "true").lower() == "true"
CHECK_INTERVAL = 5

MT5_DATA_PATH = Path("/root/.mt5/drive_c/users/root/AppData/Roaming/MetaQuotes/Terminal/Common/Files")
SIGNALS_FILE = MT5_DATA_PATH / "astra_signals.json"
CANDLES_FILE = MT5_DATA_PATH / "astra_candles.json"

SUPABASE_REST_URL = f"{SUPABASE_URL}/rest/v1"
HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json"
}

def get_new_signals():
    try:
        url = f"{SUPABASE_REST_URL}/mt5_signals?status=eq.new&select=*"
        response = requests.get(url, headers=HEADERS)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        logger.error(f"Error getting signals: {e}")
        return []

def write_signals_to_file(signals):
    try:
        MT5_DATA_PATH.mkdir(parents=True, exist_ok=True)
        data = []
        for s in signals:
            data.append({
                'id': s['id'],
                'direction': s['direction'],
                'entry': float(s['entry']),
                'sl': float(s['sl']),
                'tp': float(s['tp']),
                'risk_usd': float(s['risk_usd'])
            })
        with open(SIGNALS_FILE, 'w') as f:
            json.dump(data, f)
        logger.info(f"Wrote {len(data)} signals")
        return True
    except Exception as e:
        logger.error(f"Error writing signals: {e}")
        return False

def sync_candles(candles):
    try:
        # Используем upsert через URL параметр
        url = f"{SUPABASE_REST_URL}/mt5_candles?on_conflict=symbol,timeframe,time"
        headers = HEADERS.copy()
        headers["Prefer"] = "resolution=merge-duplicates"

        response = requests.post(url, headers=headers, json=candles)
        response.raise_for_status()
        logger.info(f"Synced {len(candles)} candles")
        return True
    except Exception as e:
        logger.error(f"Error syncing candles: {e}")
        return False

def read_candles_from_file():
    try:
        if not CANDLES_FILE.exists():
            return None

        # Читаем как binary и пробуем разные кодировки
        with open(CANDLES_FILE, 'rb') as f:
            raw_data = f.read()

        # Убираем BOM если есть
        if raw_data.startswith(b'\xff\xfe'):  # UTF-16 LE BOM
            text = raw_data.decode('utf-16-le')
        elif raw_data.startswith(b'\xfe\xff'):  # UTF-16 BE BOM
            text = raw_data.decode('utf-16-be')
        elif raw_data.startswith(b'\xef\xbb\xbf'):  # UTF-8 BOM
            text = raw_data[3:].decode('utf-8')
        else:
            text = raw_data.decode('utf-8')

        text = text.lstrip('\ufeff')
        candles = json.loads(text)
        
        if not candles or len(candles) == 0:
            return None

        candles_data = []
        for candle in candles:
            candles_data.append({
                'symbol': 'XAUUSD',
                'timeframe': 'M15',
                'time': candle['time'].replace('.', '-').replace(' ', 'T') + ':00Z',
                'open': float(candle['open']),
                'high': float(candle['high']),
                'low': float(candle['low']),
                'close': float(candle['close']),
                'volume': int(candle['volume'])
            })
        return candles_data
    except Exception as e:
        logger.error(f"Error reading candles: {e}")
        return None

def should_sync_candles():
    """Check if current time is 15 seconds after M15 candle close (00/15/30/45)"""
    from datetime import datetime
    now = datetime.utcnow()
    minute = now.minute
    second = now.second

    # Sync at 15 seconds after each M15 close (00:15, 15:15, 30:15, 45:15)
    if minute % 15 == 0 and 15 <= second <= 20:
        return True
    return False

def main():
    logger.info("="*60)
    logger.info("MT5 Bridge Starting")
    logger.info(f"Mode: {'TEST' if TEST_MODE else 'LIVE'}")
    logger.info(f"MT5 Path: {MT5_DATA_PATH}")
    logger.info("="*60)

    last_sync_minute = -1

    try:
        while True:
            from datetime import datetime
            now = datetime.utcnow()
            current_minute = now.minute

            # Sync candles at 15 seconds after M15 close (00/15/30/45)
            if should_sync_candles() and current_minute != last_sync_minute:
                logger.info("Syncing candles from MT5...")
                candles = read_candles_from_file()
                if candles:
                    logger.info(f"Found {len(candles)} candles")
                    sync_candles(candles)
                last_sync_minute = current_minute

            # Check signals every 5 seconds
            signals = get_new_signals()
            if signals:
                logger.info(f"Found {len(signals)} new signals")
                write_signals_to_file(signals)

            time.sleep(CHECK_INTERVAL)
    except KeyboardInterrupt:
        logger.info("Stopped")

if __name__ == "__main__":
    main()
