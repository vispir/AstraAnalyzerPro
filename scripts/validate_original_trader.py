"""
Валидация оригинального session_breakout_trader.py из коммита 865b8e1
Ожидаемый результат: 606 сделок, $57,274 net, DD 6.84%
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Импортируем функции из оригинального файла
import importlib.util
spec = importlib.util.spec_from_file_location("original", "session_breakout_trader_original.py")
original = importlib.util.module_from_spec(spec)
spec.loader.exec_module(original)

import pandas as pd
import numpy as np
from pathlib import Path

# Load data
data_path = Path(__file__).parent.parent / "data_cache" / "dukascopy" / "m15" / "XAUUSD" / "xauusd_m15_2020-01-01_2026-04-18.parquet"
df = pd.read_parquet(data_path)
df = df.sort_index()

print("="*80)
print("ВАЛИДАЦИЯ ОРИГИНАЛЬНОГО session_breakout_trader.py")
print("="*80)
print(f"Period: {df.index[0]} - {df.index[-1]}")
print(f"Total M15 bars: {len(df)}")
print()

# Calculate indicators
df['atr'] = original.calculate_atr(df, original.ATR_PERIOD)

# H4 data
df_h4 = df.resample('4h').agg({'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last'}).dropna()
df_h4['atr'] = original.calculate_atr(df_h4, original.ATR_PERIOD)
df_h4['ema20'] = original.calculate_ema(df_h4, original.H4_EMA_PERIOD)

print(f"H4 bars: {len(df_h4)}")
print()

# Backtest
trades = []
balance = 10000
peak_balance = 10000
max_dd = 0
max_daily_dd = 0
active_long = None
active_short = None

# SHORT state machine
short_type1_reversal_active = False
short_type1_reversal_h4_high = None
short_type2_reversal_active = False
short_type2_reversal_h4_high = None
last_h4_index = None

# LONG session tracking
session_highs = {}
session_lows = {}
last_trading_date = None

dates = df.index.date
unique_dates = sorted(set(dates))

for date in unique_dates:
    day_start_balance = balance
    day_data = df[df.index.date == date]

    if len(day_data) < 10:
        continue

    # Reset session tracking
    if last_trading_date != date:
        session_highs = {}
        session_lows = {}
        last_trading_date = date

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
        if len(h4_bars) < max(original.SHORT_TYPE1_LOOKBACK_H4_BARS + 2, original.SHORT_TYPE2_H4_LOOKBACK + 1):
            continue

        current_h4 = h4_bars.iloc[-1]
        atr = atrs[i]

        if np.isnan(atr):
            continue

        # LONG TRADE MANAGEMENT
        if active_long is not None:
            profit_r = (closes[i] - active_long['entry']) / (active_long['entry'] - active_long['initial_sl'])
            if profit_r >= 5.0:
                new_sl = active_long['entry'] + 4.0 * (active_long['entry'] - active_long['initial_sl'])
                active_long['sl'] = max(active_long['sl'], new_sl)
            elif profit_r >= 4.0:
                new_sl = active_long['entry'] + 3.0 * (active_long['entry'] - active_long['initial_sl'])
                active_long['sl'] = max(active_long['sl'], new_sl)
            elif profit_r >= 3.0:
                new_sl = active_long['entry'] + 2.0 * (active_long['entry'] - active_long['initial_sl'])
                active_long['sl'] = max(active_long['sl'], new_sl)
            elif profit_r >= 2.0:
                new_sl = active_long['entry'] + 1.0 * (active_long['entry'] - active_long['initial_sl'])
                active_long['sl'] = max(active_long['sl'], new_sl)

            if lows[i] <= active_long['sl']:
                pnl = (active_long['sl'] - active_long['entry']) / (active_long['entry'] - active_long['initial_sl']) * original.RISK_PER_TRADE
                balance += pnl
                trades.append({'date': active_long['entry_time'], 'exit_date': times[i], 'pnl': pnl, 'direction': 'LONG'})
                active_long = None
            elif highs[i] >= active_long['tp']:
                pnl = (active_long['tp'] - active_long['entry']) / (active_long['entry'] - active_long['initial_sl']) * original.RISK_PER_TRADE
                balance += pnl
                trades.append({'date': active_long['entry_time'], 'exit_date': times[i], 'pnl': pnl, 'direction': 'LONG'})
                active_long = None

        # SHORT TRADE MANAGEMENT
        if active_short is not None:
            profit_r = (active_short['entry'] - closes[i]) / (active_short['initial_sl'] - active_short['entry'])
            if profit_r >= 5.0:
                new_sl = active_short['entry'] - 4.0 * (active_short['initial_sl'] - active_short['entry'])
                active_short['sl'] = min(active_short['sl'], new_sl)
            elif profit_r >= 4.0:
                new_sl = active_short['entry'] - 3.0 * (active_short['initial_sl'] - active_short['entry'])
                active_short['sl'] = min(active_short['sl'], new_sl)
            elif profit_r >= 3.0:
                new_sl = active_short['entry'] - 2.0 * (active_short['initial_sl'] - active_short['entry'])
                active_short['sl'] = min(active_short['sl'], new_sl)
            elif profit_r >= 2.0:
                new_sl = active_short['entry'] - 1.0 * (active_short['initial_sl'] - active_short['entry'])
                active_short['sl'] = min(active_short['sl'], new_sl)

            if highs[i] >= active_short['sl']:
                pnl = (active_short['entry'] - active_short['sl']) / (active_short['initial_sl'] - active_short['entry']) * original.RISK_PER_TRADE
                balance += pnl
                trades.append({'date': active_short['entry_time'], 'exit_date': times[i], 'pnl': pnl, 'direction': 'SHORT'})
                active_short = None
                short_type1_reversal_active = False
                short_type2_reversal_active = False
            elif lows[i] <= active_short['tp']:
                pnl = (active_short['entry'] - active_short['tp']) / (active_short['initial_sl'] - active_short['entry']) * original.RISK_PER_TRADE
                balance += pnl
                trades.append({'date': active_short['entry_time'], 'exit_date': times[i], 'pnl': pnl, 'direction': 'SHORT'})
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
            for session_name, (start_hour, end_hour) in original.LONG_SESSIONS.items():
                if start_hour <= hour < end_hour:
                    if session_name not in session_highs:
                        session_highs[session_name] = highs[i]
                        session_lows[session_name] = lows[i]
                    else:
                        session_highs[session_name] = max(session_highs[session_name], highs[i])
                        session_lows[session_name] = min(session_lows[session_name], lows[i])

            for session_name, (start_hour, end_hour) in original.LONG_SESSIONS.items():
                if session_name in session_highs and hour >= end_hour:
                    session_high = session_highs[session_name]
                    session_low = session_lows[session_name]

                    if closes[i] > session_high:
                        if original.USE_H4_EMA_FILTER:
                            if pd.isna(current_h4['ema20']):
                                continue
                            if current_h4['close'] < current_h4['ema20']:
                                continue

                        entry = closes[i]
                        sl = session_low - original.ATR_BUFFER * atr
                        risk = entry - sl

                        if risk <= 0:
                            continue

                        tp = entry + risk * original.TP_RR

                        active_long = {
                            'entry': entry,
                            'sl': sl,
                            'initial_sl': sl,
                            'tp': tp,
                            'entry_time': times[i]
                        }

                        del session_highs[session_name]
                        del session_lows[session_name]
                        break

        # SHORT LOGIC
        if active_short is None and hour < 21:
            prev_h4 = h4_bars.iloc[-2]

            current_h4_index = current_h4.name
            if last_h4_index != current_h4_index:
                last_h4_index = current_h4_index

                if original.USE_H4_EMA_FILTER:
                    if pd.isna(current_h4['ema20']):
                        short_type1_reversal_active = False
                        short_type2_reversal_active = False
                        continue

                    if current_h4['close'] >= current_h4['ema20']:
                        short_type1_reversal_active = False
                        short_type2_reversal_active = False
                        continue

                if not short_type1_reversal_active:
                    lookback_highs = h4_bars.iloc[-original.SHORT_TYPE1_LOOKBACK_H4_BARS-1:-1]['high']
                    historical_high = lookback_highs.max()

                    if current_h4['high'] > historical_high:
                        if current_h4['close'] < prev_h4['close']:
                            short_type1_reversal_active = True
                            short_type1_reversal_h4_high = current_h4['high']

                if not short_type2_reversal_active:
                    if len(h4_bars) >= original.SHORT_TYPE2_H4_LOOKBACK + 1:
                        lookback_bars = h4_bars.iloc[-original.SHORT_TYPE2_H4_LOOKBACK-1:-1]
                        price_change = current_h4['high'] - lookback_bars['low'].min()
                        h4_atr = current_h4.get('atr', atr)

                        if not np.isnan(h4_atr) and price_change >= original.SHORT_TYPE2_ATR_MULTIPLIER * h4_atr:
                            if current_h4['close'] < prev_h4['close']:
                                short_type2_reversal_active = True
                                short_type2_reversal_h4_high = current_h4['high']

            if i > 0:
                prev_m15_low = lows[i-1]

                if short_type1_reversal_active and closes[i] < prev_m15_low:
                    entry = closes[i]
                    sl = short_type1_reversal_h4_high + original.ATR_BUFFER * atr
                    risk = sl - entry

                    if risk > 0:
                        tp = entry - risk * original.TP_RR

                        active_short = {
                            'entry': entry,
                            'sl': sl,
                            'initial_sl': sl,
                            'tp': tp,
                            'entry_time': times[i]
                        }
                        short_type1_reversal_active = False

                elif short_type2_reversal_active and closes[i] < prev_m15_low:
                    entry = closes[i]
                    sl = short_type2_reversal_h4_high + original.ATR_BUFFER * atr
                    risk = sl - entry

                    if risk > 0:
                        tp = entry - risk * original.TP_RR

                        active_short = {
                            'entry': entry,
                            'sl': sl,
                            'initial_sl': sl,
                            'tp': tp,
                            'entry_time': times[i]
                        }
                        short_type2_reversal_active = False

    # Daily DD
    daily_dd = (day_start_balance - balance) / day_start_balance * 100 if day_start_balance > 0 else 0
    if daily_dd > max_daily_dd:
        max_daily_dd = daily_dd

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
print("RESULTS")
print("="*80)
print()
print(f"Total Trades: {len(trades_df)}")
print(f"  LONG: {len(long_df)} ({len(long_df)/len(trades_df)*100:.1f}%)")
print(f"  SHORT: {len(short_df)} ({len(short_df)/len(trades_df)*100:.1f}%)")
print()
print(f"Total PnL: ${trades_df['pnl'].sum():,.2f}")
print(f"  LONG PnL: ${long_df['pnl'].sum():,.2f}")
print(f"  SHORT PnL: ${short_df['pnl'].sum():,.2f}")
print()
print(f"Win Rate: {len(winning_trades) / len(trades_df) * 100:.1f}%")
print(f"  LONG WR: {len(long_df[long_df['pnl'] > 0]) / len(long_df) * 100:.1f}%")
print(f"  SHORT WR: {len(short_df[short_df['pnl'] > 0]) / len(short_df) * 100:.1f}%")
print()
print(f"Max DD: {max_dd:.2f}%")
print(f"Max Daily DD: {max_daily_dd:.2f}%")
print(f"Profit Factor: {profit_factor:.2f}")
print(f"Return: {(balance - 10000) / 10000 * 100:.1f}%")
print()

print("="*80)
print("СРАВНЕНИЕ С ЭТАЛОНОМ")
print("="*80)
print(f"Trades: {len(trades_df)} vs 606 эталон ({len(trades_df) - 606:+d} разница)")
print(f"PnL: ${trades_df['pnl'].sum():,.0f} vs $57,274 эталон")
print(f"DD: {max_dd:.2f}% vs 6.84% эталон")
print()

if abs(len(trades_df) - 606) <= 10 and abs(trades_df['pnl'].sum() - 57274) < 1000:
    print("="*80)
    print(">>> ВАЛИДАЦИЯ УСПЕШНА: ФАЙЛ СОВПАДАЕТ С ЭТАЛОНОМ <<<")
    print("="*80)
    print("Можно заменить текущий session_breakout_trader.py этим файлом")
else:
    print("="*80)
    print("[!] ВНИМАНИЕ: Расхождение с эталоном")
    print("="*80)
print()
print("="*80)
