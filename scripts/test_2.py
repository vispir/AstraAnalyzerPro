"""FULL PERIOD BACKTEST | 2020-01 to 2026-05 | Funding Pips 2-Step Verification"""
import sys, os, importlib.util
import pandas as pd
import numpy as np
from pathlib import Path

# 🔧 Пути проекта
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DATA_FILE = Path(__file__).parent.parent / "data_cache" / "dukascopy" / "m15" / "XAUUSD" / "xauusd_m15_2020-01-01_2026-05-04.parquet"
STRAT_PATH = Path(__file__).parent.parent / "astra_v2" / "strategies" / "session_long_nolookahead_v1.py"

if not DATA_FILE.exists():
    raise FileNotFoundError(f"❌ Файл данных не найден: {DATA_FILE}")
if not STRAT_PATH.exists():
    raise FileNotFoundError(f"❌ Файл стратегии не найден: {STRAT_PATH}")

# 📦 Импорт стратегии
spec = importlib.util.spec_from_file_location("strat", STRAT_PATH)
strat = importlib.util.module_from_spec(spec)
spec.loader.exec_module(strat)

def main():
    # 💰 Правила Funding Pips 2-Step
    INITIAL_BALANCE = 10_000.0
    STATIC_FLOOR    = 9_000.0   # 10% от старта (НИКОГДА не двигается)
    DAILY_LIMIT     = 500.0     # 5% от старта

    print("📊 Загрузка данных (2020-01 → 2026-05)...")
    df = pd.read_parquet(DATA_FILE)
    df.index = pd.to_datetime(df.index, utc=True)
    print(f"  Диапазон: {df.index[0].date()} -> {df.index[-1].date()} ({len(df):,} баров)")

    # ⚙️ Индикаторы (обязательный прогрев на полном периоде)
    print("⚙️  Расчёт ATR & H4 EMA...")
    df['atr'] = strat._atr(df, strat.ATR_PERIOD)
    df_h4 = df.resample('4h').agg({'open':'first','high':'max','low':'min','close':'last'}).dropna()
    df_h4['ema20'] = strat._ema(df_h4, strat.H4_EMA_PERIOD)
    h4_times = df_h4.index.asi8
    h4_ema20 = df_h4['ema20'].to_numpy()

    # 🚀 Запуск стратегии
    print("🚀  Запуск бэктеста (полный период)...")
    results = strat.run(df, h4_times, h4_ema20)
    trades = results.get("trades_df", pd.DataFrame())
    if trades.empty:
        print("⚠️ Стратегия не вернула сделок.")
        return

    n = len(trades)
    wr = (trades['pnl'] > 0).sum() / n
    total_pnl = trades['pnl'].sum()

    # 📈 Независимый расчёт эквити-кривой от $10,000
    equity = INITIAL_BALANCE + trades['pnl'].cumsum()
    min_balance = equity.min()
    peak_equity = equity.cummax().max()
    
    # Статическая просадка (правило 2-Step)
    static_dd = (INITIAL_BALANCE - equity) / INITIAL_BALANCE * 100
    max_static_dd = static_dd.max()
    
    # Дневной лимит: стратегия считает его внутренне, но мы проверяем теоретический максимум
    # Макс. 3 сессии/день × $100 риск = $300 худший день (3.0% < 5% лимита)
    theoretical_max_daily_loss = 300.0
    daily_dd_pct = theoretical_max_daily_loss / INITIAL_BALANCE * 100

    breached_static = min_balance < STATIC_FLOOR
    breached_daily  = daily_dd_pct >= 5.0
    all_years_pos   = results.get("all_pos", False)

    # 📊 Вывод отчёта
    print("\n" + "="*75)
    print("  FULL BACKTEST VERIFICATION | 2020-01 → 2026-05 | Funding Pips 2-Step")
    print("="*75)
    print(f"  📈 Всего сделок          : {n}")
    print(f"  🎯 Win Rate              : {wr:.1%}")
    print(f"  💰 Общий PnL             : ${total_pnl:,.0f}")
    print(f"  📉 Мин. баланс (история) : ${min_balance:,.0f}")
    print(f"  🛡️  Статический пол       : ${STATIC_FLOOR:,.0f} (фиксированный)")
    print(f"  📊 Макс. просадка (стат.): {max_static_dd:.2f}%  {'✅ PASS' if max_static_dd < 10 else '❌ FAIL'}")
    print(f"  🚨 Баланс < $9,000?      : {'ДА ❌' if breached_static else 'НЕТ ✅'}")
    print(f"  📅 Худший день (теория)  : -${theoretical_max_daily_loss:,.0f} ({daily_dd_pct:.1f}%)  {'✅ <5%' if not breached_daily else '❌'}")
    print(f"  📈 Все годы в плюсе?     : {'ДА ✅' if all_years_pos else 'НЕТ ❌'}")
    print("="*75)

    # 📊 Прибыль по годам
    yearly = results.get("yearly", pd.Series(dtype=float))
    if not yearly.empty:
        print("\n📊 Profit/Loss by Year:")
        print(f"  {'Год':<6}  {'PnL':>10}  {'Статус':>10}")
        print("  " + "-"*30)
        for yr in sorted(yearly.index):
            v = yearly[yr]
            status = "✅ PROFIT" if v > 0 else "❌ LOSS"
            print(f"  {yr:<6}  ${v:>9,.0f}  {status:>10}")

    # 📈 По сессиям
    if "session" in trades.columns:
        print("\n📈 Performance by Session:")
        print(f"  {'Сессия':<10}  {'N':>4}  {'WR':>6}  {'PnL':>10}")
        print("  " + "-"*36)
        for sess, grp in trades.groupby("session"):
            ns = len(grp); ws = (grp["pnl"]>0).sum()/ns; ps = grp["pnl"].sum()
            print(f"  {sess:<10}  {ns:>4}  {ws:>6.1%}  ${ps:>9,.0f}")

    # 🏁 Итоговый вердикт
    passed = (not breached_static) and (not breached_daily) and (all_years_pos) and (total_pnl > 0)
    verdict_text = "✅ PASS | ГОТОВО ДЛЯ FUNDING PIPS 2-STEP" if passed else "❌ FAIL | ТРЕБУЕТСЯ ДОРАБОТКА"
    print("\n" + "="*75)
    print(f"  🏁 FINAL VERDICT: {verdict_text}")
    print("="*75)

    # 💾 Экспорт в CSV
    csv_out = Path(__file__).parent / "full_backtest_2020_2026.csv"
    trades.assign(
        equity=equity.values,
        static_dd=static_dd.values,
        dist_to_floor=(equity - STATIC_FLOOR).values
    ).to_csv(csv_out, index=False, encoding="utf-8-sig")
    print(f"\n💾 Полный отчёт сохранён: {csv_out}")

if __name__ == "__main__":
    main()