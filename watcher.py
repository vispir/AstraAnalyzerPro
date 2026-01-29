"""
Astra Watcher v2.1 - Обновлённый наблюдатель
Совместим с smc_detector v2.1 (пробой по high/low, swing pivots для зон)

ИЗМЕНЕНИЯ:
1. Поддержка break_by_wick для различения пробоев телом vs фитилём
2. Разделение Internal vs Swing структур (swing = направление, internal = вход)
3. Использование position_in_range_pct для точного определения зоны
4. Улучшенная логика фильтров с учётом типа пробоя
5. Добавлен swing_bias для определения основного направления
"""
import os
from datetime import datetime, timedelta, timezone
import logging
import json
from services.db_service import db_service
from services.telegram_service import telegram_service 

# Переменная для отслеживания времени последнего сигнала
LAST_SIGNAL_TIME = None 

# ============================================================================
# КОНСТАНТЫ (v2.1 - обновлённые)
# ============================================================================
SIGNAL_COOLDOWN_HOURS = 2      # Кулдаун для BUY/SELL сигналов
WAIT_COOLDOWN_HOURS = 1        # Кулдаун для вердиктов WAIT

# Критические паттерны для вызова LLM (обновлено для v2.1)
STRONG_SETUPS = ['BOS', 'CHOCH', 'OB_RETEST', 'SWING_BOS', 'SWING_CHOCH']

# Паттерны для импульсного входа (пропуск фильтра дистанции)
IMPULSE_PATTERNS = ['CHOCH', 'SWING_CHOCH', 'SWING_BOS']

# Порог близости к структурам (%)
STRUCTURE_PROXIMITY_THRESHOLD = 0.5

# Подключение сервисов
try:
    from services.oanda_service import oanda_service
    from services.llm_service import llm_service
    
    # 🔧 ВАЖНО: Импортируем исправленный детектор
    # Вариант 1: Если переименовали файл
    try:
        from services.smc_detector import smc_detector 
    except ImportError:
        # Вариант 2: Если оставили новое имя
        try:
            from services.smc_detector_fixed import smc_detector
        except ImportError:
            smc_detector = None
            
    from services.news_service import news_service
except ImportError as e:
    print(f"Watcher Critical Import Error: {e}")
    smc_detector = None
    news_service = None

logger = logging.getLogger("AstraWatcher")


# ============================================================================
# РЫНОЧНЫЕ ПРОВЕРКИ
# ============================================================================

def is_market_active():
    """
    Проверка активности рынка
    Рынок золота работает: воскресенье 23:00 UTC - пятница 22:00 UTC
    
    ВАЖНО: Rollover час (22:00-23:00 UTC) - рынок закрыт
    22:00-23:00 UTC = 02:00-03:00 Астрахань (UTC+4)
    """
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
    
    # 🔴 ROLLOVER ЧАС: 22:00-23:00 UTC (Пн-Чт)
    if weekday in [0, 1, 2, 3]:
        if hour == 22:
            logger.info(f"Market CLOSED: Rollover hour (22:00-23:00 UTC)")
            return False
    
    logger.debug(f"Market OPEN: Weekday={weekday}, Hour={hour}:00 UTC")
    return True


def is_news_block_active():
    """Проверка блокировки по новостям"""
    if not news_service:
        return False
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


# ============================================================================
# SMC АНАЛИЗ (v2.1 - обновлённый)
# ============================================================================

