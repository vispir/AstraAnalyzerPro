import os
from datetime import datetime, timedelta, timezone # Добавили timezone
import logging
import json
from services.db_service import db_service
from services.telegram_service import telegram_service 

# Переменная для отслеживания времени последнего сигнала (теперь дублируется в облаке)
LAST_SIGNAL_TIME = None 

# константы
SIGNAL_COOLDOWN_HOURS = 1  # Кулдаун только для BUY/SELL сигналов (WAIT не учитывается)
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

def check_cooldown():
    """
    Проверка кулдауна через облачную базу Supabase
    ВАЖНО: Проверяет только BUY/SELL сигналы, игнорируя WAIT
    """
    try:
        # Тянем время последнего ТОРГОВОГО сигнала (BUY/SELL) из облака
        last_time = db_service.get_last_trade_signal_time()
        # Сравниваем в формате UTC
        now = datetime.now(timezone.utc)
        
        time_passed = now - last_time
        if time_passed.total_seconds() < (SIGNAL_COOLDOWN_HOURS * 3600):
            # Если время еще не прошло
            remaining = timedelta(seconds=(SIGNAL_COOLDOWN_HOURS * 3600) - time_passed.total_seconds())
            logger.info(f"⏳ Кулдаун активен (только BUY/SELL). Ждем еще {str(remaining).split('.')[0]}")
            return False
        
        return True
    except Exception as e:
        logger.error(f"Ошибка проверки кулдауна через БД: {e}")
        return True # В случае сбоя БД разрешаем анализ, чтобы не "ослепнуть"

def is_price_near_smc_structure(current_price, analysis, threshold_percent=0.5):
    """
    Проверяет, находится ли текущая цена близко к значимым SMC структурам.
    
    Args:
        current_price: текущая цена
        analysis: результат SMC анализа
        threshold_percent: порог в процентах (по умолчанию 0.5%)
    
    Returns:
        tuple: (bool, str) - (находится ли близко, описание ближайшей структуры)
    """
    threshold = current_price * (threshold_percent / 100)
    
    near_structures = []
    
    # 1. Order Blocks
    for ob in analysis.get('order_blocks', []):
        ob_top = ob.get('top', 0)
        ob_bottom = ob.get('bottom', 0)
        
        # Проверяем, находится ли цена внутри блока или рядом
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
    
    # 3. Liquidity Levels (поддержка/сопротивление)
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
    
    # 5. Key Levels (PDH, PDL, Equilibrium)
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
        description = ", ".join(near_structures[:3])  # Берем только первые 3
        return True, description
    
    return False, "Нет близких SMC структур"

