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
    """
    Форматирует детальный отладочный отчет для Telegram
    Показывает ПОЛНУЮ картину каждого цикла анализа
    """
    status_emoji = {
        'market_closed': '💤', 
        'news_block': '📰', 
        'oanda_error': '🔌',
        'no_smc': '⚙️', 
        'technical_scan_complete': '📊',
        'not_near_structure': '🔍', 
        'weak_patterns': '📉',
        'cooldown': '⏳', 
        'signal_sent': '✅', 
        'wait_decision': '⚖️'
    }
    
    status_texts = {
        'market_closed': 'Рынок закрыт', 
        'news_block': 'Блокировка по новостям',
        'oanda_error': 'Ошибка OANDA',
        'no_smc': 'SMC детектор недоступен',
        'technical_scan_complete': 'Технический анализ завершен',
        'not_near_structure': 'SKIP - Цена далеко от структур (>0.5%)',
        'weak_patterns': 'SKIP - Нет сильных паттернов',
        'cooldown': 'SKIP - Активен кулдаун',
        'signal_sent': '🎯 ТОРГОВЫЙ СИГНАЛ!',
        'wait_decision': 'LLM рекомендует WAIT'
    }
    
    status = status_data.get('status', 'unknown')
    emoji = status_emoji.get(status, '❓')
    
    # Заголовок
    now_utc = datetime.now(timezone.utc)
    msg = f"<b>{emoji} ASTRA WATCHER REPORT</b>\n"
    msg += f"<code>UTC: {now_utc.strftime('%Y-%m-%d %H:%M:%S')}</code>\n"
    msg += "━" * 32 + "\n\n"
    
    # Решение
    decision = status_texts.get(status, 'Неизвестно')
    msg += f"<b>📋 Решение:</b> {decision}\n\n"
    
    # Рыночные данные (если есть)
    if status_data.get('price', 0) > 0:
        msg += "<b>💹 Рыночные данные:</b>\n"
        msg += f"├ Цена: <code>${status_data['price']:.2f}</code>\n"
        
        if 'trend' in status_data:
            trend_emoji = "📈" if "UP" in status_data['trend'] else "📉" if "DOWN" in status_data['trend'] else "↔️"
            msg += f"├ Тренд: {trend_emoji} {status_data['trend']}\n"
        
        if 'zone' in status_data:
            zone = status_data['zone']
            zone_emoji = "🔴" if zone == "PREMIUM" else "🟢" if zone == "DISCOUNT" else "⚪"
            msg += f"└ Зона: {zone_emoji} {zone}\n\n"
    
    # SMC паттерны (если есть)
    if 'smc_summary' in status_data and any(status_data['smc_summary'].values()):
        smc = status_data['smc_summary']
        msg += "<b>📊 Найденные паттерны SMC:</b>\n"
        msg += f"├ Order Blocks: {smc.get('ob', 0)}\n"
        msg += f"├ Fair Value Gaps: {smc.get('fvg', 0)}\n"
        msg += f"├ Break of Structure: {smc.get('bos', 0)}\n"
        msg += f"└ Change of Character: {smc.get('choch', 0)}\n\n"
        
        if 'found_signals' in status_data and status_data['found_signals']:
            signals_list = ", ".join(status_data['found_signals'][:5])
            msg += f"<b>🎯 Активные сигналы:</b> {signals_list}\n\n"
    
    # Близость к структурам
    if 'near_structures' in status_data:
        msg += f"<b>🎯 Уровни рядом:</b>\n{status_data['near_structures']}\n\n"
    
    # Причина остановки (детали)
    if 'reason' in status_data:
        msg += f"<b>💡 Детали:</b>\n<i>{status_data['reason']}</i>\n\n"
    
    # Вердикт LLM (если был вызван)
    if 'llm_verdict' in status_data:
        msg += f"<b>🤖 Gemini вердикт:</b>\n<code>{status_data['llm_verdict'][:200]}...</code>\n\n"
    
    # Футер с временем следующей проверки
    msg += "━" * 32 + "\n"
    msg += "<i>⏱ Следующая проверка через 15 минут</i>"
    
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
    """
    Отправляет отладочный отчет ВСЕМ активным пользователям
    Каждые 15 минут - независимо от результата анализа
    """
    try:
        user_ids = db_service.get_all_active_users()
        if user_ids:
            message = format_debug_report(status_data)
            telegram_service.broadcast_signal(user_ids, message)
            logger.info(f"📤 Debug отчет отправлен ({len(user_ids)} пользователей)")
    except Exception as e:
        logger.error(f"❌ Debug Error: {e}")

