"""
Multi-Instrument Combined Strategy Backtest (LONG + SHORT)
Session Breakout + Reversal на M15 данных 2020-2024
Тестируем: EURUSD, XAGUSD
"""
import sys
import pandas as pd
import numpy as np
from pathlib import Path
from datetime import datetime, timezone

# ============================================================================
# PARAMETERS
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

# Instruments to test
INSTRUMENTS = {
    'EURUSD': 'data_cache/dukascopy/m15/EURUSD/eurusd_m15_2020-01-01_2024-12-31.parquet',
    'XAGUSD': 'data_cache/dukascopy/m15/XAGUSD/xagusd_m15_2020-01-01_2024-12-31.parquet',
}

def run_backtest(symbol, df):
    """Run backtest for a single instrument"""

    # Prepare H4 data
    df_h4 = df.resample('4h').agg({
        'open': 'first',
        'high': 'max',
        'low': 'min',
        'close': 'last'
    }).dropna()

    df_h4['ema20'] = df_h4['close'].ewm(span=20, adjust=False).mean()
    df_h4['atr'] = (df_h4['high'] - df_h4['low']).rolling(14).mean()

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
            atr_val = df_h4.loc[:date]['atr'].iloc[-1] if len(df_h4.loc[:date]) > 0 else 0.01
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
                    atr_val = h4_bar['atr'] if 'atr' in df_h4.columns else 0.01
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
            atr_val = h4_bar['atr'] if 'atr' in df_h4.columns else 0.01
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

    return trades, balance, peak_balance


# ============================================================================
# MAIN
# ============================================================================
print("="*80)
print("MULTI-INSTRUMENT BACKTEST (2020-2024)")
print("="*80)
print()

results = {}

for symbol, file_path in INSTRUMENTS.items():
    print("="*80)
    print(f"TESTING: {symbol}")
    print("="*80)

    full_path = Path(__file__).parent.parent / file_path
    if not full_path.exists():
        print(f"ERROR: File not found: {full_path}")
        continue

    print(f"Loading data from {file_path}...")
    df = pd.read_parquet(full_path)
    df = df.sort_index()
    print(f"Loaded {len(df)} M15 candles")
    print(f"Period: {df.index[0]} - {df.index[-1]}")
    print()

    print("Running backtest...")
    trades, final_balance, peak_balance = run_backtest(symbol, df)

    if len(trades) == 0:
        print("No trades!")
        print()
        continue

    trades_df = pd.DataFrame(trades)

    # Calculate metrics
    wins = trades_df[trades_df['pnl'] > 0]
    losses = trades_df[trades_df['pnl'] < 0]
    gross_pnl = trades_df['pnl'].sum()
    win_rate = len(wins) / len(trades_df) * 100

    # Drawdown
    trades_df['peak'] = trades_df['balance'].cummax()
    trades_df['dd'] = (trades_df['balance'] - trades_df['peak']) / INITIAL_BALANCE * 100
    max_dd = trades_df['dd'].min()

    # Daily DD
    trades_df['date_only'] = trades_df['date'].dt.date
    daily_pnl = trades_df.groupby('date_only')['pnl'].sum()
    daily_dd = daily_pnl.min()
    daily_dd_pct = abs(daily_dd) / INITIAL_BALANCE * 100

    # Swap estimate
    avg_hold_days = 2.8
    total_swap = len(trades_df[trades_df['strategy'] == 'LONG']) * avg_hold_days * -5
    total_swap += len(trades_df[trades_df['strategy'] == 'SHORT']) * avg_hold_days * -3
    net_pnl = gross_pnl + total_swap

    # By year
    trades_df['year'] = trades_df['date'].dt.year
    yearly_results = []
    for year in sorted(trades_df['year'].unique()):
        year_trades = trades_df[trades_df['year'] == year]
        year_pnl = year_trades['pnl'].sum()
        yearly_results.append((year, year_pnl))

    all_years_profitable = all(pnl > 0 for _, pnl in yearly_results)

    # Store results
    results[symbol] = {
        'trades': len(trades_df),
        'long_trades': len(trades_df[trades_df['strategy'] == 'LONG']),
        'short_trades': len(trades_df[trades_df['strategy'] == 'SHORT']),
        'gross_pnl': gross_pnl,
        'net_pnl': net_pnl,
        'win_rate': win_rate,
        'max_dd': abs(max_dd),
        'daily_dd': daily_dd_pct,
        'all_years_profitable': all_years_profitable,
        'yearly': yearly_results
    }

    # Print results
    print(f"Total trades: {len(trades_df)}")
    print(f"  LONG: {len(trades_df[trades_df['strategy'] == 'LONG'])}")
    print(f"  SHORT: {len(trades_df[trades_df['strategy'] == 'SHORT'])}")
    print()
    print(f"Gross PnL: ${gross_pnl:,.2f}")
    print(f"Net PnL (with swap): ${net_pnl:,.2f}")
    print(f"Win Rate: {win_rate:.1f}%")
    print(f"Max DD: {abs(max_dd):.2f}%")
    print(f"Daily DD: {daily_dd_pct:.2f}%")
    print()

    print("BY YEAR:")
    for year, pnl in yearly_results:
        year_trades = trades_df[trades_df['year'] == year]
        year_wr = len(year_trades[year_trades['pnl'] > 0]) / len(year_trades) * 100
        print(f"  {year}: ${pnl:,.0f} | {len(year_trades)} trades | WR {year_wr:.1f}%")
    print()

    # Validation
    checks = [
        ("Net PnL > $50k", net_pnl > 50000),
        ("Max DD < 10%", abs(max_dd) < 10),
        ("Daily DD < 5%", daily_dd_pct < 5),
        ("All years profitable", all_years_profitable)
    ]

    print("VALIDATION:")
    all_passed = True
    for check_name, passed in checks:
        status = "PASS" if passed else "FAIL"
        print(f"  [{status}] {check_name}")
        if not passed:
            all_passed = False

    if all_passed:
        print("  => STRATEGY VALIDATED FOR " + symbol)
    else:
        print("  => STRATEGY NOT SUITABLE FOR " + symbol)

    print()

# ============================================================================
# SUMMARY
# ============================================================================
print("="*80)
print("SUMMARY")
print("="*80)
print()

for symbol, res in results.items():
    status = "✓ VALID" if (res['net_pnl'] > 50000 and res['max_dd'] < 10 and
                           res['daily_dd'] < 5 and res['all_years_profitable']) else "✗ INVALID"
    print(f"{symbol}: {status}")
    print(f"  Net PnL: ${res['net_pnl']:,.0f} | Max DD: {res['max_dd']:.2f}% | WR: {res['win_rate']:.1f}%")
    print()
