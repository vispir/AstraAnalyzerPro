# -*- coding: utf-8 -*-
"""
Strategy Search: test 3 fundamentally different strategies on XAUUSD M15 2020-2026
Compare: EMA Pullback, Session Fade, S/R Bounce
"""
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

import pandas as pd
import numpy as np
import warnings
warnings.filterwarnings('ignore')

M15_FILE = r"D:\Works\ASTRA ANALYZER CHART\data_cache\dukascopy\m15\XAUUSD\xauusd_m15_2020-01-01_2026-05-12.parquet"
ATR_PERIOD = 14
RISK_USD   = 100.0

# ── Load & prepare data ────────────────────────────────────────────────────
print("Loading data...")
df = pd.read_parquet(M15_FILE)
df.index = pd.to_datetime(df.index, utc=True)
df = df.sort_index()
df.columns = [c.lower() for c in df.columns]

# ATR
df['tr']  = np.maximum(df['high']-df['low'],
            np.maximum(abs(df['high']-df['close'].shift(1)),
                       abs(df['low'] -df['close'].shift(1))))
df['atr'] = df['tr'].ewm(alpha=1/ATR_PERIOD, adjust=False).mean()

# H4 EMA + slope
h4 = df.resample('4h', origin='epoch').agg(
    open=('open','first'), high=('high','max'),
    low=('low','min'),  close=('close','last')).dropna()
h4['ema20']    = h4['close'].ewm(span=20, adjust=False).mean()
h4['ema50']    = h4['close'].ewm(span=50, adjust=False).mean()
h4['slope']    = h4['ema20'] - h4['ema20'].shift(3)
h4['h4_up']    = (h4['close'] > h4['ema20']) & (h4['slope'] > 0)
h4['h4_down']  = (h4['close'] < h4['ema20']) & (h4['slope'] < 0)
h4['ema20_val']= h4['ema20']
h4['ema50_val']= h4['ema50']

# S/R: H4 swing highs/lows (local max/min over 5 bars)
h4['swing_high'] = h4['high'][(h4['high'] == h4['high'].rolling(5, center=True).max())]
h4['swing_low']  = h4['low'][ (h4['low']  == h4['low'].rolling(5, center=True).min())]

# Daily trend
d1 = df.resample('1D', origin='epoch').agg(close=('close','last')).dropna()
d1['d1_ema20'] = d1['close'].ewm(span=20, adjust=False).mean()
d1['d1_up']    = d1['close'] > d1['d1_ema20']

def map_h4(s): return s.shift(1).reindex(df.index, method='ffill')
def map_d1(s): return s.shift(1).reindex(df.index, method='ffill')

df['h4_up']     = map_h4(h4['h4_up'])
df['h4_down']   = map_h4(h4['h4_down'])
df['h4_ema20']  = map_h4(h4['ema20_val'])
df['h4_ema50']  = map_h4(h4['ema50_val'])
df['h4_slope']  = map_h4(h4['slope'])
df['d1_up']     = map_d1(d1['d1_up'])
df['dow']       = df.index.dayofweek
df['hour']      = df.index.hour

print(f"  Bars: {len(df)}, {df.index[0].date()} to {df.index[-1].date()}")

