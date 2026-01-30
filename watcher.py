"""
Astra Watcher v2.4 - Абсолютный запрет + жёсткий фикс зон
==============================================================
Критические исправления:
1. Supabase 400 Error - правильная структура данных для БД
2. Ужесточение триггера LLM - приоритет Swing над Internal
3. Сохранение 15-минутных отчётов
4. ЖЁСТКИЙ ФИКС ЗОН - пересчёт Premium/Discount поверх детектора

Рекомендация от Gemini: В сильном тренде детектор не находит "красивые"
pivot точки, поэтому зоны считаются неверно. Решение - пересчитываем
зоны в watcher.py используя простой max/min за 250 свечей.
"""

import os
import math
from datetime import datetime, timedelta, timezone
import logging
import json
from services.db_service import db_service
from services.telegram_service import telegram_service 


def safe_float(value, default=0.0):
    """
    Безопасное преобразование в float с проверкой на NaN/Inf
    """
    try:
        result = float(value) if value is not None else default
        if math.isnan(result) or math.isinf(result):
            return default
        return result
    except (TypeError, ValueError):
        return default


# Переменная для отслеживания времени последнего сигнала
LAST_SIGNAL_TIME = None 

# Константы
SIGNAL_COOLDOWN_HOURS = 2      # Кулдаун для BUY/SELL сигналов
WAIT_COOLDOWN_HOURS = 1        # Кулдаун для вердиктов WAIT

# Сильные паттерны для разных типов
SWING_STRONG_SETUPS = ['SWING_BOS', 'SWING_CHOCH']           # Swing паттерны (приоритет!)
INTERNAL_STRONG_SETUPS = ['INT_BOS', 'INT_CHOCH', 'OB_RETEST']  # Internal паттерны
ALL_STRONG_SETUPS = SWING_STRONG_SETUPS + INTERNAL_STRONG_SETUPS + ['BOS', 'CHOCH']

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


# ============================================================================
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# ============================================================================

def is_market_active():
    """
    Проверка активности рынка
    Рынок золота работает: воскресенье 23:00 UTC - пятница 22:00 UTC
    
    ВАЖНО: Rollover час (22:00-23:00 UTC) - рынок закрыт
    """
    now = datetime.now(timezone.utc)
    weekday = now.weekday()
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
    
    # Rollover час: 22:00-23:00 UTC (Пн-Чт)
    if weekday in [0, 1, 2, 3] and hour == 22:
        logger.info(f"Market CLOSED: Rollover hour (22:00-23:00 UTC)")
        return False
    
    logger.debug(f"Market OPEN: Weekday={weekday}, Hour={hour}:00 UTC")
    return True


