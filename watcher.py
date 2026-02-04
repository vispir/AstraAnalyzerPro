"""
Astra Watcher v8.0 - SMC-Correct Zone Logic
============================================
КРИТИЧЕСКОЕ ИСПРАВЛЕНИЕ v8.0:
- ИСПРАВЛЕНА логика зон Premium/Discount!
- Старая (НЕПРАВИЛЬНАЯ) логика:
  * DOWNTREND + DISCOUNT = запрет SELL ❌
  * UPTREND + PREMIUM = запрет BUY ❌
- Новая (ПРАВИЛЬНАЯ SMC) логика:
  * DOWNTREND + DISCOUNT = идеально для SELL (продолжение тренда) ✅
  * UPTREND + PREMIUM = идеально для BUY (продолжение тренда) ✅
  * DOWNTREND + PREMIUM = идеально для SELL (откат) ✅
  * UPTREND + DISCOUNT = идеально для BUY (откат) ✅

SMC SWEET SPOTS (идеальные сетапы):
- UPTREND + DISCOUNT = BUY на откате 🎯
- DOWNTREND + PREMIUM = SELL на откате 🎯

COUNTER-TREND ЗАЩИТА:
- Для торговли против тренда ТРЕБУЕТСЯ CHoCH (смена характера)
- Без CHoCH → только торговля по тренду

v7.5.2 СОХРАНЕНО:
- Парсинг LLM через extract_llm_verdict
- Умный кулдаун с override
- Confirmed сигналы для торговли
"""

import os
import math
import html
from datetime import datetime, timedelta, timezone
import logging
import json
from services.db_service import db_service
from services.telegram_service import telegram_service

# ============================================================================
# КОНСТАНТЫ v7.5.2 SMART COOLDOWN
# ============================================================================

SIGNAL_COOLDOWN_HOURS = 2       # После BUY/SELL
WAIT_COOLDOWN_HOURS = 0.5       # v7.5.2: 30 минут после WAIT (было 1 час)
FRESH_SIGNAL_BARS = 25
LOOKBACK_BARS = 600  # Увеличено до 600 для правильного Price Discovery (нужно найти пивоты до 331 баров назад)
EXTREME_DISCOUNT_THRESHOLD = 15.0
EXTREME_PREMIUM_THRESHOLD = 85.0

# v7.5.2: Smart Cooldown Override - критерии для игнорирования кулдауна
OVERRIDE_MIN_CONFIRMED = 2      # Минимум confirmed для override
OVERRIDE_MIN_IMPULSE = 80       # Минимальная сила импульса для override (%)

SWING_STRONG_SETUPS = ['SWING_BOS', 'SWING_CHOCH']
INTERNAL_STRONG_SETUPS = ['INT_BOS', 'INT_CHOCH', 'OB_RETEST']
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

def escape_html(text):
    """Экранирует HTML спецсимволы для Telegram"""
    if text is None:
        return ""
    return html.escape(str(text))


def safe_float(value, default=0.0):
    try:
        result = float(value) if value is not None else default
        if math.isnan(result) or math.isinf(result):
            return default
        return result
    except (TypeError, ValueError):
        return default


def is_market_active():
    now = datetime.now(timezone.utc)
    weekday = now.weekday()
    hour = now.hour
    
    if weekday == 5:
        return False
    
    if weekday == 6 and hour < 23:
        return False
    
    if weekday == 4 and hour >= 22:
        return False
    
    if weekday in [0, 1, 2, 3] and hour == 22:
        return False
    
    return True


def is_news_blockactive():
    if not news_service:
        return False
    try:
        upcoming_news = news_service.get_upcoming_news(hours=2, currencies=['USD'], impact=['High'])
        past_news = news_service.get_past_news(hours=1)
        now_ts = int(datetime.now().timestamp())
        
        for event in upcoming_news:
            ts = event.get('timestamp')
            if ts and (ts - now_ts) < (45 * 60):
                return True
        
        for event in past_news:
            ts = event.get('timestamp')
            if ts and (now_ts - ts) < (15 * 60):
                return True
    except Exception as e:
        logger.error(f"Ошибка новостей: {e}")
    return False


def check_smart_cooldown():
    try:
        now = datetime.now(timezone.utc)
        
        last_trade = db_service.get_last_trade_signal_time()
        if (now - last_trade).total_seconds() < (SIGNAL_COOLDOWN_HOURS * 3600):
            return False

        last_wait = db_service.get_last_wait_time()
        if (now - last_wait).total_seconds() < (WAIT_COOLDOWN_HOURS * 3600):
            return False
        
        return True
    except Exception as e:
        logger.error(f"Ошибка проверки кулдауна: {e}")
        return True


def calculate_forced_zones(candles):
    """Расчёт зон по max/min за все свечи"""
    all_highs = [c['high'] for c in candles]
    all_lows = [c['low'] for c in candles]
    
    global_high = max(all_highs)
    global_low = min(all_lows)
    current_close = candles[-1]['close']
    
    if global_high == global_low:
        position_pct = 50.0
    else:
        position_pct = ((current_close - global_low) / (global_high - global_low)) * 100
    
    if position_pct < 33.3:
        forced_zone = "DISCOUNT"
    elif position_pct > 66.6:
        forced_zone = "PREMIUM"
    else:
        forced_zone = "EQUILIBRIUM"
    
    return forced_zone, position_pct, global_high, global_low


def is_price_near_smc_structure(current_price, analysis, threshold_percent=0.5):
    threshold = current_price * (threshold_percent / 100)
    near_structures = []
    
    for ob in analysis.get('order_blocks', []):
        ob_top = ob.get('top', 0)
        ob_bottom = ob.get('bottom', 0)
        if ob_bottom - threshold <= current_price <= ob_top + threshold:
            near_structures.append(f"{ob.get('type', 'OB')} [{ob_bottom:.2f}-{ob_top:.2f}]")
    
    for fvg in analysis.get('fvg', []):
        fvg_top = fvg.get('top', 0)
        fvg_bottom = fvg.get('bottom', 0)
        if fvg_bottom - threshold <= current_price <= fvg_top + threshold:
            near_structures.append(f"{fvg.get('type', 'FVG')} [{fvg_bottom:.2f}-{fvg_top:.2f}]")
    
    for liq in analysis.get('liquidity', []):
        liq_price = liq.get('price', 0)
        if abs(current_price - liq_price) <= threshold:
            near_structures.append(f"{liq.get('type', 'LEVEL')} @ {liq_price:.2f}")
    
    if near_structures:
        return True, ", ".join(near_structures[:3])
    
    return False, "Нет близких SMC структур"


# ============================================================================
# ПАРСИНГ И ФОРМАТИРОВАНИЕ
# ============================================================================

def parse_llm_response(ai_response):
    try:
        start = ai_response.find('{')
        end = ai_response.rfind('}') + 1
        if start >= 0 and end > start:
            json_str = ai_response[start:end]
            return json.loads(json_str)
    except Exception as e:
        logger.error(f"Ошибка парсинга LLM: {e}")
    return None


