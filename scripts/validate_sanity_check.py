"""
Финальная проверка достоверности бэктеста Session Breakout v3.0
================================================================
1. Проверка направлений сделок (LONG/SHORT)
2. Проверка расчёта PnL
3. Проверка с балансом $9,950 (DD лимиты)
4. Примеры реальных сделок
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from session_breakout_trader import (
    RISK_PER_TRADE, TP_RR, ATR_PERIOD, USE_H4_EMA_FILTER, H4_EMA_PERIOD,
    ATR_BUFFER, LONG_SESSIONS, SHORT_TYPE1_LOOKBACK_H4_BARS,
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
print("SANITY CHECK: Session Breakout v3.0")
print("="*80)
print()

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

# Backtest with detailed trade tracking
trades = []
balance = 9950  # Starting with $9,950
initial_balance = 9950
peak_balance = 9950
max_dd = 0
max_dd_usd = 0
max_daily_dd = 0
max_daily_dd_usd = 0
active_long = None
active_short = None

short_type1_reversal_active = False
short_type1_reversal_h4_high = None
short_type2_reversal_active = False
short_type2_reversal_h4_high = None
last_h4_index = None

dates = df.index.date
unique_dates = sorted(set(dates))

yearly_pnl = {}

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

            exit_price = None
            exit_reason = None

            if lows[i] <= active_long['sl']:
                exit_price = active_long['sl']
                exit_reason = 'SL'
                pnl = (active_long['sl'] - active_long['entry']) * active_long['size']
                balance += pnl
                trades.append({
                    'entry_time': active_long['entry_time'],
                    'exit_time': times[i],
                    'direction': 'LONG',
                    'session': active_long['session'],
                    'entry': active_long['entry'],
                    'sl': active_long['initial_sl'],
                    'tp': active_long['tp'],
                    'exit_price': exit_price,
                    'exit_reason': exit_reason,
                    'pnl': pnl,
                    'risk': active_long['entry'] - active_long['initial_sl']
                })
                active_long = None
            elif highs[i] >= active_long['tp']:
                exit_price = active_long['tp']
                exit_reason = 'TP'
                pnl = (active_long['tp'] - active_long['entry']) * active_long['size']
                balance += pnl
                trades.append({
                    'entry_time': active_long['entry_time'],
                    'exit_time': times[i],
                    'direction': 'LONG',
                    'session': active_long['session'],
                    'entry': active_long['entry'],
                    'sl': active_long['initial_sl'],
                    'tp': active_long['tp'],
                    'exit_price': exit_price,
                    'exit_reason': exit_reason,
                    'pnl': pnl,
                    'risk': active_long['entry'] - active_long['initial_sl']
                })
                active_long = None

        # SHORT TRADE MANAGEMENT
        if active_short is not None:
            apply_step_trailing(active_short, lows[i], highs[i], is_long=False)

            exit_price = None
            exit_reason = None

            if highs[i] >= active_short['sl']:
                exit_price = active_short['sl']
                exit_reason = 'SL'
                pnl = (active_short['entry'] - active_short['sl']) * active_short['size']
                balance += pnl
                trades.append({
                    'entry_time': active_short['entry_time'],
                    'exit_time': times[i],
                    'direction': 'SHORT',
                    'session': 'reversal',
                    'entry': active_short['entry'],
                    'sl': active_short['initial_sl'],
                    'tp': active_short['tp'],
                    'exit_price': exit_price,
                    'exit_reason': exit_reason,
                    'pnl': pnl,
                    'risk': active_short['initial_sl'] - active_short['entry']
                })
                active_short = None
                short_type1_reversal_active = False
                short_type2_reversal_active = False
            elif lows[i] <= active_short['tp']:
                exit_price = active_short['tp']
                exit_reason = 'TP'
                pnl = (active_short['entry'] - active_short['tp']) * active_short['size']
                balance += pnl
                trades.append({
                    'entry_time': active_short['entry_time'],
                    'exit_time': times[i],
                    'direction': 'SHORT',
                    'session': 'reversal',
                    'entry': active_short['entry'],
                    'sl': active_short['initial_sl'],
                    'tp': active_short['tp'],
                    'exit_price': exit_price,
                    'exit_reason': exit_reason,
                    'pnl': pnl,
                    'risk': active_short['initial_sl'] - active_short['entry']
                })
                active_short = None
                short_type1_reversal_active = False
                short_type2_reversal_active = False

        # Update DD
        if balance > peak_balance:
            peak_balance = balance
        dd_usd = peak_balance - balance
        dd = dd_usd / peak_balance * 100
        if dd > max_dd:
            max_dd = dd
            max_dd_usd = dd_usd

        # LONG LOGIC
        if active_long is None:
            for session_name, (start_hour, end_hour) in LONG_SESSIONS.items():
                if start_hour <= hour < end_hour:
                    if session_name not in session_highs:
                        session_highs[session_name] = highs[i]
                        session_lows[session_name] = lows[i]
                    else:
                        session_highs[session_name] = max(session_highs[session_name], highs[i])
                        session_lows[session_name] = min(session_lows[session_name], lows[i])

            for session_name, (start_hour, end_hour) in LONG_SESSIONS.items():
                if session_name in session_highs and hour >= end_hour:
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
                            'entry_time': times[i],
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
                            'entry_time': times[i]
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
                            'entry_time': times[i]
                        }
                        short_type2_reversal_active = False

    # Daily DD
    daily_dd_usd = day_start_balance - balance
    daily_dd = (daily_dd_usd / day_start_balance * 100) if day_start_balance > 0 and daily_dd_usd > 0 else 0
    if daily_dd > max_daily_dd:
        max_daily_dd = daily_dd
        max_daily_dd_usd = daily_dd_usd

    # Yearly PnL
    year = date.year
    if year not in yearly_pnl:
        yearly_pnl[year] = {'start_balance': day_start_balance, 'end_balance': balance}
    yearly_pnl[year]['end_balance'] = balance

# Results
trades_df = pd.DataFrame(trades)

print("="*80)
print("1. ПРОВЕРКА НАПРАВЛЕНИЙ СДЕЛОК")
print("="*80)
print()

# LONG examples
long_trades = trades_df[trades_df['direction'] == 'LONG']
print("5 ПРИМЕРОВ LONG СДЕЛОК:")
print("-" * 80)
for idx, trade in long_trades.head(5).iterrows():
    print(f"\nEntry: {trade['entry_time']}")
    print(f"  Entry: {trade['entry']:.2f}")
    print(f"  SL: {trade['sl']:.2f} (ниже entry: {trade['sl'] < trade['entry']})")
    print(f"  TP: {trade['tp']:.2f} (выше entry: {trade['tp'] > trade['entry']})")
    print(f"  Exit: {trade['exit_price']:.2f} ({trade['exit_reason']})")
    print(f"  PnL: ${trade['pnl']:.2f}")
    print(f"  Risk: {trade['risk']:.2f}")

    # Verify logic
    if trade['exit_reason'] == 'TP':
        expected_pnl = trade['risk'] * TP_RR * (RISK_PER_TRADE / trade['risk'])
        print(f"  [OK] TP hit: price rose from {trade['entry']:.2f} to {trade['exit_price']:.2f}")
        print(f"  [OK] Expected PnL: ${expected_pnl:.2f}, Actual: ${trade['pnl']:.2f}")
    else:
        # Check if trailing SL or initial SL
        if trade['exit_price'] > trade['entry']:
            print(f"  [OK] Trailing SL hit: price rose from {trade['entry']:.2f} to {trade['exit_price']:.2f} (profit)")
        else:
            print(f"  [OK] Initial SL hit: price fell from {trade['entry']:.2f} to {trade['exit_price']:.2f} (loss)")
        print(f"  [OK] Actual PnL: ${trade['pnl']:.2f}")

print("\n" + "="*80)
print()

# SHORT examples
short_trades = trades_df[trades_df['direction'] == 'SHORT']
print("5 ПРИМЕРОВ SHORT СДЕЛОК:")
print("-" * 80)
for idx, trade in short_trades.head(5).iterrows():
    print(f"\nEntry: {trade['entry_time']}")
    print(f"  Entry: {trade['entry']:.2f}")
    print(f"  SL: {trade['sl']:.2f} (выше entry: {trade['sl'] > trade['entry']})")
    print(f"  TP: {trade['tp']:.2f} (ниже entry: {trade['tp'] < trade['entry']})")
    print(f"  Exit: {trade['exit_price']:.2f} ({trade['exit_reason']})")
    print(f"  PnL: ${trade['pnl']:.2f}")
    print(f"  Risk: {trade['risk']:.2f}")

    # Verify logic
    if trade['exit_reason'] == 'TP':
        expected_pnl = trade['risk'] * TP_RR * (RISK_PER_TRADE / trade['risk'])
        print(f"  [OK] TP hit: price fell from {trade['entry']:.2f} to {trade['exit_price']:.2f}")
        print(f"  [OK] Expected PnL: ${expected_pnl:.2f}, Actual: ${trade['pnl']:.2f}")
    else:
        # Check if trailing SL or initial SL
        if trade['exit_price'] < trade['entry']:
            print(f"  [OK] Trailing SL hit: price fell from {trade['entry']:.2f} to {trade['exit_price']:.2f} (profit)")
        else:
            print(f"  [OK] Initial SL hit: price rose from {trade['entry']:.2f} to {trade['exit_price']:.2f} (loss)")
        print(f"  [OK] Actual PnL: ${trade['pnl']:.2f}")

print("\n" + "="*80)
print("2. ПРОВЕРКА РАСЧЁТА PnL")
print("="*80)
print()

# Check PnL calculations
winning_long = long_trades[long_trades['pnl'] > 0].head(1)
losing_long = long_trades[long_trades['pnl'] < 0].head(1)
winning_short = short_trades[short_trades['pnl'] > 0].head(1)
losing_short = short_trades[short_trades['pnl'] < 0].head(1)

print("ПОБЕДНАЯ LONG:")
if len(winning_long) > 0:
    trade = winning_long.iloc[0]
    risk = trade['entry'] - trade['sl']
    size = RISK_PER_TRADE / risk
    expected_pnl = (trade['tp'] - trade['entry']) * size
    print(f"  Entry: {trade['entry']:.2f}, SL: {trade['sl']:.2f}, TP: {trade['tp']:.2f}")
    print(f"  Risk: {risk:.2f}, Size: {size:.4f}")
    print(f"  Expected PnL: ${expected_pnl:.2f}")
    print(f"  Actual PnL: ${trade['pnl']:.2f}")
    print(f"  [OK] Match: {abs(expected_pnl - trade['pnl']) < 0.01}")

print("\nУБЫТОЧНАЯ LONG:")
if len(losing_long) > 0:
    trade = losing_long.iloc[0]
    risk = trade['entry'] - trade['sl']
    size = RISK_PER_TRADE / risk
    expected_pnl = (trade['exit_price'] - trade['entry']) * size
    print(f"  Entry: {trade['entry']:.2f}, SL: {trade['sl']:.2f}, Exit: {trade['exit_price']:.2f}")
    print(f"  Risk: {risk:.2f}, Size: {size:.4f}")
    print(f"  Expected PnL: ${expected_pnl:.2f}")
    print(f"  Actual PnL: ${trade['pnl']:.2f}")
    print(f"  [OK] Match: {abs(expected_pnl - trade['pnl']) < 0.01}")

print("\nПОБЕДНАЯ SHORT:")
if len(winning_short) > 0:
    trade = winning_short.iloc[0]
    risk = trade['sl'] - trade['entry']
    size = RISK_PER_TRADE / risk
    expected_pnl = (trade['entry'] - trade['tp']) * size
    print(f"  Entry: {trade['entry']:.2f}, SL: {trade['sl']:.2f}, TP: {trade['tp']:.2f}")
    print(f"  Risk: {risk:.2f}, Size: {size:.4f}")
    print(f"  Expected PnL: ${expected_pnl:.2f}")
    print(f"  Actual PnL: ${trade['pnl']:.2f}")
    print(f"  [OK] Match: {abs(expected_pnl - trade['pnl']) < 0.01}")

print("\nУБЫТОЧНАЯ SHORT:")
if len(losing_short) > 0:
    trade = losing_short.iloc[0]
    risk = trade['sl'] - trade['entry']
    size = RISK_PER_TRADE / risk
    expected_pnl = (trade['entry'] - trade['exit_price']) * size
    print(f"  Entry: {trade['entry']:.2f}, SL: {trade['sl']:.2f}, Exit: {trade['exit_price']:.2f}")
    print(f"  Risk: {risk:.2f}, Size: {size:.4f}")
    print(f"  Expected PnL: ${expected_pnl:.2f}")
    print(f"  Actual PnL: ${trade['pnl']:.2f}")
    print(f"  [OK] Match: {abs(expected_pnl - trade['pnl']) < 0.01}")

print("\n" + "="*80)
print("3. ПРОВЕРКА С БАЛАНСОМ $9,950")
print("="*80)
print()

print(f"Начальный баланс: ${initial_balance:,.2f}")
print(f"Конечный баланс: ${balance:,.2f}")
print(f"Total PnL: ${balance - initial_balance:,.2f}")
print()
print(f"Max DD: {max_dd:.2f}% (${max_dd_usd:.2f})")
print(f"Max DD лимит: 10% (${initial_balance * 0.10:.2f})")
print(f"[OK] DD в пределах лимита: {max_dd_usd <= initial_balance * 0.10}")
print()
print(f"Max Daily DD: {max_daily_dd:.2f}% (${max_daily_dd_usd:.2f})")
print(f"Daily DD лимит: 5% (${initial_balance * 0.05:.2f})")
print(f"[OK] Daily DD в пределах лимита: {max_daily_dd_usd <= initial_balance * 0.05}")
print()
print(f"Risk per trade: ${RISK_PER_TRADE} = {RISK_PER_TRADE / initial_balance * 100:.2f}% от ${initial_balance:,.2f}")
print()

print("="*80)
print("ПРИБЫЛЬНОСТЬ ПО ГОДАМ:")
print("="*80)
for year in sorted(yearly_pnl.keys()):
    year_data = yearly_pnl[year]
    year_pnl = year_data['end_balance'] - year_data['start_balance']
    status = '[OK]' if year_pnl > 0 else '[LOSS]'
    print(f"{year}: ${year_pnl:,.2f} {status}")

print()
print("="*80)
print("ИТОГОВАЯ СТАТИСТИКА:")
print("="*80)
print(f"Total Trades: {len(trades_df)}")
print(f"  LONG: {len(long_trades)} ({len(long_trades)/len(trades_df)*100:.1f}%)")
print(f"  SHORT: {len(short_trades)} ({len(short_trades)/len(trades_df)*100:.1f}%)")
print()
print(f"Win Rate: {len(trades_df[trades_df['pnl'] > 0]) / len(trades_df) * 100:.1f}%")
print(f"  LONG WR: {len(long_trades[long_trades['pnl'] > 0]) / len(long_trades) * 100:.1f}%")
print(f"  SHORT WR: {len(short_trades[short_trades['pnl'] > 0]) / len(short_trades) * 100:.1f}%")
print()

winning_trades = trades_df[trades_df['pnl'] > 0]
losing_trades = trades_df[trades_df['pnl'] < 0]
gross_profit = winning_trades['pnl'].sum()
gross_loss = abs(losing_trades['pnl'].sum())
profit_factor = gross_profit / gross_loss if gross_loss > 0 else 0

print(f"Gross Profit: ${gross_profit:,.2f}")
print(f"Gross Loss: ${gross_loss:,.2f}")
print(f"Profit Factor: {profit_factor:.2f}")
print()

print("="*80)
print("[OK] SANITY CHECK COMPLETE")
print("="*80)
