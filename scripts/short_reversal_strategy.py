"""
SHORT Reversal Strategy - Bull Trap & Overbought
Логика: H4 ложный пробой/перекупленность + M15 подтверждение
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
import numpy as np
from astra_v2.data.dukascopy import load_timeframe
from itertools import product

START_DATE = "2020-01-01"
END_DATE = "2026-04-18"

# Parameters to test
RISK_VALUES = [100, 120, 158]
TP_VALUES = [2.0, 2.5, 3.0, 3.5]
ATR_PERIOD = 14
ATR_MULTIPLIER = 1.5

# Trading windows to test
WINDOWS = {
    'asian': (7, 10),
    'london': (13, 16),
    'ny': (18, 21),
    'morning': (10, 13)
}

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

def check_h4_signal(h4_bar, h4_prev, rsi, bb_upper):
    """Check H4 conditions for SHORT setup"""

    # 1. Bull trap: H4 closed above previous high
    bull_trap = h4_bar['close'] > h4_prev['high']

    # 2. Overbought: RSI > 65
    overbought = rsi > 65

    # 3. Above Bollinger Band
    above_bb = h4_bar['close'] > bb_upper

    return bull_trap or overbought or above_bb

def check_m15_confirmation(m15_bars):
    """Check M15 confirmation for entry"""
    if len(m15_bars) < 5:
        return False, None

    current = m15_bars.iloc[-1]

    # 1. Bearish candle
    bearish_candle = current['close'] < current['open']

    # 2. Break of local low (last 3-5 bars)
    local_low = m15_bars.iloc[-5:-1]['low'].min()
    break_low = current['close'] < local_low

    if bearish_candle or break_low:
        return True, current['close']

    return False, None

def run_backtest(risk_per_trade, tp_rr, windows_enabled):
    """Run backtest with given parameters"""

    # Load data
    df_m15 = load_timeframe("M15", start=START_DATE, end=END_DATE, symbol="XAUUSD")

    if 'datetime' in df_m15.columns:
        df_m15.set_index('datetime', inplace=True)
    df_m15 = df_m15.sort_index()

    # Resample to H4
    df_h4 = df_m15.resample('4h').agg({
        'open': 'first',
        'high': 'max',
        'low': 'min',
        'close': 'last'
    }).dropna()

    # Calculate H4 indicators
    df_h4['rsi'] = calculate_rsi(df_h4, 14)
    df_h4['bb_upper'], df_h4['bb_lower'] = calculate_bollinger_bands(df_h4, 20, 2.0)

    # Calculate M15 ATR
    df_m15['atr'] = calculate_atr(df_m15, ATR_PERIOD)

    # Trading state
    balance = 10000
    equity_curve = []
    trades = []
    active_trade = None

    # Track H4 signal
    h4_signal_active = False
    h4_signal_time = None

    # Process each M15 bar
    for idx, row in df_m15.iterrows():
        current_hour = idx.hour

        # Check if in trading window
        in_window = False
        for window_name in windows_enabled:
            if window_name in WINDOWS:
                start_h, end_h = WINDOWS[window_name]
                if start_h <= current_hour < end_h:
                    in_window = True
                    break

        if not in_window and active_trade is None:
            equity_curve.append({'time': idx, 'equity': balance})
            continue

        # Manage active trade
        if active_trade:
            current_price = row['close']
            risk = active_trade['sl'] - active_trade['entry']
            profit_r = (active_trade['entry'] - current_price) / risk

            # Step trailing
            new_sl = active_trade['sl']
            if tp_rr >= 3.0:
                if profit_r >= 3.0:
                    new_sl = min(new_sl, active_trade['entry'] - 2.0 * risk)
                elif profit_r >= 2.0:
                    new_sl = min(new_sl, active_trade['entry'] - 1.0 * risk)
            elif tp_rr >= 2.5:
                if profit_r >= 2.0:
                    new_sl = min(new_sl, active_trade['entry'] - 1.0 * risk)

            active_trade['sl'] = new_sl

            # Check TP
            if current_price <= active_trade['tp']:
                pnl = risk_per_trade * tp_rr
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
                    pnl = -risk_per_trade
                else:
                    pnl = (active_trade['entry'] - active_trade['sl']) / risk * risk_per_trade
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

        # Check for H4 signal every 4 hours
        h4_time = idx.floor('4h')
        if h4_time in df_h4.index:
            h4_idx = df_h4.index.get_loc(h4_time)

            if h4_idx > 0:
                h4_bar = df_h4.iloc[h4_idx]
                h4_prev = df_h4.iloc[h4_idx - 1]

                if pd.notna(h4_bar['rsi']) and pd.notna(h4_bar['bb_upper']):
                    if check_h4_signal(h4_bar, h4_prev, h4_bar['rsi'], h4_bar['bb_upper']):
                        h4_signal_active = True
                        h4_signal_time = h4_time

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

                confirmed, entry_price = check_m15_confirmation(m15_bars)

                if confirmed:
                    atr_val = row['atr']
                    if pd.notna(atr_val):
                        # Entry
                        entry = entry_price
                        sl = entry + ATR_MULTIPLIER * atr_val
                        risk = sl - entry
                        tp = entry - tp_rr * risk

                        active_trade = {
                            'direction': 'SHORT',
                            'entry': entry,
                            'sl': sl,
                            'original_sl': sl,
                            'tp': tp,
                            'entry_time': idx,
                            'entry_hour': idx.hour,
                            'risk_usd': risk_per_trade
                        }

                        h4_signal_active = False
                        h4_signal_time = None

        equity_curve.append({'time': idx, 'equity': balance})

    # Calculate metrics
    if len(trades) == 0:
        return None

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

    return {
        'risk': risk_per_trade,
        'tp_rr': tp_rr,
        'windows': windows_enabled,
        'total_pnl': total_pnl,
        'max_dd': max_dd,
        'max_daily_dd': max_daily_dd,
        'win_rate': win_rate,
        'total_trades': len(df_trades),
        'profit_factor': profit_factor
    }

if __name__ == "__main__":
    print("="*80)
    print("SHORT Reversal Strategy - Bull Trap & Overbought")
    print("="*80)
    print("\nH4 Conditions: Bull trap OR RSI>65 OR Above BB")
    print("M15 Confirmation: Bearish candle OR Break local low")
    print("\nTesting parameters:")
    print(f"  Risk: {RISK_VALUES}")
    print(f"  TP: {TP_VALUES}")
    print(f"  Windows: {list(WINDOWS.keys())}")
    print("="*80)

    results = []

    # Test all window combinations
    window_names = list(WINDOWS.keys())
    from itertools import combinations

    all_window_combos = []
    for r in range(1, len(window_names) + 1):
        for combo in combinations(window_names, r):
            all_window_combos.append(list(combo))

    total_tests = len(RISK_VALUES) * len(TP_VALUES) * len(all_window_combos)
    current_test = 0

    for risk, tp_rr in product(RISK_VALUES, TP_VALUES):
        for windows in all_window_combos:
            current_test += 1
            windows_str = "+".join(windows)
            print(f"\n[{current_test}/{total_tests}] Testing Risk=${risk}, TP={tp_rr}R, Windows={windows_str}...")

            result = run_backtest(risk, tp_rr, windows)

            if result is None:
                print(f"  No trades")
                continue

            results.append(result)

            print(f"  PnL: ${result['total_pnl']:,.0f}, DD: {result['max_dd']:.2f}%, WR: {result['win_rate']:.1f}%, Trades: {result['total_trades']}, PF: {result['profit_factor']:.2f}")

            # Check if passes criteria
            if result['max_dd'] < 8.0 and result['total_pnl'] > 20000 and result['win_rate'] > 40:
                print(f"  [+] PASS!")

    # Sort by PnL
    results.sort(key=lambda x: x['total_pnl'], reverse=True)

    print("\n" + "="*80)
    print("TOP 10 RESULTS (by Total PnL)")
    print("="*80)

    for i, r in enumerate(results[:10], 1):
        windows_str = "+".join(r['windows'])
        status = "[+] PASS" if r['max_dd'] < 8.0 and r['total_pnl'] > 20000 and r['win_rate'] > 40 else "[-] FAIL"

        print(f"\n#{i} {status}")
        print(f"  Risk: ${r['risk']}, TP: {r['tp_rr']}R, Windows: {windows_str}")
        print(f"  PnL: ${r['total_pnl']:,.0f}, DD: {r['max_dd']:.2f}%, Daily DD: {r['max_daily_dd']:.2f}%")
        print(f"  WR: {r['win_rate']:.1f}%, Trades: {r['total_trades']}, PF: {r['profit_factor']:.2f}")

    print("\n" + "="*80)
    print("BEST PASSING RESULTS")
    print("="*80)

    passing = [r for r in results if r['max_dd'] < 8.0 and r['total_pnl'] > 20000 and r['win_rate'] > 40]

    if passing:
        print(f"\nFound {len(passing)} passing combinations!")

        best = passing[0]
        windows_str = "+".join(best['windows'])

        print(f"\n[+] BEST COMBINATION:")
        print(f"  Risk: ${best['risk']}")
        print(f"  TP: {best['tp_rr']}R")
        print(f"  Windows: {windows_str}")
        print(f"  Step Trailing: Enabled")
        print(f"\nResults:")
        print(f"  Total PnL: ${best['total_pnl']:,.0f}")
        print(f"  Max DD: {best['max_dd']:.2f}% (< 8% [+])")
        print(f"  Daily DD: {best['max_daily_dd']:.2f}%")
        print(f"  Win Rate: {best['win_rate']:.1f}% (> 40% [+])")
        print(f"  Total Trades: {best['total_trades']}")
        print(f"  Profit Factor: {best['profit_factor']:.2f}")
    else:
        print("\n[-] No combinations passed all criteria (DD < 8%, PnL > $20k, WR > 40%)")

        if results:
            print("\nClosest results:")
            for r in results[:3]:
                windows_str = "+".join(r['windows'])
                print(f"\n  Risk=${r['risk']}, TP={r['tp_rr']}R, Windows={windows_str}")
                print(f"  PnL: ${r['total_pnl']:,.0f}, DD: {r['max_dd']:.2f}%, WR: {r['win_rate']:.1f}%")
