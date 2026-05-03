"""
Session Breakout LONG — без look-ahead (v1)

Параметры (лучшие из grid_search_windows_joint.py, 512 комбинаций):
  Risk        = $100
  TP          = 12R
  ATR buffer  = 0.5
  EMA slope   = 5 (h4_ema > h4_ema20[ptr-5])
  Trailing    = step LOW-trigger: 11R->10R, 10R->9R, ..., 2R->1R
  Sessions    = asian  (03-06 UTC range, 06-24 UTC entry)
               london (08-11 UTC range, 11-24 UTC entry)
               ny     (15-18 UTC range, 18-24 UTC entry)
  Сессии НЕ пересекаются по времени формирования диапазона.

Результат бэктеста:
  N=801  WR=40.0%  PnL=$47,620  MaxDD=8.79%  DlyDD=2.17%  AllYrs=YES

Нет look-ahead:
  H4 EMA: ptr_closed — последний закрытый H4 бар (open + 4h <= current_m15_time).
           forming_close = текущий M15 close (не будущее).
           ema_base = h4_ema20[ptr_closed] — EMA от закрытого бара.
  Slope:   h4_ema > h4_ema20[ptr_closed - SLOPE_N] — наклон EMA за 5 баров.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import pandas as pd
import numpy as np
from pathlib import Path

# ── Параметры стратегии ───────────────────────────────────────────────────────
RISK        = 100.0   # риск на сделку, $
TP_RR       = 12.0    # тейк-профит в R
ATR_BUFFER  = 0.5     # буфер SL в ATR единицах
ATR_PERIOD  = 14
H4_EMA_PERIOD = 20
SLOPE_N     = 5       # slope: h4_ema > ema N закрытых H4 баров назад
MIN_H4_BARS = 22      # минимум закрытых H4 баров перед первым входом

SESSIONS = {
    'asian':  {'range_hours': (3,  6),  'entry_start':  6, 'entry_end': 24},
    'london': {'range_hours': (8,  11), 'entry_start': 11, 'entry_end': 24},
    'ny':     {'range_hours': (15, 18), 'entry_start': 18, 'entry_end': 24},
}

# ── Константы ─────────────────────────────────────────────────────────────────
K_EMA  = 2.0 / (H4_EMA_PERIOD + 1.0)
H4_NS  = int(4 * 3600 * 1e9)


# ── Индикаторы ────────────────────────────────────────────────────────────────
def _atr(df: pd.DataFrame, period: int) -> pd.Series:
    h, l, c = df['high'], df['low'], df['close']
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()


def _ema(df: pd.DataFrame, period: int) -> pd.Series:
    return df['close'].ewm(span=period, min_periods=period, adjust=False).mean()


# ── Трейлинг ──────────────────────────────────────────────────────────────────
def _trail(t: dict, low: float) -> None:
    """Step trailing LONG: LOW-trigger, шаги 11R->10R, ..., 2R->1R."""
    risk = t['entry'] - t['initial_sl']
    rr   = (low - t['entry']) / risk
    for trigger, lock in ((11,10),(10,9),(9,8),(8,7),(7,6),(6,5),(5,4),(4,3),(3,2),(2,1)):
        if rr >= trigger:
            t['sl'] = max(t['sl'], t['entry'] + lock * risk)
            break


# ── Бэктест ───────────────────────────────────────────────────────────────────
def run(df: pd.DataFrame, h4_times: np.ndarray, h4_ema20: np.ndarray) -> dict:
    """
    Прогон стратегии. Возвращает словарь с результатами.

    Аргументы:
        df        — M15 DataFrame с колонками high/low/close/atr, индекс DatetimeTZNaive UTC
        h4_times  — массив int64 (nanoseconds) старта каждого H4 бара (df_h4.index.asi8)
        h4_ema20  — массив float64 EMA20 для каждого H4 бара
    """
    n_h4     = len(h4_times)
    times_ns = df.index.asi8
    m15      = df.to_numpy()
    col      = {c: i for i, c in enumerate(df.columns)}
    i_h = col['high']; i_l = col['low']; i_c = col['close']; i_a = col['atr']

    # указатели
    ptr_closed     = -1       # последний закрытый H4 бар
    forming_period = -1       # timestamp начала текущего forming H4
    forming_close  = np.nan
    ema_base       = np.nan   # EMA20 последнего закрытого H4

    trades       = []
    active_long  = {}         # sn -> trade dict
    balance      = 10_000.0
    peak         = 10_000.0
    max_dd       = 0.0
    max_daily_dd = 0.0
    prev_date    = None
    day_start    = balance
    s_highs      = {}
    s_lows       = {}

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

        # дневной DD
        cur_date = cur_ts.date()
        if cur_date != prev_date:
            if prev_date is not None and day_start > 0:
                dd = (day_start - balance) / day_start * 100
                if dd > max_daily_dd: max_daily_dd = dd
            day_start = balance
            prev_date = cur_date
            s_highs   = {}
            s_lows    = {}

        # H4 указатель — продвигаем пока следующий H4 уже закрыт
        while ptr_closed + 1 < n_h4 and h4_times[ptr_closed + 1] + H4_NS <= ts_ns:
            ptr_closed += 1

        # до накопления MIN_H4 закрытых баров — только собираем диапазоны
        if ptr_closed < MIN_H4_BARS - 1:
            for sn, p in SESSIONS.items():
                sh, eh = p['range_hours']
                if sh <= hour < eh:
                    s_highs[sn] = max(s_highs.get(sn, 0),   high)
                    s_lows[sn]  = min(s_lows.get(sn, 1e9),  low)
            continue

        # forming H4 bar — EMA на текущем (незакрытом) баре
        h4p = int(ts_ns // int(14400 * 1e9)) * int(14400 * 1e9)
        if h4p != forming_period:
            forming_period = h4p
            forming_close  = close
            ema_base = h4_ema20[ptr_closed] if ptr_closed >= 0 else np.nan
        else:
            forming_close = close

        if np.isnan(ema_base):
            continue

        # h4_ema = EMA если текущий бар закрылся прямо сейчас
        h4_ema = forming_close * K_EMA + ema_base * (1.0 - K_EMA)

        # фильтры (вычисляем как флаги — не прерываем до управления сделками)
        ema_ok   = forming_close > ema_base
        slope_ok = (ptr_closed >= SLOPE_N) and (not np.isnan(h4_ema20[ptr_closed - SLOPE_N])) \
                   and (h4_ema > h4_ema20[ptr_closed - SLOPE_N])

        # управление открытыми LONG сделками — ВСЕГДА, независимо от фильтров
        for sn in list(active_long.keys()):
            t = active_long[sn]
            _trail(t, low)
            if low <= t['sl']:
                t['pnl'] = (t['sl'] - t['entry']) * t['size']
                balance += t['pnl']; t['year'] = cur_ts.year
                trades.append(t); del active_long[sn]
            elif high >= t['tp']:
                t['pnl'] = (t['tp'] - t['entry']) * t['size']
                balance += t['pnl']; t['year'] = cur_ts.year
                trades.append(t); del active_long[sn]

        if balance > peak: peak = balance
        dd = (peak - balance) / peak * 100
        if dd > max_dd: max_dd = dd

        # диапазоны сессий — ВСЕГДА
        for sn, p in SESSIONS.items():
            sh, eh = p['range_hours']
            if sh <= hour < eh:
                s_highs[sn] = max(s_highs.get(sn, 0),   high)
                s_lows[sn]  = min(s_lows.get(sn, 1e9),  low)

        # новые входы — только если оба фильтра прошли
        if not (ema_ok and slope_ok):
            continue

        for sn, p in SESSIONS.items():
            if sn not in s_highs or sn in active_long:
                continue
            if not (p['entry_start'] <= hour < p['entry_end']):
                continue
            if close > s_highs[sn]:
                sl  = s_lows[sn] - ATR_BUFFER * atr
                rsk = close - sl
                if rsk <= 0: continue
                active_long[sn] = {
                    'entry': close, 'sl': sl, 'initial_sl': sl,
                    'tp': close + rsk * TP_RR,
                    'size': RISK / rsk,
                    'direction': 'LONG', 'session': sn,
                }

    # последний дневной DD
    if prev_date is not None and day_start > 0:
        dd = (day_start - balance) / day_start * 100
        if dd > max_daily_dd: max_daily_dd = dd

    # закрыть незавершённые по последней цене
    last_close = float(m15[-1, i_c])
    last_year  = int(df.index[-1].year)
    for sn, t in active_long.items():
        t['pnl'] = (last_close - t['entry']) * t['size']
        balance += t['pnl']; t['year'] = last_year; trades.append(t)

    tdf    = pd.DataFrame(trades) if trades else pd.DataFrame(columns=['pnl', 'year', 'session'])
    pnl    = balance - 10_000
    n      = len(tdf)
    wr     = (tdf['pnl'] > 0).sum() / n if n > 0 else 0.0
    yearly = tdf.groupby('year')['pnl'].sum() if n > 0 else pd.Series(dtype=float)

    return {
        'n': n, 'wr': wr, 'pnl': pnl,
        'max_dd': max_dd, 'max_daily_dd': max_daily_dd,
        'yearly': yearly,
        'all_pos': bool(all(yearly > 0)) if len(yearly) > 0 else False,
        'trades_df': tdf,
    }


# ── Точка входа (бэктест + отчёт) ────────────────────────────────────────────
def main():
    data_path = (
        Path(__file__).parent.parent.parent
        / "data_cache" / "dukascopy" / "m15" / "XAUUSD"
        / "xauusd_m15_2020-01-01_2026-04-18.parquet"
    )

    print("=" * 64)
    print("Session Breakout LONG — без look-ahead (v1)")
    print(f"Risk=${RISK:.0f}  TP={TP_RR}R  ATRbuf={ATR_BUFFER}  Slope={SLOPE_N}  EMA{H4_EMA_PERIOD}")
    print("=" * 64)

    df = pd.read_parquet(data_path).sort_index()
    df['atr'] = _atr(df, ATR_PERIOD)

    df_h4 = df.resample('4h').agg(
        {'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last'}
    ).dropna()
    df_h4['ema20'] = _ema(df_h4, H4_EMA_PERIOD)
    h4_times = df_h4.index.asi8
    h4_ema20 = df_h4['ema20'].to_numpy()

    print(f"Данные: {df.index[0].date()} — {df.index[-1].date()}  ({len(df):,} баров M15)")

    r = run(df, h4_times, h4_ema20)

    print(f"\nN        = {r['n']}")
    print(f"WR       = {r['wr']:.1%}")
    print(f"PnL      = ${r['pnl']:,.0f}")
    print(f"MaxDD    = {r['max_dd']:.2f}%")
    print(f"DlyDD    = {r['max_daily_dd']:.2f}%")
    print(f"AllYrs   = {'YES' if r['all_pos'] else 'NO'}")

    print("\nPnL по годам:")
    for yr in sorted(r['yearly'].index):
        v = r['yearly'][yr]
        mark = 'OK' if v > 0 else 'LOSS'
        print(f"  {yr}: ${v:>8,.0f}  [{mark}]")

    if r['n'] > 0:
        tdf = r['trades_df']
        by_sess = tdf.groupby('session').agg(
            n=('pnl', 'count'),
            wr=('pnl', lambda x: (x > 0).sum() / len(x)),
            pnl=('pnl', 'sum')
        )
        print("\nПо сессиям:")
        for sn, row in by_sess.iterrows():
            print(f"  {sn:<8} N={row['n']:4.0f}  WR={row['wr']:.1%}  PnL=${row['pnl']:>8,.0f}")

    print()
    print("Ожидаемые значения (из grid_search_windows_joint.py, 512 комбинаций):")
    print("  N=801  WR=40.0%  PnL=$47,620  MaxDD=8.79%  DlyDD=2.17%  AllYrs=YES")
    print("=" * 64)


if __name__ == "__main__":
    main()
