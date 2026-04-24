"""
SHORT Strategy Optimization: Entry Confirmation
Оптимизация подтверждения разворота и M15 входа
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
import numpy as np
from datetime import datetime
from itertools import product
from astra_v2.data.dukascopy import load_timeframe

# Fixed parameters
RISK_PER_TRADE = 158
TP_RR = 5.5
ATR_PERIOD = 14
ATR_BUFFER = 0.5
START_DATE = "2020-01-01"
END_DATE = "2026-04-18"
LOOKBACK_H4_BARS = 5

# Parameters to optimize
H4_REVERSAL_BARS = [1, 2]  # Number of H4 bars confirming reversal
M15_LOOKBACK_BARS = [3, 5, 10]  # Number of M15 bars for low breakout

# Step Trailing
USE_STEP_TRAILING = True

# H4 EMA20 Filter
USE_H4_EMA_FILTER = True
H4_EMA_PERIOD = 20

# Trading hours
TRADING_START_HOUR = 7
TRADING_END_HOUR = 21

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

def calculate_ema(df, period):
    return df['close'].ewm(span=period, adjust=False).mean()

def run_backtest(df, df_h4, h4_reversal_bars, m15_lookback_bars):
    trades = []
    active_trade = None
    balance = 10000
    peak_balance = 10000
    max_dd = 0
    max_daily_dd = 0

    # Track if we're in reversal mode
    reversal_active = False
    reversal_h4_high = None

    dates = df.index.date
    unique_dates = sorted(set(dates))

    for date_idx, date in enumerate(unique_dates):
        day_start_balance = balance
        day_data = df[df.index.date == date]

        if len(day_data) < 10:
            continue

        # Convert to numpy
        highs = day_data['high'].to_numpy()
        lows = day_data['low'].to_numpy()
        closes = day_data['close'].to_numpy()
        atrs = day_data['atr'].to_numpy()
        hours = np.array([t.hour for t in day_data.index])
        times = day_data.index.to_numpy()

        for i in range(len(day_data)):
            current_time = times[i]

            # Check exit conditions for active trade
            if active_trade is not None:
                # Step Trailing Stop
                if USE_STEP_TRAILING:
                    risk = active_trade['initial_sl'] - active_trade['entry']
                    profit_in_r = (active_trade['entry'] - lows[i]) / risk

                    # 2R -> 1R, 3R -> 2R, 4R -> 3R, 5R -> 4R
                    if profit_in_r >= 5.0:
                        new_sl = active_trade['entry'] - 4.0 * risk
                        active_trade['sl'] = min(active_trade['sl'], new_sl)
                    elif profit_in_r >= 4.0:
                        new_sl = active_trade['entry'] - 3.0 * risk
                        active_trade['sl'] = min(active_trade['sl'], new_sl)
                    elif profit_in_r >= 3.0:
                        new_sl = active_trade['entry'] - 2.0 * risk
                        active_trade['sl'] = min(active_trade['sl'], new_sl)
                    elif profit_in_r >= 2.0:
                        new_sl = active_trade['entry'] - 1.0 * risk
                        active_trade['sl'] = min(active_trade['sl'], new_sl)

                # Check SL/TP
                if highs[i] >= active_trade['sl']:
                    pnl = (active_trade['entry'] - active_trade['sl']) * active_trade['size']
                    balance += pnl
                    trades.append({
                        'entry_time': active_trade['entry_time'],
                        'exit_time': times[i],
                        'entry_price': active_trade['entry'],
                        'exit_price': active_trade['sl'],
                        'pnl': pnl,
                        'exit_reason': 'sl'
                    })
                    active_trade = None
                    reversal_active = False
                elif lows[i] <= active_trade['tp']:
                    pnl = (active_trade['entry'] - active_trade['tp']) * active_trade['size']
                    balance += pnl
                    trades.append({
                        'entry_time': active_trade['entry_time'],
                        'exit_time': times[i],
                        'entry_price': active_trade['entry'],
                        'exit_price': active_trade['tp'],
                        'pnl': pnl,
                        'exit_reason': 'tp'
                    })
                    active_trade = None
                    reversal_active = False

                # Update max DD
                if balance > peak_balance:
                    peak_balance = balance
                dd = (peak_balance - balance) / peak_balance * 100
                if dd > max_dd:
                    max_dd = dd

                if active_trade is not None:
                    continue

            # Check for reversal setup and entry
            hour = hours[i]
            if not (TRADING_START_HOUR <= hour < TRADING_END_HOUR):
                continue

            atr = atrs[i]
            if np.isnan(atr):
                continue

            # Get current H4 bar
            h4_bars = df_h4[df_h4.index <= current_time]
            if len(h4_bars) < LOOKBACK_H4_BARS + h4_reversal_bars + 1:
                continue

            current_h4 = h4_bars.iloc[-1]

            # Check if we just detected a reversal
            if not reversal_active:
                # Check if current H4 made new high
                lookback_highs = h4_bars.iloc[-LOOKBACK_H4_BARS-1:-1]['high']
                historical_high = lookback_highs.max()

                if current_h4['high'] > historical_high:
                    # New high detected, check for reversal confirmation
                    reversal_confirmed = True

                    # Check last N H4 bars are all closing lower
                    for j in range(1, h4_reversal_bars + 1):
                        if j >= len(h4_bars):
                            reversal_confirmed = False
                            break
                        current_bar = h4_bars.iloc[-j]
                        prev_bar = h4_bars.iloc[-j-1]
                        if current_bar['close'] >= prev_bar['close']:
                            reversal_confirmed = False
                            break

                    if reversal_confirmed:
                        reversal_active = True
                        reversal_h4_high = current_h4['high']

            # If reversal is active, look for M15 entry
            if reversal_active and active_trade is None:
                # Check H4 EMA20 filter
                if USE_H4_EMA_FILTER:
                    if pd.isna(current_h4['ema20']):
                        continue
                    if current_h4['close'] >= current_h4['ema20']:
                        continue  # Skip SHORT if not below EMA20

                # Entry: M15 close below low of last N M15 bars
                if i >= m15_lookback_bars:
                    lookback_lows = lows[i-m15_lookback_bars:i]
                    min_low = lookback_lows.min()

                    if closes[i] < min_low:
                        entry = closes[i]
                        sl = reversal_h4_high + ATR_BUFFER * atr
                        risk = sl - entry

                        if risk <= 0:
                            continue

                        tp = entry - risk * TP_RR
                        size = RISK_PER_TRADE / risk

                        active_trade = {
                            'entry': entry,
                            'sl': sl,
                            'initial_sl': sl,
                            'tp': tp,
                            'size': size,
                            'entry_time': times[i]
                        }
                        reversal_active = False  # Reset after entry

        # Calculate daily DD
        if day_start_balance > 0:
            daily_dd = (day_start_balance - balance) / day_start_balance * 100
            if daily_dd > max_daily_dd:
                max_daily_dd = daily_dd

    # Close any remaining active trade
    if active_trade is not None:
        last_bar = df.iloc[-1]
        pnl = (active_trade['entry'] - last_bar['close']) * active_trade['size']
        balance += pnl
        trades.append({
            'entry_time': active_trade['entry_time'],
            'exit_time': df.index[-1],
            'entry_price': active_trade['entry'],
            'exit_price': last_bar['close'],
            'pnl': pnl,
            'exit_reason': 'eod'
        })

    if len(trades) == 0:
        return None

    trades_df = pd.DataFrame(trades)

    total_trades = len(trades_df)
    wins = trades_df[trades_df['pnl'] > 0]
    losses = trades_df[trades_df['pnl'] <= 0]

    win_rate = len(wins) / total_trades if total_trades > 0 else 0

    total_profit = wins['pnl'].sum() if len(wins) > 0 else 0
    total_loss = abs(losses['pnl'].sum()) if len(losses) > 0 else 0
    profit_factor = total_profit / total_loss if total_loss > 0 else 0

    total_pnl = balance - 10000

    return {
        'total_trades': total_trades,
        'win_rate': win_rate,
        'profit_factor': profit_factor,
        'max_drawdown_pct': max_dd,
        'max_daily_dd': max_daily_dd,
        'total_pnl': total_pnl,
        'final_balance': balance
    }

def main():
    print("=== SHORT Reversal Optimization: Entry Confirmation ===")
    print(f"Period: {START_DATE} - {END_DATE}")
    print(f"Fixed: Lookback={LOOKBACK_H4_BARS} H4 bars, Risk=${RISK_PER_TRADE}, TP={TP_RR}R")
    print(f"Testing {len(H4_REVERSAL_BARS)} H4 reversal x {len(M15_LOOKBACK_BARS)} M15 lookback = {len(H4_REVERSAL_BARS) * len(M15_LOOKBACK_BARS)} combinations\n")

    # Load M15 data
    print("Loading M15 data...")
    df = load_timeframe("m15", start=START_DATE, end=END_DATE, symbol="XAUUSD")
    print(f"Loaded {len(df):,} M15 bars\n")

    if 'datetime' in df.columns:
        df.set_index('datetime', inplace=True)
    df = df.sort_index()

    # Calculate ATR
    df['atr'] = calculate_atr(df, ATR_PERIOD)

    # Resample to H4
    print("Resampling M15 to H4...")
    df_h4 = df.resample('4h').agg({'open':'first','high':'max','low':'min','close':'last'}).dropna()
    df_h4['ema20'] = calculate_ema(df_h4, H4_EMA_PERIOD)
    print(f"Resampled {len(df_h4):,} H4 bars\n")

    # Run backtests
    results = []

    for h4_rev, m15_look in product(H4_REVERSAL_BARS, M15_LOOKBACK_BARS):
        print(f"Testing H4_Rev={h4_rev}, M15_Look={m15_look}...", end=' ', flush=True)

        result = run_backtest(df, df_h4, h4_rev, m15_look)

        if result is None:
            print("NO TRADES")
            continue

        result['h4_reversal'] = h4_rev
        result['m15_lookback'] = m15_look
        results.append(result)

        print(f"PnL=${result['total_pnl']:,.0f}, DD={result['max_drawdown_pct']:.2f}%, Trades={result['total_trades']}, WR={result['win_rate']:.1%}")

    if len(results) == 0:
        print("\nNo valid results!")
        return

    # Sort by PnL
    results_df = pd.DataFrame(results)
    results_df = results_df.sort_values('total_pnl', ascending=False)

    # Print results
    print("\n" + "="*130)
    print("RESULTS: ENTRY CONFIRMATION OPTIMIZATION")
    print("="*130)
    print(f"{'H4 Rev':<8} {'M15 Look':<10} {'Trades':<8} {'Win Rate':<10} {'PF':<8} {'Total PnL':<12} {'Max DD':<10} {'Daily DD':<10} {'Status':<15}")
    print("-"*130)

    for _, row in results_df.iterrows():
        passes_dd = row['max_drawdown_pct'] < 8.0
        passes_wr = row['win_rate'] > 0.45
        passes_pnl = row['total_pnl'] > 0

        status = "PASS" if (passes_dd and passes_wr and passes_pnl) else "FAIL"

        print(f"{row['h4_reversal']:<8} {row['m15_lookback']:<10} {row['total_trades']:<8} {row['win_rate']:<9.1%} {row['profit_factor']:<7.2f} ${row['total_pnl']:<11,.0f} {row['max_drawdown_pct']:<9.2f}% {row['max_daily_dd']:<9.2f}% {status:<15}")

    print("="*130)

    # Best result
    best = results_df.iloc[0]
    print(f"\nBEST RESULT:")
    print(f"H4 Reversal Bars: {best['h4_reversal']}")
    print(f"M15 Lookback Bars: {best['m15_lookback']}")
    print(f"Total PnL: ${best['total_pnl']:,.2f}")
    print(f"Max DD: {best['max_drawdown_pct']:.2f}%")
    print(f"Max Daily DD: {best['max_daily_dd']:.2f}%")
    print(f"Win Rate: {best['win_rate']:.1%}")
    print(f"Profit Factor: {best['profit_factor']:.2f}")
    print(f"Total Trades: {best['total_trades']}")

    # Analysis
    print("\n=== ANALYSIS ===")
    print("H4 Reversal Bars:")
    print("  1 bar: Faster entry, more signals, potentially more false signals")
    print("  2 bars: Stronger confirmation, fewer signals, higher quality")
    print("\nM15 Lookback Bars:")
    print("  3 bars: Tight entry, more signals")
    print("  5 bars: Balanced")
    print("  10 bars: Wide entry, fewer signals, stronger breakout")

if __name__ == "__main__":
    main()
