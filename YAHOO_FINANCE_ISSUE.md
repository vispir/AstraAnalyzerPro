# Yahoo Finance Недоступен Локально

## Проблема
Yahoo Finance API недоступен на вашем локальном компьютере из-за:
- Региональных ограничений
- Блокировки провайдером
- Настроек firewall/антивируса

## Решение

### Вариант 1: Использовать Twelve Data API (Рекомендуется)

Добавьте параметр `source=twelvedata` ко всем запросам:

```
GET /api/market/candles?tf=M15&limit=100&source=twelvedata
```

В frontend измените запросы:
```javascript
const response = await fetch('/api/market/candles?source=twelvedata');
```

### Вариант 2: Использовать VPN

1. Установите VPN
2. Подключитесь к серверу в США или Европе
3. Перезапустите приложение

### Вариант 3: Изменить источник по умолчанию

Измените в `routes/market_routes.py` строку 37:

```python
# Было:
source = request.args.get('source', 'twelvedata')

# Изменить на:
source = request.args.get('source', 'twelvedata')  # уже правильно!
```

По умолчанию используется `twelvedata`, так что просто не указывайте `source=yfinance`.

## Проверка доступности

Запустите тест:
```bash
python -c "import yfinance as yf; print(yf.Ticker('GC=F').history(period='1d'))"
```

Если выдает ошибку `'NoneType' object is not subscriptable` — Yahoo Finance недоступен.

## Текущая конфигурация

✅ Twelve Data API работает  
❌ Yahoo Finance API заблокирован локально  
✅ На удаленном сервере Yahoo Finance работает
