# Чеклист перехода TEST → LIVE режим
**Дата создания:** 2026-04-25

---

## 🎯 Где находится TEST_MODE

### **1. VPS Bridge (Python)**
**Файл:** `vps/mt5_bridge_fileexchange.py` (основной)

**Текущее состояние:**
```python
TEST_MODE = os.getenv("TEST_MODE", "true").lower() == "true"
```

**Где используется:**
- Читает сигналы из Supabase
- Записывает в JSON файл для MT5 EA
- Логирует режим: "TEST (no real trades)" или "LIVE (real trades)"

---

### **2. Environment Variable (.env на VPS)**
**Файл:** `.env` на VPS

**Текущее состояние:**
```bash
TEST_MODE=true
```

**Что делает:**
- Передает значение в Python bridge через `os.getenv()`
- Это ГЛАВНЫЙ переключатель

---

### **3. MT5 EA v2**
**Файл:** `vps/AstraSessionBreakout_v2.mq5`

**Текущее состояние:**
```mql5
input bool TestMode = true;  // Test mode (no real trades)
```

**Где используется:**
- Строка 16: Input параметр
- Строка 36: Логирует "Test Mode: ON (no real trades)" или "OFF (live trading)"
- Строка 100: Проверка перед открытием сделки
- Строка 290: Проверка перед модификацией ордера

**Что делает:**
- Если `TestMode = true` → EA только логирует, НЕ открывает сделки
- Если `TestMode = false` → EA открывает реальные сделки

---

## ✅ Что нужно сделать для перехода в LIVE

### **Вариант 1: Изменить только .env (РЕКОМЕНДУЕТСЯ)**

На VPS в файле `.env`:
```bash
# БЫЛО:
TEST_MODE=true

# СТАЛО:
TEST_MODE=false
```

**Затем перезапустить bridge:**
```bash
# На VPS
pkill -f mt5_bridge_fileexchange.py
python vps/mt5_bridge_fileexchange.py &
```

---

### **Вариант 2: Изменить в коде (НЕ рекомендуется)**

В `vps/mt5_bridge_fileexchange.py`:
```python
# БЫЛО:
TEST_MODE = os.getenv("TEST_MODE", "true").lower() == "true"

# СТАЛО:
TEST_MODE = os.getenv("TEST_MODE", "false").lower() == "true"
```

**Проблема:** Нужно коммитить и пушить изменения, сложнее откатить.

---

## 🔍 Как проверить текущий режим

### **1. Логи bridge на VPS:**
```bash
tail -f /path/to/bridge.log
```

Ищите строку:
```
Mode: TEST (no real trades)  # TEST режим
Mode: LIVE (real trades)     # LIVE режим
```

### **2. Проверить .env на VPS:**
```bash
cat .env | grep TEST_MODE
```

### **3. Проверить MT5 терминал:**
- Открыты ли реальные позиции?
- Есть ли ордера в истории?

---

## ⚠️ ВАЖНО: Нужно менять ВО ВСЕХ местах?

**ДА!** Нужно изменить в **ДВУХ местах**:

1. **.env на VPS** (для Python bridge)
2. **EA v2 параметры в MT5** (для Expert Advisor)

### **Почему нужно менять в обоих:**

**1. Python Bridge (через .env):**
```python
TEST_MODE = os.getenv("TEST_MODE", "true").lower() == "true"
```
- Если `TEST_MODE=true` → bridge НЕ пишет сигналы в JSON
- Если `TEST_MODE=false` → bridge пишет сигналы в JSON

**2. MT5 EA v2 (через input параметр):**
```mql5
input bool TestMode = true;
```
- Если `TestMode = true` → EA читает JSON, но НЕ открывает сделки (только логирует)
- Если `TestMode = false` → EA читает JSON И открывает реальные сделки

### **Двойная защита:**

Это **специально сделано** для безопасности:
- Даже если bridge случайно запишет сигнал в TEST режиме → EA не откроет сделку
- Даже если EA в LIVE режиме → без сигналов от bridge ничего не произойдет

**Оба флага должны быть `false` для реальной торговли!**

---

