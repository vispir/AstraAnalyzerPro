# 🌟 Astra Analyzer Pro

Профессиональная система анализа торговли золотом (XAUUSD) с AI-рекомендациями через Gemini API и интеграцией экономических новостей.

## 🚀 Возможности

### 📊 Анализ графиков
- ✅ Мультитаймфреймовый анализ (M15, H1, H4)
- ✅ Автоматическая агрегация H1 → H4 свечей
- ✅ Интерактивные графики в стиле TradingView (Lightweight Charts)
- ✅ Реальные данные через Yahoo Finance

### 💰 Риск-менеджмент
- ✅ Автоматический расчет лота (0.5% от баланса)
- ✅ Проверка R:R соотношения (минимум 1:2)
- ✅ Контроль дневной просадки
- ✅ Калькулятор позиции

### 🤖 AI и Новости
- ✅ AI анализ сделок через Gemini API
- ✅ **LLM анализ через OpenRouter** (Google Gemini 2.0 Flash - бесплатно!)
- ✅ Автоматическая торговая сессия (London/NY overlap detection)
- ✅ Комплексный анализ: графики + новости + SMC уровни
- ✅ Экономический календарь (investpy)
- ✅ Геополитические новости о золоте (gnews)
- ✅ Комбинированная лента новостей

### ⚡ Кэширование
- ✅ **In-memory кэширование** для оптимизации производительности
- ✅ Кэширование данных свечей (TTL: 5 минут)
- ✅ Кэширование новостей (TTL: 30-60 минут)
- ✅ Умное кэширование графиков (инвалидация при изменении данных)
- ✅ API для мониторинга и управления кэшем
- ✅ Статистика использования (hit rate, размер, evictions)

### 🔐 Безопасность
- ✅ Безопасное хранение API ключей (.env)
- ✅ Модульная архитектура
- ✅ CORS защита

## 📋 Требования

- Python 3.8+
- Gemini API ключ (для AI анализа)

## 🔧 Установка

### 1. Клонируйте репозиторий
```bash
git clone <repository-url>
cd AstraAnalyzerPro
```

### 2. Установите зависимости
```bash
pip install -r requirements.txt
```

### 3. Настройте переменные окружения

Скопируйте `env.template` в `.env`:
```bash
# Windows
copy env.template .env

# Linux/Mac
cp env.template .env
```

Откройте `.env` и настройте параметры:
```env
# API Keys
GEMINI_API_KEY=your_gemini_api_key_here

# Trading Configuration
YAHOO_SYMBOL=GC=F
START_BALANCE=5000
DAILY_LOSS_LIMIT=250
MAX_LOT_SIZE=0.10
RISK_PERCENT=0.005

# Server Configuration
FLASK_PORT=5000
FLASK_DEBUG=False
```

### 4. Получите Gemini API ключ

