# Quick Start Guide - Session Breakout v2.1

## Быстрая инструкция по запуску системы

### 1. Запуск MT5 Bridge

```bash
# Подключись к VPS
ssh root@your-vps-ip

# Перейди в папку bridge
cd ~/astra_mt5_bridge

# Активируй виртуальное окружение
source venv/bin/activate

# Запусти bridge в фоне
nohup python mt5_bridge.py > bridge.log 2>&1 &

# Проверь что запустился
ps aux | grep mt5_bridge

# Смотри логи (Ctrl+C чтобы выйти)
tail -f bridge.log
```

**Ожидаемый вывод в логах:**
```
MT5 Bridge Starting
Mode: TEST
MT5 Path: /root/.mt5/drive_c/users/root/AppData/Roaming/MetaQuotes/Terminal/Common/Files
```

---

### 2. Запуск MT5 и EA

**2.1. Запусти MT5 через Wine**
```bash
cd /root/.mt5/drive_c/Program\ Files/MetaTrader\ 5/
WINEPREFIX=/root/.mt5 wine terminal64.exe &
```

**2.2. Подожди 30 секунд** пока MT5 загрузится

**2.3. Открой график XAUUSD M15**
- File → New Chart → XAUUSD
- Переключи таймфрейм на M15

**2.4. Перетащи EA на график**
- Navigator (Ctrl+N) → Expert Advisors → `AstraSessionBreakout_v2`
- Перетащи на график XAUUSD M15

**2.5. Настрой параметры EA**
В окне настроек проверь:
- `RiskUSD` = **158.0**
- `MagicNumber` = **20241121**
- `EnableTrailing` = **true**
- `TestMode` = **true** (для тестирования, false для live)
- `CheckInterval` = **5**

Нажми **OK**

**2.6. Проверь что EA запустился**
- В правом верхнем углу графика должен быть смайлик 😊
- Внизу во вкладке "Experts" должно быть:
```
Astra Session Breakout EA v2.1 - Starting
Risk: $158 | Magic: 20241121
Trailing: Enabled
Test Mode: ON (no real trades)
```

---

### 3. Проверка работы системы

**3.1. Проверь синхронизацию свечей (каждые 15 минут)**

В MT5 логах (вкладка "Experts"):
```
Synced 300 M15 candles to file
```

В Bridge логах:
```bash
tail -f ~/astra_mt5_bridge/bridge.log
```
Должно быть:
```
Syncing candles from MT5...
Found 300 candles
Synced 300 candles
```

**3.2. Проверь Supabase**
- Зайди в https://supabase.com/dashboard
- Table Editor → `mt5_candles`
- Должно быть 300 записей с XAUUSD M15

**3.3. Проверь Render**
- Зайди в https://dashboard.render.com
- Открой логи своего сервиса
- Каждые 15 минут должно быть:
```
✓ Loaded 300 candles from Supabase
✓ No entry conditions met for any session
```

---

### 4. Расписание синхронизации

**MT5 EA синхронизирует свечи:**
- 00:10, 00:25, 00:40, 00:55 UTC
- 01:10, 01:25, 01:40, 01:55 UTC
- И так далее каждые 15 минут

**Bridge отправляет в Supabase:**
- 00:15, 00:30, 00:45, 01:00 UTC
- 01:15, 01:30, 01:45, 02:00 UTC
- И так далее каждые 15 минут

**Render проверяет условия:**
- 00:00, 00:15, 00:30, 00:45 UTC
- 01:00, 01:15, 01:30, 01:45 UTC
- И так далее каждые 15 минут

---

### 5. Telegram уведомления

**Каждые 15 минут приходит статус:**
```
📊 Session Breakout Monitor
2026-04-21 23:00 UTC

💰 Price: $2650.50
🕐 Session: ASIAN BREAKOUT
⏳ No entry conditions met
```

**При генерации сигнала:**
```
🎯 NEW SIGNAL - ASIAN SESSION

Direction: LONG
Entry: 2650.50
Stop Loss: 2648.30
Take Profit: 2663.40
Risk: $158
R:R: 5.5
```

---

### 6. Остановка системы

**Остановить Bridge:**
```bash
pkill -f mt5_bridge.py
```

**Остановить MT5:**
```bash
pkill -9 wine
pkill -9 wineserver
```

**Остановить EA (в MT5):**
- Кликни правой кнопкой на график → Expert Advisors → Remove

---

### 7. Переключение в LIVE режим

**Когда будешь готов торговать реально:**

1. Открой MetaEditor (F4 в MT5)
2. Найди `AstraSessionBreakout_v2.mq5`
3. Измени `input bool TestMode = true;` на `input bool TestMode = false;`
4. Compile (F7)
5. Перезапусти EA на графике

**Или просто измени параметр при запуске EA:**
- Перетащи EA на график
- В настройках измени `TestMode` на **false**
- OK

---

### 8. Быстрая проверка "все работает"

```bash
# 1. Проверь процессы
ps aux | grep wine && ps aux | grep mt5_bridge

# 2. Проверь логи bridge
tail -20 ~/astra_mt5_bridge/bridge.log

# 3. Проверь файлы
ls -lh /root/.mt5/drive_c/users/root/AppData/Roaming/MetaQuotes/Terminal/Common/Files/

# 4. Проверь последнюю синхронизацию
tail -5 ~/astra_mt5_bridge/bridge.log | grep "Synced"
```

Если все 4 команды показывают результат - система работает! ✅

---

## Контакты

- **VPS IP:** your-vps-ip
- **Supabase:** https://supabase.com/dashboard/project/lkznmelzulctjqzhvzuy
- **Render:** https://dashboard.render.com
- **Telegram Admin Chat ID:** 788797319
- **Main Bot:** @AstraAnalyzerPro_bot
- **Signal Bot:** @AstraSignal_Bot

---

## Важные файлы

- **Bridge код:** `~/astra_mt5_bridge/mt5_bridge.py`
- **Bridge логи:** `~/astra_mt5_bridge/bridge.log`
- **Bridge .env:** `~/astra_mt5_bridge/.env`
- **EA код:** `/root/.mt5/drive_c/Program Files/MetaTrader 5/MQL5/Experts/AstraSessionBreakout_v2.mq5`
- **MT5 файлы:** `/root/.mt5/drive_c/users/root/AppData/Roaming/MetaQuotes/Terminal/Common/Files/`

---

## Стратегия Session Breakout v2.1

**Параметры:**
- Risk: $158 (фиксированный)
- TP: 5.5R
- Step Trailing: 2R→1R, 3R→2R, 4R→3R, 5R→4R
- Direction: LONG only
- H4 EMA20 filter: Enabled

**Сессии:**
- Asian: Range 0-7 UTC, Breakout 7-10 UTC
- London: Range 7-12 UTC, Breakout 13-16 UTC
- NY: Range 13-17 UTC, Breakout 18-21 UTC

**Результаты бэктеста (2020-2026):**
- Total PnL: +$40,134
- Max DD: 6.32%
- Win Rate: 41.7%
- Profit Factor: 3.89
- Total Trades: 360

✅ Проходит все лимиты Funding Pips!
