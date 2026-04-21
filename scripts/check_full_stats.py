"""
Full statistics check for combined_session_backtest.py
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Run the actual backtest and get trades
import subprocess
result = subprocess.run(
    ['python', 'scripts/combined_session_backtest.py'],
    capture_output=True,
    text=True,
    cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)

print(result.stdout)

# Now load and analyze the trades
import pandas as pd
import numpy as np
from astra_v2.data.dukascopy import load_timeframe

# Re-run to get trades_df
exec(open('scripts/combined_session_backtest.py').read())

print("\n" + "=" * 100)
print("DETAILED STATISTICS")
print("=" * 100)

wins = trades_df[trades_df['pnl'] > 0]
losses = trades_df[trades_df['pnl'] <= 0]

print(f"\nWinning trades: {len(wins)}")
print(f"Total winning PnL: ${wins['pnl'].sum():,.2f}")
print(f"\nLosing trades: {len(losses)}")
print(f"Total losing PnL: ${losses['pnl'].sum():,.2f}")
print(f"\nNet PnL: ${trades_df['pnl'].sum():,.2f}")
print(f"Final balance: ${10000 + trades_df['pnl'].sum():,.2f}")
