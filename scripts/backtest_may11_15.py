# -*- coding: utf-8 -*-
"""
Честная симуляция мая 11-15, 2026.
Входы по тем же правилам, выходы по SL/trail/TP.
Если данные кончаются — закрытие по последней цене с пометкой data_end.
"""
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
import pandas as pd, numpy as np, warnings
warnings.filterwarnings('ignore')
from datetime import date as date_cls

TP_R=4.0; SL_MULT=1.2; TRAIL_START=0.8; TRAIL_STEP=0.3; RISK=120
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

N = len(df)
hi=df['high'].values; lo=df['low'].values; cl=df['close'].values
atr=df['atr'].values; h4u=df['h4_up'].values; h4d=df['h4_dn'].values
hr=df['hour'].values; dow=df['dow'].values; dates=df.index.date

# Диапазон: только май 11-14 (15 — нет данных)
sim_start = date_cls(2026,5,11)
sim_end   = date_cls(2026,5,14)

trades = []
traded = {}

# Ищем бары входа — без ограничения N-MAX_BARS (данные используем до последнего)
for i in range(300, N-1):
    av = atr[i]
    if av <= 0 or np.isnan(av): continue
    date = dates[i]
    if date < sim_start or date > sim_end: continue

    is_fri = (dow[i] == 4)

    for direction in ['long', 'short']:
        if direction == 'long':
            if not (h4u[i] and hr[i] in LONG_HOURS): continue
            if traded.get((date,'long'), 0) >= 1: continue
        else:
            if not (h4d[i] and hr[i] in SHORT_HOURS): continue
            if traded.get((date,'short'), 0) >= 1: continue

        sl_dist = av * SL_MULT
        entry   = cl[i]
        cur_sl  = entry - sl_dist if direction=='long' else entry + sl_dist
        tp_px   = entry + TP_R * sl_dist if direction=='long' else entry - TP_R * sl_dist

        best_r = 0.0; result_r = None; exit_reason = 'data_end'

        for j in range(i+1, N):
            # Пятница 21:00 UTC
            if dow[j] == 4 and hr[j] >= 21:
                result_r = (cl[j]-entry)/sl_dist if direction=='long' else (entry-cl[j])/sl_dist
                exit_reason = 'fri_close'; break
            # Понедельник после выходных
            if dow[j] in [5, 6, 0] and dow[i] == 4:
                result_r = (cl[j]-entry)/sl_dist if direction=='long' else (entry-cl[j])/sl_dist
                exit_reason = 'fri_close'; break

            if direction == 'long':
                if lo[j] <= cur_sl:
                    result_r = (cur_sl-entry)/sl_dist; exit_reason = 'SL'; break
                if hi[j] >= tp_px:
                    result_r = TP_R; exit_reason = 'TP'; break
                r = (hi[j]-entry)/sl_dist
                if r > best_r: best_r = r
                if best_r >= TRAIL_START:
                    ns = entry + (best_r-TRAIL_STEP)*sl_dist
                    if ns > cur_sl: cur_sl = ns
            else:
                if hi[j] >= cur_sl:
                    result_r = (entry-cur_sl)/sl_dist; exit_reason = 'SL'; break
                if lo[j] <= tp_px:
                    result_r = TP_R; exit_reason = 'TP'; break
                r = (entry-lo[j])/sl_dist
                if r > best_r: best_r = r
                if best_r >= TRAIL_START:
                    ns = entry - (best_r-TRAIL_STEP)*sl_dist
                    if ns < cur_sl: cur_sl = ns

        # Если данные кончились — закрываем по trailing SL (или initial SL)
        if result_r is None:
            result_r = (cur_sl-entry)/sl_dist if direction=='long' else (entry-cur_sl)/sl_dist
            exit_reason = 'data_end'

        exit_px = entry + result_r*sl_dist if direction=='long' else entry - result_r*sl_dist
        pnl = result_r * RISK

        trades.append({
            'date': date, 'direction': direction.upper(),
            'hour': hr[i], 'entry': entry,
            'sl_init': entry-sl_dist if direction=='long' else entry+sl_dist,
            'exit_px': exit_px, 'result_r': result_r,
            'pnl': pnl, 'win': result_r > 0,
            'reason': exit_reason,
        })
        traded[(date,direction)] = traded.get((date,direction), 0) + 1

DOW = ['Пн','Вт','Ср','Чт','Пт','Сб','Вс']

print('='*72)
print('  МАЙ 11-14, 2026  |  AstraH4Trend v1.1  |  Честная симуляция')
print('  Выходы: SL / Trail / TP / fri_close / data_end (последняя цена)')
print('='*72)

# Итоги мая 4-8 (уже известны)
may4_8_pnl = 712.84
may4_8_trades = 6
bal_start_phase2 = 10000 + may4_8_pnl

print(f'\n  Старт фазы 2 (11 мая): ${bal_start_phase2:,.2f}  (после мая 4-8: +${may4_8_pnl:.2f})')
print()

if not trades:
    print('  Нет сделок в периоде.')
else:
    print(f'  {"Дата":<12}{"День":<5}{"Dir":<7}{"Ч":>3}{"Entry":>9}{"Exit":>9}{"SL":>9}  {"Причина":<12}{"R":>6}{"PnL":>9}  Баланс')
    print(f'  {"-"*85}')
    running = bal_start_phase2
    for t in sorted(trades, key=lambda x: (x['date'], x['hour'])):
        running += t['pnl']
        sign = '+' if t['pnl'] >= 0 else ''
        dow_n = DOW[pd.Timestamp(str(t['date'])).dayofweek]
        note = ' ⚠' if t['reason'] == 'data_end' else ''
        print(f'  {str(t["date"]):<12}{dow_n:<5}{t["direction"]:<7}{t["hour"]:>3}'
              f'  ${t["entry"]:>7.1f}  ${t["exit_px"]:>7.1f}  ${t["sl_init"]:>7.1f}'
              f'  {t["reason"]:<12}{t["result_r"]:>+5.2f}R  {sign}${abs(t["pnl"]):>6.0f}  ${running:>9,.2f}{note}')

    T = pd.DataFrame(trades)
    n=len(T); wr=T['win'].mean(); pnl=T['pnl'].sum()

    print(f'\n  {"="*60}')
    print(f'  ИТОГО 11-14 мая:  {n} сд  WR={wr:.0%}  PnL={"+" if pnl>=0 else ""}${pnl:,.2f}')

    data_end_count = (T['reason']=='data_end').sum()
    if data_end_count:
        print(f'  ⚠  {data_end_count} сделок закрыты по data_end (данные кончились 15 мая)')
        print(f'     В реальности они продолжились бы дальше.')

    print(f'\n  {"="*60}')
    print(f'  ИТОГО 4-15 МАЯ (полный период):')
    print(f'  {"="*60}')
    total_trades = may4_8_trades + n
    total_pnl = may4_8_pnl + pnl
    final_bal = 10000 + total_pnl
    print(f'  Сделок:  {total_trades}')
    print(f'  PnL:     {"+" if total_pnl>=0 else ""}${total_pnl:,.2f}')
    print(f'  Баланс:  ${final_bal:,.2f}  (старт $10,000)')
