"""
Quick validation: Session Breakout LONG strategy
Проверка что это та стратегия которая дала +$41k
"""
import pandas as pd
from pathlib import Path

# Load XAUUSD M15 data
data_path = Path(__file__).parent.parent / "data_cache" / "dukascopy" / "m15" / "XAUUSD" / "xauusd_m15_2020-01-01_2026-04-18.parquet"
if not data_path.exists():
    print(f"ERROR: {data_path} not found")
    exit(1)

df = pd.read_parquet(data_path)
df = df.sort_index()

print("="*80)
print("SESSION BREAKOUT VALIDATION")
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
df_h4['atr'] = (df_h4['high'] - df_h4['low']).rolling(14).mean()

# Parameters
INITIAL_BALANCE = 10000
RISK_PER_TRADE = 158
TP_RR = 5.5

trades = []
balance = INITIAL_BALANCE

for date in pd.date_range(df.index[0].date(), df.index[-1].date(), freq='D', tz='UTC'):
    day_bars = df[df.index.date == date.date()]
    if len(day_bars) == 0:
        continue

    # LONG: Session Breakout
    for session_name, range_start, range_end, breakout_start, breakout_end in [
        ('asian', 0, 7, 7, 10),
        ('london', 7, 13, 13, 16),
        ('ny', 13, 18, 18, 21)
    ]:
        range_bars = day_bars[(day_bars.index.hour >= range_start) & (day_bars.index.hour < range_end)]
        breakout_bars = day_bars[(day_bars.index.hour >= breakout_start) & (day_bars.index.hour < breakout_end)]

        if len(range_bars) == 0 or len(breakout_bars) == 0:
            continue

        range_high = range_bars['high'].max()
        range_low = range_bars['low'].min()
        range_size = range_high - range_low

        # Range size filter
        atr_val = df_h4.loc[:date]['atr'].iloc[-1] if len(df_h4.loc[:date]) > 0 else 20
        if range_size < atr_val * 0.3 or range_size > atr_val * 3.0:
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

            # Simulate trade
            future_bars = df[idx:]
            exit_price = None
            exit_reason = None

            # Step trailing
            current_sl = sl
            for future_idx, future_bar in future_bars.iterrows():
                if future_idx == idx:
                    continue

                profit_r = (future_bar['close'] - entry) / risk_points

                # Update trailing
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
                    'session': session_name,
                    'entry': entry,
                    'exit': exit_price,
                    'pnl': pnl
                })
                break

# Results
trades_df = pd.DataFrame(trades)
print(f"Total LONG trades: {len(trades_df)}")
print(f"Gross PnL: ${trades_df['pnl'].sum():,.2f}")
print(f"Win Rate: {len(trades_df[trades_df['pnl'] > 0]) / len(trades_df) * 100:.1f}%")
print()

# Swap estimate
avg_hold_days = 2.8
total_swap = len(trades_df) * avg_hold_days * -5
net_pnl = trades_df['pnl'].sum() + total_swap

print(f"Swap impact: ${total_swap:,.2f}")
print(f"Net PnL: ${net_pnl:,.2f}")
print(f"Final Balance: ${INITIAL_BALANCE + net_pnl:,.2f}")
print()

# Validation
print("="*80)
if net_pnl > 40000:
    print("CONFIRMED: This is the strategy that gave +$40k+")
    print("Parameters match: Risk=$158, TP=5.5R, Step Trailing, H4 EMA20 filter")
else:
    print(f"WARNING: Net PnL ${net_pnl:,.0f} doesn't match expected +$40k+")