## 📋 Пошаговая инструкция перехода в LIVE

### **Шаг 1: Убедитесь что стратегия работает в TEST**
- [ ] Render cron работает (каждые 15 минут)
- [ ] VPS bridge читает сигналы из Supabase
- [ ] MT5 EA видит JSON файлы
- [ ] Telegram уведомления приходят

### **Шаг 2: Подключите VPS к LIVE счету**
- [ ] Откройте MT5 на VPS
- [ ] Войдите в LIVE счет Funding Pips
- [ ] Убедитесь что баланс = $9,950

### **Шаг 3: Измените .env на VPS И EA параметры в MT5**

**A. Изменить .env на VPS:**
```bash
# На VPS
nano .env

# Измените:
TEST_MODE=false

# Сохраните: Ctrl+O, Enter, Ctrl+X
```

**B. Изменить EA параметры в MT5:**
1. Откройте MT5 на VPS
2. Найдите EA на графике XAUUSD M15
3. Правой кнопкой → "Свойства"
4. Вкладка "Входные параметры"
5. Найдите `TestMode`
6. Измените с `true` на `false`
7. Нажмите "OK"

**Или перезагрузите EA с новыми параметрами:**
1. Удалите EA с графика
2. Перетащите `AstraSessionBreakout_v2.mq5` на график
3. В окне параметров установите `TestMode = false`
4. Нажмите "OK"

### **Шаг 4: Перезапустите bridge**
```bash
# Остановите старый процесс
pkill -f mt5_bridge_fileexchange.py

# Запустите новый
cd /path/to/project
python vps/mt5_bridge_fileexchange.py &

# Проверьте логи
tail -f bridge.log
```

Должны увидеть:
```
Mode: LIVE (real trades)
```

### **Шаг 5: Мониторинг первых сделок**
- [ ] Дождитесь первого сигнала от Render
- [ ] Проверьте что bridge записал в JSON
- [ ] Проверьте что EA открыл позицию в MT5
- [ ] Проверьте Telegram уведомление

### **Шаг 6: Откат в TEST (если что-то пошло не так)**
```bash
# На VPS
nano .env
# Измените обратно: TEST_MODE=true
# Перезапустите bridge
pkill -f mt5_bridge_fileexchange.py
python vps/mt5_bridge_fileexchange.py &
```

---

## 🚨 Критические проверки перед LIVE

- [ ] Баланс на счету = $9,950
- [ ] Risk per trade = $158 (1.59%)
- [ ] MT5 подключен к правильному счету (не demo!)
- [ ] EA загружен на график XAUUSD M15
- [ ] Bridge работает и видит Supabase
- [ ] Render cron работает (проверить последний запуск)
- [ ] Telegram бот отправляет уведомления
- [ ] Есть резервная копия .env (на случай отката)

---

## 📊 Мониторинг после перехода в LIVE

### **Первые 24 часа:**
- Проверять каждые 2-4 часа
- Следить за Telegram уведомлениями
- Проверять MT5 позиции
- Мониторить логи bridge

### **Первая неделя:**
- Проверять 2 раза в день
- Следить за DD (не должен превышать 10%)
- Проверять что все сделки соответствуют стратегии

### **Первый месяц:**
- Проверять раз в день
- Убедиться что минимум 1 сделка в месяц (для Funding Pips)
- Следить за PnL

---

## 🎯 Итоговый ответ на вопрос

**Нужно ли менять TEST_MODE в трех местах?**

**ДА, но только в ДВУХ местах:**

1. **.env на VPS** → `TEST_MODE=false`
2. **EA v2 в MT5** → `TestMode = false`

**Почему не три:**
- Старые bridge файлы (mt5_bridge.py, mt5_bridge_fixed.py, mt5_bridge_simple.py) НЕ используются
- Используется только `mt5_bridge_fileexchange.py`, который читает из `.env`

**Двойная защита = безопасность:**
- Bridge не пишет сигналы в TEST → EA не получает сигналы
- EA в TEST режиме → не открывает сделки даже если получит сигнал
- **Оба должны быть в LIVE для реальной торговли!**

---

**Готово к переходу в LIVE!** 🚀
