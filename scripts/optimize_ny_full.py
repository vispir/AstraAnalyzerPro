"""
Полная оптимизация NY LONG параметров
======================================
Baseline v3.0: $69,520 PnL, 557 trades, DD 6.65%

Перебор:
- Временные окна (range_start, range_end, entry_start, entry_end)
- ATR_BUFFER для NY (0.3, 0.5, 0.7)
- Range фильтры (min_range_atr, max_range_atr)

Цель: Найти максимальный Total PnL > $69,520 при DD < 8%
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from session_breakout_trader import (
    RISK_PER_TRADE, TP_RR, ATR_PERIOD, USE_H4_EMA_FILTER, H4_EMA_PERIOD,
    SHORT_TYPE1_LOOKBACK_H4_BARS, SHORT_TYPE2_H4_LOOKBACK, SHORT_TYPE2_ATR_MULTIPLIER,
    calculate_atr, calculate_ema
)

import pandas as pd
import numpy as np
from pathlib import Path
from itertools import product

# Load data
data_path = Path(__file__).parent.parent / "data_cache" / "dukascopy" / "m15" / "XAUUSD" / "xauusd_m15_2020-01-01_2026-04-18.parquet"
df = pd.read_parquet(data_path)
df = df.sort_index()

print("="*80)
print("ПОЛНАЯ ОПТИМИЗАЦИЯ NY LONG")
print("="*80)
print(f"Period: {df.index[0]} - {df.index[-1]}")
print(f"Baseline v3.0: $69,520 PnL, DD 6.65%")
print()

# Generate all valid combinations
range_starts = [12, 13, 14, 15]
range_ends = [16, 17, 18]
entry_offsets = [2, 3, 4]  # entry_end = range_end + offset
atr_buffers = [0.3, 0.5, 0.7]
min_range_filters = [0, 0.3, 0.5]  # 0 = no filter
max_range_filters = [999, 2.0, 3.0]  # 999 = no filter

combinations = []
for rs, re, offset, buf, min_r, max_r in product(
    range_starts, range_ends, entry_offsets, atr_buffers, min_range_filters, max_range_filters
):
    # Validate: range minimum 1 hour
    if re - rs < 1:
        continue

    # entry_start = range_end
    es = re
    ee = re + offset

    # Validate: entry minimum 1 hour
    if ee - es < 1:
        continue

    # Validate: entry_end <= 22 (reasonable limit)
    if ee > 22:
        continue

    combinations.append({
        'range_start': rs,
        'range_end': re,
        'entry_start': es,
        'entry_end': ee,
        'atr_buffer': buf,
        'min_range_atr': min_r,
        'max_range_atr': max_r
    })

print(f"Total combinations to test: {len(combinations)}")
print()

# Prepare data
df['atr'] = calculate_atr(df, ATR_PERIOD)
df_h4 = df.resample('4h').agg({'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last'}).dropna()
df_h4['atr'] = calculate_atr(df_h4, ATR_PERIOD)
df_h4['ema20'] = calculate_ema(df_h4, H4_EMA_PERIOD)

def apply_step_trailing(active_trade, current_low, current_high, is_long=True):
    if is_long:
        risk = active_trade['entry'] - active_trade['initial_sl']
        profit_in_r = (current_low - active_trade['entry']) / risk
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
    else:
        risk = active_trade['initial_sl'] - active_trade['entry']
        profit_in_r = (active_trade['entry'] - current_high) / risk
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

def run_backtest(params):
    """Run backtest with specific NY parameters"""

    # Asian/London use default ATR_BUFFER (0.5)
    LONG_SESSIONS = {
        'asian': (7, 10, 0.5),  # (start, end, atr_buffer)
        'london': (13, 16, 0.5),
        'ny': (params['range_start'], params['range_end'], params['atr_buffer'])
    }

    NY_ENTRY_START = params['entry_start']
    NY_ENTRY_END = params['entry_end']
    NY_MIN_RANGE = params['min_range_atr']
    NY_MAX_RANGE = params['max_range_atr']

    trades = []
    balance = 10000
    peak_balance = 10000
    max_dd = 0
    active_long = None
    active_short = None

    short_type1_reversal_active = False
    short_type1_reversal_h4_high = None
    short_type2_reversal_active = False
    short_type2_reversal_h4_high = None
    last_h4_index = None

    dates = df.index.date
    unique_dates = sorted(set(dates))

    for date in unique_dates:
        day_data = df[df.index.date == date]

        if len(day_data) < 10:
            continue

        session_highs = {}
        session_lows = {}

        highs = day_data['high'].to_numpy()
        lows = day_data['low'].to_numpy()
        closes = day_data['close'].to_numpy()
        atrs = day_data['atr'].to_numpy()
        hours = np.array([t.hour for t in day_data.index])
        times = day_data.index.to_numpy()

        for i in range(len(day_data)):
            current_time = times[i]
            hour = hours[i]

            h4_bars = df_h4[df_h4.index <= current_time]
            if len(h4_bars) < max(SHORT_TYPE1_LOOKBACK_H4_BARS + 2, SHORT_TYPE2_H4_LOOKBACK + 1):
                continue

            current_h4 = h4_bars.iloc[-1]
            atr = atrs[i]

            if np.isnan(atr):
                continue

            # LONG TRADE MANAGEMENT
            if active_long is not None:
                apply_step_trailing(active_long, lows[i], highs[i], is_long=True)

                if lows[i] <= active_long['sl']:
                    pnl = (active_long['sl'] - active_long['entry']) * active_long['size']
                    balance += pnl
                    trades.append({'pnl': pnl, 'direction': 'LONG', 'session': active_long['session'], 'year': current_time.year})
                    active_long = None
                elif highs[i] >= active_long['tp']:
                    pnl = (active_long['tp'] - active_long['entry']) * active_long['size']
                    balance += pnl
                    trades.append({'pnl': pnl, 'direction': 'LONG', 'session': active_long['session'], 'year': current_time.year})
                    active_long = None

            # SHORT TRADE MANAGEMENT
            if active_short is not None:
                apply_step_trailing(active_short, lows[i], highs[i], is_long=False)

                if highs[i] >= active_short['sl']:
                    pnl = (active_short['entry'] - active_short['sl']) * active_short['size']
                    balance += pnl
                    trades.append({'pnl': pnl, 'direction': 'SHORT', 'year': current_time.year})
                    active_short = None
                    short_type1_reversal_active = False
                    short_type2_reversal_active = False
                elif lows[i] <= active_short['tp']:
                    pnl = (active_short['entry'] - active_short['tp']) * active_short['size']
                    balance += pnl
                    trades.append({'pnl': pnl, 'direction': 'SHORT', 'year': current_time.year})
                    active_short = None
                    short_type1_reversal_active = False
                    short_type2_reversal_active = False

            # Update DD
            if balance > peak_balance:
                peak_balance = balance
            dd = (peak_balance - balance) / peak_balance * 100
            if dd > max_dd:
                max_dd = dd

            # LONG LOGIC
            if active_long is None:
                # Track session ranges
                for session_name, (start_hour, end_hour, session_buffer) in LONG_SESSIONS.items():
                    if start_hour <= hour < end_hour:
                        if session_name not in session_highs:
                            session_highs[session_name] = highs[i]
                            session_lows[session_name] = lows[i]
                        else:
                            session_highs[session_name] = max(session_highs[session_name], highs[i])
                            session_lows[session_name] = min(session_lows[session_name], lows[i])

                # Check breakout
                for session_name, (start_hour, end_hour, session_buffer) in LONG_SESSIONS.items():
                    if session_name in session_highs:
                        # NY: custom entry window
                        if session_name == 'ny':
                            if not (NY_ENTRY_START <= hour < NY_ENTRY_END):
                                continue
                        else:
                            # Asian/London: after session ends
                            if hour < end_hour:
                                continue

                        session_high = session_highs[session_name]
                        session_low = session_lows[session_name]
                        range_size = session_high - session_low

                        # NY range filters
                        if session_name == 'ny':
                            min_range = NY_MIN_RANGE * atr
                            max_range = NY_MAX_RANGE * atr

                            if NY_MIN_RANGE > 0 and range_size < min_range:
                                continue
                            if NY_MAX_RANGE < 999 and range_size > max_range:
                                continue

                        if closes[i] > session_high:
                            # Check if SHORT is active (one position at a time)
                            if active_short is not None:
                                continue

                            if USE_H4_EMA_FILTER:
                                if pd.isna(current_h4['ema20']):
                                    continue
                                if current_h4['close'] < current_h4['ema20']:
                                    continue

                            entry = closes[i]
                            sl = session_low - session_buffer * atr
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
                                'session': session_name
                            }
                            break

            # SHORT LOGIC (unchanged from v3.0)
            if active_short is None and hour < 21:
                prev_h4 = h4_bars.iloc[-2]

                current_h4_index = current_h4.name
                if last_h4_index != current_h4_index:
                    last_h4_index = current_h4_index

                    if USE_H4_EMA_FILTER:
                        if pd.isna(current_h4['ema20']):
                            short_type1_reversal_active = False
                            short_type2_reversal_active = False
                            continue

                        if current_h4['close'] >= current_h4['ema20']:
                            short_type1_reversal_active = False
                            short_type2_reversal_active = False
                            continue

                    if not short_type1_reversal_active:
                        lookback_highs = h4_bars.iloc[-SHORT_TYPE1_LOOKBACK_H4_BARS-1:-1]['high']
                        historical_high = lookback_highs.max()

                        if current_h4['high'] > historical_high:
                            if current_h4['close'] < prev_h4['close']:
                                short_type1_reversal_active = True
                                short_type1_reversal_h4_high = current_h4['high']

                    if not short_type2_reversal_active:
                        if len(h4_bars) >= SHORT_TYPE2_H4_LOOKBACK + 1:
                            lookback_bars = h4_bars.iloc[-SHORT_TYPE2_H4_LOOKBACK-1:-1]
                            price_change = current_h4['high'] - lookback_bars['low'].min()
                            h4_atr = current_h4.get('atr', atr)

                            if not np.isnan(h4_atr) and price_change >= SHORT_TYPE2_ATR_MULTIPLIER * h4_atr:
                                if current_h4['close'] < prev_h4['close']:
                                    short_type2_reversal_active = True
                                    short_type2_reversal_h4_high = current_h4['high']

                if i > 0:
                    prev_m15_low = lows[i-1]

                    if short_type1_reversal_active and closes[i] < prev_m15_low:
                        entry = closes[i]
                        sl = short_type1_reversal_h4_high + 0.5 * atr
                        risk = sl - entry

                        if risk > 0:
                            tp = entry - risk * TP_RR
                            size = RISK_PER_TRADE / risk

                            active_short = {
                                'entry': entry,
                                'sl': sl,
                                'initial_sl': sl,
                                'tp': tp,
                                'size': size
                            }
                            short_type1_reversal_active = False

                    elif short_type2_reversal_active and closes[i] < prev_m15_low:
                        entry = closes[i]
                        sl = short_type2_reversal_h4_high + 0.5 * atr
                        risk = sl - entry

                        if risk > 0:
                            tp = entry - risk * TP_RR
                            size = RISK_PER_TRADE / risk

                            active_short = {
                                'entry': entry,
                                'sl': sl,
                                'initial_sl': sl,
                                'tp': tp,
                                'size': size
                            }
                            short_type2_reversal_active = False

    return trades, max_dd

# Run all combinations
print("Running optimization...")
results = []

for idx, params in enumerate(combinations, 1):
    if idx % 50 == 0:
        print(f"Progress: {idx}/{len(combinations)} ({idx/len(combinations)*100:.1f}%)")

    trades, max_dd = run_backtest(params)
    trades_df = pd.DataFrame(trades)

    if len(trades_df) == 0:
        continue

    long_df = trades_df[trades_df['direction'] == 'LONG']
    ny_df = long_df[long_df['session'] == 'ny']

    total_pnl = trades_df['pnl'].sum()
    ny_trades = len(ny_df)
    ny_pnl = ny_df['pnl'].sum() if len(ny_df) > 0 else 0
    ny_wr = len(ny_df[ny_df['pnl'] > 0]) / len(ny_df) * 100 if len(ny_df) > 0 else 0

    # Check if all years profitable
    yearly_pnl = trades_df.groupby('year')['pnl'].sum()
    all_years_profitable = all(yearly_pnl > 0)

    # Apply filters
    if total_pnl <= 69520:
        continue
    if max_dd >= 8.0:
        continue
    if not all_years_profitable:
        continue
    if ny_trades < 20:
        continue

    results.append({
        'params': params,
        'total_pnl': total_pnl,
        'ny_trades': ny_trades,
        'ny_pnl': ny_pnl,
        'ny_wr': ny_wr,
        'max_dd': max_dd,
        'yearly_pnl': yearly_pnl.to_dict(),
        'total_trades': len(trades_df)
    })

print(f"\nCompleted! Found {len(results)} combinations passing filters.")
print()

# Sort by total PnL
results_sorted = sorted(results, key=lambda x: x['total_pnl'], reverse=True)

if len(results_sorted) == 0:
    print("="*80)
    print("NO COMBINATIONS PASSED FILTERS")
    print("="*80)
    print("Filters:")
    print("  - Total PnL > $69,520")
    print("  - Max DD < 8%")
    print("  - All years profitable")
    print("  - NY trades >= 20")
else:
    print("="*80)
    print("TOP 10 RESULTS")
    print("="*80)
    print(f"{'#':<3} {'Range':<10} {'Entry':<10} {'Buffer':<7} {'Min R':<6} {'Max R':<6} {'Total PnL':<12} {'NY':<15} {'DD%':<6}")
    print("-"*80)

    for idx, r in enumerate(results_sorted[:10], 1):
        p = r['params']
        range_str = f"{p['range_start']}-{p['range_end']}"
        entry_str = f"{p['entry_start']}-{p['entry_end']}"
        ny_str = f"{r['ny_trades']}t ${r['ny_pnl']:,.0f}"

        print(f"{idx:<3} {range_str:<10} {entry_str:<10} {p['atr_buffer']:<7.1f} {p['min_range_atr']:<6.1f} {p['max_range_atr']:<6.1f} ${r['total_pnl']:>10,.0f} {ny_str:<15} {r['max_dd']:>5.2f}")

    print()
    print("="*80)
    print("TOP-1 DETAILS")
    print("="*80)

    top1 = results_sorted[0]
    p = top1['params']

    print(f"\nParameters:")
    print(f"  Range window: {p['range_start']}-{p['range_end']} UTC")
    print(f"  Entry window: {p['entry_start']}-{p['entry_end']} UTC")
    print(f"  ATR Buffer: {p['atr_buffer']}")
    print(f"  Min Range: {p['min_range_atr']} ATR")
    print(f"  Max Range: {p['max_range_atr']} ATR")
    print()
    print(f"Results:")
    print(f"  Total PnL: ${top1['total_pnl']:,.0f} (+${top1['total_pnl'] - 69520:,.0f} vs baseline)")
    print(f"  Total Trades: {top1['total_trades']}")
    print(f"  NY LONG: {top1['ny_trades']} trades, ${top1['ny_pnl']:,.0f} PnL, WR {top1['ny_wr']:.1f}%")
    print(f"  Max DD: {top1['max_dd']:.2f}%")
    print()
    print("PnL by year:")
    for year in sorted(top1['yearly_pnl'].keys()):
        print(f"  {year}: ${top1['yearly_pnl'][year]:,.0f}")

    print()
    print("="*80)
    print(f"Baseline v3.0: $69,520 PnL, DD 6.65%")
    print(f"Best result: ${top1['total_pnl']:,.0f} PnL, DD {top1['max_dd']:.2f}%")
    print(f"Improvement: +${top1['total_pnl'] - 69520:,.0f} ({(top1['total_pnl'] - 69520) / 69520 * 100:.1f}%)")
    print("="*80)
