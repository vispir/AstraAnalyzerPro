# Инструкция по применению критических исправлений

## Что исправлено

✅ **Cron timing:** `*/15` → `1,16,31,46` (не пропускаем первую свечу breakout)
✅ **Candle count:** 300 → 500 M15 свечей (точнее H4 EMA20)
✅ **Код задеплоен** на GitHub (ветка deploy)

## Шаги для применения

### 1. Render (автоматически)
Render автоматически подтянет изменения из GitHub и обновит cron расписание.

**Проверка через 5-10 минут:**
1. Зайди на https://dashboard.render.com
2. Найди cron job "astra-session-breakout"
3. Проверь что расписание: `1,16,31,46 * * * *`
4. Если не обновилось - вручную измени в настройках

### 2. VPS (вручную)

**Обновить MT5 EA:**
```bash
# 1. Скачать обновленный EA с GitHub
cd ~
wget https://raw.githubusercontent.com/vispir/AstraAnalyzerPro/deploy/vps/AstraSessionBreakout_v2.mq5

# 2. Скопировать в MT5
cp AstraSessionBreakout_v2.mq5 /root/.mt5/drive_c/users/root/AppData/Roaming/MetaQuotes/Terminal/<TERMINAL_ID>/MQL5/Experts/

# 3. Открыть MT5 и перекомпилировать EA
# (через MetaEditor или просто перезапустить MT5)

# 4. Перезапустить EA на графике
```

**Или проще - через MT5 GUI:**
1. Открой MT5 на VPS
2. MetaEditor → File → Open → AstraSessionBreakout_v2.mq5
3. Замени содержимое на новый код (из GitHub)
4. Compile (F7)
5. Перезапусти EA на графике XAUUSD M15

### 3. Проверка работы

**Через 1 час проверь:**

**Render логи:**
```
✓ Loaded 500 candles from Supabase  (было: 300)
✓ Resampled 31 H4 bars, calculated EMA20  (было: 19-20)
```

**MT5 EA логи:**
```
Synced 500 M15 candles to file  (было: 300)
```

**Telegram статусы:**
- Должны приходить в XX:01, XX:16, XX:31, XX:46 (было: XX:00, XX:15, XX:30, XX:45)

## Ожидаемый результат

После применения исправлений:
- ✅ Не пропускаем первую свечу breakout окна (07:00, 13:00, 18:00 UTC)
- ✅ Точнее рассчитываем H4 EMA20 (31 бар вместо 19)
- ✅ Система 100% совпадает с бэктестом по таймингу

## Если что-то пошло не так

**Render не обновил cron:**
- Зайди в Dashboard → Cron Job → Settings
- Вручную измени Schedule на: `1,16,31,46 * * * *`
- Save

**MT5 EA не синхронизирует 500 свечей:**
- Проверь что перекомпилировал новую версию
- Перезапусти EA на графике
- Проверь логи: должно быть "Synced 500 M15 candles"

**Вопросы:**
- Проверь CRITICAL_TIMING_FIXES.md для деталей
- Или спроси меня
