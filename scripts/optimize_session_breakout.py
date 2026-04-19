"""
Grid Search Optimization for Session Range Breakout
180 combinations: 5×4×3×3
Optimized with numpy arrays and multiprocessing
"""
import sys
import os
import json
from datetime import datetime
from itertools import product
from multiprocessing import Pool, freeze_support

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
import numpy as np
from astra_v2.data.dukascopy import load_timeframe

# Global variable for shared data
GLOBAL_DF = None

# Parameter grid
TP_RR_VALUES = [2.0, 2.5, 3.0, 3.5]
STOP_BUFFER_ATR_VALUES = [0.3, 0.5, 0.7, 1.0]
MIN_RANGE_ATR_VALUES = [0.3, 0.5, 0.7]
MAX_RANGE_ATR_VALUES = [1.5, 2.0, 3.0]

ATR_PERIOD = 20
RISK_PER_TRADE = 100
START_DATE = "2020-01-01"
END_DATE = "2026-04-18"

def init_worker(df):
    """Initialize worker process with shared data"""
    global GLOBAL_DF
    GLOBAL_DF = df

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
    mask = (df.index.hour >= start_hour) & (df.index.hour < end_hour)
    session_bars = df[mask]

    if len(session_bars) == 0:
        return None, None

    range_high = session_bars['high'].max()
    range_low = session_bars['low'].min()

    return range_high, range_low

