"""
MT5 Signal Writer - Supabase Integration
Writes trading signals from backtest strategy to Supabase for MT5 EA consumption
Uses direct HTTP requests to avoid supabase-py dependency issues
"""
import os
import requests
from datetime import datetime

# Supabase credentials (set as environment variables)
SUPABASE_URL = os.getenv("SUPABASE_URL", "https://your-project.supabase.co")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "your-anon-key")

# REST API endpoint
SUPABASE_REST_URL = f"{SUPABASE_URL}/rest/v1"

# Headers for all requests
HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
    "Prefer": "return=representation"
}

def get_active_signal():
    """
    Check if there's already an active trade
    Returns: dict or None
    """
    try:
        url = f"{SUPABASE_REST_URL}/mt5_signals?status=eq.active&select=*"
        response = requests.get(url, headers=HEADERS)
        response.raise_for_status()

        data = response.json()
        if data and len(data) > 0:
            return data[0]
        return None
    except Exception as e:
        print(f"Error checking active signal: {e}")
        return None

def write_signal(direction, entry, sl, tp, session, risk_usd=165):
    """
    Write new trading signal to Supabase

    Args:
        direction: 'LONG' or 'SHORT'
        entry: Entry price
        sl: Stop loss price
        tp: Take profit price
        session: 'asian', 'london', or 'ny'
        risk_usd: Risk amount in USD (default 165)

    Returns:
        dict: Created signal record or None if failed
    """
    # Check if there's already an active signal
    active = get_active_signal()
    if active:
        print(f"Active signal already exists (ID: {active['id']}), skipping new signal")
        return None

    try:
        signal_data = {
            'direction': direction,
            'entry': float(entry),
            'sl': float(sl),
            'tp': float(tp),
            'session': session,
            'risk_usd': float(risk_usd),
            'status': 'new',
            'created_at': datetime.utcnow().isoformat()
        }

        url = f"{SUPABASE_REST_URL}/mt5_signals"
        response = requests.post(url, headers=HEADERS, json=signal_data)
        response.raise_for_status()

        data = response.json()
        if data and len(data) > 0:
            signal = data[0]
            print(f"Signal written: {direction} {session.upper()} @ {entry:.2f}, SL: {sl:.2f}, TP: {tp:.2f}, Risk: ${risk_usd}")
            return signal
        else:
            print("Failed to write signal - no data returned")
            return None

    except Exception as e:
        print(f"Error writing signal: {e}")
        return None

def update_signal_status(signal_id, status, exit_price=None, pnl=None):
    """
    Update signal status (used by EA or for manual updates)

    Args:
        signal_id: Signal ID
        status: 'active', 'closed', 'cancelled'
        exit_price: Exit price (optional)
        pnl: Profit/Loss in USD (optional)
    """
    try:
        update_data = {'status': status}
        if exit_price is not None:
            update_data['exit_price'] = float(exit_price)
        if pnl is not None:
            update_data['pnl'] = float(pnl)

        url = f"{SUPABASE_REST_URL}/mt5_signals?id=eq.{signal_id}"
        response = requests.patch(url, headers=HEADERS, json=update_data)
        response.raise_for_status()

        print(f"Signal {signal_id} updated to status: {status}")
        return response.json()
    except Exception as e:
        print(f"Error updating signal status: {e}")
        return None

def get_recent_signals(limit=10):
    """Get recent signals for monitoring"""
    try:
        url = f"{SUPABASE_REST_URL}/mt5_signals?select=*&order=created_at.desc&limit={limit}"
        response = requests.get(url, headers=HEADERS)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        print(f"Error fetching recent signals: {e}")
        return []

# Example usage
if __name__ == "__main__":
    print("MT5 Signal Writer - Supabase Integration")
    print("="*60)

    # Check for active signals
    active = get_active_signal()
    if active:
        print(f"Active signal found: {active}")
    else:
        print("No active signals")

    # Example: Write a test signal
    # signal = write_signal(
    #     direction='LONG',
    #     entry=2650.50,
    #     sl=2645.00,
    #     tp=2680.75,
    #     session='london',
    #     risk_usd=165
    # )

    # Get recent signals
    recent = get_recent_signals(5)
    print(f"\nRecent signals: {len(recent)}")
    for sig in recent:
        print(f"  {sig['created_at']}: {sig['direction']} {sig['session']} - {sig['status']}")