def extract_structure_events(analysis: dict) -> dict:
    """
    🆕 v2.1: Извлекает структурные события с разделением на Internal/Swing
    и информацией о типе пробоя (тело vs фитиль)
    
    Returns:
        {
            'has_swing_bos': bool,
            'has_swing_choch': bool,
            'has_internal_bos': bool,
            'has_internal_choch': bool,
            'swing_bias': 'BULLISH' | 'BEARISH' | 'NEUTRAL',
            'wick_breaks': int,  # Количество пробоев фитилём
            'body_breaks': int,  # Количество пробоев телом
            'events': list       # Все события для отчёта
        }
    """
    result = {
        'has_swing_bos': False,
        'has_swing_choch': False,
        'has_internal_bos': False,
        'has_internal_choch': False,
        'swing_bias': 'NEUTRAL',
        'wick_breaks': 0,
        'body_breaks': 0,
        'events': []
    }
    
    # Swing BOS
    for event in analysis.get('swing_bos', []):
        result['has_swing_bos'] = True
        result['events'].append(f"SWING_{event.get('type', 'BOS')}")
        
        # Определяем направление swing
        if 'BULLISH' in event.get('type', ''):
            result['swing_bias'] = 'BULLISH'
        elif 'BEARISH' in event.get('type', ''):
            result['swing_bias'] = 'BEARISH'
        
        # 🆕 Проверяем тип пробоя
        if event.get('break_by_wick', False):
            result['wick_breaks'] += 1
        else:
            result['body_breaks'] += 1
    
    # Swing CHOCH
    for event in analysis.get('swing_choch', []):
        result['has_swing_choch'] = True
        result['events'].append(f"SWING_{event.get('type', 'CHOCH')}")
        
        if 'BULLISH' in event.get('type', ''):
            result['swing_bias'] = 'BULLISH'
        elif 'BEARISH' in event.get('type', ''):
            result['swing_bias'] = 'BEARISH'
        
        if event.get('break_by_wick', False):
            result['wick_breaks'] += 1
        else:
            result['body_breaks'] += 1
    
    # Internal BOS
    for event in analysis.get('internal_bos', []):
        result['has_internal_bos'] = True
        result['events'].append(f"INT_{event.get('type', 'BOS')}")
        
        if event.get('break_by_wick', False):
            result['wick_breaks'] += 1
        else:
            result['body_breaks'] += 1
    
    # Internal CHOCH
    for event in analysis.get('internal_choch', []):
        result['has_internal_choch'] = True
        result['events'].append(f"INT_{event.get('type', 'CHOCH')}")
        
        if event.get('break_by_wick', False):
            result['wick_breaks'] += 1
        else:
            result['body_breaks'] += 1
    
    # Если swing_bias не определён, используем internal_trend
    if result['swing_bias'] == 'NEUTRAL':
        internal_trend = analysis.get('internal_trend', 'NEUTRAL')
        if internal_trend == 'UPTREND':
            result['swing_bias'] = 'BULLISH'
        elif internal_trend == 'DOWNTREND':
            result['swing_bias'] = 'BEARISH'
    
    return result


def get_zone_info(analysis: dict) -> dict:
    """
    🆕 v2.1: Извлекает информацию о зонах с учётом нового расчёта
    на основе swing pivot'ов
    
    Returns:
        {
            'current_zone': 'PREMIUM' | 'DISCOUNT' | 'EQUILIBRIUM',
            'position_pct': float (0-100),
            'range_source': 'SWING_PIVOTS' | 'LAST_50_BARS',
            'range_high': float,
            'range_low': float,
            'equilibrium': float
        }
    """
    zones = analysis.get('advanced', {}).get('zones', {})
    key_levels = analysis.get('advanced', {}).get('key_levels', {})
    
    return {
        'current_zone': key_levels.get('Current_Zone', zones.get('current_zone', 'UNKNOWN')),
        'position_pct': zones.get('position_in_range_pct', 50.0),
        'range_source': zones.get('range_source', 'UNKNOWN'),
        'range_high': zones.get('range_high', 0),
        'range_low': zones.get('range_low', 0),
        'equilibrium': zones.get('equilibrium', {}).get('price', 0)
    }


