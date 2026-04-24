"""
Combined LONG + SHORT Strategy Backtest
LONG: Session Breakout
SHORT: Reversal After Historical High
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
import numpy as np
from datetime import datetime
from astra_v2.data.dukascopy import load_timeframe

# Common parameters
RISK_PER_TRADE = 158
TP_RR = 5.5
ATR_PERIOD = 14
ATR_BUFFER = 0.5
START_DATE = "2020-01-01"
END_DATE = "2026-04-18"

# LONG Session Breakout parameters
LONG_SESSIONS = {
    'asian': (7, 10),
    'london': (13, 16),
    'ny': (18, 21)
}

# SHORT Reversal parameters
SHORT_LOOKBACK_H4_BARS = 5
SHORT_H4_REVERSAL_BARS = 1

# Step Trailing
USE_STEP_TRAILING = True

# H4 EMA20 Filter
USE_H4_EMA_FILTER = True
H4_EMA_PERIOD = 20

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

def apply_step_trailing(active_trade, current_low, is_long=True):
    """Apply step trailing stop logic"""
    if not USE_STEP_TRAILING:
        return

    if is_long:
        risk = active_trade['entry'] - active_trade['initial_sl']
        profit_in_r = (current_low - active_trade['entry']) / risk

        # 2R -> 1R, 3R -> 2R, 4R -> 3R, 5R -> 4R
        if profit_in_r >= 5.0:
            new_sl = active_trade['entry'] + 4.0 * risk
            active_trade['sl'] = max(active_trade['sl'], new_sl)
        elif profit_in_r >= 4.0:
            new_sl = active_trade['entry'] + 3.0 * risk
            active_trade['sl'] = max(active_trade['sl'], new_sl)
        elif profit_in_r >= 3.0:
            new_sl = active_trade['entry'] + 2.0 * risk
            active_trade['sl'] = max(active_trade['sl'], new_sl)
        elif profit_in_r >= 2.0:
            new_sl = active_trade['entry'] + 1.0 * risk
            active_trade['sl'] = max(active_trade['sl'], new_sl)
    else:  # SHORT
        risk = active_trade['initial_sl'] - active_trade['entry']
        profit_in_r = (active_trade['entry'] - current_low) / risk

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

def run_combined_backtest(df, df_h4):
    trades = []
    active_long = None
    active_short = None
    balance = 10000
    peak_balance = 10000
    max_dd = 0
    max_daily_dd = 0

    # SHORT reversal tracking
    short_reversal_active = False
    short_reversal_h4_high = None

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
        opens = day_data['open'].to_numpy()
        atrs = day_data['atr'].to_numpy()
        hours = np.array([t.hour for t in day_data.index])
        times = day_data.index.to_numpy()

        # Track session highs/lows
        session_highs = {}
        session_lows = {}

        for i in range(len(day_data)):
            current_time = times[i]
            hour = hours[i]

            # Get current H4 bar
            h4_bars = df_h4[df_h4.index <= current_time]
            if len(h4_bars) < SHORT_LOOKBACK_H4_BARS + SHORT_H4_REVERSAL_BARS + 1:
                continue

            current_h4 = h4_bars.iloc[-1]

            # === LONG TRADE MANAGEMENT ===
            if active_long is not None:
                apply_step_trailing(active_long, lows[i], is_long=True)

                # Check SL/TP
                if lows[i] <= active_long['sl']:
                    pnl = (active_long['sl'] - active_long['entry']) * active_long['size']
                    balance += pnl
                    trades.append({
                        'entry_time': active_long['entry_time'],
                        'exit_time': times[i],
                        'entry_price': active_long['entry'],
                        'exit_price': active_long['sl'],
                        'pnl': pnl,
                        'exit_reason': 'sl',
                        'direction': 'LONG'
                    })
                    active_long = None
                elif highs[i] >= active_long['tp']:
                    pnl = (active_long['tp'] - active_long['entry']) * active_long['size']
                    balance += pnl
                    trades.append({
                        'entry_time': active_long['entry_time'],
                        'exit_time': times[i],
                        'entry_price': active_long['entry'],
                        'exit_price': active_long['tp'],
                        'pnl': pnl,
                        'exit_reason': 'tp',
                        'direction': 'LONG'
                    })
                    active_long = None

            # === SHORT TRADE MANAGEMENT ===
            if active_short is not None:
                apply_step_trailing(active_short, lows[i], is_long=False)

                # Check SL/TP
                if highs[i] >= active_short['sl']:
                    pnl = (active_short['entry'] - active_short['sl']) * active_short['size']
                    balance += pnl
                    trades.append({
                        'entry_time': active_short['entry_time'],
                        'exit_time': times[i],
                        'entry_price': active_short['entry'],
                        'exit_price': active_short['sl'],
                        'pnl': pnl,
                        'exit_reason': 'sl',
                        'direction': 'SHORT'
                    })
                    active_short = None
                    short_reversal_active = False
                elif lows[i] <= active_short['tp']:
                    pnl = (active_short['entry'] - active_short['tp']) * active_short['size']
                    balance += pnl
                    trades.append({
                        'entry_time': active_short['entry_time'],
                        'exit_time': times[i],
                        'entry_price': active_short['entry'],
                        'exit_price': active_short['tp'],
                        'pnl': pnl,
                        'exit_reason': 'tp',
                        'direction': 'SHORT'
                    })
                    active_short = None
                    short_reversal_active = False

            # Update max DD
            if balance > peak_balance:
                peak_balance = balance
            dd = (peak_balance - balance) / peak_balance * 100
            if dd > max_dd:
                max_dd = dd

            atr = atrs[i]
            if np.isnan(atr):
                continue

            # === LONG SESSION BREAKOUT LOGIC ===
            if active_long is None:
                # Track session ranges
                for session_name, (start_hour, end_hour) in LONG_SESSIONS.items():
                    if start_hour <= hour < end_hour:
                        if session_name not in session_highs:
                            session_highs[session_name] = highs[i]
                            session_lows[session_name] = lows[i]
                        else:
                            session_highs[session_name] = max(session_highs[session_name], highs[i])
                            session_lows[session_name] = min(session_lows[session_name], lows[i])

                # Check for breakout
                for session_name, (start_hour, end_hour) in LONG_SESSIONS.items():
                    if session_name in session_highs and hour >= end_hour:
                        session_high = session_highs[session_name]

                        # Breakout above session high
                        if closes[i] > session_high:
                            # Check H4 EMA20 filter
                            if USE_H4_EMA_FILTER:
                                if pd.isna(current_h4['ema20']):
                                    continue
                                if current_h4['close'] < current_h4['ema20']:
                                    continue  # Skip LONG if below EMA20

                            entry = closes[i]
                            sl = session_lows[session_name] - ATR_BUFFER * atr
                            risk = entry - sl

                            if risk <= 0:
                                continue

                            tp = entry + risk * TP_RR
                            size = RISK_PER_TRADE / risk

                            active_long = {
                                'entry': entry,
                                'sl': sl,
                                'initial_sl': sl,
                                'tp': tp,
                                'size': size,
                                'entry_time': times[i],
                                'session': session_name
                            }
                            break

            # === SHORT REVERSAL LOGIC ===
            if active_short is None:
                # Check for reversal setup
                if not short_reversal_active:
                    # Check if current H4 made new high
                    lookback_highs = h4_bars.iloc[-SHORT_LOOKBACK_H4_BARS-1:-1]['high']
                    historical_high = lookback_highs.max()

                    if current_h4['high'] > historical_high:
                        # New high detected, check for reversal (1 H4 bar down)
                        prev_h4 = h4_bars.iloc[-2]
                        if current_h4['close'] < prev_h4['close']:
                            short_reversal_active = True
                            short_reversal_h4_high = current_h4['high']

                # If reversal is active, look for M15 entry
                if short_reversal_active:
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
                            sl = short_reversal_h4_high + ATR_BUFFER * atr
                            risk = sl - entry

                            if risk <= 0:
                                continue

                            tp = entry - risk * TP_RR
                            size = RISK_PER_TRADE / risk

                            active_short = {
                                'entry': entry,
                                'sl': sl,
                                'initial_sl': sl,
                                'tp': tp,
                                'size': size,
                                'entry_time': times[i]
                            }
                            short_reversal_active = False

        # Calculate daily DD
        if day_start_balance > 0:
            daily_dd = (day_start_balance - balance) / day_start_balance * 100
            if daily_dd > max_daily_dd:
                max_daily_dd = daily_dd

    # Close any remaining active trades
    if active_long is not None:
        last_bar = df.iloc[-1]
        pnl = (last_bar['close'] - active_long['entry']) * active_long['size']
        balance += pnl
        trades.append({
            'entry_time': active_long['entry_time'],
            'exit_time': df.index[-1],
            'entry_price': active_long['entry'],
            'exit_price': last_bar['close'],
            'pnl': pnl,
            'exit_reason': 'eod',
            'direction': 'LONG'
        })

    if active_short is not None:
        last_bar = df.iloc[-1]
        pnl = (active_short['entry'] - last_bar['close']) * active_short['size']
        balance += pnl
        trades.append({
            'entry_time': active_short['entry_time'],
            'exit_time': df.index[-1],
            'entry_price': active_short['entry'],
            'exit_price': last_bar['close'],
            'pnl': pnl,
            'exit_reason': 'eod',
            'direction': 'SHORT'
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

    # LONG stats
    long_trades = trades_df[trades_df['direction'] == 'LONG']
    long_wins = long_trades[long_trades['pnl'] > 0]
    long_wr = len(long_wins) / len(long_trades) if len(long_trades) > 0 else 0
    long_pnl = long_trades['pnl'].sum() if len(long_trades) > 0 else 0

    # SHORT stats
    short_trades = trades_df[trades_df['direction'] == 'SHORT']
    short_wins = short_trades[short_trades['pnl'] > 0]
    short_wr = len(short_wins) / len(short_trades) if len(short_trades) > 0 else 0
    short_pnl = short_trades['pnl'].sum() if len(short_trades) > 0 else 0

    return {
        'total_trades': total_trades,
        'win_rate': win_rate,
        'profit_factor': profit_factor,
        'max_drawdown_pct': max_dd,
        'max_daily_dd': max_daily_dd,
        'total_pnl': total_pnl,
        'final_balance': balance,
        'long_trades': len(long_trades),
        'long_wr': long_wr,
        'long_pnl': long_pnl,
        'short_trades': len(short_trades),
        'short_wr': short_wr,
        'short_pnl': short_pnl
    }

def main():
    print("=== COMBINED LONG + SHORT BACKTEST ===")
    print(f"Period: {START_DATE} - {END_DATE}")
    print(f"Risk per trade: ${RISK_PER_TRADE}, TP: {TP_RR}R")
    print(f"LONG: Session Breakout (Asian 07-10, London 13-16, NY 18-21)")
    print(f"SHORT: Reversal After High (Lookback={SHORT_LOOKBACK_H4_BARS} H4, Rev={SHORT_H4_REVERSAL_BARS} H4)\n")

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

    # Run backtest
    print("Running combined backtest...")
    result = run_combined_backtest(df, df_h4)

    if result is None:
        print("No trades generated!")
        return

    # Print results
    print("\n" + "="*100)
    print("COMBINED RESULTS")
    print("="*100)
    print(f"Total Trades:      {result['total_trades']}")
    print(f"  LONG Trades:     {result['long_trades']}")
    print(f"  SHORT Trades:    {result['short_trades']}")
    print(f"\nTotal PnL:         ${result['total_pnl']:,.2f}")
    print(f"  LONG PnL:        ${result['long_pnl']:,.2f}")
    print(f"  SHORT PnL:       ${result['short_pnl']:,.2f}")
    print(f"\nWin Rate:          {result['win_rate']:.1%}")
    print(f"  LONG WR:         {result['long_wr']:.1%}")
    print(f"  SHORT WR:        {result['short_wr']:.1%}")
    print(f"\nProfit Factor:     {result['profit_factor']:.2f}")
    print(f"Max Drawdown:      {result['max_drawdown_pct']:.2f}%")
    print(f"Max Daily DD:      {result['max_daily_dd']:.2f}%")
    print(f"Final Balance:     ${result['final_balance']:,.2f}")
    print("="*100)

    # Check targets
    print("\n=== TARGET ANALYSIS ===")
    target_pnl = 40134
    target_dd = 10.0
    target_daily_dd = 5.0

    pnl_pass = result['total_pnl'] > target_pnl
    dd_pass = result['max_drawdown_pct'] < target_dd
    daily_dd_pass = result['max_daily_dd'] < target_daily_dd

    print(f"PnL Target (>${target_pnl:,}):        {'✓ PASS' if pnl_pass else '✗ FAIL'} (${result['total_pnl']:,.2f})")
    print(f"Max DD Target (<{target_dd}%):      {'✓ PASS' if dd_pass else '✗ FAIL'} ({result['max_drawdown_pct']:.2f}%)")
    print(f"Daily DD Target (<{target_daily_dd}%):    {'✓ PASS' if daily_dd_pass else '✗ FAIL'} ({result['max_daily_dd']:.2f}%)")

    overall_pass = pnl_pass and dd_pass and daily_dd_pass
    print(f"\nOVERALL: {'✓✓✓ ALL TARGETS MET ✓✓✓' if overall_pass else '✗ TARGETS NOT MET'}")

if __name__ == "__main__":
    main()