def run_analysis_cycle():
    """
    Основная функция анализа - Рефакторинг "Сначала детектор, затем AI"
    
    СТРОГИЙ КОНВЕЙЕР:
    1. Техническая фаза (только детектор):
       - Проверка рынка, новостей, получение свечей OANDA
       - Выполнение SMC анализа
       - ОБЯЗАТЕЛЬНЫЙ технический отчет (каждые 15 минут)
    
    2. Фильтры Gatekeeper (без AI):
       - Фильтр 1: Близость к структурам (0.5%)
       - Фильтр 2: Наличие сильных паттернов (BOS, CHOCH, OB_RETEST)
       - Фильтр 3: Кулдаун (2ч для сигналов, 1ч для WAIT)
       - При провале любого фильтра: отчет SKIP + выход БЕЗ вызова LLM
    
    3. Этап LLM анализа:
       - Только если ВСЕ фильтры пройдены
       - BUY/SELL: update_last_signal_time() + торговый сигнал
       - WAIT: update_last_wait_time() + отладочный отчет
    """
    global LAST_SIGNAL_TIME
    logger.info("📡 [TRIGGER] Цикл анализа запущен внешним вызовом (Cron/API)")
    
    # ========================================================================
    # ФАЗА 1: ТЕХНИЧЕСКАЯ ПОДГОТОВКА (только детектор, без AI)
    # ========================================================================
    
    # Шаг 1.1: Проверка активности рынка
    if not is_market_active():
        logger.info("💤 Рынок закрыт. Пропуск анализа.")
        send_debug_notification({
            'status': 'market_closed',
            'reason': 'Рынок золота работает: Воскресенье 23:00 UTC - Пятница 22:00 UTC'
        })
        return
    
    # Шаг 1.2: Проверка блокировки по новостям
    if is_news_blockactive():
        logger.info("📰 Блокировка по новостям. Пропуск анализа.")
        send_debug_notification({
            'status': 'news_block',
            'reason': 'Обнаружены важные новости USD в ближайшие 45 минут или прошли менее 15 минут назад'
        })
        return
    
    # Шаг 1.3: Получение живых свечей из OANDA
    data = oanda_service.get_candles(timeframe='M15', limit=100)
    if "error" in data:
        logger.error("🔌 Ошибка получения данных от OANDA")
        send_debug_notification({
            'status': 'oanda_error',
            'reason': f'Ошибка OANDA API: {data.get("error", "Unknown error")}'
        })
        return
    
    candles = data.get("candles", [])
    if not smc_detector:
        logger.error("⚙️ SMC детектор недоступен")
        send_debug_notification({
            'status': 'no_smc',
            'reason': 'SMC детектор не инициализирован. Проверьте сервис smc_detector.'
        })
        return
    
    if not candles:
        logger.error("📊 Нет данных свечей от OANDA")
        send_debug_notification({
            'status': 'oanda_error',
            'reason': 'OANDA вернул пустой массив свечей'
        })
        return
    
    # Шаг 1.4: SMC Технический анализ (математическое ядро)
    logger.info("🔬 Выполняем SMC анализ...")
    analysis = smc_detector.analyze(candles)
    
    # Шаг 1.5: Сбор технических паттернов SMC
    found_signals = []
    for ob in analysis.get('order_blocks', []):
        if 'BULL' in ob.get('type', '') or 'BEAR' in ob.get('type', ''): 
            found_signals.append('OB_RETEST')
    for fvg in analysis.get('fvg', []):
        if 'BULL' in fvg.get('type', '') or 'BEAR' in fvg.get('type', ''): 
            found_signals.append('FVG_FILL')
    if analysis.get('choch', []): 
        found_signals.append('CHOCH')
    if analysis.get('bos', []): 
        found_signals.append('BOS')
    if analysis.get('eqh') or analysis.get('eql'): 
        found_signals.append('SWEEP')
    
    # Шаг 1.6: Извлечение текущего контекста рынка
    trend = analysis.get('trend', 'NEUTRAL')
    current_zone = analysis.get('advanced', {}).get('key_levels', {}).get('Current_Zone', 'N/A')
    current_price = candles[-1].get('close', 0)
    
    # Шаг 1.7: Проверка близости к SMC структурам
    is_near, near_description = is_price_near_smc_structure(current_price, analysis, threshold_percent=0.5)
    
    # Подготовка структуры для технического отчета
    status_data = {
        'price': current_price,
        'trend': trend,
        'zone': current_zone,
        'signals_count': len(found_signals),
        'found_signals': found_signals,
        'smc_summary': {
            'ob': len(analysis.get('order_blocks', [])),
            'fvg': len(analysis.get('fvg', [])),
            'bos': len(analysis.get('bos', [])),
            'choch': len(analysis.get('choch', []))
        },
        'near_structures': near_description
    }
    
    # ========================================================================
    # 🔔 ОБЯЗАТЕЛЬНЫЙ ТЕХНИЧЕСКИЙ ОТЧЕТ (каждые 15 минут)
    # Отправляется ПЕРЕД применением фильтров, содержит только данные детектора
    # ========================================================================
    logger.info("📊 Отправка технического отчета (данные детектора)...")
    status_data['status'] = 'technical_scan_complete'
    status_data['reason'] = 'Технический анализ завершен. Данные получены только из SMC детектора.'
    send_debug_notification(status_data)
    
    # ========================================================================
    # ФАЗА 2: ФИЛЬТРЫ GATEKEEPER (экономим токены AI)
    # ========================================================================
    
    # --- ФИЛЬТР 1: БЛИЗОСТЬ К СТРУКТУРАМ (0.5%) ---
    if not is_near:
        logger.info(f"🔍 SKIP: Цена {current_price:.2f} вне зоны интереса (>{near_description}).")
        status_data['status'] = 'not_near_structure'
        status_data['reason'] = f'SKIP - Цена ${current_price:.2f} не находится в пределах 0.5% от ключевых SMC структур.'
        send_debug_notification(status_data)
        return
    
    # --- ФИЛЬТР 2: НАЛИЧИЕ СИЛЬНЫХ ПАТТЕРНОВ ---
    is_worth_it = any(setup in found_signals for setup in STRONG_SETUPS)
    if not found_signals or not is_worth_it:
        logger.info("📊 SKIP: Нет сильных паттернов SMC.")
        status_data['status'] = 'weak_patterns'
        strong_list = ", ".join(STRONG_SETUPS)
        found_list = ", ".join(found_signals) if found_signals else "Нет"
        status_data['reason'] = f'SKIP - Требуются сильные паттерны ({strong_list}). Найдено: {found_list}'
        send_debug_notification(status_data)
        return
    
    # --- ФИЛЬТР 3: КУЛДАУН (2ч для сигналов, 1ч для WAIT) ---
    if not check_smart_cooldown():
        logger.info("⏳ SKIP: Кулдаун активен.")
        status_data['status'] = 'cooldown'
        status_data['reason'] = 'SKIP - Кулдаун активен: либо недавний торговый сигнал (2ч), либо вердикт WAIT (1ч).'
        send_debug_notification(status_data)
        return
    
    # ========================================================================
    # ФАЗА 3: АНАЛИЗ LLM (только если ВСЕ фильтры пройдены!)
    # ========================================================================
    
    logger.info("=" * 60)
    logger.info("🎯 ВСЕ ФИЛЬТРЫ GATEKEEPER ПРОЙДЕНЫ! Запрашиваем вердикт Gemini...")
    logger.info(f"   💰 Цена: ${current_price:.2f}")
    logger.info(f"   📈 Тренд: {trend}")
    logger.info(f"   🎯 Зона: {current_zone}")
    logger.info(f"   🔍 Уровни: {near_description}")
    logger.info(f"   📊 Паттерны: {', '.join(found_signals)}")
    logger.info("=" * 60)
    
    # Вызов Gemini LLM для финального вердикта
    ai_response = llm_service.get_signal_verdict(analysis)
    
    # Анализ ответа LLM
    is_confirmed = '"ACTION": "BUY"' in ai_response.upper() or '"ACTION": "SELL"' in ai_response.upper()
    is_wait = "WAIT" in ai_response.upper() or '"ACTION": "WAIT"' in ai_response.upper()
    
    # ========================================================================
    # СЛУЧАЙ A: ТОРГОВЫЙ СИГНАЛ (BUY/SELL)
    # ========================================================================
    if is_confirmed and not is_wait:
        logger.info("🔥 КОНСЕНСУС ДОСТИГНУТ! ОТПРАВКА ТОРГОВОГО СИГНАЛА!")
        
        # Обновляем метку времени последнего сигнала (блокировка на 2 часа)
        db_service.update_last_signal_time()
        
        # Получаем всех активных пользователей и отправляем сигнал
        user_ids = db_service.get_all_active_users()
        if user_ids:
            formatted_msg = format_signal_message(ai_response)
            telegram_service.broadcast_signal(user_ids, formatted_msg)
            
            # Сохраняем сигнал в БД
            db_service.save_signal({
                'symbol': 'XAU_USD',
                'signal_type': 'BUY' if 'BUY' in ai_response.upper() else 'SELL',
                'entry_price': current_price,
                'llm_reason': ai_response,
                'near_structures': near_description
            })
            
            # Отправляем детальный отчет с вердиктом LLM
            status_data['status'] = 'signal_sent'
            status_data['reason'] = 'Gemini подтвердил торговый сетап. Сигнал отправлен всем активным пользователям.'
            status_data['llm_verdict'] = ai_response
            send_debug_notification(status_data)
    
    # ========================================================================
    # СЛУЧАЙ B: ВЕРДИКТ WAIT (нет четкого сетапа)
    # ========================================================================
    else:
        logger.info("⚖️ Gemini рекомендует WAIT. Блокировка на 1 час.")
        
        # Обновляем метку времени последнего WAIT (блокировка на 1 час)
        db_service.update_last_wait_time()
        
        # Сохраняем вердикт WAIT в БД
        db_service.save_signal({
            'symbol': 'XAU_USD',
            'signal_type': 'WAIT',
            'current_price': current_price,
            'llm_reason': ai_response
        })
        
        # Отправляем отчет с вердиктом WAIT и обоснованием LLM
        status_data['status'] = 'wait_decision'
        status_data['reason'] = 'Gemini не видит четкого сетапа. Рекомендуется подождать лучших условий.'
        status_data['llm_verdict'] = ai_response
        send_debug_notification(status_data)

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