"""
ВАЛИДАЦИЯ v4.0 ПЕРЕД ДЕПЛОЕМ
==============================
Финальная проверка Session Breakout v4.0 с риском $120

Параметры:
- Multiple positions: Asian + London + NY + SHORT одновременно
- Risk: $120
- ATR: 14
- TP: 5.5R
- H4 EMA20 filter

Ожидаемые результаты:
- Total PnL: ~$80,501
- Trades: 881
- Max DD: ~6.99%
- Max Daily DD: ~3.28%
- Все годы прибыльны
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Import from session_breakout_trader_v4
from session_breakout_trader_v4 import (
    RISK_PER_TRADE, TP_RR, ATR_PERIOD, ATR_BUFFER,
    USE_H4_EMA_FILTER, H4_EMA_PERIOD,
    LONG_SESSIONS,
    SHORT_TYPE1_LOOKBACK_H4_BARS, SHORT_TYPE2_H4_LOOKBACK, SHORT_TYPE2_ATR_MULTIPLIER,
    calculate_atr, calculate_ema
)

import pandas as pd
import numpy as np
from pathlib import Path

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
    print("ВАЛИДАЦИЯ v4.0 ПЕРЕД ДЕПЛОЕМ")
    print("="*80)
    print()
    print(f"Risk per trade: ${RISK_PER_TRADE}")
    print(f"TP: {TP_RR}R")
    print(f"ATR: {ATR_PERIOD}")
    print(f"H4 EMA20 filter: {USE_H4_EMA_FILTER}")
    print()

    # Load data
    data_path = Path(__file__).parent.parent / "data_cache" / "dukascopy" / "m15" / "XAUUSD" / "xauusd_m15_2020-01-01_2026-04-18.parquet"
    df = pd.read_parquet(data_path)
    df = df.sort_index()

    print(f"Period: {df.index[0]} - {df.index[-1]}")
    print()

    # Prepare data
    df['atr'] = calculate_atr(df, ATR_PERIOD)
    df_h4 = df.resample('4h').agg({'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last'}).dropna()
    df_h4['atr'] = calculate_atr(df_h4, ATR_PERIOD)
    df_h4['ema20'] = calculate_ema(df_h4, H4_EMA_PERIOD)

    trades = []
    active_long_trades = {}
    active_short = None
    balance = 10000
    peak_balance = 10000
    max_dd = 0
    max_daily_dd = 0

    short_type1_reversal_active = False
    short_type1_reversal_h4_high = None
    short_type2_reversal_active = False
    short_type2_reversal_h4_high = None
    last_h4_index = None

    dates = df.index.date
    unique_dates = sorted(set(dates))

    for date in unique_dates:
        day_start_balance = balance
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
            atr = atrs[i]

            if np.isnan(atr):
                continue

            h4_bars = df_h4[df_h4.index <= current_time]
            if len(h4_bars) < max(SHORT_TYPE1_LOOKBACK_H4_BARS + 2, SHORT_TYPE2_H4_LOOKBACK + 1):
                continue

            current_h4 = h4_bars.iloc[-1]

            # Manage LONG trades
            for session_name in list(active_long_trades.keys()):
                trade = active_long_trades[session_name]
                apply_step_trailing(trade, lows[i], highs[i], is_long=True)

                exit_trade = False
                if lows[i] <= trade['sl']:
                    pnl = (trade['sl'] - trade['entry']) * trade['size']
                    balance += pnl
                    trade['pnl'] = pnl
                    exit_trade = True
                elif highs[i] >= trade['tp']:
                    pnl = (trade['tp'] - trade['entry']) * trade['size']
                    balance += pnl
                    trade['pnl'] = pnl
                    exit_trade = True

                if exit_trade:
                    trade['year'] = current_time.year
                    trades.append(trade)
                    del active_long_trades[session_name]

            # Manage SHORT trade
            if active_short is not None:
                apply_step_trailing(active_short, lows[i], highs[i], is_long=False)

                exit_trade = False
                if highs[i] >= active_short['sl']:
                    pnl = (active_short['entry'] - active_short['sl']) * active_short['size']
                    balance += pnl
                    active_short['pnl'] = pnl
                    exit_trade = True
                elif lows[i] <= active_short['tp']:
                    pnl = (active_short['entry'] - active_short['tp']) * active_short['size']
                    balance += pnl
                    active_short['pnl'] = pnl
                    exit_trade = True

                if exit_trade:
                    active_short['year'] = current_time.year
                    trades.append(active_short)
                    active_short = None
                    short_type1_reversal_active = False
                    short_type2_reversal_active = False

            # Update DD
            if balance > peak_balance:
                peak_balance = balance
            dd = (peak_balance - balance) / peak_balance * 100
            if dd > max_dd:
                max_dd = dd

            # Track session ranges
            for session_name, params in LONG_SESSIONS.items():
                start_hour, end_hour = params['range_hours']
                if start_hour <= hour < end_hour:
                    if session_name not in session_highs:
                        session_highs[session_name] = highs[i]
                        session_lows[session_name] = lows[i]
                    else:
                        session_highs[session_name] = max(session_highs[session_name], highs[i])
                        session_lows[session_name] = min(session_lows[session_name], lows[i])

            # LONG entry logic
            for session_name, params in LONG_SESSIONS.items():
                if session_name in session_highs and session_name not in active_long_trades:
                    entry_start = params['entry_start']
                    entry_end = params['entry_end']

                    if entry_start <= hour < entry_end:
                        session_high = session_highs[session_name]
                        session_low = session_lows[session_name]

                        if closes[i] > session_high:
                            if USE_H4_EMA_FILTER:
                                if pd.isna(current_h4['ema20']):
                                    continue
                                if current_h4['close'] < current_h4['ema20']:
                                    continue

                            entry = closes[i]
                            sl = session_low - ATR_BUFFER * atr
                            risk = entry - sl

                            if risk <= 0:
                                continue

                            tp = entry + risk * TP_RR
                            size = RISK_PER_TRADE / risk

                            active_long_trades[session_name] = {
                                'entry': entry,
                                'sl': sl,
                                'initial_sl': sl,
                                'tp': tp,
                                'size': size,
                                'direction': 'LONG',
                                'session': session_name
                            }

            # SHORT entry logic
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
                                'size': size,
                                'direction': 'SHORT',
                                'session': 'short'
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
                                'size': size,
                                'direction': 'SHORT',
                                'session': 'short'
                            }
                            short_type2_reversal_active = False

        # Daily DD
        if day_start_balance > 0:
            daily_dd = (day_start_balance - balance) / day_start_balance * 100
            if daily_dd > max_daily_dd:
                max_daily_dd = daily_dd

    # Close remaining
    for session_name, trade in active_long_trades.items():
        last_bar = df.iloc[-1]
        pnl = (last_bar['close'] - trade['entry']) * trade['size']
        balance += pnl
        trade['pnl'] = pnl
        trade['year'] = df.index[-1].year
        trades.append(trade)

    if active_short is not None:
        last_bar = df.iloc[-1]
        pnl = (active_short['entry'] - last_bar['close']) * active_short['size']
        balance += pnl
        active_short['pnl'] = pnl
        active_short['year'] = df.index[-1].year
        trades.append(active_short)

    # Results
    trades_df = pd.DataFrame(trades)
    total_pnl = balance - 10000
    win_rate = len(trades_df[trades_df['pnl'] > 0]) / len(trades_df)

    long_df = trades_df[trades_df['direction'] == 'LONG']
    short_df = trades_df[trades_df['direction'] == 'SHORT']

    yearly_pnl = trades_df.groupby('year')['pnl'].sum()
    all_years_profitable = all(yearly_pnl > 0)

    print("="*80)
    print("РЕЗУЛЬТАТЫ ВАЛИДАЦИИ")
    print("="*80)
    print(f"Total Trades: {len(trades_df)}")
    print(f"Win Rate: {win_rate:.1%}")
    print(f"Total PnL: ${total_pnl:,.0f}")
    print(f"Final Balance: ${balance:,.0f}")
    print(f"Max DD: {max_dd:.2f}%")
    print(f"Max Daily DD: {max_daily_dd:.2f}%")
    print()

    print("BREAKDOWN:")
    print(f"  LONG: {len(long_df)} trades, ${long_df['pnl'].sum():,.0f}")
    print(f"  SHORT: {len(short_df)} trades, ${short_df['pnl'].sum():,.0f}")
    print()

    print("PNL BY YEAR:")
    for year in sorted(yearly_pnl.keys()):
        print(f"  {year}: ${yearly_pnl[year]:,.0f}")
    print(f"\nAll years profitable: {'YES' if all_years_profitable else 'NO'}")
    print()

    print("="*80)
    print("ПРОВЕРКА СООТВЕТСТВИЯ ОЖИДАНИЯМ")
    print("="*80)

    expected_pnl = 80501
    expected_trades = 881
    expected_dd = 6.99
    expected_daily_dd = 3.28

    pnl_match = abs(total_pnl - expected_pnl) < 100
    trades_match = len(trades_df) == expected_trades
    dd_match = abs(max_dd - expected_dd) < 0.5
    daily_dd_match = abs(max_daily_dd - expected_daily_dd) < 0.5

    print(f"Total PnL: ${total_pnl:,.0f} (expected ~${expected_pnl:,.0f}) - {'PASS' if pnl_match else 'FAIL'}")
    print(f"Trades: {len(trades_df)} (expected {expected_trades}) - {'PASS' if trades_match else 'FAIL'}")
    print(f"Max DD: {max_dd:.2f}% (expected ~{expected_dd}%) - {'PASS' if dd_match else 'FAIL'}")
    print(f"Daily DD: {max_daily_dd:.2f}% (expected ~{expected_daily_dd}%) - {'PASS' if daily_dd_match else 'FAIL'}")
    print(f"All years profitable: {'PASS' if all_years_profitable else 'FAIL'}")
    print()

    if pnl_match and trades_match and dd_match and daily_dd_match and all_years_profitable:
        print("="*80)
        print("[OK] VALIDATION PASSED - READY FOR DEPLOYMENT")
        print("="*80)
        return True
    else:
        print("="*80)
        print("[FAIL] VALIDATION FAILED - CHECK PARAMETERS")
        print("="*80)
        return False

if __name__ == "__main__":
    success = run_validation()
    sys.exit(0 if success else 1)
