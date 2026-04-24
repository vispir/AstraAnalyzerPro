"""
SHORT Reversal Strategy v2 - Stricter Filters
Более строгие условия для уменьшения ложных сигналов
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
import numpy as np
from astra_v2.data.dukascopy import load_timeframe

START_DATE = "2020-01-01"
END_DATE = "2026-04-18"

# Test multiple parameter sets
TEST_CONFIGS = [
    {'risk': 120, 'tp': 3.0, 'rsi_threshold': 70, 'hours': (13, 16), 'name': 'London High RSI'},
    {'risk': 120, 'tp': 3.5, 'rsi_threshold': 75, 'hours': (13, 16), 'name': 'London Very High RSI'},
    {'risk': 158, 'tp': 2.5, 'rsi_threshold': 70, 'hours': (10, 13), 'name': 'Morning High RSI'},
    {'risk': 100, 'tp': 3.0, 'rsi_threshold': 72, 'hours': (13, 16), 'name': 'London RSI 72'},
]

ATR_PERIOD = 14
ATR_MULTIPLIER = 2.0  # Wider stop

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

print("\n" + "="*80)
print("TESTING MULTIPLE CONFIGURATIONS")
print("="*80)

results = []

for config in TEST_CONFIGS:
    print(f"\nTesting: {config['name']}")
    print(f"  Risk=${config['risk']}, TP={config['tp']}R, RSI>{config['rsi_threshold']}, Hours={config['hours']}")

    RISK = config['risk']
    TP_RR = config['tp']
    RSI_THRESHOLD = config['rsi_threshold']
    TRADING_HOURS = config['hours']

    balance = 10000
    equity_curve = []
    trades = []
    active_trade = None

    h4_signal_active = False
    h4_signal_time = None

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
            if TP_RR >= 3.0:
                if profit_r >= 3.0:
                    new_sl = min(new_sl, active_trade['entry'] - 2.0 * risk)
                elif profit_r >= 2.0:
                    new_sl = min(new_sl, active_trade['entry'] - 1.0 * risk)
            elif TP_RR >= 2.5:
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
                    # STRICT: RSI must be above threshold AND above BB
                    if h4_bar['rsi'] > RSI_THRESHOLD and h4_bar['close'] > h4_bar['bb_upper']:
                        h4_signal_active = True
                        h4_signal_time = h4_time

        # If H4 signal active, look for M15 confirmation
        if h4_signal_active:
            # Signal valid for 2 hours only (stricter)
            if (idx - h4_signal_time).total_seconds() > 2 * 3600:
                h4_signal_active = False
                h4_signal_time = None
                continue

            # Get last 5 M15 bars
            m15_idx = df_m15.index.get_loc(idx)
            if m15_idx >= 5:
                m15_bars = df_m15.iloc[m15_idx-4:m15_idx+1]
                current = m15_bars.iloc[-1]

                # STRICT: Must be strong bearish candle
                candle_body = abs(current['close'] - current['open'])
                candle_range = current['high'] - current['low']

                strong_bearish = (current['close'] < current['open'] and
                                 candle_body > 0.6 * candle_range)  # Body > 60% of range

                if strong_bearish:
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

    # Calculate metrics
    if len(trades) == 0:
        print(f"  No trades generated")
        continue

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

    result = {
        'name': config['name'],
        'risk': RISK,
        'tp': TP_RR,
        'rsi_threshold': RSI_THRESHOLD,
        'hours': TRADING_HOURS,
        'total_pnl': total_pnl,
        'max_dd': max_dd,
        'max_daily_dd': max_daily_dd,
        'win_rate': win_rate,
        'total_trades': len(df_trades),
        'profit_factor': profit_factor
    }
    results.append(result)

    print(f"  PnL: ${total_pnl:,.0f}, DD: {max_dd:.2f}%, WR: {win_rate:.1f}%, Trades: {len(df_trades)}, PF: {profit_factor:.2f}")

    # Check criteria
    if max_dd < 8.0 and total_pnl > 20000 and win_rate > 40:
        print(f"  [+] PASS ALL CRITERIA!")

print("\n" + "="*80)
print("SUMMARY")
print("="*80)

if results:
    results.sort(key=lambda x: x['total_pnl'], reverse=True)

    print("\nBest by PnL:")
    for i, r in enumerate(results[:3], 1):
        status = "[+]" if r['max_dd'] < 8.0 and r['total_pnl'] > 20000 and r['win_rate'] > 40 else "[-]"
        print(f"\n{i}. {status} {r['name']}")
        print(f"   PnL: ${r['total_pnl']:,.0f}, DD: {r['max_dd']:.2f}%, WR: {r['win_rate']:.1f}%, Trades: {r['total_trades']}, PF: {r['profit_factor']:.2f}")
else:
    print("\nNo valid results")
