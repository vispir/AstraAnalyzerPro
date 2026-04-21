# MT5 Bridge для VPS

Скрипт для подключения MT5 (через Wine на Debian) к Supabase для автоматической торговли по стратегии Session Breakout v2.1.

## Архитектура

```
Render (Python)                    VPS (Debian + Wine + MT5)
├── session_breakout_trader.py     ├── mt5_bridge.py
│   └── Генерирует сигналы         │   └── Читает сигналы
│                                  │   └── Открывает сделки в MT5
└── Пишет в Supabase               └── Обновляет статус в Supabase
         ↓                                    ↑
         └────────── Supabase ───────────────┘
                   (mt5_signals table)
```

## Установка на VPS

### 1. Проверка Python

```bash
python3 --version  # Должен быть 3.8+
```

Если Python не установлен:
```bash
sudo apt update
sudo apt install python3 python3-pip python3-venv
```

### 2. Создание директории

```bash
mkdir -p ~/astra_mt5_bridge
cd ~/astra_mt5_bridge
```

### 3. Копирование файлов

Скопируй на VPS:
- `mt5_bridge.py`
- `requirements.txt`
- `.env.example`

```bash
# Через scp с локальной машины:
scp mt5_bridge.py user@vps:/home/user/astra_mt5_bridge/
scp requirements.txt user@vps:/home/user/astra_mt5_bridge/
scp .env.example user@vps:/home/user/astra_mt5_bridge/
```

### 4. Установка зависимостей

```bash
cd ~/astra_mt5_bridge
pip3 install -r requirements.txt
```

### 5. Настройка .env

```bash
cp .env.example .env
nano .env
```

Заполни:
```env
SUPABASE_URL=https://твой-проект.supabase.co
SUPABASE_KEY=твой-anon-key
TEST_MODE=true  # Сначала тестируем
```

### 6. Проверка MT5

Убедись что MT5 запущен через Wine:
```bash
ps aux | grep terminal64.exe
```

Если не запущен:
```bash
wine ~/.wine/drive_c/Program\ Files/MetaTrader\ 5/terminal64.exe &
```

### 7. Тестовый запуск

```bash
python3 mt5_bridge.py
```

Должен вывести:
```
================================================================================
MT5 Bridge - Starting
================================================================================
Mode: TEST (no real trades)
Check interval: 5s
✓ Bridge ready. Monitoring for signals...
```

### 8. Запуск в фоне (systemd)

Создай systemd service:

```bash
sudo nano /etc/systemd/system/astra-mt5-bridge.service
```

Содержимое:
```ini
[Unit]
Description=Astra MT5 Bridge
After=network.target

[Service]
Type=simple
User=твой-юзер
WorkingDirectory=/home/твой-юзер/astra_mt5_bridge
ExecStart=/usr/bin/python3 /home/твой-юзер/astra_mt5_bridge/mt5_bridge.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

Запуск:
```bash
sudo systemctl daemon-reload
sudo systemctl enable astra-mt5-bridge
sudo systemctl start astra-mt5-bridge
```

Проверка:
```bash
sudo systemctl status astra-mt5-bridge
sudo journalctl -u astra-mt5-bridge -f  # Логи в реальном времени
```

## Переключение в LIVE режим

После тестирования на demo:

1. Измени `.env`:
```env
TEST_MODE=false
```

2. Перезапусти:
```bash
sudo systemctl restart astra-mt5-bridge
```

## Логи

Логи пишутся в `mt5_bridge.log`:
```bash
tail -f ~/astra_mt5_bridge/mt5_bridge.log
```

## Мониторинг

Bridge проверяет сигналы каждые 5 секунд:
- Новые сигналы (status=new) → открывает сделку → обновляет status=active
- Активные сигналы (status=active) → проверяет закрылась ли позиция → обновляет status=closed

## Troubleshooting

### MT5 не подключается

```bash
# Проверь что MT5 запущен
ps aux | grep terminal64.exe

# Проверь логи MT5
tail -f ~/.wine/drive_c/Program\ Files/MetaTrader\ 5/Logs/*.log
```

### Ошибка "MetaTrader5 library not installed"

```bash
pip3 install --upgrade MetaTrader5
```

### Ошибка подключения к Supabase

Проверь `.env`:
- SUPABASE_URL должен быть без `/rest/v1` на конце
- SUPABASE_KEY должен быть anon/public key (не service_role)

## Безопасность

- `.env` файл содержит секретные ключи - не коммить в git
- Используй `anon` key, не `service_role` key
- TEST_MODE=true для тестирования на demo счете
- Регулярно проверяй логи

## Поддержка

Логи bridge: `~/astra_mt5_bridge/mt5_bridge.log`
Логи systemd: `sudo journalctl -u astra-mt5-bridge -f`