def extract_llm_verdict(parsed):
    """
    Извлекает action и trade данные из ответа LLM.
    Поддерживает оба формата: signal.action + trade_plan и старый ACTION/ENTRY/SL/TP.
    
    ВАЖНО: Если confidence < 50, сигнал автоматически преобразуется в WAIT!
    """
    if not parsed or not isinstance(parsed, dict):
        return {'action': 'WAIT', 'entry': None, 'sl': None, 'tp': None, 'confidence': 0, 
                'reason': '', 'low_confidence_override': False, 'setup_grade': None, 'setup_type': None}
    
    signal = parsed.get('signal') or {}
    trade_plan = parsed.get('trade_plan') or {}
    math_log = parsed.get('math_debug_log') or {}
    
    action = (signal.get('action') or parsed.get('ACTION', 'WAIT')).upper()
    if action not in ('BUY', 'SELL', 'WAIT'):
        action = 'WAIT'
    
    entry = trade_plan.get('final_entry') or math_log.get('entry_price') or parsed.get('ENTRY')
    sl = trade_plan.get('final_sl') or math_log.get('buffered_stop_loss') or parsed.get('SL')
    tp = trade_plan.get('final_tp') or math_log.get('target_price') or parsed.get('TP')
    
    confidence = signal.get('confidence') or parsed.get('CONFIDENCE') or 0
    try:
        confidence = int(confidence) if confidence is not None else 0
    except (TypeError, ValueError):
        confidence = 0
    
    # Грейд и тип сетапа
    setup_grade = signal.get('setup_grade', None)
    setup_type = signal.get('setup_type', None)
    
    reason = parsed.get('executive_summary') or parsed.get('REASON', '')
    
    # КРИТИЧЕСКАЯ ПРОВЕРКА: если confidence < 50, преобразуем в WAIT
    low_confidence_override = False
    original_action = action
    
    if action in ('BUY', 'SELL') and confidence < 50:
        logger.warning(f"⚠️ LOW CONFIDENCE OVERRIDE: {action} → WAIT (confidence={confidence}%)")
        low_confidence_override = True
        action = 'WAIT'
        reason = f"[LOW CONFIDENCE: {confidence}%] Оригинальный сигнал: {original_action}. {reason}"
    
    return {
        'action': action, 
        'entry': entry, 
        'sl': sl, 
        'tp': tp, 
        'confidence': confidence, 
        'reason': str(reason)[:500],
        'low_confidence_override': low_confidence_override,
        'original_action': original_action if low_confidence_override else None,
        'setup_grade': setup_grade,
        'setup_type': setup_type
    }


def extract_executive_summary(ai_response):
    parsed = parse_llm_response(ai_response)
    if parsed and 'executive_summary' in parsed:
        return parsed['executive_summary']
    
    cleaned = ai_response.replace('```json', '').replace('```', '').strip()
    return cleaned[:197] + '...' if len(cleaned) > 200 else cleaned


def format_signal_message_with_verdict(ai_response, verdict):
    """
    Форматирует сигнал с уже готовым verdict (для передачи low_confidence_override)
    """
    parsed_data = parse_llm_response(ai_response)
    return _format_signal_internal(parsed_data, verdict, ai_response)


def format_signal_message(ai_response):
    """
    Форматирует сигнал от LLM в красивое сообщение для Telegram.
    Включает ВСЕ данные: signal, trade_plan, math_debug_log, wait_metadata
    """
    parsed_data = parse_llm_response(ai_response)
    verdict = extract_llm_verdict(parsed_data)
    return _format_signal_internal(parsed_data, verdict, ai_response)


def _format_signal_internal(parsed_data, verdict, ai_response=''):
    """
    Внутренняя функция форматирования сигнала.
    ai_response — сырой ответ LLM для fallback при неудачном парсинге.
    """
    
    if verdict and verdict['action'] in ['BUY', 'SELL']:
        action = verdict['action']
        emoji = "🟢 BUY (ПОКУПКА)" if action == "BUY" else "🔴 SELL (ПРОДАЖА)"
        
        entry = escape_html(verdict['entry'] or 'N/A')
        sl = escape_html(verdict['sl'] or 'N/A')
        tp = escape_html(verdict['tp'] or 'N/A')
        confidence = verdict['confidence'] or 0
        reason = escape_html(verdict['reason'] or 'SMC Confirmation')
        
        # Получаем дополнительные данные
        signal_data = parsed_data.get('signal', {}) if parsed_data else {}
        trade_plan = parsed_data.get('trade_plan', {}) if parsed_data else {}
        math_log = parsed_data.get('math_debug_log', {}) if parsed_data else {}
        
        setup_type = escape_html(signal_data.get('setup_type', 'N/A'))
        setup_grade = escape_html(signal_data.get('setup_grade', 'N/A'))
        tp_logic = escape_html(trade_plan.get('tp_logic', 'N/A'))
        invalidation = escape_html(trade_plan.get('invalidation_condition', 'N/A'))
        
        # Эмодзи для грейда сетапа
        grade_emoji = {
            'A+': '🏆', 'A': '⭐', 'B+': '✅', 'B': '👍'
        }.get(setup_grade, '📊')
        
        # Расчёт R:R
        rr_text = ""
        risk_reward = 0
        try:
            entry_f = float(entry)
            sl_f = float(sl)
            tp_f = float(tp)
            risk = abs(entry_f - sl_f)
            reward = abs(tp_f - entry_f)
            if risk > 0:
                risk_reward = reward / risk
                rr_text = f"<b>1:{risk_reward:.2f}</b>"
        except:
            rr_text = "N/A"
        
        # Уверенность с эмодзи
        conf_emoji = "🟢" if int(confidence) >= 70 else "🟡" if int(confidence) >= 50 else "🔴"
        
        # Math Debug Log
        math_section = ""
        if math_log:
            math_section = (
                f"\n<b>📐 Math Debug:</b>\n"
                f"├ Risk: <code>${math_log.get('risk_amount', 0):.2f}</code>\n"
                f"├ Reward: <code>${math_log.get('reward_amount', 0):.2f}</code>\n"
                f"└ Calculated R:R: <code>{math_log.get('calculated_rr', 0):.2f}</code>\n"
            )
        
        msg = (
            f"<b>🚀 ASTRA SIGNAL: GOLD (XAU/USD)</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"<b>📊 СИГНАЛ:</b>\n"
            f"├ Направление: <b>{emoji}</b>\n"
            f"├ {grade_emoji} Грейд: <b>{setup_grade}</b>\n"
            f"├ Сетап: <code>{setup_type}</code>\n"
            f"└ {conf_emoji} Уверенность: <b>{confidence}%</b>\n\n"
            f"<b>🎯 ТОРГОВЫЙ ПЛАН:</b>\n"
            f"├ Вход: <code>${entry}</code>\n"
            f"├ Стоп-лосс: <code>${sl}</code>\n"
            f"├ Тейк-профит: <code>${tp}</code>\n"
            f"├ R:R: {rr_text}\n"
            f"├ TP логика: <code>{tp_logic}</code>\n"
            f"└ Инвалидация: <i>{invalidation}</i>\n"
            f"{math_section}\n"
            f"<b>📝 SMC АНАЛИЗ:</b>\n{reason}\n\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"<i>⚠️ Не является финансовым советом</i>"
        )
        return msg
    
    elif verdict and verdict['action'] == 'WAIT':
        # Форматируем WAIT сигнал с полными данными
        reason = escape_html(verdict['reason'] or 'Ожидание лучшей возможности')
        confidence = verdict.get('confidence', 0)
        low_conf_override = verdict.get('low_confidence_override', False)
        original_action = escape_html(verdict.get('original_action'))
        
        wait_metadata = parsed_data.get('wait_metadata', {}) if parsed_data else {}
        
        trigger = escape_html(wait_metadata.get('trigger_condition', 'N/A'))
        wait_time = escape_html(wait_metadata.get('estimated_wait_time', 'N/A'))
        wait_code = escape_html(wait_metadata.get('wait_reason_code', 'N/A'))
        potential_dir = escape_html(wait_metadata.get('potential_direction', 'UNCLEAR'))
        
        # Если был low confidence override, показываем оригинальный сигнал
        if low_conf_override and original_action:
            potential_dir = original_action
            wait_code = "LOW_CONFIDENCE"
            trigger = f"Уверенность должна быть >= 50% (сейчас {confidence}%)"
        
        # Эмодзи для направления
        dir_emoji = "🟢" if potential_dir == "BUY" else "🔴" if potential_dir == "SELL" else "⚪"
        
        # Заголовок зависит от причины
        header = "⚠️ ASTRA: LOW CONFIDENCE" if low_conf_override else "⏳ ASTRA SIGNAL: WAIT"
        
        # Confidence bar
        conf_bar = ""
        if confidence > 0:
            filled = int(confidence / 10)
            empty = 10 - filled
            conf_bar = f"\n<b>📊 Уверенность:</b> {'█' * filled}{'░' * empty} <b>{confidence}%</b>\n"
        
        msg = (
            f"<b>{header}</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"<b>📋 СТАТУС:</b> Ожидание\n"
            f"<b>🏷 Код:</b> <code>{wait_code}</code>\n"
            f"{conf_bar}\n"
            f"<b>📝 ПРИЧИНА:</b>\n{reason}\n\n"
            f"<b>🎯 УСЛОВИЯ ДЛЯ ВХОДА:</b>\n"
            f"├ Триггер: <i>{trigger}</i>\n"
            f"├ Ожидание: <code>{wait_time}</code>\n"
            f"└ {dir_emoji} Потенциал: <b>{potential_dir}</b>\n\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"<i>⏱ Следующая проверка через 15 мин</i>"
        )
        return msg
    
    # Если не удалось распарсить — выводим как есть
    raw = ai_response if ai_response else (str(parsed_data) if parsed_data else 'Raw response unavailable')
    return f"<b>📢 НОВЫЙ СИГНАЛ XAUUSD:</b>\n\n{escape_html(raw)}"


