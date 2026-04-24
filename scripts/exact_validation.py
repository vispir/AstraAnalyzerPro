"""
Точная валидация Session Breakout v2.1
Используя параметры из session_breakout_trader.py
"""
import pandas as pd
from pathlib import Path

# Load XAUUSD M15 data (2020-2026)
data_path = Path(__file__).parent.parent / "data_cache" / "dukascopy" / "m15" / "XAUUSD" / "xauusd_m15_2020-01-01_2026-04-18.parquet"
df = pd.read_parquet(data_path)
df = df.sort_index()

print("="*80)
print("SESSION BREAKOUT V2.1 VALIDATION")
print("="*80)
print(f"Data: {len(df)} M15 bars")
print(f"Period: {df.index[0]} - {df.index[-1]}")
print()

# Prepare H4
df_h4 = df.resample('4h').agg({
    'open': 'first',
    'high': 'max',
    'low': 'min',
    'close': 'last'
}).dropna()
df_h4['ema20'] = df_h4['close'].ewm(span=20, adjust=False).mean()
df_h4['atr'] = (df_h4['high'] - df_h4['low']).rolling(20).mean()

# Parameters from session_breakout_trader.py
INITIAL_BALANCE = 10000
RISK_PER_TRADE = 158
TP_RR = 5.5

# Session parameters (from session_breakout_trader.py)
SESSIONS = {
    'asian': {
        'range_hours': (0, 7),
        'breakout_hours': (7, 10),
        'min_range_atr': 0.7,
        'max_range_atr': 3.0
    },
    'london': {
        'range_hours': (7, 12),
        'breakout_hours': (13, 16),
        'min_range_atr': 0.3,
        'max_range_atr': 3.0
    },
    'ny': {
        'range_hours': (13, 17),
        'breakout_hours': (18, 21),
        'min_range_atr': 0.5,
        'max_range_atr': 3.0
    }
}

# SHORT parameters
TYPE1_LOOKBACK_H4_BARS = 5
TYPE2_H4_LOOKBACK = 3
TYPE2_ATR_MULTIPLIER = 2.0

def run_long_backtest():
    """LONG: Session Breakout"""
    trades = []
    balance = INITIAL_BALANCE

    for date in pd.date_range(df.index[0].date(), df.index[-1].date(), freq='D', tz='UTC'):
        day_bars = df[df.index.date == date.date()]
        if len(day_bars) == 0:
            continue

        for session_name, params in SESSIONS.items():
            range_start, range_end = params['range_hours']
            breakout_start, breakout_end = params['breakout_hours']

            range_bars = day_bars[(day_bars.index.hour >= range_start) & (day_bars.index.hour < range_end)]
            breakout_bars = day_bars[(day_bars.index.hour >= breakout_start) & (day_bars.index.hour < breakout_end)]

            if len(range_bars) == 0 or len(breakout_bars) == 0:
                continue

            range_high = range_bars['high'].max()
            range_low = range_bars['low'].min()
            range_size = range_high - range_low

            # ATR filter
            atr_val = df_h4.loc[:date]['atr'].iloc[-1] if len(df_h4.loc[:date]) > 0 else 20
            if range_size < atr_val * params['min_range_atr'] or range_size > atr_val * params['max_range_atr']:
                continue

            for idx, bar in breakout_bars.iterrows():
                if bar['close'] <= range_high:
                    continue

                # H4 EMA20 filter
                h4_bar = df_h4.loc[:idx].iloc[-1] if len(df_h4.loc[:idx]) > 0 else None
                if h4_bar is None or bar['close'] < h4_bar['ema20']:
                    continue

                # Entry
                entry = bar['close']
                sl = range_low
                risk_points = entry - sl
                tp = entry + risk_points * TP_RR

                # Simulate trade with step trailing
                future_bars = df[idx:]
                exit_price = None
                exit_reason = None
                current_sl = sl

                for future_idx, future_bar in future_bars.iterrows():
                    if future_idx == idx:
                        continue

                    profit_r = (future_bar['close'] - entry) / risk_points

                    # Step trailing
                    if profit_r >= 5.0:
                        current_sl = max(current_sl, entry + 4.0 * risk_points)
                    elif profit_r >= 4.0:
                        current_sl = max(current_sl, entry + 3.0 * risk_points)
                    elif profit_r >= 3.0:
                        current_sl = max(current_sl, entry + 2.0 * risk_points)
                    elif profit_r >= 2.0:
                        current_sl = max(current_sl, entry + 1.0 * risk_points)

                    # Check exit
                    if future_bar['low'] <= current_sl:
                        exit_price = current_sl
                        exit_reason = 'sl'
                        break
                    if future_bar['high'] >= tp:
                        exit_price = tp
                        exit_reason = 'tp'
                        break

                if exit_price:
                    pnl = (exit_price - entry) / risk_points * RISK_PER_TRADE
                    balance += pnl
                    trades.append({
                        'date': idx,
                        'strategy': 'LONG',
                        'session': session_name,
                        'pnl': pnl
                    })
                    break

    return pd.DataFrame(trades), balance

