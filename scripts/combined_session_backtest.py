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

# Optimal parameters with unified TP_RR=5.5 and step trailing
ASIAN_PARAMS = {
    'tp_rr': 5.5,  # Unified TP for all sessions
    'stop_buffer_atr': 0.1,
    'min_range_atr': 0.7,
    'max_range_atr': 3.0,
    'trailing_start': None,  # Using step trailing instead
    'trailing_distance': 0.2,
    'range_hours': (0, 7),
    'breakout_hours': (7, 10)
}

LONDON_PARAMS = {
    'tp_rr': 5.5,  # Unified TP for all sessions
    'stop_buffer_atr': 0.3,
    'min_range_atr': 0.3,
    'max_range_atr': 3.0,
    'trailing_start': None,  # Using step trailing instead
    'trailing_distance': 0.2,
    'range_hours': (7, 12),
    'breakout_hours': (13, 16)
}

NY_PARAMS = {
    'tp_rr': 5.5,  # Unified TP for all sessions
    'stop_buffer_atr': 0.3,
    'min_range_atr': 0.5,
    'max_range_atr': 3.0,
    'trailing_start': None,  # Using step trailing instead
    'trailing_distance': 0.1,
    'range_hours': (13, 17),
    'breakout_hours': (18, 21)
}

ATR_PERIOD = 20
RISK_PER_TRADE = 158  # КОНСЕРВАТИВНЫЙ для баланса $9,950 - worst case 10 losses = $1,580 (15.88% DD)
START_DATE = "2020-01-01"
END_DATE = "2026-04-18"

# Step Trailing Stop (1R step)
USE_STEP_TRAILING = True  # When price reaches 2R -> SL to 1R, 3R -> 2R, etc.

# Breakout Confirmation Filter
USE_CONFIRMATION = False  # Require 2-bar confirmation before entry

# EMA Trend Filter
USE_TREND_FILTER = False
EMA_FAST = 50
EMA_SLOW = 200

# Volatility Filter
USE_VOLATILITY_FILTER = False  # Disabled - filters out all trades
ATR_MA_BARS = 96  # 96 bars on M15 = 1 day (24h)
VOLATILITY_THRESHOLD = 0.5  # current_atr must be > 0.5 * atr_ma (lowered from 0.7)

# Direction Filter
USE_DIRECTION_FILTER = True  # Only trade one direction
ALLOWED_DIRECTION = 'LONG'  # 'LONG' or 'SHORT' or None for both

# Day of Week Filter
USE_WEEKDAY_FILTER = False  # Disabled - filter worsens results (DD 13.34% vs 7.38%)
SKIP_WEEKDAYS = [0, 4]  # Skip Monday (0) and Friday (4)

# H4 EMA20 Trend Filter
USE_H4_EMA_FILTER = True
H4_EMA_PERIOD = 20

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

