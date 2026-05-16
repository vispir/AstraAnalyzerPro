# -*- coding: utf-8 -*-
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
import pandas as pd, numpy as np, warnings; warnings.filterwarnings('ignore')

TP_R=4.0; SL_MULT=1.2; RISK=100; START=10000
LONG_HOURS=[5,6,7,8,9,13,14,15,16]
SHORT_HOURS=[8,9,13,14,15,16]
TS_L=1.2; STEP_L=0.1; TS_S=0.8; STEP_S=0.1

f_main = r'D:\Works\ASTRA ANALYZER CHART\data_cache\dukascopy\m15\XAUUSD\xauusd_m15_2020-01-01_2026-05-12.parquet'
f_new  = r'D:\Works\ASTRA ANALYZER CHART\data_cache\dukascopy\m15\XAUUSD\xauusd_m15_2026-05-12_2026-05-15.parquet'
df = pd.concat([pd.read_parquet(f_main), pd.read_parquet(f_new)])
df.index = pd.to_datetime(df.index, utc=True)
df = df[~df.index.duplicated(keep='last')].sort_index()
df.columns = [c.lower() for c in df.columns]
df['tr'] = np.maximum(df['high']-df['low'], np.maximum(abs(df['high']-df['close'].shift(1)), abs(df['low']-df['close'].shift(1))))
df['atr'] = df['tr'].ewm(alpha=1/14, adjust=False).mean()
df['hour'] = df.index.hour; df['dow'] = df.index.dayofweek
h4 = df.resample('4h', origin='epoch').agg(close=('close','last')).dropna()
h4['ema20'] = h4['close'].ewm(span=20, adjust=False).mean()
h4['slope3'] = h4['ema20'] - h4['ema20'].shift(3)
h4['h4_up'] = (h4['close'] > h4['ema20']) & (h4['slope3'] > 0)
h4['h4_dn'] = (h4['close'] < h4['ema20']) & (h4['slope3'] < 0)
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
                if hi[j]>=tp_px: result_r=TP_R; break
                r=(hi[j]-entry)/sl_dist
                if r>best_r: best_r=r
                if best_r>=ts:
                    ns=entry+(best_r-step)*sl_dist
                    if ns>cur_sl: cur_sl=ns
            else:
                if hi[j]>=cur_sl: result_r=(entry-cur_sl)/sl_dist; break
                if lo[j]<=tp_px: result_r=TP_R; break
                r=(entry-lo[j])/sl_dist
                if r>best_r: best_r=r
                if best_r>=ts:
                    ns=entry-(best_r-step)*sl_dist
                    if ns<cur_sl: cur_sl=ns
        if result_r is None:
            result_r=(cur_sl-entry)/sl_dist if direction=='long' else (entry-cur_sl)/sl_dist
        trades.append({'date':d,'pnl':result_r*RISK,'win':result_r>0,'direction':direction.upper()})
        traded[(d,direction)]=traded.get((d,direction),0)+1

T = pd.DataFrame(trades)
T['date'] = pd.to_datetime(T['date'])
daily = T.groupby('date')['pnl'].sum().reset_index()
daily_vals = daily['pnl'].values
daily_dates = daily['date'].values

window = 10  # 2 недели = 10 торговых дней
results = []
for k in range(len(daily_vals)-window+1):
    s = daily_vals[k:k+window].sum()
    results.append((s, str(daily_dates[k])[:10], str(daily_dates[k+window-1])[:10]))
results.sort(key=lambda x: x[0])

print('='*62)
print('  ТОП-5 ХУДШИХ 2 НЕДЕЛЬ | AstraH4Trend v1.2 | Risk=$100')
print('='*62)
print(f'  Сравнение: старая Session Breakout дала -$933 за 4-15 мая')
print()
print(f'  {"#":<3} {"Период":<28} {"PnL":>8}  {"Баланс":>10}')
print(f'  {"-"*52}')
for idx, (pnl, d1, d2) in enumerate(results[:5], 1):
    print(f'  {idx:<3} {d1} -- {d2}  ${pnl:>7,.0f}  ${START+pnl:>9,.0f}')

worst_pnl = results[0][0]
print(f'\n  За 6 лет худшие 2 недели: ${worst_pnl:,.0f}')
print(f'  Баланс в худшем случае:   ${START+worst_pnl:,.0f}')
print(f'  Это {abs(worst_pnl)/START:.1%} от $10,000 -- флор $9,000 {"НЕ ПРОБИТ" if START+worst_pnl>=9000 else "ПРОБИТ"}')

prob = (1-0.546)**10
print(f'\n  Вероятность WR=0% на 10 сделках (как со старой): {prob:.4%}')
print(f'  Это значит: 1 раз из {int(1/prob):,} стартов')
print(f'\n  Макс потеря в день: -$200 (лимит 2 сделки/день x $100)')
print(f'  Теоретический макс за 2 недели: -$2,000')
print(f'  Реальный максимум за 6 лет:     ${worst_pnl:,.0f}')
