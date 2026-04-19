# 📋 ФИНАЛЬНАЯ КОНФИГУРАЦИЯ range_breakout_v1

## Параметры стратегии

### Основные параметры
- **MAX_RANGE_ATR**: 4.0 (в коде стратегии, строка 71)
- **STOP_BUFFER_ATR**: 0.5
- **TRAIL_DISTANCE_ATR**: 0.2 (глобальный параметр)
- **RISK_PCT**: 0.4% (0.004)
- **ATR_PERIOD**: 20
- **CONSOLIDATION_LOOKBACK**: 20 bars
- **TP_RR**: 2.0
- **PARTIAL_CLOSE_RR**: 1.0

### Сессии
- **ALLOWED_SESSIONS**: ("london", "new_york")
- **London**: 07:00-12:00 UTC
- **NY**: 13:00-17:00 UTC
- **Заблокированные часы**: 00:00-07:00, 12:00-13:00, 17:00-24:00 UTC

### Управление риском
- **MAX_TRADES_PER_DAY**: 3
- **FORCE_CLOSE_HOUR_UTC**: 22:00
- **TOKYO_RISK_MULTIPLIER**: 0.7 (не используется, Tokyo выключен)

### Фильтры консолидации (выключены)
- **USE_CONSOLIDATION_FILTER**: False
- **MIN_BOUNDARY_TOUCHES**: 2
- **MIN_BARS_INSIDE_PCT**: 0.70
- **MAX_CANDLE_BODY_ATR**: 1.5

---

## 📊 РЕЗУЛЬТАТЫ (XAUUSD 2020-2024)

### Бэктест
- **Trades**: 861
- **Win Rate**: 73.5%
- **Profit Factor**: 2.38
- **Max DD**: 2.22%
- **Avg RR**: 0.39
- **Net PnL**: +$32,006

### Monte Carlo (10,000 итераций)
- **Median Balance**: $42,052 (Median PnL: $32,052)
- **Median DD**: 3.68% ✅
- **5th percentile Balance**: $37,609 (worst case)
- **95th percentile DD**: 6.16% (worst case)
- **Prob DD > 5%**: 15.8% (1-2 раза за 10 лет)
- **Prob DD > 10%**: <0.1%
- **Profitable iterations**: 100%
- **Median PF**: 2.422

---

## ✅ ВАЛИДАЦИЯ FUNDING PIPS

```
┌───────────────────┬────────────┬───────────┬─────────┐
│     Критерий      │ Требование │ Результат │ Статус  │
├───────────────────┼────────────┼───────────┼─────────┤
│ Profit Factor     │ ≥ 1.5      │ 2.38      │ ✅ PASS │
├───────────────────┼────────────┼───────────┼─────────┤
│ Max DD (backtest) │ ≤ 5%       │ 2.22%     │ ✅ PASS │
├───────────────────┼────────────┼───────────┼─────────┤
│ Median DD (MC)    │ < 5%       │ 3.68%     │ ✅ PASS │
├───────────────────┼────────────┼───────────┼─────────┤
│ Prob DD > 5%      │ < 20%      │ 15.8%     │ ✅ PASS │
└───────────────────┴────────────┴───────────┴─────────┘
```

**Статус**: ✅ Готово к деплою на Funding Pips

---

## 📈 Сравнение с другими конфигурациями

### Протестированные варианты

| Конфигурация | Trades | Max DD | Net PnL | Median DD (MC) | Prob DD>5% |
|--------------|--------|--------|---------|----------------|------------|
| **London+NY (0.4%)** ✅ | 861 | 2.22% | $32,006 | 3.68% | 15.8% |
| Tokyo+London+NY (0.4%) | 861 | 2.10% | $29,738 | 3.51% | 12.4% |
| London+NY (0.5%) | 861 | 2.77% | $50,008 | 5.38% | 59.1% |
| London+NY (0.6%) | 861 | 3.32% | $75,651 | 7.72% | 90.0% |

### Почему London+NY с 0.4%?

**✅ Преимущества**:
1. Максимальный PnL среди безопасных конфигураций
2. Отличный баланс риск/доходность
3. Простая логика сессий (без перекрытий)
4. Лучшие размеры выигрышей (London avg win $10.05)
5. Низкая вероятность провала (15.8%)

**Tokyo альтернатива**:
- Лучше DD на 0.12%, но -$2,267 PnL (-7%)
- Tokyo WR 78.1% не компенсирует меньшие выигрыши ($8.37 vs $10.05)

**0.5%+ риск**:
- Неприемлемо высокая вероятность DD>5% (59-90%)
- Медиана DD превышает лимит prop firm

---

## 🔧 Файлы конфигурации

### astra_v2/config.py
```python
RANGE_BREAKOUT_V1_ALLOWED_SESSIONS = ("london", "new_york")
RANGE_BREAKOUT_V1_ATR_PERIOD = 20
RANGE_BREAKOUT_V1_CONSOLIDATION_LOOKBACK = 20
RANGE_BREAKOUT_V1_STOP_BUFFER_ATR = 0.5
RANGE_BREAKOUT_V1_TP_RR = 2.0
RANGE_BREAKOUT_V1_PARTIAL_CLOSE_RR = 1.0
RANGE_BREAKOUT_V1_MAX_TRADES_PER_DAY = 3
RANGE_BREAKOUT_V1_FORCE_CLOSE_HOUR_UTC = 22
RANGE_BREAKOUT_V1_TOKYO_RISK_MULTIPLIER = 0.7
RANGE_BREAKOUT_V1_USE_CONSOLIDATION_FILTER = False
RANGE_BREAKOUT_V1_MIN_BOUNDARY_TOUCHES = 2
RANGE_BREAKOUT_V1_MIN_BARS_INSIDE_PCT = 0.70
RANGE_BREAKOUT_V1_MAX_CANDLE_BODY_ATR = 1.5

RISK_PCT = 0.004  # 0.4% per trade
TRAIL_DISTANCE_ATR = 0.2
```

### astra_v2/strategies/range_breakout_v1.py
```python
def _get_session_label(self, now: datetime) -> str:
    hour = now.hour
    # Block 00:00-07:00, 12:00-13:00, and 17:00-22:00 UTC
    if (0 <= hour < 7) or (12 <= hour < 13) or (17 <= hour < 22):
        return "blocked"
    # London: 07:00-12:00 UTC
    elif 7 <= hour < 12:
        return "london"
    # New York: 13:00-17:00 UTC
    elif 13 <= hour < 17:
        return "new_york"
    else:
        return "other"
```

---

## 📝 Следующие шаги

1. ✅ Конфигурация финализирована
2. ✅ Все тесты пройдены
3. ⏭️ Готово к деплою на Funding Pips
4. ⏭️ Мониторинг live performance
5. ⏭️ Сравнение backtest vs live метрик

---

**Дата финализации**: 2026-04-19  
**Версия**: range_breakout_v1  
**Статус**: Production Ready ✅