# ── Backtest engine ────────────────────────────────────────────────────────
def simulate_trade(df, entry_idx, direction, sl_dist, tp_rr,
                   trail_start_r=1.5, trail_step_r=0.5, max_bars=500):
    """
    Simulate a trade with step trailing stop.
    trail_start_r: activate trailing after this many R in profit
    trail_step_r: trail step in R
    Returns: (pnl_r, bars_held, exit_reason)
    """
    entry = df['close'].iloc[entry_idx]
    sl    = entry - sl_dist if direction == 'long' else entry + sl_dist
    tp    = entry + tp_rr * sl_dist if direction == 'long' else entry - tp_rr * sl_dist

    current_sl  = sl
    trail_active = False
    best_r       = 0.0

    future = df.iloc[entry_idx+1 : entry_idx+1+max_bars]

    for _, bar in future.iterrows():
        if direction == 'long':
            # Check SL
            if bar['low'] <= current_sl:
                r = (current_sl - entry) / sl_dist
                return r * RISK_USD, _, 'sl'
            # Check TP
            if bar['high'] >= tp:
                return tp_rr * RISK_USD, _, 'tp'
            # Update trailing
            bar_r = (bar['high'] - entry) / sl_dist
            if bar_r > best_r:
                best_r = bar_r
            if best_r >= trail_start_r:
                trail_active = True
                new_sl = entry + (best_r - trail_step_r) * sl_dist
                if new_sl > current_sl:
                    current_sl = new_sl
        else:
            if bar['high'] >= current_sl:
                r = (entry - current_sl) / sl_dist
                return r * RISK_USD, _, 'sl'
            if bar['low'] <= tp:
                return tp_rr * RISK_USD, _, 'tp'
            bar_r = (entry - bar['low']) / sl_dist
            if bar_r > best_r:
                best_r = bar_r
            if best_r >= trail_start_r:
                trail_active = True
                new_sl = entry - (best_r - trail_step_r) * sl_dist
                if new_sl < current_sl:
                    current_sl = new_sl

    r = (current_sl - entry) / sl_dist if direction=='long' else (entry - current_sl) / sl_dist
    return r * RISK_USD, _, 'timeout'

# ══════════════════════════════════════════════════════════════════════════
# STRATEGY 1: EMA PULLBACK
# Long: H4 uptrend, price pulls back within 0.5 ATR of H4 EMA20, then
#       M15 closes bullish above EMA (confirmation)
# Short: mirror logic
# TP=3R, SL=1ATR, trailing from 1.5R
# ══════════════════════════════════════════════════════════════════════════
print("\nStrategy 1: EMA Pullback...")

trades_s1 = []
in_trade = {'long': False, 'short': False}
last_trade_date = {'long': None, 'short': None}

for i in range(50, len(df)-500):
    row = df.iloc[i]
    prev = df.iloc[i-1]
    date = df.index[i].date()
    dow  = row['dow']

    if dow == 4:  # skip Friday
        continue

    atr = row['atr']
    if atr <= 0 or pd.isna(row['h4_ema20']):
        continue

    # --- LONG: H4 uptrend, price near H4 EMA20 (pullback zone) ---
    if row['h4_up'] and row['d1_up']:
        ema = row['h4_ema20']
        dist_to_ema = row['close'] - ema  # positive = above EMA

        # Price pulled back close to EMA (within 0.3 ATR above it)
        near_ema = 0 <= dist_to_ema <= 0.5 * atr

        # M15 bullish confirmation: close > open, reasonable body
        m15_bull = (row['close'] > row['open']) and \
                   ((row['close'] - row['open']) > 0.3 * (row['high'] - row['low']))

        # Previous bar touched or dipped below EMA (actual pullback)
        prev_dipped = prev['low'] <= ema + 0.3 * atr

        if near_ema and m15_bull and prev_dipped:
            if last_trade_date.get('long') != date:  # one per day
                sl_dist = atr
                pnl, _, reason = simulate_trade(df, i, 'long', sl_dist, tp_rr=3.0,
                                                trail_start_r=1.5, trail_step_r=0.5)
                trades_s1.append({
                    'date': date, 'direction': 'long', 'session': 'ema_pull',
                    'pnl': pnl, 'outcome': 'win' if pnl > 0 else 'loss',
                    'reason': reason, 'hour': row['hour'], 'dow': dow,
                    'atr': atr, 'strategy': 'S1_EMA_Pullback'
                })
                last_trade_date['long'] = date

    # --- SHORT: H4 downtrend, price pulled back near EMA ---
    if row['h4_down'] and not row['d1_up']:
        ema = row['h4_ema20']
        dist_to_ema = ema - row['close']  # positive = below EMA

        near_ema = 0 <= dist_to_ema <= 0.5 * atr
        m15_bear = (row['close'] < row['open']) and \
                   ((row['open'] - row['close']) > 0.3 * (row['high'] - row['low']))
        prev_dipped = prev['high'] >= ema - 0.3 * atr

        if near_ema and m15_bear and prev_dipped:
            if last_trade_date.get('short') != date:
                sl_dist = atr
                pnl, _, reason = simulate_trade(df, i, 'short', sl_dist, tp_rr=3.0,
                                                trail_start_r=1.5, trail_step_r=0.5)
                trades_s1.append({
                    'date': date, 'direction': 'short', 'session': 'ema_pull',
                    'pnl': pnl, 'outcome': 'win' if pnl > 0 else 'loss',
                    'reason': reason, 'hour': row['hour'], 'dow': dow,
                    'atr': atr, 'strategy': 'S1_EMA_Pullback'
                })
                last_trade_date['short'] = date