def is_price_near_smc_structure(current_price: float, analysis: dict, 
                                 threshold_percent: float = 0.5) -> tuple:
    """
    Проверяет, находится ли текущая цена близко к значимым SMC структурам.
    
    🆕 v2.1: Добавлена проверка swing pivot'ов из нового детектора
    """
    threshold = current_price * (threshold_percent / 100)
    near_structures = []
    
    # 1. Order Blocks
    for ob in analysis.get('order_blocks', []):
        ob_top = ob.get('top', 0)
        ob_bottom = ob.get('bottom', 0)
        if ob_bottom - threshold <= current_price <= ob_top + threshold:
            ob_type = ob.get('type', 'OB')
            internal = " (Int)" if ob.get('internal', False) else " (Swing)"
            near_structures.append(f"{ob_type}{internal} [{ob_bottom:.2f}-{ob_top:.2f}]")
    
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
    
    # 5. Key Levels (PDH, PDL, DH, DL, Equilibrium)
    advanced = analysis.get('advanced', {})
    key_levels = advanced.get('key_levels', {})
    for level_name in ['PDH', 'PDL', 'DH', 'DL', 'Equilibrium_Price']:
        level_price = key_levels.get(level_name, 0)
        if level_price > 0 and abs(current_price - level_price) <= threshold:
            near_structures.append(f"{level_name} @ {level_price:.2f}")
    
    # 6. 🆕 v2.1: Swing Points из нового детектора
    structure_points = advanced.get('structure_points', {})
    swing_high = structure_points.get('nearest_swing_high', 0)
    swing_low = structure_points.get('nearest_swing_low', 0)
    
    if swing_high > 0 and abs(current_price - swing_high) <= threshold:
        near_structures.append(f"🔺 Swing High @ {swing_high:.2f}")
    if swing_low > 0 and abs(current_price - swing_low) <= threshold:
        near_structures.append(f"🔻 Swing Low @ {swing_low:.2f}")
    
    if near_structures:
        description = ", ".join(near_structures[:5])  # Увеличили лимит до 5
        return True, description
    
    return False, "Нет близких SMC структур"


def collect_found_signals(analysis: dict) -> list:
    """
    🆕 v2.1: Собирает найденные сигналы с разделением Internal/Swing
    """
    found_signals = []
    
    # Order Blocks (с разделением)
    for ob in analysis.get('order_blocks_internal', []):
        if 'BULL' in ob.get('type', '') or 'BEAR' in ob.get('type', ''): 
            found_signals.append('INT_OB_RETEST')
    
    for ob in analysis.get('order_blocks_swing', []):
        if 'BULL' in ob.get('type', '') or 'BEAR' in ob.get('type', ''): 
            found_signals.append('SWING_OB_RETEST')
    
    # Для обратной совместимости
    if not found_signals:
        for ob in analysis.get('order_blocks', []):
            if 'BULL' in ob.get('type', '') or 'BEAR' in ob.get('type', ''): 
                found_signals.append('OB_RETEST')
    
    # Fair Value Gaps
    for fvg in analysis.get('fvg', []):
        if 'BULL' in fvg.get('type', '') or 'BEAR' in fvg.get('type', ''): 
            found_signals.append('FVG_FILL')
    
    # 🆕 Structure events (разделённые)
    if analysis.get('swing_choch', []): 
        found_signals.append('SWING_CHOCH')
    if analysis.get('swing_bos', []): 
        found_signals.append('SWING_BOS')
    if analysis.get('internal_choch', []): 
        found_signals.append('CHOCH')
    if analysis.get('internal_bos', []): 
        found_signals.append('BOS')
    
    # Для обратной совместимости
    if not any(x in found_signals for x in ['CHOCH', 'SWING_CHOCH']):
        if analysis.get('choch', []): 
            found_signals.append('CHOCH')
    if not any(x in found_signals for x in ['BOS', 'SWING_BOS']):
        if analysis.get('bos', []): 
            found_signals.append('BOS')
    
    # Equal Highs/Lows (Liquidity Sweep)
    if analysis.get('eqh') or analysis.get('eql'): 
        found_signals.append('SWEEP')
    
    return found_signals


# ============================================================================
# ФОРМАТИРОВАНИЕ СООБЩЕНИЙ
# ============================================================================

