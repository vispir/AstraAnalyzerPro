# -*- coding: utf-8 -*-
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
import pandas as pd, numpy as np, warnings
warnings.filterwarnings('ignore')
from datetime import date as date_cls

TP_R=4.0; SL_MULT=1.2; TRAIL_START=0.8; TRAIL_STEP=0.3; MAX_BARS=400; RISK=120
LONG_HOURS=[5,6,7,8,9,13,14,15,16]; SHORT_HOURS=[8,9,13,14,15,16]
START_BAL=10000.0

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

h4 = df.resample('4h', origin='epoch').agg(
    open=('open','first'), high=('high','max'),
    low=('low','min'), close=('close','last')).dropna()
h4['ema20']  = h4['close'].ewm(span=20, adjust=False).mean()
h4['slope3'] = h4['ema20'] - h4['ema20'].shift(3)
h4['h4_up']  = (h4['close'] > h4['ema20']) & (h4['slope3'] > 0)
h4['h4_dn']  = (h4['close'] < h4['ema20']) & (h4['slope3'] < 0)

def mmap(s): return s.shift(1).reindex(df.index, method='ffill')
df['h4_up']   = mmap(h4['h4_up'])
df['h4_dn']   = mmap(h4['h4_dn'])
df['h4_ema20']= mmap(h4['ema20'])
df['hour'] = df.index.hour
df['dow']  = df.index.dayofweek

N=len(df)
hi=df['high'].values; lo=df['low'].values; cl=df['close'].values
atr=df['atr'].values; h4u=df['h4_up'].values; h4d=df['h4_dn'].values
hr=df['hour'].values; dow=df['dow'].values; dates=df.index.date

trades=[]; traded={}
for i in range(300, N-MAX_BARS-1):
    atr_v=atr[i]
    if atr_v<=0 or np.isnan(atr_v): continue
    date=dates[i]; is_fri=(dow[i]==4)
    for direction in ['long','short']:
        if direction=='long':
            if not (h4u[i] and hr[i] in LONG_HOURS): continue
            if traded.get((date,'long'),0)>=1: continue
        else:
            if not (h4d[i] and hr[i] in SHORT_HOURS): continue
            if traded.get((date,'short'),0)>=1: continue
        sl_dist=atr_v*SL_MULT; entry=cl[i]
        cur_sl=entry-sl_dist if direction=='long' else entry+sl_dist
        tp_px =entry+TP_R*sl_dist if direction=='long' else entry-TP_R*sl_dist
        best_r=0.0; result_r=None; exit_reason='timeout'
        for j in range(i+1, min(i+MAX_BARS+1, N)):
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
                if best_r>=TRAIL_START:
                    ns=entry+(best_r-TRAIL_STEP)*sl_dist
                    if ns>cur_sl: cur_sl=ns
            else:
                if hi[j]>=cur_sl: result_r=(entry-cur_sl)/sl_dist; exit_reason='SL'; break
                if lo[j]<=tp_px:  result_r=TP_R; exit_reason='TP'; break
                r=(entry-lo[j])/sl_dist
                if r>best_r: best_r=r
                if best_r>=TRAIL_START:
                    ns=entry-(best_r-TRAIL_STEP)*sl_dist
                    if ns<cur_sl: cur_sl=ns
        if result_r is None:
            result_r=(cur_sl-entry)/sl_dist if direction=='long' else (entry-cur_sl)/sl_dist
        exit_px = entry+result_r*sl_dist if direction=='long' else entry-result_r*sl_dist
        sl_init = entry-atr_v*SL_MULT if direction=='long' else entry+atr_v*SL_MULT
        trades.append({
            'date': date, 'r': result_r, 'win': result_r>0,
            'direction': direction.upper(), 'hour': hr[i],
            'entry': entry, 'sl_init': sl_init, 'exit': exit_px,
            'pnl': result_r*RISK, 'reason': exit_reason,
            'h4_ema20': df['h4_ema20'].iloc[i], 'dow': dow[i],
        })
        traded[(date,direction)] = traded.get((date,direction),0)+1

T = pd.DataFrame(trades)
may_s = date_cls(2026,5,4); may_e = date_cls(2026,5,15)
T_may = T[(T['date']>=may_s) & (T['date']<=may_e)].copy()

DOW = ['Пн','Вт','Ср','Чт','Пт','Сб','Вс']

print('='*72)
print('  БЭКТЕСТ 4-15 МАЯ 2026  |  AstraH4Trend v1.1  |  Risk=$120')
print('  С пятницей (принудительное закрытие до 21:00 UTC)')
print('='*72)

print('\n  H4 тренд по дням:')
for day in pd.date_range('2026-05-04','2026-05-15'):
    d = day.date()
    if day.dayofweek >= 5: continue
    bars = df[df.index.date == d]
    if not len(bars): continue
    r = bars.iloc[0]
    trend = 'UP' if r['h4_up'] else ('DN' if r['h4_dn'] else 'FLAT')
    mark = '  <- ПЯТНИЦА' if day.dayofweek==4 else ''
    print(f'    {d} {DOW[day.dayofweek]}: H4={trend}  close=${r["close"]:.0f}  EMA20=${r["h4_ema20"]:.0f}{mark}')

print(f'\n  {"Дата":<12}{"День":<5}{"Dir":<7}{"Ч":>3}{"Entry":>9}{"Exit":>9}{"SL":>9}{"Причина":<11}{"R":>6}{"PnL":>9}  Баланс')
print(f'  {"-"*82}')
running = START_BAL
for _, t in T_may.sort_values('date').iterrows():
    running += t['pnl']
    sign = '+' if t['pnl'] >= 0 else ''
    dow_n = DOW[t['dow']]
    print(f'  {str(t["date"]):<12}{dow_n:<5}{t["direction"]:<7}{t["hour"]:>3}'
          f'  ${t["entry"]:>7.1f}  ${t["exit"]:>7.1f}  ${t["sl_init"]:>7.1f}'
          f'  {t["reason"]:<11}{t["r"]:>+5.2f}R  {sign}${abs(t["pnl"]):>6.0f}  ${running:>9,.0f}')

if len(T_may):
    n=len(T_may); wr=T_may['win'].mean(); pnl=T_may['pnl'].sum()
    print(f'\n  {"="*55}')
    print(f'  ИТОГО 4-15 мая 2026')
    print(f'  {"="*55}')
    print(f'  Сделок:   {n}')
    print(f'  Win Rate: {wr:.1%}')
    print(f'  PnL:      {"+" if pnl>=0 else ""}${pnl:,.2f}')
    print(f'  Баланс:   ${START_BAL+pnl:,.2f}  (старт $10,000)')
    print(f'\n  По направлениям:')
    for d in ['LONG','SHORT']:
        s = T_may[T_may['direction']==d]
        if len(s):
            print(f'    {d}: {len(s)} сд  WR={s["win"].mean():.0%}  PnL={"+" if s["pnl"].sum()>=0 else ""}${s["pnl"].sum():,.0f}')
    print(f'\n  По дням:')
    print(f'  {"Дата":<12}{"День":<5}{"N":>3}{"PnL":>9}{"Накопл":>10}')
    print(f'  {"-"*42}')
    cumul=0
    for day, grp in T_may.groupby('date'):
        dp=grp['pnl'].sum(); cumul+=dp
        dow_n=DOW[pd.Timestamp(str(day)).dayofweek]
        print(f'  {str(day):<12}{dow_n:<5}{len(grp):>3}  {"+" if dp>=0 else ""}${dp:>6.0f}  ${cumul:>+8.0f}')
