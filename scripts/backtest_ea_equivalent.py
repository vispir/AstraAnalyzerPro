# -*- coding: utf-8 -*-
"""
Бэктест "как советник" — rolling 2000 M15 баров для H4 EMA.
Сравнивает сигналы и PnL с полным бэктестом (вся история).
"""
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
import pandas as pd, numpy as np, warnings
warnings.filterwarnings('ignore')

TP_R=4.0; SL_MULT=1.2; MAX_BARS=400; RISK=100
TRAIL_START_L=1.2; TRAIL_STEP_L=0.1   # LONG  — как в EA v1.2
TRAIL_START_S=0.8; TRAIL_STEP_S=0.1   # SHORT — как в EA v1.2
LONG_HOURS=[5,6,7,8,9,13,14,15,16]
SHORT_HOURS=[8,9,13,14,15,16]
EA_M15_BARS = 2000   # EA грузит 2000 M15 баров при старте

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
df['hour'] = df.index.hour
df['dow']  = df.index.dayofweek

# ── 1. ПОЛНАЯ история (бэктест-стиль) ────────────────────────────────────────
h4_full = df.resample('4h', origin='epoch').agg(close=('close','last')).dropna()
h4_full['ema20']  = h4_full['close'].ewm(span=20, adjust=False).mean()
h4_full['slope3'] = h4_full['ema20'] - h4_full['ema20'].shift(3)
h4_full['h4_up']  = (h4_full['close'] > h4_full['ema20']) & (h4_full['slope3'] > 0)
h4_full['h4_dn']  = (h4_full['close'] < h4_full['ema20']) & (h4_full['slope3'] < 0)

def mmap(s): return s.shift(1).reindex(df.index, method='ffill')
df['h4_up_full'] = mmap(h4_full['h4_up'])
df['h4_dn_full'] = mmap(h4_full['h4_dn'])

# ── 2. EA-стиль: rolling 2000 M15 → ~125 H4 баров ───────────────────────────
# Для каждого H4 бара пересчитываем EMA с нуля по последним EA_H4_WINDOW барам.
# EA_H4_WINDOW = 2000 M15 / 16 (баров в H4) ≈ 125
EA_H4_WINDOW = EA_M15_BARS // 16  # 125

alpha_h4 = 2.0 / (20 + 1)
h4_closes = h4_full['close'].values
N_H4 = len(h4_closes)

ea_ema = np.full(N_H4, np.nan)
for i in range(N_H4):
    start = max(0, i - EA_H4_WINDOW + 1)
    window = h4_closes[start:i+1]
    if len(window) < 25:          # MIN_H4_BARS = 25 как в EA
        continue
    e = window[0]
    for v in window[1:]:
        e = alpha_h4 * v + (1 - alpha_h4) * e
    ea_ema[i] = e

h4_ea = h4_full.copy()
h4_ea['ema20_ea']  = ea_ema
h4_ea['slope3_ea'] = np.where(
    np.isnan(ea_ema) | np.isnan(np.roll(ea_ema, 3)),
    np.nan,
    ea_ema - np.concatenate([[np.nan]*3, ea_ema[:-3]])
)
h4_ea['h4_up_ea'] = (h4_ea['close'] > h4_ea['ema20_ea']) & (h4_ea['slope3_ea'] > 0)
h4_ea['h4_dn_ea'] = (h4_ea['close'] < h4_ea['ema20_ea']) & (h4_ea['slope3_ea'] < 0)

df['h4_up_ea'] = mmap(h4_ea['h4_up_ea'])
df['h4_dn_ea'] = mmap(h4_ea['h4_dn_ea'])

# ── 3. Сравнение сигналов ────────────────────────────────────────────────────
mask = df['h4_up_full'].notna() & df['h4_up_ea'].notna()
diff_up = (df.loc[mask,'h4_up_full'] != df.loc[mask,'h4_up_ea']).sum()
diff_dn = (df.loc[mask,'h4_dn_full'] != df.loc[mask,'h4_dn_ea']).sum()
total   = mask.sum()

print('='*65)
print('  СРАВНЕНИЕ СИГНАЛОВ: полная история vs EA (rolling 2000 M15)')
print('='*65)
print(f'  Всего M15 баров для сравнения: {total:,}')
print(f'  Расхождений H4_UP:  {diff_up:,}  ({diff_up/total:.4%})')
print(f'  Расхождений H4_DN:  {diff_dn:,}  ({diff_dn/total:.4%})')
print(f'  Совпадений:         {total-diff_up-diff_dn:,}  ({(total-diff_up-diff_dn)/total:.4%})')

# ── 4. Торговая симуляция (одинаковая для обеих версий) ──────────────────────
N = len(df)
hi=df['high'].values; lo=df['low'].values; cl=df['close'].values
atr=df['atr'].values; hr=df['hour'].values; dow=df['dow'].values
dates=df.index.date