def run_backtest_with_params(df, tp_rr, stop_buffer_atr, min_range_atr, max_range_atr):
    # Calculate ATR once
    df_with_atr = df.copy()
    df_with_atr['atr'] = calculate_atr(df, ATR_PERIOD)

    trades = []
    active_trade = None
    balance = 10000
    peak_balance = 10000
    max_dd = 0
    max_daily_dd = 0

    dates = df_with_atr.index.date
    unique_dates = sorted(set(dates))

    for date in unique_dates:
        day_start_balance = balance
        day_data = df_with_atr[df_with_atr.index.date == date]

        if len(day_data) < 10:
            continue

        asian_high, asian_low = get_session_range(day_data, 0, 7)
        london_high, london_low = get_session_range(day_data, 7, 12)

        if asian_high is None or london_high is None:
            continue

        # Convert to numpy arrays for speed
        highs = day_data['high'].to_numpy()
        lows = day_data['low'].to_numpy()
        closes = day_data['close'].to_numpy()
        atrs = day_data['atr'].to_numpy()
        hours = np.array([t.hour for t in day_data.index])
        times = day_data.index.to_numpy()

        # Process all bars in the day
        for i in range(len(day_data)):
            # Check exit conditions for active trade
            if active_trade is not None:
                # Breakeven logic: move SL to entry after 1R profit
                if active_trade['direction'] == 'LONG':
                    risk = active_trade['entry'] - active_trade['initial_sl']
                    if highs[i] >= active_trade['entry'] + risk:
                        active_trade['sl'] = active_trade['entry']
                else:  # SHORT
                    risk = active_trade['initial_sl'] - active_trade['entry']
                    if lows[i] <= active_trade['entry'] - risk:
                        active_trade['sl'] = active_trade['entry']

                # Check SL/TP
                if active_trade['direction'] == 'LONG':
                    if lows[i] <= active_trade['sl']:
                        pnl = (active_trade['sl'] - active_trade['entry']) * active_trade['size']
                        balance += pnl
                        active_trade['exit'] = active_trade['sl']
                        active_trade['pnl'] = pnl
                        active_trade['status'] = 'sl'
                        trades.append(active_trade)
                        active_trade = None
                    elif highs[i] >= active_trade['tp']:
                        pnl = (active_trade['tp'] - active_trade['entry']) * active_trade['size']
                        balance += pnl
                        active_trade['exit'] = active_trade['tp']
                        active_trade['pnl'] = pnl
                        active_trade['status'] = 'tp'
                        trades.append(active_trade)
                        active_trade = None
                else:  # SHORT
                    if highs[i] >= active_trade['sl']:
                        pnl = (active_trade['entry'] - active_trade['sl']) * active_trade['size']
                        balance += pnl
                        active_trade['exit'] = active_trade['sl']
                        active_trade['pnl'] = pnl
                        active_trade['status'] = 'sl'
                        trades.append(active_trade)
                        active_trade = None
                    elif lows[i] <= active_trade['tp']:
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

            # Check for new trade entry (only in specific windows)
            atr = atrs[i]
            if np.isnan(atr):
                continue

            hour = hours[i]

            # Asian breakout window (07:00-10:00)
            if 7 <= hour < 10:
                asian_range = asian_high - asian_low

                if asian_range < min_range_atr * atr or asian_range > max_range_atr * atr:
                    continue

                if closes[i] > asian_high and active_trade is None:
                    entry = closes[i]
                    sl = asian_low - stop_buffer_atr * atr
                    risk = entry - sl
                    tp = entry + risk * tp_rr
                    size = RISK_PER_TRADE / risk

                    active_trade = {
                        'entry': entry,
                        'sl': sl,
                        'initial_sl': sl,
                        'tp': tp,
                        'size': size,
                        'direction': 'LONG',
                        'entry_time': times[i],
                        'range_type': 'asian'
                    }

                elif closes[i] < asian_low and active_trade is None:
                    entry = closes[i]
                    sl = asian_high + stop_buffer_atr * atr
                    risk = sl - entry
                    tp = entry - risk * tp_rr
                    size = RISK_PER_TRADE / risk

                    active_trade = {
                        'entry': entry,
                        'sl': sl,
                        'initial_sl': sl,
                        'tp': tp,
                        'size': size,
                        'direction': 'SHORT',
                        'entry_time': times[i],
                        'range_type': 'asian'
                    }

            # London breakout window (13:00-16:00)
            elif 13 <= hour < 16:
                london_range = london_high - london_low

                if london_range < min_range_atr * atr or london_range > max_range_atr * atr:
                    continue

                if closes[i] > london_high and active_trade is None:
                    entry = closes[i]
                    sl = london_low - stop_buffer_atr * atr
                    risk = entry - sl
                    tp = entry + risk * tp_rr
                    size = RISK_PER_TRADE / risk

                    active_trade = {
                        'entry': entry,
                        'sl': sl,
                        'initial_sl': sl,
                        'tp': tp,
                        'size': size,
                        'direction': 'LONG',
                        'entry_time': times[i],
                        'range_type': 'london'
                    }

                elif closes[i] < london_low and active_trade is None:
                    entry = closes[i]
                    sl = london_high + stop_buffer_atr * atr
                    risk = sl - entry
                    tp = entry - risk * tp_rr
                    size = RISK_PER_TRADE / risk

                    active_trade = {
                        'entry': entry,
                        'sl': sl,
                        'initial_sl': sl,
                        'tp': tp,
                        'size': size,
                        'direction': 'SHORT',
                        'entry_time': times[i],
                        'range_type': 'london'
                    }

        # Calculate daily drawdown at end of day
        if day_start_balance > 0:
            daily_dd = (day_start_balance - balance) / day_start_balance * 100
            if daily_dd > max_daily_dd:
                max_daily_dd = daily_dd

    if active_trade is not None:
        last_bar = df_with_atr.iloc[-1]
        if active_trade['direction'] == 'LONG':
            pnl = (last_bar['close'] - active_trade['entry']) * active_trade['size']
        else:
            pnl = (active_trade['entry'] - last_bar['close']) * active_trade['size']

        balance += pnl
        active_trade['exit'] = last_bar['close']
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

    return {
        'total_trades': total_trades,
        'win_rate': win_rate,
        'profit_factor': profit_factor,
        'max_drawdown_pct': max_dd,
        'max_daily_dd': max_daily_dd,
        'total_pnl': total_pnl,
        'final_balance': balance
    }