def format_debug_report(status_data: dict) -> str:
    """
    Форматирует детальный отладочный отчет для Telegram
    🆕 v2.1: Добавлена информация о типе пробоя и источнике зон
    """
    status_emoji = {
        'market_closed': '💤', 
        'news_block': '📰', 
        'oanda_error': '🔌',
        'no_smc': '⚙️', 
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
        'not_near_structure': 'SKIP - Цена далеко от структур',
        'weak_patterns': 'SKIP - Нет сильных паттернов',
        'cooldown': 'SKIP - Активен кулдаун',
        'signal_sent': '🎯 ТОРГОВЫЙ СИГНАЛ!',
        'wait_decision': 'LLM рекомендует WAIT'
    }
    
    status = status_data.get('status', 'unknown')
    emoji = status_emoji.get(status, '❓')
    
    # Заголовок
    now_utc = datetime.now(timezone.utc)
    msg = f"<b>{emoji} ASTRA WATCHER v2.1</b>\n"
    msg += f"<code>UTC: {now_utc.strftime('%Y-%m-%d %H:%M:%S')}</code>\n"
    msg += "━" * 32 + "\n\n"
    
    # Решение
    decision = status_texts.get(status, 'Неизвестно')
    msg += f"<b>📋 Решение:</b> {decision}\n\n"
    
    # Рыночные данные
    if status_data.get('price', 0) > 0:
        msg += "<b>💹 Рыночные данные:</b>\n"
        msg += f"├ Цена: <code>${status_data['price']:.2f}</code>\n"
        
        # Тренд с разделением Internal/Swing
        if 'swing_trend' in status_data:
            trend_emoji = "📈" if "UP" in status_data['swing_trend'] else "📉" if "DOWN" in status_data['swing_trend'] else "↔️"
            msg += f"├ Swing тренд: {trend_emoji} {status_data['swing_trend']}\n"
        elif 'trend' in status_data:
            trend_emoji = "📈" if "UP" in status_data['trend'] else "📉" if "DOWN" in status_data['trend'] else "↔️"
            msg += f"├ Тренд: {trend_emoji} {status_data['trend']}\n"
        
        # 🆕 Зона с процентом позиции
        if 'zone' in status_data:
            zone = status_data['zone']
            zone_emoji = "🔴" if zone == "PREMIUM" else "🟢" if zone == "DISCOUNT" else "⚪"
            zone_str = f"{zone_emoji} {zone}"
            
            if 'position_pct' in status_data:
                zone_str += f" ({status_data['position_pct']:.1f}%)"
            
            msg += f"├ Зона: {zone_str}\n"
        
        # 🆕 Источник расчёта зон
        if 'range_source' in status_data:
            source = status_data['range_source']
            source_emoji = "📊" if source == "SWING_PIVOTS" else "📈"
            msg += f"└ Источник: {source_emoji} {source}\n\n"
        else:
            msg += "\n"
    
    # SMC паттерны
    if 'smc_summary' in status_data and any(status_data['smc_summary'].values()):
        smc = status_data['smc_summary']
        msg += "<b>📊 SMC паттерны:</b>\n"
        msg += f"├ Order Blocks: {smc.get('ob', 0)} (Int: {smc.get('ob_internal', 0)}, Sw: {smc.get('ob_swing', 0)})\n"
        msg += f"├ Fair Value Gaps: {smc.get('fvg', 0)}\n"
        msg += f"├ BOS: {smc.get('bos', 0)} (Int: {smc.get('int_bos', 0)}, Sw: {smc.get('swing_bos', 0)})\n"
        msg += f"└ CHOCH: {smc.get('choch', 0)} (Int: {smc.get('int_choch', 0)}, Sw: {smc.get('swing_choch', 0)})\n\n"
        
        # 🆕 Информация о типе пробоя
        if 'wick_breaks' in status_data or 'body_breaks' in status_data:
            wick = status_data.get('wick_breaks', 0)
            body = status_data.get('body_breaks', 0)
            if wick > 0 or body > 0:
                msg += f"<b>🕯 Тип пробоя:</b> Фитиль: {wick}, Тело: {body}\n\n"
        
        if 'found_signals' in status_data and status_data['found_signals']:
            signals_list = ", ".join(status_data['found_signals'][:6])
            msg += f"<b>🎯 Активные сигналы:</b>\n{signals_list}\n\n"
    
    # Близость к структурам
    if 'near_structures' in status_data:
        msg += f"<b>📍 Уровни рядом:</b>\n{status_data['near_structures']}\n\n"
    
    # Причина
    if 'reason' in status_data:
        msg += f"<b>💡 Детали:</b>\n<i>{status_data['reason']}</i>\n\n"
    
    # Вердикт LLM
    if 'llm_verdict' in status_data:
        summary = extract_executive_summary(status_data['llm_verdict'])
        msg += f"<b>🤖 Gemini:</b>\n<i>{summary}</i>\n\n"
    
    # Футер
    msg += "━" * 32 + "\n"
    msg += "<i>⏱ Следующая проверка через 15 минут</i>"
    
    return msg


