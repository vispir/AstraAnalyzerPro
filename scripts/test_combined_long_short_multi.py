"""
Combined LONG + SHORT Backtest - Multiple Simultaneous Positions
=================================================================
LONG: Asian + London + NY session breakouts (одна позиция на сессию)
SHORT: Type1 + Type2 reversal (одна SHORT позиция)

Funding Pips разрешает множественные одновременные позиции.
Можно держать до 4 позиций одновременно: Asian LONG + London LONG + NY LONG + SHORT
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
import numpy as np
from pathlib import Path

# LONG Session Parameters
ASIAN_PARAMS = {
    'tp_rr': 5.5,
    'stop_buffer_atr': 0.1,
    'min_range_atr': 0.7,
    'max_range_atr': 3.0,
    'range_hours': (0, 7),
    'breakout_hours': (7, 10)
}

LONDON_PARAMS = {
    'tp_rr': 5.5,
    'stop_buffer_atr': 0.3,
    'min_range_atr': 0.3,
    'max_range_atr': 3.0,
    'range_hours': (7, 12),
    'breakout_hours': (13, 16)
}

NY_PARAMS = {
    'tp_rr': 5.5,
    'stop_buffer_atr': 0.3,
    'min_range_atr': 0.5,
    'max_range_atr': 3.0,
    'range_hours': (13, 17),
    'breakout_hours': (18, 21)
}

# SHORT Reversal Parameters
SHORT_TYPE1_LOOKBACK_H4_BARS = 5
SHORT_TYPE2_H4_LOOKBACK = 3
SHORT_TYPE2_ATR_MULTIPLIER = 2.0
SHORT_ATR_BUFFER = 0.5
SHORT_TP_RR = 5.5

# Common Parameters
ATR_PERIOD = 14
H4_EMA_PERIOD = 20
RISK_PER_TRADE = 158
USE_H4_EMA_FILTER = True

def calculate_atr(df, period=14):
    high = df['high']
    low = df['low']
    close = df['close']

    tr1 = high - low
    tr2 = abs(high - close.shift(1))
    tr3 = abs(low - close.shift(1))

    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    atr = tr.rolling(window=period).mean()

    return atr

def calculate_ema(df, period):
    return df['close'].ewm(span=period, adjust=False).mean()

def get_session_range(df, start_hour, end_hour):
    mask = (df.index.hour >= start_hour) & (df.index.hour < end_hour)
    session_bars = df[mask]

    if len(session_bars) == 0:
        return None, None

    range_high = session_bars['high'].max()
    range_low = session_bars['low'].min()

    return range_high, range_low

def apply_step_trailing(active_trade, current_low, current_high, is_long=True):
    """Step Trailing Stop: 2R->1R, 3R->2R, 4R->3R, 5R->4R"""
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

def run_combined_backtest():
    print("="*80)
    print("COMBINED LONG + SHORT BACKTEST (Multiple Simultaneous Positions)")
    print("="*80)
    print()

    # Load data
    data_path = Path(__file__).parent.parent / "data_cache" / "dukascopy" / "m15" / "XAUUSD" / "xauusd_m15_2020-01-01_2026-04-18.parquet"
    df = pd.read_parquet(data_path)
    df = df.sort_index()

    print(f"Period: {df.index[0]} - {df.index[-1]}")
    print(f"Total M15 bars: {len(df):,}")
    print()

    # Prepare data
    df['atr'] = calculate_atr(df, ATR_PERIOD)
    df_h4 = df.resample('4h').agg({'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last'}).dropna()
    df_h4['atr'] = calculate_atr(df_h4, ATR_PERIOD)
    df_h4['ema20'] = calculate_ema(df_h4, H4_EMA_PERIOD)

    print(f"H4 bars: {len(df_h4):,}")
    print()

    trades = []
    active_long_trades = {}  # Can have multiple LONG trades (one per session)
    active_short = None  # Only one SHORT at a time
    balance = 10000
    peak_balance = 10000
    max_dd = 0
    max_daily_dd = 0

    short_type1_reversal_active = False
    short_type1_reversal_h4_high = None
    short_type2_reversal_active = False
    short_type2_reversal_h4_high = None
    last_h4_index = None

    dates = df.index.date
    unique_dates = sorted(set(dates))

    for date in unique_dates:
        day_start_balance = balance
        day_data = df[df.index.date == date]

        if len(day_data) < 10:
            continue

        # Calculate ranges for all sessions
        asian_high, asian_low = get_session_range(day_data, *ASIAN_PARAMS['range_hours'])
        london_high, london_low = get_session_range(day_data, *LONDON_PARAMS['range_hours'])
        ny_high, ny_low = get_session_range(day_data, *NY_PARAMS['range_hours'])

        highs = day_data['high'].to_numpy()
        lows = day_data['low'].to_numpy()
        closes = day_data['close'].to_numpy()
        atrs = day_data['atr'].to_numpy()
        hours = np.array([t.hour for t in day_data.index])
        times = day_data.index.to_numpy()

        for i in range(len(day_data)):
            current_time = times[i]
            hour = hours[i]
            atr = atrs[i]

            if np.isnan(atr):
                continue

            h4_bars = df_h4[df_h4.index <= current_time]
            if len(h4_bars) < max(SHORT_TYPE1_LOOKBACK_H4_BARS + 2, SHORT_TYPE2_H4_LOOKBACK + 1):
                continue

            current_h4 = h4_bars.iloc[-1]

            # === MANAGE ACTIVE LONG TRADES ===
            for session_name in list(active_long_trades.keys()):
                trade = active_long_trades[session_name]
                apply_step_trailing(trade, lows[i], highs[i], is_long=True)

                exit_trade = False
                if lows[i] <= trade['sl']:
                    pnl = (trade['sl'] - trade['entry']) * trade['size']
                    balance += pnl
                    trade['exit'] = trade['sl']
                    trade['pnl'] = pnl
                    trade['status'] = 'sl'
                    exit_trade = True
                elif highs[i] >= trade['tp']:
                    pnl = (trade['tp'] - trade['entry']) * trade['size']
                    balance += pnl
                    trade['exit'] = trade['tp']
                    trade['pnl'] = pnl
                    trade['status'] = 'tp'
                    exit_trade = True

                if exit_trade:
                    trade['exit_time'] = current_time
                    trades.append(trade)
                    del active_long_trades[session_name]

            # === MANAGE ACTIVE SHORT TRADE ===
            if active_short is not None:
                apply_step_trailing(active_short, lows[i], highs[i], is_long=False)

                exit_trade = False
                if highs[i] >= active_short['sl']:
                    pnl = (active_short['entry'] - active_short['sl']) * active_short['size']
                    balance += pnl
                    active_short['exit'] = active_short['sl']
                    active_short['pnl'] = pnl
                    active_short['status'] = 'sl'
                    exit_trade = True
                elif lows[i] <= active_short['tp']:
                    pnl = (active_short['entry'] - active_short['tp']) * active_short['size']
                    balance += pnl
                    active_short['exit'] = active_short['tp']
                    active_short['pnl'] = pnl
                    active_short['status'] = 'tp'
                    exit_trade = True

                if exit_trade:
                    active_short['exit_time'] = current_time
                    trades.append(active_short)
                    active_short = None
                    short_type1_reversal_active = False
                    short_type2_reversal_active = False

            # Update DD
            if balance > peak_balance:
                peak_balance = balance
            dd = (peak_balance - balance) / peak_balance * 100
            if dd > max_dd:
                max_dd = dd

            # === LONG ENTRY LOGIC ===
            # Asian breakout
            if ASIAN_PARAMS['breakout_hours'][0] <= hour < ASIAN_PARAMS['breakout_hours'][1]:
                if asian_high is not None and 'asian' not in active_long_trades:
                    asian_range = asian_high - asian_low
                    if ASIAN_PARAMS['min_range_atr'] * atr <= asian_range <= ASIAN_PARAMS['max_range_atr'] * atr:
                        if USE_H4_EMA_FILTER:
                            if pd.isna(current_h4['ema20']) or current_h4['close'] <= current_h4['ema20']:
                                pass
                            elif closes[i] > asian_high:
                                entry = closes[i]
                                sl = asian_low - ASIAN_PARAMS['stop_buffer_atr'] * atr
                                risk = entry - sl
                                if risk > 0:
                                    tp = entry + risk * ASIAN_PARAMS['tp_rr']
                                    size = RISK_PER_TRADE / risk
                                    active_long_trades['asian'] = {
                                        'entry': entry, 'sl': sl, 'initial_sl': sl, 'tp': tp,
                                        'size': size, 'direction': 'LONG', 'entry_time': current_time,
                                        'range_type': 'asian'
                                    }

            # London breakout
            if LONDON_PARAMS['breakout_hours'][0] <= hour < LONDON_PARAMS['breakout_hours'][1]:
                if london_high is not None and 'london' not in active_long_trades:
                    london_range = london_high - london_low
                    if LONDON_PARAMS['min_range_atr'] * atr <= london_range <= LONDON_PARAMS['max_range_atr'] * atr:
                        if USE_H4_EMA_FILTER:
                            if pd.isna(current_h4['ema20']) or current_h4['close'] <= current_h4['ema20']:
                                pass
                            elif closes[i] > london_high:
                                entry = closes[i]
                                sl = london_low - LONDON_PARAMS['stop_buffer_atr'] * atr
                                risk = entry - sl
                                if risk > 0:
                                    tp = entry + risk * LONDON_PARAMS['tp_rr']
                                    size = RISK_PER_TRADE / risk
                                    active_long_trades['london'] = {
                                        'entry': entry, 'sl': sl, 'initial_sl': sl, 'tp': tp,
                                        'size': size, 'direction': 'LONG', 'entry_time': current_time,
                                        'range_type': 'london'
                                    }

            # NY breakout
            if NY_PARAMS['breakout_hours'][0] <= hour < NY_PARAMS['breakout_hours'][1]:
                if ny_high is not None and 'ny' not in active_long_trades:
                    ny_range = ny_high - ny_low
                    if NY_PARAMS['min_range_atr'] * atr <= ny_range <= NY_PARAMS['max_range_atr'] * atr:
                        if USE_H4_EMA_FILTER:
                            if pd.isna(current_h4['ema20']) or current_h4['close'] <= current_h4['ema20']:
                                pass
                            elif closes[i] > ny_high:
                                entry = closes[i]
                                sl = ny_low - NY_PARAMS['stop_buffer_atr'] * atr
                                risk = entry - sl
                                if risk > 0:
                                    tp = entry + risk * NY_PARAMS['tp_rr']
                                    size = RISK_PER_TRADE / risk
                                    active_long_trades['ny'] = {
                                        'entry': entry, 'sl': sl, 'initial_sl': sl, 'tp': tp,
                                        'size': size, 'direction': 'LONG', 'entry_time': current_time,
                                        'range_type': 'ny'
                                    }

            # === SHORT ENTRY LOGIC ===
            if active_short is None and hour < 21:
                prev_h4 = h4_bars.iloc[-2]

                current_h4_index = current_h4.name
                if last_h4_index != current_h4_index:
                    last_h4_index = current_h4_index

                    # H4 EMA20 Filter
                    if USE_H4_EMA_FILTER:
                        if pd.isna(current_h4['ema20']):
                            short_type1_reversal_active = False
                            short_type2_reversal_active = False
                            continue

                        if current_h4['close'] >= current_h4['ema20']:
                            short_type1_reversal_active = False
                            short_type2_reversal_active = False
                            continue

                    # Type 1: Historical High Reversal
                    if not short_type1_reversal_active:
                        lookback_highs = h4_bars.iloc[-SHORT_TYPE1_LOOKBACK_H4_BARS-1:-1]['high']
                        historical_high = lookback_highs.max()

                        if current_h4['high'] > historical_high:
                            if current_h4['close'] < prev_h4['close']:
                                short_type1_reversal_active = True
                                short_type1_reversal_h4_high = current_h4['high']

                    # Type 2: Local Reversal After Strong Move
                    if not short_type2_reversal_active:
                        if len(h4_bars) >= SHORT_TYPE2_H4_LOOKBACK + 1:
                            lookback_bars = h4_bars.iloc[-SHORT_TYPE2_H4_LOOKBACK-1:-1]
                            price_change = current_h4['high'] - lookback_bars['low'].min()
                            h4_atr = current_h4.get('atr', atr)

                            if not np.isnan(h4_atr) and price_change >= SHORT_TYPE2_ATR_MULTIPLIER * h4_atr:
                                if current_h4['close'] < prev_h4['close']:
                                    short_type2_reversal_active = True
                                    short_type2_reversal_h4_high = current_h4['high']

                # M15 entry trigger
                if i > 0:
                    prev_m15_low = lows[i-1]

                    if short_type1_reversal_active and closes[i] < prev_m15_low:
                        entry = closes[i]
                        sl = short_type1_reversal_h4_high + SHORT_ATR_BUFFER * atr
                        risk = sl - entry

                        if risk > 0:
                            tp = entry - risk * SHORT_TP_RR
                            size = RISK_PER_TRADE / risk

                            active_short = {
                                'entry': entry, 'sl': sl, 'initial_sl': sl, 'tp': tp,
                                'size': size, 'direction': 'SHORT', 'entry_time': current_time,
                                'range_type': 'short_type1'
                            }
                            short_type1_reversal_active = False

                    elif short_type2_reversal_active and closes[i] < prev_m15_low:
                        entry = closes[i]
                        sl = short_type2_reversal_h4_high + SHORT_ATR_BUFFER * atr
                        risk = sl - entry

                        if risk > 0:
                            tp = entry - risk * SHORT_TP_RR
                            size = RISK_PER_TRADE / risk

                            active_short = {
                                'entry': entry, 'sl': sl, 'initial_sl': sl, 'tp': tp,
                                'size': size, 'direction': 'SHORT', 'entry_time': current_time,
                                'range_type': 'short_type2'
                            }
                            short_type2_reversal_active = False

        # Calculate daily drawdown
        if day_start_balance > 0:
            daily_dd = (day_start_balance - balance) / day_start_balance * 100
            if daily_dd > max_daily_dd:
                max_daily_dd = daily_dd

    # Close remaining trades
    for session_name, trade in active_long_trades.items():
        last_bar = df.iloc[-1]
        pnl = (last_bar['close'] - trade['entry']) * trade['size']
        balance += pnl
        trade['exit'] = last_bar['close']
        trade['exit_time'] = df.index[-1]
        trade['pnl'] = pnl
        trade['status'] = 'eod'
        trades.append(trade)

    if active_short is not None:
        last_bar = df.iloc[-1]
        pnl = (active_short['entry'] - last_bar['close']) * active_short['size']
        balance += pnl
        active_short['exit'] = last_bar['close']
        active_short['exit_time'] = df.index[-1]
        active_short['pnl'] = pnl
        active_short['status'] = 'eod'
        trades.append(active_short)

    # Calculate statistics
    trades_df = pd.DataFrame(trades)

    total_trades = len(trades_df)
    wins = trades_df[trades_df['pnl'] > 0]
    losses = trades_df[trades_df['pnl'] <= 0]

    win_rate = len(wins) / total_trades if total_trades > 0 else 0
    total_profit = wins['pnl'].sum() if len(wins) > 0 else 0
    total_loss = abs(losses['pnl'].sum()) if len(losses) > 0 else 0
    profit_factor = total_profit / total_loss if total_loss > 0 else 0
    total_pnl = balance - 10000

    # Breakdown by direction
    long_df = trades_df[trades_df['direction'] == 'LONG']
    short_df = trades_df[trades_df['direction'] == 'SHORT']

    # Breakdown by session
    asian_df = long_df[long_df['range_type'] == 'asian']
    london_df = long_df[long_df['range_type'] == 'london']
    ny_df = long_df[long_df['range_type'] == 'ny']
    short_type1_df = short_df[short_df['range_type'] == 'short_type1']
    short_type2_df = short_df[short_df['range_type'] == 'short_type2']

    # Yearly breakdown
    trades_df['year'] = pd.to_datetime(trades_df['entry_time']).dt.year
    yearly_pnl = trades_df.groupby('year')['pnl'].sum()
    all_years_profitable = all(yearly_pnl > 0)

    # Print results
    print("="*80)
    print("RESULTS: LONG + SHORT (Multiple Simultaneous Positions)")
    print("="*80)
    print()
    print(f"Total Trades: {total_trades}")
    print(f"Win Rate: {win_rate:.1%}")
    print(f"Profit Factor: {profit_factor:.3f}")
    print(f"Total PnL: ${total_pnl:,.0f}")
    print(f"Final Balance: ${balance:,.0f}")
    print(f"Max DD: {max_dd:.2f}%")
    print(f"Max Daily DD: {max_daily_dd:.2f}%")
    print()

    print("="*80)
    print("BREAKDOWN BY DIRECTION")
    print("="*80)
    long_pnl = long_df['pnl'].sum()
    long_wr = len(long_df[long_df['pnl'] > 0]) / len(long_df) * 100 if len(long_df) > 0 else 0
    short_pnl = short_df['pnl'].sum()
    short_wr = len(short_df[short_df['pnl'] > 0]) / len(short_df) * 100 if len(short_df) > 0 else 0

    print(f"LONG: {len(long_df)} trades, ${long_pnl:,.0f} PnL, WR {long_wr:.1f}%")
    print(f"SHORT: {len(short_df)} trades, ${short_pnl:,.0f} PnL, WR {short_wr:.1f}%")
    print()

    print("="*80)
    print("BREAKDOWN BY SESSION")
    print("="*80)
    for name, session_df in [('ASIAN', asian_df), ('LONDON', london_df), ('NY', ny_df),
                              ('SHORT Type1', short_type1_df), ('SHORT Type2', short_type2_df)]:
        if len(session_df) > 0:
            session_pnl = session_df['pnl'].sum()
            session_wr = len(session_df[session_df['pnl'] > 0]) / len(session_df) * 100
            print(f"{name}: {len(session_df)} trades, ${session_pnl:,.0f} PnL, WR {session_wr:.1f}%")
    print()

    print("="*80)
    print("PNL BY YEAR")
    print("="*80)
    for year in sorted(yearly_pnl.keys()):
        print(f"{year}: ${yearly_pnl[year]:,.0f}")
    print(f"\nAll years profitable: {'YES' if all_years_profitable else 'NO'}")
    print()

    print("="*80)
    print("COMPARISON")
    print("="*80)
    print(f"LONG only (from previous test): $40,134 PnL, 360 trades, DD 6.32%")
    print(f"LONG + SHORT: ${total_pnl:,.0f} PnL, {total_trades} trades, DD {max_dd:.2f}%")
    print(f"SHORT contribution: ${short_pnl:,.0f} ({len(short_df)} trades)")
    print(f"Improvement: +${total_pnl - 40134:,.0f} ({(total_pnl - 40134) / 40134 * 100:.1f}%)")
    print("="*80)

if __name__ == "__main__":
    run_combined_backtest()
