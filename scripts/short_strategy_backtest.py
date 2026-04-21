"""
SHORT Strategy - Separate from LONG
Only trades during strong bearish H4 trends
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
import numpy as np
from astra_v2.data.dukascopy import load_timeframe

# SHORT strategy parameters (conservative)
ASIAN_SHORT_PARAMS = {
    'tp_rr': 3.0,
    'stop_buffer_atr': 0.2,
    'min_range_atr': 0.7,
    'max_range_atr': 3.0,
    'trailing_start': None,
    'trailing_distance': 0.2,
    'range_hours': (0, 7),
    'breakout_hours': (7, 10)
}

LONDON_SHORT_PARAMS = {
    'tp_rr': 3.5,
    'stop_buffer_atr': 0.4,
    'min_range_atr': 0.3,
    'max_range_atr': 3.0,
    'trailing_start': None,
    'trailing_distance': 0.2,
    'range_hours': (7, 12),
    'breakout_hours': (13, 16)
}

NY_SHORT_PARAMS = {
    'tp_rr': 4.0,
    'stop_buffer_atr': 0.4,
    'min_range_atr': 0.5,
    'max_range_atr': 3.0,
    'trailing_start': None,
    'trailing_distance': 0.1,
    'range_hours': (13, 17),
    'breakout_hours': (18, 21)
}

ATR_PERIOD = 20
RISK_PER_TRADE = 100  # Conservative for SHORT
START_DATE = "2020-01-01"
END_DATE = "2026-04-18"
H4_EMA_PERIOD = 20
H4_EMA_LONG_PERIOD = 50

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

def calculate_ema(df, period):
    return df['close'].ewm(span=period, adjust=False).mean()

def get_session_range(df, start_hour, end_hour):
    mask = (df.index.hour >= start_hour) & (df.index.hour < end_hour)
    session_bars = df[mask]
    if len(session_bars) == 0:
        return None, None
    range_high = session_bars['high'].max()
    range_low = session_bars['low'].min()
    return range_high, range_low

def run_short_backtest():
    print("=" * 80)
    print("=== SHORT STRATEGY BACKTEST ===")
    print("Only trades during strong bearish H4 trends")
    print("=" * 80)
    print()

    # Load data
    print("Loading M15 data...")
    df = load_timeframe("M15", start=START_DATE, end=END_DATE, symbol="XAUUSD")
    if 'datetime' in df.columns:
        df.set_index('datetime', inplace=True)
    df = df.sort_index()
    df['atr'] = calculate_atr(df, ATR_PERIOD)
    print(f"Loaded {len(df):,} M15 bars")

    print("Resampling to H4...")
    df_h4 = df.resample('4h').agg({'open':'first','high':'max','low':'min','close':'last'}).dropna()
    df_h4['ema20'] = calculate_ema(df_h4, H4_EMA_PERIOD)
    df_h4['ema50'] = calculate_ema(df_h4, H4_EMA_LONG_PERIOD)
    df_h4['atr'] = calculate_atr(df_h4, ATR_PERIOD)

    # Calculate EMA slope (is it falling?)
    df_h4['ema20_slope'] = df_h4['ema20'].diff()

    print(f"Resampled {len(df_h4):,} H4 bars")
    print()

    trades = []
    active_trades = {}
    balance = 10000
    peak_balance = 10000
    max_dd = 0
    max_daily_dd = 0

    dates = df.index.date
    unique_dates = sorted(set(dates))

    for date in unique_dates:
        day_start_balance = balance
        day_data = df[df.index.date == date]
        if len(day_data) < 10:
            continue

        asian_high, asian_low = get_session_range(day_data, *ASIAN_SHORT_PARAMS['range_hours'])
        london_high, london_low = get_session_range(day_data, *LONDON_SHORT_PARAMS['range_hours'])
        ny_high, ny_low = get_session_range(day_data, *NY_SHORT_PARAMS['range_hours'])

        highs = day_data['high'].to_numpy()
        lows = day_data['low'].to_numpy()
        closes = day_data['close'].to_numpy()
        atrs = day_data['atr'].to_numpy()
        times = day_data.index.to_numpy()
        hours = np.array([t.hour for t in day_data.index])

        for i in range(len(day_data)):
            atr = atrs[i]
            if np.isnan(atr):
                continue
            hour = hours[i]

            # Check exits
            for session_name in list(active_trades.keys()):
                trade = active_trades[session_name]

                exit_trade = False
                if trade['direction'] == 'SHORT':
                    if highs[i] >= trade['sl']:
                        pnl = (trade['entry'] - trade['sl']) * trade['size']
                        balance += pnl
                        trade['pnl'] = pnl
                        trade['status'] = 'sl'
                        exit_trade = True
                    elif lows[i] <= trade['tp']:
                        pnl = (trade['entry'] - trade['tp']) * trade['size']
                        balance += pnl
                        trade['pnl'] = pnl
                        trade['status'] = 'tp'
                        exit_trade = True

                if exit_trade:
                    trades.append(trade)
                    del active_trades[session_name]
                    if balance > peak_balance:
                        peak_balance = balance
                    dd = (peak_balance - balance) / peak_balance * 100
                    if dd > max_dd:
                        max_dd = dd

            # Entry logic - only SHORT with strict bearish filters
            current_time = times[i]
            h4_bar = df_h4[df_h4.index <= current_time].iloc[-1] if len(df_h4[df_h4.index <= current_time]) > 0 else None

            if h4_bar is None or pd.isna(h4_bar['ema20']) or pd.isna(h4_bar['ema50']) or pd.isna(h4_bar['ema20_slope']):
                continue

            # STRICT BEARISH FILTER
            is_bearish = (
                h4_bar['close'] < h4_bar['ema20'] and  # Below EMA20
                h4_bar['close'] < h4_bar['ema50'] and  # Below EMA50 (strong bear)
                h4_bar['ema20_slope'] < 0  # EMA20 falling
            )

            if not is_bearish:
                continue  # Skip if not strong bearish trend

            # Asian SHORT
            if ASIAN_SHORT_PARAMS['breakout_hours'][0] <= hour < ASIAN_SHORT_PARAMS['breakout_hours'][1]:
                if asian_low is not None and 'asian' not in active_trades:
                    asian_range = asian_high - asian_low
                    if ASIAN_SHORT_PARAMS['min_range_atr'] * atr <= asian_range <= ASIAN_SHORT_PARAMS['max_range_atr'] * atr:
                        if closes[i] < asian_low:
                            entry = closes[i]
                            sl = asian_high + ASIAN_SHORT_PARAMS['stop_buffer_atr'] * atr
                            risk = sl - entry
                            tp = entry - risk * ASIAN_SHORT_PARAMS['tp_rr']
                            size = RISK_PER_TRADE / risk
                            active_trades['asian'] = {
                                'entry': entry, 'sl': sl, 'initial_sl': sl, 'tp': tp,
                                'size': size, 'direction': 'SHORT', 'range_type': 'asian'
                            }

            # London SHORT
            if LONDON_SHORT_PARAMS['breakout_hours'][0] <= hour < LONDON_SHORT_PARAMS['breakout_hours'][1]:
                if london_low is not None and 'london' not in active_trades:
                    london_range = london_high - london_low
                    if LONDON_SHORT_PARAMS['min_range_atr'] * atr <= london_range <= LONDON_SHORT_PARAMS['max_range_atr'] * atr:
                        if closes[i] < london_low:
                            entry = closes[i]
                            sl = london_high + LONDON_SHORT_PARAMS['stop_buffer_atr'] * atr
                            risk = sl - entry
                            tp = entry - risk * LONDON_SHORT_PARAMS['tp_rr']
                            size = RISK_PER_TRADE / risk
                            active_trades['london'] = {
                                'entry': entry, 'sl': sl, 'initial_sl': sl, 'tp': tp,
                                'size': size, 'direction': 'SHORT', 'range_type': 'london'
                            }

            # NY SHORT
            if NY_SHORT_PARAMS['breakout_hours'][0] <= hour < NY_SHORT_PARAMS['breakout_hours'][1]:
                if ny_low is not None and 'ny' not in active_trades:
                    ny_range = ny_high - ny_low
                    if NY_SHORT_PARAMS['min_range_atr'] * atr <= ny_range <= NY_SHORT_PARAMS['max_range_atr'] * atr:
                        if closes[i] < ny_low:
                            entry = closes[i]
                            sl = ny_high + NY_SHORT_PARAMS['stop_buffer_atr'] * atr
                            risk = sl - entry
                            tp = entry - risk * NY_SHORT_PARAMS['tp_rr']
                            size = RISK_PER_TRADE / risk
                            active_trades['ny'] = {
                                'entry': entry, 'sl': sl, 'initial_sl': sl, 'tp': tp,
                                'size': size, 'direction': 'SHORT', 'range_type': 'ny'
                            }

        if day_start_balance > 0:
            daily_dd = (day_start_balance - balance) / day_start_balance * 100
            if daily_dd > max_daily_dd:
                max_daily_dd = daily_dd

    # Close remaining
    for session_name, trade in active_trades.items():
        last_bar = df.iloc[-1]
        pnl = (trade['entry'] - last_bar['close']) * trade['size']
        balance += pnl
        trade['pnl'] = pnl
        trade['status'] = 'eod'
        trades.append(trade)

    trades_df = pd.DataFrame(trades)

    if len(trades_df) == 0:
        print("No SHORT trades generated!")
        return None

    total_pnl = balance - 10000
    wins = trades_df[trades_df['pnl'] > 0]
    losses = trades_df[trades_df['pnl'] <= 0]
    win_rate = len(wins) / len(trades_df) if len(trades_df) > 0 else 0
    total_profit = wins['pnl'].sum() if len(wins) > 0 else 0
    total_loss = abs(losses['pnl'].sum()) if len(losses) > 0 else 0
    profit_factor = total_profit / total_loss if total_loss > 0 else 0

    print("=" * 80)
    print("=== SHORT STRATEGY RESULTS ===")
    print("=" * 80)
    print(f"\nTotal Trades: {len(trades_df)}")
    print(f"Win Rate: {win_rate:.1%}")
    print(f"Profit Factor: {profit_factor:.3f}")
    print(f"Total PnL: ${total_pnl:,.0f}")
    print(f"Final Balance: ${balance:,.0f}")
    print(f"Max Drawdown: {max_dd:.2f}%")
    print(f"Max Daily Drawdown: {max_daily_dd:.2f}%")

    print(f"\n=== BREAKDOWN BY SESSION ===")
    for session in ['asian', 'london', 'ny']:
        session_trades = trades_df[trades_df['range_type'] == session]
        if len(session_trades) > 0:
            session_pnl = session_trades['pnl'].sum()
            session_wins = len(session_trades[session_trades['pnl'] > 0])
            session_wr = session_wins / len(session_trades)
            print(f"{session.upper()}: {len(session_trades)} trades, PnL=${session_pnl:,.0f}, WR={session_wr:.1%}")

    print("=" * 80)

    return trades_df

if __name__ == "__main__":
    run_short_backtest()