1. Перейдите на [Google AI Studio](https://aistudio.google.com/app/apikey)
2. Создайте новый API ключ
3. Вставьте его в `.env` файл (`GEMINI_API_KEY`)

## 🎯 Запуск

### 1. Запустите backend сервер
```bash
python server.py
```

Вы должны увидеть:
```
==================================================
ASTRA ANALYZER PRO - SERVER STARTING
Yahoo Symbol: GC=F
Port: 5000
==================================================
```

### 2. Откройте frontend

Откройте `main.html` в браузере или используйте Live Server в VS Code.

Приложение будет доступно по адресу:
- Backend API: `http://127.0.0.1:5000`
- Frontend: `main.html` (откройте в браузере)

## 📖 Использование

### Установка уровней
1. Нажмите кнопку "📍 Установить Entry"
2. Кликните на графике для установки точки входа
3. Повторите для Stop Loss и Take Profit

### AI Анализ
1. Установите все три уровня (Entry, SL, TP)
2. Нажмите "Анализ AI"
3. Дождитесь ответа от AI с рекомендациями

### Автоматический расчет
- **R:R Ratio**: Рассчитывается автоматически
- **Размер лота**: Определяется на основе 0.5% риска от баланса
- **Минимум R:R**: 1:2 (иначе лот = 0.00)

## 🏗️ Архитектура

### Backend (модульная структура)

```
AstraAnalyzerPro/
├── server.py              # Главный сервер
├── config/
│   └── settings.py        # Конфигурация
├── services/
│   ├── yfinance_service.py   # Получение данных золота
│   ├── gemini_service.py     # AI анализ (старый)
│   ├── llm_service.py        # LLM анализ через OpenRouter ⭐ NEW
│   ├── calculator.py         # Расчет лота и R:R
│   ├── news_service.py       # Экономические новости
│   ├── chart_service.py      # Генерация графиков
│   └── smc_detector.py       # SMC анализ
└── routes/
    ├── market_routes.py      # API рынка
    ├── analysis_routes.py    # API анализа
    ├── news_routes.py        # API новостей
    ├── chart_routes.py       # API графиков
    └── llm_routes.py         # API LLM анализа ⭐ NEW
```

### API Endpoints

#### 📊 Market (рынок)
- `GET /api/market/candles?tf=H4&limit=50` - свечи по таймфрейму
- `GET /api/market/candles` - данные для AI (H4, H1, M15)
- `GET /api/market/config` - конфигурация приложения

#### 🤖 Analysis (анализ)
- `POST /api/analysis/calculate` - расчет лота и R:R
- `POST /api/analysis/analyze` - AI анализ сделки

#### 📰 News (новости)
- `GET /api/news/feed` - комбинированная лента новостей ⭐
- `GET /api/news/upcoming?hours=12` - предстоящие события
- `GET /api/news/past?hours=24` - прошедшие события
- `GET /api/news/geopolitical?days=7` - геополитические новости
- `GET /api/news/all` - все новости недели
- `GET /api/news/today` - новости на сегодня
- `GET /api/news/high-impact` - только High важности

#### 📈 Charts (графики)
- `GET /api/chart/generate?tf=M15` - генерация графика с SMC уровнями
- Поддержка таймфреймов: M15, H1, H4
- Возвращает изображение в base64 формате

#### 🧠 LLM (AI анализ через OpenRouter/Gemini/Gateway) ⭐ NEW!
- `GET /api/llm/analyze` - **полный анализ рынка с LLM**
- `GET /api/llm/analyze?model=gemini3` - анализ через Gemini 3 Pro
- `GET /api/llm/analyze?model=gateway` - анализ через AI Gateway (custom)
- `GET /api/llm/session` - текущая торговая сессия
- `GET /api/llm/status` - статус LLM сервиса

### Frontend (main.html)
- **Lightweight Charts** для графиков
- Чистый JavaScript (без фреймворков)
- Адаптивный UI в стиле TradingView
- Динамическое переключение таймфреймов

### Источники данных
- **Yahoo Finance** (yfinance) - котировки золота
- **Investing.com** (investpy) - экономический календарь
- **Google News** (gnews) - геополитические новости
- **Gemini API** - AI анализ (старый)
- **OpenRouter** + Google Gemini 2.0 Flash - комплексный LLM анализ ⭐ NEW

## ⚙️ Конфигурация

Все настройки в `.env`:

| Параметр | Описание | По умолчанию |
|----------|----------|--------------|
| `GEMINI_API_KEY` | API ключ для Gemini 3 Pro | - |
| `OPENROUTER_API_KEY` | API ключ для OpenRouter (опционально) | - |
| `AI_GATEWAY_URL` | URL для AI Gateway (опционально) | - |
| `AI_GATEWAY_KEY` | API ключ для AI Gateway (опционально) | - |
| `YAHOO_SYMBOL` | Тикер Yahoo Finance | GC=F (Gold Futures) |
| `START_BALANCE` | Начальный баланс | 5000 |
| `DAILY_LOSS_LIMIT` | Лимит дневной просадки | 250 |
| `MAX_LOT_SIZE` | Максимальный размер лота | 0.10 |
| `RISK_PERCENT` | Процент риска на сделку | 0.005 (0.5%) |
| `FLASK_PORT` | Порт сервера | 5000 |
| `FLASK_DEBUG` | Режим отладки | False |

> 💡 **Примечания:**
> - `OPENROUTER_API_KEY` не требуется для бесплатной модели Google Gemini 2.0 Flash
> - `AI_GATEWAY_URL` позволяет использовать Gateway с моделью `google/gemini-3-pro-preview`
> - Модель для Gateway: `google/gemini-3-pro-preview` (указана в коде)

### Таймфреймы

| TF | Описание | Источник данных |
|----|----------|-----------------|
| M15 | 15 минут | Yahoo Finance (15m) |
| H1 | 1 час | Yahoo Finance (1h) |
| H4 | 4 часа | Агрегация из H1 (200→50 свечей) |

## 📦 Кэширование

Система использует in-memory кэширование для оптимизации производительности:

### Типы кэшируемых данных

| Тип данных | TTL | Описание |
|------------|-----|----------|
| Данные свечей | 5 минут | Кэширование рыночных данных |
| Экономический календарь | 30 минут | События публикуются заранее |
| Геополитические новости | 60 минут | Новости обновляются реже |
| Изображения графиков | Бессрочно* | *Инвалидация при изменении данных |

### API для управления кэшем

```bash
# Статистика кэша
GET /api/analysis/cache/stats

# Детальная информация
GET /api/analysis/cache/info?prefix=candles

# Очистка кэша
POST /api/analysis/cache/clear
Content-Type: application/json
{"prefix": "candles"}

# Удаление истекших записей
POST /api/analysis/cache/cleanup
```

### Пример статистики

```json
{
  "cache_stats": {
    "size": 45,
    "hits": 1234,
    "misses": 56,
    "hit_rate": 95.65,
    "total_requests": 1290
  }
}
```

📖 Подробная документация: [CACHE_USAGE.md](CACHE_USAGE.md)

## 🐛 Устранение неполадок

### "SERVER OFFLINE"
**Решение:**
- Проверьте, что `server.py` запущен
- Убедитесь, что порт 5000 не занят
- Проверьте firewall/антивирус

### "Error fetching candles"
**Решение:**
- Проверьте интернет-соединение
- Yahoo Finance может быть временно недоступен
- Убедитесь, что `YAHOO_SYMBOL=GC=F` в `.env`
- Попробуйте очистить кэш: `POST /api/analysis/cache/clear`

### "ЛИМИТ ЗАПРОСОВ AI"
**Решение:**
- Gemini API имеет rate limits
- Подождите несколько минут
- Проверьте квоты в [Google AI Studio](https://aistudio.google.com)

### "Ошибка AI. Проверь VPN"
**Решение:**
- Gemini API может быть недоступен в вашем регионе
- Используйте VPN
- Проверьте правильность API ключа

### "investpy library is not installed"
**Решение:**
```bash
pip install investpy
```

### "gnews library is not installed"
**Решение:**
```bash
pip install gnews
```

### H4 свечи выглядят странно
**Это нормально!** H4 свечи агрегируются из H1 данных (4 свечи H1 → 1 свеча H4), так как Yahoo Finance не предоставляет прямой интервал 4h.

## 📊 Особенности реализации

### Агрегация H4 свечей
Yahoo Finance не поддерживает интервал 4h напрямую. Решение:
- Запрашиваем 200 свечей H1
- Агрегируем 4 H1 свечи → 1 H4 свеча
- Получаем 50 H4 свечей

### Фильтрация новостей
Все новости автоматически фильтруются:
- **Важность**: только High и Medium (Low исключены)
- **Валюта**: только USD (релевантно для золота)
- **Период**: upcoming (12ч), past (24ч), geopolitical (7 дней)

### Комбинированная лента `/api/news/feed`
Возвращает три категории новостей в одном запросе:
```json
{
  "upcoming": { "count": 5, "events": [...] },
  "past": { "count": 8, "events": [...] },
  "geopolitical": { "count": 15, "articles": [...] }
}
```

## 📦 Зависимости

Основные библиотеки:
- `Flask` - веб-сервер
- `yfinance` - данные Yahoo Finance
- `investpy` - экономический календарь
- `gnews` - геополитические новости
- `pandas` - обработка данных
- `python-dotenv` - переменные окружения

Полный список в `requirements.txt`

## 📝 Лицензия

MIT License

## 🤝 Вклад в проект

Pull requests приветствуются! Для крупных изменений сначала откройте issue.

## 📧 Контакты

Если у вас возникли вопросы, создайте issue в репозитории.

---

**Сделано с ❤️ для трейдеров золота 🏆**
