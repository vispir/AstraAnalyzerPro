"""
Analyze losing trades to find patterns for filtering
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
import numpy as np
from astra_v2.data.dukascopy import load_timeframe

# Import backtest function
from scripts.combined_session_backtest import run_combined_backtest

# Run backtest
trades_df = run_combined_backtest()

print("\n" + "=" * 100)
print("LOSING TRADES ANALYSIS")
print("=" * 100)

# Separate wins and losses
wins = trades_df[trades_df['pnl'] > 0].copy()
losses = trades_df[trades_df['pnl'] <= 0].copy()

print(f"\nTotal: {len(trades_df)} trades")
print(f"Winners: {len(wins)} ({len(wins)/len(trades_df)*100:.1f}%)")
print(f"Losers: {len(losses)} ({len(losses)/len(trades_df)*100:.1f}%)")

# Add time features
for df_subset in [wins, losses]:
    df_subset['hour'] = pd.to_datetime(df_subset['entry_time']).dt.hour
    df_subset['weekday'] = pd.to_datetime(df_subset['entry_time']).dt.dayofweek
    df_subset['month'] = pd.to_datetime(df_subset['entry_time']).dt.month

print("\n" + "=" * 100)
print("1. ANALYSIS BY SESSION")
print("=" * 100)

for session in ['asian', 'london', 'ny']:
    session_wins = wins[wins['range_type'] == session]
    session_losses = losses[losses['range_type'] == session]
    total_session = len(session_wins) + len(session_losses)

    if total_session > 0:
        wr = len(session_wins) / total_session * 100
        win_pnl = session_wins['pnl'].sum()
        loss_pnl = session_losses['pnl'].sum()
        net = win_pnl + loss_pnl

        print(f"\n{session.upper()}:")
        print(f"  Win Rate: {wr:.1f}%")
        print(f"  Winners: {len(session_wins)}, PnL: ${win_pnl:,.0f}")
        print(f"  Losers: {len(session_losses)}, PnL: ${loss_pnl:,.0f}")
        print(f"  Net: ${net:,.0f}")

print("\n" + "=" * 100)
print("2. ANALYSIS BY DIRECTION")
print("=" * 100)

for direction in ['LONG', 'SHORT']:
    dir_wins = wins[wins['direction'] == direction]
    dir_losses = losses[losses['direction'] == direction]
    total_dir = len(dir_wins) + len(dir_losses)

    if total_dir > 0:
        wr = len(dir_wins) / total_dir * 100
        win_pnl = dir_wins['pnl'].sum()
        loss_pnl = dir_losses['pnl'].sum()
        net = win_pnl + loss_pnl

        print(f"\n{direction}:")
        print(f"  Win Rate: {wr:.1f}%")
        print(f"  Winners: {len(dir_wins)}, PnL: ${win_pnl:,.0f}")
        print(f"  Losers: {len(dir_losses)}, PnL: ${loss_pnl:,.0f}")
        print(f"  Net: ${net:,.0f}")

print("\n" + "=" * 100)
print("3. ANALYSIS BY WEEKDAY")
print("=" * 100)

weekday_names = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']
print("\nWin rate by weekday:")
weekday_stats = []
trades_df['weekday'] = pd.to_datetime(trades_df['entry_time']).dt.dayofweek

for day in range(7):
    day_trades = trades_df[trades_df['weekday'] == day]

    if len(day_trades) > 0:
        day_wins = day_trades[day_trades['pnl'] > 0]
        wr = len(day_wins) / len(day_trades) * 100
        weekday_stats.append({
            'weekday': weekday_names[day],
            'trades': len(day_trades),
            'wr': wr,
            'net_pnl': day_trades['pnl'].sum()
        })

weekday_stats_df = pd.DataFrame(weekday_stats)
print(weekday_stats_df.to_string(index=False))

print("\n" + "=" * 100)
print("4. POTENTIAL FILTERS")
print("=" * 100)

print("\nBased on analysis, potential filters:")
print("\n1. WEEKDAY FILTER:")
worst_days = weekday_stats_df.nsmallest(2, 'wr')
print(f"   Skip worst days: {', '.join(worst_days['weekday'].values)}")
print(f"   Would remove: {worst_days['trades'].sum()} trades")
print(f"   Impact on PnL: ${worst_days['net_pnl'].sum():,.0f}")

print("\n2. SESSION FILTER:")
session_stats = []
for session in ['asian', 'london', 'ny']:
    session_trades = trades_df[trades_df['range_type'] == session]
    session_wins = session_trades[session_trades['pnl'] > 0]
    wr = len(session_wins) / len(session_trades) * 100 if len(session_trades) > 0 else 0
    session_stats.append({
        'session': session,
        'trades': len(session_trades),
        'wr': wr,
        'net_pnl': session_trades['pnl'].sum()
    })

session_stats_df = pd.DataFrame(session_stats).sort_values('wr')
worst_session = session_stats_df.iloc[0]
print(f"   Skip worst session: {worst_session['session'].upper()}")
print(f"   Would remove: {worst_session['trades']} trades")
print(f"   Impact on PnL: ${worst_session['net_pnl']:,.0f}")

print("\n3. DIRECTION FILTER:")
for direction in ['LONG', 'SHORT']:
    dir_trades = trades_df[trades_df['direction'] == direction]
    dir_wins = dir_trades[dir_trades['pnl'] > 0]
    wr = len(dir_wins) / len(dir_trades) * 100 if len(dir_trades) > 0 else 0
    net_pnl = dir_trades['pnl'].sum()
    print(f"   {direction}: WR={wr:.1f}%, Net=${net_pnl:,.0f}")

print("\n" + "=" * 100)
print("RECOMMENDATION")
print("=" * 100)
print("\nTest filters to improve PnL while keeping trades >= 150")
