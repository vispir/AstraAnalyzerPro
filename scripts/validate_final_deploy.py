"""
Финальная валидация session_breakout_trader.py
Ожидаемый результат: ~606 trades, ~$64k PnL, DD ~6.13%
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
import numpy as np
from pathlib import Path

# Load data
data_path = Path(__file__).parent.parent / "data_cache" / "dukascopy" / "m15" / "XAUUSD" / "xauusd_m15_2020-01-01_2026-04-18.parquet"
df = pd.read_parquet(data_path)
df = df.sort_index()

print("="*80)
print("ФИНАЛЬНАЯ ВАЛИДАЦИЯ session_breakout_trader.py")
print("="*80)
print(f"Period: {df.index[0]} - {df.index[-1]}")
print()

# Parameters (from combined_strategy_backtest.py)
RISK_PER_TRADE = 158
TP_RR = 5.5
ATR_PERIOD = 14
ATR_BUFFER = 0.5
USE_H4_EMA_FILTER = True
H4_EMA_PERIOD = 20

# LONG: Simple session windows
LONG_SESSIONS = {
    'asian': (7, 10),
    'london': (13, 16),
    'ny': (18, 21)
}

# SHORT parameters
SHORT_TYPE1_LOOKBACK_H4_BARS = 5
SHORT_TYPE1_H4_REVERSAL_BARS = 1
SHORT_TYPE2_H4_LOOKBACK = 3
SHORT_TYPE2_ATR_MULTIPLIER = 2.0

# Calculate indicators
def calculate_atr(df, period):
    high = df['high']
    low = df['low']
    close = df['close']
    tr1 = high - low
    tr2 = abs(high - close.shift(1))
    tr3 = abs(low - close.shift(1))
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    return tr.rolling(window=period).mean()

def apply_step_trailing(active_trade, current_low, current_high, is_long=True):
    """Apply step trailing stop logic"""
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
    else:  # SHORT
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
df_h4['ema20'] = df_h4['close'].ewm(span=H4_EMA_PERIOD, adjust=False).mean()

print(f"M15 bars: {len(df)}")
print(f"H4 bars: {len(df_h4)}")
print()

# Backtest (COPIED FROM combined_strategy_backtest.py)
trades = []
active_long = None
active_short = None
balance = 10000
peak_balance = 10000
max_dd = 0
max_daily_dd = 0

# SHORT reversal tracking
short_type1_reversal_active = False
short_type1_reversal_h4_high = None
short_type2_reversal_active = False
short_type2_reversal_h4_high = None

dates = df.index.date
unique_dates = sorted(set(dates))

for date_idx, date in enumerate(unique_dates):
    day_start_balance = balance
    day_data = df[df.index.date == date]

    if len(day_data) < 10:
        continue

    # Convert to numpy for performance
    highs = day_data['high'].to_numpy()
    lows = day_data['low'].to_numpy()
    closes = day_data['close'].to_numpy()
    opens = day_data['open'].to_numpy()
    atrs = day_data['atr'].to_numpy()
    hours = np.array([t.hour for t in day_data.index])
    times = day_data.index.to_numpy()

    # Track session highs/lows for LONG strategy
    session_highs = {}
    session_lows = {}

    for i in range(len(day_data)):
        current_time = times[i]
        hour = hours[i]

        # Get current H4 bar
        h4_bars = df_h4[df_h4.index <= current_time]
        if len(h4_bars) < max(SHORT_TYPE1_LOOKBACK_H4_BARS + SHORT_TYPE1_H4_REVERSAL_BARS + 1, SHORT_TYPE2_H4_LOOKBACK + 1):
            continue

        current_h4 = h4_bars.iloc[-1]

        # ================================================================
        # LONG TRADE MANAGEMENT
        # ================================================================
        if active_long is not None:
            apply_step_trailing(active_long, lows[i], highs[i], is_long=True)

            # Check SL/TP for LONG
            if lows[i] <= active_long['sl']:
                # LONG SL hit = price went DOWN
                pnl = (active_long['sl'] - active_long['entry']) * active_long['size']
                balance += pnl
                trades.append({
                    'entry_time': active_long['entry_time'],
                    'exit_time': times[i],
                    'entry_price': active_long['entry'],
                    'exit_price': active_long['sl'],
                    'sl': active_long['initial_sl'],
                    'tp': active_long['tp'],
                    'pnl': pnl,
                    'exit_reason': 'sl',
                    'direction': 'LONG',
                    'session': active_long['session']
                })
                active_long = None
            elif highs[i] >= active_long['tp']:
                # LONG TP hit = price went UP
                pnl = (active_long['tp'] - active_long['entry']) * active_long['size']
                balance += pnl
                trades.append({
                    'entry_time': active_long['entry_time'],
                    'exit_time': times[i],
                    'entry_price': active_long['entry'],
                    'exit_price': active_long['tp'],
                    'sl': active_long['initial_sl'],
                    'tp': active_long['tp'],
                    'pnl': pnl,
                    'exit_reason': 'tp',
                    'direction': 'LONG',
                    'session': active_long['session']
                })
                active_long = None

        # ================================================================
        # SHORT TRADE MANAGEMENT
        # ================================================================
        if active_short is not None:
            apply_step_trailing(active_short, lows[i], highs[i], is_long=False)

            # Check SL/TP for SHORT
            if highs[i] >= active_short['sl']:
                # SHORT SL hit = price went UP
                pnl = (active_short['entry'] - active_short['sl']) * active_short['size']
                balance += pnl
                trades.append({
                    'entry_time': active_short['entry_time'],
                    'exit_time': times[i],
                    'entry_price': active_short['entry'],
                    'exit_price': active_short['sl'],
                    'sl': active_short['initial_sl'],
                    'tp': active_short['tp'],
                    'pnl': pnl,
                    'exit_reason': 'sl',
                    'direction': 'SHORT'
                })
                active_short = None
                short_type1_reversal_active = False
                short_type2_reversal_active = False
            elif lows[i] <= active_short['tp']:
                # SHORT TP hit = price went DOWN
                pnl = (active_short['entry'] - active_short['tp']) * active_short['size']
                balance += pnl
                trades.append({
                    'entry_time': active_short['entry_time'],
                    'exit_time': times[i],
                    'entry_price': active_short['entry'],
                    'exit_price': active_short['tp'],
                    'sl': active_short['initial_sl'],
                    'tp': active_short['tp'],
                    'pnl': pnl,
                    'exit_reason': 'tp',
                    'direction': 'SHORT'
                })
                active_short = None
                short_type1_reversal_active = False
                short_type2_reversal_active = False

        # Update max DD
        if balance > peak_balance:
            peak_balance = balance
        dd = (peak_balance - balance) / peak_balance * 100
        if dd > max_dd:
            max_dd = dd

        atr = atrs[i]
        if np.isnan(atr):
            continue

        # ================================================================
        # LONG: SESSION BREAKOUT LOGIC
        # ================================================================
        if active_long is None:
            # Track session ranges
            for session_name, (start_hour, end_hour) in LONG_SESSIONS.items():
                if start_hour <= hour < end_hour:
                    if session_name not in session_highs:
                        session_highs[session_name] = highs[i]
                        session_lows[session_name] = lows[i]
                    else:
                        session_highs[session_name] = max(session_highs[session_name], highs[i])
                        session_lows[session_name] = min(session_lows[session_name], lows[i])

            # Check for breakout
            for session_name, (start_hour, end_hour) in LONG_SESSIONS.items():
                if session_name in session_highs and hour >= end_hour:
                    session_high = session_highs[session_name]

                    # Breakout above session high
                    if closes[i] > session_high:
                        # Check H4 EMA20 filter: LONG only if price ABOVE EMA20
                        if USE_H4_EMA_FILTER:
                            if pd.isna(current_h4['ema20']):
                                continue
                            if current_h4['close'] < current_h4['ema20']:
                                continue  # Skip LONG if below EMA20

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

        # ================================================================
        # SHORT: REVERSAL LOGIC
        # ================================================================
        if active_short is None:
            prev_h4 = h4_bars.iloc[-2]

            # Check H4 EMA20 filter: SHORT only if price BELOW EMA20
            if USE_H4_EMA_FILTER:
                if pd.isna(current_h4['ema20']):
                    continue
                if current_h4['close'] >= current_h4['ema20']:
                    # Reset reversal flags if price is above EMA20
                    short_type1_reversal_active = False
                    short_type2_reversal_active = False
                    continue

            # TYPE 1: Reversal After Historical High
            if not short_type1_reversal_active:
                lookback_highs = h4_bars.iloc[-SHORT_TYPE1_LOOKBACK_H4_BARS-1:-1]['high']
                historical_high = lookback_highs.max()

                if current_h4['high'] > historical_high:
                    if current_h4['close'] < prev_h4['close']:
                        short_type1_reversal_active = True
                        short_type1_reversal_h4_high = current_h4['high']

            # TYPE 2: Local Reversal After Strong Move
            if not short_type2_reversal_active:
                if len(h4_bars) >= SHORT_TYPE2_H4_LOOKBACK + 1:
                    lookback_bars = h4_bars.iloc[-SHORT_TYPE2_H4_LOOKBACK-1:-1]
                    price_change = current_h4['high'] - lookback_bars['low'].min()

                    h4_atr = current_h4.get('atr', atr)

                    if not np.isnan(h4_atr) and price_change >= SHORT_TYPE2_ATR_MULTIPLIER * h4_atr:
                        if current_h4['close'] < prev_h4['close']:
                            short_type2_reversal_active = True
                            short_type2_reversal_h4_high = current_h4['high']

            # M15 ENTRY LOGIC
            if i > 0:
                prev_m15_low = lows[i-1]

                # Type 1 entry (priority)
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

                # Type 2 entry (if Type 1 didn't trigger)
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

    # Calculate daily DD
    if day_start_balance > 0:
        daily_dd = (day_start_balance - balance) / day_start_balance * 100
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
print("BY SESSION (LONG)")
print("="*80)
for session in ['asian', 'london', 'ny']:
    session_trades = long_df[long_df['session'] == session]
    if len(session_trades) > 0:
        session_wins = session_trades[session_trades['pnl'] > 0]
        print(f"{session.upper()}: {len(session_trades)} trades, ${session_trades['pnl'].sum():,.0f} PnL, WR {len(session_wins)/len(session_trades)*100:.1f}%")
print()

# By year
print("="*80)
print("BY YEAR")
print("="*80)
trades_df['year'] = pd.to_datetime(trades_df['entry_time']).dt.year
for year in sorted(trades_df['year'].unique()):
    year_trades = trades_df[trades_df['year'] == year]
    year_long = year_trades[year_trades['direction'] == 'LONG']
    year_short = year_trades[year_trades['direction'] == 'SHORT']
    year_wins = year_trades[year_trades['pnl'] > 0]
    print(f"{year}: {len(year_trades)} trades (LONG: {len(year_long)}, SHORT: {len(year_short)}), ${year_trades['pnl'].sum():,.0f} PnL, WR {len(year_wins)/len(year_trades)*100:.1f}%")
print()

print("="*80)
print("VALIDATION")
print("="*80)
print(f"Expected: ~606 trades, ~$64k PnL, DD ~6.13%")
print(f"Actual: {len(trades_df)} trades, ${trades_df['pnl'].sum():,.0f} PnL, DD {max_dd:.2f}%")
print()

if 590 <= len(trades_df) <= 620 and 60000 <= trades_df['pnl'].sum() <= 68000 and max_dd < 7.0:
    print("="*80)
    print(">>> ВАЛИДАЦИЯ УСПЕШНА: ГОТОВ К ДЕПЛОЮ <<<")
    print("="*80)
else:
    print("="*80)
    print("[!] ВНИМАНИЕ: Требуется проверка")
    print("="*80)
print()
