"""
Test multiple variants to increase profit while keeping DD safe
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
import numpy as np
from astra_v2.data.dukascopy import load_timeframe

# Copy backtest logic from combined_session_backtest.py
ASIAN_PARAMS_BASE = {
    'tp_rr': 3.0,
    'stop_buffer_atr': 0.1,
    'min_range_atr': 0.7,
    'max_range_atr': 3.0,
    'trailing_start': 3.5,
    'trailing_distance': 0.2,
    'range_hours': (0, 7),
    'breakout_hours': (7, 10)
}

LONDON_PARAMS_BASE = {
    'tp_rr': 3.5,
    'stop_buffer_atr': 0.3,
    'min_range_atr': 0.3,
    'max_range_atr': 3.0,
    'trailing_start': 2.0,
    'trailing_distance': 0.2,
    'range_hours': (7, 12),
    'breakout_hours': (13, 16)
}

NY_PARAMS_BASE = {
    'tp_rr': 4.5,
    'stop_buffer_atr': 0.3,
    'min_range_atr': 0.5,
    'max_range_atr': 3.0,
    'trailing_start': 3.0,
    'trailing_distance': 0.1,
    'range_hours': (13, 17),
    'breakout_hours': (18, 21)
}

ATR_PERIOD = 20
START_DATE = "2020-01-01"
END_DATE = "2026-04-18"
H4_EMA_PERIOD = 20

def calculate_atr(df, period=20):
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

def run_variant(variant_name, risk_per_trade, asian_params, london_params, ny_params, df, df_h4):
    """Run backtest with specific parameters"""
    trades = []
    active_trades = {}
    balance = 10000
    peak_balance = 10000
    max_dd = 0
    max_daily_dd = 0

    dates = df.index.date
    unique_dates = sorted(set(dates))

    for date in unique_dates:
        day_start_balance = balance
        day_data = df[df.index.date == date]
        if len(day_data) < 10:
            continue

        asian_high, asian_low = get_session_range(day_data, *asian_params['range_hours'])
        london_high, london_low = get_session_range(day_data, *london_params['range_hours'])
        ny_high, ny_low = get_session_range(day_data, *ny_params['range_hours'])

        highs = day_data['high'].to_numpy()
        lows = day_data['low'].to_numpy()
        closes = day_data['close'].to_numpy()
        atrs = day_data['atr'].to_numpy()
        times = day_data.index.to_numpy()
        hours = np.array([t.hour for t in day_data.index])

        for i in range(len(day_data)):
            atr = atrs[i]
            if np.isnan(atr):
                continue
            hour = hours[i]

            # Check exits
            for session_name in list(active_trades.keys()):
                trade = active_trades[session_name]
                params = {'asian': asian_params, 'london': london_params, 'ny': ny_params}[session_name]

                if trade['direction'] == 'LONG':
                    risk = trade['entry'] - trade['initial_sl']
                    if highs[i] >= trade['entry'] + risk:
                        trade['sl'] = max(trade['sl'], trade['entry'])
                    if params['trailing_start'] is not None:
                        if highs[i] >= trade['entry'] + params['trailing_start'] * risk:
                            trailing_sl = highs[i] - params['trailing_distance'] * risk
                            trade['sl'] = max(trade['sl'], trailing_sl)

                exit_trade = False
                if trade['direction'] == 'LONG':
                    if lows[i] <= trade['sl']:
                        pnl = (trade['sl'] - trade['entry']) * trade['size']
                        balance += pnl
                        trade['pnl'] = pnl
                        trade['status'] = 'sl'
                        exit_trade = True
                    elif highs[i] >= trade['tp']:
                        pnl = (trade['tp'] - trade['entry']) * trade['size']
                        balance += pnl
                        trade['pnl'] = pnl
                        trade['status'] = 'tp'
                        exit_trade = True

                if exit_trade:
                    trades.append(trade)
                    del active_trades[session_name]
                    if balance > peak_balance:
                        peak_balance = balance
                    dd = (peak_balance - balance) / peak_balance * 100
                    if dd > max_dd:
                        max_dd = dd

            # Entry logic - only LONG
            # Asian
            if asian_params['breakout_hours'][0] <= hour < asian_params['breakout_hours'][1]:
                if asian_high is not None and 'asian' not in active_trades:
                    asian_range = asian_high - asian_low
                    if asian_params['min_range_atr'] * atr <= asian_range <= asian_params['max_range_atr'] * atr:
                        h4_bar = df_h4[df_h4.index <= times[i]].iloc[-1] if len(df_h4[df_h4.index <= times[i]]) > 0 else None
                        if h4_bar is None or pd.isna(h4_bar['ema20']):
                            continue

                        if closes[i] > asian_high:
                            if h4_bar['close'] <= h4_bar['ema20']:
                                continue
                            entry = closes[i]
                            sl = asian_low - asian_params['stop_buffer_atr'] * atr
                            risk = entry - sl
                            tp = entry + risk * asian_params['tp_rr']
                            size = risk_per_trade / risk
                            active_trades['asian'] = {
                                'entry': entry, 'sl': sl, 'initial_sl': sl, 'tp': tp,
                                'size': size, 'direction': 'LONG', 'range_type': 'asian'
                            }

            # London
            if london_params['breakout_hours'][0] <= hour < london_params['breakout_hours'][1]:
                if london_high is not None and 'london' not in active_trades:
                    london_range = london_high - london_low
                    if london_params['min_range_atr'] * atr <= london_range <= london_params['max_range_atr'] * atr:
                        h4_bar = df_h4[df_h4.index <= times[i]].iloc[-1] if len(df_h4[df_h4.index <= times[i]]) > 0 else None
                        if h4_bar is None or pd.isna(h4_bar['ema20']):
                            continue

                        if closes[i] > london_high:
                            if h4_bar['close'] <= h4_bar['ema20']:
                                continue
                            entry = closes[i]
                            sl = london_low - london_params['stop_buffer_atr'] * atr
                            risk = entry - sl
                            tp = entry + risk * london_params['tp_rr']
                            size = risk_per_trade / risk
                            active_trades['london'] = {
                                'entry': entry, 'sl': sl, 'initial_sl': sl, 'tp': tp,
                                'size': size, 'direction': 'LONG', 'range_type': 'london'
                            }

            # NY
            if ny_params['breakout_hours'][0] <= hour < ny_params['breakout_hours'][1]:
                if ny_high is not None and 'ny' not in active_trades:
                    ny_range = ny_high - ny_low
                    if ny_params['min_range_atr'] * atr <= ny_range <= ny_params['max_range_atr'] * atr:
                        h4_bar = df_h4[df_h4.index <= times[i]].iloc[-1] if len(df_h4[df_h4.index <= times[i]]) > 0 else None
                        if h4_bar is None or pd.isna(h4_bar['ema20']):
                            continue

                        if closes[i] > ny_high:
                            if h4_bar['close'] <= h4_bar['ema20']:
                                continue
                            entry = closes[i]
                            sl = ny_low - ny_params['stop_buffer_atr'] * atr
                            risk = entry - sl
                            tp = entry + risk * ny_params['tp_rr']
                            size = risk_per_trade / risk
                            active_trades['ny'] = {
                                'entry': entry, 'sl': sl, 'initial_sl': sl, 'tp': tp,
                                'size': size, 'direction': 'LONG', 'range_type': 'ny'
                            }

        if day_start_balance > 0:
            daily_dd = (day_start_balance - balance) / day_start_balance * 100
            if daily_dd > max_daily_dd:
                max_daily_dd = daily_dd

    # Close remaining
    for session_name, trade in active_trades.items():
        last_bar = df.iloc[-1]
        pnl = (last_bar['close'] - trade['entry']) * trade['size']
        balance += pnl
        trade['pnl'] = pnl
        trade['status'] = 'eod'
        trades.append(trade)

    trades_df = pd.DataFrame(trades)
    total_pnl = balance - 10000
    passes = max_dd < 10.0 and max_daily_dd < 5.0 and len(trades_df) >= 150

    wins = trades_df[trades_df['pnl'] > 0]
    losses = trades_df[trades_df['pnl'] <= 0]
    total_profit = wins['pnl'].sum() if len(wins) > 0 else 0
    total_loss = abs(losses['pnl'].sum()) if len(losses) > 0 else 0
    profit_factor = total_profit / total_loss if total_loss > 0 else 0

    return {
        'variant': variant_name,
        'pnl': total_pnl,
        'trades': len(trades_df),
        'pf': profit_factor,
        'dd': max_dd,
        'daily_dd': max_daily_dd,
        'passes': passes
    }

print("=" * 100)
print("TESTING PROFIT IMPROVEMENT VARIANTS")
print("=" * 100)
print()

# Load data
print("Loading data...")
df = load_timeframe("M15", start=START_DATE, end=END_DATE, symbol="XAUUSD")
if 'datetime' in df.columns:
    df.set_index('datetime', inplace=True)
df = df.sort_index()
df['atr'] = calculate_atr(df, ATR_PERIOD)

df_h4 = df.resample('4h').agg({'open':'first','high':'max','low':'min','close':'last'}).dropna()
df_h4['ema20'] = calculate_ema(df_h4, H4_EMA_PERIOD)
print(f"Loaded {len(df):,} M15 bars, {len(df_h4):,} H4 bars")
print()

results = []

# Baseline (current LONG only)
print("1. Testing BASELINE (LONG only, RISK=$100)...")
results.append(run_variant("Baseline", 100, ASIAN_PARAMS_BASE, LONDON_PARAMS_BASE, NY_PARAMS_BASE, df, df_h4))

# Variant 1: Increase risk to $150
print("2. Testing RISK=$150...")
results.append(run_variant("Risk $150", 150, ASIAN_PARAMS_BASE, LONDON_PARAMS_BASE, NY_PARAMS_BASE, df, df_h4))

# Variant 2: Increase risk to $200
print("3. Testing RISK=$200...")
results.append(run_variant("Risk $200", 200, ASIAN_PARAMS_BASE, LONDON_PARAMS_BASE, NY_PARAMS_BASE, df, df_h4))

# Variant 3: Increase TP_RR for all sessions
print("4. Testing Higher TP_RR (Asian=4, London=4.5, NY=5.5)...")
asian_high_tp = ASIAN_PARAMS_BASE.copy()
asian_high_tp['tp_rr'] = 4.0
london_high_tp = LONDON_PARAMS_BASE.copy()
london_high_tp['tp_rr'] = 4.5
ny_high_tp = NY_PARAMS_BASE.copy()
ny_high_tp['tp_rr'] = 5.5
results.append(run_variant("Higher TP_RR", 100, asian_high_tp, london_high_tp, ny_high_tp, df, df_h4))

# Variant 4: Remove trailing stop
print("5. Testing No Trailing Stop...")
asian_no_trail = ASIAN_PARAMS_BASE.copy()
asian_no_trail['trailing_start'] = None
london_no_trail = LONDON_PARAMS_BASE.copy()
london_no_trail['trailing_start'] = None
ny_no_trail = NY_PARAMS_BASE.copy()
ny_no_trail['trailing_start'] = None
results.append(run_variant("No Trailing", 100, asian_no_trail, london_no_trail, ny_no_trail, df, df_h4))

# Variant 5: Combo - Risk $150 + Higher TP
print("6. Testing COMBO: Risk $150 + Higher TP_RR...")
results.append(run_variant("Risk $150 + TP", 150, asian_high_tp, london_high_tp, ny_high_tp, df, df_h4))

# Variant 6: Combo - Risk $150 + No Trailing
print("7. Testing COMBO: Risk $150 + No Trailing...")
results.append(run_variant("Risk $150 + NoTrail", 150, asian_no_trail, london_no_trail, ny_no_trail, df, df_h4))

# Variant 7: Aggressive - Risk $200 + Higher TP + No Trailing
print("8. Testing AGGRESSIVE: Risk $200 + Higher TP + No Trailing...")
results.append(run_variant("Aggressive", 200, asian_high_tp, london_high_tp, ny_high_tp, df, df_h4))

print()
print("=" * 100)
print("RESULTS COMPARISON")
print("=" * 100)
print(f"{'Variant':<25} {'PnL':<15} {'Trades':<10} {'PF':<8} {'DD%':<8} {'DailyDD%':<10} {'Status':<8}")
print("-" * 100)

for r in results:
    status = "PASS" if r['passes'] else "FAIL"
    print(f"{r['variant']:<25} ${r['pnl']:<14,.0f} {r['trades']:<10} {r['pf']:<8.3f} {r['dd']:<8.2f} {r['daily_dd']:<10.2f} {status:<8}")

print("=" * 100)

# Find best passing variant
passing = [r for r in results if r['passes']]
if len(passing) > 0:
    best = max(passing, key=lambda x: x['pnl'])
    print(f"\nBEST VARIANT: {best['variant']}")
    print(f"PnL: ${best['pnl']:,.0f}, PF: {best['pf']:.3f}, DD: {best['dd']:.2f}%, Daily DD: {best['daily_dd']:.2f}%")
else:
    print("\nNo variants passed all filters!")
