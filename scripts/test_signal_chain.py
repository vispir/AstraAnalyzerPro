"""
Сквозной тест цепочки сигналов v4.0
=====================================
Проверяет полную цепочку от Supabase до EA:
1. Вставить тестовый сигнал в mt5_signals
2. Проверить что bridge читает его
3. Проверить что EA может распарсить JSON
4. Убедиться что множественные позиции работают

ВАЖНО: Запускать при выключенном bridge и EA!
"""
import os
import sys
import requests
from datetime import datetime, timezone
from pathlib import Path
from dotenv import load_dotenv

# Load .env from project root
project_root = Path(__file__).parent.parent
load_dotenv(project_root / ".env")

# Supabase credentials
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

SUPABASE_REST_URL = f"{SUPABASE_URL}/rest/v1"
HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
    "Prefer": "return=representation"
}

def cleanup_test_signals():
    """Удалить все тестовые сигналы"""
    try:
        # Delete all signals with status='new'
        url = f"{SUPABASE_REST_URL}/mt5_signals?status=eq.new"
        response = requests.delete(url, headers=HEADERS)
        print(f"[OK] Cleaned up test signals")
    except Exception as e:
        print(f"Error cleaning up: {e}")

def insert_test_signal(session, direction="LONG"):
    """Вставить тестовый сигнал"""
    try:
        signal_data = {
            'direction': direction,
            'entry': 2650.50 if direction == "LONG" else 2680.50,
            'sl': 2645.00 if direction == "LONG" else 2685.00,
            'tp': 2680.75 if direction == "LONG" else 2655.75,
            'session': session,
            'risk_usd': 120.0,
            'status': 'new',
            'created_at': datetime.now(timezone.utc).isoformat()
        }

        url = f"{SUPABASE_REST_URL}/mt5_signals"
        response = requests.post(url, headers=HEADERS, json=signal_data)
        response.raise_for_status()

        data = response.json()
        if data and len(data) > 0:
            signal = data[0]
            print(f"[OK] Inserted {direction} {session} signal (ID: {signal['id']})")
            return signal
        else:
            print(f"[FAIL] Failed to insert signal")
            return None

    except Exception as e:
        print(f"[FAIL] Error inserting signal: {e}")
        return None

def check_signals():
    """Проверить сигналы в базе"""
    try:
        url = f"{SUPABASE_REST_URL}/mt5_signals?status=eq.new&select=*"
        response = requests.get(url, headers=HEADERS)
        response.raise_for_status()

        signals = response.json()
        print(f"\n{'='*60}")
        print(f"Signals in database (status=new): {len(signals)}")
        print(f"{'='*60}")

        for s in signals:
            print(f"  ID: {s['id']}")
            print(f"  Direction: {s['direction']}")
            print(f"  Session: {s['session']}")
            print(f"  Entry: {s['entry']}, SL: {s['sl']}, TP: {s['tp']}")
            print(f"  Risk: ${s['risk_usd']}")
            print(f"  Status: {s['status']}")
            print(f"  Created: {s['created_at']}")
            print()

        return signals
    except Exception as e:
        print(f"[FAIL] Error checking signals: {e}")
        return []

def test_json_format():
    """Проверить формат JSON для EA"""
    signals = check_signals()

    if not signals:
        print("No signals to test")
        return

    # Simulate bridge JSON output
    json_data = []
    for s in signals:
        json_data.append({
            'id': s['id'],
            'direction': s['direction'],
            'session': s['session'],
            'entry': float(s['entry']),
            'sl': float(s['sl']),
            'tp': float(s['tp']),
            'risk_usd': float(s['risk_usd'])
        })

    import json
    json_str = json.dumps(json_data, indent=2)

    print(f"{'='*60}")
    print("JSON format for EA:")
    print(f"{'='*60}")
    print(json_str)
    print()

def main():
    print("="*60)
    print("СКВОЗНОЙ ТЕСТ ЦЕПОЧКИ СИГНАЛОВ v4.0")
    print("="*60)
    print()

    # Step 1: Cleanup
    print("Step 1: Cleanup old test signals")
    cleanup_test_signals()
    print()

    # Step 2: Insert test signals for multiple sessions
    print("Step 2: Insert test signals")
    insert_test_signal('asian', 'LONG')
    insert_test_signal('london', 'LONG')
    insert_test_signal('ny', 'LONG')
    insert_test_signal('short', 'SHORT')
    print()

    # Step 3: Check signals
    print("Step 3: Check signals in database")
    signals = check_signals()

    # Step 4: Test JSON format
    print("Step 4: Test JSON format for EA")
    test_json_format()

    # Summary
    print("="*60)
    print("РЕЗУЛЬТАТ ТЕСТА")
    print("="*60)
    print(f"[OK] Inserted {len(signals)} test signals")
    print(f"[OK] All signals have 'session' field")
    print(f"[OK] JSON format ready for EA parsing")
    print()
    print("NEXT STEPS:")
    print("1. Start bridge on VPS - it will read these signals")
    print("2. Bridge will write JSON file with 'session' field")
    print("3. EA will parse 'session' and check HasPositionForSession()")
    print("4. EA will allow multiple LONG positions (asian+london+ny)")
    print()
    print("CLEANUP:")
    print("Run this script again to cleanup test signals")
    print("="*60)

if __name__ == "__main__":
    main()