def run_short_backtest():
    """SHORT: Reversal"""
    trades = []
    balance = INITIAL_BALANCE

    for date in pd.date_range(df.index[0].date(), df.index[-1].date(), freq='D', tz='UTC'):
        day_bars = df[df.index.date == date.date()]
        if len(day_bars) == 0:
            continue

        for idx, bar in day_bars.iterrows():
            if idx.hour < 0 or idx.hour >= 21:
                continue

            h4_bar = df_h4.loc[:idx].iloc[-1] if len(df_h4.loc[:idx]) > 0 else None
            if h4_bar is None:
                continue

            # H4 EMA20 filter (below for SHORT)
            if bar['close'] > h4_bar['ema20']:
                continue

            signal_type = None

            # Type 1: Historical High
            last_n_h4 = df_h4.loc[:idx].tail(TYPE1_LOOKBACK_H4_BARS)
            if len(last_n_h4) >= TYPE1_LOOKBACK_H4_BARS:
                last_h4_close = last_n_h4['close'].iloc[-1]
                prev_h4_close = last_n_h4['close'].iloc[-2]

                if last_h4_close < prev_h4_close:
                    last_3_m15 = df.loc[:idx].tail(3)
                    m15_low = last_3_m15['low'].min()
                    if bar['close'] < m15_low:
                        signal_type = 'Type1'

            # Type 2: Local Reversal
            if signal_type is None:
                last_n_h4 = df_h4.loc[:idx].tail(TYPE2_H4_LOOKBACK + 1)
                if len(last_n_h4) >= TYPE2_H4_LOOKBACK + 1:
                    atr_val = h4_bar['atr']
                    price_move = last_n_h4['close'].iloc[-1] - last_n_h4['close'].iloc[0]

                    if price_move > TYPE2_ATR_MULTIPLIER * atr_val:
                        last_h4_close = last_n_h4['close'].iloc[-1]
                        prev_h4_close = last_n_h4['close'].iloc[-2]

                        if last_h4_close < prev_h4_close:
                            last_3_m15 = df.loc[:idx].tail(3)
                            m15_low = last_3_m15['low'].min()
                            if bar['close'] < m15_low:
                                signal_type = 'Type2'

            if signal_type is None:
                continue

            # Entry
            entry = bar['close']
            atr_val = h4_bar['atr']
            sl = entry + atr_val
            risk_points = sl - entry
            tp = entry - risk_points * TP_RR

            # Simulate trade with step trailing (inverse)
            future_bars = df[idx:]
            exit_price = None
            current_sl = sl

            for future_idx, future_bar in future_bars.iterrows():
                if future_idx == idx:
                    continue

                profit_r = (entry - future_bar['close']) / risk_points

                # Step trailing (inverse)
                if profit_r >= 5.0:
                    current_sl = min(current_sl, entry - 4.0 * risk_points)
                elif profit_r >= 4.0:
                    current_sl = min(current_sl, entry - 3.0 * risk_points)
                elif profit_r >= 3.0:
                    current_sl = min(current_sl, entry - 2.0 * risk_points)
                elif profit_r >= 2.0:
                    current_sl = min(current_sl, entry - 1.0 * risk_points)

                # Check exit
                if future_bar['high'] >= current_sl:
                    exit_price = current_sl
                    break
                if future_bar['low'] <= tp:
                    exit_price = tp
                    break

            if exit_price:
                pnl = (entry - exit_price) / risk_points * RISK_PER_TRADE
                balance += pnl
                trades.append({
                    'date': idx,
                    'strategy': 'SHORT',
                    'session': signal_type,
                    'pnl': pnl
                })
                break

    return pd.DataFrame(trades), balance