def run_single_combination(args):
    """Wrapper for multiprocessing - uses global df"""
    tp_rr, stop_buffer, min_range, max_range, run_count, total_runs = args
    start_time = datetime.now()

    try:
        result = run_backtest_with_params(GLOBAL_DF, tp_rr, stop_buffer, min_range, max_range)

        if result is None:
            return None

        elapsed = (datetime.now() - start_time).total_seconds()

        # Filter: DD < 10%, daily DD < 5%, trades >= 150
        passes_filter = (
            result['max_drawdown_pct'] < 10.0 and
            result['max_daily_dd'] < 5.0 and
            result['total_trades'] >= 150
        )

        result_entry = {
            'run': run_count,
            'params': {
                'tp_rr': float(tp_rr),
                'stop_buffer_atr': float(stop_buffer),
                'min_range_atr': float(min_range),
                'max_range_atr': float(max_range),
            },
            'summary': result,
            'passes_filter': bool(passes_filter),
            'elapsed_seconds': float(elapsed),
        }

        status = "PASS" if passes_filter else "FAIL"
        print(f"[{run_count}/{total_runs}] {status}: TP_RR={tp_rr}, STOP={stop_buffer}, MIN_R={min_range}, MAX_R={max_range} | "
              f"PF={result['profit_factor']:.3f}, DD={result['max_drawdown_pct']:.2f}%, DailyDD={result['max_daily_dd']:.2f}%, "
              f"PnL=${result['total_pnl']:,.0f}, Trades={result['total_trades']}, WR={result['win_rate']:.1%}", flush=True)

        return result_entry

    except Exception as e:
        print(f"[{run_count}/{total_runs}] ERROR: {str(e)}", flush=True)
        return None

def run_optimization():
    print("=== Session Range Breakout Grid Optimization ===")
    print(f"Start time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"\nLoading data...")

    df = load_timeframe("M15", start=START_DATE, end=END_DATE, symbol="XAUUSD")
    print(f"Loaded {len(df):,} bars")

    if 'datetime' in df.columns:
        df.set_index('datetime', inplace=True)
    df = df.sort_index()

    # Prepare all combinations
    combinations = list(product(TP_RR_VALUES, STOP_BUFFER_ATR_VALUES, MIN_RANGE_ATR_VALUES, MAX_RANGE_ATR_VALUES))
    total_runs = len(combinations)

    print(f"\nStarting optimization: {total_runs} combinations")
    print(f"Using 4 CPU cores with multiprocessing")
    print("=" * 80)

    # Prepare arguments (without df - will use global)
    args_list = [
        (tp_rr, stop_buffer, min_range, max_range, i+1, total_runs)
        for i, (tp_rr, stop_buffer, min_range, max_range) in enumerate(combinations)
    ]

    # Run in parallel with global df
    results = []
    with Pool(processes=4, initializer=init_worker, initargs=(df,)) as pool:
        for result in pool.imap_unordered(run_single_combination, args_list):
            if result is not None:
                results.append(result)

    # Save results
    output_file = f"backtest_results/session_breakout_optimization_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    with open(output_file, "w") as f:
        json.dump(results, f, indent=2)

    print("\n" + "=" * 80)
    print("=== OPTIMIZATION COMPLETE ===")
    print(f"End time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Total runs: {len(results)}")

    passed = [r for r in results if r.get('passes_filter', False)]
    print(f"Passed filter: {len(passed)}")
    print(f"Results saved to: {output_file}")

    # Sort by PF descending
    results.sort(key=lambda x: x['summary']['profit_factor'], reverse=True)

    # Print top 20
    print("\n=== TOP 20 BY PROFIT FACTOR ===")
    print(f"{'Rank':<5} {'TP_RR':<7} {'STOP':<7} {'MIN_R':<7} {'MAX_R':<7} {'Trades':<8} {'WR%':<7} {'PF':<7} {'DD%':<7} {'DailyDD%':<9} {'PnL':<12} {'Status':<6}")
    print("-" * 110)

    for i, r in enumerate(results[:20], 1):
        p = r['params']
        s = r['summary']
        status = "PASS" if r['passes_filter'] else "FAIL"
        print(f"{i:<5} {p['tp_rr']:<7.1f} {p['stop_buffer_atr']:<7.1f} {p['min_range_atr']:<7.1f} {p['max_range_atr']:<7.1f} "
              f"{s['total_trades']:<8} {s['win_rate']*100:<7.1f} {s['profit_factor']:<7.3f} "
              f"{s['max_drawdown_pct']:<7.2f} {s['max_daily_dd']:<9.2f} ${s['total_pnl']:<11,.0f} {status:<6}")

if __name__ == "__main__":
    freeze_support()
    run_optimization()