def format_debug_report(status_data):
    """
    Форматирует отладочный отчет для Telegram (отправляется каждые 15 минут)
    
    Args:
        status_data: Dict с информацией о цикле анализа
    """
    from datetime import datetime
    
    # Эмодзи для статуса
    status_emoji = {
        'market_closed': '💤',
        'news_block': '📰',
        'no_signals': '🛰️',
        'weak_patterns': '📊',
        'wrong_zone': '🚫',
        'not_near_structure': '🔍',
        'cooldown': '⏳',
        'llm_called': '🤖',
        'signal_sent': '✅',
        'wait_decision': '⚖️'
    }
    
    status = status_data.get('status', 'unknown')
    emoji = status_emoji.get(status, '❓')
    
    # Заголовок
    msg = f"<b>{emoji} ASTRA DEBUG REPORT</b>\n"
    msg += f"<code>{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</code>\n"
    msg += "─" * 30 + "\n\n"
    
    # Статус цикла
    status_texts = {
        'market_closed': '💤 Рынок закрыт',
        'news_block': '📰 Блокировка по новостям',
        'no_signals': '🛰️ Нет сигналов на рынке',
        'weak_patterns': '📊 Нет сильных паттернов',
        'wrong_zone': '🚫 Цена в неправильной зоне',
        'not_near_structure': '🔍 Цена не у структур',
        'cooldown': '⏳ Активен кулдаун',
        'llm_called': '🤖 LLM анализ выполнен',
        'signal_sent': '✅ Сигнал отправлен',
        'wait_decision': '⚖️ LLM: WAIT'
    }
    
    msg += f"<b>Статус:</b> {status_texts.get(status, 'Неизвестно')}\n\n"
    
    # Детали если есть
    if 'price' in status_data:
        msg += f"<b>💰 Цена:</b> <code>{status_data['price']:.2f}</code>\n"
    
    if 'trend' in status_data:
        trend_emoji = {'UPTREND': '📈', 'DOWNTREND': '📉', 'NEUTRAL': '➡️'}
        msg += f"<b>Тренд:</b> {trend_emoji.get(status_data['trend'], '❓')} {status_data['trend']}\n"
    
    if 'zone' in status_data:
        zone_emoji = {'PREMIUM': '🔴', 'DISCOUNT': '🟢', 'EQUILIBRIUM': '🟡'}
        msg += f"<b>Зона:</b> {zone_emoji.get(status_data['zone'], '⚪')} {status_data['zone']}\n"
    
    if 'signals_count' in status_data:
        msg += f"<b>Паттернов:</b> {status_data['signals_count']}\n"
    
    if 'found_signals' in status_data and status_data['found_signals']:
        signals_str = ', '.join(status_data['found_signals'][:5])
        msg += f"<b>Найдены:</b> <code>{signals_str}</code>\n"
    
    if 'near_structures' in status_data:
        msg += f"<b>Близкие структуры:</b> {status_data['near_structures']}\n"
    
    if 'reason' in status_data:
        msg += f"\n<i>{status_data['reason']}</i>\n"
    
    # Дополнительная информация о SMC структурах
    if 'smc_summary' in status_data:
        smc = status_data['smc_summary']
        msg += "\n<b>📊 SMC Структуры:</b>\n"
        msg += f"• Order Blocks: {smc.get('ob', 0)}\n"
        msg += f"• FVG: {smc.get('fvg', 0)}\n"
        msg += f"• Liquidity: {smc.get('liq', 0)}\n"
        msg += f"• BOS/CHOCH: {smc.get('bos', 0)}/{smc.get('choch', 0)}\n"
    
    return msg

def format_signal_message(ai_response):
    """Превращает JSON от Gemini в красивый текст для Телеграма"""
    try:
        # Пытаемся вытащить JSON из ответа Gemini
        start = ai_response.find('{')
        end = ai_response.rfind('}') + 1
        data = json.loads(ai_response[start:end])
        
        action = data.get("ACTION", "N/A")
        emoji = "🟢 BUY" if action == "BUY" else "🔴 SELL"
        
        msg = (
            f"<b>🚀 ASTRA SIGNAL: GOLD (XAU/USD)</b>\n\n"
            f"Направление: <b>{emoji}</b>\n"
            f"Вход: <code>{data.get('ENTRY')}</code>\n"
            f"Стоп: <code>{data.get('SL')}</code>\n"
            f"Тейк: <code>{data.get('TP')}</code>\n\n"
            f"<b>Анализ:</b>\n<i>{data.get('REASON', 'По техническому анализу SMC')}</i>"
        )
        return msg
    except:
        # Если JSON не парсится, шлем как есть
        return f"<b>📢 НОВЫЙ СИГНАЛ XAUUSD:</b>\n\n{ai_response}"

def send_debug_notification(status_data):
    """
    Отправляет отладочное уведомление всем пользователям
    """
    try:
        user_ids = db_service.get_all_active_users()
        if user_ids:
            message = format_debug_report(status_data)
            success_count = telegram_service.broadcast_signal(user_ids, message)
            logger.info(f"📤 Debug уведомление отправлено {success_count} пользователям")
        else:
            logger.warning("⚠️ Нет активных пользователей для debug уведомлений")
    except Exception as e:
        logger.error(f"❌ Ошибка отправки debug уведомления: {e}")