def format_debug_report(status_data):
    status_emoji = {
        'market_closed': '💤', 'news_block': '📰', 'oanda_error': '🔌',
        'no_smc': '⚙️', 'not_near_structure': '🔍', 'equilibrium_zone': '⚪',
        'weak_patterns': '📉', 'neutral_no_swing': '⚖️', 'cooldown': '⏳',
        'signal_sent': '✅', 'wait_decision': '⚖️',
        'impulse_override': '⚡', 'reversal_mode': '🔄',
        'smc_sweet_spot': '🎯',  # v8.0: Идеальный SMC сетап
        'no_confirmed_signal': '⏳',  # v6.0: Нет уверенного пробоя
        'impulse_no_confirmation': '⚠️',  # v7.5.2: Impulse/Reversal без internal confirmed
        'low_confidence_wait': '📉'  # v7.6: Низкая уверенность (< 50%)
    }
    
    status_texts = {
        'market_closed': 'Рынок закрыт',
        'news_block': 'Блокировка по новостям',
        'oanda_error': 'Ошибка OANDA',
        'no_smc': 'SMC детектор недоступен',
        'not_near_structure': 'SKIP - Цена далеко от структур',
        'equilibrium_zone': 'SKIP - Зона Equilibrium',
        'weak_patterns': 'SKIP - Нет сильных паттернов',
        'neutral_no_swing': 'SKIP - Нейтральный тренд без Swing',
        'cooldown': 'SKIP - Активен кулдаун',
        'signal_sent': '🎯 ТОРГОВЫЙ СИГНАЛ!',
        'wait_decision': 'LLM рекомендует WAIT',
        'impulse_override': '⚡ IMPULSE MODE: Сильный импульс',
        'reversal_mode': '🔄 REVERSAL MODE: Поиск разворота',
        'smc_sweet_spot': '🎯 SMC SWEET SPOT: Идеальный сетап',  # v8.0
        'no_confirmed_signal': 'SKIP - Нет CONFIRMED пробоя (LLM не вызван)',  # v6.0
        'impulse_no_confirmation': 'SKIP - Impulse/Reversal без internal confirmed',  # v7.5.2
        'low_confidence_wait': '📉 LOW CONFIDENCE: Сигнал отклонён (< 50%)'  # v7.6
    }
    
    status = status_data.get('status', 'unknown')
    emoji = status_emoji.get(status, '❓')
    
    now_utc = datetime.now(timezone.utc)
    msg = f"<b>{emoji} ASTRA WATCHER v8.0</b>\n"
    msg += f"<code>UTC: {now_utc.strftime('%Y-%m-%d %H:%M:%S')}</code>\n"
    msg += "━" * 32 + "\n\n"
    
    # v7.5.2: Показываем если был cooldown override
    if status_data.get('cooldown_override'):
        msg += "<b>🔓 COOLDOWN OVERRIDE:</b>\n"
        for reason in status_data.get('override_reasons', []):
            msg += f"└ {escape_html(reason)}\n"
        msg += "\n"
    
    msg += f"<b>📋 Решение:</b> {status_texts.get(status, 'Неизвестно')}\n\n"
    
    if status_data.get('price', 0) > 0:
        msg += "<b>💹 Рыночные данные:</b>\n"
        msg += f"├ Цена: <code>${status_data['price']:.2f}</code>\n"
        
        if 'trend' in status_data:
            trend_emoji = "📈" if "UP" in status_data['trend'] else "📉" if "DOWN" in status_data['trend'] else "↔️"
            msg += f"├ Swing Тренд: {trend_emoji} {escape_html(status_data['trend'])}\n"
        
        if 'internal_trend' in status_data:
            int_trend = status_data['internal_trend']
            int_emoji = "📈" if "UP" in int_trend else "📉" if "DOWN" in int_trend else "↔️"
            msg += f"├ Internal Тренд: {int_emoji} {escape_html(int_trend)}\n"
        
        if 'zone' in status_data:
            zone = status_data['zone']
            zone_emoji = "🔴" if zone == "PREMIUM" else "🟢" if zone == "DISCOUNT" else "⚪"
            msg += f"├ Зона: {zone_emoji} {escape_html(zone)}\n"
        
        if 'position_in_range_pct' in status_data:
            msg += f"└ Позиция: {status_data['position_in_range_pct']:.1f}% диапазона\n\n"
    
    if 'global_high' in status_data and 'global_low' in status_data:
        zone_source = status_data.get('zone_source', 'FALLBACK')
        if zone_source == 'SWING_STRUCTURE':
            msg += f"<b>📐 Диапазон (Price Discovery):</b>\n"
        else:
            msg += f"<b>📐 Диапазон {LOOKBACK_BARS} свечей:</b>\n"
        msg += f"├ High: ${status_data['global_high']:.2f}\n"
        msg += f"└ Low: ${status_data['global_low']:.2f}\n\n"
    
    # Impulse Context v5.2
    if 'impulse_context' in status_data:
        ic = status_data['impulse_context']
        msg += "<b>⚡ Impulse Context v5.2:</b>\n"
        msg += f"├ Режим: {escape_html(ic.get('market_condition', 'N/A'))}\n"
        msg += f"├ Breakout: {'✅' if ic.get('has_breakout') else '❌'}\n"
        msg += f"├ Void Run: {'✅' if ic.get('is_void_run') else '❌'}\n"
        msg += f"├ Impulse: {'✅' if ic.get('is_impulse') else '❌'} ({ic.get('impulse_strength', 0)}%)\n"
        if ic.get('override_reason'):
            msg += f"└ 🔓 {escape_html(ic['override_reason'])}\n"
        msg += "\n"
    
    if 'smc_summary' in status_data:
        smc = status_data['smc_summary']
        msg += "<b>📊 SMC Паттерны v8.0:</b>\n"
        msg += f"├ Order Blocks: {smc.get('ob', 0)}\n"
        msg += f"├ Fair Value Gaps: {smc.get('fvg', 0)}\n"
        msg += f"├ Swing BOS: {smc.get('swing_bos_total', 0)} (All) | ✅ Confirmed: {smc.get('swing_bos_confirmed', 0)}\n"
        msg += f"├ Swing CHoCH: {smc.get('swing_choch_total', 0)} (All) | ✅ Confirmed: {smc.get('swing_choch_confirmed', 0)}\n"
        msg += f"├ Int BOS: {smc.get('int_bos_total', 0)} | ✅ Confirmed: {smc.get('int_bos_confirmed', 0)}\n"
        msg += f"├ Int CHoCH: {smc.get('int_choch_total', 0)} | ✅ Confirmed: {smc.get('int_choch_confirmed', 0)}\n"
        msg += f"└ <b>CONFIRMED TOTAL: {smc.get('confirmed_total', 0)}</b>\n\n"
    
    if 'swing_signals' in status_data and status_data['swing_signals']:
        msg += f"<b>🎯 Swing сигналы:</b> {escape_html(', '.join(status_data['swing_signals'][:5]))}\n"
    
    if 'internal_signals' in status_data and status_data['internal_signals']:
        msg += f"<b>📍 Internal сигналы:</b> {escape_html(', '.join(status_data['internal_signals'][:5]))}\n\n"
    
    if 'near_structures' in status_data:
        msg += f"<b>🎯 Уровни рядом:</b>\n{escape_html(status_data['near_structures'])}\n\n"
    
    if 'reason' in status_data:
        msg += f"<b>💡 Детали:</b>\n<i>{escape_html(status_data['reason'])}</i>\n\n"
    
    # Confidence информация (если есть)
    if 'confidence' in status_data:
        conf = status_data['confidence']
        conf_bar = '█' * int(conf / 10) + '░' * (10 - int(conf / 10))
        conf_emoji = "🟢" if conf >= 70 else "🟡" if conf >= 50 else "🔴"
        msg += f"<b>{conf_emoji} Confidence:</b> {conf_bar} <b>{conf}%</b>\n"
        
        if status_data.get('original_action'):
            msg += f"<b>⚠️ Оригинальный сигнал:</b> {escape_html(status_data['original_action'])} → WAIT\n"
        msg += "\n"
    
    if 'llm_verdict' in status_data:
        summary = extract_executive_summary(status_data['llm_verdict'])
        msg += f"<b>🤖 Gemini резюме:</b>\n<i>{escape_html(summary)}</i>\n\n"
    
    msg += "━" * 32 + "\n"
    msg += "<i>⏱ Следующая проверка через 15 минут</i>"
    
    return msg


