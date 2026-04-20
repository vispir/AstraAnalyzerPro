# MT5 Integration Setup Guide

## Компоненты интеграции Astra Strategy с MT5

### Архитектура
```
Python Strategy (combined_session_backtest.py)
    ↓ (генерирует сигнал)
mt5_signal_writer.py
    ↓ (записывает в Supabase)
Supabase Database (mt5_signals table)
    ↓ (читает каждые 15 сек)
MT5 EA (AstraSessionBreakout.mq5)
    ↓ (открывает сделку)
MT5 Terminal (XAUUSD)
```

---

## Шаг 1: Настройка Supabase

### 1.1 Создать проект в Supabase
1. Зайти на https://supabase.com
2. Создать новый проект
3. Сохранить:
   - Project URL: `https://your-project.supabase.co`
   - Anon/Public Key: `eyJhbGc...`

### 1.2 Создать таблицу
1. Открыть SQL Editor в Supabase
2. Выполнить скрипт из `supabase_schema.sql`
3. Проверить что таблица `mt5_signals` создана

### 1.3 Настроить API
1. Settings → API
2. Убедиться что REST API включен
3. URL должен быть: `https://your-project.supabase.co/rest/v1/`

---

## Шаг 2: Настройка Python модуля

### 2.1 Установить зависимости
```bash
pip install supabase
```

### 2.2 Настроить переменные окружения
Создать файл `.env` в корне проекта:
```
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_KEY=your-anon-key
```

Или установить в системе:
```bash
# Windows
set SUPABASE_URL=https://your-project.supabase.co
set SUPABASE_KEY=your-anon-key

# Linux/Mac
export SUPABASE_URL=https://your-project.supabase.co
export SUPABASE_KEY=your-anon-key
```

### 2.3 Тестировать подключение
```bash
python astra_v2/mt5/mt5_signal_writer.py
```

Должно вывести:
```
MT5 Signal Writer - Supabase Integration
============================================================
No active signals
Recent signals: 0
```

---

## Шаг 3: Интеграция со стратегией

### 3.1 Добавить импорт в combined_session_backtest.py
```python
from astra_v2.mt5.mt5_signal_writer import write_signal, get_active_signal
```

### 3.2 Добавить запись сигналов при генерации сделки
Найти место где создается сделка (после `if closes[i] > range_high:`):
```python
if closes[i] > range_high:
    entry = closes[i]
    sl = range_low - sess_params['stop_buffer'] * atr
    risk_amt = entry - sl
    tp = entry + risk_amt * sess_params['tp_rr']
    size = RISK_PER_TRADE / risk_amt
    
    # Write signal to Supabase for MT5
    write_signal(
        direction='LONG',
        entry=entry,
        sl=sl,
        tp=tp,
        session=sess_name,
        risk_usd=RISK_PER_TRADE
    )
    
    active_trades[sess_name] = {
        'entry': entry, 'sl': sl, 'initial_sl': sl, 'tp': tp,
        'size': size, 'direction': 'LONG', 'range_type': sess_name
    }
```

---

## Шаг 4: Настройка MT5 EA

### 4.1 Скопировать EA в MT5
1. Открыть MT5
2. File → Open Data Folder
3. Скопировать `AstraSessionBreakout.mq5` в `MQL5/Experts/`
4. Перезапустить MT5 или нажать Refresh в MetaEditor

### 4.2 Компилировать EA
1. Открыть MetaEditor (F4 в MT5)
2. Открыть `AstraSessionBreakout.mq5`
3. Нажать Compile (F7)
4. Проверить что нет ошибок

### 4.3 Включить WebRequest
**ВАЖНО!** MT5 блокирует WebRequest по умолчанию.

1. Tools → Options → Expert Advisors
2. Поставить галочку "Allow WebRequest for listed URL"
3. Добавить URL: `https://your-project.supabase.co`
4. Нажать OK
5. Перезапустить MT5

