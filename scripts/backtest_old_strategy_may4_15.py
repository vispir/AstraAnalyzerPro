# -*- coding: utf-8 -*-
"""
Backtest СТАРОЙ стратегии Session Breakout v5.0 (LONG+SHORT FB)
за период 4-15 мая 2026 года.

LONG:  Asian(3-6) London(8-11) NY(15-18) breakout, TP=12R, SL=session_low-0.5ATR
SHORT: london_fb(6-9) TP=3R, ny_fb(12-15) TP=10R, Risk=$100
H4 EMA20 slope фильтр (только LONG)
"""
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
import pandas as pd, numpy as np, warnings
warnings.filterwarnings('ignore')
from datetime import date as date_cls

RISK      = 100.0
ATR_BUF   = 0.5      # SL = session_low - ATR_BUF * ATR
TP_LONG   = 12.0     # R
TP_LDN_FB = 3.0      # R
TP_NY_FB  = 10.0     # R
FB_BUF    = 0.3      # ATR буфер для SL у SHORT FB
NC        = 4        # bars_above для FB подтверждения

LONG_SESSIONS = {
    'asian':  {'rh': (3,  6),  'entry': 6},
    'london': {'rh': (8,  11), 'entry': 11},
    'ny':     {'rh': (15, 18), 'entry': 18},
}
FB_SESSIONS = {
    'london_fb': {'rh': (6,  9),  'tp': TP_LDN_FB},
    'ny_fb':     {'rh': (12, 15), 'tp': TP_NY_FB},
}

# --- Данные ---
f_main = r"D:\Works\ASTRA ANALYZER CHART\data_cache\dukascopy\m15\XAUUSD\xauusd_m15_2020-01-01_2026-05-12.parquet"
f_new  = r"D:\Works\ASTRA ANALYZER CHART\data_cache\dukascopy\m15\XAUUSD\xauusd_m15_2026-05-12_2026-05-15.parquet"
df = pd.concat([pd.read_parquet(f_main), pd.read_parquet(f_new)])
df.index = pd.to_datetime(df.index, utc=True)
df = df[~df.index.duplicated(keep='last')].sort_index()
df.columns = [c.lower() for c in df.columns]

df['tr'] = np.maximum(df['high']-df['low'],
           np.maximum(abs(df['high']-df['close'].shift(1)),
                      abs(df['low'] -df['close'].shift(1))))
df['atr'] = df['tr'].ewm(alpha=1/14, adjust=False).mean()

# H4 EMA20 slope (no-lookahead)
h4 = df.resample('4h', origin='epoch').agg(
    open=('open','first'), high=('high','max'),
    low=('low','min'), close=('close','last')).dropna()
h4['ema20']  = h4['close'].ewm(span=20, adjust=False).mean()
h4['slope3'] = h4['ema20'] - h4['ema20'].shift(3)
h4['h4_up']  = (h4['close'] > h4['ema20']) & (h4['slope3'] > 0)

def mmap(s): return s.shift(1).reindex(df.index, method='ffill')
df['h4_up']   = mmap(h4['h4_up'])
df['h4_ema20']= mmap(h4['ema20'])
df['hour'] = df.index.hour
df['dow']  = df.index.dayofweek

# --- Симуляция ---
trades = []
prev_date = None
ls_highs = {}; ls_lows = {}
active_long  = {}   # session -> trade dict
active_short = {}   # fb_session -> trade dict
fb_state = {sn: {'sh': 0.0, 'bars_above': 0, 'ok': False, 'done': False} for sn in FB_SESSIONS}

