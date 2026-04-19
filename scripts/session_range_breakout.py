"""
Session Range Breakout Strategy for XAUUSD M15
Trades Asian and London session range breakouts
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
import numpy as np
from datetime import datetime
from astra_v2.data.dukascopy import load_timeframe

# Parameters
STOP_BUFFER_ATR = 0.5
TP_RR = 2.0
MIN_RANGE_ATR = 1.0
MAX_RANGE_ATR = 5.0
ATR_PERIOD = 20
RISK_PER_TRADE = 100  # USD

def calculate_atr(df, period=20):
    high = df['high']
    low = df['low']
    close = df['close']

    tr1 = high - low
    tr2 = abs(high - close.shift(1))
    tr3 = abs(low - close.shift(1))

    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    atr = tr.rolling(window=period).mean()

    return atr

def get_session_range(df, start_hour, end_hour):
    """Get high/low range for a session"""
    mask = (df.index.hour >= start_hour) & (df.index.hour < end_hour)
    session_bars = df[mask]

    if len(session_bars) == 0:
        return None, None

    range_high = session_bars['high'].max()
    range_low = session_bars['low'].min()

    return range_high, range_low

def run_backtest(start_date, end_date):
    # Load data
    print(f"Loading XAUUSD M15 data from {start_date} to {end_date}...")
    df = load_timeframe("M15", start=start_date, end=end_date, symbol="XAUUSD")
    print(f"Loaded {len(df):,} bars")

    # Ensure datetime index
    if 'datetime' in df.columns:
        df.set_index('datetime', inplace=True)
    df = df.sort_index()

    # Calculate ATR
    df['atr'] = calculate_atr(df, ATR_PERIOD)

    # Results
    trades = []
    active_trade = None
    balance = 10000
    peak_balance = 10000
    max_dd = 0

    # Group by date
    dates = df.index.date
    unique_dates = sorted(set(dates))

    for date in unique_dates:
        day_data = df[df.index.date == date]

        if len(day_data) < 10:
            continue

        # Asian session range (00:00-07:00 UTC)
        asian_high, asian_low = get_session_range(day_data, 0, 7)

        # London session range (07:00-12:00 UTC)
        london_high, london_low = get_session_range(day_data, 7, 12)

        if asian_high is None or london_high is None:
            continue

        # Check for Asian breakout in London open window (07:00-10:00)
        london_open_window = day_data[(day_data.index.hour >= 7) & (day_data.index.hour < 10)]

        for idx, bar in london_open_window.iterrows():
            if active_trade is not None:
                # Check exit conditions
                if active_trade['direction'] == 'LONG':
                    if bar['low'] <= active_trade['sl']:
                        pnl = (active_trade['sl'] - active_trade['entry']) * active_trade['size']
                        balance += pnl
                        active_trade['exit'] = active_trade['sl']
                        active_trade['pnl'] = pnl
                        active_trade['status'] = 'sl'
                        trades.append(active_trade)
                        active_trade = None
                    elif bar['high'] >= active_trade['tp']:
                        pnl = (active_trade['tp'] - active_trade['entry']) * active_trade['size']
                        balance += pnl
                        active_trade['exit'] = active_trade['tp']
                        active_trade['pnl'] = pnl
                        active_trade['status'] = 'tp'
                        trades.append(active_trade)
                        active_trade = None
                else:  # SHORT
                    if bar['high'] >= active_trade['sl']:
                        pnl = (active_trade['entry'] - active_trade['sl']) * active_trade['size']
                        balance += pnl
                        active_trade['exit'] = active_trade['sl']
                        active_trade['pnl'] = pnl
                        active_trade['status'] = 'sl'
                        trades.append(active_trade)
                        active_trade = None
                    elif bar['low'] <= active_trade['tp']:
                        pnl = (active_trade['entry'] - active_trade['tp']) * active_trade['size']
                        balance += pnl
                        active_trade['exit'] = active_trade['tp']
                        active_trade['pnl'] = pnl
                        active_trade['status'] = 'tp'
                        trades.append(active_trade)
                        active_trade = None

                # Update max DD
                if balance > peak_balance:
                    peak_balance = balance
                dd = (peak_balance - balance) / peak_balance * 100
                if dd > max_dd:
                    max_dd = dd

                if active_trade is not None:
                    continue

            # Check for Asian range breakout
            atr = bar['atr']
            if pd.isna(atr):
                continue

            asian_range = asian_high - asian_low

            # Filter by range size
            if asian_range < MIN_RANGE_ATR * atr or asian_range > MAX_RANGE_ATR * atr:
                continue

            # Breakout up
            if bar['close'] > asian_high and active_trade is None:
                entry = bar['close']
                sl = asian_low - STOP_BUFFER_ATR * atr
                risk = entry - sl
                tp = entry + risk * TP_RR
                size = RISK_PER_TRADE / risk

                active_trade = {
                    'entry': entry,
                    'sl': sl,
                    'tp': tp,
                    'size': size,
                    'direction': 'LONG',
                    'entry_time': idx,
                    'range_type': 'asian'
                }

            # Breakout down
            elif bar['close'] < asian_low and active_trade is None:
                entry = bar['close']
                sl = asian_high + STOP_BUFFER_ATR * atr
                risk = sl - entry
                tp = entry - risk * TP_RR
                size = RISK_PER_TRADE / risk

                active_trade = {
                    'entry': entry,
                    'sl': sl,
                    'tp': tp,
                    'size': size,
                    'direction': 'SHORT',
                    'entry_time': idx,
                    'range_type': 'asian'
                }

        # Check for London breakout in NY open window (13:00-16:00)
        ny_open_window = day_data[(day_data.index.hour >= 13) & (day_data.index.hour < 16)]

        for idx, bar in ny_open_window.iterrows():
            if active_trade is not None:
                # Check exit conditions (same as above)
                if active_trade['direction'] == 'LONG':
                    if bar['low'] <= active_trade['sl']:
                        pnl = (active_trade['sl'] - active_trade['entry']) * active_trade['size']
                        balance += pnl
                        active_trade['exit'] = active_trade['sl']
                        active_trade['pnl'] = pnl
                        active_trade['status'] = 'sl'
                        trades.append(active_trade)
                        active_trade = None
                    elif bar['high'] >= active_trade['tp']:
                        pnl = (active_trade['tp'] - active_trade['entry']) * active_trade['size']
                        balance += pnl
                        active_trade['exit'] = active_trade['tp']
                        active_trade['pnl'] = pnl
                        active_trade['status'] = 'tp'
                        trades.append(active_trade)
                        active_trade = None
                else:  # SHORT
                    if bar['high'] >= active_trade['sl']:
                        pnl = (active_trade['entry'] - active_trade['sl']) * active_trade['size']
                        balance += pnl
                        active_trade['exit'] = active_trade['sl']
                        active_trade['pnl'] = pnl
                        active_trade['status'] = 'sl'
                        trades.append(active_trade)
                        active_trade = None
                    elif bar['low'] <= active_trade['tp']:
                        pnl = (active_trade['entry'] - active_trade['tp']) * active_trade['size']
                        balance += pnl
                        active_trade['exit'] = active_trade['tp']
                        active_trade['pnl'] = pnl
                        active_trade['status'] = 'tp'
                        trades.append(active_trade)
                        active_trade = None

                # Update max DD
                if balance > peak_balance:
                    peak_balance = balance
                dd = (peak_balance - balance) / peak_balance * 100
                if dd > max_dd:
                    max_dd = dd

                if active_trade is not None:
                    continue

            # Check for London range breakout
            atr = bar['atr']
            if pd.isna(atr):
                continue

            london_range = london_high - london_low

            # Filter by range size
            if london_range < MIN_RANGE_ATR * atr or london_range > MAX_RANGE_ATR * atr:
                continue

            # Breakout up
            if bar['close'] > london_high and active_trade is None:
                entry = bar['close']
                sl = london_low - STOP_BUFFER_ATR * atr
                risk = entry - sl
                tp = entry + risk * TP_RR
                size = RISK_PER_TRADE / risk

                active_trade = {
                    'entry': entry,
                    'sl': sl,
                    'tp': tp,
                    'size': size,
                    'direction': 'LONG',
                    'entry_time': idx,
                    'range_type': 'london'
                }

            # Breakout down
            elif bar['close'] < london_low and active_trade is None:
                entry = bar['close']
                sl = london_high + STOP_BUFFER_ATR * atr
                risk = sl - entry
                tp = entry - risk * TP_RR
                size = RISK_PER_TRADE / risk

                active_trade = {
                    'entry': entry,
                    'sl': sl,
                    'tp': tp,
                    'size': size,
                    'direction': 'SHORT',
                    'entry_time': idx,
                    'range_type': 'london'
                }

    # Close any remaining trade at end
    if active_trade is not None:
        last_bar = df.iloc[-1]
        if active_trade['direction'] == 'LONG':
            pnl = (last_bar['close'] - active_trade['entry']) * active_trade['size']
        else:
            pnl = (active_trade['entry'] - last_bar['close']) * active_trade['size']

        balance += pnl
        active_trade['exit'] = last_bar['close']
        active_trade['pnl'] = pnl
        active_trade['status'] = 'eod'
        trades.append(active_trade)

    # Calculate statistics
    if len(trades) == 0:
        print("No trades executed")
        return

    trades_df = pd.DataFrame(trades)

    total_trades = len(trades_df)
    wins = trades_df[trades_df['pnl'] > 0]
    losses = trades_df[trades_df['pnl'] <= 0]

    win_rate = len(wins) / total_trades if total_trades > 0 else 0

    total_profit = wins['pnl'].sum() if len(wins) > 0 else 0
    total_loss = abs(losses['pnl'].sum()) if len(losses) > 0 else 0
    profit_factor = total_profit / total_loss if total_loss > 0 else 0

    total_pnl = balance - 10000

    print(f"\n=== Session Range Breakout Results ===")
    print(f"Period: {df.index[0]} to {df.index[-1]}")
    print(f"Total Trades: {total_trades}")
    print(f"Win Rate: {win_rate:.1%}")
    print(f"Profit Factor: {profit_factor:.3f}")
    print(f"Max Drawdown: {max_dd:.2f}%")
    print(f"Total PnL: ${total_pnl:,.2f}")
    print(f"Final Balance: ${balance:,.2f}")
    print(f"\nAsian breakouts: {len(trades_df[trades_df['range_type'] == 'asian'])}")
    print(f"London breakouts: {len(trades_df[trades_df['range_type'] == 'london'])}")

if __name__ == "__main__":
    # Run backtest for 2020-2026
    run_backtest("2020-01-01", "2026-04-18")