def run_combined_backtest():
    print("=== Combined Session Backtest ===")
    print(f"Testing all 3 sessions with optimal parameters\n")

    # Load M15 data
    print("Loading M15 data...")
    df = load_timeframe("M15", start=START_DATE, end=END_DATE, symbol="XAUUSD")
    print(f"Loaded {len(df):,} M15 bars\n")

    if 'datetime' in df.columns:
        df.set_index('datetime', inplace=True)
    df = df.sort_index()

    # Load H4 data for EMA filter
    if USE_H4_EMA_FILTER:
        print("Resampling M15 to H4 for EMA20 filter...")
        df_h4 = df.resample('4H').agg({'open':'first','high':'max','low':'min','close':'last'}).dropna()
        df_h4['ema20'] = calculate_ema(df_h4, H4_EMA_PERIOD)
        df_h4['atr'] = calculate_atr(df_h4, ATR_PERIOD)
        print(f"Resampled {len(df_h4):,} H4 bars, calculated EMA{H4_EMA_PERIOD}\n")
    else:
        df_h4 = None

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

                    # Step Trailing Stop (1R step)
                    if USE_STEP_TRAILING:
                        profit_r = (highs[i] - trade['entry']) / risk
                        if profit_r >= 2.0:
                            trade['sl'] = max(trade['sl'], trade['entry'] + 1.0 * risk)
                        if profit_r >= 3.0:
                            trade['sl'] = max(trade['sl'], trade['entry'] + 2.0 * risk)
                        if profit_r >= 4.0:
                            trade['sl'] = max(trade['sl'], trade['entry'] + 3.0 * risk)
                        if profit_r >= 5.0:
                            trade['sl'] = max(trade['sl'], trade['entry'] + 4.0 * risk)
                    else:
                        # Breakeven at 1R (old logic)
                        if highs[i] >= trade['entry'] + risk:
                            trade['sl'] = max(trade['sl'], trade['entry'])

                        # Trailing SL if enabled (old logic)
                        if params['trailing_start'] is not None:
                            if highs[i] >= trade['entry'] + params['trailing_start'] * risk:
                                trailing_sl = highs[i] - params['trailing_distance'] * risk
                                trade['sl'] = max(trade['sl'], trailing_sl)

                else:  # SHORT
                    risk = trade['initial_sl'] - trade['entry']

                    # Step Trailing Stop (1R step)
                    if USE_STEP_TRAILING:
                        profit_r = (trade['entry'] - lows[i]) / risk
                        if profit_r >= 2.0:
                            trade['sl'] = min(trade['sl'], trade['entry'] - 1.0 * risk)
                        if profit_r >= 3.0:
                            trade['sl'] = min(trade['sl'], trade['entry'] - 2.0 * risk)
                        if profit_r >= 4.0:
                            trade['sl'] = min(trade['sl'], trade['entry'] - 3.0 * risk)
                        if profit_r >= 5.0:
                            trade['sl'] = min(trade['sl'], trade['entry'] - 4.0 * risk)
                    else:
                        # Breakeven at 1R (old logic)
                        if lows[i] <= trade['entry'] - risk:
                            trade['sl'] = min(trade['sl'], trade['entry'])

                        # Trailing SL if enabled (old logic)
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
                    trade['exit_time'] = times[i]
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
                        # Check weekday filter
                        if USE_WEEKDAY_FILTER:
                            weekday = times[i].weekday()
                            if weekday in SKIP_WEEKDAYS:
                                continue  # Skip Monday and Friday

                        # Check H4 EMA20 filter
                        if USE_H4_EMA_FILTER and df_h4 is not None:
                            # Find corresponding H4 bar
                            current_time = times[i]
                            h4_bar = df_h4[df_h4.index <= current_time].iloc[-1] if len(df_h4[df_h4.index <= current_time]) > 0 else None
                            if h4_bar is None or pd.isna(h4_bar['ema20']):
                                continue  # Skip if no H4 data available

                        # Check volatility filter
                        if USE_VOLATILITY_FILTER:
                            atr_ma_val = df['atr_ma'].iloc[day_data.index.get_loc(times[i])]
                            if not (atr > VOLATILITY_THRESHOLD * atr_ma_val):
                                continue  # Skip if volatility too low

                        # Get EMA values for trend filter
                        ema_fast_val = df['ema_fast'].iloc[day_data.index.get_loc(times[i])] if USE_TREND_FILTER else None
                        ema_slow_val = df['ema_slow'].iloc[day_data.index.get_loc(times[i])] if USE_TREND_FILTER else None

                        # Breakout confirmation logic
                        if USE_CONFIRMATION:
                            if i < 2:
                                continue  # Need at least 2 previous bars

                            # Check 2-bar confirmation
                            bar_2_close = closes[i - 2]
                            bar_1_close = closes[i - 1]
                            bar_1_open = df['open'].iloc[i - 1]
                            bar_1_body = abs(bar_1_close - bar_1_open)
                            is_doji = bar_1_body < 0.15 * atr

                            # LONG confirmation
                            if closes[i] > asian_high:
                                if not (bar_2_close > asian_high and bar_1_close > asian_high):
                                    continue  # Need bar[-2] and bar[-1] both above range_high
                                if is_doji and not (closes[i] > asian_high):
                                    continue  # If bar[-1] is doji, need current bar also above
                            # SHORT confirmation
                            elif closes[i] < asian_low:
                                if not (bar_2_close < asian_low and bar_1_close < asian_low):
                                    continue  # Need bar[-2] and bar[-1] both below range_low
                                if is_doji and not (closes[i] < asian_low):
                                    continue  # If bar[-1] is doji, need current bar also below

                        if closes[i] > asian_high:
                            # Check H4 EMA20 filter for LONG
                            if USE_H4_EMA_FILTER and df_h4 is not None:
                                if h4_bar['close'] <= h4_bar['ema20']:
                                    continue  # Skip LONG if H4 close not above EMA20

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
                            # Check direction filter
                            if USE_DIRECTION_FILTER and ALLOWED_DIRECTION == 'LONG':
                                continue  # Skip SHORT if only LONG allowed

                            # Check H4 EMA20 filter for SHORT
                            if USE_H4_EMA_FILTER and df_h4 is not None:
                                if h4_bar['close'] >= h4_bar['ema20']:
                                    continue  # Skip SHORT if H4 close not below EMA20

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
                        # Check weekday filter
                        if USE_WEEKDAY_FILTER:
                            weekday = times[i].weekday()
                            if weekday in SKIP_WEEKDAYS:
                                continue  # Skip Monday and Friday

                        # Check H4 EMA20 filter
                        if USE_H4_EMA_FILTER and df_h4 is not None:
                            # Find corresponding H4 bar
                            current_time = times[i]
                            h4_bar = df_h4[df_h4.index <= current_time].iloc[-1] if len(df_h4[df_h4.index <= current_time]) > 0 else None
                            if h4_bar is None or pd.isna(h4_bar['ema20']):
                                continue  # Skip if no H4 data available

                        # Check volatility filter
                        if USE_VOLATILITY_FILTER:
                            atr_ma_val = df['atr_ma'].iloc[day_data.index.get_loc(times[i])]
                            if not (atr > VOLATILITY_THRESHOLD * atr_ma_val):
                                continue  # Skip if volatility too low

                        # Get EMA values for trend filter
                        ema_fast_val = df['ema_fast'].iloc[day_data.index.get_loc(times[i])] if USE_TREND_FILTER else None
                        ema_slow_val = df['ema_slow'].iloc[day_data.index.get_loc(times[i])] if USE_TREND_FILTER else None

                        # Breakout confirmation logic
                        if USE_CONFIRMATION:
                            if i < 2:
                                continue  # Need at least 2 previous bars

                            # Check 2-bar confirmation
                            bar_2_close = closes[i - 2]
                            bar_1_close = closes[i - 1]
                            bar_1_open = df['open'].iloc[i - 1]
                            bar_1_body = abs(bar_1_close - bar_1_open)
                            is_doji = bar_1_body < 0.15 * atr

                            # LONG confirmation
                            if closes[i] > london_high:
                                if not (bar_2_close > london_high and bar_1_close > london_high):
                                    continue
                                if is_doji and not (closes[i] > london_high):
                                    continue
                            # SHORT confirmation
                            elif closes[i] < london_low:
                                if not (bar_2_close < london_low and bar_1_close < london_low):
                                    continue
                                if is_doji and not (closes[i] < london_low):
                                    continue

                        # Check direction filter for SHORT
                        if closes[i] < london_low:
                            if USE_DIRECTION_FILTER and ALLOWED_DIRECTION == 'LONG':
                                continue  # Skip SHORT if only LONG allowed

                        if closes[i] > london_high:
                            # Check H4 EMA20 filter for LONG
                            if USE_H4_EMA_FILTER and df_h4 is not None:
                                if h4_bar['close'] <= h4_bar['ema20']:
                                    continue  # Skip LONG if H4 close not above EMA20

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
                            # Check H4 EMA20 filter for SHORT
                            if USE_H4_EMA_FILTER and df_h4 is not None:
                                if h4_bar['close'] >= h4_bar['ema20']:
                                    continue  # Skip SHORT if H4 close not below EMA20

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
                        # Check weekday filter
                        if USE_WEEKDAY_FILTER:
                            weekday = times[i].weekday()
                            if weekday in SKIP_WEEKDAYS:
                                continue  # Skip Monday and Friday

                        # Check H4 EMA20 filter
                        if USE_H4_EMA_FILTER and df_h4 is not None:
                            # Find corresponding H4 bar
                            current_time = times[i]
                            h4_bar = df_h4[df_h4.index <= current_time].iloc[-1] if len(df_h4[df_h4.index <= current_time]) > 0 else None
                            if h4_bar is None or pd.isna(h4_bar['ema20']):
                                continue  # Skip if no H4 data available

                        # Check volatility filter
                        if USE_VOLATILITY_FILTER:
                            atr_ma_val = df['atr_ma'].iloc[day_data.index.get_loc(times[i])]
                            if not (atr > VOLATILITY_THRESHOLD * atr_ma_val):
                                continue  # Skip if volatility too low

                        # Get EMA values for trend filter
                        ema_fast_val = df['ema_fast'].iloc[day_data.index.get_loc(times[i])] if USE_TREND_FILTER else None
                        ema_slow_val = df['ema_slow'].iloc[day_data.index.get_loc(times[i])] if USE_TREND_FILTER else None

                        # Breakout confirmation logic
                        if USE_CONFIRMATION:
                            if i < 2:
                                continue  # Need at least 2 previous bars

                            # Check 2-bar confirmation
                            bar_2_close = closes[i - 2]
                            bar_1_close = closes[i - 1]
                            bar_1_open = df['open'].iloc[i - 1]
                            bar_1_body = abs(bar_1_close - bar_1_open)
                            is_doji = bar_1_body < 0.15 * atr

                            # LONG confirmation
                            if closes[i] > ny_high:
                                if not (bar_2_close > ny_high and bar_1_close > ny_high):
                                    continue
                                if is_doji and not (closes[i] > ny_high):
                                    continue
                            # SHORT confirmation
                            elif closes[i] < ny_low:
                                if not (bar_2_close < ny_low and bar_1_close < ny_low):
                                    continue
                                if is_doji and not (closes[i] < ny_low):
                                    continue

                        # Check direction filter for SHORT
                        if closes[i] < ny_low:
                            if USE_DIRECTION_FILTER and ALLOWED_DIRECTION == 'LONG':
                                continue  # Skip SHORT if only LONG allowed

                        if closes[i] > ny_high:
                            # Check H4 EMA20 filter for LONG
                            if USE_H4_EMA_FILTER and df_h4 is not None:
                                if h4_bar['close'] <= h4_bar['ema20']:
                                    continue  # Skip LONG if H4 close not above EMA20

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
                            # Check H4 EMA20 filter for SHORT
                            if USE_H4_EMA_FILTER and df_h4 is not None:
                                if h4_bar['close'] >= h4_bar['ema20']:
                                    continue  # Skip SHORT if H4 close not below EMA20

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
        trade['exit_time'] = df.index[-1]
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

    # Calculate trade duration statistics
    trades_df['duration_days'] = (trades_df['exit_time'] - trades_df['entry_time']).dt.total_seconds() / 86400
    trades_df['duration_category'] = trades_df['duration_days'].apply(
        lambda x: 'same_day' if x < 1 else ('1_day' if x < 2 else '2+_days')
    )

    avg_duration = trades_df['duration_days'].mean()
    median_duration = trades_df['duration_days'].median()
    max_duration = trades_df['duration_days'].max()

    same_day_count = len(trades_df[trades_df['duration_category'] == 'same_day'])
    one_day_count = len(trades_df[trades_df['duration_category'] == '1_day'])
    two_plus_days_count = len(trades_df[trades_df['duration_category'] == '2+_days'])

    # Calculate swap costs
    # XAUUSD LONG swap: -$3 per lot per day (conservative estimate)
    # XAUUSD SHORT swap: +$1.5 per lot per day (conservative estimate)
    SWAP_LONG_PER_LOT = -3.0
    SWAP_SHORT_PER_LOT = 1.5

    def calculate_swap(row):
        nights = int(row['duration_days'])  # Full nights held
        lot_size = row['size'] / 100  # Convert to standard lots (assuming size is in oz, 100oz = 1 lot)

        if nights == 0:
            return 0.0

        # Wednesday has triple swap (for weekend)
        entry_weekday = row['entry_time'].weekday()
        swap_cost = 0.0

        for day in range(nights):
            current_weekday = (entry_weekday + day) % 7
            multiplier = 3 if current_weekday == 2 else 1  # Wednesday = 2

            if row['direction'] == 'LONG':
                swap_cost += SWAP_LONG_PER_LOT * lot_size * multiplier
            else:
                swap_cost += SWAP_SHORT_PER_LOT * lot_size * multiplier

        return swap_cost

    trades_df['swap_cost'] = trades_df.apply(calculate_swap, axis=1)
    trades_df['pnl_after_swap'] = trades_df['pnl'] + trades_df['swap_cost']

    total_swap_cost = trades_df['swap_cost'].sum()
    total_pnl_after_swap = trades_df['pnl_after_swap'].sum()
    balance_after_swap = 10000 + total_pnl_after_swap

    # Recalculate max DD with swaps
    balance_with_swap = 10000
    peak_balance_with_swap = 10000
    max_dd_with_swap = 0

    for idx, trade in trades_df.iterrows():
        balance_with_swap += trade['pnl_after_swap']
        if balance_with_swap > peak_balance_with_swap:
            peak_balance_with_swap = balance_with_swap
        dd = (peak_balance_with_swap - balance_with_swap) / peak_balance_with_swap * 100
        if dd > max_dd_with_swap:
            max_dd_with_swap = dd

    # Print results
    print("=" * 80)
    print("=== COMBINED BACKTEST RESULTS ===")
    print("=" * 80)
    print(f"\nTotal Trades: {total_trades}")
    print(f"Win Rate: {win_rate:.1%}")
    print(f"Profit Factor: {profit_factor:.3f}")
    print(f"Total PnL: ${total_pnl:,.0f}")
    print(f"Final Balance: ${balance:,.0f}")
    print(f"Max Drawdown: {max_dd:.2f}%")
    print(f"Max Daily Drawdown: {max_daily_dd:.2f}%")

    # Trade duration statistics
    print(f"\n=== TRADE DURATION STATISTICS ===")
    print(f"Average duration: {avg_duration:.2f} days")
    print(f"Median duration: {median_duration:.2f} days")
    print(f"Maximum duration: {max_duration:.2f} days")
    print(f"\nDuration distribution:")
    print(f"  Same day (< 1 day): {same_day_count} trades ({same_day_count/total_trades:.1%})")
    print(f"  1 day (1-2 days): {one_day_count} trades ({one_day_count/total_trades:.1%})")
    print(f"  2+ days: {two_plus_days_count} trades ({two_plus_days_count/total_trades:.1%})")

    # Swap impact analysis
    print(f"\n=== SWAP IMPACT ANALYSIS ===")
    print(f"Total swap cost: ${total_swap_cost:,.2f}")
    print(f"PnL after swaps: ${total_pnl_after_swap:,.2f}")
    print(f"Final balance after swaps: ${balance_after_swap:,.0f}")
    print(f"Max DD with swaps: {max_dd_with_swap:.2f}%")
    print(f"Swap impact on PnL: {(total_swap_cost/total_pnl)*100:.2f}%")

    # Check if swaps kill the strategy
    passes_dd_with_swap = max_dd_with_swap < 10.0
    swap_kills_strategy = not passes_dd_with_swap and passes_dd

    if swap_kills_strategy:
        print(f"\nWARNING: Swaps push DD over 10% limit!")
        print(f"  Without swaps: {max_dd:.2f}%")
        print(f"  With swaps: {max_dd_with_swap:.2f}%")
    elif total_pnl_after_swap < 0:
        print(f"\nWARNING: Strategy becomes unprofitable with swaps!")
    else:
        print(f"\nSwaps are manageable - strategy remains profitable")

    # Breakdown by session
    print(f"\n=== BREAKDOWN BY SESSION ===")
    for session in ['asian', 'london', 'ny']:
        session_trades = trades_df[trades_df['range_type'] == session]
        if len(session_trades) > 0:
            session_pnl = session_trades['pnl'].sum()
            session_wins = len(session_trades[session_trades['pnl'] > 0])
            session_wr = session_wins / len(session_trades)
            print(f"{session.upper()}: {len(session_trades)} trades, PnL=${session_pnl:,.0f}, WR={session_wr:.1%}")

    # Check filters
    print(f"\n=== FILTER CHECK ===")
    passes_dd = max_dd < 10.0
    passes_daily_dd = max_daily_dd < 5.0
    passes_trades = total_trades >= 150

    print(f"Max DD < 10%: {'PASS' if passes_dd else 'FAIL'} ({max_dd:.2f}%)")
    print(f"Max Daily DD < 5%: {'PASS' if passes_daily_dd else 'FAIL'} ({max_daily_dd:.2f}%)")
    print(f"Total Trades >= 150: {'PASS' if passes_trades else 'FAIL'} ({total_trades})")

    # Detailed win/loss statistics
    print(f"\n=== WIN/LOSS STATISTICS ===")
    print(f"Winning trades: {len(wins)}, Total: ${total_profit:,.2f}")
    print(f"Losing trades: {len(losses)}, Total: ${-total_loss:,.2f}")
    print(f"Net PnL: ${total_pnl:,.2f}")
    print(f"Verification: Winning + Losing = ${total_profit - total_loss:,.2f} (should equal Net PnL)")

    if passes_dd and passes_daily_dd and passes_trades:
        print(f"\n{'='*80}")
        print("ALL FILTERS PASSED - STRATEGY IS VALID")
        print(f"{'='*80}")
    else:
        print(f"\n{'='*80}")
        print("SOME FILTERS FAILED - NEED ADJUSTMENT")
        print(f"{'='*80}")

    return trades_df

if __name__ == "__main__":
    run_combined_backtest()