def is_news_blockactive():
    """Логика блокировки по новостям"""
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
    """
    try:
        now = datetime.now(timezone.utc)
        
        # Проверка BUY/SELL
        last_trade = db_service.get_last_trade_signal_time()
        if (now - last_trade).total_seconds() < (SIGNAL_COOLDOWN_HOURS * 3600):
            logger.info("⏳ Кулдаун СДЕЛКИ активен. Пропуск.")
            return False

        # Проверка WAIT
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


# ============================================================================
# ЖЁСТКИЙ РАСЧЁТ ЗОН (ОБХОД ДЕТЕКТОРА)
# ============================================================================

def calculate_forced_zones(candles):
    """
    🔧 ЖЁСТКИЙ ФИКС ЗОН (рекомендация от Gemini)
    
    Проблема: В сильном тренде детектор не находит "красивые" pivot точки,
    поэтому зоны Premium/Discount считаются неверно.
    
    Решение: Считаем зоны тупо по max/min за все 250 свечей.
    Это железобетонно и не зависит от pivot detection.
    
    Returns:
        (current_zone, position_in_range_pct, global_high, global_low)
    """
    try:
        # Берём max и min за все свечи
        all_highs = [c['high'] for c in candles]
        all_lows = [c['low'] for c in candles]
        
        global_high = max(all_highs)
        global_low = min(all_lows)
        current_close = candles[-1]['close']
        
        # Защита от деления на ноль
        if global_high == global_low:
            position_pct = 50.0
        else:
            # Считаем, где мы реально находимся (0% = дно, 100% = вершина)
            position_pct = ((current_close - global_low) / (global_high - global_low)) * 100
        
        # Принудительно определяем зону
        if position_pct < 33.3:
            forced_zone = "DISCOUNT"
        elif position_pct > 66.6:
            forced_zone = "PREMIUM"
        else:
            forced_zone = "EQUILIBRIUM"
        
        logger.info(f"🔧 ЗОНЫ ПЕРЕСЧИТАНЫ: Цена {current_close:.2f} в диапазоне "
                   f"[{global_low:.2f} - {global_high:.2f}] -> {position_pct:.1f}% ({forced_zone})")
        
        return forced_zone, position_pct, global_high, global_low
        
    except Exception as e:
        logger.error(f"Ошибка расчёта зон: {e}")
        return "UNKNOWN", 50.0, 0.0, 0.0


# ============================================================================
# ПАРСИНГ И ФОРМАТИРОВАНИЕ
# ============================================================================

def parse_llm_response(ai_response):
    """
    Парсит JSON ответ от Gemini LLM
    """
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


def extract_executive_summary(ai_response):
    """
    Извлекает executive_summary из ответа LLM
    """
    parsed = parse_llm_response(ai_response)
    if parsed and 'executive_summary' in parsed:
        return parsed['executive_summary']
    
    cleaned = ai_response.replace('```json', '').replace('```', '').strip()
    if len(cleaned) > 200:
        return cleaned[:197] + '...'
    return cleaned


def format_signal_message(ai_response):
    """Превращает JSON от Gemini в красивый текст"""
    parsed_data = parse_llm_response(ai_response)
    
    if parsed_data:
        action = parsed_data.get("ACTION", "N/A")
        emoji = "🟢 BUY" if action == "BUY" else "🔴 SELL"
        msg = (f"<b>🚀 ASTRA SIGNAL: GOLD (XAU/USD)</b>\n\n"
               f"Направление: <b>{emoji}</b>\n"
               f"Вход: <code>{parsed_data.get('ENTRY')}</code>\n"
               f"Стоп: <code>{parsed_data.get('SL')}</code>\n"
               f"Тейк: <code>{parsed_data.get('TP')}</code>\n\n"
               f"<b>Анализ:</b>\n<i>{parsed_data.get('REASON', 'SMC Confirmation')}</i>")
        return msg
    else:
        return f"<b>📢 НОВЫЙ СИГНАЛ XAUUSD:</b>\n\n{ai_response}"


def format_debug_report(status_data):
    """
    Форматирует детальный отладочный отчет для Telegram
    """
    status_emoji = {
        'market_closed': '💤', 
        'news_block': '📰', 
        'oanda_error': '🔌',
        'no_smc': '⚙️', 
        'not_near_structure': '🔍',
        'equilibrium_zone': '⚪',
        'hard_filter_discount_downtrend': '🛑',
        'hard_filter_premium_uptrend': '🛑',
        'weak_patterns': '📉',
        'neutral_no_swing': '⚖️',
        'cooldown': '⏳', 
        'signal_sent': '✅', 
        'wait_decision': '⚖️'
    }
    
    status_texts = {
        'market_closed': 'Рынок закрыт', 
        'news_block': 'Блокировка по новостям',
        'oanda_error': 'Ошибка OANDA',
        'no_smc': 'SMC детектор недоступен',
        'not_near_structure': 'SKIP - Цена далеко от структур (>0.5%)',
        'equilibrium_zone': 'SKIP - Цена в зоне Equilibrium',
        'hard_filter_discount_downtrend': '🛑 ЗАПРЕТ: Продажа в DISCOUNT при DownTrend',
        'hard_filter_premium_uptrend': '🛑 ЗАПРЕТ: Покупка в PREMIUM при UpTrend',
        'weak_patterns': 'SKIP - Нет сильных паттернов',
        'neutral_no_swing': 'SKIP - Нейтральный тренд без Swing пробоя',
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
    
    # Рыночные данные
    if status_data.get('price', 0) > 0:
        msg += "<b>💹 Рыночные данные:</b>\n"
        msg += f"├ Цена: <code>${status_data['price']:.2f}</code>\n"
        
        if 'trend' in status_data:
            trend_emoji = "📈" if "UP" in status_data['trend'] else "📉" if "DOWN" in status_data['trend'] else "↔️"
            msg += f"├ Swing Тренд: {trend_emoji} {status_data['trend']}\n"
        
        if 'internal_trend' in status_data:
            int_trend = status_data['internal_trend']
            int_emoji = "📈" if "UP" in int_trend else "📉" if "DOWN" in int_trend else "↔️"
            msg += f"├ Internal Тренд: {int_emoji} {int_trend}\n"
        
        if 'zone' in status_data:
            zone = status_data['zone']
            zone_emoji = "🔴" if zone == "PREMIUM" else "🟢" if zone == "DISCOUNT" else "⚪"
            msg += f"├ Зона: {zone_emoji} {zone}\n"
        
        # Позиция в рендже (показываем всегда!)
        if 'position_in_range_pct' in status_data:
            pos_pct = status_data['position_in_range_pct']
            msg += f"└ Позиция: {pos_pct:.1f}% диапазона\n\n"
        else:
            msg += "\n"
    
    # Диапазон (если есть)
    if 'global_high' in status_data and 'global_low' in status_data:
        msg += f"<b>📐 Диапазон 250 свечей:</b>\n"
        msg += f"├ High: ${status_data['global_high']:.2f}\n"
        msg += f"└ Low: ${status_data['global_low']:.2f}\n\n"
    
    # SMC паттерны (разделённые)
    if 'smc_summary' in status_data and any(status_data['smc_summary'].values()):
        smc = status_data['smc_summary']
        msg += "<b>📊 SMC Паттерны:</b>\n"
        msg += f"├ Order Blocks: {smc.get('ob', 0)}\n"
        msg += f"├ Fair Value Gaps: {smc.get('fvg', 0)}\n"
        msg += f"├ Swing BOS: {smc.get('swing_bos_total', 0)} (Total) | Fresh: {smc.get('swing_bos', 0)}\n"
        msg += f"├ Swing CHoCH: {smc.get('swing_choch_total', 0)} (Total) | Fresh: {smc.get('swing_choch', 0)}\n"
        msg += f"└ Internal BOS: {smc.get('int_bos', 0)} | CHoCH: {smc.get('int_choch', 0)}\n\n"
    
    # Найденные сигналы (разделённые по типу)
    if 'swing_signals' in status_data and status_data['swing_signals']:
        signals_list = ", ".join(status_data['swing_signals'][:5])
        msg += f"<b>🎯 Swing сигналы:</b> {signals_list}\n"
    
    if 'internal_signals' in status_data and status_data['internal_signals']:
        signals_list = ", ".join(status_data['internal_signals'][:5])
        msg += f"<b>📍 Internal сигналы:</b> {signals_list}\n\n"
    
    # Близость к структурам
    if 'near_structures' in status_data:
        msg += f"<b>🎯 Уровни рядом:</b>\n{status_data['near_structures']}\n\n"
    
    # Причина остановки
    if 'reason' in status_data:
        msg += f"<b>💡 Детали:</b>\n<i>{status_data['reason']}</i>\n\n"
    
    # Вердикт LLM
    if 'llm_verdict' in status_data:
        summary = extract_executive_summary(status_data['llm_verdict'])
        msg += f"<b>🤖 Gemini резюме:</b>\n<i>{summary}</i>\n\n"
    
    # Футер
    msg += "━" * 32 + "\n"
    msg += "<i>⏱ Следующая проверка через 15 минут</i>"
    
    return msg


def send_debug_notification(status_data):
    """
    Отправляет отладочный отчет ВСЕМ активным пользователям
    """
    try:
        user_ids = db_service.get_all_active_users()
        if user_ids:
            message = format_debug_report(status_data)
            telegram_service.broadcast_signal(user_ids, message)
            logger.info(f"📤 Debug отчет отправлен ({len(user_ids)} пользователей)")
    except Exception as e:
        logger.error(f"❌ Debug Error: {e}")


# ============================================================================
# СБОР СИГНАЛОВ (РАЗДЕЛЕНИЕ SWING vs INTERNAL)
# ============================================================================

def collect_signals_by_type(analysis):
    """
    Собирает и классифицирует сигналы на Swing и Internal
    
    Returns:
        (swing_signals, internal_signals, all_signals)
    """
    swing_signals = []
    internal_signals = []
    
    # === SWING СИГНАЛЫ (приоритет!) ===
    
    # Swing BOS
    for bos in analysis.get('swing_bos', []):
        bos_type = bos.get('type', 'BOS')
        bars_ago = bos.get('bars_ago', 0)
        swing_signals.append(f"SWING_BOS ({bos_type}, {bars_ago} bars ago)")
    
    # Swing CHoCH
    for choch in analysis.get('swing_choch', []):
        choch_type = choch.get('type', 'CHOCH')
        bars_ago = choch.get('bars_ago', 0)
        swing_signals.append(f"SWING_CHOCH ({choch_type}, {bars_ago} bars ago)")
    
    # Swing Order Blocks
    for ob in analysis.get('order_blocks_swing', []):
        ob_type = ob.get('type', 'OB')
        swing_signals.append(f"SWING_OB ({ob_type})")
    
    # === INTERNAL СИГНАЛЫ ===
    
    # Internal BOS
    for bos in analysis.get('internal_bos', []):
        bos_type = bos.get('type', 'BOS')
        bars_ago = bos.get('bars_ago', 0)
        internal_signals.append(f"INT_BOS ({bos_type}, {bars_ago} bars ago)")
    
    # Internal CHoCH
    for choch in analysis.get('internal_choch', []):
        choch_type = choch.get('type', 'CHOCH')
        bars_ago = choch.get('bars_ago', 0)
        internal_signals.append(f"INT_CHOCH ({choch_type}, {bars_ago} bars ago)")
    
    # Internal Order Blocks
    for ob in analysis.get('order_blocks_internal', []):
        ob_type = ob.get('type', 'OB')
        internal_signals.append(f"INT_OB ({ob_type})")
    
    # FVG (относим к internal)
    for fvg in analysis.get('fvg', []):
        fvg_type = fvg.get('type', 'FVG')
        internal_signals.append(f"FVG ({fvg_type})")
    
    # Equal Highs/Lows (относим к internal)
    if analysis.get('eqh'):
        internal_signals.append('EQH_SWEEP')
    if analysis.get('eql'):
        internal_signals.append('EQL_SWEEP')
    
    # Объединяем для обратной совместимости
    all_signals = swing_signals + internal_signals
    
    return swing_signals, internal_signals, all_signals


def get_signal_label(action):
    """
    Возвращает label для сигнала (для БД)
    """
    if action == 'BUY':
        return "🟢 ПОКУПКА"
    elif action == 'SELL':
        return "🔴 ПРОДАЖА"
    else:
        return "⚖️ ОЖИДАНИЕ"


def prepare_signal_data_for_db(llm_action, parsed_llm, ai_response, current_price, 
                                trend, internal_trend, zone, 
                                swing_signals, internal_signals, smc_summary):
    """
    Подготавливает данные для сохранения в Supabase
    
    ИСПРАВЛЕНИЕ v2.3:
    - patterns = СТРОГО список (не .join()!)
    - smc_summary = СТРОГО dict
    - Все числовые поля через safe_float() с проверкой NaN
    """
    
    # Собираем все паттерны в СПИСОК (СТРОГО список, не строку!)
    all_patterns = swing_signals + internal_signals
    patterns_list = list(all_patterns) if all_patterns else []
    
    # Определяем label
    signal_label = get_signal_label(llm_action)
    
    # Извлекаем данные из LLM ответа с БЕЗОПАСНЫМ приведением типов
    entry_price = safe_float(current_price, 0.0)
    stop_loss = 0.0
    take_profit = 0.0
    confidence = 0
    reason = ""
    
    if parsed_llm and isinstance(parsed_llm, dict):
        entry_price = safe_float(parsed_llm.get('ENTRY', current_price), safe_float(current_price, 0.0))
        stop_loss = safe_float(parsed_llm.get('SL', 0), 0.0)
        take_profit = safe_float(parsed_llm.get('TP', 0), 0.0)
        try:
            confidence = int(parsed_llm.get('CONFIDENCE', 0) or 0)
        except (TypeError, ValueError):
            confidence = 0
        reason = str(parsed_llm.get('REASON', ''))[:500]
    
    # Формируем payload с ПРАВИЛЬНЫМИ типами для Supabase
    signal_data = {
        'symbol': 'XAU_USD',
        'signal_type': str(llm_action),
        'signal_label': signal_label,
        'status': 'active',
        
        # Цены - все через safe_float() для защиты от NaN
        'entry_price': safe_float(entry_price, 0.0),
        'current_price': safe_float(current_price, 0.0),
        'stop_loss': safe_float(stop_loss, 0.0),
        'take_profit': safe_float(take_profit, 0.0),
        
        # Тренды - строки
        'trend': str(trend) if trend else 'NEUTRAL',
        'internal_trend': str(internal_trend) if internal_trend else 'NEUTRAL',
        'zone': str(zone) if zone else 'UNKNOWN',
        
        # Паттерны - СТРОГО СПИСОК (не строка!)
        'patterns': patterns_list,
        
        # SMC Summary - СТРОГО словарь (jsonb)
        'smc_summary': dict(smc_summary) if isinstance(smc_summary, dict) else {},
        
        # LLM данные
        'llm_full_response': str(ai_response)[:2000] if ai_response else '',
        'llm_reason': reason,
        'llm_confidence': confidence
    }
    
    logger.debug(f"Prepared DB payload: signal_type={llm_action}, patterns={len(patterns_list)}, confidence={confidence}")
    
    return signal_data


# ============================================================================
# ГЛАВНЫЙ ЦИКЛ АНАЛИЗА
# ============================================================================

def run_analysis_cycle():
    """
    Основная функция анализа v2.3
    
    КЛЮЧЕВЫЕ ИЗМЕНЕНИЯ:
    1. Разделение Swing vs Internal сигналов
    2. Ужесточение: NEUTRAL тренд требует Swing пробоя для вызова LLM
    3. Правильная структура данных для Supabase
    4. 🔧 ЖЁСТКИЙ ФИКС ЗОН - пересчёт поверх детектора
    """
    global LAST_SIGNAL_TIME
    logger.info("📡 [TRIGGER] Цикл анализа запущен")
    
    # ========================================================================
    # ФАЗА 1: ТЕХНИЧЕСКАЯ ПОДГОТОВКА
    # ========================================================================
    
    # Проверка активности рынка
    if not is_market_active():
        now_utc = datetime.now(timezone.utc)
        weekday = now_utc.weekday()
        hour = now_utc.hour
        
        if weekday in [0, 1, 2, 3] and hour == 22:
            reason = '⏸ Rollover час (22:00-23:00 UTC). Высокие спреды.'
        elif weekday == 5:
            reason = 'Суббота - рынок закрыт.'
        elif weekday == 6 and hour < 23:
            reason = f'Воскресенье - рынок откроется в 23:00 UTC (сейчас {hour}:00).'
        elif weekday == 4 and hour >= 22:
            reason = 'Пятница после 22:00 UTC - рынок закрыт.'
        else:
            reason = 'Рынок закрыт.'
        
        send_debug_notification({'status': 'market_closed', 'reason': reason})
        return
    
    # Проверка блокировки по новостям
    if is_news_blockactive():
        send_debug_notification({
            'status': 'news_block',
            'reason': 'Важные новости USD в ближайшие 45 мин или прошли менее 15 мин назад'
        })
        return
    
    # Получение данных OANDA
    data = oanda_service.get_candles(timeframe='M15', limit=250)
    if "error" in data:
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
    
    if not smc_detector:
        send_debug_notification({
            'status': 'no_smc',
            'reason': 'SMC детектор не инициализирован'
        })
        return
    
    # ---------------------------------------------------------
    # 1. Запускаем SMC анализ (ради BOS, CHoCH, OB)
    # ---------------------------------------------------------
    logger.info("🔬 Выполняем SMC анализ...")
    analysis = smc_detector.analyze(candles)
    
    # ---------------------------------------------------------
    # 2. 🔧 ЖЁСТКИЙ ФИКС ЗОН (Переписываем логику детектора)
    # ---------------------------------------------------------
    # Мы берём max и min за все 250 свечей. Это железобетонно.
    current_zone, position_in_range_pct, global_high, global_low = calculate_forced_zones(candles)
    
    # ---------------------------------------------------------
    # 3. Извлекаем остальные данные (Тренды берём из детектора - там всё ок)
    # ---------------------------------------------------------
    swing_trend = analysis.get('trend', 'NEUTRAL')
    internal_trend = analysis.get('internal_trend', 'NEUTRAL')
    current_price = safe_float(candles[-1].get('close', 0), 0.0)
    
    # ========================================================================
    # СБОР СИГНАЛОВ (РАЗДЕЛЕНИЕ SWING vs INTERNAL)
    # ========================================================================
    
    swing_signals, internal_signals, all_signals = collect_signals_by_type(analysis)
    
    logger.info(f"📊 Найдено: Swing={len(swing_signals)}, Internal={len(internal_signals)}")
    
    # SMC Summary для БД (Total = вся история 250 свечей, Fresh = последние 10 баров)
    smc_summary = {
        'ob': len(analysis.get('order_blocks', [])),
        'fvg': len(analysis.get('fvg', [])),
        # Свежие (Fresh) - для фильтров
        'swing_bos': len(analysis.get('swing_bos', [])),
        'swing_choch': len(analysis.get('swing_choch', [])),
        'int_bos': len(analysis.get('internal_bos', [])),
        'int_choch': len(analysis.get('internal_choch', [])),
        # Total (вся история) - для отчёта
        'swing_bos_total': len(analysis.get('all_swing_bos', [])),
        'swing_choch_total': len(analysis.get('all_swing_choch', [])),
        'int_bos_total': len(analysis.get('all_internal_bos', [])),
        'int_choch_total': len(analysis.get('all_internal_choch', []))
    }
    
    # Проверка близости к структурам
    is_near, near_description = is_price_near_smc_structure(current_price, analysis, threshold_percent=0.5)
    
    # Базовая структура статуса
    status_data = {
        'price': current_price,
        'trend': swing_trend,
        'internal_trend': internal_trend,
        'zone': current_zone,
        'position_in_range_pct': position_in_range_pct,
        'global_high': global_high,
        'global_low': global_low,
        'swing_signals': swing_signals,
        'internal_signals': internal_signals,
        'smc_summary': smc_summary,
        'near_structures': near_description,
        'status': 'unknown',
        'reason': ''
    }
    
    # ========================================================================
    # ФАЗА 2: ФИЛЬТРЫ GATEKEEPER (УЖЕСТОЧЁННЫЕ)
    # ========================================================================
    
    # Проверяем наличие Swing пробоев
    has_swing_bos = len(analysis.get('swing_bos', [])) > 0
    has_swing_choch = len(analysis.get('swing_choch', [])) > 0
    has_swing_break = has_swing_bos or has_swing_choch
    
    has_int_bos = len(analysis.get('internal_bos', [])) > 0
    has_int_choch = len(analysis.get('internal_choch', [])) > 0
    has_internal_break = has_int_bos or has_int_choch
    
    logger.info(f"🔍 Swing Break: {has_swing_break} | Internal Break: {has_internal_break}")
    
    # --- ФИЛЬТР 1: БЛИЗОСТЬ К СТРУКТУРАМ ---
    # Исключение: пропускаем если есть Swing пробой
    if not is_near and not has_swing_break:
        status_data['status'] = 'not_near_structure'
        status_data['reason'] = f'Цена ${current_price:.2f} далеко от SMC структур (>0.5%). Нет Swing пробоя.'
        send_debug_notification(status_data)
        return
    
    # --- ФИЛЬТР 1.5: EQUILIBRIUM ZONE PROTECTION ---
    # 🔥 ЭКОНОМИЯ LLM: Вызов Gemini ЗАПРЕЩЕН в зоне Equilibrium!
    if current_zone == "EQUILIBRIUM":
        status_data['status'] = 'equilibrium_zone'
        status_data['reason'] = (
            f'Цена в зоне Equilibrium ({position_in_range_pct:.1f}% рендж). '
            f'Ждём выхода в Premium/Discount для поиска сетапа.'
        )
        send_debug_notification(status_data)
        return
    
    # =========================================================================
    # 🛑 АБСОЛЮТНЫЙ ЗАПРЕТ (HARD FILTER) — v2.4
    # =========================================================================
    # Эти комбинации НИКОГДА не должны вызывать LLM!
    # 
    # DOWNTREND + DISCOUNT = Падение уже произошло, продавать поздно
    # UPTREND + PREMIUM = Рост уже произошёл, покупать поздно
    # =========================================================================
    
    if swing_trend == "DOWNTREND" and current_zone == "DISCOUNT":
        status_data['status'] = 'hard_filter_discount_downtrend'
        status_data['reason'] = (
            f'🛑 КАТЕГОРИЧЕСКИЙ ЗАПРЕТ: Продажа в DISCOUNT при DownTrend.\n'
            f'Цена уже упала на {100 - position_in_range_pct:.1f}% от максимума.\n'
            f'Входить в SELL поздно — ждём откат в Premium или разворот.'
        )
        send_debug_notification(status_data)
        return
    
    if swing_trend == "UPTREND" and current_zone == "PREMIUM":
        status_data['status'] = 'hard_filter_premium_uptrend'
        status_data['reason'] = (
            f'🛑 КАТЕГОРИЧЕСКИЙ ЗАПРЕТ: Покупка в PREMIUM при UpTrend.\n'
            f'Цена уже выросла на {position_in_range_pct:.1f}% от минимума.\n'
            f'Входить в BUY поздно — ждём откат в Discount или разворот.'
        )
        send_debug_notification(status_data)
        return
    
    # --- ФИЛЬТР 2: НЕЙТРАЛЬНЫЙ ТРЕНД ТРЕБУЕТ SWING ПРОБОЯ ---
    # 🔥 КЛЮЧЕВОЕ ИЗМЕНЕНИЕ: При NEUTRAL нужен обязательно Swing BOS/CHoCH
    if swing_trend == "NEUTRAL":
        if not has_swing_break:
            # Internal сигналов НЕДОСТАТОЧНО для нейтрального рынка!
            status_data['status'] = 'neutral_no_swing'
            
            if has_internal_break:
                status_data['reason'] = (
                    f'🛑 Тренд НЕЙТРАЛЬНЫЙ. Найдены Internal сигналы ({len(internal_signals)}), '
                    f'но для вызова LLM требуется Swing BOS или Swing CHoCH. '
                    f'Internal сигналы недостаточны при боковике.'
                )
            else:
                status_data['reason'] = (
                    f'🛑 Тренд НЕЙТРАЛЬНЫЙ. Нет структурных пробоев. '
                    f'Ожидаем Swing BOS/CHoCH для определения направления.'
                )
            
            send_debug_notification(status_data)
            return
        else:
            logger.info(f"⚡ Нейтральный тренд, но есть Swing пробой - продолжаем!")
    
    # --- ФИЛЬТР 3: НАЛИЧИЕ СИЛЬНЫХ ПАТТЕРНОВ ---
    has_strong_swing = any('SWING' in s for s in swing_signals)
    has_strong_internal = any('INT' in s or 'OB' in s for s in internal_signals)
    
    if not all_signals or (not has_strong_swing and not has_strong_internal):
        status_data['status'] = 'weak_patterns'
        status_data['reason'] = f'Нет сильных SMC паттернов. Swing: {swing_signals}, Internal: {internal_signals}'
        send_debug_notification(status_data)
        return
    
    # --- ФИЛЬТР 4: КУЛДАУН ---
    if not check_smart_cooldown():
        status_data['status'] = 'cooldown'
        status_data['reason'] = 'Кулдаун активен: недавний сигнал (2ч) или WAIT (1ч).'
        send_debug_notification(status_data)
        return
    
    # ========================================================================
    # ФАЗА 3: ВЫЗОВ LLM
    # ========================================================================
    
    logger.info("=" * 60)
    logger.info("🎯 ВСЕ ФИЛЬТРЫ ПРОЙДЕНЫ! Запрашиваем Gemini...")
    logger.info(f"   💰 Цена: ${current_price:.2f}")
    logger.info(f"   📈 Swing Тренд: {swing_trend}")
    logger.info(f"   📍 Internal Тренд: {internal_trend}")
    logger.info(f"   🎯 Зона: {current_zone} ({position_in_range_pct:.1f}%)")
    logger.info(f"   📐 Диапазон: ${global_low:.2f} - ${global_high:.2f}")
    logger.info(f"   🔥 Swing сигналы: {swing_signals}")
    logger.info(f"   📊 Internal сигналы: {internal_signals}")
    logger.info("=" * 60)
    
    # Вызов Gemini
    ai_response = llm_service.get_signal_verdict(analysis)
    
    # Парсим ответ
    parsed_llm = parse_llm_response(ai_response)
    llm_action = parsed_llm.get('ACTION', 'WAIT') if parsed_llm else 'WAIT'
    is_confirmed = llm_action in ['BUY', 'SELL']
    
    # ========================================================================
    # ПОДГОТОВКА ДАННЫХ ДЛЯ БД (ИСПРАВЛЕННАЯ СТРУКТУРА)
    # ========================================================================
    
    signal_data_db = prepare_signal_data_for_db(
        llm_action=llm_action,
        parsed_llm=parsed_llm,
        ai_response=ai_response,
        current_price=current_price,
        trend=swing_trend,
        internal_trend=internal_trend,
        zone=current_zone,
        swing_signals=swing_signals,
        internal_signals=internal_signals,
        smc_summary=smc_summary
    )
    
    # ========================================================================
    # СЛУЧАЙ A: ТОРГОВЫЙ СИГНАЛ (BUY/SELL)
    # ========================================================================
    
    if is_confirmed:
        logger.info(f"🔥 ТОРГОВЫЙ СИГНАЛ: {llm_action}")
        
        # Обновляем кулдаун
        db_service.update_last_signal_time()
        
        # Сохраняем в БД
        try:
            signal_id = db_service.save_signal(signal_data_db)
            logger.info(f"💾 Сигнал сохранен в БД (ID: {signal_id})")
        except Exception as e:
            logger.error(f"❌ Ошибка сохранения в БД: {e}")
            signal_id = None
        
        # Отправляем сигнал пользователям
        user_ids = db_service.get_all_active_users()
        if user_ids:
            formatted_msg = format_signal_message(ai_response)
            telegram_service.broadcast_signal(user_ids, formatted_msg)
            logger.info(f"📤 Сигнал отправлен {len(user_ids)} пользователям")
        
        # Отправляем отладочный отчёт
        status_data['status'] = 'signal_sent'
        status_data['reason'] = f'Gemini подтвердил {llm_action}. Сигнал отправлен.'
        status_data['llm_verdict'] = ai_response
        send_debug_notification(status_data)
    
    # ========================================================================
    # СЛУЧАЙ B: ВЕРДИКТ WAIT
    # ========================================================================
    
    else:
        logger.info("⚖️ Gemini рекомендует WAIT")
        
        # Обновляем кулдаун WAIT
        db_service.update_last_wait_time()
        
        # Сохраняем WAIT в БД
        try:
            signal_id = db_service.save_signal(signal_data_db)
            logger.info(f"💾 WAIT сохранен в БД (ID: {signal_id})")
        except Exception as e:
            logger.error(f"❌ Ошибка сохранения WAIT в БД: {e}")
        
        # Отправляем отчёт
        status_data['status'] = 'wait_decision'
        status_data['reason'] = 'Gemini не видит четкого сетапа. Ожидание.'
        status_data['llm_verdict'] = ai_response
        send_debug_notification(status_data)


def start_watcher():
    """
    Инициализация наблюдателя
    """
    logger.info("🛰 Astra Watcher v2.3 инициализирован (с жёстким фиксом зон)")


if __name__ == "__main__":
    logger.info("🧪 Ручной запуск анализа...")
    run_analysis_cycle()
