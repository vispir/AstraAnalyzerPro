import os
from datetime import datetime, timedelta, timezone # Добавили timezone
import logging
import json
from services.db_service import db_service
from services.telegram_service import telegram_service 

# Переменная для отслеживания времени последнего сигнала (теперь дублируется в облаке)
LAST_SIGNAL_TIME = None 

# константы
SIGNAL_COOLDOWN_HOURS = 2  # Кулдаун для BUY/SELL сигналов
WAIT_COOLDOWN_HOURS = 1    # Кулдаун для вердиктов WAIT
STRONG_SETUPS = ['BOS', 'CHOCH', 'OB_RETEST', 'FVG_FILL', 'SWEEP']

# Подключение сервисов
try:
    from services.oanda_service import oanda_service
    from services.llm_service import llm_service
    try:
        from services.smc_detector import smc_detector 
    except ImportError:
        smc_detector = None
    from services.news_service import news_service
except ImportError as e:
    print(f"Watcher Critical Import Error: {e}")

logger = logging.getLogger("AstraWatcher")

def is_market_active():
    """
    Проверка активности рынка
    Рынок золота работает: воскресенье 23:00 UTC - пятница 22:00 UTC
    """
    # Используем UTC для точности
    now = datetime.now(timezone.utc)
    
    weekday = now.weekday()  # 0=Monday, 6=Sunday
    hour = now.hour
    
    # Суббота - рынок полностью закрыт
    if weekday == 5:
        logger.debug(f"Market CLOSED: Saturday (UTC: {now.strftime('%Y-%m-%d %H:%M')})")
        return False
    
    # Воскресенье - рынок открывается в 23:00 UTC
    if weekday == 6:
        if hour < 23:
            logger.debug(f"Market CLOSED: Sunday before 23:00 UTC (now: {hour}:00)")
            return False
        else:
            logger.debug(f"Market OPEN: Sunday after 23:00 UTC (now: {hour}:00)")
            return True
    
    # Пятница - рынок закрывается в 22:00 UTC
    if weekday == 4:
        if hour >= 22:
            logger.debug(f"Market CLOSED: Friday after 22:00 UTC (now: {hour}:00)")
            return False
    
    # Понедельник утро - рынок открыт с 00:00 UTC (после открытия в воскресенье 23:00)
    logger.debug(f"Market OPEN: Weekday={weekday}, Hour={hour}:00 UTC")
    return True

def is_news_blockactive():
    """Твоя логика блокировки по новостям"""
    if not news_service: return False
    try:
        upcoming_news = news_service.get_upcoming_news(hours=2, currencies=['USD'], impact=['High'])
        past_news = news_service.get_past_news(hours=1)
        now_ts = int(datetime.now().timestamp())
        for event in upcoming_news:
            ts = event.get('timestamp')
            if ts and (ts - now_ts) < (45 * 60):
                logger.warning(f"🚫 NEWS BLOCK: {event['title']}")
                return True
        for event in past_news:
            ts = event.get('timestamp')
            if ts and (now_ts - ts) < (15 * 60):
                logger.warning(f"🚫 NEWS BLOCK: {event['title']}")
                return True
    except Exception as e:
        logger.error(f"Ошибка новостей: {e}")
    return False

def check_smart_cooldown():
    """
    Умная проверка кулдауна через Supabase
    Разделяет блокировку для ТОРГОВЫХ сигналов (2ч) и WAIT (1ч)
    """
    try:
        now = datetime.now(timezone.utc)
        
        # 1. Проверка BUY/SELL
        last_trade = db_service.get_last_trade_signal_time()
        if (now - last_trade).total_seconds() < (SIGNAL_COOLDOWN_HOURS * 3600):
            logger.info("⏳ Кулдаун СДЕЛКИ активен. Пропуск.")
            return False

        # 2. Проверка WAIT
        last_wait = db_service.get_last_wait_time()
        if (now - last_wait).total_seconds() < (WAIT_COOLDOWN_HOURS * 3600):
            logger.info("⏳ Кулдаун ОЖИДАНИЯ (WAIT) активен. Пропуск.")
            return False
        
        return True
    except Exception as e:
        logger.error(f"Ошибка проверки кулдауна через БД: {e}")
        return True 

