# -*- coding: utf-8 -*-
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
import pandas as pd, numpy as np, warnings; warnings.filterwarnings('ignore')

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

# Полная equity кривая от $10,000
eq = START + T['pnl'].cumsum()
eq.index = T['date'].values

# Находим все просадки: от пика вниз до минимума и обратно
print('='*68)
print('  ТОП-7 САМЫХ ГЛУБОКИХ ПРОСАДОК | AstraH4Trend v1.2 | 2020-2026')
print('  Старт $10,000 — как падал баланс и когда восстанавливался')
print('='*68)

# Находим все DD периоды
running_max = eq.cummax()
dd = running_max - eq

# Находим топ просадок (уникальных)
dd_vals = dd.values
eq_vals = eq.values
dates_arr = T['date'].values

# Группируем просадки
drawdowns = []
in_dd = False
peak_val = START; peak_idx = 0; dd_start = 0

for i in range(len(dd_vals)):
    if not in_dd and dd_vals[i] > 50:  # начало просадки
        in_dd = True
        # ищем пик перед этим
        peak_val = eq_vals[:i].max() if i > 0 else START
        peak_idx = eq_vals[:i].argmax() if i > 0 else 0
        dd_start = i
    elif in_dd and dd_vals[i] <= 50:  # конец просадки
        in_dd = False
        # найдём минимум в этом периоде
        seg = eq_vals[dd_start:i]
        min_idx = dd_start + seg.argmin()
        min_val = seg.min()
        recovery_date = dates_arr[i]
        peak_date = dates_arr[peak_idx] if peak_idx < len(dates_arr) else dates_arr[0]
        min_date = dates_arr[min_idx]
        drawdowns.append({
            'peak_date': peak_date,
            'peak_val': peak_val,
            'min_date': min_date,
            'min_val': min_val,
            'recovery_date': recovery_date,
            'dd_abs': peak_val - min_val,
            'dd_pct': (peak_val - min_val) / START * 100,
            'recovery_days': (pd.Timestamp(recovery_date) - pd.Timestamp(min_date)).days
        })

drawdowns.sort(key=lambda x: x['dd_abs'], reverse=True)

print(f'\n  {"#":<3} {"Пик":>12} {"Баланс пик":>11} {"Дно":>12} {"Баланс дно":>11} {"Просадка":>9} {"Восстановление":>16}')
print(f'  {"-"*78}')
for idx, d in enumerate(drawdowns[:7], 1):
    rec_str = str(d['recovery_date'])[:10]
    days = d['recovery_days']
    print(f'  {idx:<3} {str(d["peak_date"])[:10]:>12}  ${d["peak_val"]:>9,.0f}'
          f'  {str(d["min_date"])[:10]:>12}  ${d["min_val"]:>9,.0f}'
          f'  ${d["dd_abs"]:>7,.0f}  → восст. {rec_str} ({days}д)')

# Самый интересный случай — декабрь 2020 (старт с 18 дек)
print(f'\n{"="*68}')
print(f'  ДЕТАЛЬНЫЙ РАЗБОР — ХУДШИЙ ПЕРИОД (дек 2020 — янв 2021)')
print(f'  Если бы ты начал 18 декабря 2020 с $10,000')
print(f'{"="*68}')

sd = pd.Timestamp('2020-12-18')
sub = T[T['date'] >= sd].head(60)
eq2 = START + sub['pnl'].cumsum()
daily2 = sub.groupby('date')['pnl'].sum()

running = START
print(f'\n  {"Дата":<12} {"PnL дня":>9}  {"Баланс":>10}  {"Статус"}')
print(f'  {"-"*50}')
for d, pnl in daily2.items():
    running += pnl
    bar = '▼' * int(abs(pnl)//100) if pnl < 0 else '▲' * int(pnl//100)
    status = f'{"▼ " if pnl<0 else "▲ "}{bar}'
    print(f'  {str(d.date()):<12} ${pnl:>+7,.0f}   ${running:>9,.0f}  {status}')
    if d.date() > pd.Timestamp('2021-02-01').date(): break

print(f'\n  Мин баланс в этом периоде: ${eq2.min():,.0f}')
print(f'  Когда восстановился до $10,000+: ', end='')
recovered = eq2[eq2 >= START]
if len(recovered):
    print(f'{str(sub.loc[recovered.index[0], "date"])[:10]}')
else:
    print('ещё не восстановился в этом окне')
