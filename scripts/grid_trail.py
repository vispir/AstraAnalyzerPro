# -*- coding: utf-8 -*-
"""
Grid search по параметрам трейлинг-стопа.
Отдельные параметры для LONG и SHORT.
Метрика: PnL / MaxDD (profit factor просадки) + все года прибыльны.
"""
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
import pandas as pd, numpy as np, warnings, itertools
warnings.filterwarnings('ignore')

TP_R=4.0; SL_MULT=1.2; MAX_BARS=400; RISK=100
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
atr_v=df['atr'].values; h4u=df['h4_up'].values; h4d=df['h4_dn'].values
hr=df['hour'].values; dow=df['dow'].values; dates=df.index.date

# ── ШАГ 1: находим все входы один раз ─────────────────────────────────
print("Поиск входов...", flush=True)
entries = []
traded = {}
for i in range(300, N-MAX_BARS-1):
    av = atr_v[i]
    if av <= 0 or np.isnan(av): continue
    d = dates[i]; is_fri = (dow[i]==4)
    for direction in ['long','short']:
        if direction=='long':
            if not (h4u[i] and hr[i] in LONG_HOURS): continue
            if traded.get((d,'long'),0) >= 1: continue
        else:
            if not (h4d[i] and hr[i] in SHORT_HOURS): continue
            if traded.get((d,'short'),0) >= 1: continue
        sl_dist = av * SL_MULT
        entry_px = cl[i]
        # Записываем путь цены (hi/lo) на следующие MAX_BARS баров
        end = min(i+MAX_BARS+1, N)
        path_hi = hi[i+1:end].copy()
        path_lo = lo[i+1:end].copy()
        path_cl = cl[i+1:end].copy()
        path_dow = dow[i+1:end].copy()
        path_hr  = hr[i+1:end].copy()
        entries.append({
            'i': i, 'date': d, 'direction': direction,
            'entry': entry_px, 'sl_dist': sl_dist,
            'is_fri': is_fri,
            'path_hi': path_hi, 'path_lo': path_lo,
            'path_cl': path_cl, 'path_dow': path_dow, 'path_hr': path_hr,
            'year': pd.Timestamp(str(d)).year,
        })
        traded[(d,direction)] = traded.get((d,direction),0)+1

print(f"Найдено {len(entries)} входов. Запуск grid search...", flush=True)

# ── ШАГ 2: функция симуляции по заранее найденным входам ──────────────
def simulate(entries, ts_l, step_l, ts_s, step_s):
    """ts=trail_start, step=trail_step для LONG и SHORT"""
    results = []
    for e in entries:
        d = e['direction']
        ts   = ts_l   if d=='long' else ts_s
        step = step_l if d=='long' else step_s

        entry   = e['entry']
        sl_dist = e['sl_dist']
        is_fri  = e['is_fri']
        ph = e['path_hi']; pl = e['path_lo']
        pc = e['path_cl']; pd_ = e['path_dow']; phr = e['path_hr']

        cur_sl = entry - sl_dist if d=='long' else entry + sl_dist
        tp_px  = entry + TP_R*sl_dist if d=='long' else entry - TP_R*sl_dist
        best_r = 0.0; result_r = None

        for k in range(len(ph)):
            # Пятница
            if is_fri and pd_[k]==4 and phr[k]>=21:
                result_r = (pc[k]-entry)/sl_dist if d=='long' else (entry-pc[k])/sl_dist; break
            if is_fri and pd_[k] in [5,6,0]:
                result_r = (pc[k]-entry)/sl_dist if d=='long' else (entry-pc[k])/sl_dist; break

            if d=='long':
                if pl[k] <= cur_sl: result_r=(cur_sl-entry)/sl_dist; break
                if ph[k] >= tp_px:  result_r=TP_R; break
                r = (ph[k]-entry)/sl_dist
                if r > best_r: best_r=r
                if best_r >= ts:
                    ns = entry + (best_r-step)*sl_dist
                    if ns > cur_sl: cur_sl=ns
            else:
                if ph[k] >= cur_sl: result_r=(entry-cur_sl)/sl_dist; break
                if pl[k] <= tp_px:  result_r=TP_R; break
                r = (entry-pl[k])/sl_dist
                if r > best_r: best_r=r
                if best_r >= ts:
                    ns = entry - (best_r-step)*sl_dist
                    if ns < cur_sl: cur_sl=ns

        if result_r is None:
            result_r = (cur_sl-entry)/sl_dist if d=='long' else (entry-cur_sl)/sl_dist

        results.append({'year': e['year'], 'direction': d,
                        'pnl': result_r*RISK, 'win': result_r>0})
    return results

