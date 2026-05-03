"""
Точный анализ MaxDD: пик, дно, дата, баланс
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pandas as pd
import numpy as np
import importlib.util

_sp = Path(__file__).parent.parent / "astra_v2" / "strategies" / "session_long_nolookahead_v1.py"
spec = importlib.util.spec_from_file_location("strat", _sp)
strat = importlib.util.module_from_spec(spec)
spec.loader.exec_module(strat)

RISK_L = strat.RISK; TP_RR_L = strat.TP_RR; ATR_BUFFER_L = strat.ATR_BUFFER
ATR_PERIOD = strat.ATR_PERIOD; H4_EMA_PERIOD = strat.H4_EMA_PERIOD
SLOPE_N = strat.SLOPE_N; MIN_H4_BARS = strat.MIN_H4_BARS
K_EMA = strat.K_EMA; H4_NS = strat.H4_NS

LONG_SESSIONS = {
    'asian':  {'range_hours': (3,  6),  'entry_start':  6, 'entry_end': 24},
    'london': {'range_hours': (8,  11), 'entry_start': 11, 'entry_end': 24},
    'ny':     {'range_hours': (15, 18), 'entry_start': 18, 'entry_end': 24},
}
FB_SESSIONS = {
    'london_fb': {'range': (6,  9),  'tp': 3,  'buf': 0.3, 'nc': 4, 'risk': 100.0},
    'ny_fb':     {'range': (12, 15), 'tp': 10, 'buf': 0.3, 'nc': 4, 'risk': 100.0},
}

data_path = (Path(__file__).parent.parent
             / "data_cache/dukascopy/m15/XAUUSD/xauusd_m15_2020-01-01_2026-04-18.parquet")
df = pd.read_parquet(data_path).sort_index()
df['atr'] = strat._atr(df, ATR_PERIOD)
df_h4 = df.resample('4h').agg({'open':'first','high':'max','low':'min','close':'last'}).dropna()
df_h4['ema20'] = strat._ema(df_h4, H4_EMA_PERIOD)
h4_times = df_h4.index.asi8; h4_ema20 = df_h4['ema20'].to_numpy(); n_h4 = len(h4_times)
times_ns = df.index.asi8; m15 = df.to_numpy()
col = {c: i for i, c in enumerate(df.columns)}
i_h=col['high']; i_l=col['low']; i_c=col['close']; i_a=col['atr']

ptr_closed=-1; forming_period=-1; forming_close=np.nan; ema_base=np.nan
active_long={}; active_short={}; ls_highs={}; ls_lows={}
fb_state={sn:{'sh':0.,'bars_above':0,'ok':False,'peak':0.,'done':False} for sn in FB_SESSIONS}

balance=10_000.; peak=10_000.; max_dd=0.
peak_ts=None; trough_ts=None; peak_bal=10_000.; trough_bal=10_000.
equity_curve=[]  # (timestamp, balance)
prev_date=None

def trail_short(t, high):
    risk=t['initial_sl']-t['entry']; rr=(t['entry']-high)/risk
    for trigger,lock in t['trail_steps']:
        if rr>=trigger: t['sl']=min(t['sl'],t['entry']-lock*risk); break

for i in range(len(df)):
    ts_ns=int(times_ns[i]); cur_ts=df.index[i]
    high=float(m15[i,i_h]); low=float(m15[i,i_l])
    close=float(m15[i,i_c]); atr=float(m15[i,i_a])
    hour=cur_ts.hour
    if np.isnan(atr): continue

    cur_date=cur_ts.date()
    if cur_date!=prev_date:
        prev_date=cur_date; ls_highs={}; ls_lows={}
        for sn in fb_state:
            fb_state[sn]={'sh':0.,'bars_above':0,'ok':False,'peak':0.,'done':False}

    while ptr_closed+1<n_h4 and h4_times[ptr_closed+1]+H4_NS<=ts_ns: ptr_closed+=1

    if ptr_closed<MIN_H4_BARS-1:
        for sn,p in LONG_SESSIONS.items():
            sh,eh=p['range_hours']
            if sh<=hour<eh: ls_highs[sn]=max(ls_highs.get(sn,0),high); ls_lows[sn]=min(ls_lows.get(sn,1e9),low)
        for sn,cfg in FB_SESSIONS.items():
            rs,re=cfg['range']
            if rs<=hour<re: fb_state[sn]['sh']=max(fb_state[sn]['sh'],high)
        continue

    h4p=int(ts_ns//int(14400*1e9))*int(14400*1e9)
    if h4p!=forming_period:
        forming_period=h4p; forming_close=close
        ema_base=h4_ema20[ptr_closed] if ptr_closed>=0 else np.nan
    else: forming_close=close
    if np.isnan(ema_base): continue

    h4_ema=forming_close*K_EMA+ema_base*(1.-K_EMA)
    ema_ok=forming_close>ema_base
    slope_ok=(ptr_closed>=SLOPE_N and not np.isnan(h4_ema20[ptr_closed-SLOPE_N])
              and h4_ema>h4_ema20[ptr_closed-SLOPE_N])

    for sn in list(active_long.keys()):
        t=active_long[sn]; strat._trail(t,low)
        if low<=t['sl']:
            balance+=(t['sl']-t['entry'])*t['size']; del active_long[sn]
        elif high>=t['tp']:
            balance+=(t['tp']-t['entry'])*t['size']; del active_long[sn]

    for sn in list(active_short.keys()):
        t=active_short[sn]; trail_short(t,high)
        if high>=t['sl']:
            balance+=(t['entry']-t['sl'])*t['size']; del active_short[sn]
        elif low<=t['tp']:
            balance+=(t['entry']-t['tp'])*t['size']; del active_short[sn]

    if balance>peak:
        peak=balance; peak_ts=cur_ts; peak_bal=balance
    dd=(peak-balance)/peak*100
    if dd>max_dd:
        max_dd=dd; trough_ts=cur_ts; trough_bal=balance
        dd_peak_ts=peak_ts; dd_peak_bal=peak_bal

    equity_curve.append((cur_ts, balance))

    for sn,p in LONG_SESSIONS.items():
        sh,eh=p['range_hours']
        if sh<=hour<eh: ls_highs[sn]=max(ls_highs.get(sn,0),high); ls_lows[sn]=min(ls_lows.get(sn,1e9),low)

    for sn,cfg in FB_SESSIONS.items():
        rs,re=cfg['range']
        if rs<=hour<re: fb_state[sn]['sh']=max(fb_state[sn]['sh'],high); continue
        st=fb_state[sn]
        if st['sh']>0 and hour>=re and not st['done'] and sn not in active_short:
            if not st['ok']:
                if close>st['sh']:
                    st['bars_above']+=1; st['peak']=max(st['peak'],high)
                    if st['bars_above']>=cfg['nc']: st['ok']=True
            else:
                st['peak']=max(st['peak'],high)
                if close<st['sh']:
                    sl_s=st['peak']+cfg['buf']*atr; rsk_s=sl_s-close
                    if rsk_s>0:
                        trail_steps=[(j,j-1) for j in range(int(cfg['tp'])-1,1,-1)]
                        active_short[sn]={'entry':close,'sl':sl_s,'initial_sl':sl_s,
                            'tp':close-cfg['tp']*rsk_s,'size':cfg['risk']/rsk_s,
                            'session':sn,'trail_steps':trail_steps}
                        st['done']=True

    if ema_ok and slope_ok:
        for sn,p in LONG_SESSIONS.items():
            if sn not in ls_highs or sn in active_long: continue
            if not (p['entry_start']<=hour<p['entry_end']): continue
            if close>ls_highs[sn]:
                sl_l=ls_lows[sn]-ATR_BUFFER_L*atr; rsk_l=close-sl_l
                if rsk_l<=0: continue
                active_long[sn]={'entry':close,'sl':sl_l,'initial_sl':sl_l,
                    'tp':close+rsk_l*TP_RR_L,'size':RISK_L/rsk_l,'session':sn}

eq = pd.DataFrame(equity_curve, columns=['ts','bal'])

print("=" * 60)
print("АНАЛИЗ МАКСИМАЛЬНОЙ ПРОСАДКИ (MaxDD)")
print("=" * 60)
print(f"\nСтартовый баланс:  $10,000")
print(f"Итоговый баланс:   ${balance:,.0f}  (+${balance-10000:,.0f})")
print(f"\nMaxDD = {max_dd:.2f}%")
print(f"  Пик:   ${dd_peak_bal:>10,.2f}  ({dd_peak_ts.date()})")
print(f"  Дно:   ${trough_bal:>10,.2f}  ({trough_ts.date()})")
print(f"  Абс.просадка: ${dd_peak_bal-trough_bal:,.2f}")
print()

# Equity curve by year
print("Баланс на конец каждого года:")
for yr in range(2020, 2027):
    sub = eq[eq['ts'].dt.year == yr]
    if len(sub) == 0: continue
    end_bal = sub.iloc[-1]['bal']
    max_bal = sub['bal'].max()
    min_bal = sub['bal'].min()
    print(f"  {yr}: конец=${end_bal:>10,.0f}  пик=${max_bal:>10,.0f}  дно=${min_bal:>10,.0f}")

print()
# Top 5 drawdowns
print("Top-5 drawdowns (peak to trough):")
running_peak = 10000.; dds = []
for _, row in eq.iterrows():
    b = row['bal']; t = row['ts']
    if b > running_peak: running_peak = b
    dd_pct = (running_peak - b) / running_peak * 100
    dds.append((dd_pct, running_peak, b, t))

# Find distinct drawdown episodes
dds_df = pd.DataFrame(dds, columns=['dd','peak','bal','ts'])
# Find local maxima in dd
in_dd = False; episodes = []; ep_max = 0; ep_start = None; ep_ts = None; ep_peak = 0; ep_trough = 0
for _, r in dds_df.iterrows():
    if r['dd'] > 0.5:
        if not in_dd: in_dd=True; ep_max=r['dd']; ep_ts=r['ts']; ep_peak=r['peak']; ep_trough=r['bal']
        elif r['dd']>ep_max: ep_max=r['dd']; ep_ts=r['ts']; ep_trough=r['bal']
    else:
        if in_dd:
            episodes.append((ep_max, ep_peak, ep_trough, ep_ts))
            in_dd=False
if in_dd:
    episodes.append((ep_max, ep_peak, ep_trough, ep_ts))

episodes.sort(key=lambda x: -x[0])
print(f"  {'DD%':>6}  {'Пик $':>10}  {'Дно $':>10}  {'Абс $':>8}  {'Дата дна':>12}")
for dd_pct, pk, tr, ts in episodes[:5]:
    print(f"  {dd_pct:>5.2f}%  ${pk:>9,.0f}  ${tr:>9,.0f}  ${pk-tr:>7,.0f}  {ts.date()}")
print("=" * 60)
