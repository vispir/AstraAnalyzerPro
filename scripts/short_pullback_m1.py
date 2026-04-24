"""
SHORT Pullback Strategy - M1 Entry after M15 Impulse
Ловим откаты после бычьих импульсов
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
import numpy as np
from datetime import datetime
import time

START_DATE = "2020-01-01"
END_DATE = "2026-04-18"

# Parameters to test
RISK_VALUES = [100, 120, 158]
TP_VALUES = [1.5, 2.0, 2.5]
TRADING_HOURS = (7, 21)  # Active hours UTC
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

def load_m1_data():
    """Load M1 data from parquet"""
    print("Loading M1 data from parquet...")

    # Load 2020-2024 data
    path1 = "D:/Works/ASTRA ANALYZER CHART/data_cache/dukascopy/m1/XAUUSD/xauusd_m1_2020-01-01_2024-12-31.parquet"
    # Load 2026 data
    path2 = "D:/Works/ASTRA ANALYZER CHART/data_cache/dukascopy/m1/XAUUSD/xauusd_m1_2026-01-01_2026-04-18.parquet"

    dfs = []

    if os.path.exists(path1):
        print(f"  Loading {path1}...")
        df1 = pd.read_parquet(path1)
        dfs.append(df1)

    if os.path.exists(path2):
        print(f"  Loading {path2}...")
        df2 = pd.read_parquet(path2)
        dfs.append(df2)

    if len(dfs) == 0:
        print(f"ERROR: No M1 data files found")
        return None

    # Combine
    df = pd.concat(dfs, ignore_index=False)

    if 'datetime' in df.columns:
        df['datetime'] = pd.to_datetime(df['datetime'])
        df.set_index('datetime', inplace=True)

    if df.index.name != 'datetime':
        df.index = pd.to_datetime(df.index)

    df = df.sort_index()

    # Filter by date range
    df = df[(df.index >= START_DATE) & (df.index <= END_DATE)]

    print(f"Loaded {len(df):,} M1 candles from {df.index[0]} to {df.index[-1]}")

    return df

def run_backtest(df_m1, risk_per_trade, tp_rr):
    """Run backtest with given parameters"""

    print(f"\nResampling M1 to M15...")
    df_m15 = df_m1.resample('15min').agg({
        'open': 'first',
        'high': 'max',
        'low': 'min',
        'close': 'last'
    }).dropna()

    print(f"Calculating ATR on M15 and M1...")
    df_m15['atr'] = calculate_atr(df_m15, ATR_PERIOD)
    df_m1['atr'] = calculate_atr(df_m1, ATR_PERIOD)

    # Trading state
    balance = 10000
    equity_curve = []
    trades = []
    active_trade = None

    # M15 impulse tracking
    m15_impulse_active = False
    m15_impulse_high = None
    m15_impulse_time = None
    m15_impulse_atr = None

    print(f"Running backtest...")
    start_time = time.time()

    total_bars = len(df_m1)
    progress_step = total_bars // 20  # 5% steps

    for i, (idx, row) in enumerate(df_m1.iterrows()):
        # Progress
        if i % progress_step == 0:
            pct = (i / total_bars) * 100
            elapsed = time.time() - start_time
            if i > 0:
                eta = (elapsed / i) * (total_bars - i)
                print(f"  Progress: {pct:.0f}% ({i:,}/{total_bars:,}) - ETA: {eta/60:.1f}m")

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
            if tp_rr >= 2.0:
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
                m15_impulse_active = False
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
                m15_impulse_active = False
                equity_curve.append({'time': idx, 'equity': balance})
                continue

            equity_curve.append({'time': idx, 'equity': balance})
            continue

        # Check for M15 impulse every 15 minutes
        m15_time = idx.floor('15min')
        if m15_time in df_m15.index and not m15_impulse_active:
            m15_idx = df_m15.index.get_loc(m15_time)

            if m15_idx >= 3:
                # Get last 3 M15 bars
                last_3 = df_m15.iloc[m15_idx-2:m15_idx+1]

                # Check if all 3 are bullish
                all_bullish = all(last_3['close'] > last_3['open'])

                if all_bullish:
                    # Check total rise
                    total_rise = last_3['close'].iloc[-1] - last_3['open'].iloc[0]
                    atr_val = last_3['atr'].iloc[-1]

                    if pd.notna(atr_val) and total_rise > 0.8 * atr_val:
                        # M15 impulse detected
                        m15_impulse_active = True
                        m15_impulse_high = last_3['high'].max()
                        m15_impulse_time = m15_time
                        m15_impulse_atr = atr_val

        # If M15 impulse active, look for M1 pullback
        if m15_impulse_active:
            # Impulse valid for 30 minutes
            if (idx - m15_impulse_time).total_seconds() > 30 * 60:
                m15_impulse_active = False
                continue

            # Get last 3 M1 bars
            m1_idx = df_m1.index.get_loc(idx)
            if m1_idx >= 3:
                last_3_m1 = df_m1.iloc[m1_idx-2:m1_idx+1]

                # Check if all 3 are bearish
                all_bearish = all(last_3_m1['close'] < last_3_m1['open'])

                if all_bearish:
                    m1_atr = row['atr']
                    if pd.notna(m1_atr):
                        # Entry SHORT
                        entry = row['close']
                        sl = m15_impulse_high + 1.5 * m1_atr
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

                        m15_impulse_active = False

        equity_curve.append({'time': idx, 'equity': balance})

    elapsed = time.time() - start_time
    print(f"  Backtest completed in {elapsed/60:.1f} minutes")

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
        'total_pnl': total_pnl,
        'max_dd': max_dd,
        'max_daily_dd': max_daily_dd,
        'win_rate': win_rate,
        'total_trades': len(df_trades),
        'profit_factor': profit_factor
    }

if __name__ == "__main__":
    print("="*80)
    print("SHORT Pullback Strategy - M1 Entry after M15 Impulse")
    print("="*80)
    print("\nLogic:")
    print("  1. M15: 3 bullish candles, total rise > 0.8x ATR(14)")
    print("  2. M1: First 3 bearish candles = SHORT entry")
    print("  3. SL: M15 impulse high + 1.5x ATR(14) M1")
    print("  4. TP: 1.5R, 2.0R, 2.5R")
    print("\nParameters:")
    print(f"  Risk: {RISK_VALUES}")
    print(f"  TP: {TP_VALUES}")
    print(f"  Trading Hours: {TRADING_HOURS[0]:02d}:00-{TRADING_HOURS[1]:02d}:00 UTC")
    print("="*80)

    # Load M1 data
    df_m1 = load_m1_data()

    if df_m1 is None:
        print("\nERROR: Could not load M1 data")
        sys.exit(1)

    results = []

    for risk in RISK_VALUES:
        for tp_rr in TP_VALUES:
            print(f"\n{'='*80}")
            print(f"Testing Risk=${risk}, TP={tp_rr}R")
            print(f"{'='*80}")

            result = run_backtest(df_m1, risk, tp_rr)

            if result is None:
                print(f"  No trades generated")
                continue

            results.append(result)

            print(f"\nResults:")
            print(f"  PnL: ${result['total_pnl']:,.0f}")
            print(f"  DD: {result['max_dd']:.2f}%")
            print(f"  Daily DD: {result['max_daily_dd']:.2f}%")
            print(f"  WR: {result['win_rate']:.1f}%")
            print(f"  Trades: {result['total_trades']}")
            print(f"  PF: {result['profit_factor']:.2f}")

            # Check criteria
            if result['max_dd'] < 8.0 and result['win_rate'] > 40 and result['total_pnl'] > 10000:
                print(f"  [+] PASS ALL CRITERIA!")

    # Sort by PnL
    results.sort(key=lambda x: x['total_pnl'], reverse=True)

    print("\n" + "="*80)
    print("SUMMARY - TOP RESULTS")
    print("="*80)

    for i, r in enumerate(results[:5], 1):
        status = "[+]" if r['max_dd'] < 8.0 and r['win_rate'] > 40 and r['total_pnl'] > 10000 else "[-]"

        print(f"\n#{i} {status}")
        print(f"  Risk: ${r['risk']}, TP: {r['tp_rr']}R")
        print(f"  PnL: ${r['total_pnl']:,.0f}, DD: {r['max_dd']:.2f}%, WR: {r['win_rate']:.1f}%")
        print(f"  Trades: {r['total_trades']}, PF: {r['profit_factor']:.2f}")

    print("\n" + "="*80)
    print("CONCLUSION")
    print("="*80)

    passing = [r for r in results if r['max_dd'] < 8.0 and r['win_rate'] > 40 and r['total_pnl'] > 10000]

    if passing:
        best = passing[0]
        print(f"\n[+] Found {len(passing)} passing combination(s)!")
        print(f"\nBest: Risk=${best['risk']}, TP={best['tp_rr']}R")
        print(f"  PnL: ${best['total_pnl']:,.0f}")
        print(f"  DD: {best['max_dd']:.2f}% (< 8% [+])")
        print(f"  WR: {best['win_rate']:.1f}% (> 40% [+])")
        print(f"  Trades: {best['total_trades']}")
    else:
        print("\n[-] No combinations passed all criteria (DD < 8%, WR > 40%, PnL > $10k)")
