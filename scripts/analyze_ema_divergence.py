"""
Анализ расхождений EMA-фильтра: оригинал vs ea_mirror.

Для каждого M15 бара где ВОЗМОЖЕН LONG вход (сессия активна, цена пробила
диапазон сессии) считаем:
  - orig_up  = h4_close[ptr_orig]   > h4_ema20[ptr_orig]
  - mirror_up = forming_close        > forming_ema

Расхождение = когда orig_up != mirror_up.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from session_breakout_trader import (
    ATR_PERIOD, H4_EMA_PERIOD, LONG_SESSIONS, calculate_atr, calculate_ema,
)
import pandas as pd
import numpy as np
from pathlib import Path

K_EMA = 2.0 / (H4_EMA_PERIOD + 1.0)


def floor4h_ns(ts_ns: int) -> int:
    return int(ts_ns // int(14400 * 1e9)) * int(14400 * 1e9)


def run():
    print("=" * 70)
    print("АНАЛИЗ РАСХОЖДЕНИЙ EMA: ОРИГИНАЛ vs EA_MIRROR")
    print("=" * 70)

    data_path = (
        Path(__file__).parent.parent
        / "data_cache" / "dukascopy" / "m15" / "XAUUSD"
        / "xauusd_m15_2020-01-01_2026-04-18.parquet"
    )
    df = pd.read_parquet(data_path).sort_index()
    df['atr'] = calculate_atr(df, ATR_PERIOD)

    df_h4 = df.resample('4h').agg(
        {'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last'}
    ).dropna()
    df_h4['ema20'] = calculate_ema(df_h4, H4_EMA_PERIOD)

    h4_times = df_h4.index.asi8
    h4_close = df_h4['close'].to_numpy()
    h4_ema20 = df_h4['ema20'].to_numpy()
    n_h4     = len(h4_times)
    H4_NS    = int(4 * 3600 * 1e9)
    MIN_H4   = 22

    times_ns  = df.index.asi8
    hours_arr = np.array([t.hour for t in df.index])
    closes    = df['close'].to_numpy()
    highs     = df['high'].to_numpy()
    lows      = df['low'].to_numpy()

    ptr_orig   = -1
    ptr_closed = -1

    forming_period = -1
    forming_close  = np.nan
    ema_base       = np.nan

    # Отслеживание максимумов/минимумов сессий
    session_highs = {}
    session_lows  = {}
    prev_date = None

    # Счётчики
    total_potential_signals = 0   # баров где цена пробила диапазон сессии
    both_up    = 0   # оба согласны: UP
    both_down  = 0   # оба согласны: DOWN
    orig_up_mirror_down = 0   # оригинал UP, mirror DOWN (look-ahead помог)
    orig_down_mirror_up = 0   # оригинал DOWN, mirror UP (look-ahead помешал)

    divergence_examples = []   # сохранить несколько примеров для вывода

    for i in range(len(df)):
        ts_ns = int(times_ns[i])
        cur_ts = df.index[i]
        hour   = int(hours_arr[i])
        close  = float(closes[i])
        high   = float(highs[i])
        low    = float(lows[i])

        cur_date = cur_ts.date()
        if cur_date != prev_date:
            prev_date     = cur_date
            session_highs = {}
            session_lows  = {}

        # Продвинуть указатели
        while ptr_orig + 1 < n_h4 and h4_times[ptr_orig + 1] <= ts_ns:
            ptr_orig += 1
        while ptr_closed + 1 < n_h4 and h4_times[ptr_closed + 1] + H4_NS <= ts_ns:
            ptr_closed += 1

        if ptr_orig < MIN_H4 - 1 or ptr_closed < MIN_H4 - 1:
            # Обновить диапазоны сессий даже в начале
            for sn, p in LONG_SESSIONS.items():
                sh, eh = p['range_hours']
                if sh <= hour < eh:
                    session_highs[sn] = max(session_highs.get(sn, 0), high)
                    session_lows[sn]  = min(session_lows.get(sn, 1e9), low)
            continue

        # Обновить forming H4 bar
        h4_period = floor4h_ns(ts_ns)
        if h4_period != forming_period:
            forming_period = h4_period
            forming_close  = close
            ema_base = h4_ema20[ptr_closed] if ptr_closed >= 0 else np.nan
        else:
            forming_close = close

        # Обновить диапазоны сессий
        for sn, p in LONG_SESSIONS.items():
            sh, eh = p['range_hours']
            if sh <= hour < eh:
                session_highs[sn] = max(session_highs.get(sn, 0), high)
                session_lows[sn]  = min(session_lows.get(sn, 1e9), low)

        # Проверить потенциальные LONG входы для каждой сессии
        for sn, p in LONG_SESSIONS.items():
            if sn not in session_highs:
                continue
            if not (p['entry_start'] <= hour < p['entry_end']):
                continue
            if close <= session_highs[sn]:
                continue   # цена не пробила диапазон

            # Потенциальный сигнал! Проверить EMA фильтр
            total_potential_signals += 1

            # ОРИГИНАЛ: h4_close[ptr_orig] > h4_ema20[ptr_orig]
            orig_up = h4_close[ptr_orig] > h4_ema20[ptr_orig]

            # EA_MIRROR: forming_close > ema_mirror
            if np.isnan(ema_base):
                continue
            ema_mirror = forming_close * K_EMA + ema_base * (1.0 - K_EMA)
            mirror_up  = forming_close > ema_mirror

            if orig_up and mirror_up:
                both_up += 1
            elif not orig_up and not mirror_up:
                both_down += 1
            elif orig_up and not mirror_up:
                orig_up_mirror_down += 1
                if len(divergence_examples) < 5:
                    divergence_examples.append({
                        'ts': cur_ts, 'session': sn, 'type': 'orig_UP_mirror_DOWN',
                        'h4_orig_close': h4_close[ptr_orig],
                        'h4_orig_ema':   h4_ema20[ptr_orig],
                        'mirror_close':  forming_close,
                        'mirror_ema':    ema_mirror,
                    })
            else:  # not orig_up and mirror_up
                orig_down_mirror_up += 1
                if len([e for e in divergence_examples if e['type'] == 'orig_DOWN_mirror_UP']) < 3:
                    divergence_examples.append({
                        'ts': cur_ts, 'session': sn, 'type': 'orig_DOWN_mirror_UP',
                        'h4_orig_close': h4_close[ptr_orig],
                        'h4_orig_ema':   h4_ema20[ptr_orig],
                        'mirror_close':  forming_close,
                        'mirror_ema':    ema_mirror,
                    })

    # ── Вывод ────────────────────────────────────────────────────────────────
    total_diverge = orig_up_mirror_down + orig_down_mirror_up
    pct_diverge   = total_diverge / total_potential_signals * 100 if total_potential_signals > 0 else 0
    pct_orig_up_mirror_down = orig_up_mirror_down / total_potential_signals * 100 if total_potential_signals > 0 else 0
    pct_orig_down_mirror_up = orig_down_mirror_up / total_potential_signals * 100 if total_potential_signals > 0 else 0

    print(f"\nВсего потенциальных сигналов: {total_potential_signals}")
    print(f"  Оба UP   (оба пропускают):  {both_up:>5}  ({both_up/total_potential_signals*100:.1f}%)")
    print(f"  Оба DOWN (оба блокируют):   {both_down:>5}  ({both_down/total_potential_signals*100:.1f}%)")
    print()
    print(f"  РАСХОЖДЕНИЯ: {total_diverge} ({pct_diverge:.1f}% от всех сигналов)")
    print(f"    orig=UP  mirror=DOWN (look-ahead ДАЁТ сделку, mirror БЛОКИРУЕТ): {orig_up_mirror_down:>4}  ({pct_orig_up_mirror_down:.1f}%)")
    print(f"    orig=DOWN mirror=UP  (look-ahead БЛОКИРУЕТ, mirror ДАЁТ сделку): {orig_down_mirror_up:>4}  ({pct_orig_down_mirror_up:.1f}%)")
    print()

    # По сессиям — перезапустим чтобы разбить по сессиям
    print("=" * 70)
    if pct_diverge < 5:
        print("ВЫВОД: расхождений МАЛО (<5%) — разница $31k объясняется другим.")
    elif pct_diverge < 15:
        print("ВЫВОД: расхождений УМЕРЕННО — look-ahead частично объясняет разницу.")
    else:
        print("ВЫВОД: расхождений МНОГО (>15%) — look-ahead критичен.")

    print()
    print("Примеры расхождений (orig=UP mirror=DOWN):")
    for ex in [e for e in divergence_examples if e['type'] == 'orig_UP_mirror_DOWN'][:5]:
        print(f"  {ex['ts']}  sn={ex['session']}")
        print(f"    orig:   close={ex['h4_orig_close']:.4f}  ema={ex['h4_orig_ema']:.4f}  UP (+{ex['h4_orig_close']-ex['h4_orig_ema']:.4f})")
        print(f"    mirror: close={ex['mirror_close']:.4f}  ema={ex['mirror_ema']:.4f}  DOWN ({ex['mirror_close']-ex['mirror_ema']:.4f})")

    print()

    # ── Дополнительно: посчитать по часам дня ────────────────────────────────
    print("=" * 70)
    print("Анализ: на каком часу H4 периода чаще всего расхождение?")
    print("(час внутри H4 бара: 0=первый час, 1=второй, ...)")

    # Второй проход — быстрый, только расхождения
    ptr_orig2   = -1
    ptr_closed2 = -1
    forming_period2 = -1
    forming_close2  = np.nan
    ema_base2       = np.nan
    session_highs2  = {}
    session_lows2   = {}
    prev_date2 = None

    hour_in_h4_diverge = {}  # {0,1,2,3} -> count

    for i in range(len(df)):
        ts_ns = int(times_ns[i])
        cur_ts = df.index[i]
        hour   = int(hours_arr[i])
        close  = float(closes[i])
        high   = float(highs[i])
        low    = float(lows[i])

        cur_date = cur_ts.date()
        if cur_date != prev_date2:
            prev_date2     = cur_date
            session_highs2 = {}
            session_lows2  = {}

        while ptr_orig2 + 1 < n_h4 and h4_times[ptr_orig2 + 1] <= ts_ns:
            ptr_orig2 += 1
        while ptr_closed2 + 1 < n_h4 and h4_times[ptr_closed2 + 1] + H4_NS <= ts_ns:
            ptr_closed2 += 1

        if ptr_orig2 < MIN_H4 - 1 or ptr_closed2 < MIN_H4 - 1:
            for sn, p in LONG_SESSIONS.items():
                sh, eh = p['range_hours']
                if sh <= hour < eh:
                    session_highs2[sn] = max(session_highs2.get(sn, 0), high)
                    session_lows2[sn]  = min(session_lows2.get(sn, 1e9), low)
            continue

        h4_period2 = floor4h_ns(ts_ns)
        if h4_period2 != forming_period2:
            forming_period2 = h4_period2
            forming_close2  = close
            ema_base2 = h4_ema20[ptr_closed2] if ptr_closed2 >= 0 else np.nan
        else:
            forming_close2 = close

        # час внутри H4 периода (0=первый час, 1=второй, ...)
        h4_start_ns = floor4h_ns(ts_ns)
        elapsed_ns  = ts_ns - h4_start_ns
        hour_in_h4  = int(elapsed_ns // int(3600 * 1e9))

        for sn, p in LONG_SESSIONS.items():
            sh, eh = p['range_hours']
            if sh <= hour < eh:
                session_highs2[sn] = max(session_highs2.get(sn, 0), high)
                session_lows2[sn]  = min(session_lows2.get(sn, 1e9), low)

        for sn, p in LONG_SESSIONS.items():
            if sn not in session_highs2:
                continue
            if not (p['entry_start'] <= hour < p['entry_end']):
                continue
            if close <= session_highs2[sn]:
                continue

            if np.isnan(ema_base2):
                continue

            orig_up2   = h4_close[ptr_orig2] > h4_ema20[ptr_orig2]
            ema_m2     = forming_close2 * K_EMA + ema_base2 * (1.0 - K_EMA)
            mirror_up2 = forming_close2 > ema_m2

            if orig_up2 != mirror_up2:
                hour_in_h4_diverge[hour_in_h4] = hour_in_h4_diverge.get(hour_in_h4, 0) + 1

    for h_idx in sorted(hour_in_h4_diverge):
        print(f"  Час {h_idx} H4 периода: {hour_in_h4_diverge[h_idx]} расхождений")

    print("=" * 70)


if __name__ == "__main__":
    run()
