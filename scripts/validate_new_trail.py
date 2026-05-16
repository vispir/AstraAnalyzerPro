# -*- coding: utf-8 -*-
import sys, io, calendar
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
import pandas as pd, numpy as np, warnings; warnings.filterwarnings('ignore')
from datetime import date as date_cls

TS_L=1.2; STEP_L=0.1; TS_S=0.8; STEP_S=0.1
TP_R=4.0; SL_MULT=1.2; MAX_BARS=400; RISK=100
LONG_HOURS=[5,6,7,8,9,13,14,15,16]; SHORT_HOURS=[8,9,13,14,15,16]

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
N=len(df); hi=df['high'].values; lo=df['low'].values; cl=df['close'].values
atr=df['atr'].values; h4u=df['h4_up'].values; h4d=df['h4_dn'].values
hr=df['hour'].values; dow=df['dow'].values; dates=df.index.date

def run(d_from=None, d_to=None):
    trades=[]; traded={}
    for i in range(300, N-MAX_BARS-1):
        av=atr[i]
        if av<=0 or np.isnan(av): continue
        d=dates[i]
        if d_from and d < d_from: continue
        if d_to   and d > d_to:   continue
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
            best_r=0.0; result_r=None
            for j in range(i+1, min(i+MAX_BARS+1,N)):
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
                           'direction':direction.upper(),'r':result_r})
            traded[(d,direction)]=traded.get((d,direction),0)+1
    return pd.DataFrame(trades)

DOW=['Пн','Вт','Ср','Чт','Пт','Сб','Вс']

# ── ПОЛНЫЙ БЭКТЕСТ 2020-2026 ──────────────────────────────────────────
T = run()
eq = 10000 + T['pnl'].cumsum()
dd_series = eq.cummax() - eq
max_dd = dd_series.max()
max_dd_peak = max_dd / eq.cummax()[dd_series.idxmax()]
daily = T.groupby('date')['pnl'].sum()
worst_day = daily.min()
worst_day_date = daily.idxmin()
losses = (T['pnl']<0).astype(int).values
max_c=cur_c=0
for v in losses:
    cur_c=cur_c+1 if v else 0
    max_c=max(max_c,cur_c)

print('='*65)
print('  ВАЛИДАЦИЯ | LONG trail 1.2/0.1 | SHORT trail 0.8/0.1')
print('='*65)
print(f'\n  ПОЛНЫЙ БЭКТЕСТ 2020-2026 | Risk=$100')
print(f'  Сделок:   {len(T):,}  |  WR: {T["win"].mean():.1%}')
print(f'  PnL:      +${T["pnl"].sum():,.0f}')
print(f'  Баланс:   ${10000+T["pnl"].sum():,.0f}')

print(f'\n  ── ПРОСАДКИ ────────────────────────────────────────')
chk1 = "✅" if max_dd/10000 < 0.10 else "❌"
chk2 = "✅" if abs(worst_day)/10000 < 0.05 else "❌"
chk3 = "✅" if abs(worst_day)/10000 < 0.10 else "❌"
print(f'  Max DD абс:      ${max_dd:,.0f}')
print(f'  Max DD % старта: {max_dd/10000:.2%}   {chk1} (лимит 10%)')
print(f'  Max DD % пика:   {max_dd_peak:.2%}')
print(f'  Худший ДЕНЬ:     ${worst_day:,.0f}  ({worst_day_date})')
print(f'    — {abs(worst_day)/10000:.2%} от $10k  {chk2} (лимит 5%)  {chk3} (лимит 10%)')
print(f'  Макс подряд SL:  {max_c} сделок  (${max_c*RISK:,.0f} макс потеря серией)')
print(f'  Мин баланс:      ${eq.min():,.0f}')

print(f'\n  ── ПО НАПРАВЛЕНИЯМ ─────────────────────────────────')
for d in ['LONG','SHORT']:
    s=T[T['direction']==d]
    print(f'  {d}: {len(s)} сд  WR={s["win"].mean():.0%}  PnL=+${s["pnl"].sum():,.0f}')

print(f'\n  ── ПО ГОДАМ ────────────────────────────────────────')
print(f'  {"Год":<6}{"N":>4}  {"WR":>5}  {"PnL":>10}  {"MaxDD":>9}  {"Нак":>10}')
print(f'  {"-"*50}')
cumul=0
for yr in range(2020,2027):
    yt=T[pd.to_datetime(T['date']).dt.year==yr]
    if not len(yt): continue
    ye=10000+yt['pnl'].cumsum(); ydd=(ye.cummax()-ye).max()
    yp=yt['pnl'].sum(); cumul+=yp
    ok='✅' if yp>0 else '❌'
    print(f'  {yr:<6}{len(yt):>4}  {yt["win"].mean():>5.0%}  +${yp:>8,.0f}  ${ydd:>8,.0f}  +${cumul:>8,.0f} {ok}')

