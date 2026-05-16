# -*- coding: utf-8 -*-
import sys, io, calendar
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
import pandas as pd, numpy as np, warnings; warnings.filterwarnings('ignore')
from datetime import date as date_cls

# v1.2 параметры
TS_L=1.2; STEP_L=0.1   # LONG trail
TS_S=0.8; STEP_S=0.1   # SHORT trail
TP_R=4.0; SL_MULT=1.2; RISK=100
LONG_HOURS=[5,6,7,8,9,13,14,15,16]
SHORT_HOURS=[8,9,13,14,15,16]

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
df['hour'] = df.index.hour; df['dow'] = df.index.dayofweek
h4 = df.resample('4h', origin='epoch').agg(close=('close','last')).dropna()
h4['ema20']  = h4['close'].ewm(span=20, adjust=False).mean()
h4['slope3'] = h4['ema20'] - h4['ema20'].shift(3)
h4['h4_up']  = (h4['close'] > h4['ema20']) & (h4['slope3'] > 0)
h4['h4_dn']  = (h4['close'] < h4['ema20']) & (h4['slope3'] < 0)
def mmap(s): return s.shift(1).reindex(df.index, method='ffill')
df['h4_up'] = mmap(h4['h4_up']); df['h4_dn'] = mmap(h4['h4_dn'])
N=len(df)
hi=df['high'].values; lo=df['low'].values; cl=df['close'].values
atr=df['atr'].values; h4u=df['h4_up'].values; h4d=df['h4_dn'].values
hr=df['hour'].values; dow=df['dow'].values; dates=df.index.date

DOW=['Пн','Вт','Ср','Чт','Пт','Сб','Вс']

def run_period(d_from, d_to, remove_bar_limit=False):
    trades=[]; traded={}
    limit = N-1 if remove_bar_limit else N-400-1
    for i in range(300, limit):
        av=atr[i]
        if av<=0 or np.isnan(av): continue
        d=dates[i]
        if d < d_from or d > d_to: continue
        is_fri=(dow[i]==4)
        for direction in ['long','short']:
            if direction=='long':
                if not (h4u[i] and hr[i] in LONG_HOURS): continue
                if traded.get((d,'long'),0)>=1: continue
                ts=TS_L; step=STEP_L
            else:
                if not (h4d[i] and hr[i] in SHORT_HOURS): continue
                if traded.get((d,'short'),0)>=1: continue
                ts=TS_S; step=STEP_S
            sl_dist=av*SL_MULT; entry=cl[i]
            cur_sl=entry-sl_dist if direction=='long' else entry+sl_dist
            tp_px=entry+TP_R*sl_dist if direction=='long' else entry-TP_R*sl_dist
            best_r=0.0; result_r=None; exit_reason='data_end'
            for j in range(i+1, N):
                if is_fri and dow[j]==4 and hr[j]>=21:
                    result_r=(cl[j]-entry)/sl_dist if direction=='long' else (entry-cl[j])/sl_dist
                    exit_reason='fri_close'; break
                if is_fri and dow[j] in [5,6,0]:
                    result_r=(cl[j]-entry)/sl_dist if direction=='long' else (entry-cl[j])/sl_dist
                    exit_reason='fri_close'; break
                if direction=='long':
                    if lo[j]<=cur_sl: result_r=(cur_sl-entry)/sl_dist; exit_reason='SL'; break
                    if hi[j]>=tp_px:  result_r=TP_R; exit_reason='TP'; break
                    r=(hi[j]-entry)/sl_dist
                    if r>best_r: best_r=r
                    if best_r>=ts:
                        ns=entry+(best_r-step)*sl_dist
                        if ns>cur_sl: cur_sl=ns
                else:
                    if hi[j]>=cur_sl: result_r=(entry-cur_sl)/sl_dist; exit_reason='SL'; break
                    if lo[j]<=tp_px:  result_r=TP_R; exit_reason='TP'; break
                    r=(entry-lo[j])/sl_dist
                    if r>best_r: best_r=r
                    if best_r>=ts:
                        ns=entry-(best_r-step)*sl_dist
                        if ns<cur_sl: cur_sl=ns
            if result_r is None:
                result_r=(cur_sl-entry)/sl_dist if direction=='long' else (entry-cur_sl)/sl_dist
            trades.append({'date':d,'direction':direction.upper(),'hour':hr[i],
                           'entry':cl[i],'sl_dist':sl_dist,
                           'pnl':result_r*RISK,'win':result_r>0,
                           'r':result_r,'reason':exit_reason})
            traded[(d,direction)]=traded.get((d,direction),0)+1
    return pd.DataFrame(trades)

print('='*72)
print('  МАЙ 4-15, 2026  |  AstraH4Trend v1.2  |  Risk=$100')
print('  LONG trail: 1.2R/0.1R  |  SHORT trail: 0.8R/0.1R')
print('='*72)

# Мая 4-8: стандартный бэктест (бары в пределах лимита)
T1 = run_period(date_cls(2026,5,4), date_cls(2026,5,8), remove_bar_limit=False)
# Мая 11-14: убираем ограничение (последние бары датасета)
T2 = run_period(date_cls(2026,5,11), date_cls(2026,5,14), remove_bar_limit=True)

T = pd.concat([T1, T2]).sort_values('date').reset_index(drop=True)

print(f'\n  {"Дата":<12}{"День":<5}{"Dir":<7}{"Ч":>3}{"R":>7}  {"PnL":>8}  {"Причина":<12} Баланс')
print(f'  {"-"*68}')
running=10000.0
for _,t in T.iterrows():
    running+=t['pnl']
    dow_n=DOW[pd.Timestamp(str(t['date'])).dayofweek]
    sign='+' if t['pnl']>=0 else ''
    note=' ⚠' if t['reason']=='data_end' else ''
    print(f'  {str(t["date"]):<12}{dow_n:<5}{t["direction"]:<7}{t["hour"]:>3}'
          f'  {t["r"]:>+5.2f}R  {sign}${abs(t["pnl"]):>6.0f}  {t["reason"]:<12} ${running:>9,.0f}{note}')

n=len(T); wr=T['win'].mean(); pnl=T['pnl'].sum()
print(f'\n  {"="*60}')
print(f'  ИТОГО 4-15 МАЯ:  {n} сд  WR={wr:.0%}  PnL={"+" if pnl>=0 else ""}${pnl:,.0f}')
print(f'  Баланс: ${10000+pnl:,.0f}  (старт $10,000)')
print(f'  Мин баланс: ${(T["pnl"].cumsum()+10000).min():,.0f}')
print(f'\n  По направлениям:')
for d in ['LONG','SHORT']:
    s=T[T['direction']==d]
    if len(s): print(f'    {d}: {len(s)} сд  WR={s["win"].mean():.0%}  PnL=+${s["pnl"].sum():,.0f}')

print(f'\n  {"="*60}')
print(f'  СРАВНЕНИЕ: что было бы со СТАРОЙ стратегией (Session Breakout v5.0)')
print(f'  {"="*60}')
print(f'  Старая: 8 сд  WR=0%  PnL=-$800  Баланс: $9,200 → потом до $9,067')
print(f'  Новая:  {n} сд  WR={wr:.0%}  PnL=+${pnl:,.0f}  Баланс: ${10000+pnl:,.0f}')
diff = pnl - (-800)
print(f'  Разница: +${diff:,.0f} в пользу новой стратегии')
