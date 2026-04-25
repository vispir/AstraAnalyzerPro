"""
Оптимизация NY LONG временных окон
===================================
Baseline v3.0: Asian + London LONG, SHORT 0-21 UTC → $69,520

ЗАДАЧА: Найти прибыльную комбинацию NY LONG окон

Тестируем 10 комбинаций (range_start, range_end, entry_start, entry_end)
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from session_breakout_trader import (
    RISK_PER_TRADE, TP_RR, ATR_PERIOD, USE_H4_EMA_FILTER, H4_EMA_PERIOD,
    ATR_BUFFER, SHORT_TYPE1_LOOKBACK_H4_BARS,
    SHORT_TYPE2_H4_LOOKBACK, SHORT_TYPE2_ATR_MULTIPLIER,
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
print("NY LONG WINDOWS OPTIMIZATION")
print("="*80)
print(f"Period: {df.index[0]} - {df.index[-1]}")
print(f"Baseline v3.0: $69,520 PnL (Asian + London LONG, SHORT 0-21 UTC)")
print()

# NY window combinations to test
NY_COMBINATIONS = [
    (13, 17, 17, 20),
    (13, 17, 18, 21),
    (14, 17, 17, 20),
    (14, 18, 18, 21),
    (15, 18, 18, 22),
    (12, 16, 16, 20),
    (13, 16, 16, 19),
    (14, 17, 18, 22),
    (15, 17, 17, 21),
    (13, 18, 18, 22),
]

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

df['atr'] = calculate_atr(df, ATR_PERIOD)

# H4 data
df_h4 = df.resample('4h').agg({'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last'}).dropna()
df_h4['atr'] = calculate_atr(df_h4, ATR_PERIOD)
df_h4['ema20'] = calculate_ema(df_h4, H4_EMA_PERIOD)

def run_backtest(ny_range_start, ny_range_end, ny_entry_start, ny_entry_end):
    """Run backtest with specific NY window configuration"""

    # LONG sessions: Asian + London + NY
    LONG_SESSIONS = {
        'asian': (7, 10),
        'london': (13, 16),
        'ny': (ny_range_start, ny_range_end)
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

            # LONG LOGIC (Asian + London + NY)
            if active_long is None:
                # Track session ranges during session hours
                for session_name, (start_hour, end_hour) in LONG_SESSIONS.items():
                    if start_hour <= hour < end_hour:
                        if session_name not in session_highs:
                            session_highs[session_name] = highs[i]
                            session_lows[session_name] = lows[i]
                        else:
                            session_highs[session_name] = max(session_highs[session_name], highs[i])
                            session_lows[session_name] = min(session_lows[session_name], lows[i])

                # Check breakout after session ends
                for session_name, (start_hour, end_hour) in LONG_SESSIONS.items():
                    if session_name in session_highs:
                        # Special handling for NY: custom entry window
                        if session_name == 'ny':
                            if not (ny_entry_start <= hour < ny_entry_end):
                                continue
                        else:
                            # Asian/London: breakout check starts after session ends
                            if hour < end_hour:
                                continue

                        session_high = session_highs[session_name]

                        if closes[i] > session_high:
                            if USE_H4_EMA_FILTER:
                                if pd.isna(current_h4['ema20']):
                                    continue
                                if current_h4['close'] < current_h4['ema20']:
                                    continue

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
                        sl = short_type1_reversal_h4_high + ATR_BUFFER * atr
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
                        sl = short_type2_reversal_h4_high + ATR_BUFFER * atr
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

for idx, (rs, re, es, ee) in enumerate(NY_COMBINATIONS, 1):
    print(f"Testing combination {idx}/10: range ({rs}-{re}), entry ({es}-{ee})...", end=" ")

    trades, max_dd = run_backtest(rs, re, es, ee)
    trades_df = pd.DataFrame(trades)

    long_df = trades_df[trades_df['direction'] == 'LONG']
    ny_df = long_df[long_df['session'] == 'ny']

    total_pnl = trades_df['pnl'].sum()
    ny_trades = len(ny_df)
    ny_pnl = ny_df['pnl'].sum() if len(ny_df) > 0 else 0
    ny_wr = len(ny_df[ny_df['pnl'] > 0]) / len(ny_df) * 100 if len(ny_df) > 0 else 0

    # Check if all years profitable
    yearly_pnl = trades_df.groupby('year')['pnl'].sum()
    all_years_profitable = all(yearly_pnl > 0)

    results.append({
        'combo': f"({rs},{re},{es},{ee})",
        'range_window': f"{rs}-{re}",
        'entry_window': f"{es}-{ee}",
        'total_pnl': total_pnl,
        'ny_trades': ny_trades,
        'ny_pnl': ny_pnl,
        'ny_wr': ny_wr,
        'max_dd': max_dd,
        'all_years_profit': all_years_profitable,
        'total_trades': len(trades_df)
    })

    print(f"${total_pnl:,.0f} | NY: {ny_trades} trades, ${ny_pnl:,.0f}")

# Sort by total PnL
results_sorted = sorted(results, key=lambda x: x['total_pnl'], reverse=True)

print()
print("="*80)
print("RESULTS - ALL COMBINATIONS")
print("="*80)
print(f"{'#':<3} {'Range':<8} {'Entry':<8} {'Total PnL':<12} {'NY Trades':<10} {'NY PnL':<10} {'NY WR%':<8} {'DD%':<6} {'All Years+':<10}")
print("-"*80)

for idx, r in enumerate(results_sorted, 1):
    all_years_mark = "YES" if r['all_years_profit'] else "NO"
    print(f"{idx:<3} {r['range_window']:<8} {r['entry_window']:<8} ${r['total_pnl']:>10,.0f} {r['ny_trades']:>10} ${r['ny_pnl']:>9,.0f} {r['ny_wr']:>7.1f} {r['max_dd']:>5.2f} {all_years_mark:<10}")

print()
print("="*80)
print("TOP 3 COMBINATIONS")
print("="*80)

for idx, r in enumerate(results_sorted[:3], 1):
    vs_baseline = r['total_pnl'] - 69520
    vs_sign = "+" if vs_baseline > 0 else ""

    print(f"\n#{idx} - Range {r['range_window']}, Entry {r['entry_window']}")
    print(f"  Total PnL: ${r['total_pnl']:,.0f} ({vs_sign}${vs_baseline:,.0f} vs baseline)")
    print(f"  NY LONG: {r['ny_trades']} trades, ${r['ny_pnl']:,.0f} PnL, WR {r['ny_wr']:.1f}%")
    print(f"  Max DD: {r['max_dd']:.2f}%")
    print(f"  All years profitable: {'YES' if r['all_years_profit'] else 'NO'}")

print()
print("="*80)
print(f"Baseline v3.0 (no NY): $69,520 PnL, DD 6.65%")
print(f"Best result: ${results_sorted[0]['total_pnl']:,.0f} PnL, DD {results_sorted[0]['max_dd']:.2f}%")
print(f"Improvement: {'+' if results_sorted[0]['total_pnl'] > 69520 else ''}${results_sorted[0]['total_pnl'] - 69520:,.0f}")
print("="*80)