def parse_llm_response(ai_response: str) -> dict:
    """Парсит JSON ответ от Gemini LLM"""
    try:
        start = ai_response.find('{')
        end = ai_response.rfind('}') + 1
        if start >= 0 and end > start:
            json_str = ai_response[start:end]
            data = json.loads(json_str)
            return data
    except Exception as e:
        logger.error(f"Ошибка парсинга LLM ответа: {e}")
    return None


def extract_executive_summary(ai_response: str) -> str:
    """Извлекает executive_summary из ответа LLM"""
    parsed = parse_llm_response(ai_response)
    if parsed and 'executive_summary' in parsed:
        return parsed['executive_summary']
    
    cleaned = ai_response.replace('```json', '').replace('```', '').strip()
    if len(cleaned) > 200:
        return cleaned[:197] + '...'
    return cleaned


def format_signal_message(ai_response: str) -> str:
    """Превращает JSON от Gemini в красивый текст"""
    parsed_data = parse_llm_response(ai_response)
    
    if parsed_data:
        action = parsed_data.get("ACTION", "N/A")
        emoji = "🟢 BUY" if action == "BUY" else "🔴 SELL"
        msg = (f"<b>🚀 ASTRA SIGNAL v2.1: GOLD (XAU/USD)</b>\n\n"
               f"Направление: <b>{emoji}</b>\n"
               f"Вход: <code>{parsed_data.get('ENTRY')}</code>\n"
               f"Стоп: <code>{parsed_data.get('SL')}</code>\n"
               f"Тейк: <code>{parsed_data.get('TP')}</code>\n\n"
               f"<b>Анализ:</b>\n<i>{parsed_data.get('REASON', 'SMC Confirmation')}</i>")
        return msg
    else:
        return f"<b>📢 НОВЫЙ СИГНАЛ XAUUSD:</b>\n\n{ai_response}"


def send_debug_notification(status_data: dict):
    """Отправляет отладочный отчет всем активным пользователям"""
    try:
        user_ids = db_service.get_all_active_users()
        if user_ids:
            message = format_debug_report(status_data)
            telegram_service.broadcast_signal(user_ids, message)
            logger.info(f"📤 Debug отчет отправлен ({len(user_ids)} пользователей)")
    except Exception as e:
        logger.error(f"❌ Debug Error: {e}")


# ============================================================================
# ОСНОВНОЙ ЦИКЛ АНАЛИЗА (v2.1)
# ============================================================================

