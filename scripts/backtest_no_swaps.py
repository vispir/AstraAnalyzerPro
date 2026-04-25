"""
Full Backtest: LONG + SHORT (No Swaps) + DD Analysis
"""
import pandas as pd
import numpy as np
from pathlib import Path

data_path = Path(__file__).parent.parent / "data_cache" / "dukascopy" / "m15" / "XAUUSD" / "xauusd_m15_2020-01-01_2026-04-18.parquet"
df = pd.read_parquet(data_path)
df = df.sort_index()

# H4 data
df_h4 = df.resample('4h').agg({'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last'}).dropna()
df_h4['ema20'] = df_h4['close'].ewm(span=20, adjust=False).mean()
df_h4['atr'] = (df_h4['high'] - df_h4['low']).rolling(20).mean()

RISK = 158
TP_RR = 5.5
INITIAL_BALANCE = 10000

print("="*80)
print("BACKTEST: LONG + SHORT (NO SWAPS)")
print("="*80)
print(f"Period: {df.index[0]} - {df.index[-1]}")
print(f"Initial Balance: ${INITIAL_BALANCE:,.0f}")
print()

# LONG trades
print("Running LONG backtest...")
long_trades = []
SESSIONS = {
    'asian': {'range': (0, 7), 'breakout': (7, 10), 'min_atr': 0.7, 'max_atr': 3.0},
    'london': {'range': (7, 12), 'breakout': (13, 16), 'min_atr': 0.3, 'max_atr': 3.0},
    'ny': {'range': (13, 17), 'breakout': (18, 21), 'min_atr': 0.5, 'max_atr': 3.0}
}

for date in pd.date_range(df.index[0].date(), df.index[-1].date(), freq='D', tz='UTC'):
    day_bars = df[df.index.date == date.date()]
    if len(day_bars) == 0:
        continue
    for sess_name, params in SESSIONS.items():
        r_start, r_end = params['range']
        range_bars = day_bars[(day_bars.index.hour >= r_start) & (day_bars.index.hour < r_end)]
        if len(range_bars) == 0:
            continue
        range_high = range_bars['high'].max()
        range_low = range_bars['low'].min()
        range_size = range_high - range_low
        atr = df_h4.loc[:date]['atr'].iloc[-1] if len(df_h4.loc[:date]) > 0 else 20
        if range_size < atr * params['min_atr'] or range_size > atr * params['max_atr']:
            continue
        b_start, b_end = params['breakout']
        breakout_bars = day_bars[(day_bars.index.hour >= b_start) & (day_bars.index.hour < b_end)]
        for idx, bar in breakout_bars.iterrows():
            if bar['close'] <= range_high:
                continue
            h4 = df_h4.loc[:idx].iloc[-1] if len(df_h4.loc[:idx]) > 0 else None
            if h4 is None or bar['close'] < h4['ema20']:
                continue
            entry = bar['close']
            sl = range_low
            risk_points = entry - sl
            tp = entry + risk_points * TP_RR
            future = df[idx:]
            current_sl = sl
            for _, fb in future.iterrows():
                if _ == idx:
                    continue
                profit_r = (fb['close'] - entry) / risk_points
                if profit_r >= 5.0:
                    current_sl = max(current_sl, entry + 4.0 * risk_points)
                elif profit_r >= 4.0:
                    current_sl = max(current_sl, entry + 3.0 * risk_points)
                elif profit_r >= 3.0:
                    current_sl = max(current_sl, entry + 2.0 * risk_points)
                elif profit_r >= 2.0:
                    current_sl = max(current_sl, entry + 1.0 * risk_points)
                if fb['low'] <= current_sl:
                    pnl = (current_sl - entry) / risk_points * RISK
                    long_trades.append({'date': idx, 'exit_date': _, 'pnl': pnl, 'type': 'LONG'})
                    break
                if fb['high'] >= tp:
                    pnl = (tp - entry) / risk_points * RISK
                    long_trades.append({'date': idx, 'exit_date': _, 'pnl': pnl, 'type': 'LONG'})
                    break
            break

