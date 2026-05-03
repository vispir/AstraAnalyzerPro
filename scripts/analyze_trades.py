"""
Анализ бэктеста: макс. одновременных позиций + макс. серия убыточных сделок
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + "/..")

import pandas as pd
import numpy as np
from pathlib import Path
import importlib.util

_sp = Path(__file__).parent.parent / "astra_v2" / "strategies" / "session_long_nolookahead_v1.py"
spec = importlib.util.spec_from_file_location("strat", _sp)
strat = importlib.util.module_from_spec(spec)
spec.loader.exec_module(strat)

RISK_L        = strat.RISK
TP_RR_L       = strat.TP_RR
ATR_BUFFER_L  = strat.ATR_BUFFER
ATR_PERIOD    = strat.ATR_PERIOD
H4_EMA_PERIOD = strat.H4_EMA_PERIOD
SLOPE_N       = strat.SLOPE_N
MIN_H4_BARS   = strat.MIN_H4_BARS
K_EMA         = strat.K_EMA
H4_NS         = strat.H4_NS

LONG_SESSIONS = {
    'asian':  {'range_hours': (3,  6),  'entry_start':  6, 'entry_end': 24},
    'london': {'range_hours': (8,  11), 'entry_start': 11, 'entry_end': 24},
    'ny':     {'range_hours': (15, 18), 'entry_start': 18, 'entry_end': 24},
}

FB_SESSIONS = {
    'london_fb': {'range': (6,  9),  'tp': 3,  'buf': 0.3, 'nc': 4, 'risk': 100.0},
    'ny_fb':     {'range': (12, 15), 'tp': 10, 'buf': 0.3, 'nc': 4, 'risk': 100.0},
}

data_path = (
    Path(__file__).parent.parent
    / "data_cache" / "dukascopy" / "m15" / "XAUUSD"
    / "xauusd_m15_2020-01-01_2026-04-18.parquet"
)

df = pd.read_parquet(data_path).sort_index()
df['atr'] = strat._atr(df, ATR_PERIOD)
df_h4 = df.resample('4h').agg({'open':'first','high':'max','low':'min','close':'last'}).dropna()
df_h4['ema20'] = strat._ema(df_h4, H4_EMA_PERIOD)
h4_times = df_h4.index.asi8
h4_ema20 = df_h4['ema20'].to_numpy()
n_h4     = len(h4_times)
times_ns = df.index.asi8
m15      = df.to_numpy()
col      = {c: i for i, c in enumerate(df.columns)}
i_h = col['high']; i_l = col['low']; i_c = col['close']; i_a = col['atr']

ptr_closed = -1; forming_period = -1; forming_close = np.nan; ema_base = np.nan
active_long  = {}
active_short = {}
ls_highs = {}; ls_lows = {}
fb_state = {sn: {'sh': 0., 'bars_above': 0, 'ok': False, 'peak': 0., 'done': False}
            for sn in FB_SESSIONS}

trades = []
prev_date = None


def trail_short(t, high):
    risk = t['initial_sl'] - t['entry']
    rr   = (t['entry'] - high) / risk
    for trigger, lock in t['trail_steps']:
        if rr >= trigger:
            t['sl'] = min(t['sl'], t['entry'] - lock * risk)
            break


for i in range(len(df)):
    ts_ns  = int(times_ns[i])
    cur_ts = df.index[i]
    high   = float(m15[i, i_h])
    low    = float(m15[i, i_l])
    close  = float(m15[i, i_c])
    atr    = float(m15[i, i_a])
    hour   = cur_ts.hour
    if np.isnan(atr):
        continue

    cur_date = cur_ts.date()
    if cur_date != prev_date:
        prev_date = cur_date
        ls_highs = {}; ls_lows = {}
        for sn in fb_state:
            fb_state[sn] = {'sh': 0., 'bars_above': 0, 'ok': False, 'peak': 0., 'done': False}

    while ptr_closed + 1 < n_h4 and h4_times[ptr_closed + 1] + H4_NS <= ts_ns:
        ptr_closed += 1

    if ptr_closed < MIN_H4_BARS - 1:
        for sn, p in LONG_SESSIONS.items():
            sh, eh = p['range_hours']
            if sh <= hour < eh:
                ls_highs[sn] = max(ls_highs.get(sn, 0),   high)
                ls_lows[sn]  = min(ls_lows.get(sn, 1e9),  low)
        for sn, cfg in FB_SESSIONS.items():
            rs, re = cfg['range']
            if rs <= hour < re:
                fb_state[sn]['sh'] = max(fb_state[sn]['sh'], high)
        continue

    h4p = int(ts_ns // int(14400 * 1e9)) * int(14400 * 1e9)
    if h4p != forming_period:
        forming_period = h4p
        forming_close  = close
        ema_base = h4_ema20[ptr_closed] if ptr_closed >= 0 else np.nan
    else:
        forming_close = close
    if np.isnan(ema_base):
        continue

    h4_ema   = forming_close * K_EMA + ema_base * (1.0 - K_EMA)
    ema_ok   = forming_close > ema_base
    slope_ok = (ptr_closed >= SLOPE_N
                and not np.isnan(h4_ema20[ptr_closed - SLOPE_N])
                and h4_ema > h4_ema20[ptr_closed - SLOPE_N])

    # Manage LONG
    for sn in list(active_long.keys()):
        t = active_long[sn]
        strat._trail(t, low)
        if low <= t['sl']:
            pnl = (t['sl'] - t['entry']) * t['size']
            trades.append({'entry_time': t['entry_time'], 'exit_time': cur_ts,
                           'pnl': pnl, 'direction': 'LONG', 'session': sn})
            del active_long[sn]
        elif high >= t['tp']:
            pnl = (t['tp'] - t['entry']) * t['size']
            trades.append({'entry_time': t['entry_time'], 'exit_time': cur_ts,
                           'pnl': pnl, 'direction': 'LONG', 'session': sn})
            del active_long[sn]

    # Manage SHORT
    for sn in list(active_short.keys()):
        t = active_short[sn]
        trail_short(t, high)
        if high >= t['sl']:
            pnl = (t['entry'] - t['sl']) * t['size']
            trades.append({'entry_time': t['entry_time'], 'exit_time': cur_ts,
                           'pnl': pnl, 'direction': 'SHORT', 'session': sn})
            del active_short[sn]
        elif low <= t['tp']:
            pnl = (t['entry'] - t['tp']) * t['size']
            trades.append({'entry_time': t['entry_time'], 'exit_time': cur_ts,
                           'pnl': pnl, 'direction': 'SHORT', 'session': sn})
            del active_short[sn]

    # Build LONG ranges
    for sn, p in LONG_SESSIONS.items():
        sh, eh = p['range_hours']
        if sh <= hour < eh:
            ls_highs[sn] = max(ls_highs.get(sn, 0),   high)
            ls_lows[sn]  = min(ls_lows.get(sn, 1e9),  low)

    # FB SHORT state machine
    for sn, cfg in FB_SESSIONS.items():
        rs, re = cfg['range']
        if rs <= hour < re:
            fb_state[sn]['sh'] = max(fb_state[sn]['sh'], high)
            continue
        st = fb_state[sn]
        if st['sh'] > 0 and hour >= re and not st['done'] and sn not in active_short:
            if not st['ok']:
                if close > st['sh']:
                    st['bars_above'] += 1
                    st['peak'] = max(st['peak'], high)
                    if st['bars_above'] >= cfg['nc']:
                        st['ok'] = True
            else:
                st['peak'] = max(st['peak'], high)
                if close < st['sh']:
                    sl_s  = st['peak'] + cfg['buf'] * atr
                    rsk_s = sl_s - close
                    if rsk_s > 0:
                        trail_steps = [(j, j-1) for j in range(int(cfg['tp']) - 1, 1, -1)]
                        tp_price    = close - cfg['tp'] * rsk_s
                        active_short[sn] = {
                            'entry': close, 'sl': sl_s, 'initial_sl': sl_s,
                            'tp': tp_price, 'size': cfg['risk'] / rsk_s,
                            'session': sn, 'trail_steps': trail_steps,
                            'entry_time': cur_ts,
                        }
                        st['done'] = True

    # LONG entries
    if ema_ok and slope_ok:
        for sn, p in LONG_SESSIONS.items():
            if sn not in ls_highs or sn in active_long:
                continue
            if not (p['entry_start'] <= hour < p['entry_end']):
                continue
            if close > ls_highs[sn]:
                sl_l  = ls_lows[sn] - ATR_BUFFER_L * atr
                rsk_l = close - sl_l
                if rsk_l <= 0:
                    continue
                active_long[sn] = {
                    'entry': close, 'sl': sl_l, 'initial_sl': sl_l,
                    'tp': close + rsk_l * TP_RR_L,
                    'size': RISK_L / rsk_l, 'session': sn,
                    'entry_time': cur_ts,
                }

# Sort by exit time
tdf = pd.DataFrame(trades).sort_values('exit_time').reset_index(drop=True)
print(f"Всего сделок: {len(tdf)}")
print()

# ── 1. Одновременно открытые позиции ──────────────────────────────
events = []
for _, r in tdf.iterrows():
    events.append((r['entry_time'], +1, r['session']))
    events.append((r['exit_time'],  -1, r['session']))
events.sort(key=lambda x: (x[0], x[1]))

cur_open = 0; max_open = 0; max_open_time = None; cur_sessions = []
for ts, delta, sess in events:
    if delta == +1:
        cur_open += 1
        cur_sessions.append(sess)
    else:
        cur_open -= 1
        if sess in cur_sessions:
            cur_sessions.remove(sess)
    if cur_open > max_open:
        max_open      = cur_open
        max_open_time = ts
        max_sessions  = list(cur_sessions)

print(f"Макс. одновременно открытых позиций: {max_open}")
print(f"  Время: {max_open_time}")
print(f"  Сессии: {max_sessions}")
print()

# Распределение
print("Распределение количества одновременных позиций:")
sim_counts = []
for _, r in tdf.iterrows():
    overlap = ((tdf['entry_time'] < r['exit_time']) & (tdf['exit_time'] > r['entry_time'])).sum()
    sim_counts.append(overlap)
tdf['sim'] = sim_counts
for k, v in tdf['sim'].value_counts().sort_index().items():
    print(f"  {k} одновременно: {v} сделок ({v/len(tdf)*100:.1f}%)")
print()

# ── 2. Серии убытков ───────────────────────────────────────────────
pnls = tdf['pnl'].tolist()

# Максимальная серия
max_streak = 0; cur_streak = 0; best_i = 0
for idx, p in enumerate(pnls):
    if p < 0:
        cur_streak += 1
        if cur_streak > max_streak:
            max_streak = cur_streak
            best_i = idx - cur_streak + 1
    else:
        cur_streak = 0

print(f"Макс. серия убыточных сделок подряд: {max_streak}")
if max_streak > 0:
    seg = tdf.iloc[best_i:best_i + max_streak]
    print(f"  Начало: {seg.iloc[0]['exit_time'].date()}")
    print(f"  Конец:  {seg.iloc[-1]['exit_time'].date()}")
    print(f"  Суммарный убыток: ${seg['pnl'].sum():,.0f}")
    print(f"  Сессии: {seg['session'].tolist()}")
print()

# Топ-5 серий убытков
streaks = []
buf = []; buf_sum = 0
for idx, p in enumerate(pnls):
    if p < 0:
        buf.append(idx); buf_sum += p
    else:
        if buf:
            streaks.append((len(buf), buf_sum, buf[0], buf[-1]))
        buf = []; buf_sum = 0
if buf:
    streaks.append((len(buf), buf_sum, buf[0], buf[-1]))
streaks.sort(key=lambda x: -x[0])

print("Топ-5 серий убытков по длине:")
print(f"  {'Длина':>6}  {'Убыток':>9}  {'С':>12}  {'По':>12}")
for length, total, si, ei in streaks[:5]:
    d1 = tdf.iloc[si]['exit_time'].date()
    d2 = tdf.iloc[ei]['exit_time'].date()
    print(f"  {length:>6}  ${total:>8,.0f}  {str(d1):>12}  {str(d2):>12}")
