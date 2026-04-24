"""
SHORT Reversal Strategy - Quick Test
Тестируем только лучшие комбинации для скорости
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
import numpy as np
from astra_v2.data.dukascopy import load_timeframe

START_DATE = "2020-01-01"
END_DATE = "2026-04-18"

# Quick test parameters
RISK = 120
TP_RR = 3.0
ATR_PERIOD = 14
ATR_MULTIPLIER = 1.5
TRADING_HOURS = (10, 16)  # London + Morning window

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

def calculate_rsi(df, period=14):
    delta = df['close'].diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
    rs = gain / loss
    rsi = 100 - (100 / (1 + rs))
    return rsi

def calculate_bollinger_bands(df, period=20, std=2.0):
    sma = df['close'].rolling(window=period).mean()
    std_dev = df['close'].rolling(window=period).std()
    upper = sma + (std_dev * std)
    lower = sma - (std_dev * std)
    return upper, lower

print("Loading data...")
df_m15 = load_timeframe("M15", start=START_DATE, end=END_DATE, symbol="XAUUSD")

if 'datetime' in df_m15.columns:
    df_m15.set_index('datetime', inplace=True)
df_m15 = df_m15.sort_index()

print("Resampling to H4...")
df_h4 = df_m15.resample('4h').agg({
    'open': 'first',
    'high': 'max',
    'low': 'min',
    'close': 'last'
}).dropna()

print("Calculating indicators...")
df_h4['rsi'] = calculate_rsi(df_h4, 14)
df_h4['bb_upper'], df_h4['bb_lower'] = calculate_bollinger_bands(df_h4, 20, 2.0)
df_m15['atr'] = calculate_atr(df_m15, ATR_PERIOD)

print("Running backtest...")

balance = 10000
equity_curve = []
trades = []
active_trade = None

h4_signal_active = False
h4_signal_time = None
h4_signal_price = None

for idx, row in df_m15.iterrows():
    current_hour = idx.hour

    # Trading window
    if not (TRADING_HOURS[0] <= current_hour < TRADING_HOURS[1]) and active_trade is None:
        equity_curve.append({'time': idx, 'equity': balance})
        continue

    # Manage active trade
    if active_trade:
        current_price = row['close']
        risk = active_trade['sl'] - active_trade['entry']
        profit_r = (active_trade['entry'] - current_price) / risk

        # Step trailing
        new_sl = active_trade['sl']
        if profit_r >= 3.0:
            new_sl = min(new_sl, active_trade['entry'] - 2.0 * risk)
        elif profit_r >= 2.0:
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
            equity_curve.append({'time': idx, 'equity': balance})
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
            equity_curve.append({'time': idx, 'equity': balance})
            continue

        equity_curve.append({'time': idx, 'equity': balance})
        continue

    # Check for H4 signal
    h4_time = idx.floor('4h')
    if h4_time in df_h4.index:
        h4_idx = df_h4.index.get_loc(h4_time)

        if h4_idx > 0 and not h4_signal_active:
            h4_bar = df_h4.iloc[h4_idx]
            h4_prev = df_h4.iloc[h4_idx - 1]

            if pd.notna(h4_bar['rsi']) and pd.notna(h4_bar['bb_upper']):
                # Check H4 conditions
                bull_trap = h4_bar['close'] > h4_prev['high']
                overbought = h4_bar['rsi'] > 65
                above_bb = h4_bar['close'] > h4_bar['bb_upper']

                if bull_trap or overbought or above_bb:
                    h4_signal_active = True
                    h4_signal_time = h4_time
                    h4_signal_price = h4_bar['close']

    # If H4 signal active, look for M15 confirmation
    if h4_signal_active:
        # Signal valid for 4 hours
        if (idx - h4_signal_time).total_seconds() > 4 * 3600:
            h4_signal_active = False
            h4_signal_time = None
            continue

        # Get last 5 M15 bars
        m15_idx = df_m15.index.get_loc(idx)
        if m15_idx >= 5:
            m15_bars = df_m15.iloc[m15_idx-4:m15_idx+1]
            current = m15_bars.iloc[-1]

            # Check M15 confirmation
            bearish_candle = current['close'] < current['open']
            local_low = m15_bars.iloc[-5:-1]['low'].min()
            break_low = current['close'] < local_low

            if bearish_candle or break_low:
                atr_val = row['atr']
                if pd.notna(atr_val):
                    # Entry
                    entry = current['close']
                    sl = entry + ATR_MULTIPLIER * atr_val
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

                    h4_signal_active = False
                    h4_signal_time = None

    equity_curve.append({'time': idx, 'equity': balance})

print("\n" + "="*80)
print("SHORT REVERSAL STRATEGY - RESULTS")
print("="*80)

if len(trades) == 0:
    print("\nNo trades generated!")
else:
    df_trades = pd.DataFrame(trades)
    df_equity = pd.DataFrame(equity_curve)

    total_pnl = df_trades['pnl'].sum()
    wins = df_trades[df_trades['pnl'] > 0]
    losses = df_trades[df_trades['pnl'] <= 0]
    win_rate = len(wins) / len(df_trades) * 100

    # Profit factor
    total_wins = wins['pnl'].sum() if len(wins) > 0 else 0
    total_losses = abs(losses['pnl'].sum()) if len(losses) > 0 else 1
    profit_factor = total_wins / total_losses if total_losses > 0 else 0

    # Calculate DD
    df_equity['peak'] = df_equity['equity'].cummax()
    df_equity['dd'] = (df_equity['equity'] - df_equity['peak']) / df_equity['peak'] * 100
    max_dd = abs(df_equity['dd'].min())

    # Daily DD
    df_equity['date'] = df_equity['time'].dt.date
    daily_equity = df_equity.groupby('date')['equity'].agg(['first', 'min'])
    daily_equity['daily_dd'] = (daily_equity['min'] - daily_equity['first']) / daily_equity['first'] * 100
    max_daily_dd = abs(daily_equity['daily_dd'].min())

    print(f"\nParameters:")
    print(f"  Risk: ${RISK}")
    print(f"  TP: {TP_RR}R")
    print(f"  Trading Hours: {TRADING_HOURS[0]:02d}:00-{TRADING_HOURS[1]:02d}:00 UTC")
    print(f"  Step Trailing: Enabled")

    print(f"\nResults:")
    print(f"  Total PnL: ${total_pnl:,.0f}")
    print(f"  Final Balance: ${balance:,.0f}")
    print(f"  Max DD: {max_dd:.2f}%")
    print(f"  Max Daily DD: {max_daily_dd:.2f}%")
    print(f"  Win Rate: {win_rate:.1f}%")
    print(f"  Total Trades: {len(df_trades)}")
    print(f"  Winning Trades: {len(wins)}")
    print(f"  Losing Trades: {len(losses)}")
    print(f"  Profit Factor: {profit_factor:.2f}")

    # Check criteria
    print(f"\nCriteria Check:")
    print(f"  DD < 8%: {'[+] PASS' if max_dd < 8.0 else '[-] FAIL'} ({max_dd:.2f}%)")
    print(f"  PnL > $20k: {'[+] PASS' if total_pnl > 20000 else '[-] FAIL'} (${total_pnl:,.0f})")
    print(f"  WR > 40%: {'[+] PASS' if win_rate > 40 else '[-] FAIL'} ({win_rate:.1f}%)")

    if max_dd < 8.0 and total_pnl > 20000 and win_rate > 40:
        print(f"\n[+] ALL CRITERIA PASSED!")
    else:
        print(f"\n[-] Does not meet all criteria")