print(f"LONG: {len(long_trades)} trades")

# SHORT trades
print("Running SHORT backtest...")
short_trades = []
SHORT_TYPE1_LOOKBACK = 5
SHORT_TYPE2_LOOKBACK = 3
SHORT_TYPE2_ATR_MULT = 2.0

for date in pd.date_range(df.index[0].date(), df.index[-1].date(), freq='D', tz='UTC'):
    day_bars = df[df.index.date == date.date()]
    if len(day_bars) == 0:
        continue
    for idx, bar in day_bars.iterrows():
        if idx.hour < 0 or idx.hour >= 21:
            continue
        h4 = df_h4.loc[:idx].iloc[-1] if len(df_h4.loc[:idx]) > 0 else None
        if h4 is None or bar['close'] > h4['ema20']:
            continue
        signal = None
        last_h4 = df_h4.loc[:idx].tail(SHORT_TYPE1_LOOKBACK)
        if len(last_h4) >= SHORT_TYPE1_LOOKBACK:
            if last_h4['close'].iloc[-1] < last_h4['close'].iloc[-2]:
                m15_low = df.loc[:idx].tail(3)['low'].min()
                if bar['close'] < m15_low:
                    signal = 'Type1'
        if signal is None:
            last_h4 = df_h4.loc[:idx].tail(SHORT_TYPE2_LOOKBACK + 1)
            if len(last_h4) >= SHORT_TYPE2_LOOKBACK + 1:
                move = last_h4['close'].iloc[-1] - last_h4['close'].iloc[0]
                if move > SHORT_TYPE2_ATR_MULT * h4['atr']:
                    if last_h4['close'].iloc[-1] < last_h4['close'].iloc[-2]:
                        m15_low = df.loc[:idx].tail(3)['low'].min()
                        if bar['close'] < m15_low:
                            signal = 'Type2'
        if signal is None:
            continue
        entry = bar['close']
        sl = entry + h4['atr']
        risk_points = sl - entry
        tp = entry - risk_points * TP_RR
        future = df[idx:]
        current_sl = sl
        for _, fb in future.iterrows():
            if _ == idx:
                continue
            profit_r = (entry - fb['close']) / risk_points
            if profit_r >= 5.0:
                current_sl = min(current_sl, entry - 4.0 * risk_points)
            elif profit_r >= 4.0:
                current_sl = min(current_sl, entry - 3.0 * risk_points)
            elif profit_r >= 3.0:
                current_sl = min(current_sl, entry - 2.0 * risk_points)
            elif profit_r >= 2.0:
                current_sl = min(current_sl, entry - 1.0 * risk_points)
            if fb['high'] >= current_sl:
                pnl = (entry - current_sl) / risk_points * RISK
                short_trades.append({'date': idx, 'exit_date': _, 'pnl': pnl, 'type': 'SHORT'})
                break
            if fb['low'] <= tp:
                pnl = (entry - tp) / risk_points * RISK
                short_trades.append({'date': idx, 'exit_date': _, 'pnl': pnl, 'type': 'SHORT'})
                break
        break

print(f"SHORT: {len(short_trades)} trades")
print()

# Combine trades
all_trades = long_trades + short_trades
trades_df = pd.DataFrame(all_trades).sort_values('date')

# Calculate equity curve
trades_df['cumulative_pnl'] = trades_df['pnl'].cumsum()
trades_df['balance'] = INITIAL_BALANCE + trades_df['cumulative_pnl']

# Calculate drawdown
trades_df['peak'] = trades_df['balance'].cummax()
trades_df['drawdown'] = trades_df['balance'] - trades_df['peak']
trades_df['drawdown_pct'] = (trades_df['drawdown'] / trades_df['peak']) * 100