def run_analysis_cycle():
    """
    Основная функция анализа - v2.1
    
    🆕 ИЗМЕНЕНИЯ:
    1. Разделение Internal vs Swing структур
    2. Учёт типа пробоя (фитиль vs тело) 
    3. Использование swing_bias для направления
    4. Зоны на основе swing pivot'ов
    
    КОНВЕЙЕР:
    1. Технические проверки (рынок, новости, данные)
    2. SMC анализ с новым детектором v2.1
    3. Фильтры Gatekeeper (с учётом типа пробоя)
    4. LLM анализ (только если все фильтры пройдены)
    """
    global LAST_SIGNAL_TIME
    logger.info("📡 [v2.1] Цикл анализа запущен")
    
    # ========================================================================
    # ФАЗА 1: ТЕХНИЧЕСКАЯ ПОДГОТОВКА
    # ========================================================================
    
    # Шаг 1.1: Проверка рынка
    if not is_market_active():
        logger.info("💤 Рынок закрыт. Пропуск анализа.")
        now_utc = datetime.now(timezone.utc)
        weekday = now_utc.weekday()
        hour = now_utc.hour
        
        if weekday in [0, 1, 2, 3] and hour == 22:
            reason = '⏸ Rollover: 22:00-23:00 UTC (02:00-03:00 Астрахань). Высокие спреды.'
        elif weekday == 5:
            reason = 'Суббота - рынок закрыт.'
        elif weekday == 6 and hour < 23:
            reason = f'Воскресенье - откроется в 23:00 UTC (сейчас {hour}:00).'
        elif weekday == 4 and hour >= 22:
            reason = 'Пятница после 22:00 - рынок закрыт.'
        else:
            reason = 'Рынок закрыт'
        
        send_debug_notification({'status': 'market_closed', 'reason': reason})
        return
    
    # Шаг 1.2: Проверка новостей
    if is_news_block_active():
        logger.info("📰 Блокировка по новостям.")
        send_debug_notification({
            'status': 'news_block',
            'reason': 'Важные новости USD в ближайшие 45 мин или прошли менее 15 мин назад'
        })
        return
    
    # Шаг 1.3: Получение данных OANDA
    data = oanda_service.get_candles(timeframe='M15', limit=250)
    if "error" in data:
        logger.error("🔌 Ошибка OANDA")
        send_debug_notification({
            'status': 'oanda_error',
            'reason': f'Ошибка OANDA API: {data.get("error", "Unknown")}'
        })
        return
    
    candles = data.get("candles", [])
    if not candles:
        send_debug_notification({
            'status': 'oanda_error',
            'reason': 'OANDA вернул пустой массив свечей'
        })
        return
    
    # Шаг 1.4: Проверка SMC детектора
    if not smc_detector:
        logger.error("⚙️ SMC детектор недоступен")
        send_debug_notification({
            'status': 'no_smc',
            'reason': 'SMC детектор не инициализирован'
        })
        return
    
    # Шаг 1.5: SMC Анализ v2.1
    logger.info("🔬 Выполняем SMC анализ v2.1...")
    analysis = smc_detector.analyze(candles)
    
    # Шаг 1.6: Извлечение данных (v2.1)
    current_price = candles[-1].get('close', 0)
    
    # 🆕 Структурные события
    structure_events = extract_structure_events(analysis)
    
    # 🆕 Информация о зонах
    zone_info = get_zone_info(analysis)
    
    # 🆕 Собираем паттерны
    found_signals = collect_found_signals(analysis)
    
    # Тренды
    swing_trend = analysis.get('swing_trend', 'NEUTRAL')
    internal_trend = analysis.get('internal_trend', 'NEUTRAL')
    
    # Близость к структурам
    is_near, near_description = is_price_near_smc_structure(
        current_price, analysis, STRUCTURE_PROXIMITY_THRESHOLD
    )
    
    # Подготовка данных для отчёта
    status_data = {
        'price': current_price,
        'trend': swing_trend,  # Основной тренд = swing
        'swing_trend': swing_trend,
        'internal_trend': internal_trend,
        'zone': zone_info['current_zone'],
        'position_pct': zone_info['position_pct'],
        'range_source': zone_info['range_source'],
        'signals_count': len(found_signals),
        'found_signals': found_signals,
        'smc_summary': {
            'ob': len(analysis.get('order_blocks', [])),
            'ob_internal': len(analysis.get('order_blocks_internal', [])),
            'ob_swing': len(analysis.get('order_blocks_swing', [])),
            'fvg': len(analysis.get('fvg', [])),
            'bos': len(analysis.get('bos', [])),
            'int_bos': len(analysis.get('internal_bos', [])),
            'swing_bos': len(analysis.get('swing_bos', [])),
            'choch': len(analysis.get('choch', [])),
            'int_choch': len(analysis.get('internal_choch', [])),
            'swing_choch': len(analysis.get('swing_choch', []))
        },
        'wick_breaks': structure_events['wick_breaks'],
        'body_breaks': structure_events['body_breaks'],
        'near_structures': near_description,
        'status': 'unknown',
        'reason': ''
    }
    
    logger.info(f"📊 SMC v2.1: Swing={swing_trend}, Internal={internal_trend}, "
                f"Zone={zone_info['current_zone']} ({zone_info['position_pct']:.1f}%), "
                f"Source={zone_info['range_source']}")
    
    # ========================================================================
    # ФАЗА 2: ФИЛЬТРЫ GATEKEEPER (v2.1)
    # ========================================================================
    
    # 🆕 Проверка на SWING структурные изменения (важнее internal!)
    has_swing_break = structure_events['has_swing_bos'] or structure_events['has_swing_choch']
    has_internal_break = structure_events['has_internal_bos'] or structure_events['has_internal_choch']
    has_any_break = has_swing_break or has_internal_break
    
    # 🆕 Определяем силу сигнала
    # Swing пробой телом > Swing пробой фитилём > Internal пробой
    is_strong_signal = False
    if has_swing_break and structure_events['body_breaks'] > 0:
        is_strong_signal = True
        logger.info("🔥 СИЛЬНЫЙ СИГНАЛ: Swing пробой ТЕЛОМ!")
    elif has_swing_break:
        logger.info("⚡ Swing пробой фитилём обнаружен")
    elif has_internal_break:
        logger.info("📍 Internal пробой обнаружен")
    
    # --- ФИЛЬТР 1: БЛИЗОСТЬ К СТРУКТУРАМ ---
    # 🆕 v2.1: Swing пробой телом пропускает этот фильтр
    if not is_near and not is_strong_signal:
        # 🆕 Обычный swing пробой (фитилём) тоже может пропустить фильтр
        if has_swing_break:
            logger.info("⚡ Фильтр дистанции пропущен: Swing пробой обнаружен")
            status_data['bypass_reason'] = 'Swing structure break detected'
        else:
            logger.info(f"🔍 SKIP: Цена {current_price:.2f} вне зоны интереса.")
            status_data['status'] = 'not_near_structure'
            status_data['reason'] = f'Цена ${current_price:.2f} не в пределах {STRUCTURE_PROXIMITY_THRESHOLD}% от SMC структур.'
            send_debug_notification(status_data)
            return
    
    # --- ФИЛЬТР 2: НАЛИЧИЕ СИЛЬНЫХ ПАТТЕРНОВ ---
    is_worth_it = any(setup in found_signals for setup in STRONG_SETUPS)
    if not found_signals or not is_worth_it:
        logger.info("📊 SKIP: Нет сильных паттернов SMC.")
        status_data['status'] = 'weak_patterns'
        strong_list = ", ".join(STRONG_SETUPS)
        found_list = ", ".join(found_signals) if found_signals else "Нет"
        status_data['reason'] = f'Требуются: {strong_list}. Найдено: {found_list}'
        send_debug_notification(status_data)
        return
    
    # --- ФИЛЬТР 3: НЕЙТРАЛЬНЫЙ ТРЕНД ---
    # 🆕 v2.1: Используем swing_bias вместо internal
    effective_bias = structure_events['swing_bias']
    if effective_bias == 'NEUTRAL':
        effective_bias = 'BULLISH' if internal_trend == 'UPTREND' else 'BEARISH' if internal_trend == 'DOWNTREND' else 'NEUTRAL'
    
    # CHOCH даёт право на вызов LLM даже при нейтральном тренде
    has_choch = 'CHOCH' in found_signals or 'SWING_CHOCH' in found_signals
    
    if effective_bias == 'NEUTRAL' and not has_choch:
        logger.info("🛑 SKIP: Нейтральный тренд без CHOCH.")
        status_data['status'] = 'weak_patterns'
        status_data['reason'] = '🛑 Нейтральный тренд. Ждём CHOCH для разворота.'
        send_debug_notification(status_data)
        return
    
    # --- ФИЛЬТР 4: КУЛДАУН ---
    if not check_smart_cooldown():
        logger.info("⏳ SKIP: Кулдаун активен.")
        status_data['status'] = 'cooldown'
        status_data['reason'] = 'Кулдаун: недавний сигнал (2ч) или WAIT (1ч).'
        send_debug_notification(status_data)
        return
    
    # ========================================================================
    # ФАЗА 3: LLM АНАЛИЗ
    # ========================================================================
    
    logger.info("=" * 60)
    logger.info("🎯 ВСЕ ФИЛЬТРЫ ПРОЙДЕНЫ! Запрос к Gemini...")
    logger.info(f"   💰 Цена: ${current_price:.2f}")
    logger.info(f"   📈 Swing Trend: {swing_trend}, Bias: {effective_bias}")
    logger.info(f"   🎯 Зона: {zone_info['current_zone']} ({zone_info['position_pct']:.1f}%)")
    logger.info(f"   📍 Источник зон: {zone_info['range_source']}")
    logger.info(f"   🔍 Уровни: {near_description}")
    logger.info(f"   📊 Паттерны: {', '.join(found_signals)}")
    logger.info(f"   🕯 Пробои: Фитиль={structure_events['wick_breaks']}, Тело={structure_events['body_breaks']}")
    logger.info("=" * 60)
    
    # Вызов Gemini
    ai_response = llm_service.get_signal_verdict(analysis)
    
    # Парсинг ответа
    parsed_llm = parse_llm_response(ai_response)
    llm_action = parsed_llm.get('ACTION', 'WAIT') if parsed_llm else 'WAIT'
    is_confirmed = llm_action in ['BUY', 'SELL']
    
    # Подготовка данных для БД
    signal_data_db = {
        'symbol': 'XAU_USD',
        'signal_type': llm_action,
        'entry_price': current_price,
        'current_price': current_price,
        'trend': swing_trend,
        'zone': zone_info['current_zone'],
        'patterns': ', '.join(found_signals) if found_signals else 'None',
        'near_structures': near_description,
        'llm_full_response': ai_response,
        'llm_reason': parsed_llm.get('REASON', ai_response[:500]) if parsed_llm else ai_response[:500],
        'llm_confidence': parsed_llm.get('CONFIDENCE', 0) if parsed_llm else 0,
        'stop_loss': 0,
        'take_profit': 0,
        # 🆕 v2.1: Новые поля
        'swing_bias': effective_bias,
        'range_source': zone_info['range_source'],
        'wick_breaks': structure_events['wick_breaks'],
        'body_breaks': structure_events['body_breaks']
    }
    
    if is_confirmed:
        signal_data_db['entry_price'] = parsed_llm.get('ENTRY', current_price) if parsed_llm else current_price
        signal_data_db['stop_loss'] = parsed_llm.get('SL', 0) if parsed_llm else 0
        signal_data_db['take_profit'] = parsed_llm.get('TP', 0) if parsed_llm else 0
    
    # ========================================================================
    # ОТПРАВКА РЕЗУЛЬТАТА
    # ========================================================================
    
    if is_confirmed:
        logger.info(f"🔥 ТОРГОВЫЙ СИГНАЛ: {llm_action}")
        
        db_service.update_last_signal_time()
        signal_id = db_service.save_signal(signal_data_db)
        logger.info(f"💾 Сигнал сохранен (ID: {signal_id})")
        
        user_ids = db_service.get_all_active_users()
        if user_ids:
            formatted_msg = format_signal_message(ai_response)
            telegram_service.broadcast_signal(user_ids, formatted_msg)
            logger.info(f"📤 Сигнал отправлен {len(user_ids)} пользователям")
            
            status_data['status'] = 'signal_sent'
            status_data['reason'] = f'Gemini подтвердил {llm_action}. Отправлено.'
            status_data['llm_verdict'] = ai_response
            send_debug_notification(status_data)
    else:
        logger.info("⚖️ Gemini: WAIT. Блокировка на 1 час.")
        
        db_service.update_last_wait_time()
        signal_id = db_service.save_signal(signal_data_db)
        logger.info(f"💾 WAIT сохранен (ID: {signal_id})")
        
        status_data['status'] = 'wait_decision'
        status_data['reason'] = 'Gemini не видит четкого сетапа.'
        status_data['llm_verdict'] = ai_response
        send_debug_notification(status_data)


def start_watcher():
    """Инициализация наблюдателя"""
    logger.info("🛰 Astra Watcher v2.1 инициализирован и ожидает Cron-команды.")


if __name__ == "__main__":
    logger.info("🧪 Ручной запуск анализа v2.1...")
    run_analysis_cycle()
