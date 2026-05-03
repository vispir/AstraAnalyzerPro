"""
Grid search: SHORT session breakout — complement to LONG strategy.

LONG фиксирован (asian 03-06, london 08-11, ny 15-18, TP=12R, Risk=$100).
SHORT перебирается: окна сессий × TP × ATR buffer × H4 медвежий фильтр.
Метрики DD считаются по ОБЪЕДИНЁННОМУ балансу LONG+SHORT.

SHORT логика (зеркало LONG):
  Вход:    close < range_LOW  (пробой вниз)
  SL:      range_HIGH + ATR_BUFFER * atr  (выше диапазона)
  TP:      entry - rsk * TP_RR  (ниже входа)
  Trailing: HIGH-trigger (консервативно): rr = (entry - high) / risk
             SL опускается: sl = min(sl, entry - lock * risk)
  P&L SL:  (entry - sl_price) * size  → отрицательно если sl > entry ✅
  P&L TP:  (entry - tp) * size        → положительно т.к. tp < entry ✅

Нет look-ahead:
  - Диапазон SHORT строится так же как LONG — накопление bar-by-bar
  - Вход только ПОСЛЕ завершения окна range (entry_start = range_end)
  - H4 EMA — тот же ptr_closed (только закрытые H4 бары)
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
import numpy as np
from pathlib import Path
import importlib.util
import time

# ── Загрузка LONG стратегии (для констант и индикаторов) ──────────────────────
_sp = Path(__file__).parent.parent / "astra_v2" / "strategies" / "session_long_nolookahead_v1.py"
spec = importlib.util.spec_from_file_location("strat", _sp)
strat = importlib.util.module_from_spec(spec)
spec.loader.exec_module(strat)

# ── Константы LONG (фиксированные) ────────────────────────────────────────────
RISK_L        = strat.RISK          # $100
TP_RR_L       = strat.TP_RR         # 12R
ATR_BUFFER_L  = strat.ATR_BUFFER    # 0.5
ATR_PERIOD    = strat.ATR_PERIOD    # 14
H4_EMA_PERIOD = strat.H4_EMA_PERIOD # 20
SLOPE_N       = strat.SLOPE_N       # 5
MIN_H4_BARS   = strat.MIN_H4_BARS   # 22
K_EMA         = strat.K_EMA
H4_NS         = strat.H4_NS

LONG_SESSIONS = {
    'asian':  {'range_hours': (3,  6),  'entry_start':  6, 'entry_end': 24},
    'london': {'range_hours': (8,  11), 'entry_start': 11, 'entry_end': 24},
    'ny':     {'range_hours': (15, 18), 'entry_start': 18, 'entry_end': 24},
}

# ── SHORT trailing шаги (HIGH-trigger, консервативно для SHORT) ───────────────
# rr = (entry - high) / risk  →  price ниже entry на rr R
# sl = min(sl, entry - lock * risk)  →  SL опускается
TRAIL_STEPS_S = [(i, i-1) for i in range(int(TP_RR_L)-1, 1, -1)]
# Для каждого TP будем генерировать динамически

# ── SHORT grid параметры ──────────────────────────────────────────────────────
# Окна для SHORT (диапазон UTC)
SHORT_ASIAN_RANGES  = [(0,3),(2,5),(3,6),(5,8),(7,10),(8,11)]
SHORT_LONDON_RANGES = [(6,9),(7,10),(8,11),(10,13),(13,16)]
SHORT_NY_RANGES     = [(12,15),(13,16),(14,17),(15,18),(16,19)]

SHORT_TP_VALUES     = [5, 7, 10, 12]
SHORT_ATR_BUFFERS   = [0.3, 0.5, 1.0]
SHORT_H4_FILTERS    = [True, False]   # True = требовать медвежий H4 EMA
SHORT_RISK          = 100.0           # риск SHORT = $100

SESSION_RANGES = {
    'asian_s':  SHORT_ASIAN_RANGES,
    'london_s': SHORT_LONDON_RANGES,
    'ny_s':     SHORT_NY_RANGES,
}


# ── Трейлинг SHORT (HIGH-trigger) ─────────────────────────────────────────────
def _trail_short(t: dict, high: float) -> None:
    """
    Trailing для SHORT: триггер по HIGH бара (консервативно).
    rr = (entry - high) / risk — сколько R в прибыли (HIGH = худшая цена для SHORT).
    SL опускается: sl = min(sl, entry - lock * risk).
    """
    risk = t['initial_sl'] - t['entry']   # SL выше entry → risk > 0
    rr   = (t['entry'] - high) / risk     # > 0 когда high < entry (SHORT в прибыли)
    for trigger, lock in t['trail_steps']:
        if rr >= trigger:
            # SL опускается: новый SL = entry - lock*risk (в зоне прибыли)
            t['sl'] = min(t['sl'], t['entry'] - lock * risk)
            break


# ── Объединённый бэктест LONG + SHORT ─────────────────────────────────────────
def run_combined(df: pd.DataFrame,
                 h4_times: np.ndarray,
                 h4_ema20: np.ndarray,
                 short_sess_name: str,
                 short_range: tuple,
                 tp_s: float,
                 atrbuf_s: float,
                 use_h4_bearish: bool) -> dict:
    """
    Запускает LONG (фиксированный) + SHORT (один short_sess_name) одновременно.
    Оба используют единый баланс → DD считается по общей эквити.

    LONG P&L:
      SL hit: (sl_price - entry) * size  → sl < entry → отрицательно = убыток ✅
      TP hit: (tp - entry) * size        → tp > entry → положительно = прибыль ✅

    SHORT P&L:
      SL hit: (entry - sl_price) * size  → sl > entry → отрицательно = убыток ✅
      TP hit: (entry - tp) * size        → tp < entry → положительно = прибыль ✅
    """
    trail_steps_s = [(i, i-1) for i in range(int(tp_s)-1, 1, -1)]

    n_h4     = len(h4_times)
    times_ns = df.index.asi8
    m15      = df.to_numpy()
    col      = {c: i for i, c in enumerate(df.columns)}
    i_h = col['high']; i_l = col['low']; i_c = col['close']; i_a = col['atr']

    ptr_closed     = -1
    forming_period = -1
    forming_close  = np.nan
    ema_base       = np.nan

    active_long  = {}   # sn -> LONG trade
    active_short = {}   # sn -> SHORT trade (только short_sess_name)

    balance      = 10_000.0
    peak         = 10_000.0
    max_dd       = 0.0
    max_daily_dd = 0.0
    prev_date    = None
    day_start    = balance

    # Раздельные диапазоны для LONG и SHORT
    ls_highs = {}; ls_lows = {}   # LONG session ranges
    ss_highs = {}; ss_lows = {}   # SHORT session ranges

    trades_l = []   # LONG trades
    trades_s = []   # SHORT trades

    sr_start, sr_end = short_range   # SHORT range hours

    for i in range(len(df)):
        ts_ns  = int(times_ns[i])
        cur_ts = df.index[i]
        high   = float(m15[i, i_h])
        low    = float(m15[i, i_l])
        close  = float(m15[i, i_c])
        atr    = float(m15[i, i_a])
        hour   = cur_ts.hour

        if np.isnan(atr):
            continue

        # ── Дневной сброс ─────────────────────────────────────────────────────
        cur_date = cur_ts.date()
        if cur_date != prev_date:
            if prev_date is not None and day_start > 0:
                dd = (day_start - balance) / day_start * 100
                if dd > max_daily_dd: max_daily_dd = dd
            day_start = balance
            prev_date = cur_date
            ls_highs = {}; ls_lows = {}
            ss_highs = {}; ss_lows = {}

        # ── H4 указатель (только закрытые бары) ──────────────────────────────
        while ptr_closed + 1 < n_h4 and h4_times[ptr_closed + 1] + H4_NS <= ts_ns:
            ptr_closed += 1

        # ── Warmup: только накапливаем диапазоны, сделок нет ─────────────────
        if ptr_closed < MIN_H4_BARS - 1:
            for sn, p in LONG_SESSIONS.items():
                sh, eh = p['range_hours']
                if sh <= hour < eh:
                    ls_highs[sn] = max(ls_highs.get(sn, 0),   high)
                    ls_lows[sn]  = min(ls_lows.get(sn, 1e9),  low)
            if sr_start <= hour < sr_end:
                ss_highs[short_sess_name] = max(ss_highs.get(short_sess_name, 0),   high)
                ss_lows[short_sess_name]  = min(ss_lows.get(short_sess_name, 1e9),  low)
            continue

        # ── H4 EMA (forming bar) ──────────────────────────────────────────────
        h4p = int(ts_ns // int(14400 * 1e9)) * int(14400 * 1e9)
        if h4p != forming_period:
            forming_period = h4p
            forming_close  = close
            ema_base = h4_ema20[ptr_closed] if ptr_closed >= 0 else np.nan
        else:
            forming_close = close

        if np.isnan(ema_base):
            continue

        h4_ema = forming_close * K_EMA + ema_base * (1.0 - K_EMA)

        # ── Фильтры LONG (бычий H4) ───────────────────────────────────────────
        ema_ok_l   = forming_close > ema_base
        slope_ok_l = (ptr_closed >= SLOPE_N
                      and not np.isnan(h4_ema20[ptr_closed - SLOPE_N])
                      and h4_ema > h4_ema20[ptr_closed - SLOPE_N])

        # ── Фильтры SHORT (медвежий H4, опционально) ──────────────────────────
        if use_h4_bearish:
            ema_ok_s   = forming_close < ema_base
            slope_ok_s = (ptr_closed >= SLOPE_N
                          and not np.isnan(h4_ema20[ptr_closed - SLOPE_N])
                          and h4_ema < h4_ema20[ptr_closed - SLOPE_N])
            short_filter_ok = ema_ok_s and slope_ok_s
        else:
            short_filter_ok = True

        # ── Управление LONG сделками (ВСЕГДА, независимо от фильтров) ─────────
        for sn in list(active_long.keys()):
            t = active_long[sn]
            strat._trail(t, low)         # trailing LONG: LOW-trigger
            if low <= t['sl']:
                # SL hit: sl < entry → pnl = (sl - entry)*size < 0 = убыток ✅
                t['pnl'] = (t['sl'] - t['entry']) * t['size']
                balance += t['pnl']; t['year'] = cur_ts.year
                trades_l.append(t); del active_long[sn]
            elif high >= t['tp']:
                # TP hit: tp > entry → pnl = (tp - entry)*size > 0 = прибыль ✅
                t['pnl'] = (t['tp'] - t['entry']) * t['size']
                balance += t['pnl']; t['year'] = cur_ts.year
                trades_l.append(t); del active_long[sn]

        # ── Управление SHORT сделками (ВСЕГДА, независимо от фильтров) ────────
        for sn in list(active_short.keys()):
            t = active_short[sn]
            _trail_short(t, high)        # trailing SHORT: HIGH-trigger
            if high >= t['sl']:
                # SL hit: sl > entry изначально → pnl = (entry - sl)*size < 0 = убыток ✅
                # После trailing sl < entry → pnl > 0 = прибыль ✅
                t['pnl'] = (t['entry'] - t['sl']) * t['size']
                balance += t['pnl']; t['year'] = cur_ts.year
                trades_s.append(t); del active_short[sn]
            elif low <= t['tp']:
                # TP hit: tp < entry → pnl = (entry - tp)*size > 0 = прибыль ✅
                t['pnl'] = (t['entry'] - t['tp']) * t['size']
                balance += t['pnl']; t['year'] = cur_ts.year
                trades_s.append(t); del active_short[sn]

        # ── DD на объединённом балансе ─────────────────────────────────────────
        if balance > peak: peak = balance
        dd = (peak - balance) / peak * 100
        if dd > max_dd: max_dd = dd

        # ── Накопление LONG диапазонов ─────────────────────────────────────────
        for sn, p in LONG_SESSIONS.items():
            sh, eh = p['range_hours']
            if sh <= hour < eh:
                ls_highs[sn] = max(ls_highs.get(sn, 0),   high)
                ls_lows[sn]  = min(ls_lows.get(sn, 1e9),  low)

        # ── Накопление SHORT диапазонов ────────────────────────────────────────
        # Диапазон SHORT формируется bar-by-bar в своё окно
        # Вход SHORT только ПОСЛЕ завершения окна (entry_start = sr_end)
        if sr_start <= hour < sr_end:
            ss_highs[short_sess_name] = max(ss_highs.get(short_sess_name, 0),   high)
            ss_lows[short_sess_name]  = min(ss_lows.get(short_sess_name, 1e9),  low)

        # ── Новые LONG входы ──────────────────────────────────────────────────
        if ema_ok_l and slope_ok_l:
            for sn, p in LONG_SESSIONS.items():
                if sn not in ls_highs or sn in active_long:
                    continue
                if not (p['entry_start'] <= hour < p['entry_end']):
                    continue
                if close > ls_highs[sn]:
                    sl_l  = ls_lows[sn] - ATR_BUFFER_L * atr
                    rsk_l = close - sl_l
                    if rsk_l <= 0: continue
                    # sl_l < close < tp_l — правильно для LONG ✅
                    active_long[sn] = {
                        'entry':      close,
                        'sl':         sl_l,
                        'initial_sl': sl_l,
                        'tp':         close + rsk_l * TP_RR_L,
                        'size':       RISK_L / rsk_l,
                        'session':    sn,
                    }

        # ── Новые SHORT входы ──────────────────────────────────────────────────
        if short_filter_ok:
            sn = short_sess_name
            # Вход только после окна формирования (hour >= sr_end)
            if (hour >= sr_end and sn in ss_highs
                    and sn not in active_short
                    and close < ss_lows[sn]):
                sl_s  = ss_highs[sn] + atrbuf_s * atr
                rsk_s = sl_s - close            # sl_s > close → rsk_s > 0 ✅
                if rsk_s > 0:
                    tp_s_price = close - rsk_s * tp_s  # tp < close < sl_s ✅
                    active_short[sn] = {
                        'entry':      close,
                        'sl':         sl_s,
                        'initial_sl': sl_s,
                        'tp':         tp_s_price,
                        'size':       SHORT_RISK / rsk_s,
                        'session':    sn,
                        'trail_steps': trail_steps_s,
                    }

    # ── Последний дневной DD ───────────────────────────────────────────────────
    if prev_date is not None and day_start > 0:
        dd = (day_start - balance) / day_start * 100
        if dd > max_daily_dd: max_daily_dd = dd

    # ── Закрыть незавершённые по последней цене ───────────────────────────────
    last_close = float(m15[-1, i_c])
    last_year  = int(df.index[-1].year)
    for sn, t in active_long.items():
        t['pnl'] = (last_close - t['entry']) * t['size']
        balance += t['pnl']; t['year'] = last_year; trades_l.append(t)
    for sn, t in active_short.items():
        t['pnl'] = (t['entry'] - last_close) * t['size']
        balance += t['pnl']; t['year'] = last_year; trades_s.append(t)

    # ── Статистика ────────────────────────────────────────────────────────────
    def stats(trades):
        if not trades:
            return {'n': 0, 'wr': 0.0, 'pnl': 0.0, 'yearly': pd.Series(dtype=float)}
        tdf = pd.DataFrame(trades)
        n   = len(tdf)
        wr  = (tdf['pnl'] > 0).sum() / n
        pnl = tdf['pnl'].sum()
        yr  = tdf.groupby('year')['pnl'].sum()
        return {'n': n, 'wr': wr, 'pnl': pnl, 'yearly': yr}

    sl = stats(trades_l)
    ss = stats(trades_s)

    total_pnl = balance - 10_000
    all_years = sorted(set(list(sl['yearly'].index) + list(ss['yearly'].index)))
    combined_yearly = pd.Series({
        yr: sl['yearly'].get(yr, 0) + ss['yearly'].get(yr, 0)
        for yr in all_years
    })
    all_pos = bool(all(combined_yearly > 0)) if len(combined_yearly) > 0 else False

    return {
        'pnl':       total_pnl,
        'pnl_l':     sl['pnl'],
        'pnl_s':     ss['pnl'],
        'n_l':       sl['n'],
        'n_s':       ss['n'],
        'wr_l':      sl['wr'],
        'wr_s':      ss['wr'],
        'max_dd':    max_dd,
        'max_dly_dd': max_daily_dd,
        'all_pos':   all_pos,
        'yearly':    combined_yearly,
    }


def passes(r):
    return r['max_dd'] < 10.0 and r['max_dly_dd'] < 5.0 and r['all_pos']


def main():
    data_path = (
        Path(__file__).parent.parent
        / "data_cache" / "dukascopy" / "m15" / "XAUUSD"
        / "xauusd_m15_2020-01-01_2026-04-18.parquet"
    )

    print("=" * 80)
    print("LONG+SHORT Grid Search  (LONG fixed, SHORT pereborated)")
    print(f"LONG: asian(3-6) london(8-11) ny(15-18)  TP={TP_RR_L}R  Risk=${RISK_L:.0f}")
    print(f"SHORT risk: ${SHORT_RISK:.0f}  TP grid: {SHORT_TP_VALUES}  "
          f"ATRbuf grid: {SHORT_ATR_BUFFERS}")
    print("=" * 80)

    df = pd.read_parquet(data_path).sort_index()
    df['atr'] = strat._atr(df, ATR_PERIOD)
    df_h4 = df.resample('4h').agg(
        {'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last'}
    ).dropna()
    df_h4['ema20'] = strat._ema(df_h4, H4_EMA_PERIOD)
    h4_times = df_h4.index.asi8
    h4_ema20 = df_h4['ema20'].to_numpy()

    print(f"Data: {df.index[0].date()} - {df.index[-1].date()}  ({len(df):,} bars)\n")

    # ── Baseline: LONG only ───────────────────────────────────────────────────
    print("[Baseline: LONG only]")
    bl = run_combined(df, h4_times, h4_ema20,
                      'dummy', (99, 100), 1, 0.5, False)
    print(f"  LONG only: N={bl['n_l']}  WR={bl['wr_l']:.1%}  "
          f"PnL=${bl['pnl_l']:,.0f}  MaxDD={bl['max_dd']:.2f}%  "
          f"DlyDD={bl['max_dly_dd']:.2f}%  AllYrs={'YES' if bl['all_pos'] else 'NO'}\n")

    all_results = {}

    for sess_name, range_cands in SESSION_RANGES.items():
        n_combos = len(range_cands) * len(SHORT_TP_VALUES) * len(SHORT_ATR_BUFFERS) * len(SHORT_H4_FILTERS)
        print("=" * 80)
        print(f"SHORT session: {sess_name}  ({n_combos} combos)")
        print("=" * 80)

        results = []
        t0 = time.time()
        done = 0

        for rng in range_cands:
            for tp_s in SHORT_TP_VALUES:
                for abuf in SHORT_ATR_BUFFERS:
                    for h4f in SHORT_H4_FILTERS:
                        r = run_combined(df, h4_times, h4_ema20,
                                         sess_name, rng, tp_s, abuf, h4f)
                        r.update({'rng': rng, 'tp_s': tp_s, 'abuf': abuf, 'h4f': h4f})
                        results.append(r)
                        done += 1
                        elapsed = time.time() - t0
                        eta = elapsed / done * (n_combos - done)
                        ok = '*' if passes(r) else ' '
                        flt = 'H4' if h4f else 'no'
                        print(f"  {ok} [{done:3d}/{n_combos}] "
                              f"rng={rng[0]}-{rng[1]} TP={tp_s}R buf={abuf} flt={flt}  "
                              f"N_s={r['n_s']:3d} WR_s={r['wr_s']:.0%}  "
                              f"PnL_s=${r['pnl_s']:>6,.0f}  "
                              f"PnL_tot=${r['pnl']:>7,.0f}  "
                              f"DD={r['max_dd']:.1f}% dDD={r['max_dly_dd']:.1f}%  "
                              f"AllY={'Y' if r['all_pos'] else 'N'}  ETA={eta:.0f}s",
                              flush=True)

        all_results[sess_name] = results

        # ── Топ для этой сессии ───────────────────────────────────────────────
        passed = [r for r in results if passes(r)]
        print(f"\n  --- TOP для {sess_name} (прошли DD<10%,dDD<5%,AllYrs) [{len(passed)} / {n_combos}] ---")
        if passed:
            print(f"  {'#':>3}  {'rng':>8}  {'TP':>4}  {'buf':>5}  {'flt':>4}  "
                  f"{'N_s':>5}  {'WR_s':>6}  {'PnL_s':>8}  {'PnL_tot':>9}  "
                  f"{'MaxDD':>7}  {'DlyDD':>6}")
            print("  " + "-" * 80)
            for k, r in enumerate(sorted(passed, key=lambda x: -x['pnl'])[:10], 1):
                flt = 'H4' if r['h4f'] else 'no'
                rng = r['rng']
                print(f"  #{k:2d}  {rng[0]}-{rng[1]:>4}  {r['tp_s']:2d}R  "
                      f"{r['abuf']:.1f}  {flt:>4}  "
                      f"{r['n_s']:5d}  {r['wr_s']:.1%}  "
                      f"${r['pnl_s']:>7,.0f}  ${r['pnl']:>8,.0f}  "
                      f"{r['max_dd']:.2f}%  {r['max_dly_dd']:.2f}%")
        else:
            print("  (нет комбинаций, прошедших все фильтры)")
            print("  Лучшие по PnL_total:")
            for k, r in enumerate(sorted(results, key=lambda x: -x['pnl'])[:5], 1):
                flt = 'H4' if r['h4f'] else 'no'
                rng = r['rng']
                print(f"  #{k:2d}  {rng[0]}-{rng[1]}  TP={r['tp_s']}R  "
                      f"buf={r['abuf']}  flt={flt}  "
                      f"N_s={r['n_s']}  WR_s={r['wr_s']:.0%}  "
                      f"PnL_s=${r['pnl_s']:,.0f}  PnL_tot=${r['pnl']:,.0f}  "
                      f"DD={r['max_dd']:.1f}%  dDD={r['max_dly_dd']:.1f}%")
        print()

    # ── Итоговое сравнение лучших по каждой сессии ────────────────────────────
    print("=" * 80)
    print("SUMMARY: лучший SHORT по каждой сессии (из прошедших фильтр)")
    print("=" * 80)
    print(f"  Baseline LONG only: PnL=${bl['pnl_l']:,.0f}  MaxDD={bl['max_dd']:.2f}%\n")

    all_years = None
    best_per_sess = {}
    for sess_name, results in all_results.items():
        passed = [r for r in results if passes(r)]
        src    = passed if passed else results
        best   = sorted(src, key=lambda x: -x['pnl'])[0]
        best_per_sess[sess_name] = best
        flt = 'H4' if best['h4f'] else 'no'
        rng = best['rng']
        mark = '***' if best in passed else '(no filter)'
        print(f"  {sess_name:<10} rng={rng[0]}-{rng[1]}  TP={best['tp_s']}R  "
              f"buf={best['abuf']}  flt={flt}  "
              f"N_s={best['n_s']}  WR_s={best['wr_s']:.0%}  "
              f"PnL_s=${best['pnl_s']:,.0f}  PnL_tot=${best['pnl']:,.0f}  "
              f"MaxDD={best['max_dd']:.2f}%  DlyDD={best['max_dly_dd']:.2f}%  {mark}")
        if all_years is None:
            all_years = sorted(best['yearly'].index)

    if all_years:
        print(f"\n  PnL по годам (лучшие):")
        yr_hdr = "  ".join(str(y) for y in all_years)
        print(f"  {'Label':<20}  {yr_hdr}")
        print("  " + "-" * (22 + 9 * len(all_years)))
        print(f"  {'LONG only':<20}  " + "  ".join(
            f"${bl['yearly'].get(y, 0):>6,.0f}" for y in all_years))
        for sn, best in best_per_sess.items():
            yr_vals = "  ".join(f"${best['yearly'].get(y, 0):>6,.0f}" for y in all_years)
            print(f"  {sn:<20}  {yr_vals}")


if __name__ == "__main__":
    main()
