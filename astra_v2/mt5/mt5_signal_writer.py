"""
MT5 Signal Writer - Supabase Integration
Writes trading signals from backtest strategy to Supabase for MT5 EA consumption
"""
import os
from datetime import datetime
from supabase import create_client, Client

# Supabase credentials (set as environment variables)
SUPABASE_URL = os.getenv("SUPABASE_URL", "https://your-project.supabase.co")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "your-anon-key")

# Initialize Supabase client
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

def get_active_signal():
    """
    Check if there's already an active trade
    Returns: dict or None
    """
    try:
        response = supabase.table('mt5_signals').select('*').eq('status', 'active').execute()
        if response.data and len(response.data) > 0:
            return response.data[0]
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

        response = supabase.table('mt5_signals').insert(signal_data).execute()

        if response.data and len(response.data) > 0:
            signal = response.data[0]
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

        response = supabase.table('mt5_signals').update(update_data).eq('id', signal_id).execute()
        print(f"Signal {signal_id} updated to status: {status}")
        return response.data
    except Exception as e:
        print(f"Error updating signal status: {e}")
        return None

def get_recent_signals(limit=10):
    """Get recent signals for monitoring"""
    try:
        response = supabase.table('mt5_signals').select('*').order('created_at', desc=True).limit(limit).execute()
        return response.data
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
