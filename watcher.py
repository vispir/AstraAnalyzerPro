"""
Astra Watcher v3.0 - Adaptive Impulse
======================================
КРИТИЧЕСКИЕ ИЗМЕНЕНИЯ:
1. Impulse Override - разрешает торговлю в экстремальных зонах при импульсе
2. FRESH_SIGNAL_BARS = 25 (помним пробой дольше)
3. Логика Роберта - запрет зон только при флэте, не при тренде
4. Передача is_breakout_impulse в LLM
"""

import os
import math
from datetime import datetime, timedelta, timezone
import logging
import json
from services.db_service import db_service
from services.telegram_service import telegram_service


# ============================================================================
# КОНСТАНТЫ v3.0
# ============================================================================

LOOKBACK_BARS = 250                    # Глубина анализа для зон
FRESH_SIGNAL_BARS = 25                 # Свежий сигнал (было 10)
EXTREME_DISCOUNT_THRESHOLD = 15.0      # < 15% = экстремальный дискаунт
EXTREME_PREMIUM_THRESHOLD = 85.0       # > 85% = экстремальный премиум
SIGNAL_COOLDOWN_HOURS = 2              # Кулдаун для BUY/SELL
WAIT_COOLDOWN_HOURS = 1                # Кулдаун для WAIT

# Сильные паттерны
SWING_STRONG_SETUPS = ['SWING_BOS', 'SWING_CHOCH']
INTERNAL_STRONG_SETUPS = ['INT_BOS', 'INT_CHOCH', 'OB_RETEST']
ALL_STRONG_SETUPS = SWING_STRONG_SETUPS + INTERNAL_STRONG_SETUPS + ['BOS', 'CHOCH']


# ============================================================================
# ИМПОРТЫ СЕРВИСОВ
# ============================================================================

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

def safe_float(value, default=0.0):
    """Безопасное преобразование в float"""
    try:
        result = float(value) if value is not None else default
        if math.isnan(result) or math.isinf(result):
            return default
        return result
    except (TypeError, ValueError):
        return default


def is_market_active():
    """Проверка активности рынка"""
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
    """Проверка кулдауна через Supabase"""
    try:
        now = datetime.now(timezone.utc)
        
        last_trade = db_service.get_last_trade_signal_time()
        if (now - last_trade).total_seconds() < (SIGNAL_COOLDOWN_HOURS * 3600):
            logger.info("⏳ Кулдаун СДЕЛКИ активен.")
            return False

        last_wait = db_service.get_last_wait_time()
        if (now - last_wait).total_seconds() < (WAIT_COOLDOWN_HOURS * 3600):
            logger.info("⏳ Кулдаун WAIT активен.")
            return False
        
        return True
    except Exception as e:
        logger.error(f"Ошибка кулдауна: {e}")
        return True


def calculate_forced_zones(candles):
    """
    🔧 ЖЁСТКИЙ РАСЧЁТ ЗОН
    Считаем по max/min за LOOKBACK_BARS свечей
    """
    if not candles:
        return "UNKNOWN", 50.0, 0.0, 0.0
    
    all_highs = [c['high'] for c in candles[-LOOKBACK_BARS:]]
    all_lows = [c['low'] for c in candles[-LOOKBACK_BARS:]]
    
    global_high = max(all_highs)
    global_low = min(all_lows)
    current_close = candles[-1]['close']
    
    if global_high == global_low:
        return "EQUILIBRIUM", 50.0, global_high, global_low
    
    position_pct = ((current_close - global_low) / (global_high - global_low)) * 100
    
    if position_pct < 33.3:
        zone = "DISCOUNT"
    elif position_pct > 66.6:
        zone = "PREMIUM"
    else:
        zone = "EQUILIBRIUM"
    
    logger.info(f"🔧 ЗОНЫ: {current_close:.2f} в [{global_low:.2f} - {global_high:.2f}] = {position_pct:.1f}% ({zone})")
    
    return zone, position_pct, global_high, global_low


