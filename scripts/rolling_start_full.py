# -*- coding: utf-8 -*-
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
import pandas as pd, numpy as np, warnings; warnings.filterwarnings('ignore')
from datetime import date as date_cls, timedelta

TP_R=4.0; SL_MULT=1.2; RISK=100; START=10000; FLOOR=9000
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

# Прогоняем все сделки один раз
print("Запуск полного бэктеста...", flush=True)
trades=[]; traded={}
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
        trades.append({'date':d,'pnl':result_r*RISK,'win':result_r>0})
        traded[(d,direction)]=traded.get((d,direction),0)+1

T = pd.DataFrame(trades)
T['date'] = pd.to_datetime(T['date'])
all_dates = sorted(T['date'].unique())
print(f"Сделок: {len(T)} | Период: {all_dates[0].date()} — {all_dates[-1].date()}")

# Генерируем все даты старта: 1-е и 18-е каждого месяца 2020-2026
start_dates = []
for yr in range(2020, 2027):
    for mo in range(1, 13):
        if yr==2026 and mo>5: break
        for day in [1, 18]:
            try:
                sd = date_cls(yr, mo, day)
                # Сдвигаем на ближайший торговый день вперёд если нужно
                sd_ts = pd.Timestamp(sd)
                # Найдём ближайшую дату в данных >= sd
                future = [d for d in all_dates if d.date() >= sd]
                if future:
                    start_dates.append(future[0].date())
            except:
                pass
start_dates = sorted(set(start_dates))
print(f"Тестируем {len(start_dates)} дат старта...")

results = []
breaches = []

for sd in start_dates:
    sd_ts = pd.Timestamp(sd)
    sub = T[T['date'] >= sd_ts]
    if len(sub) < 5:
        continue
    eq = START + sub['pnl'].cumsum()
    min_bal = eq.min()
    breach = min_bal < FLOOR
    worst_day = sub.groupby('date')['pnl'].sum().min()
    pnl_total = sub['pnl'].sum()

    results.append({
        'start': sd, 'n': len(sub),
        'min_bal': min_bal, 'breach': breach,
        'worst_day': worst_day, 'pnl': pnl_total
    })
    if breach:
        breaches.append({'start': sd, 'min_bal': min_bal})

R = pd.DataFrame(results)

print(f'\n{"="*65}')
print(f'  ROLLING START АНАЛИЗ | AstraH4Trend v1.2 | Risk=$100')
print(f'  Старт $10,000 | Флор $9,000 | 1-е и 18-е каждого месяца')
print(f'{"="*65}')
print(f'  Всего стартов протестировано: {len(R)}')
print(f'  Флор $9,000 пробит:           {R["breach"].sum()} из {len(R)}')
print(f'  Процент безопасных стартов:   {(1-R["breach"].mean()):.1%}')
print(f'  Мин баланс среди всех стартов: ${R["min_bal"].min():,.0f}')

if breaches:
    print(f'\n  ❌ ПРОБОИ ФЛОРА:')
    for b in breaches:
        print(f'    Старт {b["start"]}: мин баланс ${b["min_bal"]:,.0f}')
else:
    print(f'\n  ✅ Ни один старт не пробил флор $9,000!')

# Таблица по годам
print(f'\n  По годам (1-е + 18-е каждого месяца):')
print(f'  {"Год":<6}{"Стартов":>8}{"Пробоев":>9}{"Мин бал":>10}{"Худший день":>13}')
print(f'  {"-"*47}')
for yr in range(2020, 2027):
    ry = R[pd.to_datetime(R['start']).dt.year == yr]
    if not len(ry): continue
    breach_count = ry['breach'].sum()
    flag = ' ❌' if breach_count > 0 else ' ✅'
    print(f'  {yr:<6}{len(ry):>8}{breach_count:>9}{ry["min_bal"].min():>10,.0f}'
          f'{ry["worst_day"].min():>13,.0f}{flag}')

# Отдельно — старты с 18-го (имитация нашей ситуации)
print(f'\n  {"="*60}')
print(f'  СТАРТЫ С 18-го ЧИСЛА (ваша ситуация — 18 мая)')
print(f'  {"="*60}')
R18 = R[pd.to_datetime(R['start']).dt.day >= 15]
print(f'  {"Дата старта":<14}{"Сделок":>7}{"Мин бал":>10}{"Худший день":>13}{"Пробой?":>9}')
print(f'  {"-"*55}')
for _, row in R18.sort_values('start').iterrows():
    flag = '❌' if row['breach'] else '✅'
    print(f'  {str(row["start"]):<14}{row["n"]:>7}{row["min_bal"]:>10,.0f}'
          f'{row["worst_day"]:>13,.0f}{flag:>9}')

print(f'\n  Стартов с 18-го: {len(R18)} | Пробоев: {R18["breach"].sum()} | '
      f'Безопасных: {(1-R18["breach"].mean()):.1%}')
