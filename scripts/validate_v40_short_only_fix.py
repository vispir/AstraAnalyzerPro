"""
Бэктест v4.0 — исправлен look-ahead ТОЛЬКО в SHORT state machine.
LONG EMA фильтр остался как в оригинале.

Цель: изолировать влияние look-ahead именно на SHORT стратегию.
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from session_breakout_trader import (
    RISK_PER_TRADE, TP_RR, ATR_PERIOD, ATR_BUFFER,
    USE_H4_EMA_FILTER, H4_EMA_PERIOD,
    LONG_SESSIONS,
    SHORT_TYPE1_LOOKBACK_H4_BARS, SHORT_TYPE2_H4_LOOKBACK, SHORT_TYPE2_ATR_MULTIPLIER,
    calculate_atr, calculate_ema
)

import pandas as pd
import numpy as np
from pathlib import Path

def apply_step_trailing(active_trade, current_low, current_high, is_long=True):
    if is_long:
        risk = active_trade['entry'] - active_trade['initial_sl']
        profit_in_r = (current_low - active_trade['entry']) / risk
        if profit_in_r >= 5.0:
            active_trade['sl'] = max(active_trade['sl'], active_trade['entry'] + 4.0 * risk)
        elif profit_in_r >= 4.0:
            active_trade['sl'] = max(active_trade['sl'], active_trade['entry'] + 3.0 * risk)
        elif profit_in_r >= 3.0:
            active_trade['sl'] = max(active_trade['sl'], active_trade['entry'] + 2.0 * risk)
        elif profit_in_r >= 2.0:
            active_trade['sl'] = max(active_trade['sl'], active_trade['entry'] + 1.0 * risk)
    else:
        risk = active_trade['initial_sl'] - active_trade['entry']
        profit_in_r = (active_trade['entry'] - current_high) / risk
        if profit_in_r >= 5.0:
            active_trade['sl'] = min(active_trade['sl'], active_trade['entry'] - 4.0 * risk)
        elif profit_in_r >= 4.0:
            active_trade['sl'] = min(active_trade['sl'], active_trade['entry'] - 3.0 * risk)
        elif profit_in_r >= 3.0:
            active_trade['sl'] = min(active_trade['sl'], active_trade['entry'] - 2.0 * risk)
        elif profit_in_r >= 2.0:
            active_trade['sl'] = min(active_trade['sl'], active_trade['entry'] - 1.0 * risk)

def run():
    print("="*80)
    print("БЭКТЕСТ v4.0 — SHORT look-ahead убран, LONG оригинал")
    print("="*80)

    data_path = Path(__file__).parent.parent / "data_cache" / "dukascopy" / "m15" / "XAUUSD" / "xauusd_m15_2020-01-01_2026-04-18.parquet"
    df = pd.read_parquet(data_path)
    df = df.sort_index()

    df['atr'] = calculate_atr(df, ATR_PERIOD)
    df_h4 = df.resample('4h').agg({'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last'}).dropna()
    df_h4['atr'] = calculate_atr(df_h4, ATR_PERIOD)
    df_h4['ema20'] = calculate_ema(df_h4, H4_EMA_PERIOD)

    h4_offset = pd.Timedelta(hours=4)

    trades = []
    active_long_trades = {}
    active_short = None
    balance = 10000
    peak_balance = 10000
    max_dd = 0
    max_daily_dd = 0

    short_type1_active = False
    short_type1_h4_high = None
    short_type2_active = False
    short_type2_h4_high = None
    last_h4_index = None

    for date in sorted(set(df.index.date)):
        day_start_balance = balance
        day_data = df[df.index.date == date]
        if len(day_data) < 10:
            continue

        session_highs = {}
        session_lows = {}
        highs  = day_data['high'].to_numpy()
        lows   = day_data['low'].to_numpy()
        closes = day_data['close'].to_numpy()
        atrs   = day_data['atr'].to_numpy()
        hours  = np.array([t.hour for t in day_data.index])
        times  = day_data.index.to_numpy()

        for i in range(len(day_data)):
            current_time = times[i]
            hour = hours[i]
            atr  = atrs[i]
            if np.isnan(atr):
                continue

            # LONG uses original h4_bars (with look-ahead, same as original backtest)
            h4_bars_long = df_h4[df_h4.index <= current_time]
            # SHORT uses only completed H4 bars (no look-ahead)
            h4_bars_short = df_h4[df_h4.index + h4_offset <= current_time]

            if len(h4_bars_long) < max(SHORT_TYPE1_LOOKBACK_H4_BARS + 2, SHORT_TYPE2_H4_LOOKBACK + 1):
                continue

            current_h4_long = h4_bars_long.iloc[-1]

            # Manage LONG trades
            for sn in list(active_long_trades.keys()):
                t = active_long_trades[sn]
                apply_step_trailing(t, lows[i], highs[i], is_long=True)
                if lows[i] <= t['sl']:
                    t['pnl'] = (t['sl'] - t['entry']) * t['size']
                    balance += t['pnl']; t['year'] = current_time.year
                    trades.append(t); del active_long_trades[sn]
                elif highs[i] >= t['tp']:
                    t['pnl'] = (t['tp'] - t['entry']) * t['size']
                    balance += t['pnl']; t['year'] = current_time.year
                    trades.append(t); del active_long_trades[sn]

            # Manage SHORT trade
            if active_short is not None:
                apply_step_trailing(active_short, lows[i], highs[i], is_long=False)
                if highs[i] >= active_short['sl']:
                    active_short['pnl'] = (active_short['entry'] - active_short['sl']) * active_short['size']
                    balance += active_short['pnl']; active_short['year'] = current_time.year
                    trades.append(active_short); active_short = None
                    short_type1_active = False; short_type2_active = False
                elif lows[i] <= active_short['tp']:
                    active_short['pnl'] = (active_short['entry'] - active_short['tp']) * active_short['size']
                    balance += active_short['pnl']; active_short['year'] = current_time.year
                    trades.append(active_short); active_short = None
                    short_type1_active = False; short_type2_active = False

            # DD
            if balance > peak_balance: peak_balance = balance
            dd = (peak_balance - balance) / peak_balance * 100
            if dd > max_dd: max_dd = dd

            # Session ranges
            for sn, params in LONG_SESSIONS.items():
                sh, eh = params['range_hours']
                if sh <= hour < eh:
                    session_highs[sn] = max(session_highs.get(sn, 0), highs[i])
                    session_lows[sn]  = min(session_lows.get(sn, 1e9), lows[i])

            # LONG entries (original logic)
            for sn, params in LONG_SESSIONS.items():
                if sn not in session_highs or sn in active_long_trades:
                    continue
                if not (params['entry_start'] <= hour < params['entry_end']):
                    continue
                if closes[i] > session_highs[sn]:
                    if USE_H4_EMA_FILTER:
                        if pd.isna(current_h4_long['ema20']) or current_h4_long['close'] < current_h4_long['ema20']:
                            continue
                    sl   = session_lows[sn] - ATR_BUFFER * atr
                    risk = closes[i] - sl
                    if risk <= 0: continue
                    tp = closes[i] + risk * TP_RR
                    active_long_trades[sn] = {
                        'entry': closes[i], 'sl': sl, 'initial_sl': sl,
                        'tp': tp, 'size': RISK_PER_TRADE / risk,
                        'direction': 'LONG', 'session': sn
                    }

            # SHORT state machine — NO LOOK-AHEAD (completed bars only)
            if active_short is None and hour < 21 and len(h4_bars_short) >= SHORT_TYPE1_LOOKBACK_H4_BARS + 2:
                current_h4_short = h4_bars_short.iloc[-1]
                prev_h4_short    = h4_bars_short.iloc[-2]
                cur_idx = current_h4_short.name

                if last_h4_index != cur_idx:
                    last_h4_index = cur_idx
                    if USE_H4_EMA_FILTER:
                        if pd.isna(current_h4_short['ema20']) or current_h4_short['close'] >= current_h4_short['ema20']:
                            short_type1_active = False; short_type2_active = False
                            continue

                    if not short_type1_active:
                        lookback = h4_bars_short.iloc[-SHORT_TYPE1_LOOKBACK_H4_BARS-1:-1]['high']
                        if current_h4_short['high'] > lookback.max() and current_h4_short['close'] < prev_h4_short['close']:
                            short_type1_active = True
                            short_type1_h4_high = current_h4_short['high']

                    if not short_type2_active and len(h4_bars_short) >= SHORT_TYPE2_H4_LOOKBACK + 2:
                        lookback_bars = h4_bars_short.iloc[-SHORT_TYPE2_H4_LOOKBACK-1:-1]
                        move = current_h4_short['high'] - lookback_bars['low'].min()
                        h4_atr = current_h4_short.get('atr', atr)
                        if not np.isnan(h4_atr) and move >= SHORT_TYPE2_ATR_MULTIPLIER * h4_atr:
                            if current_h4_short['close'] < prev_h4_short['close']:
                                short_type2_active = True
                                short_type2_h4_high = current_h4_short['high']

                if i > 0:
                    prev_low = lows[i-1]
                    if short_type1_active and closes[i] < prev_low:
                        sl = short_type1_h4_high + ATR_BUFFER * atr
                        risk = sl - closes[i]
                        if risk > 0:
                            active_short = {
                                'entry': closes[i], 'sl': sl, 'initial_sl': sl,
                                'tp': closes[i] - risk * TP_RR,
                                'size': RISK_PER_TRADE / risk,
                                'direction': 'SHORT', 'session': 'short'
                            }
                            short_type1_active = False
                    elif short_type2_active and closes[i] < prev_low:
                        sl = short_type2_h4_high + ATR_BUFFER * atr
                        risk = sl - closes[i]
                        if risk > 0:
                            active_short = {
                                'entry': closes[i], 'sl': sl, 'initial_sl': sl,
                                'tp': closes[i] - risk * TP_RR,
                                'size': RISK_PER_TRADE / risk,
                                'direction': 'SHORT', 'session': 'short'
                            }
                            short_type2_active = False

        if day_start_balance > 0:
            ddaily = (day_start_balance - balance) / day_start_balance * 100
            if ddaily > max_daily_dd: max_daily_dd = ddaily

    # Close remaining
    last_bar = df.iloc[-1]
    for sn, t in active_long_trades.items():
        t['pnl'] = (last_bar['close'] - t['entry']) * t['size']
        balance += t['pnl']; t['year'] = df.index[-1].year; trades.append(t)
    if active_short is not None:
        active_short['pnl'] = (active_short['entry'] - last_bar['close']) * active_short['size']
        balance += active_short['pnl']; active_short['year'] = df.index[-1].year; trades.append(active_short)

    tdf = pd.DataFrame(trades)
    total_pnl = balance - 10000
    wr = len(tdf[tdf['pnl'] > 0]) / len(tdf)
    long_df  = tdf[tdf['direction'] == 'LONG']
    short_df = tdf[tdf['direction'] == 'SHORT']
    yearly   = tdf.groupby('year')['pnl'].sum()

    print(f"\nTotal Trades: {len(tdf)}   Win Rate: {wr:.1%}")
    print(f"Total PnL:   ${total_pnl:,.0f}")
    print(f"Max DD:      {max_dd:.2f}%   Max Daily DD: {max_daily_dd:.2f}%")
    print()
    print("BREAKDOWN:")
    print(f"  LONG:  {len(long_df)} trades,  ${long_df['pnl'].sum():,.0f}")
    print(f"  SHORT: {len(short_df)} trades, ${short_df['pnl'].sum():,.0f}")
    print()
    print("PNL BY YEAR:")
    for yr in sorted(yearly.keys()):
        print(f"  {yr}: ${yearly[yr]:,.0f}")
    print(f"\nAll years profitable: {'YES' if all(yearly > 0) else 'NO'}")
    print()
    print("="*80)
    print("СРАВНЕНИЕ:")
    print(f"  Оригинал (look-ahead): SHORT {188} сделок  ${19320:,}   LONG {693} сделок  ${61181:,}")
    print(f"  Без look-ahead SHORT:  SHORT {len(short_df)} сделок  ${short_df['pnl'].sum():,.0f}   LONG {len(long_df)} сделок  ${long_df['pnl'].sum():,.0f}")
    print("="*80)

if __name__ == "__main__":
    run()
