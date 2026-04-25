"""
Бэктест Session Breakout v3.0 только для 2026 года
Начальный баланс: $9,950
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

# Filter only 2026
df = df[df.index.year == 2026]

print("="*80)
print("Session Breakout v3.0 - ТОЛЬКО 2026 ГОД")
print("="*80)
print(f"Period: {df.index[0]} - {df.index[-1]}")
print(f"Начальный баланс: $9,950")
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

# H4 data - need full history for EMA20
df_full = pd.read_parquet(data_path)
df_full = df_full.sort_index()
df_h4 = df_full.resample('4h').agg({'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last'}).dropna()
df_h4['atr'] = calculate_atr(df_h4, ATR_PERIOD)
df_h4['ema20'] = calculate_ema(df_h4, H4_EMA_PERIOD)

# Backtest
trades = []
balance = 9950
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

monthly_pnl = {}

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

            if lows[i] <= active_long['sl']:
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
                    'exit_price': active_long['sl'],
                    'pnl': pnl
                })
                active_long = None
            elif highs[i] >= active_long['tp']:
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
                    'exit_price': active_long['tp'],
                    'pnl': pnl
                })
                active_long = None

        # SHORT TRADE MANAGEMENT
        if active_short is not None:
            apply_step_trailing(active_short, lows[i], highs[i], is_long=False)

            if highs[i] >= active_short['sl']:
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
                    'exit_price': active_short['sl'],
                    'pnl': pnl
                })
                active_short = None
                short_type1_reversal_active = False
                short_type2_reversal_active = False
            elif lows[i] <= active_short['tp']:
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
                    'exit_price': active_short['tp'],
                    'pnl': pnl
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

    # Monthly PnL
    month = date.month
    if month not in monthly_pnl:
        monthly_pnl[month] = {'start_balance': day_start_balance, 'end_balance': balance}
    monthly_pnl[month]['end_balance'] = balance

# Results
trades_df = pd.DataFrame(trades)
long_df = trades_df[trades_df['direction'] == 'LONG']
short_df = trades_df[trades_df['direction'] == 'SHORT']

winning_trades = trades_df[trades_df['pnl'] > 0]
losing_trades = trades_df[trades_df['pnl'] < 0]
gross_profit = winning_trades['pnl'].sum()
gross_loss = abs(losing_trades['pnl'].sum())
profit_factor = gross_profit / gross_loss if gross_loss > 0 else 0

print("="*80)
print("РЕЗУЛЬТАТЫ 2026 ГОДА")
print("="*80)
print()
print(f"Начальный баланс: ${initial_balance:,.2f}")
print(f"Конечный баланс: ${balance:,.2f}")
print(f"Total PnL: ${balance - initial_balance:,.2f}")
print(f"Return: {(balance - initial_balance) / initial_balance * 100:.1f}%")
print()
print(f"Total Trades: {len(trades_df)}")
print(f"  LONG: {len(long_df)} ({len(long_df)/len(trades_df)*100:.1f}%)")
print(f"  SHORT: {len(short_df)} ({len(short_df)/len(trades_df)*100:.1f}%)")
print()
print(f"Win Rate: {len(winning_trades) / len(trades_df) * 100:.1f}%")
print(f"  LONG WR: {len(long_df[long_df['pnl'] > 0]) / len(long_df) * 100:.1f}%")
print(f"  SHORT WR: {len(short_df[short_df['pnl'] > 0]) / len(short_df) * 100:.1f}%")
print()
print(f"Max DD: {max_dd:.2f}% (${max_dd_usd:.2f})")
print(f"Max Daily DD: {max_daily_dd:.2f}% (${max_daily_dd_usd:.2f})")
print(f"Profit Factor: {profit_factor:.2f}")
print()
print(f"Gross Profit: ${gross_profit:,.2f}")
print(f"Gross Loss: ${gross_loss:,.2f}")
print()

print("="*80)
print("ПО МЕСЯЦАМ 2026:")
print("="*80)
month_names = {1: 'Jan', 2: 'Feb', 3: 'Mar', 4: 'Apr'}
for month in sorted(monthly_pnl.keys()):
    month_data = monthly_pnl[month]
    month_pnl = month_data['end_balance'] - month_data['start_balance']
    month_name = month_names.get(month, str(month))
    print(f"{month_name}: ${month_pnl:,.2f}")

print()
print("="*80)
print("BY SESSION (LONG)")
print("="*80)
for session in ['asian', 'london']:
    session_trades = long_df[long_df['session'] == session]
    if len(session_trades) > 0:
        session_wins = session_trades[session_trades['pnl'] > 0]
        print(f"{session.upper()}: {len(session_trades)} trades, ${session_trades['pnl'].sum():,.0f} PnL, WR {len(session_wins)/len(session_trades)*100:.1f}%")
print()