def is_price_near_smc_structure(current_price, analysis, threshold_percent=0.5):
    """Проверяет близость цены к SMC структурам"""
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
    """Парсит JSON от Gemini"""
    try:
        start = ai_response.find('{')
        end = ai_response.rfind('}') + 1
        if start >= 0 and end > start:
            return json.loads(ai_response[start:end])
    except Exception as e:
        logger.error(f"Ошибка парсинга LLM: {e}")
    return None


def extract_executive_summary(ai_response):
    """Извлекает резюме из ответа LLM"""
    parsed = parse_llm_response(ai_response)
    if parsed and 'executive_summary' in parsed:
        return parsed['executive_summary']
    cleaned = ai_response.replace('```json', '').replace('```', '').strip()
    return cleaned[:200] + '...' if len(cleaned) > 200 else cleaned


def format_signal_message(ai_response):
    """Форматирует сигнал для Telegram"""
    parsed = parse_llm_response(ai_response)
    
    if parsed:
        action = parsed.get("ACTION", "N/A")
        emoji = "🟢 BUY" if action == "BUY" else "🔴 SELL"
        return (f"<b>🚀 ASTRA SIGNAL: GOLD (XAU/USD)</b>\n\n"
                f"Направление: <b>{emoji}</b>\n"
                f"Вход: <code>{parsed.get('ENTRY')}</code>\n"
                f"Стоп: <code>{parsed.get('SL')}</code>\n"
                f"Тейк: <code>{parsed.get('TP')}</code>\n\n"
                f"<b>Анализ:</b>\n<i>{parsed.get('REASON', 'SMC Confirmation')}</i>")
    return f"<b>📢 СИГНАЛ XAUUSD:</b>\n\n{ai_response}"


