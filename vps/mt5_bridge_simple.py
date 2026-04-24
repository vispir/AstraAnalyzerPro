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

MT5_DATA_PATH = Path.home() / ".wine/drive_c/Program Files/MetaTrader 5/MQL5/Files"
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
        url = f"{SUPABASE_REST_URL}/mt5_candles"
        response = requests.post(url, headers=HEADERS, json=candles)
        response.raise_for_status()
        logger.info(f"Synced {len(candles)} candles")
        return True
    except Exception as e:
        logger.error(f"Error syncing candles: {e}")
        return False

def main():
    logger.info("="*60)
    logger.info("MT5 Bridge Starting")
    logger.info(f"Mode: {'TEST' if TEST_MODE else 'LIVE'}")
    logger.info(f"MT5 Path: {MT5_DATA_PATH}")
    logger.info("="*60)

    try:
        while True:
            signals = get_new_signals()
            if signals:
                logger.info(f"Found {len(signals)} new signals")
                write_signals_to_file(signals)
            time.sleep(CHECK_INTERVAL)
    except KeyboardInterrupt:
        logger.info("Stopped")

if __name__ == "__main__":
    main()