# ── ШАГ 3: параметры grid ──────────────────────────────────────────────
# trail_start: когда включается трейлинг (в R)
# trail_step:  насколько SL отстаёт от пика (в R)
# Ограничение: step < start (иначе SL уйдёт за вход в убыток)
STARTS = [0.5, 0.8, 1.0, 1.2, 1.5, 2.0, 3.0]
STEPS  = [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.8]

results_all = []
total = sum(1 for sl,ss,ss2,ss3 in itertools.product(STARTS,STEPS,STARTS,STEPS)
            if ss < sl and ss3 < ss2)
done = 0

for ts_l, step_l in itertools.product(STARTS, STEPS):
    if step_l >= ts_l: continue
    for ts_s, step_s in itertools.product(STARTS, STEPS):
        if step_s >= ts_s: continue
        done += 1
        if done % 50 == 0:
            print(f"  {done}/{total}...", flush=True)

        res = simulate(entries, ts_l, step_l, ts_s, step_s)
        T = pd.DataFrame(res)
        pnl   = T['pnl'].sum()
        eq    = 10000 + T['pnl'].cumsum()
        dd    = (eq.cummax()-eq).max()
        wr    = T['win'].mean()
        ratio = pnl/dd if dd > 0 else 0

        # Все годы прибыльны?
        yr_pnl = T.groupby('year')['pnl'].sum()
        all_yr_pos = (yr_pnl > 0).all()

        # LONG и SHORT по отдельности
        tl = T[T['direction']=='long']
        ts_df = T[T['direction']=='short']
        pnl_l = tl['pnl'].sum(); pnl_s = ts_df['pnl'].sum()

        results_all.append({
            'ts_l': ts_l, 'step_l': step_l,
            'ts_s': ts_s, 'step_s': step_s,
            'pnl': pnl, 'dd': dd, 'wr': wr,
            'ratio': ratio, 'all_yr': all_yr_pos,
            'pnl_l': pnl_l, 'pnl_s': pnl_s,
            'n': len(T),
        })

R = pd.DataFrame(results_all)
R_valid = R[R['all_yr']]  # только варианты где все годы в плюсе

print(f"\nВсего комбинаций: {len(R)}  Все годы в плюсе: {len(R_valid)}")

# ── ШАГ 4: вывод топ результатов ──────────────────────────────────────
print('\n' + '='*80)
print('  ТОП-15 ПО PnL/DD  (все годы прибыльны)')
print('='*80)
print(f'  {"Start_L":>8}{"Step_L":>8}{"Start_S":>8}{"Step_S":>8}'
      f'{"PnL":>10}{"DD":>8}{"PnL/DD":>8}{"WR":>6}{"PnL_L":>9}{"PnL_S":>9}')
print(f'  {"-"*76}')
for _, row in R_valid.nlargest(15,'ratio').iterrows():
    print(f'  {row["ts_l"]:>8.1f}{row["step_l"]:>8.1f}{row["ts_s"]:>8.1f}{row["step_s"]:>8.1f}'
          f'  ${row["pnl"]:>7,.0f}  ${row["dd"]:>5,.0f}  {row["ratio"]:>6.1f}'
          f'  {row["wr"]:>5.0%}  ${row["pnl_l"]:>7,.0f}  ${row["pnl_s"]:>7,.0f}')

print('\n' + '='*80)
print('  ТОП-10 ПО PnL  (все годы прибыльны)')
print('='*80)
print(f'  {"Start_L":>8}{"Step_L":>8}{"Start_S":>8}{"Step_S":>8}'
      f'{"PnL":>10}{"DD":>8}{"PnL/DD":>8}{"WR":>6}{"PnL_L":>9}{"PnL_S":>9}')
print(f'  {"-"*76}')
for _, row in R_valid.nlargest(10,'pnl').iterrows():
    print(f'  {row["ts_l"]:>8.1f}{row["step_l"]:>8.1f}{row["ts_s"]:>8.1f}{row["step_s"]:>8.1f}'
          f'  ${row["pnl"]:>7,.0f}  ${row["dd"]:>5,.0f}  {row["ratio"]:>6.1f}'
          f'  {row["wr"]:>5.0%}  ${row["pnl_l"]:>7,.0f}  ${row["pnl_s"]:>7,.0f}')

# Текущий для сравнения
cur = R[(R['ts_l']==0.8)&(R['step_l']==0.3)&(R['ts_s']==0.8)&(R['step_s']==0.3)]
if len(cur):
    row = cur.iloc[0]
    print(f'\n  Текущий (0.8/0.3 для обоих):')
    print(f'  PnL=${row["pnl"]:,.0f}  DD=${row["dd"]:,.0f}  PnL/DD={row["ratio"]:.1f}  WR={row["wr"]:.0%}')
    print(f'  LONG: ${row["pnl_l"]:,.0f}  SHORT: ${row["pnl_s"]:,.0f}')
