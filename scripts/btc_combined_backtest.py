"""
BTCUSD Combined Strategy Backtest (LONG + SHORT)
Session Breakout + Reversal на M15 данных 2020-2026
"""
import sys
import pandas as pd
import numpy as np
from pathlib import Path
from datetime import datetime, timezone

print("="*80)
print("BTCUSD COMBINED STRATEGY BACKTEST (2020-2026)")
print("="*80)
print()

# ============================================================================
# 1. ЗАГРУЗКА ДАННЫХ
# ============================================================================
print("1. LOADING DATA")
print("-"*80)

# Load M1 data and resample to M15
m1_path = Path(__file__).parent.parent / "data_cache" / "dukascopy" / "m1" / "BTCUSD" / "btcusd_m1_2020-01-01_2024-12-31.parquet"
if not m1_path.exists():
    print(f"ERROR: File not found: {m1_path}")
    sys.exit(1)

print("Loading M1 data...")
df_m1 = pd.read_parquet(m1_path)
print(f"Loaded {len(df_m1)} M1 candles")

print("Resampling M1 to M15...")
df = df_m1.resample('15min').agg({
    'open': 'first',
    'high': 'max',
    'low': 'min',
    'close': 'last'
}).dropna()

df = df.sort_index()
print(f"Resampled to {len(df)} M15 candles")
print(f"Period: {df.index[0]} - {df.index[-1]}")
print()

# ============================================================================
# 2. PREPARE H4 DATA
# ============================================================================
print("2. PREPARING H4 DATA")
print("-"*80)

df_h4 = df.resample('4h').agg({
    'open': 'first',
    'high': 'max',
    'low': 'min',
    'close': 'last'
}).dropna()

df_h4['ema20'] = df_h4['close'].ewm(span=20, adjust=False).mean()
df_h4['atr'] = (df_h4['high'] - df_h4['low']).rolling(14).mean()

print(f"H4 candles: {len(df_h4)}")
print()

# ============================================================================
# 3. BACKTEST PARAMETERS
# ============================================================================
INITIAL_BALANCE = 10000
RISK_PER_TRADE = 158
TP_RR = 5.5
H4_EMA_PERIOD = 20

# SHORT parameters
TYPE1_LOOKBACK_H4_BARS = 5
TYPE1_H4_REVERSAL_BARS = 1
TYPE2_H4_LOOKBACK = 3
TYPE2_ATR_MULTIPLIER = 2.0

print("3. PARAMETERS")
print("-"*80)
print(f"Initial Balance: ${INITIAL_BALANCE}")
print(f"Risk per trade: ${RISK_PER_TRADE}")
print(f"TP R:R: {TP_RR}")
print(f"H4 EMA Period: {H4_EMA_PERIOD}")
print()
print("LONG: Session Breakout (Asian 07-10, London 13-16, NY 18-21 UTC)")
print("SHORT: Reversal Type1 (5 H4 lookback) + Type2 (3 H4 lookback, 2.0 ATR)")
print()

# ============================================================================
# 4. RUN BACKTEST
# ============================================================================
print("="*80)
print("4. RUNNING BACKTEST")
print("="*80)

trades = []
balance = INITIAL_BALANCE
peak_balance = INITIAL_BALANCE

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
        atr_val = df_h4.loc[:date]['atr'].iloc[-1] if len(df_h4.loc[:date]) > 0 else 1000
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
                peak_balance = max(peak_balance, balance)

                trades.append({
                    'date': idx,
                    'strategy': 'LONG',
                    'session': session_name,
                    'entry': entry,
                    'sl': sl,
                    'tp': tp,
                    'exit': exit_price,
                    'reason': exit_reason,
                    'pnl': pnl,
                    'balance': balance
                })
                break

    # SHORT: Reversal
    if len(day_bars) == 0:
        continue

    h4_bars_today = df_h4[df_h4.index.date == date.date()]
    if len(h4_bars_today) < 2:
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
            h4_high_max = last_n_h4['high'].max()
            last_h4_close = last_n_h4['close'].iloc[-1]
            prev_h4_close = last_n_h4['close'].iloc[-2] if len(last_n_h4) >= 2 else last_h4_close

            if last_h4_close < prev_h4_close:
                last_3_m15 = df.loc[:idx].tail(3)
                m15_low = last_3_m15['low'].min()
                if bar['close'] < m15_low:
                    signal_type = 'Type1_HistoricalHigh'

        # Type 2: Local Reversal After Strong Move
        if signal_type is None:
            last_n_h4 = df_h4.loc[:idx].tail(TYPE2_H4_LOOKBACK + 1)
            if len(last_n_h4) >= TYPE2_H4_LOOKBACK + 1:
                atr_val = h4_bar['atr'] if 'atr' in df_h4.columns else 1000
                price_move = last_n_h4['close'].iloc[-1] - last_n_h4['close'].iloc[0]

                if price_move > TYPE2_ATR_MULTIPLIER * atr_val:
                    last_h4_close = last_n_h4['close'].iloc[-1]
                    prev_h4_close = last_n_h4['close'].iloc[-2]

                    if last_h4_close < prev_h4_close:
                        last_3_m15 = df.loc[:idx].tail(3)
                        m15_low = last_3_m15['low'].min()
                        if bar['close'] < m15_low:
                            signal_type = 'Type2_LocalReversal'

        if signal_type is None:
            continue

        # Entry
        entry = bar['close']
        atr_val = h4_bar['atr'] if 'atr' in df_h4.columns else 1000
        sl = entry + atr_val
        risk_points = sl - entry
        tp = entry - risk_points * TP_RR

        # Simulate trade
        future_bars = df[idx:]
        exit_price = None
        exit_reason = None

        # Step trailing (inverse)
        current_sl = sl
        for future_idx, future_bar in future_bars.iterrows():
            if future_idx == idx:
                continue

            profit_r = (entry - future_bar['close']) / risk_points

            # Update trailing (inverse)
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
                exit_reason = 'sl'
                break
            if future_bar['low'] <= tp:
                exit_price = tp
                exit_reason = 'tp'
                break

        if exit_price:
            pnl = (entry - exit_price) / risk_points * RISK_PER_TRADE
            balance += pnl
            peak_balance = max(peak_balance, balance)

            trades.append({
                'date': idx,
                'strategy': 'SHORT',
                'session': signal_type,
                'entry': entry,
                'sl': sl,
                'tp': tp,
                'exit': exit_price,
                'reason': exit_reason,
                'pnl': pnl,
                'balance': balance
            })
            break

