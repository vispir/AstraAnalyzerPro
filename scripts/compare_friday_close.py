# -*- coding: utf-8 -*-
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
import pandas as pd, numpy as np, warnings; warnings.filterwarnings('ignore')

TP_R=4.0; SL_MULT=1.2; RISK=100; START=10000
LONG_HOURS=[5,6,7,8,9,13,14,15,16]; SHORT_HOURS=[8,9,13,14,15,16]
TS_L=1.2; STEP_L=0.1; TS_S=0.8; STEP_S=0.1

f_main = r"D:\Works\ASTRA ANALYZER CHART\data_cache\dukascopy\m15\XAUUSD\xauusd_m15_2020-01-01_2026-05-12.parquet"
f_new  = r"D:\Works\ASTRA ANALYZER CHART\data_cache\dukascopy\m15\XAUUSD\xauusd_m15_2026-05-12_2026-05-15.parquet"
df = pd.concat([pd.read_parquet(f_main), pd.read_parquet(f_new)])
df.index = pd.to_datetime(df.index, utc=True)
df = df[~df.index.duplicated(keep='last')].sort_index()
df.columns = [c.lower() for c in df.columns]
df['tr'] = np.maximum(df['high']-df['low'],
           np.maximum(abs(df['high']-df['close'].shift(1)),
                      abs(df['low']-df['close'].shift(1))))
df['atr'] = df['tr'].ewm(alpha=1/14, adjust=False).mean()
df['hour'] = df.index.hour
df['minute'] = df.index.minute
df['dow'] = df.index.dayofweek
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
hr=df['hour'].values; mn=df['minute'].values; dow=df['dow'].values
dates=df.index.date

def run_sim(label, early_close):
    """
    early_close=False: закрытие в пятницу при hr>=21 (старое)
    early_close=True:  закрытие при hr==20 and mn>=30 OR hr>=21 (новое, EA срабатывает в 20:45)
    """
    trades=[]; traded={}
    affected = 0  # сделок, на которые повлияло раннее закрытие
    for i in range(300, N-400-1):
        av=atr[i]
        if av<=0 or np.isnan(av): continue
        d=dates[i]; is_fri=(dow[i]==4)
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
            best_r=0.0; result_r=None; was_affected=False
            for j in range(i+1, min(i+401,N)):
                # пятничное закрытие — НОВОЕ (20:45 UTC)
                if early_close and is_fri and dow[j]==4 and hr[j]==20 and mn[j]>=30:
                    result_r=(cl[j]-entry)/sl_dist if direction=='long' else (entry-cl[j])/sl_dist
                    was_affected=True; break
                # пятничное закрытие — СТАРОЕ (21:00 UTC) / fallback
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
            trades.append({'date':d,'pnl':result_r*RISK,'win':result_r>0,
                           'direction':direction.upper(), 'r':result_r})
            if was_affected: affected+=1
            traded[(d,direction)]=traded.get((d,direction),0)+1
    T=pd.DataFrame(trades)
    T['date']=pd.to_datetime(T['date'])
    return T, affected

print("Запуск бэктестов...")
T_old, aff_old = run_sim('old', early_close=False)
T_new, aff_new = run_sim('new', early_close=True)

def stats(T, label, affected=0):
    pnl=T['pnl'].sum(); wr=T['win'].mean(); n=len(T)
    eq=START+T['pnl'].cumsum()
    dd=(eq.cummax()-eq).max()
    min_bal=eq.min()
    print(f'\n  [{label}]')
    print(f'  Сделок:        {n}')
    print(f'  Win Rate:      {wr:.1%}')
    print(f'  PnL:           {"+" if pnl>=0 else ""}${pnl:,.0f}')
    print(f'  Max DD:        ${dd:,.0f}  ({dd/START:.1%})')
    print(f'  Min баланс:    ${min_bal:,.0f}')
    if affected:
        print(f'  Затронуто ранним закрытием: {affected} сделок')

print('='*62)
print('  СРАВНЕНИЕ: пятничное закрытие 21:00 UTC vs 20:45 UTC')
print('  AstraH4Trend v1.2 | Risk=$100 | 2020-2026')
print('='*62)
stats(T_old, 'Старое: закрытие hr>=21 UTC', aff_old)
stats(T_new, 'Новое:  закрытие hr==20&mn>=30 UTC (20:45)', aff_new)

diff_pnl = T_new['pnl'].sum() - T_old['pnl'].sum()
print(f'\n  Разница PnL: {diff_pnl:+,.0f}$')
print(f'  Сделок затронуто: {aff_new} из {len(T_new)} ({aff_new/len(T_new):.1%})')

# По годам
print(f'\n  {"Год":<6}{"Старое PnL":>12}{"Новое PnL":>12}{"Разница":>10}')
print(f'  {"-"*42}')
for yr in range(2020, 2027):
    o = T_old[pd.to_datetime(T_old['date']).dt.year==yr]
    n = T_new[pd.to_datetime(T_new['date']).dt.year==yr]
    if not len(o): continue
    diff = n['pnl'].sum() - o['pnl'].sum()
    flag = f'  {"↑" if diff>0 else "↓" if diff<0 else "="} {abs(diff):.0f}' if diff!=0 else '  ='
    print(f'  {yr:<6}{o["pnl"].sum():>+12,.0f}{n["pnl"].sum():>+12,.0f}{flag}')

# Детальный разбор пятничных сделок затронутых
print(f'\n  Детальный разбор: сделки затронутые ранним закрытием')
print(f'  (т.е. разница между старым и новым результатом)')
print(f'  {"Дата":<12}{"Dir":>6}{"R старый":>10}{"R новый":>10}{"Разница$":>10}')
print(f'  {"-"*50}')

# Найдём разницу по дата+направление
merged = T_old.merge(T_new, on=['date','direction'], suffixes=('_old','_new'))
merged['diff'] = merged['pnl_new'] - merged['pnl_old']
diffs = merged[merged['diff'].abs() > 0.01].copy()
diffs = diffs.sort_values('diff')
print(f'  Строк с разницей: {len(diffs)}')
for _, row in diffs.head(20).iterrows():
    print(f'  {str(row["date"].date()):<12}{row["direction"]:>6}'
          f'{row["r_old"]:>+10.2f}R{row["r_new"]:>+10.2f}R'
          f'{row["diff"]:>+10.0f}$')
if len(diffs) > 20:
    print(f'  ... и ещё {len(diffs)-20} строк')
print(f'\n  Итого разница: {diffs["diff"].sum():+,.0f}$')
print(f'  Выиграли от раннего закрытия: {(diffs["diff"]>0).sum()} сделок')
print(f'  Потеряли от раннего закрытия: {(diffs["diff"]<0).sum()} сделок')
