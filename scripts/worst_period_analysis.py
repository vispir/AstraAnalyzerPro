# -*- coding: utf-8 -*-
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
import pandas as pd, numpy as np, warnings; warnings.filterwarnings('ignore')

TP_R=4.0; SL_MULT=1.2; RISK=100; START=10000
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

def run(ts_l, step_l, ts_s, step_s):
    trades=[]; traded={}
    for i in range(300, N-400-1):
        av=atr[i]
        if av<=0 or np.isnan(av): continue
        d=dates[i]; is_fri=(dow[i]==4)
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
            for j in range(i+1, min(i+401,N)):
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
            trades.append({'date':d,'pnl':result_r*RISK,'win':result_r>0,'direction':direction.upper()})
            traded[(d,direction)]=traded.get((d,direction),0)+1
    return pd.DataFrame(trades)

def analyze_worst(T, label):
    T = T.copy()
    T['date'] = pd.to_datetime(T['date'])
    daily = T.groupby('date')['pnl'].sum().reset_index()
    daily.columns = ['date','pnl']

    # Худший месяц
    daily['ym'] = daily['date'].dt.to_period('M')
    monthly = daily.groupby('ym')['pnl'].sum()
    worst_mo = monthly.idxmin()
    worst_mo_pnl = monthly.min()

    # Худшие 4 недели подряд (скользящее окно 20 торговых дней)
    daily_vals = daily['pnl'].values
    daily_dates = daily['date'].values
    window = 20
    worst_4w = 0; worst_4w_start = None; worst_4w_end = None
    for k in range(len(daily_vals)-window+1):
        s = daily_vals[k:k+window].sum()
        if s < worst_4w:
            worst_4w = s
            worst_4w_start = daily_dates[k]
            worst_4w_end   = daily_dates[k+window-1]

    # Макс подряд SL
    losses = (T['pnl']<0).astype(int).values
    max_c=cur_c=0; streak_end_idx=0; streak_start_idx=0; tmp_start=0
    for idx,v in enumerate(losses):
        if v: cur_c+=1
        else: cur_c=0
        if cur_c==1: tmp_start=idx
        if cur_c>max_c:
            max_c=cur_c; streak_end_idx=idx; streak_start_idx=tmp_start

    streak_start_date = T.iloc[streak_start_idx]['date']
    streak_end_date   = T.iloc[streak_end_idx]['date']

    # Мин баланс
    eq = START + T['pnl'].cumsum()
    min_bal = eq.min()
    min_bal_date = T.iloc[eq.idxmin()]['date']

    print(f'\n{"="*62}')
    print(f'  {label}')
    print(f'{"="*62}')
    print(f'  Худший МЕСЯЦ:      {worst_mo}  →  ${worst_mo_pnl:,.0f}')
    print(f'  Худшие 4 НЕДЕЛИ:   {str(worst_4w_start)[:10]} — {str(worst_4w_end)[:10]}  →  ${worst_4w:,.0f}')
    print(f'  Макс подряд SL:    {max_c} сделок  (${max_c*RISK:,.0f} потеря серией)')
    print(f'    Период серии:    {str(streak_start_date)[:10]} — {str(streak_end_date)[:10]}')
    print(f'  Мин баланс:        ${min_bal:,.0f}  ({str(min_bal_date)[:10]})')
    print(f'  Макс просадка от $10k: ${START-min_bal:,.0f}  ({(START-min_bal)/START:.1%})')

    # Худший месяц детально
    print(f'\n  Детали худшего месяца ({worst_mo}):')
    mo_trades = T[T['date'].dt.to_period('M')==worst_mo]
    print(f'    Сделок: {len(mo_trades)}  WR={mo_trades["win"].mean():.0%}  PnL=${mo_trades["pnl"].sum():,.0f}')
    mo_daily = daily[daily['ym']==worst_mo][['date','pnl']]
    bad_days = mo_daily[mo_daily['pnl']<0]
    print(f'    Убыточных дней: {len(bad_days)}  Худший: ${bad_days["pnl"].min():,.0f}')

    # По годам — минимальный
    T['year'] = T['date'].dt.year
    yr_pnl = T.groupby('year')['pnl'].sum()
    worst_yr = yr_pnl.idxmin()
    print(f'\n  Худший ГОД:        {worst_yr}  →  +${yr_pnl[worst_yr]:,.0f}')
    print(f'\n  ── Все годы ────────────────────────────────')
    for yr, pnl in yr_pnl.items():
        bar = '█' * int(pnl/500)
        sign = '+' if pnl>=0 else ''
        print(f'    {yr}: {sign}${pnl:>6,.0f}  {bar}')

T_old = run(0.8, 0.3, 0.8, 0.3)
T_new = run(1.2, 0.1, 0.8, 0.1)

print('АНАЛИЗ ХУДШИХ ПЕРИОДОВ | Risk=$100 | 2020-2026')
analyze_worst(T_old, 'СТАРЫЕ: LONG 0.8R/0.3R | SHORT 0.8R/0.3R')
analyze_worst(T_new, 'НОВЫЕ:  LONG 1.2R/0.1R | SHORT 0.8R/0.1R')

# Сравнение итогов
print(f'\n{"="*62}')
print(f'  ИТОГ: что лучше защищает?')
print(f'{"="*62}')
old_eq = START + T_old['pnl'].cumsum()
new_eq = START + T_new['pnl'].cumsum()
print(f'  {"Метрика":<30} {"Старые":>10} {"Новые":>10}')
print(f'  {"-"*52}')
print(f'  {"Мин баланс":<30} ${old_eq.min():>8,.0f} ${new_eq.min():>8,.0f}')
print(f'  {"Макс потеря от $10k":<30} ${START-old_eq.min():>8,.0f} ${START-new_eq.min():>8,.0f}')
old_daily=T_old.groupby('date')['pnl'].sum(); new_daily=T_new.groupby('date')['pnl'].sum()
print(f'  {"Худший день":<30} ${old_daily.min():>8,.0f} ${new_daily.min():>8,.0f}')
old_mo=T_old.copy(); old_mo['date']=pd.to_datetime(old_mo['date']); old_mo=old_mo.groupby(old_mo['date'].dt.to_period('M'))['pnl'].sum()
new_mo=T_new.copy(); new_mo['date']=pd.to_datetime(new_mo['date']); new_mo=new_mo.groupby(new_mo['date'].dt.to_period('M'))['pnl'].sum()
print(f'  {"Худший месяц":<30} ${old_mo.min():>8,.0f} ${new_mo.min():>8,.0f}')