# ── 2026 ──────────────────────────────────────────────────────────────
T26=run(date_cls(2026,1,1), date_cls(2026,5,15))
eq26=10000+T26['pnl'].cumsum(); dd26=(eq26.cummax()-eq26).max()
daily26=T26.groupby('date')['pnl'].sum()
mn_names=['','Янв','Фев','Мар','Апр','Май']

print(f'\n{"="*65}')
print(f'  2026 ГОД (янв — 15 мая) | Risk=$100')
print(f'{"="*65}')
print(f'  Сделок: {len(T26)}  WR={T26["win"].mean():.0%}  PnL=+${T26["pnl"].sum():,.0f}')
print(f'  Баланс: ${10000+T26["pnl"].sum():,.0f}  |  Max DD: ${dd26:,.0f} ({dd26/10000:.1%})')
print(f'  Мин бал: ${eq26.min():,.0f}  |  Худший день: ${daily26.min():,.0f}')
print(f'\n  По месяцам:')
print(f'  {"Мес":<6}{"N":>4}  {"WR":>5}  {"PnL":>10}  {"Нак":>10}')
print(f'  {"-"*38}')
cumul26=0
for mo in range(1,6):
    ms=date_cls(2026,mo,1)
    last=calendar.monthrange(2026,mo)[1]
    me=date_cls(2026,mo, 15 if mo==5 else last)
    tm=T26[(T26['date']>=ms)&(T26['date']<=me)]
    if not len(tm): continue
    tp=tm['pnl'].sum(); cumul26+=tp
    print(f'  {mn_names[mo]:<6}{len(tm):>4}  {tm["win"].mean():>5.0%}  +${tp:>8,.0f}  +${cumul26:>8,.0f}')

# ── МАЙ 4-8 ──────────────────────────────────────────────────────────
Tm=run(date_cls(2026,5,4), date_cls(2026,5,8))
eqm=10000+Tm['pnl'].cumsum()

print(f'\n{"="*65}')
print(f'  МАЙ 4-8, 2026 | Risk=$100')
print(f'{"="*65}')
print(f'  {"Дата":<12}{"День":<5}{"Dir":<7}{"R":>7}  {"PnL":>8}  Баланс')
print(f'  {"-"*52}')
running=10000.0
for _,t in Tm.sort_values('date').iterrows():
    running+=t['pnl']
    dow_n=DOW[pd.Timestamp(str(t['date'])).dayofweek]
    sign='+' if t['pnl']>=0 else ''
    print(f'  {str(t["date"]):<12}{dow_n:<5}{t["direction"]:<7}{t["r"]:>+6.2f}R  '
          f'{sign}${abs(t["pnl"]):>6.0f}  ${running:>9,.0f}')
print(f'\n  Итого: {len(Tm)} сд  WR={Tm["win"].mean():.0%}  PnL=+${Tm["pnl"].sum():,.0f}  Мин бал=${eqm.min():,.0f}')

# ── СРАВНЕНИЕ ─────────────────────────────────────────────────────────
print(f'\n{"="*65}')
print(f'  СРАВНЕНИЕ ПАРАМЕТРОВ (2020-2026)')
print(f'{"="*65}')
print(f'  {"Метрика":<22}  {"Старые 0.8/0.3":>15}  {"Новые L1.2 S0.8 /0.1":>20}')
print(f'  {"-"*60}')
rows = [
    ('PnL', '$42,945', f'${T["pnl"].sum():,.0f}'),
    ('Max DD', '$1,176 (11.8%)', f'${max_dd:,.0f} ({max_dd/10000:.1%})'),
    ('Max DD % пика', '3.9%', f'{max_dd_peak:.1%}'),
    ('WR', '59.7%', f'{T["win"].mean():.1%}'),
    ('Худший день', '-$200', f'${worst_day:,.0f}'),
    ('Макс подряд SL', '8 сд', f'{max_c} сд'),
    ('PnL LONG', '$29,213', f'${T[T["direction"]=="LONG"]["pnl"].sum():,.0f}'),
    ('PnL SHORT', '$13,732', f'${T[T["direction"]=="SHORT"]["pnl"].sum():,.0f}'),
]
for name,old,new in rows:
    print(f'  {name:<22}  {old:>15}  {new:>20}')
