# VPS Recovery Guide - Session Breakout v2.1

## Если VPS выключился или перезагрузился

### Шаг 1: Проверка состояния системы

```bash
# Подключись к VPS
ssh root@your-vps-ip

# Проверь запущен ли MT5
ps aux | grep wine

# Проверь запущен ли bridge
ps aux | grep mt5_bridge
```

---

## Сценарий 1: MT5 не запущен

### Запуск MT5 через Wine

```bash
# Перейди в директорию MT5
cd /root/.mt5/drive_c/Program\ Files/MetaTrader\ 5/

# Запусти MT5 в фоне
WINEPREFIX=/root/.mt5 wine terminal64.exe &

# Проверь что запустился
ps aux | grep wine
```

**Важно:** После запуска MT5:
1. Дождись загрузки терминала (~30 секунд)
2. Проверь что график XAUUSD M15 открыт
3. Проверь что EA `AstraSessionBreakout_v2` активен (смайлик в правом верхнем углу)
4. В логах (вкладка "Experts") должно быть:
   ```
   Astra Session Breakout EA v2.1 - Starting
   Risk: $158 | Magic: 20241121
   Trailing: Enabled
   Test Mode: ON (no real trades)
   ```

---

## Сценарий 2: Bridge не запущен

### Запуск Python Bridge

```bash
# Перейди в директорию bridge
cd ~/astra_mt5_bridge

# Активируй виртуальное окружение
source venv/bin/activate

# Запусти bridge в фоне
nohup python mt5_bridge.py > bridge.log 2>&1 &

# Проверь что запустился
ps aux | grep mt5_bridge

# Смотри логи
tail -f bridge.log
```

**Ожидаемые логи:**
```
MT5 Bridge Starting
Mode: TEST
MT5 Path: /root/.mt5/drive_c/users/root/AppData/Roaming/MetaQuotes/Terminal/Common/Files
```

**Следующая синхронизация свечей:**
- Время: XX:00:15, XX:15:15, XX:30:15, XX:45:15 UTC
- В логах появится: `Syncing candles from MT5... Found 300 candles... Synced 300 candles`

---

## Сценарий 3: Полная перезагрузка VPS

### Последовательность запуска (важен порядок!)

**1. Запусти MT5 (первым!)**
```bash
cd /root/.mt5/drive_c/Program\ Files/MetaTrader\ 5/
WINEPREFIX=/root/.mt5 wine terminal64.exe &
```

**2. Подожди 1 минуту** (MT5 должен загрузиться и подключиться к серверу)

**3. Запусти Bridge**
```bash
cd ~/astra_mt5_bridge
source venv/bin/activate
nohup python mt5_bridge.py > bridge.log 2>&1 &
```

**4. Проверь оба процесса**
```bash
ps aux | grep wine        # Должен быть terminal64.exe
ps aux | grep mt5_bridge  # Должен быть python mt5_bridge.py
```

---

## Проверка работоспособности системы

### 1. Проверь MT5 EA

```bash
# Смотри логи MT5 (если есть доступ к GUI)
# Вкладка "Experts" внизу терминала
```

Должно быть каждые 15 минут (XX:00:10, XX:15:10, XX:30:10, XX:45:10):
```
Synced 300 M15 candles to file
```

### 2. Проверь Bridge логи

```bash
tail -f ~/astra_mt5_bridge/bridge.log
```

Должно быть каждые 15 минут (XX:00:15, XX:15:15, XX:30:15, XX:45:15):
```
Syncing candles from MT5...
Found 300 candles
Synced 300 candles
```

### 3. Проверь Supabase

Зайди в Supabase → Table Editor → `mt5_candles`
- Должно быть 300 записей
- Последняя запись должна быть свежей (не старше 15 минут)

### 4. Проверь Render

Зайди в Render → Logs
- Каждые 15 минут (XX:00, XX:15, XX:30, XX:45) должен быть запуск
- Должно быть: `✓ Loaded 300 candles from Supabase`

---

## Типичные проблемы и решения

### Проблема: MT5 не запускается

**Решение:**
```bash
# Убей все процессы wine
pkill -9 wine
pkill -9 wineserver

# Подожди 5 секунд
sleep 5

# Запусти заново
cd /root/.mt5/drive_c/Program\ Files/MetaTrader\ 5/
WINEPREFIX=/root/.mt5 wine terminal64.exe &
```