T1 = pd.DataFrame(trades_s1)
print(f"  Trades: {len(T1)}")

# ══════════════════════════════════════════════════════════════════════════
# STRATEGY 2: SESSION OPEN FADE
# First 15-30 min of London or NY gives a spike > 1.5 ATR in one direction
# Fade it: enter opposite after spike with M15 reversal candle
# TP=2R, SL=0.5 spike size
# ══════════════════════════════════════════════════════════════════════════
print("Strategy 2: Session Open Fade...")

trades_s2 = []
FADE_SESSIONS = {'london': 8, 'ny': 15}

dates = sorted(set(df.index.normalize()))
for date in dates:
    for sess, h_open in FADE_SESSIONS.items():
        # Get first 2 bars of session (open + 15 min)
        open_mask = (df.index.date == date.date()) & \
                    (df.index.hour == h_open) & (df.index.minute == 0)
        open_bars = df[open_mask]
        if len(open_bars) == 0:
            continue

        open_bar = open_bars.iloc[0]
        open_idx = df.index.get_loc(open_bars.index[0])
        if open_idx < 10 or open_idx > len(df)-300:
            continue

        atr = open_bar['atr']
        if atr <= 0:
            continue

        # Spike = move from previous close to session open bar high/low
        prev_close = df['close'].iloc[open_idx - 1]
        spike_up   = open_bar['high'] - prev_close
        spike_down = prev_close - open_bar['low']
        dow = open_bar['dow']

        if dow == 4:
            continue

        # Bullish spike (fade = short)
        if spike_up > 1.5 * atr and open_bar['close'] < open_bar['high'] - 0.3 * atr:
            # Reversal confirmation: bearish candle after spike
            if open_idx + 2 < len(df):
                conf_bar = df.iloc[open_idx + 1]
                if conf_bar['close'] < conf_bar['open']:  # bearish
                    sl_dist = 0.7 * atr
                    pnl, _, reason = simulate_trade(df, open_idx+1, 'short', sl_dist,
                                                    tp_rr=2.5, trail_start_r=1.2, trail_step_r=0.5)
                    trades_s2.append({
                        'date': date, 'direction': 'short', 'session': sess,
                        'pnl': pnl, 'outcome': 'win' if pnl > 0 else 'loss',
                        'reason': reason, 'hour': h_open, 'dow': dow,
                        'atr': atr, 'spike': spike_up, 'strategy': 'S2_Fade'
                    })

        # Bearish spike (fade = long)
        if spike_down > 1.5 * atr and open_bar['close'] > open_bar['low'] + 0.3 * atr:
            if open_idx + 2 < len(df):
                conf_bar = df.iloc[open_idx + 1]
                if conf_bar['close'] > conf_bar['open']:  # bullish
                    sl_dist = 0.7 * atr
                    pnl, _, reason = simulate_trade(df, open_idx+1, 'long', sl_dist,
                                                    tp_rr=2.5, trail_start_r=1.2, trail_step_r=0.5)
                    trades_s2.append({
                        'date': date, 'direction': 'long', 'session': sess,
                        'pnl': pnl, 'outcome': 'win' if pnl > 0 else 'loss',
                        'reason': reason, 'hour': h_open, 'dow': dow,
                        'atr': atr, 'spike': spike_down, 'strategy': 'S2_Fade'
                    })

