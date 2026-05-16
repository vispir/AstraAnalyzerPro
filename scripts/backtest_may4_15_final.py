# -*- coding: utf-8 -*-
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
import pandas as pd, numpy as np, warnings; warnings.filterwarnings('ignore')

# Параметры EA v1.2 (из советника)
TP_R       = 4.0
SL_MULT    = 1.2
TRAIL_L    = 1.2   # LONG:  TRAIL_START_L
STEP_L     = 0.1   # LONG:  TRAIL_STEP_L
TRAIL_S    = 0.8   # SHORT: TRAIL_START_S
STEP_S     = 0.1   # SHORT: TRAIL_STEP_S
RISK       = 100
LONG_HOURS = [5,6,7,8,9,13,14,15,16]
SHORT_HOURS= [8,9,13,14,15,16]
START      = 10000

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

trades=[]; traded={}
for i in range(300, N-400-1):
    av=atr[i]
    if av<=0 or np.isnan(av): continue
    d=dates[i]; is_fri=(dow[i]==4)
    for direction in ['long','short']:
        if direction=='long':
            if not (h4u[i] and hr[i] in LONG_HOURS): continue
            if traded.get((d,'long'),0)>=1: continue
            ts=TRAIL_L; step=STEP_L
        else:
            if not (h4d[i] and hr[i] in SHORT_HOURS): continue
            if traded.get((d,'short'),0)>=1: continue
            ts=TRAIL_S; step=STEP_S
        sl_dist=av*SL_MULT; entry=cl[i]
        cur_sl=entry-sl_dist if direction=='long' else entry+sl_dist
        tp_px=entry+TP_R*sl_dist if direction=='long' else entry-TP_R*sl_dist
        best_r=0.0; result_r=None; exit_reason=''
        for j in range(i+1, min(i+401,N)):
            # Пятница: новое закрытие в 20:45 UTC (обрабатывается 20:30 бар EA)
            if is_fri and dow[j]==4 and hr[j]==20 and mn[j]>=30:
                result_r=(cl[j]-entry)/sl_dist if direction=='long' else (entry-cl[j])/sl_dist
                exit_reason='fri_20:45'; break
            if is_fri and dow[j]==4 and hr[j]>=21:
                result_r=(cl[j]-entry)/sl_dist if direction=='long' else (entry-cl[j])/sl_dist
                exit_reason='fri_21:00'; break
            if is_fri and dow[j] in [5,6,0]:
                result_r=(cl[j]-entry)/sl_dist if direction=='long' else (entry-cl[j])/sl_dist
                exit_reason='weekend'; break
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
            exit_reason='timeout'
        trades.append({'date':d,'pnl':result_r*RISK,'win':result_r>0,
                       'direction':direction.upper(),'r':result_r,'exit':exit_reason})
        traded[(d,direction)]=traded.get((d,direction),0)+1

T = pd.DataFrame(trades)
T['date'] = pd.to_datetime(T['date'])

# Фильтрация: 4-15 мая 2026
mask = (T['date'] >= '2026-05-04') & (T['date'] <= '2026-05-15')
sub = T[mask].copy()

print('='*62)
print('  БЭКТЕСТ: 4-15 МАЯ 2026 | AstraH4Trend v1.2')
print('  TP=4R | SL=1.2xATR | LONG trail 1.2R/0.1R | SHORT 0.8R/0.1R')
print('  Пятница: закрытие в 20:45 UTC (-15 мин до брокера)')
print('='*62)

balance = START
print(f'\n  {"Дата":<12} {"Dir":>6} {"R":>7}  {"PnL":>8}  {"Баланс":>10}  Выход')
print(f'  {"-"*60}')
for _, row in sub.iterrows():
    balance += row['pnl']
    win_mark = '✓' if row['win'] else '✗'
    print(f'  {str(row["date"].date()):<12} {row["direction"]:>6} '
          f'{row["r"]:>+6.2f}R  ${row["pnl"]:>+6.0f}  ${balance:>9,.0f}  {win_mark} {row["exit"]}')

print(f'\n  {"─"*45}')
n = len(sub)
wins = sub['win'].sum()
pnl = sub['pnl'].sum()
long_sub = sub[sub['direction']=='LONG']
short_sub = sub[sub['direction']=='SHORT']

print(f'  Сделок:    {n}  ({wins}W / {n-wins}L)  WR: {wins/n:.0%}')
print(f'  PnL:       ${pnl:+,.0f}')
print(f'  Баланс:    ${START+pnl:,.0f}')
print(f'  Min бал:   ${(START + sub["pnl"].cumsum()).min():,.0f}')
print(f'\n  LONG:  {len(long_sub)} сделок  WR {long_sub["win"].mean():.0%}  PnL ${long_sub["pnl"].sum():+,.0f}')
print(f'  SHORT: {len(short_sub)} сделок  WR {short_sub["win"].mean():.0%}  PnL ${short_sub["pnl"].sum():+,.0f}')

print(f'\n{"="*62}')
print(f'  ПАРАМЕТРЫ СОВЕТНИКА (проверка):')
print(f'  TP_R={TP_R} | SL_MULT={SL_MULT}x ATR({14})')
print(f'  LONG:  TRAIL_START={TRAIL_L}R  TRAIL_STEP={STEP_L}R')
print(f'  SHORT: TRAIL_START={TRAIL_S}R  TRAIL_STEP={STEP_S}R')
print(f'  Risk:  ${RISK}/сделку')
print(f'  LONG  часы UTC: {LONG_HOURS}')
print(f'  SHORT часы UTC: {SHORT_HOURS}')
print(f'  Пятница: закрытие hr==20 && min>=30 (20:45 UTC)')