def run_analysis_cycle():
    """Основная функция, которую будет дергать APScheduler"""
    global LAST_SIGNAL_TIME
    logger.info("📡 Запуск цикла анализа")
    
    # Инициализируем статус-данные для отчета
    status_data = {
        'status': 'unknown',
        'price': 0,
        'trend': 'N/A',
        'zone': 'N/A',
        'signals_count': 0,
        'found_signals': [],
        'smc_summary': {}
    }
    
    # Проверка активности рынка
    if not is_market_active():
        logger.info("Market is IDLE. Skipping...")
        status_data['status'] = 'market_closed'
        status_data['reason'] = 'Рынок закрыт (выходные или технический перерыв)'
        send_debug_notification(status_data)
        return

    # Проверка новостной блокировки
    if is_news_blockactive():
        logger.info("News block active. Skipping...")
        status_data['status'] = 'news_block'
        status_data['reason'] = 'Активна блокировка по высоко-импактным новостям USD'
        send_debug_notification(status_data)
        return 
    
    # Получаем данные рынка
    data = oanda_service.get_candles(timeframe='M15', limit=100)
    if "error" in data:
        logger.error(f"Ошибка получения данных: {data.get('error')}")
        return
    
    candles = data.get("candles", [])
    if not smc_detector:
        logger.error("SMC детектор недоступен")
        return

    # 1. Сначала делаем технический анализ
    analysis = smc_detector.analyze(candles)
    
    # 2. Собираем все найденные SMC паттерны (ИСПРАВЛЕНИЕ БАГА)
    found_signals = []
    
    # Order Blocks (BULL_OB, BEAR_OB)
    for ob in analysis.get('order_blocks', []):
        ob_type = ob.get('type', '')
        if 'BULL' in ob_type or 'BEAR' in ob_type:
            found_signals.append('OB_RETEST')
    
    # Fair Value Gaps (BULL_FVG, BEAR_FVG)
    for fvg in analysis.get('fvg', []):
        fvg_type = fvg.get('type', '')
        if 'BULL' in fvg_type or 'BEAR' in fvg_type:
            found_signals.append('FVG_FILL')
    
    # Market Structure (BOS/CHOCH)
    if analysis.get('choch', []):
        found_signals.append('CHOCH')
    if analysis.get('bos', []):
        found_signals.append('BOS')
    
    # Liquidity Sweeps
    for liq in analysis.get('liquidity', []):
        liq_type = liq.get('type', '')
        if 'SWEEP' in liq_type.upper():
            found_signals.append('SWEEP')
    
    # Equal Highs/Lows (потенциальные sweep зоны)
    if analysis.get('eqh'):
        found_signals.append('SWEEP')
    if analysis.get('eql'):
        found_signals.append('SWEEP')
    
    # 3. Получаем параметры структуры
    trend = analysis.get('trend', 'NEUTRAL')
    current_zone = analysis.get('advanced', {}).get('key_levels', {}).get('Current_Zone', 'N/A')
    signals_count = len(found_signals)
    current_price = candles[-1].get('close', 0) if candles else 0
    
    # Обновляем статус данные
    status_data.update({
        'price': current_price,
        'trend': trend,
        'zone': current_zone,
        'signals_count': signals_count,
        'found_signals': found_signals,
        'smc_summary': {
            'ob': len(analysis.get('order_blocks', [])),
            'fvg': len(analysis.get('fvg', [])),
            'liq': len(analysis.get('liquidity', [])),
            'bos': len(analysis.get('bos', [])),
            'choch': len(analysis.get('choch', []))
        }
    })

    # --- ШАГ 1: ТЕХНИЧЕСКИЙ ФИЛЬТР ПО ПАТТЕРНАМ ---
    is_worth_it = any(setup in found_signals for setup in STRONG_SETUPS)
    
    if signals_count == 0:
        logger.info(f"🛰 Сигналов нет на рынке")
        status_data['status'] = 'no_signals'
        status_data['reason'] = 'На рынке не обнаружено SMC паттернов'
        send_debug_notification(status_data)
        return
    
    if not is_worth_it:
        logger.info(f"🛰 Сигналов: {signals_count}. Найдены: {found_signals}. Сильных сетапов нет.")
        status_data['status'] = 'weak_patterns'
        status_data['reason'] = f'Найдено {signals_count} паттернов, но нет сильных сетапов из списка: {", ".join(STRONG_SETUPS)}'
        send_debug_notification(status_data)
        return

    # --- ШАГ 2: ПРЕД-ФИЛЬТР ВЫГОДНОЙ ЦЕНЫ (Save LLM Tokens) ---
    # Мы не будем даже беспокоить Gemini, если математика зон против нас
    if trend == "UPTREND" and current_zone == "PREMIUM":
        logger.info(f"🛑 Пропуск: Тренд UP, но цена в PREMIUM. Слишком дорого для покупки.")
        status_data['status'] = 'wrong_zone'
        status_data['reason'] = '↗️ Тренд UPTREND, но цена в зоне PREMIUM (дорого для покупки)'
        send_debug_notification(status_data)
        return
    
    if trend == "DOWNTREND" and current_zone == "DISCOUNT":
        logger.info(f"🛑 Пропуск: Тренд DOWN, но цена в DISCOUNT. Слишком дешево для продажи.")
        status_data['status'] = 'wrong_zone'
        status_data['reason'] = '↘️ Тренд DOWNTREND, но цена в зоне DISCOUNT (дешево для продажи)'
        send_debug_notification(status_data)
        return

    # --- ШАГ 3: ПРОВЕРКА БЛИЗОСТИ ЦЕНЫ К SMC СТРУКТУРАМ ---
    is_near, near_description = is_price_near_smc_structure(current_price, analysis, threshold_percent=0.5)
    
    if not is_near:
        logger.info(f"🔍 Цена {current_price:.2f} не находится близко к SMC структурам. Пропуск LLM анализа.")
        status_data['status'] = 'not_near_structure'
        status_data['reason'] = 'Цена находится далеко от ключевых SMC структур (порог: 0.5%)'
        send_debug_notification(status_data)
        return
    
    logger.info(f"✅ Цена близко к структурам: {near_description}")
    status_data['near_structures'] = near_description

    # --- ШАГ 4: ФИЛЬТР КУЛДАУНА ---
    if not check_cooldown():
        status_data['status'] = 'cooldown'
        status_data['reason'] = f'Активен кулдаун {SIGNAL_COOLDOWN_HOURS} часа после последнего сигнала'
        send_debug_notification(status_data)
        return

    # --- ШАГ 5: ЕСЛИ ВСЁ ОК — ЗОВЕМ GEMINI ---
    logger.info(f"🎯 Все проверки пройдены! Тренд: {trend}, Зона: {current_zone}, Рядом: {near_description}")
    logger.info(f"🤖 Запрашиваем вердикт Gemini...")
    ai_response = llm_service.get_signal_verdict(analysis)

    # Проверка консенсуса ИИ
    is_confirmed = '"ACTION": "BUY"' in ai_response.upper() or '"ACTION": "SELL"' in ai_response.upper()
    is_wait = "WAIT" in ai_response.upper() or '"ACTION": "WAIT"' in ai_response.upper()
    
    # Парсим ответ LLM для сохранения в БД
    signal_json = {}
    try:
        start = ai_response.find('{')
        end = ai_response.rfind('}') + 1
        if start >= 0 and end > start:
            signal_json = json.loads(ai_response[start:end])
    except Exception as e:
        logger.warning(f"⚠️ Не удалось распарсить JSON из LLM ответа: {e}")
    
    if is_confirmed and not is_wait:
        logger.info("🔥 КОНСЕНСУС ДОСТИГНУТ! BUY/SELL сигнал")
        
        # 1. СОХРАНЯЕМ ТОРГОВЫЙ СИГНАЛ (BUY/SELL) В БД
        # Получаем полную причину без обрезания
        llm_reason_full = signal_json.get('REASON') or signal_json.get('executive_summary') or ai_response
        
        signal_data = {
            'symbol': 'XAU_USD',
            'signal_type': signal_json.get('ACTION', 'N/A').upper(),
            'entry_price': float(signal_json.get('ENTRY', 0)) if signal_json.get('ENTRY') else None,
            'stop_loss': float(signal_json.get('SL', 0)) if signal_json.get('SL') else None,
            'take_profit': float(signal_json.get('TP', 0)) if signal_json.get('TP') else None,
            'trend': trend,
            'zone': current_zone,
            'current_price': current_price,
            'patterns': found_signals,
            'near_structures': near_description,
            'smc_summary': status_data.get('smc_summary', {}),
            'llm_reason': llm_reason_full,  # Сохраняем полное описание без обрезания
            'llm_confidence': signal_json.get('CONFIDENCE'),
            'llm_full_response': ai_response
        }
        
        signal_id = db_service.save_signal(signal_data)
        
        # 2. ОБНОВЛЯЕМ ВРЕМЯ ПОСЛЕДНЕГО СИГНАЛА В SUPABASE (для старой логики, если используется)
        db_service.update_last_signal_time()
        
        # 3. Получаем всех активных юзеров из Supabase
        user_ids = db_service.get_all_active_users()
        
        if user_ids:
            logger.info(f"📤 Рассылка сигнала {len(user_ids)} пользователям...")
            formatted_msg = format_signal_message(ai_response)
            
            # 4. Рассылаем через телеграм сервис
            success_count = telegram_service.broadcast_signal(user_ids, formatted_msg)
            logger.info(f"✅ Успешно доставлено: {success_count}")
            
            # Отправляем debug отчет о успешном сигнале
            status_data['status'] = 'signal_sent'
            status_data['reason'] = f'🎯 LLM подтвердил сигнал (BUY/SELL) - ID: {signal_id}'
            send_debug_notification(status_data)
        else:
            logger.warning("⚠️ Нет активных пользователей в базе для рассылки.")
    else:
        logger.info("⚖️ ИИ отклонил вход или выдал WAIT.")
        
        # СОХРАНЯЕМ WAIT СИГНАЛ В БД (тоже важно для аналитики)
        # Получаем полную причину без обрезания
        llm_reason_full = signal_json.get('REASON') or signal_json.get('executive_summary') or ai_response
        
        wait_signal_data = {
            'symbol': 'XAU_USD',
            'signal_type': 'WAIT',
            'entry_price': None,
            'stop_loss': None,
            'take_profit': None,
            'trend': trend,
            'zone': current_zone,
            'current_price': current_price,
            'patterns': found_signals,
            'near_structures': near_description,
            'smc_summary': status_data.get('smc_summary', {}),
            'llm_reason': llm_reason_full,  # Сохраняем полное описание без обрезания
            'llm_confidence': signal_json.get('CONFIDENCE'),
            'llm_full_response': ai_response
        }
        
        wait_signal_id = db_service.save_signal(wait_signal_data)
        
        # Отправляем debug отчет о WAIT решении с полным текстом
        status_data['status'] = 'wait_decision'
        status_data['reason'] = f'⚖️ LLM рекомендует WAIT - ID: {wait_signal_id}\n\n{llm_reason_full}'  # Убрали обрезание
        send_debug_notification(status_data)

def start_watcher():
    run_analysis_cycle()

if __name__ == "__main__":
    start_watcher()