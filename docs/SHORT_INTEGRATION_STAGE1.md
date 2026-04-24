# Этап 1: Подготовка SHORT стратегии - ЗАВЕРШЁН ✓

## Что сделано:

### 1. Создана SHORT Reversal стратегия
**Файл**: `astra_v2/strategies/short_reversal_v1.py`

**Логика**:
- **Type 1**: Reversal After Historical High
  - Новый максимум за 5 H4 баров
  - 1 H4 свеча закрылась ниже
  - M15 пробой Low предыдущей свечи
  
- **Type 2**: Local Reversal After Strong Move
  - Рост 2+ ATR за 3 H4 бара
  - H4 закрылась ниже предыдущей
  - M15 пробой Low предыдущей свечи

**Фильтры**:
- H4 EMA20: SHORT только ниже EMA20
- Risk: $158, TP: 5.5R
- Step Trailing: 2R->1R, 3R->2R, 4R->3R, 5R->4R

### 2. Зарегистрирована стратегия
- Обновлён `astra_v2/strategies/registry.py`
- Добавлен `short_reversal_v1` в `_STRATEGIES`
- Обновлён `astra_v2/strategies/base.py` (StrategyId)

### 3. Добавлен флаг включения/выключения
**Файл**: `astra_v2/config.py`
```python
ENABLE_SHORT_STRATEGY = False  # По умолчанию выключен
```

**Переменная окружения**: `ENABLE_SHORT_STRATEGY=true` для включения

### 4. Создана миграция Supabase
**Файл**: `migrations/add_signal_type_column.sql`

Добавляет колонку `signal_type`:
- `session_breakout` - LONG стратегия
- `reversal_type1` - SHORT Type1
- `reversal_type2` - SHORT Type2

## Статус:
✓ SHORT код готов, но НЕ активен  
✓ LONG продолжает работать как раньше  
✓ Ничего не сломано  

## Следующий шаг:
**Этап 2**: Обновить EA на MT5 для обработки SHORT сигналов

---

## Как включить SHORT (когда будет готово):
1. Применить миграцию в Supabase
2. Установить `ENABLE_SHORT_STRATEGY=true` в .env
3. Задеплоить на Render
4. Обновить EA на VPS
