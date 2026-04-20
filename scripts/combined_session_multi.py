"""
Combined Session Backtest - All 3 sessions with optimal parameters
Tests Asian + London + NY together to verify combined DD and DailyDD
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
import numpy as np
from astra_v2.data.dukascopy import load_timeframe

# Optimal parameters from extended grid search optimization
ASIAN_PARAMS = {
    'tp_rr': 3.0,
    'stop_buffer_atr': 0.1,
    'min_range_atr': 0.7,
    'max_range_atr': 3.0,
    'trailing_start': None,
    'trailing_distance': 0.3,
    'range_hours': (0, 7),
    'breakout_hours': (7, 10)
}

LONDON_PARAMS = {
    'tp_rr': 3.5,
    'stop_buffer_atr': 0.3,
    'min_range_atr': 0.3,
    'max_range_atr': 3.0,
    'trailing_start': 2.0,
    'trailing_distance': 0.3,
    'range_hours': (7, 12),
    'breakout_hours': (13, 16)
}

NY_PARAMS = {
    'tp_rr': 3.5,
    'stop_buffer_atr': 0.3,
    'min_range_atr': 0.5,
    'max_range_atr': 3.0,
    'trailing_start': None,
    'trailing_distance': 0.3,
    'range_hours': (13, 17),
    'breakout_hours': (18, 21)
}

ATR_PERIOD = 20
RISK_PER_TRADE = 100
START_DATE = "2020-01-01"
END_DATE = "2024-12-31"  # Changed to match available data for all symbols

# EMA Trend Filter
USE_TREND_FILTER = False
EMA_FAST = 50
EMA_SLOW = 200

# Volatility Filter
USE_VOLATILITY_FILTER = False  # Disabled - filters out all trades
ATR_MA_BARS = 96  # 96 bars on M15 = 1 day (24h)
VOLATILITY_THRESHOLD = 0.5  # current_atr must be > 0.5 * atr_ma (lowered from 0.7)

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
    """Calculate Exponential Moving Average"""
    return df['close'].ewm(span=period, adjust=False).mean()

def get_session_range(df, start_hour, end_hour):
    mask = (df.index.hour >= start_hour) & (df.index.hour < end_hour)
    session_bars = df[mask]

    if len(session_bars) == 0:
        return None, None

    range_high = session_bars['high'].max()
    range_low = session_bars['low'].min()

    return range_high, range_low

def run_combined_backtest(symbol):
    print(f"=== Testing {symbol} ===")
    print(f"All 3 sessions with optimal parameters\n")

    # Load data
    print(f"Loading {symbol} data...")
    df = load_timeframe("M15", start=START_DATE, end=END_DATE, symbol=symbol)
    print(f"Loaded {len(df):,} bars\n")

    if 'datetime' in df.columns:
        df.set_index('datetime', inplace=True)
    df = df.sort_index()

    # Calculate ATR
    df['atr'] = calculate_atr(df, ATR_PERIOD)

    # Calculate ATR MA for volatility filter (on same M15 timeframe)
    if USE_VOLATILITY_FILTER:
        print(f"Calculating ATR MA{ATR_MA_BARS} bars for volatility filter...")
        df['atr_ma'] = df['atr'].rolling(window=ATR_MA_BARS).mean()
        print(f"Volatility filter enabled: current_atr must be > {VOLATILITY_THRESHOLD} * atr_ma (96 bars = 1 day)\n")
    else:
        print("Volatility filter disabled\n")

    # Calculate EMA for trend filter
    if USE_TREND_FILTER:
        print(f"Calculating EMA{EMA_FAST} and EMA{EMA_SLOW} for trend filter...")
        df['ema_fast'] = calculate_ema(df, EMA_FAST)
        df['ema_slow'] = calculate_ema(df, EMA_SLOW)
        print(f"Trend filter enabled: LONG requires EMA{EMA_FAST} > EMA{EMA_SLOW}, SHORT requires EMA{EMA_FAST} < EMA{EMA_SLOW}\n")
    else:
        print("Trend filter disabled\n")

    trades = []
    active_trades = {}  # Can have multiple active trades (one per session)
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

        # Calculate ranges for all sessions
        asian_high, asian_low = get_session_range(day_data, *ASIAN_PARAMS['range_hours'])
        london_high, london_low = get_session_range(day_data, *LONDON_PARAMS['range_hours'])
        ny_high, ny_low = get_session_range(day_data, *NY_PARAMS['range_hours'])

        # Convert to numpy arrays
        highs = day_data['high'].to_numpy()
        lows = day_data['low'].to_numpy()
        closes = day_data['close'].to_numpy()
        atrs = day_data['atr'].to_numpy()
        hours = np.array([t.hour for t in day_data.index])
        times = day_data.index.to_numpy()

        # Process all bars in the day
        for i in range(len(day_data)):
            atr = atrs[i]
            if np.isnan(atr):
                continue

            hour = hours[i]

            # Check exits for all active trades
            for session_name in list(active_trades.keys()):
                trade = active_trades[session_name]
                params = {'asian': ASIAN_PARAMS, 'london': LONDON_PARAMS, 'ny': NY_PARAMS}[session_name]

                # Breakeven and trailing logic
                if trade['direction'] == 'LONG':
                    risk = trade['entry'] - trade['initial_sl']

                    # Breakeven at 1R
                    if highs[i] >= trade['entry'] + risk:
                        trade['sl'] = max(trade['sl'], trade['entry'])

                    # Trailing SL if enabled
                    if params['trailing_start'] is not None:
                        if highs[i] >= trade['entry'] + params['trailing_start'] * risk:
                            trailing_sl = highs[i] - params['trailing_distance'] * risk
                            trade['sl'] = max(trade['sl'], trailing_sl)

                else:  # SHORT
                    risk = trade['initial_sl'] - trade['entry']

                    # Breakeven at 1R
                    if lows[i] <= trade['entry'] - risk:
                        trade['sl'] = min(trade['sl'], trade['entry'])

                    # Trailing SL if enabled
                    if params['trailing_start'] is not None:
                        if lows[i] <= trade['entry'] - params['trailing_start'] * risk:
                            trailing_sl = lows[i] + params['trailing_distance'] * risk
                            trade['sl'] = min(trade['sl'], trailing_sl)

                # Check SL/TP
                exit_trade = False
                if trade['direction'] == 'LONG':
                    if lows[i] <= trade['sl']:
                        pnl = (trade['sl'] - trade['entry']) * trade['size']
                        balance += pnl
                        trade['exit'] = trade['sl']
                        trade['pnl'] = pnl
                        trade['status'] = 'sl'
                        exit_trade = True
                    elif highs[i] >= trade['tp']:
                        pnl = (trade['tp'] - trade['entry']) * trade['size']
                        balance += pnl
                        trade['exit'] = trade['tp']
                        trade['pnl'] = pnl
                        trade['status'] = 'tp'
                        exit_trade = True
                else:  # SHORT
                    if highs[i] >= trade['sl']:
                        pnl = (trade['entry'] - trade['sl']) * trade['size']
                        balance += pnl
                        trade['exit'] = trade['sl']
                        trade['pnl'] = pnl
                        trade['status'] = 'sl'
                        exit_trade = True
                    elif lows[i] <= trade['tp']:
                        pnl = (trade['entry'] - trade['tp']) * trade['size']
                        balance += pnl
                        trade['exit'] = trade['tp']
                        trade['pnl'] = pnl
                        trade['status'] = 'tp'
                        exit_trade = True

                if exit_trade:
                    trades.append(trade)
                    del active_trades[session_name]

                    # Update max DD
                    if balance > peak_balance:
                        peak_balance = balance
                    dd = (peak_balance - balance) / peak_balance * 100
                    if dd > max_dd:
                        max_dd = dd

            # Check for new trade entries in each session
            # Asian breakout
            if ASIAN_PARAMS['breakout_hours'][0] <= hour < ASIAN_PARAMS['breakout_hours'][1]:
                if asian_high is not None and 'asian' not in active_trades:
                    asian_range = asian_high - asian_low
                    if ASIAN_PARAMS['min_range_atr'] * atr <= asian_range <= ASIAN_PARAMS['max_range_atr'] * atr:
                        # Check volatility filter
                        if USE_VOLATILITY_FILTER:
                            atr_ma_val = df['atr_ma'].iloc[day_data.index.get_loc(times[i])]
                            if not (atr > VOLATILITY_THRESHOLD * atr_ma_val):
                                continue  # Skip if volatility too low

                        # Get EMA values for trend filter
                        ema_fast_val = df['ema_fast'].iloc[day_data.index.get_loc(times[i])] if USE_TREND_FILTER else None
                        ema_slow_val = df['ema_slow'].iloc[day_data.index.get_loc(times[i])] if USE_TREND_FILTER else None

                        if closes[i] > asian_high:
                            # Check trend filter for LONG
                            if USE_TREND_FILTER and not (ema_fast_val > ema_slow_val):
                                continue  # Skip LONG if not in uptrend

                            entry = closes[i]
                            sl = asian_low - ASIAN_PARAMS['stop_buffer_atr'] * atr
                            risk = entry - sl
                            tp = entry + risk * ASIAN_PARAMS['tp_rr']
                            size = RISK_PER_TRADE / risk

                            active_trades['asian'] = {
                                'entry': entry, 'sl': sl, 'initial_sl': sl, 'tp': tp,
                                'size': size, 'direction': 'LONG', 'entry_time': times[i],
                                'range_type': 'asian'
                            }
                        elif closes[i] < asian_low:
                            # Check trend filter for SHORT
                            if USE_TREND_FILTER and not (ema_fast_val < ema_slow_val):
                                continue  # Skip SHORT if not in downtrend

                            entry = closes[i]
                            sl = asian_high + ASIAN_PARAMS['stop_buffer_atr'] * atr
                            risk = sl - entry
                            tp = entry - risk * ASIAN_PARAMS['tp_rr']
                            size = RISK_PER_TRADE / risk

                            active_trades['asian'] = {
                                'entry': entry, 'sl': sl, 'initial_sl': sl, 'tp': tp,
                                'size': size, 'direction': 'SHORT', 'entry_time': times[i],
                                'range_type': 'asian'
                            }

            # London breakout
            if LONDON_PARAMS['breakout_hours'][0] <= hour < LONDON_PARAMS['breakout_hours'][1]:
                if london_high is not None and 'london' not in active_trades:
                    london_range = london_high - london_low
                    if LONDON_PARAMS['min_range_atr'] * atr <= london_range <= LONDON_PARAMS['max_range_atr'] * atr:
                        # Check volatility filter
                        if USE_VOLATILITY_FILTER:
                            atr_ma_val = df['atr_ma'].iloc[day_data.index.get_loc(times[i])]
                            if not (atr > VOLATILITY_THRESHOLD * atr_ma_val):
                                continue  # Skip if volatility too low

                        # Get EMA values for trend filter
                        ema_fast_val = df['ema_fast'].iloc[day_data.index.get_loc(times[i])] if USE_TREND_FILTER else None
                        ema_slow_val = df['ema_slow'].iloc[day_data.index.get_loc(times[i])] if USE_TREND_FILTER else None

                        if closes[i] > london_high:
                            # Check trend filter for LONG
                            if USE_TREND_FILTER and not (ema_fast_val > ema_slow_val):
                                continue  # Skip LONG if not in uptrend
                            entry = closes[i]
                            sl = london_low - LONDON_PARAMS['stop_buffer_atr'] * atr
                            risk = entry - sl
                            tp = entry + risk * LONDON_PARAMS['tp_rr']
                            size = RISK_PER_TRADE / risk

                            active_trades['london'] = {
                                'entry': entry, 'sl': sl, 'initial_sl': sl, 'tp': tp,
                                'size': size, 'direction': 'LONG', 'entry_time': times[i],
                                'range_type': 'london'
                            }
                        elif closes[i] < london_low:
                            # Check trend filter for SHORT
                            if USE_TREND_FILTER and not (ema_fast_val < ema_slow_val):
                                continue  # Skip SHORT if not in downtrend

                            entry = closes[i]
                            sl = london_high + LONDON_PARAMS['stop_buffer_atr'] * atr
                            risk = sl - entry
                            tp = entry - risk * LONDON_PARAMS['tp_rr']
                            size = RISK_PER_TRADE / risk

                            active_trades['london'] = {
                                'entry': entry, 'sl': sl, 'initial_sl': sl, 'tp': tp,
                                'size': size, 'direction': 'SHORT', 'entry_time': times[i],
                                'range_type': 'london'
                            }

            # NY breakout
            if NY_PARAMS['breakout_hours'][0] <= hour < NY_PARAMS['breakout_hours'][1]:
                if ny_high is not None and 'ny' not in active_trades:
                    ny_range = ny_high - ny_low
                    if NY_PARAMS['min_range_atr'] * atr <= ny_range <= NY_PARAMS['max_range_atr'] * atr:
                        # Check volatility filter
                        if USE_VOLATILITY_FILTER:
                            atr_ma_val = df['atr_ma'].iloc[day_data.index.get_loc(times[i])]
                            if not (atr > VOLATILITY_THRESHOLD * atr_ma_val):
                                continue  # Skip if volatility too low

                        # Get EMA values for trend filter
                        ema_fast_val = df['ema_fast'].iloc[day_data.index.get_loc(times[i])] if USE_TREND_FILTER else None
                        ema_slow_val = df['ema_slow'].iloc[day_data.index.get_loc(times[i])] if USE_TREND_FILTER else None

                        if closes[i] > ny_high:
                            # Check trend filter for LONG
                            if USE_TREND_FILTER and not (ema_fast_val > ema_slow_val):
                                continue  # Skip LONG if not in uptrend

                            entry = closes[i]
                            sl = ny_low - NY_PARAMS['stop_buffer_atr'] * atr
                            risk = entry - sl
                            tp = entry + risk * NY_PARAMS['tp_rr']
                            size = RISK_PER_TRADE / risk

                            active_trades['ny'] = {
                                'entry': entry, 'sl': sl, 'initial_sl': sl, 'tp': tp,
                                'size': size, 'direction': 'LONG', 'entry_time': times[i],
                                'range_type': 'ny'
                            }
                        elif closes[i] < ny_low:
                            # Check trend filter for SHORT
                            if USE_TREND_FILTER and not (ema_fast_val < ema_slow_val):
                                continue  # Skip SHORT if not in downtrend

                            entry = closes[i]
                            sl = ny_high + NY_PARAMS['stop_buffer_atr'] * atr
                            risk = sl - entry
                            tp = entry - risk * NY_PARAMS['tp_rr']
                            size = RISK_PER_TRADE / risk

                            active_trades['ny'] = {
                                'entry': entry, 'sl': sl, 'initial_sl': sl, 'tp': tp,
                                'size': size, 'direction': 'SHORT', 'entry_time': times[i],
                                'range_type': 'ny'
                            }

        # Calculate daily drawdown at end of day
        if day_start_balance > 0:
            daily_dd = (day_start_balance - balance) / day_start_balance * 100
            if daily_dd > max_daily_dd:
                max_daily_dd = daily_dd

    # Close any remaining active trades
    for session_name, trade in active_trades.items():
        last_bar = df.iloc[-1]
        if trade['direction'] == 'LONG':
            pnl = (last_bar['close'] - trade['entry']) * trade['size']
        else:
            pnl = (trade['entry'] - last_bar['close']) * trade['size']

        balance += pnl
        trade['exit'] = last_bar['close']
        trade['pnl'] = pnl
        trade['status'] = 'eod'
        trades.append(trade)

    # Calculate statistics
    trades_df = pd.DataFrame(trades)

    total_trades = len(trades_df)
    wins = trades_df[trades_df['pnl'] > 0]
    losses = trades_df[trades_df['pnl'] <= 0]

    win_rate = len(wins) / total_trades if total_trades > 0 else 0

    total_profit = wins['pnl'].sum() if len(wins) > 0 else 0
    total_loss = abs(losses['pnl'].sum()) if len(losses) > 0 else 0
    profit_factor = total_profit / total_loss if total_loss > 0 else 0

    total_pnl = balance - 10000

    # Return results instead of printing
    return {
        'symbol': symbol,
        'total_trades': total_trades,
        'win_rate': win_rate,
        'profit_factor': profit_factor,
        'total_pnl': total_pnl,
        'final_balance': balance,
        'max_dd': max_dd,
        'max_daily_dd': max_daily_dd,
        'passes_filters': max_dd < 10.0 and max_daily_dd < 5.0 and total_trades >= 150
    }

if __name__ == "__main__":
    # Test on multiple symbols
    symbols = ["XAUUSD", "XAGUSD", "EURUSD"]

    print("=" * 100)
    print("=== MULTI-SYMBOL BACKTEST ===")
    print(f"Testing Session Range Breakout on {len(symbols)} instruments")
    print(f"Period: {START_DATE} to {END_DATE}")
    print("=" * 100)
    print()

    results = []
    for symbol in symbols:
        try:
            result = run_combined_backtest(symbol)
            results.append(result)
            print()
        except Exception as e:
            print(f"ERROR testing {symbol}: {str(e)}")
            print()

    # Print summary table
    print("=" * 100)
    print("=== SUMMARY TABLE ===")
    print("=" * 100)
    print(f"{'Symbol':<10} {'PnL':<15} {'PF':<8} {'DD%':<8} {'DailyDD%':<10} {'Trades':<8} {'WR%':<8} {'Status':<8}")
    print("-" * 100)

    for r in results:
        status = "PASS" if r['passes_filters'] else "FAIL"
        print(f"{r['symbol']:<10} ${r['total_pnl']:<14,.0f} {r['profit_factor']:<8.3f} {r['max_dd']:<8.2f} "
              f"{r['max_daily_dd']:<10.2f} {r['total_trades']:<8} {r['win_rate']*100:<8.1f} {status:<8}")

    print("=" * 100)

    # Calculate total if all pass
    passed = [r for r in results if r['passes_filters']]
    if len(passed) > 0:
        total_pnl = sum(r['total_pnl'] for r in passed)
        print(f"\nTotal PnL from passing instruments: ${total_pnl:,.0f}")
        print(f"Instruments passed: {len(passed)}/{len(results)}")

