import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

print("Step 1: Imports complete")
sys.stdout.flush()

import pandas as pd
import numpy as np
from astra_v2.data.dukascopy import load_timeframe

print("Step 2: Loading data...")
sys.stdout.flush()

df = load_timeframe("M15", start="2020-01-01", end="2026-04-18", symbol="XAUUSD")
if 'datetime' in df.columns:
    df.set_index('datetime', inplace=True)
df = df.sort_index()

print(f"Step 3: Loaded {len(df)} bars")
sys.stdout.flush()

high = df['high']
low = df['low']
close = df['close']
tr1 = high - low
tr2 = abs(high - close.shift(1))
tr3 = abs(low - close.shift(1))
tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
df['atr'] = tr.rolling(window=20).mean()

print("Step 4: ATR calculated")
sys.stdout.flush()

df_h4 = df.resample('4h').agg({'open':'first','high':'max','low':'min','close':'last'}).dropna()
df_h4['ema20'] = df_h4['close'].ewm(span=20, adjust=False).mean()

print(f"Step 5: H4 data ready - {len(df_h4)} bars")
sys.stdout.flush()

print("Step 6: Starting backtest with Risk=100, TP=3.0, Stop=0.3")
sys.stdout.flush()

balance = 10000
trades = 0
dates = df.index.date
unique_dates = sorted(set(dates))

print(f"Step 7: Processing {len(unique_dates)} days")
sys.stdout.flush()

for idx, date in enumerate(unique_dates):
    if idx % 500 == 0:
        print(f"Day {idx}/{len(unique_dates)}")
        sys.stdout.flush()

print(f"Step 8: Complete - {trades} trades, Balance: ${balance:.0f}")
sys.stdout.flush()
