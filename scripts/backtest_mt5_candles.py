# -*- coding: utf-8 -*-
import sys, io, json
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
import pandas as pd, numpy as np, warnings; warnings.filterwarnings('ignore')

# v1.2 параметры
TS_L=1.2; STEP_L=0.1; TS_S=0.8; STEP_S=0.1
TP_R=4.0; SL_MULT=1.2; RISK=100
LONG_HOURS=[5,6,7,8,9,13,14,15,16]
SHORT_HOURS=[8,9,13,14,15,16]

# Загрузка MT5 свечей
with open(r"D:\Works\ASTRA ANALYZER CHART\mt5 candles\mt5_candles_all.json", encoding='utf-8') as f:
    raw = json.load(f)

df = pd.DataFrame(raw)
df['time'] = pd.to_datetime(df['time'], utc=True)
df = df.set_index('time').sort_index()
df = df[~df.index.duplicated(keep='last')]
for col in ['open','high','low','close']:
    df[col] = df[col].astype(float)

print(f"Загружено: {len(df)} свечей")
print(f"Период:   {df.index[0]} ... {df.index[-1]}")

# Проверка непрерывности
gaps = df.index.to_series().diff().dropna()
big_gaps = gaps[gaps > pd.Timedelta('30min')]
if len(big_gaps):
    print(f"\nГэпы в данных (>30 мин):")
    for t, g in big_gaps.items():
        print(f"  {t}: пропуск {g}")
else:
    print("Гэпов нет — данные непрерывны")

# ATR и H4
df['tr'] = np.maximum(df['high']-df['low'],
           np.maximum(abs(df['high']-df['close'].shift(1)),
                      abs(df['low'] -df['close'].shift(1))))
df['atr'] = df['tr'].ewm(alpha=1/14, adjust=False).mean()
df['hour'] = df.index.hour
df['dow']  = df.index.dayofweek

h4 = df.resample('4h', origin='epoch').agg(close=('close','last')).dropna()
h4['ema20']  = h4['close'].ewm(span=20, adjust=False).mean()
h4['slope3'] = h4['ema20'] - h4['ema20'].shift(3)
h4['h4_up']  = (h4['close'] > h4['ema20']) & (h4['slope3'] > 0)
h4['h4_dn']  = (h4['close'] < h4['ema20']) & (h4['slope3'] < 0)

def mmap(s): return s.shift(1).reindex(df.index, method='ffill')
df['h4_up'] = mmap(h4['h4_up'])
df['h4_dn'] = mmap(h4['h4_dn'])

N=len(df)
hi=df['high'].values; lo=df['low'].values; cl=df['close'].values
atr=df['atr'].values; h4u=df['h4_up'].values; h4d=df['h4_dn'].values
hr=df['hour'].values; dow=df['dow'].values; dates=df.index.date

# Бэктест
trades=[]; traded={}
WARMUP=200  # меньше warmup т.к. данных мало

for i in range(WARMUP, N-1):
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
        trades.append({'date':d,'direction':direction.upper(),
                       'hour':hr[i],'r':result_r,
                       'pnl':result_r*RISK,'win':result_r>0})
        traded[(d,direction)]=traded.get((d,direction),0)+1

DOW=['Пн','Вт','Ср','Чт','Пт','Сб','Вс']
T=pd.DataFrame(trades)

print(f'\n{"="*65}')
print(f'  БЭКТЕСТ НА MT5-СВЕЧАХ | AstraH4Trend v1.2 | Risk=$100')
print(f'{"="*65}')

if not len(T):
    print("  Нет сделок.")
else:
    eq=10000+T['pnl'].cumsum()
    daily=T.groupby('date')['pnl'].sum()
    print(f'  Сделок: {len(T)}  WR={T["win"].mean():.0%}  PnL=+${T["pnl"].sum():,.0f}')
    print(f'  Баланс: ${eq.iloc[-1]:,.0f}  |  Мин баланс: ${eq.min():,.0f}')
    print(f'  Худший день: ${daily.min():,.0f}  |  Флор $9,000: {"✅ НЕ ПРОБИТ" if eq.min()>=9000 else "❌ ПРОБИТ"}')

    print(f'\n  {"Дата":<12}{"День":<5}{"Dir":<7}{"Ч":>3}  {"R":>7}  {"PnL":>8}  Баланс')
    print(f'  {"-"*55}')
    running=10000.0
    for _,t in T.iterrows():
        running+=t['pnl']
        dow_n=DOW[pd.Timestamp(str(t['date'])).dayofweek]
        sign='+' if t['pnl']>=0 else ''
        print(f'  {str(t["date"]):<12}{dow_n:<5}{t["direction"]:<7}{t["hour"]:>3}'
              f'  {t["r"]:>+6.2f}R  {sign}${abs(t["pnl"]):>6.0f}  ${running:>9,.0f}')

    print(f'\n  По направлениям:')
    for d in ['LONG','SHORT']:
        s=T[T['direction']==d]
        if len(s):
            print(f'    {d}: {len(s)} сд  WR={s["win"].mean():.0%}  PnL=${s["pnl"].sum():+,.0f}')