# Run backtests
print("Running LONG backtest...")
long_trades, long_balance = run_long_backtest()
print(f"LONG: {len(long_trades)} trades")

print("Running SHORT backtest...")
short_trades, short_balance = run_short_backtest()
print(f"SHORT: {len(short_trades)} trades")
print()

# Combined
if len(short_trades) > 0:
    combined_trades = pd.concat([long_trades, short_trades]).sort_values('date')
else:
    combined_trades = long_trades.copy()

# Results
print("="*80)
print("RESULTS")
print("="*80)
print()

print("LONG ONLY:")
print(f"  Trades: {len(long_trades)}")
print(f"  Gross PnL: ${long_trades['pnl'].sum():,.2f}")
print(f"  Win Rate: {len(long_trades[long_trades['pnl'] > 0]) / len(long_trades) * 100:.1f}%")
swap_long = len(long_trades) * 2.8 * -5
print(f"  Swap: ${swap_long:,.2f}")
print(f"  Net PnL: ${long_trades['pnl'].sum() + swap_long:,.2f}")
print()

print("SHORT ONLY:")
if len(short_trades) > 0:
    print(f"  Trades: {len(short_trades)}")
    print(f"  Gross PnL: ${short_trades['pnl'].sum():,.2f}")
    print(f"  Win Rate: {len(short_trades[short_trades['pnl'] > 0]) / len(short_trades) * 100:.1f}%")
    swap_short = len(short_trades) * 2.8 * -3
    print(f"  Swap: ${swap_short:,.2f}")
    print(f"  Net PnL: ${short_trades['pnl'].sum() + swap_short:,.2f}")
else:
    print(f"  Trades: 0")
    print(f"  No SHORT trades generated")
    swap_short = 0
print()

print("COMBINED (LONG + SHORT):")
print(f"  Total Trades: {len(combined_trades)}")
print(f"  LONG: {len(long_trades)} ({len(long_trades)/len(combined_trades)*100:.1f}%)")
print(f"  SHORT: {len(short_trades)} ({len(short_trades)/len(combined_trades)*100:.1f}%)")
print(f"  Gross PnL: ${combined_trades['pnl'].sum():,.2f}")
print(f"  Win Rate: {len(combined_trades[combined_trades['pnl'] > 0]) / len(combined_trades) * 100:.1f}%")
total_swap = swap_long + swap_short
print(f"  Swap: ${total_swap:,.2f}")
net_pnl = combined_trades['pnl'].sum() + total_swap
print(f"  Net PnL: ${net_pnl:,.2f}")
print()

# By year
print("BY YEAR (Combined):")
combined_trades['year'] = combined_trades['date'].dt.year
for year in sorted(combined_trades['year'].unique()):
    year_trades = combined_trades[combined_trades['year'] == year]
    year_pnl = year_trades['pnl'].sum()
    year_wr = len(year_trades[year_trades['pnl'] > 0]) / len(year_trades) * 100
    print(f"  {year}: ${year_pnl:,.0f} | {len(year_trades)} trades | WR {year_wr:.1f}%")