### 4.4 Настроить параметры EA
1. Перетащить EA на график XAUUSD
2. Настроить параметры:
   - `SupabaseURL`: `https://your-project.supabase.co`
   - `SupabaseKey`: `your-anon-key`
   - `Symbol_Trade`: `XAUUSD`
   - `CheckInterval`: `15` (секунд)
   - `MaxSlippage`: `10.0`
3. Включить AutoTrading (кнопка в верхней панели)
4. Нажать OK

---

## Шаг 5: Тестирование

### 5.1 Ручной тест сигнала
```python
from astra_v2.mt5.mt5_signal_writer import write_signal

# Записать тестовый сигнал
signal = write_signal(
    direction='LONG',
    entry=2650.50,
    sl=2645.00,
    tp=2680.75,
    session='london',
    risk_usd=165
)
```

### 5.2 Проверить в Supabase
1. Table Editor → mt5_signals
2. Должна появиться новая запись со статусом `new`

### 5.3 Проверить в MT5
1. Открыть Experts log (View → Toolbox → Experts)
2. Через 15 секунд должно появиться:
   ```
   New signal found: LONG london @ 2650.5 SL:2645 TP:2680.75
   Trade executed successfully: LONG 0.01 lots @ 2650.52
   Signal 1 status updated to: active
   ```

### 5.4 Проверить сделку
1. View → Toolbox → Trade
2. Должна появиться открытая позиция XAUUSD
3. Комментарий: "Astra london"

---

## Шаг 6: Мониторинг

### 6.1 Логи Python
```bash
# Запустить стратегию с выводом логов
python scripts/combined_session_backtest.py
```

Должно выводить:
```
Signal written: LONG LONDON @ 2650.50, SL: 2645.00, TP: 2680.75, Risk: $165
```

### 6.2 Логи MT5
1. View → Toolbox → Experts
2. Фильтр по "Astra"
3. Смотреть сообщения:
   - "New signal found"
   - "Trade executed"
   - "Trailing stop updated"

### 6.3 Supabase Dashboard
1. Table Editor → mt5_signals
2. Смотреть статусы:
   - `new` → сигнал создан, ждет MT5
   - `active` → сделка открыта
   - `closed` → сделка закрыта

---

## Troubleshooting

### Проблема: WebRequest error -1
**Решение:** Включить WebRequest для Supabase URL в настройках MT5

### Проблема: "Active signal already exists"
**Решение:** Дождаться закрытия текущей сделки или вручную изменить статус в Supabase

### Проблема: "Failed to write signal"
**Решение:** Проверить SUPABASE_URL и SUPABASE_KEY в переменных окружения

### Проблема: Сделка не открывается
**Решение:** 
1. Проверить что AutoTrading включен в MT5
2. Проверить баланс и маржу
3. Проверить что символ XAUUSD доступен

### Проблема: Trailing stop не работает
**Решение:** Проверить что позиция открыта и `initialEntry`, `initialSL` установлены

---

## Безопасность

### Production настройки:
1. Использовать Service Role Key вместо Anon Key для Python
2. Настроить Row Level Security (RLS) в Supabase
3. Ограничить доступ к таблице по IP
4. Использовать HTTPS для всех запросов
5. Хранить ключи в защищенном хранилище (не в коде!)

### Рекомендации:
- Тестировать на демо-счете перед live
- Мониторить логи обоих компонентов
- Установить алерты на критические ошибки
- Регулярно проверять синхронизацию сигналов

---

## Параметры стратегии

### Текущие настройки (v2.1):
- **Risk per trade**: $165
- **TP_RR**: 5.5
- **Step Trailing**: Включен (2R→1R, 3R→2R, 4R→3R, 5R→4R)
- **H4 EMA20 filter**: Включен
- **Sessions**: Asian (0-7h), London (7-12h), NY (13-17h)

### Ожидаемая производительность:
- Среднемесячная прибыль: ~$560 (5.6%)
- Win Rate: 50.8%
- Max DD: 6.48%
- Trades per month: ~5-6

---

## Контакты и поддержка

GitHub: https://github.com/vispir/AstraAnalyzerPro
Branch: feat/astra-v2.1
