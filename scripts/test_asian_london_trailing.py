"""
Test Trailing Stop for Asian and London sessions
Tests TrailStart=3.0, TrailDist=0.3 for Asian and London
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
import numpy as np
from astra_v2.data.dukascopy import load_timeframe

# Current optimal parameters
BASELINE_ASIAN = {
    'tp_rr': 3.0,
    'stop_buffer_atr': 0.1,
    'min_range_atr': 0.7,
    'max_range_atr': 3.0,
    'trailing_start': None,
    'trailing_distance': 0.3,
    'range_hours': (0, 7),
    'breakout_hours': (7, 10)
}

BASELINE_LONDON = {
    'tp_rr': 3.5,
    'stop_buffer_atr': 0.3,
    'min_range_atr': 0.3,
    'max_range_atr': 3.0,
    'trailing_start': 2.0,
    'trailing_distance': 0.3,
    'range_hours': (7, 12),
    'breakout_hours': (13, 16)
}

NY_PARAMS = {
    'tp_rr': 4.5,
    'stop_buffer_atr': 0.3,
    'min_range_atr': 0.5,
    'max_range_atr': 3.0,
    'trailing_start': 3.0,
    'trailing_distance': 0.3,
    'range_hours': (13, 17),
    'breakout_hours': (18, 21)
}

ATR_PERIOD = 20
RISK_PER_TRADE = 100
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

def run_backtest(asian_params, london_params, ny_params, df, df_h4):
    """Run backtest with given parameters"""
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
        hours = np.array([t.hour for t in day_data.index])
        times = day_data.index.to_numpy()

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

                else:
                    risk = trade['initial_sl'] - trade['entry']

                    if lows[i] <= trade['entry'] - risk:
                        trade['sl'] = min(trade['sl'], trade['entry'])

                    if params['trailing_start'] is not None:
                        if lows[i] <= trade['entry'] - params['trailing_start'] * risk:
                            trailing_sl = lows[i] + params['trailing_distance'] * risk
                            trade['sl'] = min(trade['sl'], trailing_sl)

                exit_trade = False
                if trade['direction'] == 'LONG':
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
                else:
                    if highs[i] >= trade['sl']:
                        pnl = (trade['entry'] - trade['sl']) * trade['size']
                        balance += pnl
                        trade['exit'] = trade['sl']
                        trade['pnl'] = pnl
                        trade['status'] = 'sl'
                        exit_trade = True
                    elif lows[i] <= trade['tp']:
                        pnl = (trade['entry'] - trade['tp']) * trade['size']
                        balance += pnl
                        trade['exit'] = trade['tp']
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

            # Asian breakout
            if asian_params['breakout_hours'][0] <= hour < asian_params['breakout_hours'][1]:
                if asian_high is not None and 'asian' not in active_trades:
                    asian_range = asian_high - asian_low
                    if asian_params['min_range_atr'] * atr <= asian_range <= asian_params['max_range_atr'] * atr:
                        current_time = times[i]
                        h4_bar = df_h4[df_h4.index <= current_time].iloc[-1] if len(df_h4[df_h4.index <= current_time]) > 0 else None
                        if h4_bar is None or pd.isna(h4_bar['ema20']):
                            continue

                        if closes[i] > asian_high:
                            if h4_bar['close'] <= h4_bar['ema20']:
                                continue

                            entry = closes[i]
                            sl = asian_low - asian_params['stop_buffer_atr'] * atr
                            risk = entry - sl
                            tp = entry + risk * asian_params['tp_rr']
                            size = RISK_PER_TRADE / risk

                            active_trades['asian'] = {
                                'entry': entry, 'sl': sl, 'initial_sl': sl, 'tp': tp,
                                'size': size, 'direction': 'LONG', 'entry_time': times[i],
                                'range_type': 'asian'
                            }
                        elif closes[i] < asian_low:
                            if h4_bar['close'] >= h4_bar['ema20']:
                                continue

                            entry = closes[i]
                            sl = asian_high + asian_params['stop_buffer_atr'] * atr
                            risk = sl - entry
                            tp = entry - risk * asian_params['tp_rr']
                            size = RISK_PER_TRADE / risk

                            active_trades['asian'] = {
                                'entry': entry, 'sl': sl, 'initial_sl': sl, 'tp': tp,
                                'size': size, 'direction': 'SHORT', 'entry_time': times[i],
                                'range_type': 'asian'
                            }

            # London breakout
            if london_params['breakout_hours'][0] <= hour < london_params['breakout_hours'][1]:
                if london_high is not None and 'london' not in active_trades:
                    london_range = london_high - london_low
                    if london_params['min_range_atr'] * atr <= london_range <= london_params['max_range_atr'] * atr:
                        current_time = times[i]
                        h4_bar = df_h4[df_h4.index <= current_time].iloc[-1] if len(df_h4[df_h4.index <= current_time]) > 0 else None
                        if h4_bar is None or pd.isna(h4_bar['ema20']):
                            continue

                        if closes[i] > london_high:
                            if h4_bar['close'] <= h4_bar['ema20']:
                                continue

                            entry = closes[i]
                            sl = london_low - london_params['stop_buffer_atr'] * atr
                            risk = entry - sl
                            tp = entry + risk * london_params['tp_rr']
                            size = RISK_PER_TRADE / risk

                            active_trades['london'] = {
                                'entry': entry, 'sl': sl, 'initial_sl': sl, 'tp': tp,
                                'size': size, 'direction': 'LONG', 'entry_time': times[i],
                                'range_type': 'london'
                            }
                        elif closes[i] < london_low:
                            if h4_bar['close'] >= h4_bar['ema20']:
                                continue

                            entry = closes[i]
                            sl = london_high + london_params['stop_buffer_atr'] * atr
                            risk = sl - entry
                            tp = entry - risk * london_params['tp_rr']
                            size = RISK_PER_TRADE / risk

                            active_trades['london'] = {
                                'entry': entry, 'sl': sl, 'initial_sl': sl, 'tp': tp,
                                'size': size, 'direction': 'SHORT', 'entry_time': times[i],
                                'range_type': 'london'
                            }

            # NY breakout
            if ny_params['breakout_hours'][0] <= hour < ny_params['breakout_hours'][1]:
                if ny_high is not None and 'ny' not in active_trades:
                    ny_range = ny_high - ny_low
                    if ny_params['min_range_atr'] * atr <= ny_range <= ny_params['max_range_atr'] * atr:
                        current_time = times[i]
                        h4_bar = df_h4[df_h4.index <= current_time].iloc[-1] if len(df_h4[df_h4.index <= current_time]) > 0 else None
                        if h4_bar is None or pd.isna(h4_bar['ema20']):
                            continue

                        if closes[i] > ny_high:
                            if h4_bar['close'] <= h4_bar['ema20']:
                                continue

                            entry = closes[i]
                            sl = ny_low - ny_params['stop_buffer_atr'] * atr
                            risk = entry - sl
                            tp = entry + risk * ny_params['tp_rr']
                            size = RISK_PER_TRADE / risk

                            active_trades['ny'] = {
                                'entry': entry, 'sl': sl, 'initial_sl': sl, 'tp': tp,
                                'size': size, 'direction': 'LONG', 'entry_time': times[i],
                                'range_type': 'ny'
                            }
                        elif closes[i] < ny_low:
                            if h4_bar['close'] >= h4_bar['ema20']:
                                continue

                            entry = closes[i]
                            sl = ny_high + ny_params['stop_buffer_atr'] * atr
                            risk = sl - entry
                            tp = entry - risk * ny_params['tp_rr']
                            size = RISK_PER_TRADE / risk

                            active_trades['ny'] = {
                                'entry': entry, 'sl': sl, 'initial_sl': sl, 'tp': tp,
                                'size': size, 'direction': 'SHORT', 'entry_time': times[i],
                                'range_type': 'ny'
                            }

        if day_start_balance > 0:
            daily_dd = (day_start_balance - balance) / day_start_balance * 100
            if daily_dd > max_daily_dd:
                max_daily_dd = daily_dd

    # Close remaining trades
    for session_name, trade in active_trades.items():
        last_bar = df.iloc[-1]
        if trade['direction'] == 'LONG':
            pnl = (last_bar['close'] - trade['entry']) * trade['size']
        else:
            pnl = (trade['entry'] - last_bar['close']) * trade['size']

        balance += pnl
        trade['exit'] = last_bar['close']
        trade['pnl'] = pnl
        trade['status'] = 'eod'
        trades.append(trade)

    trades_df = pd.DataFrame(trades)
    total_trades = len(trades_df)
    wins = trades_df[trades_df['pnl'] > 0]
    losses = trades_df[trades_df['pnl'] <= 0]
    win_rate = len(wins) / total_trades if total_trades > 0 else 0
    total_profit = wins['pnl'].sum() if len(wins) > 0 else 0
    total_loss = abs(losses['pnl'].sum()) if len(losses) > 0 else 0
    profit_factor = total_profit / total_loss if total_loss > 0 else 0
    total_pnl = balance - 10000

    # Session stats
    asian_trades = trades_df[trades_df['range_type'] == 'asian']
    london_trades = trades_df[trades_df['range_type'] == 'london']
    ny_trades = trades_df[trades_df['range_type'] == 'ny']

    return {
        'total_trades': total_trades,
        'win_rate': win_rate,
        'profit_factor': profit_factor,
        'total_pnl': total_pnl,
        'max_dd': max_dd,
        'max_daily_dd': max_daily_dd,
        'asian_pnl': asian_trades['pnl'].sum() if len(asian_trades) > 0 else 0,
        'london_pnl': london_trades['pnl'].sum() if len(london_trades) > 0 else 0,
        'ny_pnl': ny_trades['pnl'].sum() if len(ny_trades) > 0 else 0,
        'passes_filters': max_dd < 10.0 and max_daily_dd < 5.0 and total_trades >= 150
    }

if __name__ == "__main__":
    print("=" * 100)
    print("=== TRAILING STOP TEST FOR ASIAN AND LONDON ===")
    print(f"Period: {START_DATE} to {END_DATE}")
    print("=" * 100)
    print()

    # Load data
    print("Loading M15 data...")
    df = load_timeframe("M15", start=START_DATE, end=END_DATE, symbol="XAUUSD")
    if 'datetime' in df.columns:
        df.set_index('datetime', inplace=True)
    df = df.sort_index()
    df['atr'] = calculate_atr(df, ATR_PERIOD)

    print("Loading H4 data...")
    h4_end_date = "2024-12-31" if END_DATE > "2024-12-31" else END_DATE
    df_h4 = load_timeframe("H4", start=START_DATE, end=h4_end_date, symbol="XAUUSD")
    if 'datetime' in df_h4.columns:
        df_h4.set_index('datetime', inplace=True)
    df_h4 = df_h4.sort_index()
    df_h4['ema20'] = calculate_ema(df_h4, H4_EMA_PERIOD)
    print()

    # Test baseline
    print("Testing BASELINE (current configuration)...")
    baseline = run_backtest(BASELINE_ASIAN, BASELINE_LONDON, NY_PARAMS, df, df_h4)

    # Test Asian with trailing
    print("Testing ASIAN with TrailStart=3.0, TrailDist=0.3...")
    asian_trail = BASELINE_ASIAN.copy()
    asian_trail['trailing_start'] = 3.0
    asian_trail['trailing_distance'] = 0.3
    variant1 = run_backtest(asian_trail, BASELINE_LONDON, NY_PARAMS, df, df_h4)

    # Test London with trailing
    print("Testing LONDON with TrailStart=3.0, TrailDist=0.3...")
    london_trail = BASELINE_LONDON.copy()
    london_trail['trailing_start'] = 3.0
    london_trail['trailing_distance'] = 0.3
    variant2 = run_backtest(BASELINE_ASIAN, london_trail, NY_PARAMS, df, df_h4)

    # Test both with trailing
    print("Testing BOTH Asian+London with TrailStart=3.0, TrailDist=0.3...")
    variant3 = run_backtest(asian_trail, london_trail, NY_PARAMS, df, df_h4)

    # Print results
    print()
    print("=" * 120)
    print("=== RESULTS TABLE ===")
    print("=" * 120)
    print(f"{'Variant':<35} {'Total PnL':<15} {'Asian PnL':<12} {'London PnL':<13} {'NY PnL':<10} {'PF':<8} {'DD%':<8} {'Status':<8}")
    print("-" * 120)

    results = [
        ("BASELINE (current)", baseline),
        ("Asian Trail=3.0", variant1),
        ("London Trail=3.0", variant2),
        ("Both Trail=3.0", variant3)
    ]

    for name, r in results:
        status = "PASS" if r['passes_filters'] else "FAIL"
        print(f"{name:<35} ${r['total_pnl']:<14,.0f} ${r['asian_pnl']:<11,.0f} ${r['london_pnl']:<12,.0f} ${r['ny_pnl']:<9,.0f} "
              f"{r['profit_factor']:<8.3f} {r['max_dd']:<8.2f} {status:<8}")

    print("=" * 120)

    # Find best
    passed = [r for r in results if r[1]['passes_filters']]
    if len(passed) > 0:
        best = max(passed, key=lambda x: x[1]['total_pnl'])
        print(f"\nBest variant: {best[0]}")
        print(f"Total PnL: ${best[1]['total_pnl']:,.0f}")

        if best[0] != "BASELINE (current)":
            improvement = best[1]['total_pnl'] - baseline['total_pnl']
            improvement_pct = (improvement / baseline['total_pnl']) * 100
            print(f"Improvement vs baseline: ${improvement:,.0f} ({improvement_pct:+.1f}%)")
