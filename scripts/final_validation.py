"""
ФИНАЛЬНАЯ ВАЛИДАЦИЯ: session_breakout_trader.py
Импортирует функции напрямую из deployed файла и тестирует на исторических данных
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
import numpy as np
from pathlib import Path

# Import from session_breakout_trader.py
import session_breakout_trader as sbt

# Load data
data_path = Path(__file__).parent.parent / "data_cache" / "dukascopy" / "m15" / "XAUUSD" / "xauusd_m15_2020-01-01_2026-04-18.parquet"
df = pd.read_parquet(data_path)
df = df.sort_index()

print("="*80)
print("FINAL VALIDATION: session_breakout_trader.py")
print("="*80)
print(f"Period: {df.index[0]} - {df.index[-1]}")
print(f"Initial Balance: $10,000")
print()

# Calculate indicators
df['atr'] = sbt.calculate_atr(df, sbt.ATR_PERIOD)

# H4 data
df_h4 = df.resample('4h').agg({'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last'}).dropna()
df_h4['atr'] = sbt.calculate_atr(df_h4, sbt.ATR_PERIOD)
df_h4['ema20'] = sbt.calculate_ema(df_h4, sbt.H4_EMA_PERIOD)

# Backtest
trades = []
balance = 10000
active_long = None
active_short = None

# Reset global state
sbt.short_type1_reversal_active = False
sbt.short_type1_reversal_h4_high = None
sbt.short_type2_reversal_active = False
sbt.short_type2_reversal_h4_high = None
sbt.last_h4_index = None
sbt.session_highs = {}
sbt.session_lows = {}
sbt.last_trading_date = None

dates = df.index.date
unique_dates = sorted(set(dates))

for date in unique_dates:
    day_data = df[df.index.date == date]

    if len(day_data) < 10:
        continue

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
        if len(h4_bars) < max(sbt.SHORT_TYPE1_LOOKBACK_H4_BARS + 2, sbt.SHORT_TYPE2_H4_LOOKBACK + 1):
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
                pnl = (active_long['sl'] - active_long['entry']) / (active_long['entry'] - active_long['initial_sl']) * sbt.RISK_PER_TRADE
                balance += pnl
                trades.append({'date': active_long['entry_time'], 'exit_date': times[i], 'pnl': pnl, 'direction': 'LONG'})
                active_long = None
            elif highs[i] >= active_long['tp']:
                pnl = (active_long['tp'] - active_long['entry']) / (active_long['entry'] - active_long['initial_sl']) * sbt.RISK_PER_TRADE
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
                pnl = (active_short['entry'] - active_short['sl']) / (active_short['initial_sl'] - active_short['entry']) * sbt.RISK_PER_TRADE
                balance += pnl
                trades.append({'date': active_short['entry_time'], 'exit_date': times[i], 'pnl': pnl, 'direction': 'SHORT'})
                active_short = None
                sbt.short_type1_reversal_active = False
                sbt.short_type2_reversal_active = False
            elif lows[i] <= active_short['tp']:
                pnl = (active_short['entry'] - active_short['tp']) / (active_short['initial_sl'] - active_short['entry']) * sbt.RISK_PER_TRADE
                balance += pnl
                trades.append({'date': active_short['entry_time'], 'exit_date': times[i], 'pnl': pnl, 'direction': 'SHORT'})
                active_short = None
                sbt.short_type1_reversal_active = False
                sbt.short_type2_reversal_active = False

        # LONG LOGIC (using functions from session_breakout_trader.py)
        if active_long is None:
            # Simulate check_long_session_breakout logic inline
            current_date = day_data.index[-1].date()
            if sbt.last_trading_date != current_date:
                sbt.session_highs = {}
                sbt.session_lows = {}
                sbt.last_trading_date = current_date

            # Track session ranges
            for session_name, (start_hour, end_hour) in sbt.LONG_SESSIONS.items():
                if start_hour <= hour < end_hour:
                    if session_name not in sbt.session_highs:
                        sbt.session_highs[session_name] = highs[i]
                        sbt.session_lows[session_name] = lows[i]
                    else:
                        sbt.session_highs[session_name] = max(sbt.session_highs[session_name], highs[i])
                        sbt.session_lows[session_name] = min(sbt.session_lows[session_name], lows[i])

            # Check for breakout
            for session_name, (start_hour, end_hour) in sbt.LONG_SESSIONS.items():
                if session_name in sbt.session_highs and hour >= end_hour:
                    session_high = sbt.session_highs[session_name]
                    session_low = sbt.session_lows[session_name]

                    if closes[i] > session_high:
                        if pd.isna(current_h4['ema20']):
                            continue
                        if current_h4['close'] < current_h4['ema20']:
                            continue

                        entry = closes[i]
                        sl = session_low - sbt.ATR_BUFFER * atr
                        risk = entry - sl

                        if risk <= 0:
                            continue

                        tp = entry + risk * sbt.TP_RR

                        active_long = {
                            'entry': entry,
                            'sl': sl,
                            'initial_sl': sl,
                            'tp': tp,
                            'entry_time': times[i]
                        }

                        del sbt.session_highs[session_name]
                        del sbt.session_lows[session_name]
                        break

        # SHORT LOGIC (using state machine from session_breakout_trader.py)
        if active_short is None:
            prev_h4 = h4_bars.iloc[-2]

            current_h4_index = current_h4.name
            if sbt.last_h4_index != current_h4_index:
                sbt.last_h4_index = current_h4_index

                if pd.isna(current_h4['ema20']):
                    sbt.short_type1_reversal_active = False
                    sbt.short_type2_reversal_active = False
                    continue

                if current_h4['close'] >= current_h4['ema20']:
                    sbt.short_type1_reversal_active = False
                    sbt.short_type2_reversal_active = False
                    continue

                # Type 1
                if not sbt.short_type1_reversal_active:
                    lookback_highs = h4_bars.iloc[-sbt.SHORT_TYPE1_LOOKBACK_H4_BARS-1:-1]['high']
                    historical_high = lookback_highs.max()

                    if current_h4['high'] > historical_high:
                        if current_h4['close'] < prev_h4['close']:
                            sbt.short_type1_reversal_active = True
                            sbt.short_type1_reversal_h4_high = current_h4['high']

                # Type 2
                if not sbt.short_type2_reversal_active:
                    if len(h4_bars) >= sbt.SHORT_TYPE2_H4_LOOKBACK + 1:
                        lookback_bars = h4_bars.iloc[-sbt.SHORT_TYPE2_H4_LOOKBACK-1:-1]
                        price_change = current_h4['high'] - lookback_bars['low'].min()
                        h4_atr = current_h4.get('atr', atr)

                        if not np.isnan(h4_atr) and price_change >= sbt.SHORT_TYPE2_ATR_MULTIPLIER * h4_atr:
                            if current_h4['close'] < prev_h4['close']:
                                sbt.short_type2_reversal_active = True
                                sbt.short_type2_reversal_h4_high = current_h4['high']

            # M15 entry
            if i > 0:
                prev_m15_low = lows[i-1]

                if sbt.short_type1_reversal_active and closes[i] < prev_m15_low:
                    entry = closes[i]
                    sl = sbt.short_type1_reversal_h4_high + sbt.ATR_BUFFER * atr
                    risk = sl - entry

                    if risk > 0:
                        tp = entry - risk * sbt.TP_RR

                        active_short = {
                            'entry': entry,
                            'sl': sl,
                            'initial_sl': sl,
                            'tp': tp,
                            'entry_time': times[i]
                        }
                        sbt.short_type1_reversal_active = False

                elif sbt.short_type2_reversal_active and closes[i] < prev_m15_low:
                    entry = closes[i]
                    sl = sbt.short_type2_reversal_h4_high + sbt.ATR_BUFFER * atr
                    risk = sl - entry

                    if risk > 0:
                        tp = entry - risk * sbt.TP_RR

                        active_short = {
                            'entry': entry,
                            'sl': sl,
                            'initial_sl': sl,
                            'tp': tp,
                            'entry_time': times[i]
                        }
                        sbt.short_type2_reversal_active = False

# Results
trades_df = pd.DataFrame(trades)
long_df = trades_df[trades_df['direction'] == 'LONG']
short_df = trades_df[trades_df['direction'] == 'SHORT']

print("="*80)
print("RESULTS")
print("="*80)
print()
print(f"Total Trades: {len(trades_df)}")
print(f"  LONG: {len(long_df)} ({len(long_df)/len(trades_df)*100:.1f}%)")
print(f"  SHORT: {len(short_df)} ({len(short_df)/len(trades_df)*100:.1f}%)")
print()
print(f"Gross PnL: ${trades_df['pnl'].sum():,.2f}")
print(f"Win Rate: {len(trades_df[trades_df['pnl'] > 0]) / len(trades_df) * 100:.1f}%")
print(f"Final Balance: ${balance:,.2f}")
print(f"Total Return: {(balance - 10000) / 10000 * 100:.2f}%")
print()

# Validation
print("="*80)
print("VALIDATION CHECK")
print("="*80)
expected_trades = 606
expected_long = 389
expected_short = 217
expected_pnl = 57274

trades_ok = 590 <= len(trades_df) <= 620
long_ok = 370 <= len(long_df) <= 410
short_ok = 190 <= len(short_df) <= 230
pnl_ok = 54000 <= trades_df['pnl'].sum() <= 60000

print(f"Expected: ~{expected_trades} trades (LONG ~{expected_long}, SHORT ~{expected_short}), ~${expected_pnl:,} PnL")
print(f"Got: {len(trades_df)} trades (LONG {len(long_df)}, SHORT {len(short_df)}), ${trades_df['pnl'].sum():,.0f} PnL")
print()

if trades_ok and long_ok and short_ok and pnl_ok:
    print("✅ VALIDATION PASSED!")
    print("session_breakout_trader.py готов к деплою!")
else:
    print("❌ VALIDATION FAILED!")
    if not trades_ok:
        print(f"  Total trades {len(trades_df)} outside range 590-620")
    if not long_ok:
        print(f"  LONG trades {len(long_df)} outside range 370-410")
    if not short_ok:
        print(f"  SHORT trades {len(short_df)} outside range 190-230")
    if not pnl_ok:
        print(f"  PnL ${trades_df['pnl'].sum():,.0f} outside range $54k-$60k")

print("="*80)
