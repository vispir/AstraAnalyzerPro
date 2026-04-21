"""
Local Range Breakout Strategy - Grid Search Optimization
Tests dynamic local range breakout with H4 EMA20 trend filter
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
import numpy as np
from astra_v2.data.dukascopy import load_timeframe
from multiprocessing import Pool
from itertools import product

START_DATE = "2020-01-01"
END_DATE = "2026-04-18"
ATR_PERIOD = 20
RISK_PER_TRADE = 100
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
    return df['close'].ewm(span=period, adjust=False).mean()

def run_backtest(params, df, df_h4):
    lookback, min_range_atr, max_range_atr, stop_buffer, tp_rr, confirmation = params

    trades = []
    active_trade = None
    balance = 10000
    peak_balance = 10000
    max_dd = 0
    max_daily_dd = 0

    highs = df['high'].to_numpy()
    lows = df['low'].to_numpy()
    closes = df['close'].to_numpy()
    atrs = df['atr'].to_numpy()
    times = df.index.to_numpy()

    # Precompute H4 arrays for fast lookup
    h4_times = df_h4.index.to_numpy()
    h4_closes = df_h4['close'].to_numpy()
    h4_emas = df_h4['ema20'].to_numpy()
    h4_atrs = df_h4['atr'].to_numpy()

    dates = df.index.date
    unique_dates = sorted(set(dates))

    for date in unique_dates:
        day_start_balance = balance
        day_mask = df.index.date == date
        day_indices = np.where(day_mask)[0]

        if len(day_indices) < lookback + 5:
            continue

        for idx in day_indices:
            if idx < lookback + 3:
                continue

            atr = atrs[idx]
            if np.isnan(atr):
                continue

            # Check exit for active trade
            if active_trade is not None:
                if active_trade['direction'] == 'LONG':
                    risk = active_trade['entry'] - active_trade['initial_sl']
                    # Breakeven at 1R
                    if highs[idx] >= active_trade['entry'] + risk:
                        active_trade['sl'] = max(active_trade['sl'], active_trade['entry'])

                    # Check SL
                    if lows[idx] <= active_trade['sl']:
                        pnl = (active_trade['sl'] - active_trade['entry']) * active_trade['size']
                        balance += pnl
                        active_trade['exit'] = active_trade['sl']
                        active_trade['pnl'] = pnl
                        active_trade['status'] = 'sl'
                        trades.append(active_trade)
                        active_trade = None
                        if balance > peak_balance:
                            peak_balance = balance
                        dd = (peak_balance - balance) / peak_balance * 100
                        if dd > max_dd:
                            max_dd = dd
                    # Check TP
                    elif highs[idx] >= active_trade['tp']:
                        pnl = (active_trade['tp'] - active_trade['entry']) * active_trade['size']
                        balance += pnl
                        active_trade['exit'] = active_trade['tp']
                        active_trade['pnl'] = pnl
                        active_trade['status'] = 'tp'
                        trades.append(active_trade)
                        active_trade = None
                        if balance > peak_balance:
                            peak_balance = balance
                        dd = (peak_balance - balance) / peak_balance * 100
                        if dd > max_dd:
                            max_dd = dd
                else:  # SHORT
                    risk = active_trade['initial_sl'] - active_trade['entry']
                    # Breakeven at 1R
                    if lows[idx] <= active_trade['entry'] - risk:
                        active_trade['sl'] = min(active_trade['sl'], active_trade['entry'])

                    # Check SL
                    if highs[idx] >= active_trade['sl']:
                        pnl = (active_trade['entry'] - active_trade['sl']) * active_trade['size']
                        balance += pnl
                        active_trade['exit'] = active_trade['sl']
                        active_trade['pnl'] = pnl
                        active_trade['status'] = 'sl'
                        trades.append(active_trade)
                        active_trade = None
                        if balance > peak_balance:
                            peak_balance = balance
                        dd = (peak_balance - balance) / peak_balance * 100
                        if dd > max_dd:
                            max_dd = dd
                    # Check TP
                    elif lows[idx] <= active_trade['tp']:
                        pnl = (active_trade['entry'] - active_trade['tp']) * active_trade['size']
                        balance += pnl
                        active_trade['exit'] = active_trade['tp']
                        active_trade['pnl'] = pnl
                        active_trade['status'] = 'tp'
                        trades.append(active_trade)
                        active_trade = None
                        if balance > peak_balance:
                            peak_balance = balance
                        dd = (peak_balance - balance) / peak_balance * 100
                        if dd > max_dd:
                            max_dd = dd

            # Entry logic
            if active_trade is None:
                # Calculate local range (excluding last 3 bars)
                range_start = idx - lookback - 3
                range_end = idx - 3
                if range_start < 0:
                    continue

                range_high = highs[range_start:range_end].max()
                range_low = lows[range_start:range_end].min()
                range_size = range_high - range_low

                # Range filter
                if not (min_range_atr * atr <= range_size <= max_range_atr * atr):
                    continue

                # H4 EMA20 trend filter
                current_time = times[idx]
                h4_idx = np.searchsorted(h4_times, current_time, side='right') - 1
                if h4_idx < 0 or h4_idx >= len(h4_times):
                    continue
                if np.isnan(h4_emas[h4_idx]) or np.isnan(h4_atrs[h4_idx]):
                    continue

                h4_close = h4_closes[h4_idx]
                h4_ema = h4_emas[h4_idx]
                h4_atr = h4_atrs[h4_idx]

                # Determine trend
                if h4_close > h4_ema and abs(h4_close - h4_ema) >= 0.3 * h4_atr:
                    trend = 'up'
                elif h4_close < h4_ema and abs(h4_close - h4_ema) >= 0.3 * h4_atr:
                    trend = 'down'
                else:
                    trend = 'neutral'

                # Check breakout confirmation
                if confirmation:
                    # Need bar[-3] and bar[-2] closed beyond range
                    bar_3 = closes[idx - 3]
                    bar_2 = closes[idx - 2]
                    bar_1 = closes[idx - 1]

                    # Check if bar[-2] is doji
                    bar_2_body = abs(closes[idx - 2] - df['open'].iloc[idx - 2])
                    is_doji = bar_2_body < 0.15 * atr

                    # LONG confirmation
                    if bar_3 > range_high and bar_2 > range_high:
                        if is_doji:
                            # Need bar[-1] also above
                            if bar_1 > range_high and (trend == 'up' or trend == 'neutral'):
                                entry = closes[idx]
                                sl = range_low - stop_buffer * atr
                                risk = entry - sl
                                tp = entry + risk * tp_rr
                                size = RISK_PER_TRADE / risk
                                active_trade = {
                                    'entry': entry, 'sl': sl, 'initial_sl': sl, 'tp': tp,
                                    'size': size, 'direction': 'LONG', 'entry_time': times[idx]
                                }
                        else:
                            if trend == 'up' or trend == 'neutral':
                                entry = closes[idx]
                                sl = range_low - stop_buffer * atr
                                risk = entry - sl
                                tp = entry + risk * tp_rr
                                size = RISK_PER_TRADE / risk
                                active_trade = {
                                    'entry': entry, 'sl': sl, 'initial_sl': sl, 'tp': tp,
                                    'size': size, 'direction': 'LONG', 'entry_time': times[idx]
                                }

                    # SHORT confirmation
                    elif bar_3 < range_low and bar_2 < range_low:
                        if is_doji:
                            # Need bar[-1] also below
                            if bar_1 < range_low and (trend == 'down' or trend == 'neutral'):
                                entry = closes[idx]
                                sl = range_high + stop_buffer * atr
                                risk = sl - entry
                                tp = entry - risk * tp_rr
                                size = RISK_PER_TRADE / risk
                                active_trade = {
                                    'entry': entry, 'sl': sl, 'initial_sl': sl, 'tp': tp,
                                    'size': size, 'direction': 'SHORT', 'entry_time': times[idx]
                                }
                        else:
                            if trend == 'down' or trend == 'neutral':
                                entry = closes[idx]
                                sl = range_high + stop_buffer * atr
                                risk = sl - entry
                                tp = entry - risk * tp_rr
                                size = RISK_PER_TRADE / risk
                                active_trade = {
                                    'entry': entry, 'sl': sl, 'initial_sl': sl, 'tp': tp,
                                    'size': size, 'direction': 'SHORT', 'entry_time': times[idx]
                                }
                else:
                    # No confirmation - enter on first close beyond range
                    if closes[idx] > range_high and (trend == 'up' or trend == 'neutral'):
                        entry = closes[idx]
                        sl = range_low - stop_buffer * atr
                        risk = entry - sl
                        tp = entry + risk * tp_rr
                        size = RISK_PER_TRADE / risk
                        active_trade = {
                            'entry': entry, 'sl': sl, 'initial_sl': sl, 'tp': tp,
                            'size': size, 'direction': 'LONG', 'entry_time': times[idx]
                        }
                    elif closes[idx] < range_low and (trend == 'down' or trend == 'neutral'):
                        entry = closes[idx]
                        sl = range_high + stop_buffer * atr
                        risk = sl - entry
                        tp = entry - risk * tp_rr
                        size = RISK_PER_TRADE / risk
                        active_trade = {
                            'entry': entry, 'sl': sl, 'initial_sl': sl, 'tp': tp,
                            'size': size, 'direction': 'SHORT', 'entry_time': times[idx]
                        }

        # Daily DD
        if day_start_balance > 0:
            daily_dd = (day_start_balance - balance) / day_start_balance * 100
            if daily_dd > max_daily_dd:
                max_daily_dd = daily_dd

    # Close remaining trade
    if active_trade is not None:
        last_close = closes[-1]
        if active_trade['direction'] == 'LONG':
            pnl = (last_close - active_trade['entry']) * active_trade['size']
        else:
            pnl = (active_trade['entry'] - last_close) * active_trade['size']
        balance += pnl
        active_trade['exit'] = last_close
        active_trade['pnl'] = pnl
        active_trade['status'] = 'eod'
        trades.append(active_trade)

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

    passes = max_dd < 10.0 and max_daily_dd < 5.0 and total_trades >= 150

    return {
        'lookback': lookback,
        'min_range_atr': min_range_atr,
        'max_range_atr': max_range_atr,
        'stop_buffer': stop_buffer,
        'tp_rr': tp_rr,
        'confirmation': confirmation,
        'trades': total_trades,
        'win_rate': win_rate * 100,
        'profit_factor': profit_factor,
        'max_dd': max_dd,
        'max_daily_dd': max_daily_dd,
        'total_pnl': total_pnl,
        'passes': passes
    }

def worker(args):
    params, df, df_h4 = args
    return run_backtest(params, df, df_h4)

if __name__ == "__main__":
    print("=" * 100)
    print("=== LOCAL RANGE BREAKOUT STRATEGY - GRID SEARCH OPTIMIZATION ===")
    print(f"Period: {START_DATE} to {END_DATE}")
    print("=" * 100)
    print()

    # Load data
    print("Loading M15 data...")
    df = load_timeframe("M15", start=START_DATE, end=END_DATE, symbol="XAUUSD")
    if 'datetime' in df.columns:
        df.set_index('datetime', inplace=True)
    df = df.sort_index()
    df['atr'] = calculate_atr(df, ATR_PERIOD)
    print(f"Loaded {len(df)} M15 bars")

    print("Resampling M15 to H4...")
    df_h4 = df.resample('4h').agg({'open':'first','high':'max','low':'min','close':'last'}).dropna()
    df_h4['ema20'] = calculate_ema(df_h4, H4_EMA_PERIOD)
    df_h4['atr'] = calculate_atr(df_h4, ATR_PERIOD)
    print(f"Resampled {len(df_h4)} H4 bars")
    print()

    # Grid search parameters - minimal for speed test
    lookback_values = [20]
    min_range_values = [0.5]
    max_range_values = [3.0]
    stop_buffer_values = [0.1, 0.3]
    tp_rr_values = [2.5, 3.0, 3.5]
    confirmation_values = [False]

    param_combinations = list(product(
        lookback_values,
        min_range_values,
        max_range_values,
        stop_buffer_values,
        tp_rr_values,
        confirmation_values
    ))

    print(f"Testing {len(param_combinations)} parameter combinations...")
    print("Running sequentially...")
    print()

    # Run grid search sequentially
    results = []
    for i, params in enumerate(param_combinations):
        if (i + 1) % 10 == 0:
            print(f"Progress: {i + 1}/{len(param_combinations)}")
        result = run_backtest(params, df, df_h4)
        results.append(result)

    # Filter valid results
    valid_results = [r for r in results if r is not None]

    if len(valid_results) == 0:
        print("No valid results found!")
        sys.exit(1)

    # Sort by profit factor DESC
    valid_results.sort(key=lambda x: x['profit_factor'], reverse=True)

    # Print top 10
    print("=" * 140)
    print("=== TOP 10 RESULTS (sorted by Profit Factor) ===")
    print("=" * 140)
    print(f"{'LOOKBACK':<10} {'MIN_R':<8} {'MAX_R':<8} {'STOP':<8} {'TP_RR':<8} {'Confirm':<10} "
          f"{'Trades':<8} {'WR%':<8} {'PF':<8} {'DD%':<8} {'DailyDD%':<10} {'PnL':<12} {'Status':<8}")
    print("-" * 140)

    for i, r in enumerate(valid_results[:10]):
        status = "PASS" if r['passes'] else "FAIL"
        confirm_str = "Yes" if r['confirmation'] else "No"
        print(f"{r['lookback']:<10} {r['min_range_atr']:<8.1f} {r['max_range_atr']:<8.1f} "
              f"{r['stop_buffer']:<8.1f} {r['tp_rr']:<8.1f} {confirm_str:<10} "
              f"{r['trades']:<8} {r['win_rate']:<8.1f} {r['profit_factor']:<8.3f} "
              f"{r['max_dd']:<8.2f} {r['max_daily_dd']:<10.2f} ${r['total_pnl']:<11,.0f} {status:<8}")

    print("=" * 140)

    # Best passing result
    passing_results = [r for r in valid_results if r['passes']]
    if len(passing_results) > 0:
        best = passing_results[0]
        print()
        print("=== BEST PASSING RESULT ===")
        print(f"LOOKBACK={best['lookback']}, MIN_R={best['min_range_atr']}, MAX_R={best['max_range_atr']}, "
              f"STOP={best['stop_buffer']}, TP_RR={best['tp_rr']}, Confirmation={best['confirmation']}")
        print(f"Trades: {best['trades']}, WR: {best['win_rate']:.1f}%, PF: {best['profit_factor']:.3f}")
        print(f"PnL: ${best['total_pnl']:,.0f}, DD: {best['max_dd']:.2f}%, Daily DD: {best['max_daily_dd']:.2f}%")
    else:
        print()
        print("No results passed all filters (DD<10%, DailyDD<5%, Trades>=150)")
