"""
ФИНАЛЬНАЯ ПРОВЕРКА: Что работает на Render СЕЙЧАС
Логика из session_breakout_trader.py (deploy branch)
"""
import pandas as pd
from pathlib import Path

# Load data
data_path = Path(__file__).parent.parent / "data_cache" / "dukascopy" / "m15" / "XAUUSD" / "xauusd_m15_2020-01-01_2026-04-18.parquet"
df = pd.read_parquet(data_path)
df = df.sort_index()

print("="*80)
print("ТЕКУЩАЯ СИСТЕМА НА RENDER - БЭКТЕСТ")
print("="*80)
print(f"Период: {df.index[0]} - {df.index[-1]}")
print()

# H4 data
df_h4 = df.resample('4h').agg({
    'open': 'first',
    'high': 'max',
    'low': 'min',
    'close': 'last'
}).dropna()
df_h4['ema20'] = df_h4['close'].ewm(span=20, adjust=False).mean()
df_h4['atr'] = (df_h4['high'] - df_h4['low']).rolling(20).mean()

# Parameters (from session_breakout_trader.py)
RISK = 158
TP_RR = 5.5

# LONG: Session parameters (from session_breakout_trader.py)
SESSIONS = {
    'asian': {'range': (0, 7), 'breakout': (7, 10), 'min_atr': 0.7, 'max_atr': 3.0},
    'london': {'range': (7, 12), 'breakout': (13, 16), 'min_atr': 0.3, 'max_atr': 3.0},
    'ny': {'range': (13, 17), 'breakout': (18, 21), 'min_atr': 0.5, 'max_atr': 3.0}
}

# SHORT: Reversal parameters
SHORT_TYPE1_LOOKBACK = 5
SHORT_TYPE2_LOOKBACK = 3
SHORT_TYPE2_ATR_MULT = 2.0

def run_long():
    trades = []
    for date in pd.date_range(df.index[0].date(), df.index[-1].date(), freq='D', tz='UTC'):
        day_bars = df[df.index.date == date.date()]
        if len(day_bars) == 0:
            continue

        for sess_name, params in SESSIONS.items():
            # Get range data
            r_start, r_end = params['range']
            range_bars = day_bars[(day_bars.index.hour >= r_start) & (day_bars.index.hour < r_end)]

            if len(range_bars) == 0:
                continue

            range_high = range_bars['high'].max()
            range_low = range_bars['low'].min()
            range_size = range_high - range_low

            # ATR filter
            atr = df_h4.loc[:date]['atr'].iloc[-1] if len(df_h4.loc[:date]) > 0 else 20
            if range_size < atr * params['min_atr'] or range_size > atr * params['max_atr']:
                continue

            # Check breakout window
            b_start, b_end = params['breakout']
            breakout_bars = day_bars[(day_bars.index.hour >= b_start) & (day_bars.index.hour < b_end)]

            for idx, bar in breakout_bars.iterrows():
                if bar['close'] <= range_high:
                    continue

                # H4 EMA20 filter
                h4 = df_h4.loc[:idx].iloc[-1] if len(df_h4.loc[:idx]) > 0 else None
                if h4 is None or bar['close'] < h4['ema20']:
                    continue

                # Entry
                entry = bar['close']
                sl = range_low
                risk_points = entry - sl
                tp = entry + risk_points * TP_RR

                # Simulate with step trailing
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
                        trades.append({'date': idx, 'pnl': pnl})
                        break
                    if fb['high'] >= tp:
                        pnl = (tp - entry) / risk_points * RISK
                        trades.append({'date': idx, 'pnl': pnl})
                        break
                break

    return pd.DataFrame(trades)

def run_short():
    trades = []
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

            # Type 1
            last_h4 = df_h4.loc[:idx].tail(SHORT_TYPE1_LOOKBACK)
            if len(last_h4) >= SHORT_TYPE1_LOOKBACK:
                if last_h4['close'].iloc[-1] < last_h4['close'].iloc[-2]:
                    m15_low = df.loc[:idx].tail(3)['low'].min()
                    if bar['close'] < m15_low:
                        signal = 'Type1'

            # Type 2
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

            # Entry
            entry = bar['close']
            sl = entry + h4['atr']
            risk_points = sl - entry
            tp = entry - risk_points * TP_RR

            # Simulate with step trailing (inverse)
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
                    trades.append({'date': idx, 'pnl': pnl})
                    break
                if fb['low'] <= tp:
                    pnl = (entry - tp) / risk_points * RISK
                    trades.append({'date': idx, 'pnl': pnl})
                    break
            break

    return pd.DataFrame(trades)

print("Запуск LONG бэктеста...")
long_df = run_long()
print(f"LONG: {len(long_df)} trades")

print("Запуск SHORT бэктеста...")
short_df = run_short()
print(f"SHORT: {len(short_df)} trades")
print()

# Results
print("="*80)
print("РЕЗУЛЬТАТЫ")
print("="*80)
print()

if len(long_df) > 0:
    long_pnl = long_df['pnl'].sum()
    long_wr = len(long_df[long_df['pnl'] > 0]) / len(long_df) * 100
    long_swap = len(long_df) * 2.8 * -5
    print(f"LONG:")
    print(f"  Trades: {len(long_df)}")
    print(f"  Gross PnL: ${long_pnl:,.2f}")
    print(f"  Win Rate: {long_wr:.1f}%")
    print(f"  Swap: ${long_swap:,.2f}")
    print(f"  Net PnL: ${long_pnl + long_swap:,.2f}")
else:
    long_pnl = 0
    long_swap = 0
print()

if len(short_df) > 0:
    short_pnl = short_df['pnl'].sum()
    short_wr = len(short_df[short_df['pnl'] > 0]) / len(short_df) * 100
    short_swap = len(short_df) * 2.8 * -3
    print(f"SHORT:")
    print(f"  Trades: {len(short_df)}")
    print(f"  Gross PnL: ${short_pnl:,.2f}")
    print(f"  Win Rate: {short_wr:.1f}%")
    print(f"  Swap: ${short_swap:,.2f}")
    print(f"  Net PnL: ${short_pnl + short_swap:,.2f}")
else:
    short_pnl = 0
    short_swap = 0
print()

# Combined
total_trades = len(long_df) + len(short_df)
total_gross = long_pnl + short_pnl
total_swap = long_swap + short_swap
total_net = total_gross + total_swap

print("="*80)
print("COMBINED (LONG + SHORT):")
print(f"  Total Trades: {total_trades}")
print(f"  LONG: {len(long_df)} | SHORT: {len(short_df)}")
print(f"  Gross PnL: ${total_gross:,.2f}")
print(f"  Swap: ${total_swap:,.2f}")
print(f"  Net PnL: ${total_net:,.2f}")
print("="*80)