# ============================================================================
# 5. RESULTS
# ============================================================================
print()
print("="*80)
print("5. RESULTS")
print("="*80)

trades_df = pd.DataFrame(trades)

if len(trades_df) == 0:
    print("ERROR: No trades!")
    sys.exit(1)

print(f"Total trades: {len(trades_df)}")
print(f"  LONG: {len(trades_df[trades_df['strategy'] == 'LONG'])}")
print(f"  SHORT: {len(trades_df[trades_df['strategy'] == 'SHORT'])}")
print()

wins = trades_df[trades_df['pnl'] > 0]
losses = trades_df[trades_df['pnl'] < 0]

print("OVERALL:")
print(f"  Initial Balance: ${INITIAL_BALANCE:,.2f}")
print(f"  Final Balance: ${balance:,.2f}")
print(f"  Gross PnL: ${trades_df['pnl'].sum():,.2f}")
print(f"  Win Rate: {len(wins)/len(trades_df)*100:.1f}%")
print(f"  Wins: {len(wins)} | Losses: {len(losses)}")
print()

# Drawdown
trades_df['peak'] = trades_df['balance'].cummax()
trades_df['dd'] = (trades_df['balance'] - trades_df['peak']) / INITIAL_BALANCE * 100
max_dd = trades_df['dd'].min()

print(f"  Max Drawdown: {abs(max_dd):.2f}%")
print(f"  Peak Balance: ${peak_balance:,.2f}")
print()

# Daily DD
trades_df['date_only'] = trades_df['date'].dt.date
daily_pnl = trades_df.groupby('date_only')['pnl'].sum()
daily_dd = daily_pnl.min()
daily_dd_pct = abs(daily_dd) / INITIAL_BALANCE * 100

print(f"  Daily Max Loss: ${daily_dd:.2f} ({daily_dd_pct:.2f}%)")
print()

# By strategy
print("BY STRATEGY:")
print("-"*80)
for strategy in ['LONG', 'SHORT']:
    strat_trades = trades_df[trades_df['strategy'] == strategy]
    if len(strat_trades) == 0:
        continue
    strat_wins = strat_trades[strat_trades['pnl'] > 0]
    strat_pnl = strat_trades['pnl'].sum()
    strat_wr = len(strat_wins) / len(strat_trades) * 100
    print(f"{strategy}: {len(strat_trades)} trades | PnL: ${strat_pnl:,.2f} | WR: {strat_wr:.1f}%")

print()

# By year
print("BY YEAR:")
print("-"*80)
trades_df['year'] = trades_df['date'].dt.year
for year in sorted(trades_df['year'].unique()):
    year_trades = trades_df[trades_df['year'] == year]
    year_wins = year_trades[year_trades['pnl'] > 0]
    year_pnl = year_trades['pnl'].sum()
    year_wr = len(year_wins) / len(year_trades) * 100
    print(f"{year}: PnL ${year_pnl:,.0f} | Trades: {len(year_trades)} | WR: {year_wr:.1f}%")

print()

# Swap estimate
avg_hold_days = 2.8
total_swap = len(trades_df[trades_df['strategy'] == 'LONG']) * avg_hold_days * -5
total_swap += len(trades_df[trades_df['strategy'] == 'SHORT']) * avg_hold_days * -3

net_pnl = trades_df['pnl'].sum() + total_swap
final_balance = INITIAL_BALANCE + net_pnl

print("WITH SWAP:")
print(f"  Swap impact: ${total_swap:,.2f}")
print(f"  Net PnL: ${net_pnl:,.2f}")
print(f"  Final Balance: ${final_balance:,.2f}")
print()

# ============================================================================
# 6. VALIDATION
# ============================================================================
print("="*80)
print("6. VALIDATION")
print("="*80)

checks = []
checks.append(("Net PnL > $50k", net_pnl > 50000, f"${net_pnl:,.0f}"))
checks.append(("Max DD < 10%", abs(max_dd) < 10, f"{abs(max_dd):.2f}%"))
checks.append(("Daily DD < 5%", daily_dd_pct < 5, f"{daily_dd_pct:.2f}%"))

# Check all years profitable
all_years_profitable = True
for year in sorted(trades_df['year'].unique()):
    year_pnl = trades_df[trades_df['year'] == year]['pnl'].sum()
    if year_pnl <= 0:
        all_years_profitable = False
        break

checks.append(("All years profitable", all_years_profitable, "Check above"))

print()
for check_name, passed, value in checks:
    status = "PASS" if passed else "FAIL"
    print(f"  [{status}] {check_name}: {value}")

print()
all_passed = all(c[1] for c in checks)

if all_passed:
    print("="*80)
    print("ALL CHECKS PASSED - BTCUSD STRATEGY VALIDATED")
    print("="*80)
else:
    print("="*80)
    print("SOME CHECKS FAILED - REVIEW RESULTS")
    print("="*80)
