"""
ВАЛИДАЦИЯ v4.0 С ROLLING WINDOW 800 M15
========================================
Симулирует live условия:
- В каждый момент видим только последние 800 M15 баров
- Ресемплируем в ~50 H4 баров
- EMA20 считается только на этих 50 барах
- НЕТ look-ahead bias

Сравнение:
- Full history: $80,501 (13,750 H4 bars)
- Rolling 800 M15: ??? (~50 H4 bars)
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
import numpy as np
from pathlib import Path

from session_breakout_trader import (
    RISK_PER_TRADE, TP_RR, ATR_PERIOD, ATR_BUFFER,
    USE_H4_EMA_FILTER, H4_EMA_PERIOD,
    LONG_SESSIONS,
    SHORT_TYPE1_LOOKBACK_H4_BARS, SHORT_TYPE2_H4_LOOKBACK, SHORT_TYPE2_ATR_MULTIPLIER,
    calculate_atr, calculate_ema
)

ROLLING_WINDOW = 800  # M15 bars

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

def run_validation():
    print("="*80)
    print("ВАЛИДАЦИЯ v4.0 С ROLLING WINDOW 800 M15")
    print("="*80)
    print()

    # Load full data
    data_path = Path(__file__).parent.parent / "data_cache" / "dukascopy" / "m15" / "XAUUSD" / "xauusd_m15_2020-01-01_2026-04-18.parquet"
    df_full = pd.read_parquet(data_path)
    df_full = df_full.sort_index()

    print(f"Full dataset: {df_full.index[0]} - {df_full.index[-1]}")
    print(f"Total M15 bars: {len(df_full)}")
    print(f"Rolling window: {ROLLING_WINDOW} M15 bars (~{ROLLING_WINDOW/16:.0f} H4 bars)")
    print()

    trades = []
    active_long_trades = {}
    active_short = None
    balance = 10000

    short_type1_reversal_active = False
    short_type1_reversal_h4_high = None
    short_type2_reversal_active = False
    short_type2_reversal_h4_high = None
    last_h4_index = None

    dates = df_full.index.date
    unique_dates = sorted(set(dates))

    # Cache for H4 data
    cached_h4 = None
    cached_h4_time = None

    for date in unique_dates:
        day_data = df_full[df_full.index.date == date]

        if len(day_data) < 10:
            continue

        session_highs = {}
        session_lows = {}
        session_traded_today = {}  # Track if session already traded today

        for i in range(len(day_data)):
            current_time = day_data.index[i]

            # ROLLING WINDOW: оптимизированный доступ O(1)
            global_i = df_full.index.get_loc(current_time)
            available_data = df_full.iloc[max(0, global_i - ROLLING_WINDOW + 1):global_i + 1].copy()

            if len(available_data) < 100:  # Skip early bars
                continue

            # Calculate ATR on rolling window
            available_data['atr'] = calculate_atr(available_data, ATR_PERIOD)

            # Resample to H4 on rolling window (cache if same H4 bar)
            current_h4_time = pd.Timestamp(current_time).floor('4h')

            if cached_h4 is None or cached_h4_time != current_h4_time:
                df_h4 = available_data.resample('4h').agg({
                    'open': 'first',
                    'high': 'max',
                    'low': 'min',
                    'close': 'last'
                }).dropna()

                df_h4['atr'] = calculate_atr(df_h4, ATR_PERIOD)
                df_h4['ema20'] = calculate_ema(df_h4, H4_EMA_PERIOD)

                cached_h4 = df_h4
                cached_h4_time = current_h4_time
            else:
                df_h4 = cached_h4

            # Current bar data
            current_bar = available_data.iloc[-1]
            current_high = current_bar['high']
            current_low = current_bar['low']
            current_close = current_bar['close']
            current_atr = current_bar['atr']
            hour = current_time.hour

            if np.isnan(current_atr):
                continue

            # Check H4 data availability
            h4_bars = df_h4[df_h4.index <= current_time]
            if len(h4_bars) < max(SHORT_TYPE1_LOOKBACK_H4_BARS + 2, SHORT_TYPE2_H4_LOOKBACK + 1):
                continue

            current_h4 = h4_bars.iloc[-1]

            # Detect new H4 bar
            is_new_h4_bar = (current_h4.name != last_h4_index)

            # === MANAGE ACTIVE LONG TRADES ===
            for session_name in list(active_long_trades.keys()):
                trade = active_long_trades[session_name]
                apply_step_trailing(trade, current_low, current_high, is_long=True)

                exit_trade = False
                if current_low <= trade['sl']:
                    pnl = (trade['sl'] - trade['entry']) * trade['size']
                    balance += pnl
                    trade['exit'] = trade['sl']
                    trade['pnl'] = pnl
                    trade['status'] = 'sl'
                    exit_trade = True
                elif current_high >= trade['tp']:
                    pnl = (trade['tp'] - trade['entry']) * trade['size']
                    balance += pnl
                    trade['exit'] = trade['tp']
                    trade['pnl'] = pnl
                    trade['status'] = 'tp'
                    exit_trade = True

                if exit_trade:
                    trades.append(trade)
                    del active_long_trades[session_name]

            # === MANAGE ACTIVE SHORT TRADE ===
            if active_short:
                apply_step_trailing(active_short, current_low, current_high, is_long=False)

                exit_trade = False
                if current_high >= active_short['sl']:
                    pnl = (active_short['entry'] - active_short['sl']) * active_short['size']
                    balance += pnl
                    active_short['exit'] = active_short['sl']
                    active_short['pnl'] = pnl
                    active_short['status'] = 'sl'
                    exit_trade = True
                elif current_low <= active_short['tp']:
                    pnl = (active_short['entry'] - active_short['tp']) * active_short['size']
                    balance += pnl
                    active_short['exit'] = active_short['tp']
                    active_short['pnl'] = pnl
                    active_short['status'] = 'tp'
                    exit_trade = True

                if exit_trade:
                    trades.append(active_short)
                    active_short = None
                    short_type1_reversal_active = False
                    short_type2_reversal_active = False

            # === CHECK LONG SESSIONS ===
            for session_name, session_config in LONG_SESSIONS.items():
                if session_name in active_long_trades:
                    continue

                # Skip if already traded this session today
                if session_traded_today.get(session_name, False):
                    continue

                range_start, range_end = session_config['range_hours']
                entry_start = session_config['entry_start']
                entry_end = session_config['entry_end']

                # Track session range
                if range_start <= hour < range_end:
                    if session_name not in session_highs:
                        session_highs[session_name] = current_high
                        session_lows[session_name] = current_low
                    else:
                        session_highs[session_name] = max(session_highs[session_name], current_high)
                        session_lows[session_name] = min(session_lows[session_name], current_low)

                # Check breakout
                if entry_start <= hour < entry_end:
                    if session_name in session_highs and session_name in session_lows:
                        session_high = session_highs[session_name]
                        session_low = session_lows[session_name]

                        if current_close > session_high:
                            # H4 EMA20 filter
                            if USE_H4_EMA_FILTER:
                                if np.isnan(current_h4['ema20']) or current_h4['close'] <= current_h4['ema20']:
                                    continue

                            entry = session_high
                            sl = session_low - ATR_BUFFER * current_atr
                            risk = entry - sl
                            tp = entry + TP_RR * risk
                            size = RISK_PER_TRADE / risk

                            active_long_trades[session_name] = {
                                'entry_time': current_time,
                                'direction': 'LONG',
                                'session': session_name,
                                'entry': entry,
                                'sl': sl,
                                'tp': tp,
                                'initial_sl': sl,
                                'size': size,
                                'risk': RISK_PER_TRADE
                            }

                            # Mark session as traded today
                            session_traded_today[session_name] = True

                            # Debug: print first 5 LONG trades
                            if len(trades) < 5:
                                print(f"LONG #{len(trades)+1}: {current_time} | {session_name} | entry={entry:.2f} | session_high={session_high:.2f}")

            # === CHECK SHORT REVERSAL ===
            if active_short is None and 0 <= hour < 21:
                # ACTIVATION: Only on new H4 bar
                if is_new_h4_bar:
                    # Type1: Historical High
                    if not short_type1_reversal_active:
                        lookback_h4 = h4_bars.iloc[-(SHORT_TYPE1_LOOKBACK_H4_BARS+1):-1]
                        if len(lookback_h4) >= SHORT_TYPE1_LOOKBACK_H4_BARS:
                            historical_high = lookback_h4['high'].max()
                            prev_h4 = h4_bars.iloc[-2]

                            # Check if current H4 high broke historical high AND closed lower
                            if current_h4['high'] > historical_high and current_h4['close'] < prev_h4['close']:
                                short_type1_reversal_active = True
                                short_type1_reversal_h4_high = current_h4['high']

                                # Debug: print Type1 activation
                                if len(trades) < 200:
                                    print(f"SHORT Type1 ACTIVATED: {current_time} | H4 high={current_h4['high']:.2f} > hist={historical_high:.2f} | H4 close={current_h4['close']:.2f} < prev={prev_h4['close']:.2f}")

                    # Type2: Local Reversal
                    if not short_type2_reversal_active:
                        lookback_h4 = h4_bars.iloc[-(SHORT_TYPE2_H4_LOOKBACK+1):-1]
                        if len(lookback_h4) >= SHORT_TYPE2_H4_LOOKBACK:
                            local_low = lookback_h4['low'].min()
                            local_high = lookback_h4['high'].max()
                            move_size = local_high - local_low

                            if move_size >= SHORT_TYPE2_ATR_MULTIPLIER * current_atr:
                                if current_h4['high'] > local_high:
                                    short_type2_reversal_active = True
                                    short_type2_reversal_h4_high = current_h4['high']

                # M15 ENTRY: Check every M15 bar
                # Type1 entry
                if short_type1_reversal_active and short_type1_reversal_h4_high:
                    # Wait for M15 close below previous M15 low
                    if i > 0:
                        prev_m15_low = day_data.iloc[i-1]['low']
                        if current_close < prev_m15_low:
                            # H4 EMA20 filter
                            if USE_H4_EMA_FILTER:
                                if np.isnan(current_h4['ema20']) or current_h4['close'] >= current_h4['ema20']:
                                    short_type1_reversal_active = False
                                    short_type1_reversal_h4_high = None
                                else:
                                    entry = current_close
                                    sl = short_type1_reversal_h4_high + ATR_BUFFER * current_atr
                                    risk = sl - entry
                                    tp = entry - TP_RR * risk
                                    size = RISK_PER_TRADE / risk

                                    active_short = {
                                        'entry_time': current_time,
                                        'direction': 'SHORT',
                                        'session': 'short_type1',
                                        'entry': entry,
                                        'sl': sl,
                                        'tp': tp,
                                        'initial_sl': sl,
                                        'size': size,
                                        'risk': RISK_PER_TRADE
                                    }
                                    short_type1_reversal_active = False
                                    short_type1_reversal_h4_high = None
                            else:
                                entry = current_close
                                sl = short_type1_reversal_h4_high + ATR_BUFFER * current_atr
                                risk = sl - entry
                                tp = entry - TP_RR * risk
                                size = RISK_PER_TRADE / risk

                                active_short = {
                                    'entry_time': current_time,
                                    'direction': 'SHORT',
                                    'session': 'short_type1',
                                    'entry': entry,
                                    'sl': sl,
                                    'tp': tp,
                                    'initial_sl': sl,
                                    'size': size,
                                    'risk': RISK_PER_TRADE
                                }
                                short_type1_reversal_active = False
                                short_type1_reversal_h4_high = None

                # Type2 entry
                if short_type2_reversal_active and short_type2_reversal_h4_high:
                    # Wait for M15 close below previous M15 low
                    if i > 0:
                        prev_m15_low = day_data.iloc[i-1]['low']
                        if current_close < prev_m15_low:
                            # H4 EMA20 filter
                            if USE_H4_EMA_FILTER:
                                if np.isnan(current_h4['ema20']) or current_h4['close'] >= current_h4['ema20']:
                                    short_type2_reversal_active = False
                                    short_type2_reversal_h4_high = None
                                else:
                                    entry = current_close
                                    sl = short_type2_reversal_h4_high + ATR_BUFFER * current_atr
                                    risk = sl - entry
                                    tp = entry - TP_RR * risk
                                    size = RISK_PER_TRADE / risk

                                    active_short = {
                                        'entry_time': current_time,
                                        'direction': 'SHORT',
                                        'session': 'short_type2',
                                        'entry': entry,
                                        'sl': sl,
                                        'tp': tp,
                                        'initial_sl': sl,
                                        'size': size,
                                        'risk': RISK_PER_TRADE
                                    }
                                    short_type2_reversal_active = False
                                    short_type2_reversal_h4_high = None
                            else:
                                entry = current_close
                                sl = short_type2_reversal_h4_high + ATR_BUFFER * current_atr
                                risk = sl - entry
                                tp = entry - TP_RR * risk
                                size = RISK_PER_TRADE / risk

                                active_short = {
                                    'entry_time': current_time,
                                    'direction': 'SHORT',
                                    'session': 'short_type2',
                                    'entry': entry,
                                    'sl': sl,
                                    'tp': tp,
                                    'initial_sl': sl,
                                    'size': size,
                                    'risk': RISK_PER_TRADE
                                }
                                short_type2_reversal_active = False
                                short_type2_reversal_h4_high = None

            # Update last H4 index
            if is_new_h4_bar:
                last_h4_index = current_h4.name

    # === RESULTS ===
    print("="*80)
    print("РЕЗУЛЬТАТЫ")
    print("="*80)
    print()

    df_trades = pd.DataFrame(trades)

    total_trades = len(df_trades)
    wins = len(df_trades[df_trades['pnl'] > 0])
    losses = len(df_trades[df_trades['pnl'] < 0])
    win_rate = wins / total_trades * 100 if total_trades > 0 else 0
    total_pnl = df_trades['pnl'].sum()
    final_balance = balance

    print(f"Total Trades: {total_trades}")
    print(f"Wins: {wins} | Losses: {losses}")
    print(f"Win Rate: {win_rate:.1f}%")
    print(f"Total PnL: ${total_pnl:,.0f}")
    print(f"Final Balance: ${final_balance:,.0f}")
    print()

    # Breakdown by direction
    long_trades = df_trades[df_trades['direction'] == 'LONG']
    short_trades = df_trades[df_trades['direction'] == 'SHORT']

    print("BREAKDOWN:")
    print(f"  LONG: {len(long_trades)} trades, ${long_trades['pnl'].sum():,.0f}")
    print(f"  SHORT: {len(short_trades)} trades, ${short_trades['pnl'].sum():,.0f}")
    print()

    # By year
    df_trades['year'] = pd.to_datetime(df_trades['entry_time']).dt.year
    print("PNL BY YEAR:")
    for year in sorted(df_trades['year'].unique()):
        year_pnl = df_trades[df_trades['year'] == year]['pnl'].sum()
        print(f"  {year}: ${year_pnl:,.0f}")
    print()

    # Drawdown
    df_trades['cumulative'] = df_trades['pnl'].cumsum()
    df_trades['peak'] = df_trades['cumulative'].cummax()
    df_trades['drawdown'] = df_trades['cumulative'] - df_trades['peak']
    max_dd = df_trades['drawdown'].min()
    max_dd_pct = abs(max_dd / 10000) * 100

    print(f"Max Drawdown: ${max_dd:,.0f} ({max_dd_pct:.2f}%)")
    print()

    # Breakdown by session
    print("BREAKDOWN BY SESSION:")
    for session in ['asian', 'london', 'ny', 'short_type1', 'short_type2']:
        session_trades = df_trades[df_trades['session'] == session]
        if len(session_trades) > 0:
            print(f"  {session}: {len(session_trades)} trades, ${session_trades['pnl'].sum():,.0f}")
    print()

    # Comparison
    print("="*80)
    print("СРАВНЕНИЕ")
    print("="*80)
    print(f"Full history (13,750 H4): $80,501 | 881 trades | DD 6.99%")
    print(f"Rolling 800 M15 (~50 H4): ${total_pnl:,.0f} | {total_trades} trades | DD {max_dd_pct:.2f}%")
    print()

    diff_pnl = total_pnl - 80501
    diff_pct = (diff_pnl / 80501) * 100
    print(f"Difference: ${diff_pnl:,.0f} ({diff_pct:+.1f}%)")
    print()

    if abs(diff_pct) <= 10:
        print("[OK] Results within ±10% - rolling window matches backtest")
    else:
        print("[WARNING] Results differ by more than 10%")
        print("Possible reasons:")
        print("- EMA20 on 50 H4 bars vs 13,750 H4 bars")
        print("- Early period has insufficient warmup")
        print("- Different EMA20 values affect filter decisions")

if __name__ == "__main__":
    run_validation()