def run_sim(h4u, h4d, label):
    trades=[]; traded={}
    for i in range(300, N-MAX_BARS-1):
        av=atr[i]
        if av<=0 or np.isnan(av): continue
        date=dates[i]; is_fri=(dow[i]==4)
        for direction in ['long','short']:
            if direction=='long':
                if not (h4u[i] and hr[i] in LONG_HOURS): continue
                if traded.get((date,'long'),0)>=1: continue
            else:
                if not (h4d[i] and hr[i] in SHORT_HOURS): continue
                if traded.get((date,'short'),0)>=1: continue
            sl_dist=av*SL_MULT; entry=cl[i]
            cur_sl=entry-sl_dist if direction=='long' else entry+sl_dist
            tp_px =entry+TP_R*sl_dist if direction=='long' else entry-TP_R*sl_dist
            best_r=0.0; result_r=None; exit_reason='timeout'
            for j in range(i+1, min(i+MAX_BARS+1,N)):
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
                    if best_r>=TRAIL_START_L:
                        ns=entry+(best_r-TRAIL_STEP_L)*sl_dist
                        if ns>cur_sl: cur_sl=ns
                else:
                    if hi[j]>=cur_sl: result_r=(entry-cur_sl)/sl_dist; exit_reason='SL'; break
                    if lo[j]<=tp_px:  result_r=TP_R; exit_reason='TP'; break
                    r=(entry-lo[j])/sl_dist
                    if r>best_r: best_r=r
                    if best_r>=TRAIL_START_S:
                        ns=entry-(best_r-TRAIL_STEP_S)*sl_dist
                        if ns<cur_sl: cur_sl=ns
            if result_r is None:
                result_r=(cur_sl-entry)/sl_dist if direction=='long' else (entry-cur_sl)/sl_dist
            trades.append({
                'date': date, 'r': result_r, 'win': result_r>0,
                'direction': direction.upper(), 'pnl': result_r*RISK,
                'reason': exit_reason, 'dow': dow[i],
            })
            traded[(date,direction)] = traded.get((date,direction),0)+1
    return pd.DataFrame(trades)

h4u_full = df['h4_up_full'].values.astype(bool)
h4d_full = df['h4_dn_full'].values.astype(bool)
h4u_ea   = df['h4_up_ea'].fillna(False).values.astype(bool)
h4d_ea   = df['h4_dn_ea'].fillna(False).values.astype(bool)

print('\n  Запуск симуляций...')
T_full = run_sim(h4u_full, h4d_full, 'full')
T_ea   = run_sim(h4u_ea,   h4d_ea,   'ea')

# ── 5. Сравнение результатов ─────────────────────────────────────────────────
def stats(T, label):
    if not len(T): return
    pnl=T['pnl'].sum(); wr=T['win'].mean(); n=len(T)
    eq=10000+T['pnl'].cumsum(); dd=(eq.cummax()-eq).max()
    print(f'\n  [{label}]')
    print(f'  Сделок:   {n}')
    print(f'  Win Rate: {wr:.1%}')
    print(f'  PnL:      {"+" if pnl>=0 else ""}${pnl:,.0f}')
    print(f'  Max DD:   ${dd:,.0f}  ({dd/10000:.1%})')

print('\n' + '='*65)
print('  РЕЗУЛЬТАТЫ 2020-2026  |  Risk=$100')
print('='*65)
stats(T_full, 'Полная история (бэктест)')
stats(T_ea,   'EA-стиль (rolling 2000 M15)')

# Разница в сделках
merged = T_full.merge(T_ea, on=['date','direction'], suffixes=('_f','_ea'), how='outer')
diff_trades = merged['pnl_f'].sub(merged['pnl_ea']).abs()
print(f'\n  Разница в PnL между версиями: ${(T_full["pnl"].sum() - T_ea["pnl"].sum()):+,.0f}')
print(f'  Сделок только в full:  {T_full.shape[0] - T_ea.shape[0]}')

# По годам
print('\n  По годам (EA-стиль vs Полная история):')
print(f'  {"Год":<6}{"EA PnL":>10}{"Full PnL":>10}{"Разница":>10}{"EA N":>6}{"Full N":>7}')
print(f'  {"-"*50}')
for yr in range(2020, 2027):
    f = T_full[pd.to_datetime(T_full['date']).dt.year == yr]
    e = T_ea  [pd.to_datetime(T_ea['date']  ).dt.year == yr]
    if not len(f) and not len(e): continue
    fp=f['pnl'].sum(); ep=e['pnl'].sum()
    print(f'  {yr:<6}{ep:>+10,.0f}{fp:>+10,.0f}{ep-fp:>+10,.0f}{len(e):>6}{len(f):>7}')