T2 = pd.DataFrame(trades_s2)
print(f"  Trades: {len(T2)}")

# ══════════════════════════════════════════════════════════════════════════
# STRATEGY 3: H4 SWING LEVEL BOUNCE
# Price returns to recent H4 swing high (now support) or swing low (now resistance)
# Confirmation: M15 pin bar or engulfing at the level
# TP=2R, SL=1ATR
# ══════════════════════════════════════════════════════════════════════════
print("Strategy 3: S/R Bounce...")

trades_s3 = []

# Build rolling list of recent H4 swing levels
h4_swings = h4[['swing_high', 'swing_low']].copy()

for i in range(100, len(df)-300):
    row  = df.iloc[i]
    prev = df.iloc[i-1]
    date = df.index[i].date()
    dow  = row['dow']

    if dow == 4:
        continue

    atr = row['atr']
    if atr <= 0:
        continue

    cur_time = df.index[i]
    # Recent H4 swings (last 20 H4 bars = ~3 days)
    recent_h4 = h4_swings[h4_swings.index < cur_time].tail(20)
    if len(recent_h4) == 0:
        continue

    recent_highs = recent_h4['swing_high'].dropna().values
    recent_lows  = recent_h4['swing_low'].dropna().values

    # --- LONG: bounce from swing low (support) ---
    if row['h4_up'] or row['d1_up']:
        for level in recent_lows:
            dist = abs(row['low'] - level)
            if dist < 0.3 * atr and row['close'] > level:
                # Pin bar: lower wick > 2x body
                body  = abs(row['close'] - row['open'])
                l_wick = row['open'] - row['low'] if row['close'] > row['open'] else row['close'] - row['low']
                if l_wick > 1.5 * body and body > 0:
                    # Check no recent trade this day
                    day_trades = [t for t in trades_s3 if t['date'] == date and t['direction'] == 'long']
                    if not day_trades:
                        sl_dist = atr
                        pnl, _, reason = simulate_trade(df, i, 'long', sl_dist,
                                                        tp_rr=2.5, trail_start_r=1.2, trail_step_r=0.4)
                        trades_s3.append({
                            'date': date, 'direction': 'long', 'session': 'sr_bounce',
                            'pnl': pnl, 'outcome': 'win' if pnl > 0 else 'loss',
                            'reason': reason, 'hour': row['hour'], 'dow': dow,
                            'atr': atr, 'level': level, 'strategy': 'S3_SR_Bounce'
                        })
                    break

    # --- SHORT: bounce from swing high (resistance) ---
    if row['h4_down'] or not row['d1_up']:
        for level in recent_highs:
            dist = abs(row['high'] - level)
            if dist < 0.3 * atr and row['close'] < level:
                body  = abs(row['close'] - row['open'])
                u_wick = row['high'] - row['open'] if row['close'] < row['open'] else row['high'] - row['close']
                if u_wick > 1.5 * body and body > 0:
                    day_trades = [t for t in trades_s3 if t['date'] == date and t['direction'] == 'short']
                    if not day_trades:
                        sl_dist = atr
                        pnl, _, reason = simulate_trade(df, i, 'short', sl_dist,
                                                        tp_rr=2.5, trail_start_r=1.2, trail_step_r=0.4)
                        trades_s3.append({
                            'date': date, 'direction': 'short', 'session': 'sr_bounce',
                            'pnl': pnl, 'outcome': 'win' if pnl > 0 else 'loss',
                            'reason': reason, 'hour': row['hour'], 'dow': dow,
                            'atr': atr, 'level': level, 'strategy': 'S3_SR_Bounce'
                        })
                    break