### Проблема: Bridge выдает ошибку 401 (Unauthorized)

**Причина:** Неправильный API ключ в `.env`

**Решение:**
```bash
nano ~/astra_mt5_bridge/.env
```

Проверь что есть:
```
SUPABASE_URL=https://lkznmelzulctjqzhvzuy.supabase.co
SUPABASE_KEY=твой_anon_public_key
TEST_MODE=true
```

Перезапусти bridge после изменений.

### Проблема: Bridge выдает ошибку 409 (Conflict)

**Причина:** Нет RLS политики для UPDATE

**Решение:**
1. Зайди в Supabase → Table Editor → `mt5_candles` → RLS
2. Проверь что есть 3 политики:
   - `Allow insert candles` (INSERT)
   - `Allow read candles` (SELECT)
   - `Allow update candles` (UPDATE)
3. Если нет UPDATE политики - создай её (см. основную документацию)

### Проблема: EA не синхронизирует свечи

**Решение:**
```bash
# Проверь что файл создается
ls -lh /root/.mt5/drive_c/users/root/AppData/Roaming/MetaQuotes/Terminal/Common/Files/

# Должен быть файл astra_candles.json
# Если его нет - EA не работает, перезапусти MT5
```

### Проблема: Render не читает свечи из Supabase

**Причина:** Свечи не попадают в базу или Render читает старые данные

**Решение:**
1. Проверь что bridge синхронизирует свечи (см. логи)
2. Проверь что в Supabase есть свежие данные
3. Проверь логи Render - должно быть `✓ Loaded 300 candles from Supabase`

---

## Автозапуск при перезагрузке VPS (опционально)

Если хочешь чтобы все запускалось автоматически:

### Создай systemd сервис для MT5

```bash
nano /etc/systemd/system/mt5.service
```

Вставь:
```ini
[Unit]
Description=MetaTrader 5
After=network.target

[Service]
Type=forking
User=root
Environment="WINEPREFIX=/root/.mt5"
WorkingDirectory=/root/.mt5/drive_c/Program Files/MetaTrader 5
ExecStart=/usr/bin/wine terminal64.exe
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

### Создай systemd сервис для Bridge

```bash
nano /etc/systemd/system/mt5-bridge.service
```

Вставь:
```ini
[Unit]
Description=MT5 Bridge
After=network.target mt5.service
Requires=mt5.service

[Service]
Type=simple
User=root
WorkingDirectory=/root/astra_mt5_bridge
ExecStart=/root/astra_mt5_bridge/venv/bin/python /root/astra_mt5_bridge/mt5_bridge.py
Restart=always
RestartSec=10
StandardOutput=append:/root/astra_mt5_bridge/bridge.log
StandardError=append:/root/astra_mt5_bridge/bridge.log

[Install]
WantedBy=multi-user.target
```

### Включи автозапуск

```bash
systemctl daemon-reload
systemctl enable mt5.service
systemctl enable mt5-bridge.service
systemctl start mt5.service
systemctl start mt5-bridge.service

# Проверь статус
systemctl status mt5.service
systemctl status mt5-bridge.service
```

Теперь при перезагрузке VPS все запустится автоматически!

---

## Контакты для проверки

- **Supabase:** https://supabase.com/dashboard/project/lkznmelzulctjqzhvzuy
- **Render:** https://dashboard.render.com
- **Telegram Main Bot:** @AstraAnalyzerPro_bot
- **Telegram Signal Bot:** @AstraSignal_Bot
- **Admin Chat ID:** 788797319

---

## Быстрая проверка "все ли работает"

```bash
# 1. Проверь процессы
ps aux | grep wine && ps aux | grep mt5_bridge

# 2. Проверь логи bridge
tail -20 ~/astra_mt5_bridge/bridge.log

# 3. Проверь что файлы создаются
ls -lh /root/.mt5/drive_c/users/root/AppData/Roaming/MetaQuotes/Terminal/Common/Files/astra_candles.json

# 4. Проверь последнюю синхронизацию
tail -5 ~/astra_mt5_bridge/bridge.log | grep "Synced"
```

Если все 4 пункта ОК - система работает! ✅
