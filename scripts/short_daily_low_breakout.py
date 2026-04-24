"""
SHORT Strategy: Daily Low Breakout
Вход при пробое минимума предыдущего дня
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from itertools import product
from astra_v2.data.dukascopy import load_timeframe

# Parameters to test
RISK_VALUES = [100, 120, 158]
TP_RR_VALUES = [1.5, 2.0, 2.5, 3.0]
ATR_PERIOD = 14
ATR_BUFFER = 0.5
START_DATE = "2020-01-01"
END_DATE = "2026-04-18"

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

def run_backtest(df, df_h4, risk_per_trade, tp_rr):
    trades = []
    active_trade = None
    balance = 10000
    peak_balance = 10000
    max_dd = 0
    max_daily_dd = 0

    dates = df.index.date
    unique_dates = sorted(set(dates))

    for date_idx, date in enumerate(unique_dates):
        if date_idx == 0:
            continue  # Skip first day (no previous day)

        day_start_balance = balance
        day_data = df[df.index.date == date]

        if len(day_data) < 10:
            continue

        # Get previous day data
        prev_date = unique_dates[date_idx - 1]
        prev_day_data = df[df.index.date == prev_date]

        if len(prev_day_data) == 0:
            continue

        # Previous day High/Low
        prev_high = prev_day_data['high'].max()
        prev_low = prev_day_data['low'].min()

        # Convert to numpy
        highs = day_data['high'].to_numpy()
        lows = day_data['low'].to_numpy()
        closes = day_data['close'].to_numpy()
        atrs = day_data['atr'].to_numpy()
        hours = np.array([t.hour for t in day_data.index])
        times = day_data.index.to_numpy()

        for i in range(len(day_data)):
            # Check exit conditions for active trade
            if active_trade is not None:
                # Step Trailing Stop
                if USE_STEP_TRAILING:
                    risk = active_trade['initial_sl'] - active_trade['entry']
                    profit_in_r = (active_trade['entry'] - lows[i]) / risk

                    # 2R -> 1R, 3R -> 2R, 4R -> 3R
                    if profit_in_r >= 4.0:
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

                # Update max DD
                if balance > peak_balance:
                    peak_balance = balance
                dd = (peak_balance - balance) / peak_balance * 100
                if dd > max_dd:
                    max_dd = dd

                if active_trade is not None:
                    continue

            # Check for new trade entry
            hour = hours[i]
            if not (TRADING_START_HOUR <= hour < TRADING_END_HOUR):
                continue

            atr = atrs[i]
            if np.isnan(atr):
                continue

            # Check H4 EMA20 filter
            if USE_H4_EMA_FILTER and df_h4 is not None:
                current_time = times[i]
                h4_bar = df_h4[df_h4.index <= current_time].iloc[-1] if len(df_h4[df_h4.index <= current_time]) > 0 else None
                if h4_bar is None or pd.isna(h4_bar['ema20']):
                    continue
                if h4_bar['close'] >= h4_bar['ema20']:
                    continue  # Skip SHORT if not in downtrend

            # SHORT entry: close below previous day low
            if closes[i] < prev_low and active_trade is None:
                entry = closes[i]
                sl = prev_high + ATR_BUFFER * atr
                risk = sl - entry
                tp = entry - risk * tp_rr
                size = risk_per_trade / risk

                active_trade = {
                    'entry': entry,
                    'sl': sl,
                    'initial_sl': sl,
                    'tp': tp,
                    'size': size,
                    'entry_time': times[i]
                }

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
    print("=== SHORT Daily Low Breakout Strategy ===")
    print(f"Period: {START_DATE} - {END_DATE}")
    print(f"Testing {len(RISK_VALUES)} risk levels x {len(TP_RR_VALUES)} TP levels = {len(RISK_VALUES) * len(TP_RR_VALUES)} combinations\n")

    # Load M15 data
    print("Loading M15 data...")
    df = load_timeframe("m15", start=START_DATE, end=END_DATE, symbol="XAUUSD")
    print(f"Loaded {len(df):,} M15 bars\n")

    if 'datetime' in df.columns:
        df.set_index('datetime', inplace=True)
    df = df.sort_index()

    # Calculate ATR
    df['atr'] = calculate_atr(df, ATR_PERIOD)

    # Load H4 data for EMA filter
    if USE_H4_EMA_FILTER:
        print("Resampling M15 to H4 for EMA20 filter...")
        df_h4 = df.resample('4h').agg({'open':'first','high':'max','low':'min','close':'last'}).dropna()
        df_h4['ema20'] = calculate_ema(df_h4, H4_EMA_PERIOD)
        print(f"Resampled {len(df_h4):,} H4 bars\n")
    else:
        df_h4 = None

    # Run backtests
    results = []

    for risk, tp_rr in product(RISK_VALUES, TP_RR_VALUES):
        print(f"Testing Risk=${risk}, TP={tp_rr}R...", end=' ', flush=True)

        result = run_backtest(df, df_h4, risk, tp_rr)

        if result is None:
            print("NO TRADES")
            continue

        result['risk'] = risk
        result['tp_rr'] = tp_rr
        results.append(result)

        print(f"PnL=${result['total_pnl']:,.0f}, DD={result['max_drawdown_pct']:.2f}%, Trades={result['total_trades']}, WR={result['win_rate']:.1%}")

    if len(results) == 0:
        print("\nNo valid results!")
        return

    # Sort by PnL
    results_df = pd.DataFrame(results)
    results_df = results_df.sort_values('total_pnl', ascending=False)

    # Print results
    print("\n" + "="*120)
    print("RESULTS SORTED BY PnL")
    print("="*120)
    print(f"{'Risk':<6} {'TP':<5} {'Trades':<8} {'Win Rate':<10} {'PF':<8} {'Total PnL':<12} {'Max DD':<10} {'Daily DD':<10} {'Status':<15}")
    print("-"*120)

    for _, row in results_df.iterrows():
        passes_dd = row['max_drawdown_pct'] < 8.0
        passes_wr = row['win_rate'] > 0.40
        passes_pnl = row['total_pnl'] > 15000

        status = "✓ PASS" if (passes_dd and passes_wr and passes_pnl) else "FAIL"

        print(f"${row['risk']:<5} {row['tp_rr']:<5.1f} {row['total_trades']:<8} {row['win_rate']:<9.1%} {row['profit_factor']:<7.2f} ${row['total_pnl']:<11,.0f} {row['max_drawdown_pct']:<9.2f}% {row['max_daily_dd']:<9.2f}% {status:<15}")

    print("="*120)

    # Best result
    best = results_df.iloc[0]
    print(f"\nBEST RESULT:")
    print(f"Risk=${best['risk']}, TP={best['tp_rr']}R")
    print(f"Total PnL: ${best['total_pnl']:,.2f}")
    print(f"Max DD: {best['max_drawdown_pct']:.2f}%")
    print(f"Max Daily DD: {best['max_daily_dd']:.2f}%")
    print(f"Win Rate: {best['win_rate']:.1%}")
    print(f"Profit Factor: {best['profit_factor']:.2f}")
    print(f"Total Trades: {best['total_trades']}")

if __name__ == "__main__":
    main()