for i, (ts, row) in enumerate(df.iterrows()):
    if pd.isna(row['atr']) or row['atr'] <= 0: continue
    hour  = row['hour']
    high  = row['high']; low = row['low']; close = row['close']; atr = row['atr']
    date  = ts.date()

    # --- Сброс дня ---
    if date != prev_date:
        prev_date = date
        ls_highs = {}; ls_lows = {}
        active_long = {}
        active_short = {}
        fb_state = {sn: {'sh': 0.0, 'bars_above': 0, 'ok': False, 'done': False}
                    for sn in FB_SESSIONS}

    # --- Обновляем активные LONG позиции (SL/TP) ---
    for sess in list(active_long.keys()):
        t = active_long[sess]
        if low <= t['sl']:
            pnl = -RISK
            trades.append({**t, 'exit': t['sl'], 'pnl': pnl, 'reason': 'SL',
                           'exit_ts': ts, 'win': False})
            del active_long[sess]
        elif high >= t['tp']:
            risk_pts = t['entry'] - t['sl']
            pnl = TP_LONG * RISK
            trades.append({**t, 'exit': t['tp'], 'pnl': pnl, 'reason': 'TP',
                           'exit_ts': ts, 'win': True})
            del active_long[sess]

    # --- Обновляем активные SHORT FB позиции (SL/TP) ---
    for sn in list(active_short.keys()):
        t = active_short[sn]
        if high >= t['sl']:
            pnl = -RISK
            trades.append({**t, 'exit': t['sl'], 'pnl': pnl, 'reason': 'SL',
                           'exit_ts': ts, 'win': False})
            del active_short[sn]
        elif low <= t['tp']:
            pnl = t['tp_r'] * RISK
            trades.append({**t, 'exit': t['tp'], 'pnl': pnl, 'reason': 'TP',
                           'exit_ts': ts, 'win': True})
            del active_short[sn]

    # --- Строим session ranges (LONG) ---
    for sn, p in LONG_SESSIONS.items():
        sh, eh = p['rh']
        if sh <= hour < eh:
            if sn not in ls_highs:
                ls_highs[sn] = high; ls_lows[sn] = low
            else:
                ls_highs[sn] = max(ls_highs[sn], high)
                ls_lows[sn]  = min(ls_lows[sn],  low)

    # --- LONG вход ---
    if row['h4_up']:
        for sn, p in LONG_SESSIONS.items():
            if sn in active_long: continue
            if sn not in ls_highs: continue
            if hour < p['entry']: continue
            if close > ls_highs[sn]:
                sl = ls_lows[sn] - ATR_BUF * atr
                risk_pts = close - sl
                if risk_pts <= 0: continue
                tp = close + TP_LONG * risk_pts
                active_long[sn] = {
                    'date': date, 'open_ts': ts, 'session': sn,
                    'direction': 'LONG', 'entry': close,
                    'sl': sl, 'tp': tp, 'risk_pts': risk_pts,
                    'tp_r': TP_LONG, 'hour': hour,
                }

    # --- SHORT FB диапазоны и вход ---
    for sn, p in FB_SESSIONS.items():
        sh, eh = p['rh']
        st = fb_state[sn]
        if st['done']: continue

        if sh <= hour < eh:
            if high > st['sh']:
                st['sh'] = high; st['bars_above'] = 0

        if hour >= eh and st['sh'] > 0:
            if high >= st['sh']:
                st['bars_above'] += 1
            if st['bars_above'] >= NC and not st['ok']:
                st['ok'] = True

        if st['ok'] and close < st['sh'] and sn not in active_short:
            entry = close
            sl = st['sh'] + FB_BUF * atr
            risk_pts = sl - entry
            if risk_pts <= 0: continue
            tp = entry - p['tp'] * risk_pts
            active_short[sn] = {
                'date': date, 'open_ts': ts, 'session': sn,
                'direction': 'SHORT', 'entry': entry,
                'sl': sl, 'tp': tp, 'risk_pts': risk_pts,
                'tp_r': p['tp'], 'hour': hour,
            }
            st['done'] = True

T = pd.DataFrame(trades) if trades else pd.DataFrame()
may_s = date_cls(2026,5,4); may_e = date_cls(2026,5,15)

if len(T):
    T_may = T[(T['date'] >= may_s) & (T['date'] <= may_e)].copy()
else:
    T_may = pd.DataFrame()

DOW = ['Пн','Вт','Ср','Чт','Пт','Сб','Вс']

print('='*72)
print('  БЭКТЕСТ 4-15 МАЯ 2026  |  Session Breakout v5.0 (СТАРАЯ)')
print('  LONG: asian/london/ny breakout TP=12R  |  SHORT: FB TP=3R/10R')
print('  Risk=$100/сделку')
print('='*72)

if len(T_may) == 0:
    print('  Нет сделок в этом периоде.')
else:
    print(f'\n  {"Дата":<12}{"День":<5}{"Session":<12}{"Dir":<7}{"Ч":>3}{"Entry":>9}{"Exit":>9}{"Причина":<9}{"PnL":>9}  Баланс')
    print(f'  {"-"*80}')
    running = 10000.0
    for _, t in T_may.sort_values('open_ts').iterrows():
        running += t['pnl']
        sign = '+' if t['pnl'] >= 0 else ''
        dow_n = DOW[pd.Timestamp(str(t['date'])).dayofweek]
        print(f'  {str(t["date"]):<12}{dow_n:<5}{t["session"]:<12}{t["direction"]:<7}{t["hour"]:>3}'
              f'  ${t["entry"]:>7.1f}  ${t["exit"]:>7.1f}  {t["reason"]:<9}'
              f'  {sign}${abs(t["pnl"]):>6.0f}  ${running:>9,.0f}')

    n = len(T_may); wr = T_may['win'].mean(); pnl = T_may['pnl'].sum()
    print(f'\n  {"="*55}')
    print(f'  ИТОГО 4-15 мая 2026  |  Session Breakout v5.0')
    print(f'  {"="*55}')
    print(f'  Сделок:   {n}')
    print(f'  Win Rate: {wr:.1%}')
    print(f'  PnL:      {"+" if pnl>=0 else ""}${pnl:,.2f}')
    print(f'  Баланс:   ${10000+pnl:,.2f}  (старт $10,000)')
    print(f'\n  По направлениям:')
    for d in ['LONG','SHORT']:
        s = T_may[T_may['direction']==d]
        if len(s):
            print(f'    {d}: {len(s)} сд  WR={s["win"].mean():.0%}  PnL={"+" if s["pnl"].sum()>=0 else ""}${s["pnl"].sum():,.0f}')
    print(f'\n  По дням:')
    cumul = 0
    for day, grp in T_may.groupby('date'):
        dp = grp['pnl'].sum(); cumul += dp
        dow_n = DOW[pd.Timestamp(str(day)).dayofweek]
        print(f'    {str(day)} {dow_n}: {len(grp)} сд  {"+" if dp>=0 else ""}${dp:,.0f}  накопл ${cumul:+,.0f}')
