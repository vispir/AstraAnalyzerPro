"""
SHORT Strategy Optimization: Reversal After Historical High
Оптимизация периода исторического максимума (5, 10, 15, 20 H4 баров)
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
import numpy as np
from datetime import datetime
from astra_v2.data.dukascopy import load_timeframe

# Fixed parameters
RISK_PER_TRADE = 158
TP_RR = 5.5
ATR_PERIOD = 14
ATR_BUFFER = 0.5
START_DATE = "2020-01-01"
END_DATE = "2026-04-18"

# Lookback periods to test
LOOKBACK_PERIODS = [5, 10, 15, 20]

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

def run_backtest(df, df_h4, lookback_bars):
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
            if len(h4_bars) < lookback_bars + 2:
                continue

            current_h4 = h4_bars.iloc[-1]
            prev_h4 = h4_bars.iloc[-2]

            # Check if we just detected a reversal
            if not reversal_active:
                # Check if current H4 made new high
                lookback_highs = h4_bars.iloc[-lookback_bars-1:-1]['high']
                historical_high = lookback_highs.max()

                if current_h4['high'] > historical_high:
                    # New high detected, check for reversal
                    if current_h4['close'] < prev_h4['close']:
                        # Reversal confirmed
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

                # Entry: M15 close below previous M15 low
                if i > 0:
                    prev_m15_low = lows[i-1]
                    if closes[i] < prev_m15_low:
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
    print("=== SHORT Reversal Optimization: Lookback Period ===")
    print(f"Period: {START_DATE} - {END_DATE}")
    print(f"Fixed: Risk=${RISK_PER_TRADE}, TP={TP_RR}R, Step Trailing enabled")
    print(f"Testing {len(LOOKBACK_PERIODS)} lookback periods: {LOOKBACK_PERIODS}\n")

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

    for lookback in LOOKBACK_PERIODS:
        print(f"Testing Lookback={lookback} H4 bars...", end=' ', flush=True)

        result = run_backtest(df, df_h4, lookback)

        if result is None:
            print("NO TRADES")
            continue

        result['lookback'] = lookback
        results.append(result)

        print(f"PnL=${result['total_pnl']:,.0f}, DD={result['max_drawdown_pct']:.2f}%, Trades={result['total_trades']}, WR={result['win_rate']:.1%}")

    if len(results) == 0:
        print("\nNo valid results!")
        return

    # Sort by total trades (to find balance)
    results_df = pd.DataFrame(results)

    # Print results
    print("\n" + "="*120)
    print("RESULTS: LOOKBACK PERIOD OPTIMIZATION")
    print("="*120)
    print(f"{'Lookback':<10} {'Trades':<8} {'Win Rate':<10} {'PF':<8} {'Total PnL':<12} {'Max DD':<10} {'Daily DD':<10} {'Status':<15}")
    print("-"*120)

    for _, row in results_df.iterrows():
        passes_dd = row['max_drawdown_pct'] < 8.0
        passes_wr = row['win_rate'] > 0.45

        status = "PASS" if (passes_dd and passes_wr) else "FAIL"

        print(f"{row['lookback']:<10} {row['total_trades']:<8} {row['win_rate']:<9.1%} {row['profit_factor']:<7.2f} ${row['total_pnl']:<11,.0f} {row['max_drawdown_pct']:<9.2f}% {row['max_daily_dd']:<9.2f}% {status:<15}")

    print("="*120)

    # Best by trades/quality balance
    print("\n=== ANALYSIS ===")
    print(f"Lookback 5:  {'More trades, lower quality' if 5 in results_df['lookback'].values else 'N/A'}")
    print(f"Lookback 10: {'Balanced' if 10 in results_df['lookback'].values else 'N/A'}")
    print(f"Lookback 15: {'Balanced' if 15 in results_df['lookback'].values else 'N/A'}")
    print(f"Lookback 20: {'Fewer trades, higher quality' if 20 in results_df['lookback'].values else 'N/A'}")

    # Best by PnL
    best_pnl = results_df.loc[results_df['total_pnl'].idxmax()]
    print(f"\nBEST BY PnL: Lookback={best_pnl['lookback']}")
    print(f"  Total PnL: ${best_pnl['total_pnl']:,.2f}")
    print(f"  Trades: {best_pnl['total_trades']}")
    print(f"  Win Rate: {best_pnl['win_rate']:.1%}")
    print(f"  Max DD: {best_pnl['max_drawdown_pct']:.2f}%")

    # Best by trades
    best_trades = results_df.loc[results_df['total_trades'].idxmax()]
    print(f"\nBEST BY TRADES: Lookback={best_trades['lookback']}")
    print(f"  Total Trades: {best_trades['total_trades']}")
    print(f"  Total PnL: ${best_trades['total_pnl']:,.2f}")
    print(f"  Win Rate: {best_trades['win_rate']:.1%}")
    print(f"  Max DD: {best_trades['max_drawdown_pct']:.2f}%")

if __name__ == "__main__":
    main()