def is_price_near_smc_structure(current_price, analysis, threshold_percent=0.5):
    """
    Проверяет, находится ли текущая цена близко к значимым SMC структурам.
    """
    threshold = current_price * (threshold_percent / 100)
    
    near_structures = []
    
    # 1. Order Blocks
    for ob in analysis.get('order_blocks', []):
        ob_top = ob.get('top', 0)
        ob_bottom = ob.get('bottom', 0)
        if ob_bottom - threshold <= current_price <= ob_top + threshold:
            ob_type = ob.get('type', 'OB')
            near_structures.append(f"{ob_type} [{ob_bottom:.2f}-{ob_top:.2f}]")
    
    # 2. Fair Value Gaps
    for fvg in analysis.get('fvg', []):
        fvg_top = fvg.get('top', 0)
        fvg_bottom = fvg.get('bottom', 0)
        if fvg_bottom - threshold <= current_price <= fvg_top + threshold:
            fvg_type = fvg.get('type', 'FVG')
            near_structures.append(f"{fvg_type} [{fvg_bottom:.2f}-{fvg_top:.2f}]")
    
    # 3. Liquidity Levels
    for liq in analysis.get('liquidity', []):
        liq_price = liq.get('price', 0)
        if abs(current_price - liq_price) <= threshold:
            liq_type = liq.get('type', 'LEVEL')
            near_structures.append(f"{liq_type} @ {liq_price:.2f}")
    
    # 4. Equal Highs/Lows
    for eqh in analysis.get('eqh', []):
        eqh_price = eqh.get('price', 0)
        if abs(current_price - eqh_price) <= threshold:
            near_structures.append(f"EQH @ {eqh_price:.2f}")
    
    for eql in analysis.get('eql', []):
        eql_price = eql.get('price', 0)
        if abs(current_price - eql_price) <= threshold:
            near_structures.append(f"EQL @ {eql_price:.2f}")
    
    # 5. Key Levels
    advanced = analysis.get('advanced', {})
    key_levels = advanced.get('key_levels', {})
    for level_name in ['PDH', 'PDL', 'DH', 'DL', 'Equilibrium_Price']:
        level_price = key_levels.get(level_name, 0)
        if level_price > 0 and abs(current_price - level_price) <= threshold:
            near_structures.append(f"{level_name} @ {level_price:.2f}")
    
    # 6. Swing Points
    structure_points = advanced.get('structure_points', {})
    swing_high = structure_points.get('nearest_swing_high', 0)
    swing_low = structure_points.get('nearest_swing_low', 0)
    
    if swing_high > 0 and abs(current_price - swing_high) <= threshold:
        near_structures.append(f"Swing High @ {swing_high:.2f}")
    if swing_low > 0 and abs(current_price - swing_low) <= threshold:
        near_structures.append(f"Swing Low @ {swing_low:.2f}")
    
    if near_structures:
        description = ", ".join(near_structures[:3])
        return True, description
    
    return False, "Нет близких SMC структур"

def format_debug_report(status_data):
    """Форматирует отладочный отчет для Telegram"""
    status_emoji = {'market_closed': '💤', 'news_block': '📰', 'no_signals': '🛰️', 'weak_patterns': '📊', 'wrong_zone': '🚫', 'not_near_structure': '🔍', 'cooldown': '⏳', 'llm_called': '🤖', 'signal_sent': '✅', 'wait_decision': '⚖️'}
    status = status_data.get('status', 'unknown')
    emoji = status_emoji.get(status, '❓')
    
    msg = f"<b>{emoji} ASTRA DEBUG REPORT</b>\n"
    msg += f"<code>{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</code>\n"
    msg += "─" * 30 + "\n\n"
    
    status_texts = {'market_closed': 'Рынок закрыт', 'news_block': 'Блокировка по новостям', 'no_signals': 'Нет сигналов', 'weak_patterns': 'Слабые паттерны', 'wrong_zone': 'Неправильная зона', 'not_near_structure': 'Цена не у структур', 'cooldown': 'Активен кулдаун', 'llm_called': 'LLM анализ выполнен', 'signal_sent': 'Сигнал отправлен', 'wait_decision': 'LLM: WAIT'}
    
    msg += f"<b>Статус:</b> {status_texts.get(status, 'Неизвестно')}\n\n"
    if 'price' in status_data: msg += f"<b>💰 Цена:</b> <code>{status_data['price']:.2f}</code>\n"
    if 'trend' in status_data: msg += f"<b>Тренд:</b> {status_data['trend']}\n"
    if 'zone' in status_data: msg += f"<b>Зона:</b> {status_data['zone']}\n"
    if 'near_structures' in status_data: msg += f"<b>Уровни:</b> {status_data['near_structures']}\n"
    
    if 'smc_summary' in status_data:
        smc = status_data['smc_summary']
        msg += f"\n<b>📊 SMC:</b> OB:{smc.get('ob')}, FVG:{smc.get('fvg')}, BOS:{smc.get('bos')}"
    
    return msg