# Daily drawdown
trades_df['trade_date'] = trades_df['date'].dt.date
daily_pnl = trades_df.groupby('trade_date')['pnl'].sum()
daily_dd = daily_pnl[daily_pnl < 0]

# Stats
max_dd = trades_df['drawdown_pct'].min()
max_dd_idx = trades_df['drawdown_pct'].idxmin()
max_dd_date = trades_df.loc[max_dd_idx, 'date']
max_dd_balance = trades_df.loc[max_dd_idx, 'balance']

worst_day = daily_dd.min() if len(daily_dd) > 0 else 0
worst_day_date = daily_dd.idxmin() if len(daily_dd) > 0 else None
worst_day_pct = (worst_day / INITIAL_BALANCE) * 100 if worst_day != 0 else 0

long_df = trades_df[trades_df['type'] == 'LONG']
short_df = trades_df[trades_df['type'] == 'SHORT']

print("="*80)
print("RESULTS (NO SWAPS)")
print("="*80)
print()
print(f"Total Trades: {len(trades_df)}")
print(f"  LONG: {len(long_df)} ({len(long_df)/len(trades_df)*100:.1f}%)")
print(f"  SHORT: {len(short_df)} ({len(short_df)/len(trades_df)*100:.1f}%)")
print()
print(f"Gross PnL: ${trades_df['pnl'].sum():,.2f}")
print(f"Win Rate: {len(trades_df[trades_df['pnl'] > 0]) / len(trades_df) * 100:.1f}%")
print(f"Avg Win: ${trades_df[trades_df['pnl'] > 0]['pnl'].mean():.2f}")
print(f"Avg Loss: ${trades_df[trades_df['pnl'] < 0]['pnl'].mean():.2f}")
print()
print(f"Initial Balance: ${INITIAL_BALANCE:,.0f}")
print(f"Final Balance: ${trades_df['balance'].iloc[-1]:,.2f}")
print(f"Total Return: {(trades_df['balance'].iloc[-1] - INITIAL_BALANCE) / INITIAL_BALANCE * 100:.2f}%")
print()

print("="*80)
print("DRAWDOWN ANALYSIS")
print("="*80)
print()
print(f"Max Drawdown: {max_dd:.2f}%")
print(f"  Date: {max_dd_date.strftime('%Y-%m-%d')}")
print(f"  Balance at DD: ${max_dd_balance:,.2f}")
print()
print(f"Worst Single Day: ${worst_day:.2f}")
print(f"  Date: {worst_day_date}")
print(f"  Percentage: {worst_day_pct:.2f}%")
print()

print("="*80)
print("RISK ASSESSMENT")
print("="*80)
if abs(max_dd) > 10:
    print(f"WARNING: Max DD {max_dd:.2f}% EXCEEDS 10% limit")
else:
    print(f"OK: Max DD {max_dd:.2f}% is within 10% limit")

if abs(worst_day_pct) > 5:
    print(f"WARNING: Worst day {worst_day_pct:.2f}% EXCEEDS 5% limit")
else:
    print(f"OK: Worst day {worst_day_pct:.2f}% is within 5% limit")
print("="*80)

# Breakdown by strategy
print()
print("="*80)
print("BREAKDOWN BY STRATEGY")
print("="*80)
print()
print(f"LONG:")
print(f"  Trades: {len(long_df)}")
print(f"  Gross PnL: ${long_df['pnl'].sum():,.2f}")
print(f"  Win Rate: {len(long_df[long_df['pnl'] > 0]) / len(long_df) * 100:.1f}%")
print()
print(f"SHORT:")
print(f"  Trades: {len(short_df)}")
if len(short_df) > 0:
    print(f"  Gross PnL: ${short_df['pnl'].sum():,.2f}")
    print(f"  Win Rate: {len(short_df[short_df['pnl'] > 0]) / len(short_df) * 100:.1f}%")
else:
    print(f"  Gross PnL: $0.00")
    print(f"  Win Rate: N/A (no trades)")
print("="*80)
