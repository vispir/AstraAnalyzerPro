"""
SHORT Pullback Strategy - 2025-2026 Only
Проверка работает ли SHORT на более свежих данных
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
import numpy as np
from datetime import datetime
import time

START_DATE = "2025-01-01"
END_DATE = "2026-04-18"

# Parameters to test
RISK = 158
TP_VALUES = [1.5, 2.0, 2.5, 3.0]
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

def run_backtest(df_m1, df_m15, tp_rr):
    """Run backtest with given TP"""

    balance = 10000
    equity_curve = []
    trades = []
    active_trade = None

    m15_impulse_active = False
    m15_impulse_high = None
    m15_impulse_time = None

    for idx, row in df_m1.iterrows():
        current_hour = idx.hour

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
            if tp_rr >= 3.0:
                if profit_r >= 3.0:
                    new_sl = min(new_sl, active_trade['entry'] - 2.0 * risk)
                elif profit_r >= 2.0:
                    new_sl = min(new_sl, active_trade['entry'] - 1.0 * risk)
            elif tp_rr >= 2.5:
                if profit_r >= 2.0:
                    new_sl = min(new_sl, active_trade['entry'] - 1.0 * risk)
            elif tp_rr >= 2.0:
                if profit_r >= 2.0:
                    new_sl = min(new_sl, active_trade['entry'] - 1.0 * risk)

            active_trade['sl'] = new_sl

            # Check TP
            if current_price <= active_trade['tp']:
                pnl = RISK * tp_rr
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
                equity_curve.append({'time': idx, 'equity': balance})
                continue

            equity_curve.append({'time': idx, 'equity': balance})
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
                        tp = entry - tp_rr * risk

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
        'tp_rr': tp_rr,
        'total_pnl': total_pnl,
        'max_dd': max_dd,
        'max_daily_dd': max_daily_dd,
        'win_rate': win_rate,
        'total_trades': len(df_trades),
        'profit_factor': profit_factor,
        'final_balance': balance
    }

if __name__ == "__main__":
    print("="*80)
    print("SHORT Pullback Strategy - 2025-2026 Test")
    print("="*80)
    print(f"\nPeriod: {START_DATE} to {END_DATE}")
    print(f"Risk: ${RISK}")
    print(f"TP: {TP_VALUES}")
    print(f"Trading Hours: {TRADING_HOURS[0]:02d}:00-{TRADING_HOURS[1]:02d}:00 UTC")
    print("="*80)

    # Load 2025 data
    print(f"\nLoading M1 data...")
    path_2025 = "D:/Works/ASTRA ANALYZER CHART/data_cache/dukascopy/xauusd_m1_2025-01-01_2025-12-31.parquet"
    path_2026 = "D:/Works/ASTRA ANALYZER CHART/data_cache/dukascopy/m1/XAUUSD/xauusd_m1_2026-01-01_2026-04-18.parquet"

    dfs = []

    if os.path.exists(path_2025):
        print(f"  Loading 2025...")
        df1 = pd.read_parquet(path_2025)
        dfs.append(df1)

    if os.path.exists(path_2026):
        print(f"  Loading 2026...")
        df2 = pd.read_parquet(path_2026)
        dfs.append(df2)

    if len(dfs) == 0:
        print("ERROR: No data files found")
        sys.exit(1)

    df_m1 = pd.concat(dfs, ignore_index=False)

    if 'datetime' in df_m1.columns:
        df_m1['datetime'] = pd.to_datetime(df_m1['datetime'])
        df_m1.set_index('datetime', inplace=True)

    if df_m1.index.name != 'datetime':
        df_m1.index = pd.to_datetime(df_m1.index)

    df_m1 = df_m1.sort_index()
    df_m1 = df_m1[(df_m1.index >= START_DATE) & (df_m1.index <= END_DATE)]

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

    results = []

    for tp_rr in TP_VALUES:
        print(f"\n{'='*80}")
        print(f"Testing TP={tp_rr}R")
        print(f"{'='*80}")

        start_time = time.time()
        result = run_backtest(df_m1, df_m15, tp_rr)
        elapsed = time.time() - start_time

        print(f"Completed in {elapsed/60:.1f} minutes")

        if result is None:
            print(f"  No trades generated")
            continue

        results.append(result)

        print(f"\nResults:")
        print(f"  Total PnL: ${result['total_pnl']:,.0f}")
        print(f"  Final Balance: ${result['final_balance']:,.0f}")
        print(f"  Max DD: {result['max_dd']:.2f}%")
        print(f"  Daily DD: {result['max_daily_dd']:.2f}%")
        print(f"  Win Rate: {result['win_rate']:.1f}%")
        print(f"  Total Trades: {result['total_trades']}")
        print(f"  Profit Factor: {result['profit_factor']:.2f}")

        # Check criteria
        if result['max_dd'] < 8.0 and result['win_rate'] > 40 and result['total_pnl'] > 5000:
            print(f"  [+] PASS ALL CRITERIA!")

    # Sort by PnL
    results.sort(key=lambda x: x['total_pnl'], reverse=True)

    print("\n" + "="*80)
    print("SUMMARY")
    print("="*80)

    for i, r in enumerate(results, 1):
        status = "[+]" if r['max_dd'] < 8.0 and r['win_rate'] > 40 and r['total_pnl'] > 5000 else "[-]"

        print(f"\n#{i} {status} TP={r['tp_rr']}R")
        print(f"  PnL: ${r['total_pnl']:,.0f}, DD: {r['max_dd']:.2f}%, Daily DD: {r['max_daily_dd']:.2f}%")
        print(f"  WR: {r['win_rate']:.1f}%, Trades: {r['total_trades']}, PF: {r['profit_factor']:.2f}")

    print("\n" + "="*80)
    print("CONCLUSION")
    print("="*80)

    passing = [r for r in results if r['max_dd'] < 8.0 and r['win_rate'] > 40 and r['total_pnl'] > 5000]

    if passing:
        best = passing[0]
        print(f"\n[+] Found {len(passing)} passing combination(s)!")
        print(f"\nBest: TP={best['tp_rr']}R")
        print(f"  PnL: ${best['total_pnl']:,.0f}")
        print(f"  DD: {best['max_dd']:.2f}% (< 8%)")
        print(f"  WR: {best['win_rate']:.1f}% (> 40%)")
        print(f"  Trades: {best['total_trades']}")
    else:
        print("\n[-] No combinations passed all criteria (DD < 8%, WR > 40%, PnL > $5k)")
        if results:
            best = results[0]
            print(f"\nBest result: TP={best['tp_rr']}R")
            print(f"  PnL: ${best['total_pnl']:,.0f}")
            print(f"  DD: {best['max_dd']:.2f}%")
            print(f"  WR: {best['win_rate']:.1f}%")