def format_signal_message(ai_response):
    """Превращает JSON от Gemini в красивый текст"""
    try:
        start = ai_response.find('{')
        end = ai_response.rfind('}') + 1
        data = json.loads(ai_response[start:end])
        action = data.get("ACTION", "N/A")
        emoji = "🟢 BUY" if action == "BUY" else "🔴 SELL"
        msg = (f"<b>🚀 ASTRA SIGNAL: GOLD (XAU/USD)</b>\n\n"
               f"Направление: <b>{emoji}</b>\n"
               f"Вход: <code>{data.get('ENTRY')}</code>\n"
               f"Стоп: <code>{data.get('SL')}</code>\n"
               f"Тейк: <code>{data.get('TP')}</code>\n\n"
               f"<b>Анализ:</b>\n<i>{data.get('REASON', 'SMC Confirmation')}</i>")
        return msg
    except:
        return f"<b>📢 НОВЫЙ СИГНАЛ XAUUSD:</b>\n\n{ai_response}"

def send_debug_notification(status_data):
    """Отправляет уведомления только по важным событиям"""
    important = ['signal_sent', 'wait_decision', 'llm_called']
    if status_data.get('status') not in important:
        return 
        
    try:
        user_ids = db_service.get_all_active_users()
        if user_ids:
            message = format_debug_report(status_data)
            telegram_service.broadcast_signal(user_ids, message)
    except Exception as e:
        logger.error(f"❌ Debug Error: {e}")

