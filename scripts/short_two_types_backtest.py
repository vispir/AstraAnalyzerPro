"""
SHORT Strategy: Two Entry Types
Type 1: Reversal After Historical High
Type 2: Local Reversal After Strong Move
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
import numpy as np
from datetime import datetime
from astra_v2.data.dukascopy import load_timeframe

# Parameters
RISK_PER_TRADE = 158
TP_RR = 5.5
ATR_PERIOD = 14
ATR_BUFFER = 0.5
START_DATE = "2020-01-01"
END_DATE = "2026-04-18"

# Type 1: Reversal After Historical High
TYPE1_LOOKBACK_H4_BARS = 5
TYPE1_H4_REVERSAL_BARS = 1

# Type 2: Local Reversal After Strong Move
TYPE2_H4_LOOKBACK = 3
TYPE2_ATR_MULTIPLIER = 2.0

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

def apply_step_trailing(active_trade, current_low):
    """Apply step trailing stop logic for SHORT"""
    if not USE_STEP_TRAILING:
        return

    risk = active_trade['initial_sl'] - active_trade['entry']
    profit_in_r = (active_trade['entry'] - current_low) / risk

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

def run_backtest(df, df_h4):
    trades = []
    active_trade = None
    balance = 10000
    peak_balance = 10000
    max_dd = 0
    max_daily_dd = 0

    # Track reversal states
    type1_reversal_active = False
    type1_reversal_h4_high = None
    type2_reversal_active = False
    type2_reversal_h4_high = None

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
                apply_step_trailing(active_trade, lows[i])

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
                        'exit_reason': 'sl',
                        'type': active_trade['type']
                    })
                    active_trade = None
                    type1_reversal_active = False
                    type2_reversal_active = False
                elif lows[i] <= active_trade['tp']:
                    pnl = (active_trade['entry'] - active_trade['tp']) * active_trade['size']
                    balance += pnl
                    trades.append({
                        'entry_time': active_trade['entry_time'],
                        'exit_time': times[i],
                        'entry_price': active_trade['entry'],
                        'exit_price': active_trade['tp'],
                        'pnl': pnl,
                        'exit_reason': 'tp',
                        'type': active_trade['type']
                    })
                    active_trade = None
                    type1_reversal_active = False
                    type2_reversal_active = False

                # Update max DD
                if balance > peak_balance:
                    peak_balance = balance
                dd = (peak_balance - balance) / peak_balance * 100
                if dd > max_dd:
                    max_dd = dd

                if active_trade is not None:
                    continue

            # Check for entry setups
            hour = hours[i]
            if not (TRADING_START_HOUR <= hour < TRADING_END_HOUR):
                continue

            atr = atrs[i]
            if np.isnan(atr):
                continue

            # Get current H4 bar
            h4_bars = df_h4[df_h4.index <= current_time]
            if len(h4_bars) < max(TYPE1_LOOKBACK_H4_BARS + TYPE1_H4_REVERSAL_BARS + 1, TYPE2_H4_LOOKBACK + 1):
                continue

            current_h4 = h4_bars.iloc[-1]
            prev_h4 = h4_bars.iloc[-2]

            # Check H4 EMA20 filter (common for both types)
            if USE_H4_EMA_FILTER:
                if pd.isna(current_h4['ema20']):
                    continue
                if current_h4['close'] >= current_h4['ema20']:
                    continue  # Skip SHORT if not below EMA20

            # === TYPE 1: Reversal After Historical High ===
            if not type1_reversal_active and active_trade is None:
                # Check if current H4 made new high
                lookback_highs = h4_bars.iloc[-TYPE1_LOOKBACK_H4_BARS-1:-1]['high']
                historical_high = lookback_highs.max()

                if current_h4['high'] > historical_high:
                    # New high detected, check for reversal
                    if current_h4['close'] < prev_h4['close']:
                        type1_reversal_active = True
                        type1_reversal_h4_high = current_h4['high']

            # === TYPE 2: Local Reversal After Strong Move ===
            if not type2_reversal_active and active_trade is None:
                # Check if H4 rose by 2+ ATR in last 3 bars
                if len(h4_bars) >= TYPE2_H4_LOOKBACK + 1:
                    lookback_bars = h4_bars.iloc[-TYPE2_H4_LOOKBACK-1:-1]
                    price_change = current_h4['high'] - lookback_bars['low'].min()

                    # Get H4 ATR
                    h4_atr = current_h4.get('atr', atr)  # Use M15 ATR if H4 ATR not available

                    if not np.isnan(h4_atr) and price_change >= TYPE2_ATR_MULTIPLIER * h4_atr:
                        # Strong move detected, check for reversal
                        if current_h4['close'] < prev_h4['close']:
                            type2_reversal_active = True
                            type2_reversal_h4_high = current_h4['high']

            # === M15 ENTRY LOGIC ===
            if active_trade is None and i > 0:
                prev_m15_low = lows[i-1]

                # Type 1 entry
                if type1_reversal_active and closes[i] < prev_m15_low:
                    entry = closes[i]
                    sl = type1_reversal_h4_high + ATR_BUFFER * atr
                    risk = sl - entry

                    if risk > 0:
                        tp = entry - risk * TP_RR
                        size = RISK_PER_TRADE / risk

                        active_trade = {
                            'entry': entry,
                            'sl': sl,
                            'initial_sl': sl,
                            'tp': tp,
                            'size': size,
                            'entry_time': times[i],
                            'type': 'Type1_HistoricalHigh'
                        }
                        type1_reversal_active = False

                # Type 2 entry (only if Type 1 didn't trigger)
                elif type2_reversal_active and closes[i] < prev_m15_low:
                    entry = closes[i]
                    sl = type2_reversal_h4_high + ATR_BUFFER * atr
                    risk = sl - entry

                    if risk > 0:
                        tp = entry - risk * TP_RR
                        size = RISK_PER_TRADE / risk

                        active_trade = {
                            'entry': entry,
                            'sl': sl,
                            'initial_sl': sl,
                            'tp': tp,
                            'size': size,
                            'entry_time': times[i],
                            'type': 'Type2_LocalReversal'
                        }
                        type2_reversal_active = False

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
            'exit_reason': 'eod',
            'type': active_trade['type']
        })

    if len(trades) == 0:
        return None

    trades_df = pd.DataFrame(trades)

    # Overall stats
    total_trades = len(trades_df)
    wins = trades_df[trades_df['pnl'] > 0]
    losses = trades_df[trades_df['pnl'] <= 0]

    win_rate = len(wins) / total_trades if total_trades > 0 else 0

    total_profit = wins['pnl'].sum() if len(wins) > 0 else 0
    total_loss = abs(losses['pnl'].sum()) if len(losses) > 0 else 0
    profit_factor = total_profit / total_loss if total_loss > 0 else 0

    total_pnl = balance - 10000

    # Type 1 stats
    type1_trades = trades_df[trades_df['type'] == 'Type1_HistoricalHigh']
    type1_wins = type1_trades[type1_trades['pnl'] > 0]
    type1_wr = len(type1_wins) / len(type1_trades) if len(type1_trades) > 0 else 0
    type1_pnl = type1_trades['pnl'].sum() if len(type1_trades) > 0 else 0
    type1_profit = type1_wins['pnl'].sum() if len(type1_wins) > 0 else 0
    type1_losses = type1_trades[type1_trades['pnl'] <= 0]
    type1_loss = abs(type1_losses['pnl'].sum()) if len(type1_losses) > 0 else 0
    type1_pf = type1_profit / type1_loss if type1_loss > 0 else 0

    # Type 2 stats
    type2_trades = trades_df[trades_df['type'] == 'Type2_LocalReversal']
    type2_wins = type2_trades[type2_trades['pnl'] > 0]
    type2_wr = len(type2_wins) / len(type2_trades) if len(type2_trades) > 0 else 0
    type2_pnl = type2_trades['pnl'].sum() if len(type2_trades) > 0 else 0
    type2_profit = type2_wins['pnl'].sum() if len(type2_wins) > 0 else 0
    type2_losses = type2_trades[type2_trades['pnl'] <= 0]
    type2_loss = abs(type2_losses['pnl'].sum()) if len(type2_losses) > 0 else 0
    type2_pf = type2_profit / type2_loss if type2_loss > 0 else 0

    return {
        'total_trades': total_trades,
        'win_rate': win_rate,
        'profit_factor': profit_factor,
        'max_drawdown_pct': max_dd,
        'max_daily_dd': max_daily_dd,
        'total_pnl': total_pnl,
        'final_balance': balance,
        'type1_trades': len(type1_trades),
        'type1_wr': type1_wr,
        'type1_pnl': type1_pnl,
        'type1_pf': type1_pf,
        'type2_trades': len(type2_trades),
        'type2_wr': type2_wr,
        'type2_pnl': type2_pnl,
        'type2_pf': type2_pf
    }

def main():
    print("=== SHORT STRATEGY: TWO ENTRY TYPES ===")
    print(f"Period: {START_DATE} - {END_DATE}")
    print(f"Risk: ${RISK_PER_TRADE}, TP: {TP_RR}R, Step Trailing enabled")
    print(f"\nType 1: Reversal After Historical High (Lookback={TYPE1_LOOKBACK_H4_BARS} H4)")
    print(f"Type 2: Local Reversal After Strong Move ({TYPE2_ATR_MULTIPLIER}+ ATR in {TYPE2_H4_LOOKBACK} H4 bars)\n")

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
    df_h4['atr'] = calculate_atr(df_h4, ATR_PERIOD)
    print(f"Resampled {len(df_h4):,} H4 bars\n")

    # Run backtest
    print("Running backtest...")
    result = run_backtest(df, df_h4)

    if result is None:
        print("No trades generated!")
        return

    # Print results
    print("\n" + "="*100)
    print("RESULTS: SHORT STRATEGY WITH TWO ENTRY TYPES")
    print("="*100)

    print("\nTYPE 1: REVERSAL AFTER HISTORICAL HIGH")
    print("-" * 100)
    print(f"Trades:          {result['type1_trades']}")
    print(f"Win Rate:        {result['type1_wr']:.1%}")
    print(f"Profit Factor:   {result['type1_pf']:.2f}")
    print(f"Total PnL:       ${result['type1_pnl']:,.2f}")

    print("\nTYPE 2: LOCAL REVERSAL AFTER STRONG MOVE")
    print("-" * 100)
    print(f"Trades:          {result['type2_trades']}")
    print(f"Win Rate:        {result['type2_wr']:.1%}")
    print(f"Profit Factor:   {result['type2_pf']:.2f}")
    print(f"Total PnL:       ${result['type2_pnl']:,.2f}")

    print("\nCOMBINED (TYPE 1 + TYPE 2)")
    print("-" * 100)
    print(f"Total Trades:    {result['total_trades']}")
    print(f"  Type 1:        {result['type1_trades']}")
    print(f"  Type 2:        {result['type2_trades']}")
    print(f"\nTotal PnL:       ${result['total_pnl']:,.2f}")
    print(f"  Type 1:        ${result['type1_pnl']:,.2f}")
    print(f"  Type 2:        ${result['type2_pnl']:,.2f}")
    print(f"\nWin Rate:        {result['win_rate']:.1%}")
    print(f"  Type 1:        {result['type1_wr']:.1%}")
    print(f"  Type 2:        {result['type2_wr']:.1%}")
    print(f"\nProfit Factor:   {result['profit_factor']:.2f}")
    print(f"  Type 1:        {result['type1_pf']:.2f}")
    print(f"  Type 2:        {result['type2_pf']:.2f}")
    print(f"\nMax Drawdown:    {result['max_drawdown_pct']:.2f}%")
    print(f"Max Daily DD:    {result['max_daily_dd']:.2f}%")
    print(f"Final Balance:   ${result['final_balance']:,.2f}")
    print("="*100)

    # Analysis
    print("\n=== ANALYSIS ===")
    print(f"Type 1 contribution: {result['type1_pnl'] / result['total_pnl'] * 100:.1f}% of total PnL")
    print(f"Type 2 contribution: {result['type2_pnl'] / result['total_pnl'] * 100:.1f}% of total PnL")
    print(f"Type 1 frequency: {result['type1_trades'] / 6:.1f} trades/year")
    print(f"Type 2 frequency: {result['type2_trades'] / 6:.1f} trades/year")

if __name__ == "__main__":
    main()