def format_debug_report(status_data):
    """Форматирует отладочный отчёт"""
    status_emoji = {
        'market_closed': '💤', 'news_block': '📰', 'oanda_error': '🔌',
        'no_smc': '⚙️', 'not_near_structure': '🔍', 'equilibrium_zone': '⚪',
        'weak_patterns': '📉', 'neutral_no_swing': '⚖️', 'cooldown': '⏳',
        'signal_sent': '✅', 'wait_decision': '⚖️',
        'impulse_override': '⚡', 'reversal_setup': '🔄',
        'hard_filter_discount_downtrend': '🛑', 'hard_filter_premium_uptrend': '🛑',
        'extreme_zone_no_signal': '⚠️'
    }
    
    status_texts = {
        'market_closed': 'Рынок закрыт',
        'news_block': 'Блокировка по новостям',
        'oanda_error': 'Ошибка OANDA',
        'no_smc': 'SMC детектор недоступен',
        'not_near_structure': 'SKIP - Цена далеко от структур',
        'equilibrium_zone': 'SKIP - Зона Equilibrium',
        'weak_patterns': 'SKIP - Нет сильных паттернов',
        'neutral_no_swing': 'SKIP - Нейтральный без Swing',
        'cooldown': 'SKIP - Кулдаун активен',
        'signal_sent': '🎯 ТОРГОВЫЙ СИГНАЛ!',
        'wait_decision': 'LLM рекомендует WAIT',
        'impulse_override': '⚡ IMPULSE MODE: Пробойный вход',
        'reversal_setup': '🔄 REVERSAL MODE: Разворотный вход',
        'hard_filter_discount_downtrend': '🛑 ЗАПРЕТ: Продажа в DISCOUNT (без импульса)',
        'hard_filter_premium_uptrend': '🛑 ЗАПРЕТ: Покупка в PREMIUM (без импульса)',
        'extreme_zone_no_signal': '⚠️ Экстремальная зона без сигналов'
    }
    
    status = status_data.get('status', 'unknown')
    emoji = status_emoji.get(status, '❓')
    decision = status_texts.get(status, 'Неизвестно')
    
    now_utc = datetime.now(timezone.utc)
    msg = f"<b>{emoji} ASTRA WATCHER v3.0</b>\n"
    msg += f"<code>UTC: {now_utc.strftime('%Y-%m-%d %H:%M:%S')}</code>\n"
    msg += "━" * 32 + "\n\n"
    msg += f"<b>📋 Решение:</b> {decision}\n\n"
    
    # Рыночные данные
    if status_data.get('price', 0) > 0:
        msg += "<b>💹 Рыночные данные:</b>\n"
        msg += f"├ Цена: <code>${status_data['price']:.2f}</code>\n"
        
        trend = status_data.get('trend', '')
        trend_emoji = "📈" if "UP" in trend else "📉" if "DOWN" in trend else "↔️"
        msg += f"├ Swing Тренд: {trend_emoji} {trend}\n"
        
        int_trend = status_data.get('internal_trend', '')
        int_emoji = "📈" if "UP" in int_trend else "📉" if "DOWN" in int_trend else "↔️"
        msg += f"├ Internal Тренд: {int_emoji} {int_trend}\n"
        
        zone = status_data.get('zone', '')
        zone_emoji = "🔴" if zone == "PREMIUM" else "🟢" if zone == "DISCOUNT" else "⚪"
        msg += f"├ Зона: {zone_emoji} {zone}\n"
        
        pos_pct = status_data.get('position_in_range_pct', 50)
        msg += f"└ Позиция: {pos_pct:.1f}% диапазона\n\n"
    
    # Диапазон
    if 'global_high' in status_data and 'global_low' in status_data:
        msg += f"<b>📐 Диапазон {LOOKBACK_BARS} свечей:</b>\n"
        msg += f"├ High: ${status_data['global_high']:.2f}\n"
        msg += f"└ Low: ${status_data['global_low']:.2f}\n\n"
    
    # Impulse Context (v3.0 NEW!)
    if 'impulse_context' in status_data:
        ic = status_data['impulse_context']
        msg += f"<b>⚡ Impulse Context:</b>\n"
        msg += f"├ Режим: {ic.get('market_condition', 'N/A')}\n"
        msg += f"├ Направление: {ic.get('impulse_direction', 'NONE')}\n"
        msg += f"├ Сила: {ic.get('impulse_strength', 0)}%\n"
        msg += f"└ VoidRun: {'ДА' if ic.get('is_void_run') else 'Нет'}\n\n"
    
    # SMC паттерны
    if 'smc_summary' in status_data:
        smc = status_data['smc_summary']
        msg += "<b>📊 SMC Паттерны:</b>\n"
        msg += f"├ Order Blocks: {smc.get('ob', 0)}\n"
        msg += f"├ Fair Value Gaps: {smc.get('fvg', 0)}\n"
        msg += f"├ Swing BOS: {smc.get('swing_bos_total', 0)} (Total) | Fresh: {smc.get('swing_bos', 0)}\n"
        msg += f"├ Swing CHoCH: {smc.get('swing_choch_total', 0)} (Total) | Fresh: {smc.get('swing_choch', 0)}\n"
        msg += f"└ Internal BOS: {smc.get('int_bos', 0)} | CHoCH: {smc.get('int_choch', 0)}\n\n"
    
    # Сигналы
    if status_data.get('swing_signals'):
        msg += f"<b>🎯 Swing:</b> {', '.join(status_data['swing_signals'][:3])}\n"
    if status_data.get('internal_signals'):
        msg += f"<b>📍 Internal:</b> {', '.join(status_data['internal_signals'][:3])}\n\n"
    
    # Близость к структурам
    if 'near_structures' in status_data:
        msg += f"<b>🎯 Уровни рядом:</b>\n{status_data['near_structures']}\n\n"
    
    # Детали
    if 'reason' in status_data:
        msg += f"<b>💡 Детали:</b>\n<i>{status_data['reason']}</i>\n\n"
    
    # LLM вердикт
    if 'llm_verdict' in status_data:
        summary = extract_executive_summary(status_data['llm_verdict'])
        msg += f"<b>🤖 Gemini:</b>\n<i>{summary}</i>\n\n"
    
    msg += "━" * 32 + "\n"
    msg += "<i>⏱ Следующая проверка через 15 минут</i>"
    
    return msg


