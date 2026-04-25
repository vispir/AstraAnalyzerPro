"""
Быстрый тест 15 комбинаций NY LONG
===================================
Baseline v3.0: $69,520 PnL, 557 trades, DD 6.65%
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

# Load data
data_path = Path(__file__).parent.parent / "data_cache" / "dukascopy" / "m15" / "XAUUSD" / "xauusd_m15_2020-01-01_2026-04-18.parquet"
df = pd.read_parquet(data_path)
df = df.sort_index()

print("="*80)
print("БЫСТРЫЙ ТЕСТ 15 КОМБИНАЦИЙ NY LONG")
print("="*80)
print(f"Period: {df.index[0]} - {df.index[-1]}")
print(f"Baseline v3.0: $69,520 PnL, DD 6.65%")
print()

# 15 combinations to test
# (range_start, range_end, entry_start, entry_end, atr_buffer, min_range_atr, max_range_atr)
COMBINATIONS = [
    (13, 17, 17, 21, 0.5, 0, 999),      # 1. baseline
    (14, 17, 17, 21, 0.5, 0, 999),      # 2.
    (15, 17, 17, 21, 0.5, 0, 999),      # 3.
    (13, 17, 17, 21, 0.3, 0, 999),      # 4.
    (13, 17, 17, 21, 0.7, 0, 999),      # 5.
    (14, 18, 18, 22, 0.5, 0, 999),      # 6.
    (15, 18, 18, 22, 0.5, 0, 999),      # 7.
    (13, 16, 16, 20, 0.5, 0, 999),      # 8.
    (14, 16, 16, 20, 0.5, 0, 999),      # 9.
    (15, 17, 17, 20, 0.3, 0, 999),      # 10.
    (13, 17, 17, 21, 0.5, 0.5, 3.0),    # 11. with range filters
    (14, 17, 17, 21, 0.3, 0.5, 999),    # 12. with min_range
    (15, 18, 18, 21, 0.5, 0, 999),      # 13.
    (14, 17, 17, 20, 0.5, 0, 999),      # 14.
    (13, 18, 18, 22, 0.3, 0, 999),      # 15.
]

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

def run_backtest(rs, re, es, ee, buf, min_r, max_r):
    """Run backtest with specific NY parameters"""

    LONG_SESSIONS = {
        'asian': (7, 10, 0.5),
        'london': (13, 16, 0.5),
        'ny': (rs, re, buf)
    }

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
                for session_name, (start_hour, end_hour, session_buffer) in LONG_SESSIONS.items():
                    if start_hour <= hour < end_hour:
                        if session_name not in session_highs:
                            session_highs[session_name] = highs[i]
                            session_lows[session_name] = lows[i]
                        else:
                            session_highs[session_name] = max(session_highs[session_name], highs[i])
                            session_lows[session_name] = min(session_lows[session_name], lows[i])

                for session_name, (start_hour, end_hour, session_buffer) in LONG_SESSIONS.items():
                    if session_name in session_highs:
                        if session_name == 'ny':
                            if not (es <= hour < ee):
                                continue
                        else:
                            if hour < end_hour:
                                continue

                        session_high = session_highs[session_name]
                        session_low = session_lows[session_name]
                        range_size = session_high - session_low

                        # NY range filters
                        if session_name == 'ny':
                            if min_r > 0 and range_size < min_r * atr:
                                continue
                            if max_r < 999 and range_size > max_r * atr:
                                continue

                        if closes[i] > session_high:
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

            # SHORT LOGIC
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
results = []

for idx, (rs, re, es, ee, buf, min_r, max_r) in enumerate(COMBINATIONS, 1):
    print(f"Testing {idx}/15: range ({rs}-{re}), entry ({es}-{ee}), buf={buf}, filters=({min_r},{max_r})...", end=" ")

    trades, max_dd = run_backtest(rs, re, es, ee, buf, min_r, max_r)
    trades_df = pd.DataFrame(trades)

    long_df = trades_df[trades_df['direction'] == 'LONG']
    ny_df = long_df[long_df['session'] == 'ny']

    total_pnl = trades_df['pnl'].sum()
    ny_trades = len(ny_df)
    ny_pnl = ny_df['pnl'].sum() if len(ny_df) > 0 else 0
    ny_wr = len(ny_df[ny_df['pnl'] > 0]) / len(ny_df) * 100 if len(ny_df) > 0 else 0

    yearly_pnl = trades_df.groupby('year')['pnl'].sum()
    all_years_profitable = all(yearly_pnl > 0)

    results.append({
        'idx': idx,
        'params': (rs, re, es, ee, buf, min_r, max_r),
        'total_pnl': total_pnl,
        'ny_trades': ny_trades,
        'ny_pnl': ny_pnl,
        'ny_wr': ny_wr,
        'max_dd': max_dd,
        'all_years_profit': all_years_profitable
    })

    print(f"${total_pnl:,.0f} | NY: {ny_trades}t ${ny_pnl:,.0f} | DD {max_dd:.2f}%")

# Sort by total PnL
results_sorted = sorted(results, key=lambda x: x['total_pnl'], reverse=True)

print()
print("="*80)
print("ALL RESULTS")
print("="*80)
print(f"{'#':<3} {'Range':<10} {'Entry':<10} {'Buf':<5} {'Filters':<12} {'Total PnL':<12} {'NY':<18} {'DD%':<6} {'All Y+':<6}")
print("-"*80)

for r in results_sorted:
    rs, re, es, ee, buf, min_r, max_r = r['params']
    range_str = f"{rs}-{re}"
    entry_str = f"{es}-{ee}"
    filters_str = f"{min_r:.1f},{max_r:.1f}" if min_r > 0 or max_r < 999 else "none"
    ny_str = f"{r['ny_trades']}t ${r['ny_pnl']:,.0f} WR{r['ny_wr']:.0f}%"
    all_y = "YES" if r['all_years_profit'] else "NO"

    print(f"{r['idx']:<3} {range_str:<10} {entry_str:<10} {buf:<5.1f} {filters_str:<12} ${r['total_pnl']:>10,.0f} {ny_str:<18} {r['max_dd']:>5.2f} {all_y:<6}")

print()
print("="*80)
print("TOP 3")
print("="*80)

for rank, r in enumerate(results_sorted[:3], 1):
    rs, re, es, ee, buf, min_r, max_r = r['params']
    vs_baseline = r['total_pnl'] - 69520

    print(f"\n#{rank} - Combo #{r['idx']}")
    print(f"  Range {rs}-{re}, Entry {es}-{ee}, Buffer {buf}")
    if min_r > 0 or max_r < 999:
        print(f"  Range filters: min={min_r} ATR, max={max_r} ATR")
    print(f"  Total PnL: ${r['total_pnl']:,.0f} ({'+' if vs_baseline > 0 else ''}${vs_baseline:,.0f} vs baseline)")
    print(f"  NY LONG: {r['ny_trades']} trades, ${r['ny_pnl']:,.0f} PnL, WR {r['ny_wr']:.1f}%")
    print(f"  Max DD: {r['max_dd']:.2f}%")
    print(f"  All years profitable: {'YES' if r['all_years_profit'] else 'NO'}")

print()
print("="*80)
print(f"Baseline v3.0: $69,520 PnL, DD 6.65%")
print(f"Best result: ${results_sorted[0]['total_pnl']:,.0f} PnL, DD {results_sorted[0]['max_dd']:.2f}%")
if results_sorted[0]['total_pnl'] > 69520 and results_sorted[0]['max_dd'] < 8.0:
    print("STATUS: WINNER FOUND!")
else:
    print("STATUS: No improvement over baseline")
print("="*80)
