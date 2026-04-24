"""
Combined Trading Strategy Backtest: LONG + SHORT
================================================

LONG Strategy: Session Breakout
- Entry: Breakout above session high (Asian 07-10, London 13-16, NY 18-21 UTC)
- Filter: H4 close > EMA20
- Risk: $158 per trade
- TP: 5.5R
- SL: Session low - 0.5 ATR
- Step Trailing: 2R→1R, 3R→2R, 4R→3R, 5R→4R

SHORT Strategy: Reversal (Type 1 + Type 2)
- Type 1: Reversal After Historical High
  - New high over last 5 H4 bars
  - 1 H4 bar closes lower
  - M15 breaks below previous M15 low
- Type 2: Local Reversal After Strong Move
  - Price rises 2+ ATR in last 3 H4 bars
  - Current H4 closes lower than previous
  - M15 breaks below previous M15 low
- Filter: H4 close < EMA20
- Risk: $158 per trade
- TP: 5.5R
- SL: Reversal H4 high + 0.5 ATR
- Step Trailing: 2R→1R, 3R→2R, 4R→3R, 5R→4R

Backtest Period: 2020-01-01 to 2026-04-18
Symbol: XAUUSD
Timeframe: M15 (with H4 resampling)
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
import numpy as np
from datetime import datetime
from astra_v2.data.dukascopy import load_timeframe

# ============================================================================
# STRATEGY PARAMETERS
# ============================================================================

# Common parameters
RISK_PER_TRADE = 158
TP_RR = 5.5
ATR_PERIOD = 14
ATR_BUFFER = 0.5
START_DATE = "2020-01-01"
END_DATE = "2026-04-18"

# LONG: Session Breakout parameters
LONG_SESSIONS = {
    'asian': (7, 10),
    'london': (13, 16),
    'ny': (18, 21)
}

# SHORT: Reversal parameters
SHORT_TYPE1_LOOKBACK_H4_BARS = 5
SHORT_TYPE1_H4_REVERSAL_BARS = 1
SHORT_TYPE2_H4_LOOKBACK = 3
SHORT_TYPE2_ATR_MULTIPLIER = 2.0

# Step Trailing Stop
USE_STEP_TRAILING = True

# H4 EMA20 Filter
USE_H4_EMA_FILTER = True
H4_EMA_PERIOD = 20

# ============================================================================
# TECHNICAL INDICATORS
# ============================================================================

def calculate_atr(df, period=14):
    """Calculate Average True Range"""
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
    """Calculate Exponential Moving Average"""
    return df['close'].ewm(span=period, adjust=False).mean()

# ============================================================================
# TRADE MANAGEMENT
# ============================================================================

def apply_step_trailing(active_trade, current_low, current_high, is_long=True):
    """
    Apply step trailing stop logic

    LONG: 2R→1R, 3R→2R, 4R→3R, 5R→4R
    SHORT: 2R→1R, 3R→2R, 4R→3R, 5R→4R
    """
    if not USE_STEP_TRAILING:
        return

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
        profit_in_r = (active_trade['entry'] - current_low) / risk

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

# ============================================================================
# BACKTEST ENGINE
# ============================================================================

def run_combined_backtest(df, df_h4):
    """
    Run combined LONG + SHORT backtest

    Returns:
        dict: Backtest results including trades, PnL, DD, WR, etc.
    """
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
                        'direction': 'LONG'
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
                        'direction': 'LONG'
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
                        'direction': 'SHORT',
                        'type': active_short['type']
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
                        'direction': 'SHORT',
                        'type': active_short['type']
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
                                'entry_time': times[i],
                                'type': 'Type1_HistoricalHigh'
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
                                'entry_time': times[i],
                                'type': 'Type2_LocalReversal'
                            }
                            short_type2_reversal_active = False

        # Calculate daily DD
        if day_start_balance > 0:
            daily_dd = (day_start_balance - balance) / day_start_balance * 100
            if daily_dd > max_daily_dd:
                max_daily_dd = daily_dd

    # Close any remaining active trades
    if active_long is not None:
        last_bar = df.iloc[-1]
        pnl = (last_bar['close'] - active_long['entry']) * active_long['size']
        balance += pnl
        trades.append({
            'entry_time': active_long['entry_time'],
            'exit_time': df.index[-1],
            'entry_price': active_long['entry'],
            'exit_price': last_bar['close'],
            'sl': active_long['initial_sl'],
            'tp': active_long['tp'],
            'pnl': pnl,
            'exit_reason': 'eod',
            'direction': 'LONG'
        })

    if active_short is not None:
        last_bar = df.iloc[-1]
        pnl = (active_short['entry'] - last_bar['close']) * active_short['size']
        balance += pnl
        trades.append({
            'entry_time': active_short['entry_time'],
            'exit_time': df.index[-1],
            'entry_price': active_short['entry'],
            'exit_price': last_bar['close'],
            'sl': active_short['initial_sl'],
            'tp': active_short['tp'],
            'pnl': pnl,
            'exit_reason': 'eod',
            'direction': 'SHORT',
            'type': active_short['type']
        })

    if len(trades) == 0:
        return None

    trades_df = pd.DataFrame(trades)

    # Calculate statistics
    total_trades = len(trades_df)
    wins = trades_df[trades_df['pnl'] > 0]
    losses = trades_df[trades_df['pnl'] <= 0]

    win_rate = len(wins) / total_trades if total_trades > 0 else 0

    total_profit = wins['pnl'].sum() if len(wins) > 0 else 0
    total_loss = abs(losses['pnl'].sum()) if len(losses) > 0 else 0
    profit_factor = total_profit / total_loss if total_loss > 0 else 0

    total_pnl = balance - 10000

    # LONG stats
    long_trades = trades_df[trades_df['direction'] == 'LONG']
    long_wins = long_trades[long_trades['pnl'] > 0]
    long_wr = len(long_wins) / len(long_trades) if len(long_trades) > 0 else 0
    long_pnl = long_trades['pnl'].sum() if len(long_trades) > 0 else 0

    # SHORT stats
    short_trades = trades_df[trades_df['direction'] == 'SHORT']
    short_wins = short_trades[short_trades['pnl'] > 0]
    short_wr = len(short_wins) / len(short_trades) if len(short_trades) > 0 else 0
    short_pnl = short_trades['pnl'].sum() if len(short_trades) > 0 else 0

    # SHORT Type 1 stats
    short_type1 = short_trades[short_trades['type'] == 'Type1_HistoricalHigh']
    short_type1_pnl = short_type1['pnl'].sum() if len(short_type1) > 0 else 0

    # SHORT Type 2 stats
    short_type2 = short_trades[short_trades['type'] == 'Type2_LocalReversal']
    short_type2_pnl = short_type2['pnl'].sum() if len(short_type2) > 0 else 0

    return {
        'total_trades': total_trades,
        'win_rate': win_rate,
        'profit_factor': profit_factor,
        'max_drawdown_pct': max_dd,
        'max_daily_dd': max_daily_dd,
        'total_pnl': total_pnl,
        'final_balance': balance,
        'long_trades': len(long_trades),
        'long_wr': long_wr,
        'long_pnl': long_pnl,
        'short_trades': len(short_trades),
        'short_wr': short_wr,
        'short_pnl': short_pnl,
        'short_type1_trades': len(short_type1),
        'short_type1_pnl': short_type1_pnl,
        'short_type2_trades': len(short_type2),
        'short_type2_pnl': short_type2_pnl,
        'trades_df': trades_df
    }

# ============================================================================
# MAIN
# ============================================================================

def main():
    print("=" * 120)
    print("COMBINED TRADING STRATEGY BACKTEST")
    print("=" * 120)
    print(f"Period: {START_DATE} to {END_DATE}")
    print(f"Symbol: XAUUSD")
    print(f"Risk per trade: ${RISK_PER_TRADE}")
    print(f"TP: {TP_RR}R")
    print(f"Step Trailing: 2R->1R, 3R->2R, 4R->3R, 5R->4R")
    print(f"\nLONG Strategy: Session Breakout (H4 close > EMA20)")
    print(f"  Sessions: Asian 07-10, London 13-16, NY 18-21 UTC")
    print(f"\nSHORT Strategy: Reversal Type 1 + Type 2 (H4 close < EMA20)")
    print(f"  Type 1: Historical High Reversal (Lookback={SHORT_TYPE1_LOOKBACK_H4_BARS} H4)")
    print(f"  Type 2: Local Reversal ({SHORT_TYPE2_ATR_MULTIPLIER}+ ATR in {SHORT_TYPE2_H4_LOOKBACK} H4 bars)")
    print("=" * 120)

    # Load M15 data
    print("\nLoading M15 data...")
    df = load_timeframe("m15", start=START_DATE, end=END_DATE, symbol="XAUUSD")
    print(f"Loaded {len(df):,} M15 bars")

    if 'datetime' in df.columns:
        df.set_index('datetime', inplace=True)
    df = df.sort_index()

    # Calculate ATR
    df['atr'] = calculate_atr(df, ATR_PERIOD)

    # Resample to H4
    print("Resampling M15 to H4...")
    df_h4 = df.resample('4h').agg({'open':'first','high':'max','low':'min','close':'last'}).dropna()
    df_h4['ema20'] = calculate_ema(df_h4, H4_EMA_PERIOD)
    df_h4['atr'] = calculate_atr(df_h4, ATR_PERIOD)
    print(f"Resampled {len(df_h4):,} H4 bars")

    # Run backtest
    print("\nRunning backtest...")
    result = run_combined_backtest(df, df_h4)

    if result is None:
        print("No trades generated!")
        return

    # Print results
    print("\n" + "=" * 120)
    print("BACKTEST RESULTS")
    print("=" * 120)

    print("\nTRADE STATISTICS:")
    print("-" * 120)
    print(f"Total Trades:      {result['total_trades']}")
    print(f"  LONG Trades:     {result['long_trades']} ({result['long_trades']/result['total_trades']*100:.1f}%)")
    print(f"  SHORT Trades:    {result['short_trades']} ({result['short_trades']/result['total_trades']*100:.1f}%)")
    print(f"    Type 1:        {result['short_type1_trades']}")
    print(f"    Type 2:        {result['short_type2_trades']}")

    print("\nPROFITABILITY:")
    print("-" * 120)
    print(f"Total PnL:         ${result['total_pnl']:,.2f}")
    print(f"  LONG PnL:        ${result['long_pnl']:,.2f} ({result['long_pnl']/result['total_pnl']*100:.1f}%)")
    print(f"  SHORT PnL:       ${result['short_pnl']:,.2f} ({result['short_pnl']/result['total_pnl']*100:.1f}%)")
    print(f"    Type 1:        ${result['short_type1_pnl']:,.2f}")
    print(f"    Type 2:        ${result['short_type2_pnl']:,.2f}")
    print(f"\nFinal Balance:     ${result['final_balance']:,.2f}")
    print(f"Return:            {(result['final_balance']/10000 - 1)*100:.1f}%")

    print("\nQUALITY METRICS:")
    print("-" * 120)
    print(f"Win Rate:          {result['win_rate']:.1%}")
    print(f"  LONG WR:         {result['long_wr']:.1%}")
    print(f"  SHORT WR:        {result['short_wr']:.1%}")
    print(f"\nProfit Factor:     {result['profit_factor']:.2f}")
    print(f"Max Drawdown:      {result['max_drawdown_pct']:.2f}%")
    print(f"Max Daily DD:      {result['max_daily_dd']:.2f}%")

    print("\nFREQUENCY:")
    print("-" * 120)
    years = (pd.to_datetime(END_DATE) - pd.to_datetime(START_DATE)).days / 365.25
    print(f"Trades per year:   {result['total_trades']/years:.1f}")
    print(f"  LONG:            {result['long_trades']/years:.1f}")
    print(f"  SHORT:           {result['short_trades']/years:.1f}")

    print("\n" + "=" * 120)
    print("TARGET VALIDATION")
    print("=" * 120)

    target_pnl = 50000
    target_dd = 10.0
    target_daily_dd = 5.0

    pnl_pass = result['total_pnl'] > target_pnl
    dd_pass = result['max_drawdown_pct'] < target_dd
    daily_dd_pass = result['max_daily_dd'] < target_daily_dd

    print(f"PnL Target (>${target_pnl:,}):        {'PASS' if pnl_pass else 'FAIL'} (${result['total_pnl']:,.2f})")
    print(f"Max DD Target (<{target_dd}%):      {'PASS' if dd_pass else 'FAIL'} ({result['max_drawdown_pct']:.2f}%)")
    print(f"Daily DD Target (<{target_daily_dd}%):    {'PASS' if daily_dd_pass else 'FAIL'} ({result['max_daily_dd']:.2f}%)")

    overall_pass = pnl_pass and dd_pass and daily_dd_pass
    print(f"\nOVERALL STATUS:    {'ALL TARGETS MET' if overall_pass else 'TARGETS NOT MET'}")
    print("=" * 120)

if __name__ == "__main__":
    main()