def run_analysis_cycle():
    """Основная функция анализа (Снайперский режим)"""
    global LAST_SIGNAL_TIME
    # Логируем запуск
    logger.info("📡 [TRIGGER] Цикл анализа запущен внешним вызовом (Cron/API)")
    
    # Сначала проверяем: работает ли рынок вообще?
    if not is_market_active():
        # logger.info("Market is IDLE. Skipping...")
        return
    
    # Подготавливаем данные для отчета
    status_data = {'status': 'unknown', 'price': 0, 'trend': 'N/A', 'zone': 'N/A', 'signals_count': 0, 'found_signals': [], 'smc_summary': {}}

    # Проверяем новости (Блокировка 45 мин до/15 мин после)
    if is_news_blockactive():
        return 
    
    # Получаем живые свечи из OANDA
    data = oanda_service.get_candles(timeframe='M15', limit=100)
    if "error" in data: return
    candles = data.get("candles", [])
    if not smc_detector or not candles: return

    # 1. SMC Технический анализ (Математическое ядро)
    analysis = smc_detector.analyze(candles)
    
    # 2. Сбор паттернов (Полный блок Роберта: BOS, CHOCH, OB, FVG, SWEEP)
    found_signals = []
    for ob in analysis.get('order_blocks', []):
        if 'BULL' in ob.get('type', '') or 'BEAR' in ob.get('type', ''): found_signals.append('OB_RETEST')
    for fvg in analysis.get('fvg', []):
        if 'BULL' in fvg.get('type', '') or 'BEAR' in fvg.get('type', ''): found_signals.append('FVG_FILL')
    if analysis.get('choch', []): found_signals.append('CHOCH')
    if analysis.get('bos', []): found_signals.append('BOS')
    if analysis.get('eqh') or analysis.get('eql'): found_signals.append('SWEEP')
    
    # Определяем текущий контекст
    trend = analysis.get('trend', 'NEUTRAL')
    current_zone = analysis.get('advanced', {}).get('key_levels', {}).get('Current_Zone', 'N/A')
    current_price = candles[-1].get('close', 0)
    
    # Обновляем отчет для логов
    status_data.update({
        'price': current_price, 'trend': trend, 'zone': current_zone,
        'signals_count': len(found_signals), 'found_signals': found_signals,
        'smc_summary': {
            'ob': len(analysis.get('order_blocks', [])), 
            'fvg': len(analysis.get('fvg', [])), 
            'bos': len(analysis.get('bos', [])), 
            'choch': len(analysis.get('choch', []))
        }
    })

    # --- ФИЛЬТР 1: СНАЙПЕРСКАЯ ТОЧНОСТЬ (0.5% зона) ---
    # САМЫЙ СТРОГИЙ! Отсечет 90% пустых ситуаций
    is_near, near_description = is_price_near_smc_structure(current_price, analysis, threshold_percent=0.5)
    if not is_near:
        logger.info(f"🔍 Цена {current_price} вне зоны интереса. Пропуск.")
        return

    # --- ФИЛЬТР 2: ТЕХНИЧЕСКИЙ (SMC Сила) ---
    is_worth_it = any(setup in found_signals for setup in STRONG_SETUPS)
    if not found_signals or not is_worth_it:
        return

    # --- ФИЛЬТР 3: ЗОНА (Premium/Discount) ---
    if (trend == "UPTREND" and current_zone == "PREMIUM") or (trend == "DOWNTREND" and current_zone == "DISCOUNT"):
        return

    # --- ФИЛЬТР 4: КУЛДАУН ---
    if not check_smart_cooldown():
        return

    # --- ШАГ 5: ВЫЗОВ GEMINI ---
    logger.info(f"🎯 Условия идеальны! Запрашиваем вердикт Gemini...")
    ai_response = llm_service.get_signal_verdict(analysis)

    is_confirmed = '"ACTION": "BUY"' in ai_response.upper() or '"ACTION": "SELL"' in ai_response.upper()
    is_wait = "WAIT" in ai_response.upper() or '"ACTION": "WAIT"' in ai_response.upper()
    
    if is_confirmed and not is_wait:
        logger.info("🔥 КОНСЕНСУС ДОСТИГНУТ!")
        db_service.update_last_signal_time() # Блок 2ч
        
        # Шлем ВСЕМ активным пользователям (независимо от photo_url)
        # Теперь сигналы получают все кто авторизовался через виджет ИЛИ через бота
        user_ids = db_service.get_all_active_users()
        if user_ids:
            formatted_msg = format_signal_message(ai_response)
            telegram_service.broadcast_signal(user_ids, formatted_msg)
            
            db_service.save_signal({
                'symbol': 'XAU_USD', 'signal_type': 'BUY' if 'BUY' in ai_response.upper() else 'SELL',
                'entry_price': current_price, 'llm_reason': ai_response, 'near_structures': near_description
            })
            send_debug_notification({'status': 'signal_sent', 'price': current_price, 'reason': 'Signal broadcasted'})
    else:
        logger.info("⚖️ ИИ выдал WAIT. Блокировка на 1 час.")
        db_service.update_last_wait_time() # Блок 1ч
        db_service.save_signal({'symbol': 'XAU_USD', 'signal_type': 'WAIT', 'current_price': current_price, 'llm_reason': ai_response})
        send_debug_notification({'status': 'wait_decision', 'price': current_price, 'reason': ai_response})

def start_watcher():
    """
    Инициализация наблюдателя. 
    Мы убрали отсюда запуск run_analysis_cycle(), 
    чтобы бот не спамил при каждом просыпании сервера.
    Теперь анализ запускается ТОЛЬКО через эндпоинт /api/cron/watcher
    """
    logger.info("🛰 Наблюдатель Astra Watcher инициализирован и ожидает Cron-команды.")

if __name__ == "__main__":
    # Если ты запускаешь файл вручную через 'python watcher.py', 
    # тогда он сделает один анализ для теста.
    logger.info("🧪 Ручной запуск анализа для теста...")
    run_analysis_cycle()