T3 = pd.DataFrame(trades_s3)
print(f"  Trades: {len(T3)}")

# ── Results ────────────────────────────────────────────────────────────────
def report(T, name):
    if len(T) == 0:
        print(f"\n{name}: NO TRADES")
        return
    n    = len(T)
    wr   = (T['outcome']=='win').mean()
    pnl  = T['pnl'].sum()
    avg_w = T[T['outcome']=='win']['pnl'].mean() if (T['outcome']=='win').any() else 0
    avg_l = T[T['outcome']=='loss']['pnl'].mean() if (T['outcome']=='loss').any() else 0

    # Rolling balance for MaxDD
    balance = 10000 + T['pnl'].cumsum()
    peak    = balance.cummax()
    dd      = ((peak - balance) / peak * 100)
    max_dd  = dd.max()

    # Per year
    T2 = T.copy()
    T2['year'] = pd.to_datetime(T2['date']).dt.year
    yearly = T2.groupby('year')['pnl'].sum()

    print(f"\n{'='*55}")
    print(f"  {name}")
    print(f"{'='*55}")
    print(f"  Trades : {n}  |  WR: {wr:.1%}")
    print(f"  PnL    : ${pnl:,.0f}  |  MaxDD: {max_dd:.2f}%")
    print(f"  Avg Win: ${avg_w:.0f}  |  Avg Loss: ${avg_l:.0f}")
    print(f"  PF     : {abs(avg_w*wr / (avg_l*(1-wr))):.2f}" if avg_l != 0 else "  PF: N/A")
    print(f"  Per year:")
    for yr, y_pnl in yearly.items():
        bar = '#' * int(abs(y_pnl) / 500)
        sign = '+' if y_pnl >= 0 else '-'
        print(f"    {yr}: {sign}${abs(y_pnl):,.0f}  {bar}")

    # Direction breakdown
    for d in ['long','short']:
        sub = T[T['direction']==d]
        if len(sub) == 0: continue
        w = (sub['outcome']=='win').mean()
        p = sub['pnl'].sum()
        print(f"  {d:6s}: N={len(sub):4d}  WR={w:.1%}  PnL=${p:,.0f}")

report(T1, "STRATEGY 1: EMA Pullback (TP=3R, trail from 1.5R)")
report(T2, "STRATEGY 2: Session Open Fade (TP=2.5R)")
report(T3, "STRATEGY 3: S/R Swing Bounce (TP=2.5R)")

# ── Comparison ─────────────────────────────────────────────────────────────
print(f"\n{'='*55}")
print("  COMPARISON SUMMARY")
print(f"{'='*55}")
print(f"  {'Strategy':<30} {'N':>5} {'WR':>7} {'PnL':>10} {'MaxDD':>7}")
print(f"  {'-'*55}")

for T, name in [(T1,'S1: EMA Pullback'), (T2,'S2: Session Fade'), (T3,'S3: S/R Bounce')]:
    if len(T) == 0:
        print(f"  {name:<30}  no trades")
        continue
    n   = len(T)
    wr  = (T['outcome']=='win').mean()
    pnl = T['pnl'].sum()
    bal = 10000 + T['pnl'].cumsum()
    dd  = ((bal.cummax()-bal)/bal.cummax()*100).max()
    print(f"  {name:<30} {n:>5} {wr:>7.1%} ${pnl:>9,.0f} {dd:>6.2f}%")

# Save
for T, fname in [(T1,'s1_ema_pullback.csv'),(T2,'s2_session_fade.csv'),(T3,'s3_sr_bounce.csv')]:
    if len(T) > 0:
        T.to_csv(f"D:\\Works\\ASTRA ANALYZER CHART\\scripts\\{fname}", index=False)

print("\nDone.")