def send_debug_notification(status_data):
    try:
        user_ids = db_service.get_all_active_users()
        if user_ids:
            message = format_debug_report(status_data)
            telegram_service.broadcast_signal(user_ids, message)
            logger.info(f"📤 Debug отчет отправлен ({len(user_ids)} пользователей)")
    except Exception as e:
        logger.error(f"❌ Debug Error: {e}")


# ============================================================================
# СБОР СИГНАЛОВ
# ============================================================================

def collect_signals_by_type(analysis):
    swing_signals = []
    internal_signals = []
    
    for bos in analysis.get('swing_bos', []):
        bos_type = bos.get('type', 'BOS')
        bars_ago = bos.get('bars_ago', 0)
        swing_signals.append(f"SWING_BOS ({bos_type}, {bars_ago} bars ago)")
    
    for choch in analysis.get('swing_choch', []):
        choch_type = choch.get('type', 'CHOCH')
        bars_ago = choch.get('bars_ago', 0)
        swing_signals.append(f"SWING_CHOCH ({choch_type}, {bars_ago} bars ago)")
    
    for ob in analysis.get('order_blocks_swing', []):
        swing_signals.append(f"SWING_OB ({ob.get('type', 'OB')})")
    
    for bos in analysis.get('internal_bos', []):
        bos_type = bos.get('type', 'BOS')
        bars_ago = bos.get('bars_ago', 0)
        internal_signals.append(f"INT_BOS ({bos_type}, {bars_ago} bars ago)")
    
    for choch in analysis.get('internal_choch', []):
        choch_type = choch.get('type', 'CHOCH')
        bars_ago = choch.get('bars_ago', 0)
        internal_signals.append(f"INT_CHOCH ({choch_type}, {bars_ago} bars ago)")
    
    for ob in analysis.get('order_blocks_internal', []):
        internal_signals.append(f"INT_OB ({ob.get('type', 'OB')})")
    
    for fvg in analysis.get('fvg', []):
        internal_signals.append(f"FVG ({fvg.get('type', 'FVG')})")
    
    if analysis.get('eqh'):
        internal_signals.append('EQH_SWEEP')
    if analysis.get('eql'):
        internal_signals.append('EQL_SWEEP')
    
    return swing_signals, internal_signals, swing_signals + internal_signals


def get_signal_label(action):
    if action == 'BUY':
        return "🟢 ПОКУПКА"
    elif action == 'SELL':
        return "🔴 ПРОДАЖА"
    else:
        return "⚖️ ОЖИДАНИЕ"


def prepare_signal_data_for_db(llm_action, parsed_llm, ai_response, current_price,
                                trend, internal_trend, zone,
                                swing_signals, internal_signals, smc_summary):
    all_patterns = swing_signals + internal_signals
    patterns_list = list(all_patterns) if all_patterns else []
    signal_label = get_signal_label(llm_action)
    
    # Извлекаем данные через универсальный парсер (поддержка ОБОИХ форматов)
    verdict = extract_llm_verdict(parsed_llm)
    
    entry_price = safe_float(verdict['entry'] or current_price, safe_float(current_price, 0.0))
    stop_loss = safe_float(verdict['sl'], 0.0)
    take_profit = safe_float(verdict['tp'], 0.0)
    confidence = verdict['confidence'] or 0
    reason = verdict['reason'] or ""
    
    return {
        'symbol': 'XAU_USD',
        'signal_type': str(llm_action),
        'signal_label': signal_label,
        'status': 'active',
        'entry_price': safe_float(entry_price, 0.0),
        'current_price': safe_float(current_price, 0.0),
        'stop_loss': safe_float(stop_loss, 0.0),
        'take_profit': safe_float(take_profit, 0.0),
        'trend': str(trend) if trend else 'NEUTRAL',
        'internal_trend': str(internal_trend) if internal_trend else 'NEUTRAL',
        'zone': str(zone) if zone else 'UNKNOWN',
        'patterns': patterns_list,
        'smc_summary': dict(smc_summary) if isinstance(smc_summary, dict) else {},
        'llm_full_response': str(ai_response)[:2000] if ai_response else '',
        'llm_reason': reason,
        'llm_confidence': confidence
    }


# ============================================================================
# ГЛАВНЫЙ ЦИКЛ АНАЛИЗА v5.2
# ============================================================================