def send_debug_notification(status_data):
    """Отправляет отчёт всем пользователям"""
    try:
        user_ids = db_service.get_all_active_users()
        if user_ids:
            message = format_debug_report(status_data)
            telegram_service.broadcast_signal(user_ids, message)
            logger.info(f"📤 Отчёт отправлен ({len(user_ids)} users)")
    except Exception as e:
        logger.error(f"❌ Ошибка отправки: {e}")


# ============================================================================
# СБОР СИГНАЛОВ
# ============================================================================

def collect_signals_by_type(analysis):
    """Собирает сигналы разделённые на Swing и Internal"""
    swing_signals = []
    internal_signals = []
    
    for bos in analysis.get('swing_bos', []):
        swing_signals.append(f"SWING_BOS ({bos.get('type', 'BOS')}, {bos.get('bars_ago', 0)} bars)")
    
    for choch in analysis.get('swing_choch', []):
        swing_signals.append(f"SWING_CHOCH ({choch.get('type', 'CHOCH')}, {choch.get('bars_ago', 0)} bars)")
    
    for ob in analysis.get('order_blocks_swing', []):
        swing_signals.append(f"SWING_OB ({ob.get('type', 'OB')})")
    
    for bos in analysis.get('internal_bos', []):
        internal_signals.append(f"INT_BOS ({bos.get('type', 'BOS')}, {bos.get('bars_ago', 0)} bars)")
    
    for choch in analysis.get('internal_choch', []):
        internal_signals.append(f"INT_CHOCH ({choch.get('type', 'CHOCH')}, {choch.get('bars_ago', 0)} bars)")
    
    for ob in analysis.get('order_blocks_internal', []):
        internal_signals.append(f"INT_OB ({ob.get('type', 'OB')})")
    
    for fvg in analysis.get('fvg', []):
        internal_signals.append(f"FVG ({fvg.get('type', 'FVG')})")
    
    return swing_signals, internal_signals, swing_signals + internal_signals


def get_signal_label(action):
    """Label для БД"""
    if action == 'BUY':
        return "🟢 ПОКУПКА"
    elif action == 'SELL':
        return "🔴 ПРОДАЖА"
    return "⚖️ ОЖИДАНИЕ"


def prepare_signal_data_for_db(llm_action, parsed_llm, ai_response, current_price,
                                trend, internal_trend, zone,
                                swing_signals, internal_signals, smc_summary,
                                impulse_context=None):
    """Подготавливает данные для Supabase"""
    
    patterns_list = list(swing_signals + internal_signals) if (swing_signals or internal_signals) else []
    signal_label = get_signal_label(llm_action)
    
    entry_price = safe_float(current_price)
    stop_loss = 0.0
    take_profit = 0.0
    confidence = 0
    reason = ""
    
    if parsed_llm and isinstance(parsed_llm, dict):
        entry_price = safe_float(parsed_llm.get('ENTRY', current_price), current_price)
        stop_loss = safe_float(parsed_llm.get('SL', 0))
        take_profit = safe_float(parsed_llm.get('TP', 0))
        try:
            confidence = int(parsed_llm.get('CONFIDENCE', 0) or 0)
        except:
            confidence = 0
        reason = str(parsed_llm.get('REASON', ''))[:500]
    
    signal_data = {
        'symbol': 'XAU_USD',
        'signal_type': str(llm_action),
        'signal_label': signal_label,
        'status': 'active',
        'entry_price': safe_float(entry_price),
        'current_price': safe_float(current_price),
        'stop_loss': safe_float(stop_loss),
        'take_profit': safe_float(take_profit),
        'trend': str(trend) if trend else 'NEUTRAL',
        'internal_trend': str(internal_trend) if internal_trend else 'NEUTRAL',
        'zone': str(zone) if zone else 'UNKNOWN',
        'patterns': patterns_list,
        'smc_summary': dict(smc_summary) if isinstance(smc_summary, dict) else {},
        'llm_full_response': str(ai_response)[:2000] if ai_response else '',
        'llm_reason': reason,
        'llm_confidence': confidence
    }
    
    # v3.0: Добавляем impulse context
    if impulse_context:
        signal_data['impulse_context'] = impulse_context
    
    return signal_data


