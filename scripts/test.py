"""LONG-only backtest for 2026 Jan-May | Current: $9,950 | Rules: $10k start"""
import sys, os, importlib.util
import pandas as pd
import numpy as np
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

DATA_FILE = Path(__file__).parent.parent / "data_cache" / "dukascopy" / "m15" / "XAUUSD" / "xauusd_m15_2020-01-01_2026-05-04.parquet"
STRAT_PATH = Path(__file__).parent.parent / "astra_v2" / "strategies" / "session_long_nolookahead_v1.py"

if not DATA_FILE.exists():
    raise FileNotFoundError(f"❌ Файл данных не найден: {DATA_FILE}")
if not STRAT_PATH.exists():
    raise FileNotFoundError(f"❌ Файл стратегии не найден: {STRAT_PATH}")

spec = importlib.util.spec_from_file_location("strat", STRAT_PATH)
strat = importlib.util.module_from_spec(spec)
spec.loader.exec_module(strat)

def main():
    # 💰 Правила Funding Pips 2-Step (фиксированы от стартовых $10,000)
    CURRENT_BALANCE  = 9_950.0   # ваш текущий баланс
    RULES_BASE       = 10_000.0  # начальный баланс аккаунта
    STATIC_FLOOR     = RULES_BASE * 0.90  # $9,000 (ФИКСИРОВАННЫЙ, не зависит от текущего баланса)
    DAILY_LIMIT      = RULES_BASE * 0.05  # $500

    print("📊 Загрузка данных...")
    df = pd.read_parquet(DATA_FILE)
    df.index = pd.to_datetime(df.index, utc=True)
    print(f"  Диапазон данных: {df.index[0].date()} -> {df.index[-1].date()} ({len(df):,} баров)")

    # ⚙️ Индикаторы (прогрев на 2020-2026 обязателен для корректного H4 EMA)
    print("⚙️  Расчёт ATR и H4 EMA...")
    df['atr'] = strat._atr(df, strat.ATR_PERIOD)
    df_h4 = df.resample('4h').agg({'open':'first','high':'max','low':'min','close':'last'}).dropna()
    df_h4['ema20'] = strat._ema(df_h4, strat.H4_EMA_PERIOD)
    h4_times = df_h4.index.asi8
    h4_ema20 = df_h4['ema20'].to_numpy()

    # 🚀 Запуск стратегии на полных данных (для точных сигналов)
    print("\n⚙️  Запуск LONG стратегии...")
    results = strat.run(df, h4_times, h4_ema20)
    trades = results.get("trades_df", pd.DataFrame())
    if trades.empty:
        print("⚠️ Стратегия не вернула сделок.")
        return

    # 📅 Фильтрация ТОЛЬКО за 2026 год (Янв-Май)
    trades_2026 = trades[trades["year"] == 2026].copy()
    n = len(trades_2026)
    if n == 0:
        print("⚠️ В 2026 году сделок не найдено.")
        return
    print(f"✅ Найдено сделок за 2026 (Янв-Май): {n}")

    # 📈 Эквити-кривая от текущего баланса $9,950
    equity = CURRENT_BALANCE + trades_2026['pnl'].cumsum()
    peak   = equity.cummax()
    
    # Просадка считается от ПРАВИЛЬНОГО СТАРТА ($10,000), как в правилах проп-фирмы
    static_dd = (RULES_BASE - equity) / RULES_BASE * 100
    max_static_dd = static_dd.max()
    
    # Отслеживание $11,000
    crossed_11k = (peak >= 11_000).any()
    first_11k_idx = np.argmax(peak >= 11_000) if crossed_11k else -1
    peak_val = peak.max()
    final_balance = equity.iloc[-1]
    breached = (equity < STATIC_FLOOR).any()

    # Дневной лимит (приближённо по скользящему окну, т.к. стратегия хранит только год)
    daily_approx = trades_2026['pnl'].rolling(7).sum().min()
    daily_dd_pct = abs(daily_approx) / RULES_BASE * 100 if daily_approx < 0 else 0

    # 📊 Вывод сводки
    print("\n" + "="*68)
    print(f"  LONG-ONLY BACKTEST | 2026 (Янв-Май) | Старт: ${CURRENT_BALANCE:,.0f}")
    print("="*68)
    print(f"  📉 Статический пол (10% от $10k)     : ${STATIC_FLOOR:,.0f} (ФИКСИРОВАННЫЙ)")
    print(f"  📈 Пик баланса                         : ${peak_val:,.0f}")
    print(f"  💰 Финальный баланс                    : ${final_balance:,.0f}")
    print(f"  📊 Макс. просадка от $10,000           : {max_static_dd:.2f}%  {'✅ PASS' if max_static_dd < 10 else '❌ FAIL'}")
    print(f"  🎯 Баланс перешагнул $11,000?          : {'ДА ✅' if crossed_11k else 'НЕТ'}")
    if crossed_11k:
        print(f"     → Произошло на сделке №{first_11k_idx + 1}")
    print(f"  🛡️  Буфер до брича (финал)             : ${final_balance - STATIC_FLOOR:,.0f}")
    print(f"  ⚠️  Прибл. худший день (7 сделок)      : ${daily_approx:,.0f} ({daily_dd_pct:.2f}%)  {'✅' if daily_dd_pct < 5 else '⚠️ Проверь даты'}")
    print(f"  🚨 Аккаунт нарушил лимит?              : {'ДА ❌' if breached else 'НЕТ ✅'}")
    print("="*68)

    # 📈 Статистика по сессиям
    if "session" in trades_2026.columns:
        print("\n📈 Статистика по сессиям:")
        print(f"  {'Сессия':<10}  {'N':>4}  {'WR':>6}  {'PnL':>10}")
        print("  " + "-"*36)
        for sess, grp in trades_2026.groupby("session"):
            ns = len(grp); ws = (grp["pnl"]>0).sum()/ns; ps = grp["pnl"].sum()
            print(f"  {sess:<10}  {ns:>4}  {ws:>6.1%}  ${ps:>9,.0f}")

    # 📝 Полный список сделок
    print(f"\n📝 Полный список сделок (старт ${CURRENT_BALANCE:,.0f}, пол ${STATIC_FLOOR:,.0f}):")
    print(f"  {'#':<4}  {'Сессия':<10}  {'PnL':>8}  {'Баланс':>9}  {'До пола':>8}  {'Итог'}")
    print("  " + "-"*54)
    
    for i, (_, t) in enumerate(trades_2026.iterrows(), 1):
        sess = t.get("session", "?")
        pnl  = t["pnl"]
        bal  = equity.iloc[i-1]
        dist = bal - STATIC_FLOOR
        res  = "WIN " if pnl > 0 else "LOSS"
        if dist < 0: res += " 🚨 BREACH"
        print(f"  {i:<4}  {sess:<10}  ${pnl:>7,.0f}  ${bal:>8,.0f}  ${dist:>7,.0f}  {res}")

    # 💾 Экспорт в CSV для Excel
    csv_out = Path(__file__).parent / "long_trades_2026_bal9950.csv"
    trades_2026.assign(
        balance=equity.values,
        peak=peak.values,
        dist_to_floor=(equity - STATIC_FLOOR).values
    ).to_csv(csv_out, index=False, encoding="utf-8-sig")
    print(f"\n💾 Детальный отчёт сохранён: {csv_out}")
    print("="*68)
    print("✅ Бэктест завершён.")

if __name__ == "__main__":
    main()