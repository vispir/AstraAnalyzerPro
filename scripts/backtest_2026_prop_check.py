# -*- coding: utf-8 -*-
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
import pandas as pd, numpy as np, warnings; warnings.filterwarnings('ignore')
from datetime import date as date_cls

TP_R=4.0; SL_MULT=1.2; RISK=100
START_BAL = 10000.0
PROP_MAX_DD_PCT = 0.10   # 10% от стартового баланса
PROP_DAILY_DD_PCT = 0.05  # 5% от стартового баланса
PROP_MAX_DD_ABS = START_BAL * PROP_MAX_DD_PCT   # $1,000
PROP_DAILY_DD_ABS = START_BAL * PROP_DAILY_DD_PCT  # $500

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

def run(d_from, d_to, ts_l, step_l, ts_s, step_s):
    trades=[]; traded={}
    for i in range(300, N-1):
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
            trades.append({'date':d,'direction':direction.upper(),
                           'pnl':result_r*RISK,'win':result_r>0,'r':result_r})
            traded[(d,direction)]=traded.get((d,direction),0)+1
    return pd.DataFrame(trades)

def analyze(T, label):
    daily = T.groupby('date')['pnl'].sum()
    eq = START_BAL + T['pnl'].cumsum()
    min_bal = eq.min()
    max_bal_ever = START_BAL  # для prop DD считается от стартового баланса

    # Prop Max DD: максимальное падение баланса от $10,000
    max_loss_from_start = START_BAL - min_bal  # если баланс падал ниже старта

    # Худший день
    worst_day = daily.min()
    worst_day_date = daily.idxmin()

    # Дни нарушающие 5% лимит
    bad_daily = daily[daily < -PROP_DAILY_DD_ABS]

    # Нарушение 10% лимита (баланс ниже $9,000)
    floor_breach = (eq < START_BAL * 0.90).any()

    print(f'\n{"="*65}')
    print(f'  {label}')
    print(f'{"="*65}')
    print(f'  Сделок: {len(T)}  |  WR: {T["win"].mean():.0%}  |  PnL: +${T["pnl"].sum():,.0f}')
    print(f'  Баланс: ${START_BAL+T["pnl"].sum():,.0f}  |  Мин баланс: ${min_bal:,.0f}')

    print(f'\n  ── ПРОП ЛИМИТЫ ─────────────────────────────────────')
    print(f'  Стартовый баланс:  ${START_BAL:,.0f}')
    print(f'  Флор 10% (нельзя ниже): ${START_BAL*0.90:,.0f}')
    print(f'  Лимит дня 5%:      -${PROP_DAILY_DD_ABS:,.0f}')

    chk_max = "✅ PASS" if not floor_breach else "❌ FAIL — баланс падал ниже $9,000!"
    chk_day = "✅ PASS" if len(bad_daily)==0 else f"❌ FAIL — {len(bad_daily)} дней нарушают лимит"

    print(f'\n  Max DD (10% от $10k):  ', end='')
    print(f'Мин баланс = ${min_bal:,.0f}  {chk_max}')
    print(f'  Дневной DD (5% от $10k): Худший день = ${worst_day:,.0f} ({worst_day_date})  {chk_day}')

    if len(bad_daily) > 0:
        print(f'  Дни с нарушением:')
        for d, v in bad_daily.items():
            print(f'    {d}: ${v:,.0f}')

    print(f'\n  ── ПО МЕСЯЦАМ ──────────────────────────────────────')
    print(f'  {"Мес":<8}{"N":>4}  {"WR":>5}  {"PnL":>9}  {"Худший день":>12}  {"Мин бал":>10}  {"Нак":>10}')
    print(f'  {"-"*60}')
    cumul = START_BAL
    months = {'01':'Янв','02':'Фев','03':'Мар','04':'Апр','05':'Май'}
    for mo in range(1, 6):
        ms = date_cls(2026, mo, 1)
        me = date_cls(2026, mo, 15) if mo==5 else date_cls(2026, mo, 28 if mo==2 else 30 if mo in [4] else 31)
        tm = T[(T['date']>=ms)&(T['date']<=me)]
        if not len(tm): continue
        tp = tm['pnl'].sum()
        cumul += tp
        dm = daily[(daily.index>=ms)&(daily.index<=me)]
        wd = dm.min() if len(dm) else 0
        eq_m = START_BAL + tm['pnl'].cumsum()  # упрощённо от старта месяца
        eq_running = START_BAL + T[T['date']<=me]['pnl'].sum()
        mo_name = f'{mo:02d}'
        print(f'  {months[mo_name]:<8}{len(tm):>4}  {tm["win"].mean():>5.0%}  ${tp:>7,.0f}  ${wd:>10,.0f}  ${cumul:>10,.0f}  +${tp:>8,.0f}')

d_from = date_cls(2026, 1, 1)
d_to   = date_cls(2026, 5, 15)

T_old = run(d_from, d_to, 0.8, 0.3, 0.8, 0.3)
T_new = run(d_from, d_to, 1.2, 0.1, 0.8, 0.1)

print('='*65)
print('  БЭКТЕСТ 1 ЯНВ — 15 МАЯ 2026  |  Prop Check  |  Risk=$100')
print('='*65)
print(f'  Prop лимиты: Max DD < 10% ($9,000 флор)  |  Дневной DD < 5% (-$500/день)')

analyze(T_old, 'СТАРЫЕ ПАРАМЕТРЫ: LONG trail 0.8R/0.3R | SHORT trail 0.8R/0.3R')
analyze(T_new, 'НОВЫЕ ПАРАМЕТРЫ:  LONG trail 1.2R/0.1R | SHORT trail 0.8R/0.1R')

# Итоговое сравнение
print(f'\n{"="*65}')
print(f'  ИТОГОВОЕ СРАВНЕНИЕ (янв–15 мая 2026)')
print(f'{"="*65}')
old_daily = T_old.groupby('date')['pnl'].sum()
new_daily = T_new.groupby('date')['pnl'].sum()
old_eq = START_BAL + T_old['pnl'].cumsum()
new_eq = START_BAL + T_new['pnl'].cumsum()
print(f'  {"Метрика":<30}  {"Старые 0.8/0.3":>14}  {"Новые L1.2 S0.8/0.1":>18}')
print(f'  {"-"*64}')
print(f'  {"PnL":<30}  ${T_old["pnl"].sum():>13,.0f}  ${T_new["pnl"].sum():>17,.0f}')
print(f'  {"Мин баланс":<30}  ${old_eq.min():>13,.0f}  ${new_eq.min():>17,.0f}')
print(f'  {"Худший день":<30}  ${old_daily.min():>13,.0f}  ${new_daily.min():>17,.0f}')
print(f'  {"Флор $9,000 пробит?":<30}  {"❌ ДА" if (old_eq<9000).any() else "✅ НЕТ":>14}  {"❌ ДА" if (new_eq<9000).any() else "✅ НЕТ":>18}')
print(f'  {"День > -$500?":<30}  {"❌ ДА" if (old_daily < -500).any() else "✅ НЕТ":>14}  {"❌ ДА" if (new_daily < -500).any() else "✅ НЕТ":>18}')
print(f'  {"WR":<30}  {T_old["win"].mean():>14.1%}  {T_new["win"].mean():>18.1%}')
print(f'  {"Сделок":<30}  {len(T_old):>14}  {len(T_new):>18}')