# ============================================================================
# ГЛАВНЫЙ ЦИКЛ АНАЛИЗА v3.0
# ============================================================================

def run_analysis_cycle():
    """
    🔥 ASTRA WATCHER v3.0 - Adaptive Impulse
    
    КЛЮЧЕВЫЕ ИЗМЕНЕНИЯ:
    1. Impulse Override - разрешает торговлю при IMPULSE_TREND или is_void_run
    2. Логика Роберта - запрет зон только при флэте
    3. FRESH_SIGNAL_BARS = 25
    4. Передача is_breakout_impulse в LLM
    """
    logger.info("📡 [TRIGGER] Astra Watcher v3.0 Adaptive Impulse")
    
    # ========================================================================
    # ФАЗА 1: ТЕХНИЧЕСКИЕ ПРОВЕРКИ
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
            reason = f'Воскресенье - откроется в 23:00 UTC (сейчас {hour}:00)'
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
    
    # Данные OANDA
    data = oanda_service.get_candles(timeframe='M15', limit=LOOKBACK_BARS)
    if "error" in data:
        send_debug_notification({'status': 'oanda_error', 'reason': str(data.get("error"))})
        return
    
    candles = data.get("candles", [])
    if not candles:
        send_debug_notification({'status': 'oanda_error', 'reason': 'Пустой массив свечей'})
        return
    
    if not smc_detector:
        send_debug_notification({'status': 'no_smc', 'reason': 'SMC детектор не загружен'})
        return
    
    # ========================================================================
    # ФАЗА 2: SMC АНАЛИЗ
    # ========================================================================
    
    logger.info("🔬 SMC анализ...")
    analysis = smc_detector.analyze(candles)
    
    # Жёсткий расчёт зон
    current_zone, position_in_range_pct, global_high, global_low = calculate_forced_zones(candles)
    
    # Тренды из детектора
    swing_trend = analysis.get('trend', 'NEUTRAL')
    internal_trend = analysis.get('internal_trend', 'NEUTRAL')
    current_price = safe_float(candles[-1].get('close', 0))
    
    # Impulse Context (v3.0 NEW!)
    impulse_context = analysis.get('impulse_context', {})
    market_condition = impulse_context.get('market_condition', 'RANGING')
    is_void_run = impulse_context.get('is_void_run', False)
    impulse_direction = impulse_context.get('impulse_direction', 'NONE')
    impulse_strength = impulse_context.get('impulse_strength', 0)
    allow_discount_sell = impulse_context.get('allow_discount_sell', False)
    allow_premium_buy = impulse_context.get('allow_premium_buy', False)
    
    # Сбор сигналов
    swing_signals, internal_signals, all_signals = collect_signals_by_type(analysis)
    
    # Подсчёт свежих BOS
    fresh_swing_bos = analysis.get('swing_bos', [])
    fresh_swing_choch = analysis.get('swing_choch', [])
    has_fresh_swing_bos = len(fresh_swing_bos) > 0
    has_fresh_swing_choch = len(fresh_swing_choch) > 0
    has_fresh_swing_break = has_fresh_swing_bos or has_fresh_swing_choch
    
    # Internal
    has_fresh_int_bos = len(analysis.get('internal_bos', [])) > 0
    has_fresh_int_choch = len(analysis.get('internal_choch', [])) > 0
    has_internal_break = has_fresh_int_bos or has_fresh_int_choch
    
    # Бычий Internal CHoCH (для разворота)
    has_bullish_internal_choch = any(
        'BULLISH' in c.get('type', '') 
        for c in analysis.get('internal_choch', [])
    )
    
    # Проверка близости к структурам
    is_near, near_description = is_price_near_smc_structure(current_price, analysis)
    
    # SMC Summary
    smc_summary = {
        'ob': len(analysis.get('order_blocks', [])),
        'fvg': len(analysis.get('fvg', [])),
        'swing_bos': len(fresh_swing_bos),
        'swing_choch': len(fresh_swing_choch),
        'int_bos': len(analysis.get('internal_bos', [])),
        'int_choch': len(analysis.get('internal_choch', [])),
        'swing_bos_total': len(analysis.get('all_swing_bos', [])),
        'swing_choch_total': len(analysis.get('all_swing_choch', [])),
        'int_bos_total': len(analysis.get('all_internal_bos', [])),
        'int_choch_total': len(analysis.get('all_internal_choch', []))
    }
    
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
        'impulse_context': impulse_context,
        'status': 'unknown',
        'reason': ''
    }
    
    logger.info(f"📊 Swing={len(swing_signals)}, Internal={len(internal_signals)}, "
                f"Impulse={market_condition}, VoidRun={is_void_run}")
    
    # ========================================================================
    # ФАЗА 3: ФИЛЬТРЫ GATEKEEPER v3.0 (ADAPTIVE IMPULSE)
    # ========================================================================
    
    # Флаги для режима входа
    is_breakout_impulse = False
    is_reversal_setup = False
    
    # --- ФИЛЬТР 1: БЛИЗОСТЬ К СТРУКТУРАМ ---
    if not is_near and not has_fresh_swing_break and not is_void_run:
        status_data['status'] = 'not_near_structure'
        status_data['reason'] = f'Цена ${current_price:.2f} далеко от структур. Нет Swing пробоя.'
        send_debug_notification(status_data)
        return
    
    # --- ФИЛЬТР 2: EQUILIBRIUM ZONE (без импульса) ---
    if current_zone == "EQUILIBRIUM" and market_condition == 'RANGING':
        status_data['status'] = 'equilibrium_zone'
        status_data['reason'] = f'Зона Equilibrium ({position_in_range_pct:.1f}%). Ждём выхода.'
        send_debug_notification(status_data)
        return
    
    # ========================================================================
    # 🔥 ФИЛЬТР 3: ADAPTIVE IMPULSE LOGIC (v3.0 KEY CHANGE!)
    # ========================================================================
    
    # --- DOWNTREND + DISCOUNT ---
    if swing_trend == "DOWNTREND" and current_zone == "DISCOUNT":
        
        # 🔥 IMPULSE OVERRIDE: Разрешаем если есть импульс!
        if market_condition == 'IMPULSE_TREND' and impulse_direction == 'BEARISH':
            is_breakout_impulse = True
            logger.info(f"⚡ IMPULSE OVERRIDE: Продажа в DISCOUNT разрешена (IMPULSE_TREND)")
            status_data['status'] = 'impulse_override'
            status_data['reason'] = f'⚡ IMPULSE MODE: Пробойный вход разрешён (сила {impulse_strength}%)'
        
        # 🔥 VOID RUN: Пробито историческое дно
        elif is_void_run and impulse_direction == 'BEARISH':
            is_breakout_impulse = True
            logger.info(f"⚡ VOID RUN: Продажа в DISCOUNT разрешена (пробито дно)")
            status_data['status'] = 'impulse_override'
            status_data['reason'] = f'⚡ VOID RUN: Пробито историческое дно! Breakout вход.'
        
        # 🔥 FRESH SWING BOS: Свежий пробой структуры
        elif has_fresh_swing_bos and allow_discount_sell:
            is_breakout_impulse = True
            logger.info(f"⚡ FRESH BOS: Продажа в DISCOUNT разрешена")
            status_data['status'] = 'impulse_override'
            status_data['reason'] = f'⚡ Fresh Swing BOS: Пробойный вход в тренде'
        
        # ❌ НЕТ ИМПУЛЬСА → ЗАПРЕТ
        else:
            status_data['status'] = 'hard_filter_discount_downtrend'
            status_data['reason'] = (
                f'🛑 ЗАПРЕТ: Продажа в DISCOUNT ({position_in_range_pct:.1f}%) при DOWNTREND '
                f'без импульса. Режим: {market_condition}, Fresh BOS: {has_fresh_swing_bos}'
            )
            send_debug_notification(status_data)
            return
    
    # --- UPTREND + PREMIUM ---
    elif swing_trend == "UPTREND" and current_zone == "PREMIUM":
        
        if market_condition == 'IMPULSE_TREND' and impulse_direction == 'BULLISH':
            is_breakout_impulse = True
            logger.info(f"⚡ IMPULSE OVERRIDE: Покупка в PREMIUM разрешена")
            status_data['status'] = 'impulse_override'
            status_data['reason'] = f'⚡ IMPULSE MODE: Пробойный вход (сила {impulse_strength}%)'
        
        elif is_void_run and impulse_direction == 'BULLISH':
            is_breakout_impulse = True
            logger.info(f"⚡ VOID RUN: Покупка в PREMIUM разрешена")
            status_data['status'] = 'impulse_override'
            status_data['reason'] = f'⚡ VOID RUN: Пробита историческая вершина!'
        
        elif has_fresh_swing_bos and allow_premium_buy:
            is_breakout_impulse = True
            status_data['status'] = 'impulse_override'
            status_data['reason'] = f'⚡ Fresh Swing BOS: Пробойный вход'
        
        else:
            status_data['status'] = 'hard_filter_premium_uptrend'
            status_data['reason'] = (
                f'🛑 ЗАПРЕТ: Покупка в PREMIUM ({position_in_range_pct:.1f}%) при UPTREND '
                f'без импульса. Режим: {market_condition}'
            )
            send_debug_notification(status_data)
            return
    
    # --- ЭКСТРЕМАЛЬНЫЙ ДИСКАУНТ (<15%) без импульса ---
    elif position_in_range_pct < EXTREME_DISCOUNT_THRESHOLD and not is_breakout_impulse:
        
        # Ищем разворот вверх
        if has_bullish_internal_choch:
            is_reversal_setup = True
            logger.info(f"🔄 REVERSAL: Бычий CHoCH в экстремальном дискаунте")
            status_data['status'] = 'reversal_setup'
            status_data['reason'] = f'🔄 REVERSAL: Бычий Internal CHoCH на дне ({position_in_range_pct:.1f}%)'
        
        elif not has_fresh_swing_bos:
            status_data['status'] = 'extreme_zone_no_signal'
            status_data['reason'] = (
                f'⚠️ Экстремальный дискаунт ({position_in_range_pct:.1f}%) без пробоя и без разворота. '
                f'Ждём Swing BOS или Bullish CHoCH.'
            )
            send_debug_notification(status_data)
            return
    
    # --- ЭКСТРЕМАЛЬНЫЙ ПРЕМИУМ (>85%) без импульса ---
    elif position_in_range_pct > EXTREME_PREMIUM_THRESHOLD and not is_breakout_impulse:
        
        # Ищем медвежий CHoCH
        has_bearish_internal_choch = any(
            'BEARISH' in c.get('type', '') 
            for c in analysis.get('internal_choch', [])
        )
        
        if has_bearish_internal_choch:
            is_reversal_setup = True
            status_data['status'] = 'reversal_setup'
            status_data['reason'] = f'🔄 REVERSAL: Медвежий CHoCH на вершине ({position_in_range_pct:.1f}%)'
        
        elif not has_fresh_swing_bos:
            status_data['status'] = 'extreme_zone_no_signal'
            status_data['reason'] = f'⚠️ Экстремальный премиум без сигналов'
            send_debug_notification(status_data)
            return
    
    # --- ФИЛЬТР 4: NEUTRAL ТРЕБУЕТ SWING ---
    if swing_trend == "NEUTRAL" and not has_fresh_swing_break:
        status_data['status'] = 'neutral_no_swing'
        status_data['reason'] = f'Тренд NEUTRAL. Нужен Swing BOS/CHoCH.'
        send_debug_notification(status_data)
        return
    
    # --- ФИЛЬТР 5: НАЛИЧИЕ СИЛЬНЫХ ПАТТЕРНОВ ---
    has_strong = any('SWING' in s or 'INT' in s or 'OB' in s for s in all_signals)
    if not all_signals or not has_strong:
        status_data['status'] = 'weak_patterns'
        status_data['reason'] = 'Нет сильных SMC паттернов'
        send_debug_notification(status_data)
        return
    
    # --- ФИЛЬТР 6: КУЛДАУН ---
    if not check_smart_cooldown():
        status_data['status'] = 'cooldown'
        status_data['reason'] = 'Кулдаун активен'
        send_debug_notification(status_data)
        return
    
    # ========================================================================
    # ФАЗА 4: ВЫЗОВ LLM
    # ========================================================================
    
    logger.info("=" * 60)
    logger.info("🎯 ВСЕ ФИЛЬТРЫ ПРОЙДЕНЫ! Вызываем Gemini...")
    logger.info(f"   💰 Цена: ${current_price:.2f}")
    logger.info(f"   📈 Trend: {swing_trend}")
    logger.info(f"   🎯 Zone: {current_zone} ({position_in_range_pct:.1f}%)")
    logger.info(f"   ⚡ Impulse: {market_condition} ({impulse_strength}%)")
    logger.info(f"   🔥 Breakout: {is_breakout_impulse}, Reversal: {is_reversal_setup}")
    logger.info("=" * 60)
    
    # v3.0: Добавляем impulse context в analysis для LLM
    analysis['impulse_context'] = {
        'is_breakout_impulse': is_breakout_impulse,
        'is_reversal_setup': is_reversal_setup,
        'market_condition': market_condition,
        'impulse_strength': impulse_strength,
        'is_void_run': is_void_run,
        'extreme_zone': current_zone if position_in_range_pct < 15 or position_in_range_pct > 85 else None,
        'position_pct': position_in_range_pct
    }
    
    # Вызов Gemini
    ai_response = llm_service.get_signal_verdict(analysis)
    
    # Парсинг ответа
    parsed_llm = parse_llm_response(ai_response)
    llm_action = parsed_llm.get('ACTION', 'WAIT') if parsed_llm else 'WAIT'
    is_confirmed = llm_action in ['BUY', 'SELL']
    
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
        smc_summary=smc_summary,
        impulse_context=analysis['impulse_context']
    )
    
    # ========================================================================
    # СЛУЧАЙ A: ТОРГОВЫЙ СИГНАЛ
    # ========================================================================
    
    if is_confirmed:
        logger.info(f"🔥 СИГНАЛ: {llm_action}")
        
        db_service.update_last_signal_time()
        
        try:
            signal_id = db_service.save_signal(signal_data_db)
            logger.info(f"💾 Сохранено (ID: {signal_id})")
        except Exception as e:
            logger.error(f"❌ Ошибка БД: {e}")
        
        user_ids = db_service.get_all_active_users()
        if user_ids:
            telegram_service.broadcast_signal(user_ids, format_signal_message(ai_response))
            logger.info(f"📤 Отправлено {len(user_ids)} users")
        
        status_data['status'] = 'signal_sent'
        status_data['reason'] = f'Gemini: {llm_action}. Breakout={is_breakout_impulse}'
        status_data['llm_verdict'] = ai_response
        send_debug_notification(status_data)
    
    # ========================================================================
    # СЛУЧАЙ B: WAIT
    # ========================================================================
    
    else:
        logger.info("⚖️ Gemini: WAIT")
        
        db_service.update_last_wait_time()
        
        try:
            db_service.save_signal(signal_data_db)
        except Exception as e:
            logger.error(f"❌ Ошибка БД: {e}")
        
        status_data['status'] = 'wait_decision'
        status_data['reason'] = 'Gemini рекомендует ожидание'
        status_data['llm_verdict'] = ai_response
        send_debug_notification(status_data)


def start_watcher():
    """Инициализация"""
    logger.info("🛰 Astra Watcher v3.0 Adaptive Impulse запущен")


if __name__ == "__main__":
    logger.info("🧪 Тест v3.0...")
    run_analysis_cycle()
