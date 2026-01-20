# 🏗️ Архитектура Astra Analyzer Pro v2.0

## 📁 Структура проекта

```
AstraAnalyzerPro/
├── config/
│   ├── __init__.py
│   └── settings.py          # Конфигурация из .env
├── services/
│   ├── __init__.py
│   ├── yfinance_service.py  # Работа с Yahoo Finance API
│   ├── gemini_service.py    # AI анализ через Gemini
│   └── calculator.py        # Торговые расчеты
├── routes/
│   ├── __init__.py
│   ├── market_routes.py     # Эндпоинты для рыночных данных
│   └── analysis_routes.py   # Эндпоинты для анализа
├── server.py                # Главный файл сервера
├── main.html                # Frontend
├── logo.svg                 # Логотип
├── env.template             # Шаблон переменных окружения
├── requirements.txt         # Python зависимости
└── .gitignore              # Игнорируемые файлы
```

## 🔧 Компоненты

### Config Layer (config/)

**settings.py** - Централизованная конфигурация
- Загрузка переменных окружения из `.env`
- Маппинг таймфреймов для Yahoo Finance
- Константы для торговли

### Service Layer (services/)

**yfinance_service.py** - Данные с Yahoo Finance
- `get_candles()` - получение свечных данных
- `get_current_price()` - текущая цена
- `get_ticker_info()` - информация о тикере
- `validate_symbol()` - проверка доступности

**gemini_service.py** - AI анализ
- `analyze_trade()` - анализ торговой установки
- `is_available()` - проверка доступности API

**calculator.py** - Торговые расчеты
- `calculate_trade_params()` - расчет лота, R:R, рисков
- `calculate_breakeven()` - уровень безубытка
- `calculate_position_size()` - размер позиции
- `calculate_daily_drawdown()` - дневная просадка

### Route Layer (routes/)

**market_routes.py** - Эндпоинты рыночных данных
```
GET  /api/market/stats?tf=M15       - статистика и свечи
GET  /api/market/candles?tf=M15     - только свечи
GET  /api/market/ticker-info        - информация о тикере
GET  /api/market/current-price      - текущая цена
GET  /api/market/config             - конфигурация
GET  /api/market/health             - healthcheck
```

**analysis_routes.py** - Эндпоинты анализа
```
POST /api/analysis/calculate        - расчет параметров сделки
POST /api/analysis/analyze          - AI анализ
POST /api/analysis/breakeven        - уровень безубытка
POST /api/analysis/drawdown         - расчет просадки
GET  /api/analysis/ai-status        - статус AI сервиса
```

### Обратная совместимость

Старые эндпоинты (без префикса `/api/`) работают для совместимости:
- `/stats` → `/api/market/stats`
- `/config` → `/api/market/config`
- `/calculate` → `/api/analysis/calculate`
- `/analyze` → `/api/analysis/analyze`

## 🔄 Поток данных

### 1. Получение данных графика

```
Frontend → GET /api/market/stats?tf=M15
          ↓
       market_routes.get_stats()
          ↓
       yfinance_service.get_candles()
          ↓
       Yahoo Finance API
          ↓
       Обработка данных
          ↓
       JSON Response → Frontend
```

### 2. Расчет параметров сделки

```
Frontend → POST /api/analysis/calculate
           {entry, sl, tp, balance}
          ↓
       analysis_routes.calculate_trade()
          ↓
       calculator.calculate_trade_params()
          ↓
       {rr_ratio, lot, stop_points, ...}
          ↓
       JSON Response → Frontend
```

### 3. AI анализ

```
Frontend → POST /api/analysis/analyze
           {entry, sl, tp, balance, equity, lot}
          ↓
       analysis_routes.analyze_trade()
          ↓
       gemini_service.analyze_trade()
          ↓
       Gemini API
          ↓
       {analysis: "текст анализа"}
          ↓
       JSON Response → Frontend
```

## 🌐 Yahoo Finance Integration

### Символы
- **XAUUSD** → **GC=F** (Gold Futures)

### Таймфреймы
```python
M15  → 15m   (15 минут)
H1   → 60m   (1 час)
H4   → 1h    (4 часа - используется агрегация)
D1   → 1d    (1 день)
```

### Периоды данных
```python
M15  → 5d    (5 дней)
H1   → 1mo   (1 месяц)
H4   → 3mo   (3 месяца)
D1   → 1y    (1 год)
```

## 🔐 Безопасность

1. **API ключи в .env** - не коммитятся в Git
2. **Валидация входных данных** - на всех эндпоинтах
3. **Логирование** - все ошибки записываются
4. **CORS** - настроен для безопасности

## 🧪 Тестирование

### Проверка сервисов

```python
# Проверка Yahoo Finance
from services.yfinance_service import yfinance_service
data = yfinance_service.get_candles('M15')
print(data)

# Проверка калькулятора
from services.calculator import calculator
result = calculator.calculate_trade_params(2650, 2640, 2670, 5000)
print(result)

# Проверка AI
from services.gemini_service import gemini_service
print(gemini_service.is_available())
```

### Проверка API

```bash
# Health check
curl http://127.0.0.1:5000/api/market/health

# Получение данных
curl http://127.0.0.1:5000/api/market/stats?tf=M15

# Расчет сделки
curl -X POST http://127.0.0.1:5000/api/analysis/calculate \
  -H "Content-Type: application/json" \
  -d '{"entry": 2650, "sl": 2640, "tp": 2670, "balance": 5000}'
```

## 📈 Расширение функционала

### Добавление нового сервиса

1. Создайте файл в `services/`
2. Реализуйте логику
3. Импортируйте в роуты
4. Используйте в эндпоинтах

### Добавление нового эндпоинта

1. Добавьте функцию в соответствующий blueprint
2. Используйте сервисы из `services/`
3. Добавьте обработку ошибок
4. Документируйте в docstring

## 🐛 Логирование

Все логи записываются в:
- **Консоль** - для разработки
- **astra_server.log** - файл с историей

Уровни логирования:
- `INFO` - обычные операции
- `WARNING` - предупреждения
- `ERROR` - ошибки
- `DEBUG` - отладочная информация (только при FLASK_DEBUG=True)

## 🚀 Преимущества новой архитектуры

✅ **Модульность** - легко добавлять новые функции  
✅ **Тестируемость** - каждый модуль можно тестировать отдельно  
✅ **Масштабируемость** - простое добавление новых источников данных  
✅ **Безопасность** - централизованная конфигурация  
✅ **Поддержка** - четкое разделение ответственности  
✅ **Независимость от MT5** - работа через Yahoo Finance API
