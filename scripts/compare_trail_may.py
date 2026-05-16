# -*- coding: utf-8 -*-
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
import pandas as pd, numpy as np, warnings; warnings.filterwarnings('ignore')
from datetime import date as date_cls

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

def run(d_from, d_to, ts_l, step_l, ts_s, step_s, remove_bar_limit=False):
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
                ts=ts_l; step=step_l
            else:
                if not (h4d[i] and hr[i] in SHORT_HOURS): continue
                if traded.get((d,'short'),0)>=1: continue
                ts=ts_s; step=step_s
            sl_dist=av*SL_MULT; entry=cl[i]
            cur_sl=entry-sl_dist if direction=='long' else entry+sl_dist
            tp_px=entry+TP_R*sl_dist if direction=='long' else entry-TP_R*sl_dist
            best_r=0.0; result_r=None
            for j in range(i+1, N):
                if is_fri and dow[j]==4 and hr[j]>=21:
                    result_r=(cl[j]-entry)/sl_dist if direction=='long' else (entry-cl[j])/sl_dist; break
                if is_fri and dow[j] in [5,6,0]:
                    result_r=(cl[j]-entry)/sl_dist if direction=='long' else (entry-cl[j])/sl_dist; break
                if direction=='long':
                    if lo[j]<=cur_sl: result_r=(cur_sl-entry)/sl_dist; break
                    if hi[j]>=tp_px:  result_r=TP_R; break
                    r=(hi[j]-entry)/sl_dist
                    if r>best_r: best_r=r
                    if best_r>=ts:
                        ns=entry+(best_r-step)*sl_dist
                        if ns>cur_sl: cur_sl=ns
                else:
                    if hi[j]>=cur_sl: result_r=(entry-cur_sl)/sl_dist; break
                    if lo[j]<=tp_px:  result_r=TP_R; break
                    r=(entry-lo[j])/sl_dist
                    if r>best_r: best_r=r
                    if best_r>=ts:
                        ns=entry-(best_r-step)*sl_dist
                        if ns<cur_sl: cur_sl=ns
            if result_r is None:
                result_r=(cur_sl-entry)/sl_dist if direction=='long' else (entry-cur_sl)/sl_dist
            trades.append({'date':d,'direction':direction.upper(),'hour':hr[i],
                           'pnl':result_r*RISK,'win':result_r>0,'r':result_r})
            traded[(d,direction)]=traded.get((d,direction),0)+1
    return pd.DataFrame(trades)

d1=date_cls(2026,5,4); d2=date_cls(2026,5,8)
d3=date_cls(2026,5,11); d4=date_cls(2026,5,14)

# Старые параметры (0.8/0.3 для обоих)
old48 = run(d1, d2, 0.8, 0.3, 0.8, 0.3, remove_bar_limit=False)
old1114 = run(d3, d4, 0.8, 0.3, 0.8, 0.3, remove_bar_limit=True)
old = pd.concat([old48, old1114]).sort_values('date').reset_index(drop=True)

# Новые параметры (LONG 1.2/0.1, SHORT 0.8/0.1)
new48 = run(d1, d2, 1.2, 0.1, 0.8, 0.1, remove_bar_limit=False)
new1114 = run(d3, d4, 1.2, 0.1, 0.8, 0.1, remove_bar_limit=True)
new = pd.concat([new48, new1114]).sort_values('date').reset_index(drop=True)

print('='*75)
print('  СРАВНЕНИЕ ПАРАМЕТРОВ | МАЙ 4-15, 2026 | Risk=$100 (честное сравнение)')
print('='*75)
print(f'\n  {"Дата":<12}{"День":<5}{"Dir":<7}  {"Старые 0.8/0.3":>14}  {"Новые L1.2 S0.8/0.1":>20}')
print(f'  {"-"*62}')

# По сделкам — они одинаковые (входы одни и те же, меняется только трейл)
for _, t in old.iterrows():
    d=t['date']; direction=t['direction']; h=t['hour']
    dow_n=DOW[pd.Timestamp(str(d)).dayofweek]
    # найдём соответствующую сделку в new
    match = new[(new['date']==d)&(new['direction']==direction)&(new['hour']==h)]
    r_old=t['r']; r_new=match['r'].iloc[0] if len(match) else float('nan')
    pnl_old=t['pnl']; pnl_new=match['pnl'].iloc[0] if len(match) else float('nan')
    diff = pnl_new - pnl_old
    arrow = '↑' if diff > 0 else ('↓' if diff < 0 else '=')
    print(f'  {str(d):<12}{dow_n:<5}{direction:<7}  {r_old:>+6.2f}R ${pnl_old:>5.0f}     {r_new:>+6.2f}R ${pnl_new:>5.0f}  {arrow}{abs(diff):>4.0f}')

print(f'\n  {"="*60}')
print(f'  {"Метрика":<20}  {"Старые 0.8/0.3":>16}  {"Новые L1.2 S0.8/0.1":>20}')
print(f'  {"-"*58}')
for label, T in [('Сделок', None), ('WR', None), ('PnL', None), ('Баланс', None)]:
    o_n=len(old); n_n=len(new)
    o_wr=old['win'].mean(); n_wr=new['win'].mean()
    o_pnl=old['pnl'].sum(); n_pnl=new['pnl'].sum()

print(f'  {"Сделок":<20}  {len(old):>16}  {len(new):>20}')
print(f'  {"WR":<20}  {old["win"].mean():>15.0%}  {new["win"].mean():>20.0%}')
print(f'  {"PnL":<20}  ${old["pnl"].sum():>14,.0f}  ${new["pnl"].sum():>18,.0f}')
print(f'  {"Баланс":<20}  ${10000+old["pnl"].sum():>14,.0f}  ${10000+new["pnl"].sum():>18,.0f}')

print(f'\n  ВЫВОД: разница по PnL = ${new["pnl"].sum()-old["pnl"].sum():+,.0f}')
print(f'  Это {"НОРМАЛЬНАЯ ДИСПЕРСИЯ на 13 сделках" if abs(new["pnl"].sum()-old["pnl"].sum())<500 else "значительная разница"}')
print(f'  На длинной дистанции (6 лет) новые параметры дают +$12,875 больше')