def run_analysis_cycle():
    """
    Astra Watcher v8.0 SMC-Correct Zone Logic
    
    НОВОЕ v8.0:
    - Исправлена логика зон Premium/Discount под SMC
    - SMC Sweet Spots: UPTREND+DISCOUNT=BUY, DOWNTREND+PREMIUM=SELL
    - Counter-trend защита: требуется CHoCH для торговли против тренда
    
    v7.5.2 СОХРАНЕНО:
    - Умный кулдаун с override при критических событиях
    - Проверка confirmed=True в каждом сигнале
    - Защита от wick breaks
    """
    logger.info("📡 [TRIGGER] Цикл анализа v8.0 запущен")
    
    # ========================================================================
    # ФАЗА 1: ТЕХНИЧЕСКАЯ ПОДГОТОВКА
    # ========================================================================
    
    if not is_market_active():
        now_utc = datetime.now(timezone.utc)
        weekday = now_utc.weekday()
        hour = now_utc.hour
        
        if weekday in [0, 1, 2, 3] and hour == 22:
            reason = '⏸ Rollover час (22:00-23:00 UTC)'
        elif weekday == 5:
            reason = 'Суббота - рынок закрыт'
        elif weekday == 6 and hour < 23:
            reason = f'Воскресенье - откроется в 23:00 UTC'
        elif weekday == 4 and hour >= 22:
            reason = 'Пятница после 22:00 - рынок закрыт'
        else:
            reason = 'Рынок закрыт'
        
        send_debug_notification({'status': 'market_closed', 'reason': reason})
        return
    
    if is_news_blockactive():
        send_debug_notification({
            'status': 'news_block',
            'reason': 'Важные новости USD'
        })
        return
    
    data = oanda_service.get_candles(timeframe='M15', limit=LOOKBACK_BARS)
    if "error" in data:
        send_debug_notification({
            'status': 'oanda_error',
            'reason': f'Ошибка OANDA: {data.get("error", "Unknown")}'
        })
        return
    
    candles = data.get("candles", [])
    if not candles:
        send_debug_notification({'status': 'oanda_error', 'reason': 'Пустой массив свечей'})
        return
    
    if not smc_detector:
        send_debug_notification({'status': 'no_smc', 'reason': 'SMC детектор недоступен'})
        return
    
    # ========================================================================
    # SMC АНАЛИЗ
    # ========================================================================
    
    logger.info("🔬 Выполняем SMC анализ v8.0...")
    analysis = smc_detector.analyze(candles)
    
    # ========================================================================
    # v8.1: Используем зоны из нового детектора (Price Discovery логика)
    # ========================================================================
    # Новый детектор уже рассчитывает правильные зоны через trailing extremes
    # Используем эти данные вместо пересчета через calculate_forced_zones()
    advanced = analysis.get('advanced', {})
    zones = advanced.get('zones', {})
    key_levels = advanced.get('key_levels', {})
    
    # Получаем текущую цену для валидации диапазона
    current_price = safe_float(candles[-1].get('close', 0), 0.0)
    
    # ДИАГНОСТИКА: Логируем, что пришло из детектора
    if zones:
        logger.info(f"🔍 ДАННЫЕ ИЗ ДЕТЕКТОРА: zones.range_high={zones.get('range_high', 0):.2f}, zones.range_low={zones.get('range_low', 0):.2f}, zones.range_source={zones.get('range_source', 'NONE')}")
    else:
        logger.warning(f"⚠️ zones пустой или отсутствует в advanced!")
    
    # Также проверяем swing_pivot_high/low напрямую из market_structure
    market_structure = analysis.get('swing_pivot_high', 0), analysis.get('swing_pivot_low', 0)
    if market_structure[0] > 0 or market_structure[1] > 0:
        logger.info(f"🔍 MARKET_STRUCTURE: swing_pivot_high={market_structure[0]:.2f}, swing_pivot_low={market_structure[1]:.2f}")
    
    # Приоритет: zones -> key_levels -> fallback на calculate_forced_zones
    zone_source = 'FALLBACK'  # По умолчанию
    zones_valid = False
    
    if zones and zones.get('range_high', 0) > 0 and zones.get('range_low', 0) > 0:
        global_high = zones.get('range_high', 0.0)
        global_low = zones.get('range_low', 0.0)
        range_size = global_high - global_low
        range_source_detector = zones.get('range_source', 'UNKNOWN')
        
        # ВАЖНО: Проверяем валидность диапазона
        # Если диапазон слишком узкий (< 0.5% от цены) или цена выходит за пределы, используем fallback
        min_valid_range = current_price * 0.005  # Минимум 0.5% от цены
        
        # Детальное логирование для диагностики
        logger.debug(f"🔍 Проверка диапазона из детектора: source={range_source_detector}, range=[{global_low:.2f} - {global_high:.2f}], size={range_size:.2f}, price={current_price:.2f}")
        logger.debug(f"🔍 Валидация: min_valid={min_valid_range:.2f}, size_ok={range_size >= min_valid_range}, price_in_range={global_low <= current_price <= global_high}")
        
        if range_size >= min_valid_range and global_low <= current_price <= global_high:
            # Диапазон валидный - используем данные из нового детектора
            current_zone = zones.get('current_zone', 'UNKNOWN')
            # ВАЖНО: Пересчитываем position_in_range_pct на основе текущей цены и валидированного диапазона
            # Это гарантирует правильность, даже если детектор вернул некорректное значение
            position_in_range_pct = ((current_price - global_low) / range_size) * 100
            zone_source = range_source_detector
            zones_valid = True
            logger.info(f"✅ Используем зоны из нового детектора ({zone_source}): [{global_low:.2f} - {global_high:.2f}], price={current_price:.2f}, pos={position_in_range_pct:.1f}%")
        else:
            # Диапазон невалидный - используем fallback
            reason = []
            if range_size < min_valid_range:
                reason.append(f"слишком узкий ({range_size:.2f} < {min_valid_range:.2f})")
            if current_price < global_low:
                reason.append(f"цена ниже диапазона ({current_price:.2f} < {global_low:.2f})")
            if current_price > global_high:
                reason.append(f"цена выше диапазона ({current_price:.2f} > {global_high:.2f})")
            logger.warning(f"⚠️ Диапазон из детектора невалидный ({', '.join(reason)}), используем fallback")
    
    if not zones_valid:
        # Пробуем fallback на key_levels
        if key_levels and key_levels.get('High_250', 0) > 0:
            # Fallback на key_levels
            current_zone = key_levels.get('Current_Zone', 'UNKNOWN')
            global_high = key_levels.get('High_250', 0.0)
            global_low = key_levels.get('Low_250', 0.0)
            # ВАЖНО: Пересчитываем position_in_range_pct на основе текущей цены
            range_size_fallback = global_high - global_low
            if range_size_fallback > 0:
                position_in_range_pct = ((current_price - global_low) / range_size_fallback) * 100
            else:
                position_in_range_pct = 50.0
            zone_source = 'KEY_LEVELS'
            logger.info(f"⚠️ Используем зоны из key_levels (fallback): [{global_low:.2f} - {global_high:.2f}], pos={position_in_range_pct:.1f}%")
        else:
            # Последний fallback на старую логику (для обратной совместимости)
            current_zone, position_in_range_pct, global_high, global_low = calculate_forced_zones(candles)
            zone_source = 'CALCULATE_FORCED'
            logger.warning(f"⚠️ Используем старую логику calculate_forced_zones (fallback): [{global_low:.2f} - {global_high:.2f}], pos={position_in_range_pct:.1f}%")
    
    # ВАЖНО: Гарантируем, что position_in_range_pct находится в разумных пределах (0-100%)
    # Это защита от некорректных значений из детектора или fallback
    if position_in_range_pct < 0:
        logger.warning(f"⚠️ position_in_range_pct < 0 ({position_in_range_pct:.1f}%), ограничиваем до 0%")
        position_in_range_pct = 0.0
    elif position_in_range_pct > 100:
        logger.warning(f"⚠️ position_in_range_pct > 100 ({position_in_range_pct:.1f}%), ограничиваем до 100%")
        position_in_range_pct = 100.0
    
    swing_trend = analysis.get('trend', 'NEUTRAL')
    internal_trend = analysis.get('internal_trend', 'NEUTRAL')
    
    # Impulse Context v5.2
    impulse_context = analysis.get('impulse_context', {})
    has_breakout = impulse_context.get('has_breakout', False)
    is_void_run = impulse_context.get('is_void_run', False)
    is_impulse = impulse_context.get('is_impulse', False)
    market_condition = impulse_context.get('market_condition', 'RANGING')
    impulse_strength = impulse_context.get('impulse_strength', 0)
    override_reason = impulse_context.get('override_reason', '')
    
    logger.info(f"🔧 ЗОНЫ ({zone_source}): {current_zone} ({position_in_range_pct:.1f}%) | Range: [{global_low:.2f} - {global_high:.2f}]")
    logger.info(f"⚡ IMPULSE: breakout={has_breakout}, void_run={is_void_run}, impulse={is_impulse}, condition={market_condition}")
    
    # Сбор сигналов
    swing_signals, internal_signals, all_signals = collect_signals_by_type(analysis)
    
    # SMC Summary v6.0
    smc_summary = {
        'ob': len(analysis.get('order_blocks', [])),
        'fvg': len(analysis.get('fvg', [])),
        # Свежие (для визуализации)
        'swing_bos': len(analysis.get('swing_bos', [])),
        'swing_choch': len(analysis.get('swing_choch', [])),
        'int_bos': len(analysis.get('internal_bos', [])),
        'int_choch': len(analysis.get('internal_choch', [])),
        # Все (исторические)
        'swing_bos_total': len(analysis.get('all_swing_bos', [])),
        'swing_choch_total': len(analysis.get('all_swing_choch', [])),
        'int_bos_total': len(analysis.get('all_internal_bos', [])),
        'int_choch_total': len(analysis.get('all_internal_choch', [])),
        # v6.0: CONFIRMED (для торговли)
        'swing_bos_confirmed': len(analysis.get('swing_bos_confirmed', [])),
        'swing_choch_confirmed': len(analysis.get('swing_choch_confirmed', [])),
        'int_bos_confirmed': len(analysis.get('internal_bos_confirmed', [])),
        'int_choch_confirmed': len(analysis.get('internal_choch_confirmed', [])),
        'confirmed_total': analysis.get('confirmed_signals_count', 0)
    }
    confirmed_count = smc_summary.get('confirmed_total', 0)
    
    is_near, near_description = is_price_near_smc_structure(current_price, analysis, threshold_percent=0.5)
    
    # Базовый статус
    status_data = {
        'price': current_price,
        'trend': swing_trend,
        'internal_trend': internal_trend,
        'zone': current_zone,
        'position_in_range_pct': position_in_range_pct,
        'global_high': global_high,
        'global_low': global_low,
        'zone_source': zone_source,
        'swing_signals': swing_signals,
        'internal_signals': internal_signals,
        'smc_summary': smc_summary,
        'near_structures': near_description,
        'impulse_context': impulse_context,
        'status': 'unknown',
        'reason': ''
    }
    
    # ========================================================================
    # ФАЗА 2: ФИЛЬТРЫ GATEKEEPER v6.0 (CONFIRMED SIGNALS)
    # ========================================================================
    
    # v6.0: Для торговых решений используем ТОЛЬКО confirmed сигналы
    # confirmed = пробой ТЕЛОМ (close), не тенью + bars_ago <= 5
    
    # v7.5.2 FIX: Проверяем что сигналы действительно confirmed=True (не wick break!)
    swing_bos_confirmed_list = analysis.get('swing_bos_confirmed', [])
    swing_choch_confirmed_list = analysis.get('swing_choch_confirmed', [])
    int_bos_confirmed_list = analysis.get('internal_bos_confirmed', [])
    int_choch_confirmed_list = analysis.get('internal_choch_confirmed', [])
    
    # Фильтруем только РЕАЛЬНО confirmed (пробой телом)
    has_swing_bos_confirmed = any(s.get('confirmed', False) for s in swing_bos_confirmed_list)
    has_swing_choch_confirmed = any(s.get('confirmed', False) for s in swing_choch_confirmed_list)
    has_swing_break_confirmed = has_swing_bos_confirmed or has_swing_choch_confirmed
    
    has_int_bos_confirmed = any(s.get('confirmed', False) for s in int_bos_confirmed_list)
    has_int_choch_confirmed = any(s.get('confirmed', False) for s in int_choch_confirmed_list)
    has_internal_break_confirmed = has_int_bos_confirmed or has_int_choch_confirmed
    
    # Свежие сигналы (для визуализации и отладки, не для торговли)
    has_swing_bos = len(analysis.get('swing_bos', [])) > 0
    has_swing_choch = len(analysis.get('swing_choch', [])) > 0
    has_swing_break = has_swing_bos or has_swing_choch
    
    has_int_bos = len(analysis.get('internal_bos', [])) > 0
    has_int_choch = len(analysis.get('internal_choch', [])) > 0
    has_internal_break = has_int_bos or has_int_choch
    
    # Флаги для режимов
    is_breakout_impulse = False
    is_reversal_setup = False
    impulse_reasons = []
    
    logger.info(f"v8.0 Confirmed Signals (REAL confirmed=True): Swing BOS={has_swing_bos_confirmed}, CHoCH={has_swing_choch_confirmed} | "
                f"Internal BOS={has_int_bos_confirmed}, CHoCH={has_int_choch_confirmed}")
    
    # ========================================================================
    # v8.0 SMC-CORRECT ZONE LOGIC (ИСПРАВЛЕНО!)
    # ========================================================================
    # 
    # SMC правила:
    # ✅ UPTREND + DISCOUNT = Идеально для BUY (покупка на откате)
    # ✅ DOWNTREND + PREMIUM = Идеально для SELL (продажа на откате)  
    # ✅ UPTREND + PREMIUM = BUY продолжение (при наличии BOS)
    # ✅ DOWNTREND + DISCOUNT = SELL продолжение (при наличии BOS)
    #
    # ❌ COUNTER-TREND запреты:
    # ❌ DOWNTREND + PREMIUM + нет CHoCH = запрет BUY (покупка против тренда)
    # ❌ UPTREND + DISCOUNT + нет CHoCH = запрет SELL (продажа против тренда)
    # ========================================================================
    
    # DOWNTREND + DISCOUNT = ОТЛИЧНОЕ место для SELL (продолжение тренда)
    if swing_trend == "DOWNTREND" and current_zone == "DISCOUNT":
        # Это SMC-идеальное условие для продажи!
        impulse_reasons.append("✅ SMC: DOWNTREND + DISCOUNT = идеально для SELL")
        
        # Дополнительные подтверждения
        if has_breakout:
            is_breakout_impulse = True
            impulse_reasons.append("📉 Пробой минимума 20 свечей")
        
        if has_swing_bos_confirmed:
            is_breakout_impulse = True
            impulse_reasons.append("💥 CONFIRMED Swing BOS (телом)")
        
        if is_impulse:
            impulse_reasons.append(f"⚡ Импульс {impulse_strength}%")
        
        # Экстремальный дискаунт: ищем разворот вверх
        if position_in_range_pct < EXTREME_DISCOUNT_THRESHOLD:
            has_bullish_internal_choch = any(
                'BULLISH' in ch.get('type', '') 
                for ch in analysis.get('internal_choch_confirmed', [])
            )
            if has_bullish_internal_choch:
                is_reversal_setup = True
                impulse_reasons.append("🔄 Бычий Internal CHoCH — потенциальный разворот")
    
    # UPTREND + PREMIUM = ОТЛИЧНОЕ место для BUY (продолжение тренда)
    if swing_trend == "UPTREND" and current_zone == "PREMIUM":
        # Это SMC-идеальное условие для покупки при сильном тренде!
        impulse_reasons.append("✅ SMC: UPTREND + PREMIUM = продолжение тренда вверх")
        
        if has_breakout:
            is_breakout_impulse = True
            impulse_reasons.append("📈 Пробой максимума 20 свечей")
        
        if has_swing_bos_confirmed:
            is_breakout_impulse = True
            impulse_reasons.append("💥 CONFIRMED Swing BOS (телом)")
        
        if is_impulse:
            impulse_reasons.append(f"⚡ Импульс {impulse_strength}%")
        
        # Экстремальный премиум: ищем разворот вниз
        if position_in_range_pct > EXTREME_PREMIUM_THRESHOLD:
            has_bearish_internal_choch = any(
                'BEARISH' in ch.get('type', '')
                for ch in analysis.get('internal_choch_confirmed', [])
            )
            if has_bearish_internal_choch:
                is_reversal_setup = True
                impulse_reasons.append("🔄 Медвежий Internal CHoCH — потенциальный разворот")
    
    # UPTREND + DISCOUNT = ИДЕАЛЬНОЕ место для BUY (откат в тренде)
    if swing_trend == "UPTREND" and current_zone == "DISCOUNT":
        impulse_reasons.append("🎯 SMC SWEET SPOT: UPTREND + DISCOUNT = BUY на откате!")
        # Это классический SMC сетап - покупка на откате в восходящем тренде
    
    # DOWNTREND + PREMIUM = ИДЕАЛЬНОЕ место для SELL (откат в тренде)
    if swing_trend == "DOWNTREND" and current_zone == "PREMIUM":
        impulse_reasons.append("🎯 SMC SWEET SPOT: DOWNTREND + PREMIUM = SELL на откате!")
        # Это классический SMC сетап - продажа на откате в нисходящем тренде
    
    # ========================================================================
    # COUNTER-TREND FILTERS (запреты торговли против тренда без CHoCH)
    # ========================================================================
    
    # DOWNTREND + PREMIUM без CHoCH = запрет BUY (покупка против тренда)
    if swing_trend == "DOWNTREND" and current_zone == "PREMIUM":
        # Проверяем есть ли бычий CHoCH (смена тренда)
        has_bullish_choch = any(
            'BULLISH' in ch.get('type', '')
            for ch in analysis.get('swing_choch_confirmed', []) + analysis.get('internal_choch_confirmed', [])
        )
        
        if has_bullish_choch:
            is_reversal_setup = True
            impulse_reasons.append("🔄 Бычий CHoCH найден — разворот возможен")
        else:
            # Нет CHoCH — это зона для SELL, не для BUY
            # LLM сам определит что делать, но мы даём контекст
            impulse_reasons.append("⚠️ DOWNTREND + PREMIUM: зона для SELL на откате (без CHoCH)")
    
    # UPTREND + DISCOUNT без CHoCH = запрет SELL (продажа против тренда)
    if swing_trend == "UPTREND" and current_zone == "DISCOUNT":
        # Проверяем есть ли медвежий CHoCH (смена тренда)
        has_bearish_choch = any(
            'BEARISH' in ch.get('type', '')
            for ch in analysis.get('swing_choch_confirmed', []) + analysis.get('internal_choch_confirmed', [])
        )
        
        if has_bearish_choch:
            is_reversal_setup = True
            impulse_reasons.append("🔄 Медвежий CHoCH найден — разворот возможен")
        else:
            # Нет CHoCH — это зона для BUY, не для SELL
            impulse_reasons.append("⚠️ UPTREND + DISCOUNT: зона для BUY на откате (без CHoCH)")
    
    # ========================================================================
    # ОСТАЛЬНЫЕ ФИЛЬТРЫ
    # ========================================================================
    
    # Близость к структурам (пропускаем при импульсе или confirmed break)
    if not is_near and not is_breakout_impulse and not has_swing_break_confirmed:
        status_data['status'] = 'not_near_structure'
        status_data['reason'] = f'Цена ${current_price:.2f} далеко от SMC структур (нет confirmed break)'
        send_debug_notification(status_data)
        return
    
    # Equilibrium (пропускаем при импульсе)
    if current_zone == "EQUILIBRIUM" and not is_breakout_impulse:
        status_data['status'] = 'equilibrium_zone'
        status_data['reason'] = f'Цена в Equilibrium ({position_in_range_pct:.1f}%)'
        send_debug_notification(status_data)
        return
    
    # NEUTRAL требует Swing (пропускаем при импульсе)
    if swing_trend == "NEUTRAL" and not is_breakout_impulse:
        # v6.0: Требуем confirmed swing break
        if not has_swing_break_confirmed:
            status_data['status'] = 'neutral_no_swing'
            status_data['reason'] = 'Нейтральный тренд без CONFIRMED Swing пробоя'
            send_debug_notification(status_data)
            return
    
    # Сильные паттерны
    has_strong_swing = any('SWING' in s for s in swing_signals)
    has_strong_internal = any('INT' in s or 'OB' in s for s in internal_signals)
    
    if not all_signals and not is_breakout_impulse:
        status_data['status'] = 'weak_patterns'
        status_data['reason'] = 'Нет SMC паттернов'
        send_debug_notification(status_data)
        return
    
    # ========================================================================
    # v7.5.2: SMART COOLDOWN с OVERRIDE для критических событий
    # ========================================================================
    if not check_smart_cooldown():
        # Кулдаун активен, НО проверяем критические события для override
        override_cooldown = False
        override_reasons = []
        
        # КРИТЕРИЙ 1: Swing BOS/CHoCH confirmed (наивысший приоритет!)
        if has_swing_bos_confirmed or has_swing_choch_confirmed:
            override_cooldown = True
            swing_type = 'BOS' if has_swing_bos_confirmed else 'CHoCH'
            override_reasons.append(f"🔥 Swing {swing_type} confirmed")
        
        # КРИТЕРИЙ 2: Множественное подтверждение (2+ confirmed)
        if confirmed_count >= OVERRIDE_MIN_CONFIRMED:
            override_cooldown = True
            override_reasons.append(f"✅ Multiple confirmations ({confirmed_count})")
        
        # КРИТЕРИЙ 3: Сильный импульс + internal confirmed
        if impulse_strength >= OVERRIDE_MIN_IMPULSE and has_internal_break_confirmed:
            override_cooldown = True
            override_reasons.append(f"⚡ Strong impulse {impulse_strength}%")
        
        # КРИТЕРИЙ 4: Breakout + internal confirmed
        if has_breakout and has_internal_break_confirmed:
            override_cooldown = True
            override_reasons.append("📊 Breakout + internal confirmed")
        
        if override_cooldown:
            # OVERRIDE! Игнорируем кулдаун - критическое событие
            logger.warning(f"🔓 COOLDOWN OVERRIDE: {' | '.join(override_reasons)}")
            status_data['cooldown_override'] = True
            status_data['override_reasons'] = override_reasons
        else:
            # Обычный SKIP по кулдауну
            status_data['status'] = 'cooldown'
            status_data['reason'] = (
                f'⏳ Кулдаун активен.\n'
                f'Confirmed signals: {confirmed_count}\n'
                f'Swing confirmed: BOS={has_swing_bos_confirmed}, CHoCH={has_swing_choch_confirmed}\n'
                f'Override требует: Swing confirmed ИЛИ {OVERRIDE_MIN_CONFIRMED}+ confirmed ИЛИ сильный импульс'
            )
            send_debug_notification(status_data)
            return
    
    # ========================================================================
    # v6.0 КРИТИЧЕСКИЙ ФИЛЬТР: ТРЕБУЕМ CONFIRMED СИГНАЛ ДЛЯ ВЫЗОВА LLM
    # ========================================================================
    # LLM вызывается ТОЛЬКО если:
    # 1. Есть хотя бы один CONFIRMED BOS/CHoCH (пробой телом свечи)
    # 2. ИЛИ активен impulse override + есть internal confirmed (v7.5.2 fix!)
    # 3. ИЛИ есть reversal setup + есть internal confirmed (v7.5.2 fix!)
    
    has_any_confirmed = (
        has_swing_bos_confirmed or 
        has_swing_choch_confirmed or 
        has_int_bos_confirmed or 
        has_int_choch_confirmed
    )
    
    # v7.5.2 FIX: Impulse/Reversal режимы тоже требуют хотя бы internal confirmed
    # Это защищает от ложных пробоев (только wick, не body)
    impulse_needs_confirmation = (is_breakout_impulse or is_reversal_setup) and not has_internal_break_confirmed
    
    if not has_any_confirmed and not (is_breakout_impulse or is_reversal_setup):
        status_data['status'] = 'no_confirmed_signal'
        status_data['reason'] = (
            f'⏳ Нет CONFIRMED сигналов (confirmed_total={confirmed_count}).\n'
            f'LLM не вызывается без уверенного пробоя (телом свечи).\n'
            f'Swing BOS_conf={has_swing_bos_confirmed}, CHoCH_conf={has_swing_choch_confirmed}\n'
            f'Internal BOS_conf={has_int_bos_confirmed}, CHoCH_conf={has_int_choch_confirmed}'
        )
        send_debug_notification(status_data)
        return
    
    # v7.5.2 FIX: Если impulse/reversal но нет даже internal confirmed → SKIP
    if impulse_needs_confirmation:
        status_data['status'] = 'impulse_no_confirmation'
        status_data['reason'] = (
            f'⚠️ Impulse/Reversal режим, но нет CONFIRMED internal сигнала.\n'
            f'Это может быть ложный пробой (только wick).\n'
            f'Internal BOS_conf={has_int_bos_confirmed}, CHoCH_conf={has_int_choch_confirmed}\n'
            f'Требуем хотя бы internal confirmed для безопасности.'
        )
        send_debug_notification(status_data)
        return
    
    logger.info(f"✅ CONFIRMED SIGNALS: {confirmed_count} | Calling LLM...")
    
    # ========================================================================
    # ФАЗА 3: ВЫЗОВ LLM
    # ========================================================================
    
    # Формируем контекст для LLM
    analysis['impulse_context'] = {
        'is_breakout_impulse': is_breakout_impulse,
        'is_reversal_setup': is_reversal_setup,
        'has_breakout': has_breakout,
        'is_void_run': is_void_run,
        'is_impulse': is_impulse,
        'market_condition': market_condition,
        'impulse_strength': impulse_strength,
        'override_reason': ' | '.join(impulse_reasons) if impulse_reasons else '',
        'position_pct': position_in_range_pct,
        'extreme_zone': current_zone if position_in_range_pct < 15 or position_in_range_pct > 85 else None
    }
    
    mode_text = ""
    if is_breakout_impulse:
        mode_text = "⚡ IMPULSE MODE"
        status_data['status'] = 'impulse_override'
    elif is_reversal_setup:
        mode_text = "🔄 REVERSAL MODE"
        status_data['status'] = 'reversal_mode'
    
    logger.info("=" * 60)
    logger.info(f"🎯 {mode_text} ВСЕ ФИЛЬТРЫ ПРОЙДЕНЫ! Запрашиваем Gemini...")
    logger.info(f"   💰 Цена: ${current_price:.2f}")
    logger.info(f"   📈 Swing Тренд: {swing_trend}")
    logger.info(f"   🎯 Зона: {current_zone} ({position_in_range_pct:.1f}%)")
    if impulse_reasons:
        logger.info(f"   ⚡ Причины: {', '.join(impulse_reasons)}")
    logger.info("=" * 60)
    
    # Вызов Gemini
    ai_response = llm_service.get_signal_verdict(analysis)
    
    # Парсим ответ (поддержка ОБОИХ форматов через extract_llm_verdict)
    parsed_llm = parse_llm_response(ai_response)
    verdict = extract_llm_verdict(parsed_llm)
    
    llm_action = verdict['action']  # BUY / SELL / WAIT
    is_confirmed = llm_action in ['BUY', 'SELL']
    low_conf_override = verdict.get('low_confidence_override', False)
    original_action = verdict.get('original_action')
    
    # Логируем с информацией о confidence override
    if low_conf_override:
        logger.warning(f"⚠️ LLM Verdict: {original_action} → WAIT (LOW CONFIDENCE: {verdict['confidence']}%)")
        logger.warning(f"   Entry={verdict['entry']}, SL={verdict['sl']}, TP={verdict['tp']}")
    else:
        logger.info(f"📊 LLM Verdict: action={llm_action}, confidence={verdict['confidence']}%, entry={verdict['entry']}, sl={verdict['sl']}, tp={verdict['tp']}")
    
    # Подготовка данных для БД
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
    # РЕЗУЛЬТАТ
    # ========================================================================
    
    if is_confirmed:
        logger.info(f"🔥 ТОРГОВЫЙ СИГНАЛ: {llm_action}")
        
        db_service.update_last_signal_time()
        
        try:
            signal_id = db_service.save_signal(signal_data_db)
            logger.info(f"💾 Сигнал сохранен (ID: {signal_id})")
        except Exception as e:
            logger.error(f"❌ Ошибка сохранения: {e}")
        
        user_ids = db_service.get_all_active_users()
        if user_ids:
            formatted_msg = format_signal_message(ai_response)
            telegram_service.broadcast_signal(user_ids, formatted_msg)
            logger.info(f"📤 Сигнал отправлен {len(user_ids)} пользователям")
        
        status_data['status'] = 'signal_sent'
        status_data['reason'] = f'{mode_text} Gemini подтвердил {llm_action}!\n' + '\n'.join(impulse_reasons)
        status_data['llm_verdict'] = ai_response
        send_debug_notification(status_data)
    
    else:
        # Определяем причину WAIT
        if low_conf_override:
            logger.warning(f"⚠️ LOW CONFIDENCE WAIT: оригинальный сигнал {original_action} отклонён (confidence={verdict['confidence']}%)")
            wait_reason = f'⚠️ LOW CONFIDENCE ({verdict["confidence"]}%): {original_action} → WAIT'
        else:
            logger.info("⚖️ Gemini рекомендует WAIT")
            wait_reason = f'{mode_text} Gemini рекомендует ожидание.'
        
        db_service.update_last_wait_time()
        
        try:
            signal_id = db_service.save_signal(signal_data_db)
            logger.info(f"💾 WAIT сохранен (ID: {signal_id})")
        except Exception as e:
            logger.error(f"❌ Ошибка сохранения: {e}")
        
        # Отправляем WAIT сигнал пользователям (с полными данными wait_metadata)
        # Передаём verdict чтобы format_signal_message знал о low_confidence_override
        user_ids = db_service.get_all_active_users()
        if user_ids:
            # Создаём модифицированный ответ с информацией о verdict
            formatted_wait_msg = format_signal_message_with_verdict(ai_response, verdict)
            telegram_service.broadcast_signal(user_ids, formatted_wait_msg)
            logger.info(f"📤 WAIT сигнал отправлен {len(user_ids)} пользователям")
        
        status_data['status'] = 'wait_decision' if not low_conf_override else 'low_confidence_wait'
        status_data['reason'] = wait_reason + '\n' + '\n'.join(impulse_reasons)
        status_data['llm_verdict'] = ai_response
        status_data['confidence'] = verdict['confidence']
        if low_conf_override:
            status_data['original_action'] = original_action
        send_debug_notification(status_data)


def start_watcher():
    logger.info("🛰 Astra Watcher v8.0 SMC-Correct Zone Logic инициализирован")


if __name__ == "__main__":
    logger.info("🧪 Ручной запуск анализа v8.0...")
    run_analysis_cycle()