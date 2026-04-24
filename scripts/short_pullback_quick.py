"""
SHORT Pullback Strategy - Quick Test (Single Config)
Быстрая проверка концепции на одной комбинации параметров
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
import numpy as np
from datetime import datetime
import time

START_DATE = "2020-01-01"
END_DATE = "2024-12-31"  # Only 2020-2024 for speed

# Single config test
RISK = 120
TP_RR = 2.0
TRADING_HOURS = (7, 21)
ATR_PERIOD = 14

def calculate_atr(df, period=14):
    high = df['high']
    low = df['low']
    close = df['close']

    tr1 = high - low
    tr2 = abs(high - close.shift(1))
    tr3 = abs(low - close.shift(1))

    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    atr = tr.rolling(window=period).mean()

    return atr

print("="*80)
print("SHORT Pullback Strategy - Quick Test")
print("="*80)
print(f"\nLoading M1 data (2020-2024 only)...")

path = "D:/Works/ASTRA ANALYZER CHART/data_cache/dukascopy/m1/XAUUSD/xauusd_m1_2020-01-01_2024-12-31.parquet"

start_load = time.time()
df_m1 = pd.read_parquet(path)
print(f"Loaded in {time.time()-start_load:.1f}s")

if 'datetime' in df_m1.columns:
    df_m1['datetime'] = pd.to_datetime(df_m1['datetime'])
    df_m1.set_index('datetime', inplace=True)

if df_m1.index.name != 'datetime':
    df_m1.index = pd.to_datetime(df_m1.index)

df_m1 = df_m1.sort_index()
print(f"Total M1 bars: {len(df_m1):,}")

print(f"\nResampling to M15...")
df_m15 = df_m1.resample('15min').agg({
    'open': 'first',
    'high': 'max',
    'low': 'min',
    'close': 'last'
}).dropna()
print(f"Total M15 bars: {len(df_m15):,}")

print(f"\nCalculating ATR...")
df_m15['atr'] = calculate_atr(df_m15, ATR_PERIOD)
df_m1['atr'] = calculate_atr(df_m1, ATR_PERIOD)

print(f"\nRunning backtest (Risk=${RISK}, TP={TP_RR}R)...")

balance = 10000
trades = []
active_trade = None

m15_impulse_active = False
m15_impulse_high = None
m15_impulse_time = None

start_bt = time.time()
total_bars = len(df_m1)
last_pct = 0

for i, (idx, row) in enumerate(df_m1.iterrows()):
    # Progress every 10%
    pct = int((i / total_bars) * 100)
    if pct >= last_pct + 10:
        elapsed = time.time() - start_bt
        eta = (elapsed / i) * (total_bars - i) if i > 0 else 0
        print(f"  {pct}% ({i:,}/{total_bars:,}) - ETA: {eta/60:.1f}m")
        last_pct = pct

    current_hour = idx.hour

    if not (TRADING_HOURS[0] <= current_hour < TRADING_HOURS[1]) and active_trade is None:
        continue

    # Manage active trade
    if active_trade:
        current_price = row['close']
        risk = active_trade['sl'] - active_trade['entry']
        profit_r = (active_trade['entry'] - current_price) / risk

        # Step trailing
        new_sl = active_trade['sl']
        if profit_r >= 2.0:
            new_sl = min(new_sl, active_trade['entry'] - 1.0 * risk)

        active_trade['sl'] = new_sl

        # Check TP
        if current_price <= active_trade['tp']:
            pnl = RISK * TP_RR
            balance += pnl
            active_trade['exit_price'] = active_trade['tp']
            active_trade['pnl'] = pnl
            active_trade['exit_time'] = idx
            active_trade['exit_reason'] = 'TP'
            trades.append(active_trade)
            active_trade = None
            m15_impulse_active = False
            continue

        # Check SL
        if current_price >= active_trade['sl']:
            if active_trade['sl'] == active_trade['original_sl']:
                pnl = -RISK
            else:
                pnl = (active_trade['entry'] - active_trade['sl']) / risk * RISK
            balance += pnl
            active_trade['exit_price'] = active_trade['sl']
            active_trade['pnl'] = pnl
            active_trade['exit_time'] = idx
            active_trade['exit_reason'] = 'SL'
            trades.append(active_trade)
            active_trade = None
            m15_impulse_active = False
            continue

        continue

    # Check for M15 impulse
    m15_time = idx.floor('15min')
    if m15_time in df_m15.index and not m15_impulse_active:
        m15_idx = df_m15.index.get_loc(m15_time)

        if m15_idx >= 3:
            last_3 = df_m15.iloc[m15_idx-2:m15_idx+1]
            all_bullish = all(last_3['close'] > last_3['open'])

            if all_bullish:
                total_rise = last_3['close'].iloc[-1] - last_3['open'].iloc[0]
                atr_val = last_3['atr'].iloc[-1]

                if pd.notna(atr_val) and total_rise > 0.8 * atr_val:
                    m15_impulse_active = True
                    m15_impulse_high = last_3['high'].max()
                    m15_impulse_time = m15_time

    # Look for M1 pullback
    if m15_impulse_active:
        if (idx - m15_impulse_time).total_seconds() > 30 * 60:
            m15_impulse_active = False
            continue

        m1_idx = df_m1.index.get_loc(idx)
        if m1_idx >= 3:
            last_3_m1 = df_m1.iloc[m1_idx-2:m1_idx+1]
            all_bearish = all(last_3_m1['close'] < last_3_m1['open'])

            if all_bearish:
                m1_atr = row['atr']
                if pd.notna(m1_atr):
                    entry = row['close']
                    sl = m15_impulse_high + 1.5 * m1_atr
                    risk = sl - entry
                    tp = entry - TP_RR * risk

                    active_trade = {
                        'direction': 'SHORT',
                        'entry': entry,
                        'sl': sl,
                        'original_sl': sl,
                        'tp': tp,
                        'entry_time': idx,
                        'entry_hour': idx.hour,
                        'risk_usd': RISK
                    }

                    m15_impulse_active = False

elapsed = time.time() - start_bt
print(f"\nBacktest completed in {elapsed/60:.1f} minutes")

print("\n" + "="*80)
print("RESULTS")
print("="*80)

if len(trades) == 0:
    print("\nNo trades generated!")
else:
    df_trades = pd.DataFrame(trades)

    total_pnl = df_trades['pnl'].sum()
    wins = df_trades[df_trades['pnl'] > 0]
    losses = df_trades[df_trades['pnl'] <= 0]
    win_rate = len(wins) / len(df_trades) * 100

    total_wins = wins['pnl'].sum() if len(wins) > 0 else 0
    total_losses = abs(losses['pnl'].sum()) if len(losses) > 0 else 1
    profit_factor = total_wins / total_losses if total_losses > 0 else 0

    print(f"\nParameters:")
    print(f"  Risk: ${RISK}")
    print(f"  TP: {TP_RR}R")
    print(f"  Period: {START_DATE} to {END_DATE}")

    print(f"\nResults:")
    print(f"  Total PnL: ${total_pnl:,.0f}")
    print(f"  Final Balance: ${balance:,.0f}")
    print(f"  Win Rate: {win_rate:.1f}%")
    print(f"  Total Trades: {len(df_trades)}")
    print(f"  Winning: {len(wins)}")
    print(f"  Losing: {len(losses)}")
    print(f"  Profit Factor: {profit_factor:.2f}")

    print(f"\nCriteria Check:")
    print(f"  WR > 40%: {'[+] PASS' if win_rate > 40 else '[-] FAIL'} ({win_rate:.1f}%)")
    print(f"  PnL > $10k: {'[+] PASS' if total_pnl > 10000 else '[-] FAIL'} (${total_pnl:,.0f})")
