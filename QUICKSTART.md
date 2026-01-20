# ⚡ Быстрый старт

## Шаг 1: Установка зависимостей
```bash
pip install -r requirements.txt
```

## Шаг 2: Настройка .env
```bash
# Windows
ren env.template .env

# Linux/Mac  
mv env.template .env
```

Откройте `.env` и вставьте свой Gemini API ключ:
```
GEMINI_API_KEY=ваш_ключ_здесь
```

Получить ключ: https://makersuite.google.com/app/apikey

## Шаг 3: Запуск

### Терминал 1 - Backend
```bash
python server.py
```

### Терминал 2 (или браузер)
Откройте `main.html` в браузере

## Проверка работы

✅ В терминале видно: "ASTRA ANALYZER PRO - SERVER STARTING"  
✅ В браузере график загружается  
✅ Баланс отображается  
✅ При клике на график можно ставить уровни  

## Быстрый тест AI

1. Кликните "📍 Установить Entry" → кликните на график
2. Кликните "🛑 Установить Stop Loss" → кликните ниже Entry
3. Кликните "🎯 Установить Take Profit" → кликните выше Entry  
4. Нажмите "Анализ AI"

Готово! 🚀
