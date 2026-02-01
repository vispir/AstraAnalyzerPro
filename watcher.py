"""
Astra Watcher v7.5.1.1 - Smart Cooldown + API Rate Limiter
=========================================================
НОВОЕ v7.5.1.1 - ЗАЩИТА ОТ ПРЕВЫШЕНИЯ ЛИМИТОВ:
- Счётчик вызовов LLM (100 requests/day безопасный лимит)
- Автоматическая блокировка при достижении лимита
- Предупреждение при 80% лимита
- Статистика в Telegram при превышении
- Автоматический сброс счётчика в 00:00 UTC

v7.5.1 СОХРАНЕНО - УМНЫЙ КУЛДАУН:
- Адаптивный кулдаун: 30 минут после WAIT, 2 часа после BUY/SELL
- OVERRIDE кулдауна при критических событиях:
  * Swing BOS/CHoCH confirmed (наивысший приоритет!)
  * Множественное подтверждение (2+ confirmed)
  * Сильный импульс (>=80%) + internal confirmed
  * Breakout + internal confirmed

v7.4 СОХРАНЕНО:
- Проверка confirmed=True в каждом сигнале
- Impulse/Reversal требуют internal confirmed
- Защита от ложных пробоев

v6.0 СОХРАНЕНО:
- CONFIRMED сигналы для торговых решений
- confirmed = пробой ТЕЛОМ, не тенью
- bars_ago <= 5 для торговых сигналов
"""

import os
import math
from datetime import datetime, timedelta, timezone
import logging
import json
from services.db_service import db_service
from services.telegram_service import telegram_service
from services import llm_rate_limiter  # v7.5.1.1: Защита от превышения лимитов

# ============================================================================
# КОНСТАНТЫ v7.5.1 SMART COOLDOWN
# ============================================================================

SIGNAL_COOLDOWN_HOURS = 2       # После BUY/SELL
WAIT_COOLDOWN_HOURS = 0.5       # v7.5.1: 30 минут после WAIT (было 1 час)
FRESH_SIGNAL_BARS = 25
LOOKBACK_BARS = 250
EXTREME_DISCOUNT_THRESHOLD = 15.0
EXTREME_PREMIUM_THRESHOLD = 85.0

# v7.5.1: Smart Cooldown Override - критерии для игнорирования кулдауна
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


def extract_executive_summary(ai_response):
    parsed = parse_llm_response(ai_response)
    if parsed and 'executive_summary' in parsed:
        return parsed['executive_summary']
    
    cleaned = ai_response.replace('```json', '').replace('```', '').strip()
    return cleaned[:197] + '...' if len(cleaned) > 200 else cleaned


def format_signal_message(ai_response):
    """
    Форматирует сигнал от LLM в красивое сообщение для Telegram
    REASON выводится полностью без обрезки
    """
    parsed_data = parse_llm_response(ai_response)
    
    if parsed_data:
        action = parsed_data.get("ACTION", "N/A")
        emoji = "🟢 BUY" if action == "BUY" else "🔴 SELL"
        
        entry = parsed_data.get('ENTRY', 'N/A')
        sl = parsed_data.get('SL', 'N/A')
        tp = parsed_data.get('TP', 'N/A')
        confidence = parsed_data.get('CONFIDENCE', 0)
        reason = parsed_data.get('REASON', 'SMC Confirmation')
        
        # Расчёт R:R если данные есть
        rr_text = ""
        try:
            entry_f = float(entry)
            sl_f = float(sl)
            tp_f = float(tp)
            risk = abs(entry_f - sl_f)
            reward = abs(tp_f - entry_f)
            if risk > 0:
                rr = reward / risk
                rr_text = f"\n📊 R:R = <b>1:{rr:.1f}</b>"
        except:
            pass
        
        # Уверенность
        conf_text = ""
        if confidence:
            conf_emoji = "🟢" if int(confidence) >= 70 else "🟡" if int(confidence) >= 50 else "🔴"
            conf_text = f"\n{conf_emoji} Уверенность: <b>{confidence}%</b>"
        
        msg = (
            f"<b>🚀 ASTRA SIGNAL: GOLD (XAU/USD)</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"Направление: <b>{emoji}</b>\n"
            f"Вход: <code>{entry}</code>\n"
            f"Стоп-лосс: <code>{sl}</code>\n"
            f"Тейк-профит: <code>{tp}</code>"
            f"{rr_text}"
            f"{conf_text}\n\n"
            f"<b>📝 Анализ:</b>\n{reason}\n\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"<i>⚠️ Не является финансовым советом</i>"
        )
        return msg
    
    # Если не удалось распарсить — выводим как есть
    return f"<b>📢 НОВЫЙ СИГНАЛ XAUUSD:</b>\n\n{ai_response}"


def format_debug_report(status_data):
    status_emoji = {
        'market_closed': '💤', 'news_block': '📰', 'oanda_error': '🔌',
        'no_smc': '⚙️', 'not_near_structure': '🔍', 'equilibrium_zone': '⚪',
        'weak_patterns': '📉', 'neutral_no_swing': '⚖️', 'cooldown': '⏳',
        'signal_sent': '✅', 'wait_decision': '⚖️',
        'impulse_override': '⚡', 'reversal_mode': '🔄',
        'hard_filter_discount_downtrend': '🛑', 'hard_filter_premium_uptrend': '🛑',
        'no_confirmed_signal': '⏳',  # v6.0: Нет уверенного пробоя
        'impulse_no_confirmation': '⚠️',  # v7.5.1: Impulse/Reversal без internal confirmed
        'api_limit_reached': '🚨'  # v7.5.1.1: Превышен лимит API
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
        'impulse_override': '⚡ IMPULSE MODE: Запрет снят!',
        'reversal_mode': '🔄 REVERSAL MODE: Поиск разворота',
        'hard_filter_discount_downtrend': '🛑 ЗАПРЕТ: Продажа в DISCOUNT',
        'hard_filter_premium_uptrend': '🛑 ЗАПРЕТ: Покупка в PREMIUM',
        'no_confirmed_signal': 'SKIP - Нет CONFIRMED пробоя (LLM не вызван)',  # v6.0
        'impulse_no_confirmation': 'SKIP - Impulse/Reversal без internal confirmed',  # v7.5.1
        'api_limit_reached': '⛔ ЛИМИТ API - Watcher остановлен до завтра'  # v7.5.1
    }
    
    status = status_data.get('status', 'unknown')
    
    status = status_data.get('status', 'unknown')
    emoji = status_emoji.get(status, '❓')
    
    now_utc = datetime.now(timezone.utc)
    msg = f"<b>{emoji} ASTRA WATCHER v7.5.1</b>\n"
    msg += f"<code>UTC: {now_utc.strftime('%Y-%m-%d %H:%M:%S')}</code>\n"
    msg += "━" * 32 + "\n\n"
    
    # v7.5.1: Показываем если был cooldown override
    if status_data.get('cooldown_override'):
        msg += "<b>🔓 COOLDOWN OVERRIDE:</b>\n"
        for reason in status_data.get('override_reasons', []):
            msg += f"└ {reason}\n"
        msg += "\n"
    
    msg += f"<b>📋 Решение:</b> {status_texts.get(status, 'Неизвестно')}\n\n"
    
    # v7.5.1.1: Показываем информацию о лимите API
    if status == 'api_limit_reached':
        llm_count = status_data.get('llm_count', 0)
        llm_limit = status_data.get('llm_limit', 100)
        msg += f"<b>🚨 ПРЕВЫШЕН ЛИМИТ API:</b>\n"
        msg += f"├ Вызовов сегодня: <code>{llm_count}/{llm_limit}</code>\n"
        msg += f"├ Использовано: <code>{(llm_count/llm_limit)*100:.1f}%</code>\n"
        msg += f"└ Сброс: <code>00:00 UTC</code>\n\n"
        msg += f"<i>⏰ Watcher автоматически возобновится завтра</i>\n"
        msg += "━" * 32 + "\n"
        return msg  # Возвращаем только информацию о лимите
    
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
        
        if 'position_in_range_pct' in status_data:
            msg += f"└ Позиция: {status_data['position_in_range_pct']:.1f}% диапазона\n\n"
    
    if 'global_high' in status_data and 'global_low' in status_data:
        msg += f"<b>📐 Диапазон {LOOKBACK_BARS} свечей:</b>\n"
        msg += f"├ High: ${status_data['global_high']:.2f}\n"
        msg += f"└ Low: ${status_data['global_low']:.2f}\n\n"
    
    # Impulse Context v5.2
    if 'impulse_context' in status_data:
        ic = status_data['impulse_context']
        msg += "<b>⚡ Impulse Context v5.2:</b>\n"
        msg += f"├ Режим: {ic.get('market_condition', 'N/A')}\n"
        msg += f"├ Breakout: {'✅' if ic.get('has_breakout') else '❌'}\n"
        msg += f"├ Void Run: {'✅' if ic.get('is_void_run') else '❌'}\n"
        msg += f"├ Impulse: {'✅' if ic.get('is_impulse') else '❌'} ({ic.get('impulse_strength', 0)}%)\n"
        if ic.get('override_reason'):
            msg += f"└ 🔓 {ic['override_reason']}\n"
        msg += "\n"
    
    if 'smc_summary' in status_data:
        smc = status_data['smc_summary']
        msg += "<b>📊 SMC Паттерны v7.5.1:</b>\n"
        msg += f"├ Order Blocks: {smc.get('ob', 0)}\n"
        msg += f"├ Fair Value Gaps: {smc.get('fvg', 0)}\n"
        msg += f"├ Swing BOS: {smc.get('swing_bos_total', 0)} (All) | ✅ Confirmed: {smc.get('swing_bos_confirmed', 0)}\n"
        msg += f"├ Swing CHoCH: {smc.get('swing_choch_total', 0)} (All) | ✅ Confirmed: {smc.get('swing_choch_confirmed', 0)}\n"
        msg += f"├ Int BOS: {smc.get('int_bos_total', 0)} | ✅ Confirmed: {smc.get('int_bos_confirmed', 0)}\n"
        msg += f"├ Int CHoCH: {smc.get('int_choch_total', 0)} | ✅ Confirmed: {smc.get('int_choch_confirmed', 0)}\n"
        msg += f"└ <b>CONFIRMED TOTAL: {smc.get('confirmed_total', 0)}</b>\n\n"
    
    if 'swing_signals' in status_data and status_data['swing_signals']:
        msg += f"<b>🎯 Swing сигналы:</b> {', '.join(status_data['swing_signals'][:5])}\n"
    
    if 'internal_signals' in status_data and status_data['internal_signals']:
        msg += f"<b>📍 Internal сигналы:</b> {', '.join(status_data['internal_signals'][:5])}\n\n"
    
    if 'near_structures' in status_data:
        msg += f"<b>🎯 Уровни рядом:</b>\n{status_data['near_structures']}\n\n"
    
    if 'reason' in status_data:
        msg += f"<b>💡 Детали:</b>\n<i>{status_data['reason']}</i>\n\n"
    
    if 'llm_verdict' in status_data:
        summary = extract_executive_summary(status_data['llm_verdict'])
        msg += f"<b>🤖 Gemini резюме:</b>\n<i>{summary}</i>\n\n"
    
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
        except:
            confidence = 0
        reason = str(parsed_llm.get('REASON', ''))[:500]
    
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
    Astra Watcher v7.5.1 Smart Cooldown Override
    
    НОВОЕ v7.5.1:
    - Умный кулдаун с override при критических событиях
    - Адаптивный кулдаун: 30 мин (WAIT), 2 часа (BUY/SELL)
    - Override критерии: Swing confirmed, множественное подтверждение, сильный импульс
    
    v7.5.1 СОХРАНЕНО:
    - Проверка confirmed=True в каждом сигнале
    - Impulse/Reversal требуют internal confirmed
    - Защита от wick breaks
    
    v6.0 СОХРАНЕНО:
    - CONFIRMED сигналы для торговли
    - confirmed = пробой ТЕЛОМ (close)
    - bars_ago <= 5 для свежести
    """
    logger.info("📡 [TRIGGER] Цикл анализа v7.5.1 запущен")
    
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
    
    logger.info("🔬 Выполняем SMC анализ v7.5.1...")
    analysis = smc_detector.analyze(candles)
    
    # Жёсткий фикс зон
    current_zone, position_in_range_pct, global_high, global_low = calculate_forced_zones(candles)
    
    swing_trend = analysis.get('trend', 'NEUTRAL')
    internal_trend = analysis.get('internal_trend', 'NEUTRAL')
    current_price = safe_float(candles[-1].get('close', 0), 0.0)
    
    # Impulse Context v5.2
    impulse_context = analysis.get('impulse_context', {})
    has_breakout = impulse_context.get('has_breakout', False)
    is_void_run = impulse_context.get('is_void_run', False)
    is_impulse = impulse_context.get('is_impulse', False)
    market_condition = impulse_context.get('market_condition', 'RANGING')
    impulse_strength = impulse_context.get('impulse_strength', 0)
    override_reason = impulse_context.get('override_reason', '')
    
    logger.info(f"🔧 ЗОНЫ: {current_zone} ({position_in_range_pct:.1f}%) | Range: [{global_low:.2f} - {global_high:.2f}]")
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
    
    # v7.5.1 FIX: Проверяем что сигналы действительно confirmed=True (не wick break!)
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
    
    logger.info(f"v7.5.1 Confirmed Signals (REAL confirmed=True): Swing BOS={has_swing_bos_confirmed}, CHoCH={has_swing_choch_confirmed} | "
                f"Internal BOS={has_int_bos_confirmed}, CHoCH={has_int_choch_confirmed}")
    
    # ========================================================================
    # v5.2 IMPULSE OVERRIDE LOGIC
    # ========================================================================
    
    # DOWNTREND + DISCOUNT
    if swing_trend == "DOWNTREND" and current_zone == "DISCOUNT":
        
        # Проверяем условия для снятия запрета
        if has_breakout:
            is_breakout_impulse = True
            impulse_reasons.append("📉 Пробой минимума 20 свечей")
        
        if is_void_run:
            is_breakout_impulse = True
            impulse_reasons.append("🕳️ Void Run (у края бездны)")
        
        if is_impulse:
            is_breakout_impulse = True
            impulse_reasons.append(f"⚡ Импульс {impulse_strength}%")
        
        if market_condition == 'IMPULSE_TREND':
            is_breakout_impulse = True
            impulse_reasons.append("🔥 IMPULSE_TREND режим")
        
        # v6.0: Используем confirmed сигналы для торговых решений
        if has_swing_bos_confirmed:
            is_breakout_impulse = True
            impulse_reasons.append("💥 CONFIRMED Swing BOS (телом)")
        
        # Экстремальный дискаунт: ищем разворот
        if position_in_range_pct < EXTREME_DISCOUNT_THRESHOLD:
            # v6.0: Ищем бычий CHoCH для разворота (confirmed)
            has_bullish_internal_choch = any(
                'BULLISH' in ch.get('type', '') 
                for ch in analysis.get('internal_choch_confirmed', [])
            )
            
            if has_bullish_internal_choch:
                is_reversal_setup = True
                impulse_reasons.append("🔄 Бычий Internal CHoCH — потенциальный разворот")
        
        # Если нет оснований для снятия запрета — блокируем
        if not is_breakout_impulse and not is_reversal_setup:
            status_data['status'] = 'hard_filter_discount_downtrend'
            status_data['reason'] = (
                f'🛑 КАТЕГОРИЧЕСКИЙ ЗАПРЕТ: Продажа в DISCOUNT ({position_in_range_pct:.1f}%) при DownTrend.\n'
                f'Нет импульса/пробоя для снятия запрета.\n'
                f'Breakout: {has_breakout}, VoidRun: {is_void_run}, Impulse: {is_impulse}'
            )
            send_debug_notification(status_data)
            return
    
    # UPTREND + PREMIUM
    if swing_trend == "UPTREND" and current_zone == "PREMIUM":
        
        if has_breakout:
            is_breakout_impulse = True
            impulse_reasons.append("📈 Пробой максимума 20 свечей")
        
        if is_void_run:
            is_breakout_impulse = True
            impulse_reasons.append("🕳️ Void Run (у вершины)")
        
        if is_impulse:
            is_breakout_impulse = True
            impulse_reasons.append(f"⚡ Импульс {impulse_strength}%")
        
        if market_condition == 'IMPULSE_TREND':
            is_breakout_impulse = True
            impulse_reasons.append("🔥 IMPULSE_TREND режим")
        
        # v6.0: Используем confirmed сигналы
        if has_swing_bos_confirmed:
            is_breakout_impulse = True
            impulse_reasons.append("💥 CONFIRMED Swing BOS (телом)")
        
        # Экстремальный премиум: ищем разворот вниз
        if position_in_range_pct > EXTREME_PREMIUM_THRESHOLD:
            # v6.0: Используем confirmed CHoCH
            has_bearish_internal_choch = any(
                'BEARISH' in ch.get('type', '')
                for ch in analysis.get('internal_choch_confirmed', [])
            )
            
            if has_bearish_internal_choch:
                is_reversal_setup = True
                impulse_reasons.append("🔄 Медвежий Internal CHoCH — потенциальный разворот")
        
        if not is_breakout_impulse and not is_reversal_setup:
            status_data['status'] = 'hard_filter_premium_uptrend'
            status_data['reason'] = (
                f'🛑 КАТЕГОРИЧЕСКИЙ ЗАПРЕТ: Покупка в PREMIUM ({position_in_range_pct:.1f}%) при UpTrend.\n'
                f'Нет импульса/пробоя для снятия запрета.'
            )
            send_debug_notification(status_data)
            return
    
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
    # v7.5.1: SMART COOLDOWN с OVERRIDE для критических событий
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
    # 2. ИЛИ активен impulse override + есть internal confirmed (v7.5.1 fix!)
    # 3. ИЛИ есть reversal setup + есть internal confirmed (v7.5.1 fix!)
    
    has_any_confirmed = (
        has_swing_bos_confirmed or 
        has_swing_choch_confirmed or 
        has_int_bos_confirmed or 
        has_int_choch_confirmed
    )
    
    confirmed_count = smc_summary.get('confirmed_total', 0)
    
    # v7.5.1 FIX: Impulse/Reversal режимы тоже требуют хотя бы internal confirmed
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
    
    # v7.5.1 FIX: Если impulse/reversal но нет даже internal confirmed → SKIP
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
    
    # ========================================================================
    # v7.5.1.1: ПРОВЕРКА API ЛИМИТА ПЕРЕД ВЫЗОВОМ LLM
    # ========================================================================
    can_request, limit_reason, current_count, limit = llm_rate_limiter.can_make_request()
    
    if not can_request:
        logger.error(f"🚨 API LIMIT REACHED: {limit_reason}")
        status_data['status'] = 'api_limit_reached'
        status_data['reason'] = limit_reason
        status_data['llm_count'] = current_count
        status_data['llm_limit'] = limit
        send_debug_notification(status_data)
        return
    
    # Логируем использование API
    if current_count >= limit * 0.8:  # Предупреждение при 80%
        logger.warning(f"⚠️ API Usage: {current_count}/{limit} ({(current_count/limit)*100:.1f}%)")
    else:
        logger.info(f"📊 API Usage: {current_count}/{limit} requests today")
    
    # Вызов Gemini
    ai_response = llm_service.get_signal_verdict(analysis)
    
    # ========================================================================
    # v7.5.1.1: ЗАПИСЫВАЕМ УСПЕШНЫЙ ЗАПРОС К LLM
    # ========================================================================
    new_count = llm_rate_limiter.record_request()
    logger.info(f"✅ LLM request successful. Total today: {new_count}")
    
    # Парсим ответ
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
        logger.info("⚖️ Gemini рекомендует WAIT")
        
        db_service.update_last_wait_time()
        
        try:
            signal_id = db_service.save_signal(signal_data_db)
            logger.info(f"💾 WAIT сохранен (ID: {signal_id})")
        except Exception as e:
            logger.error(f"❌ Ошибка сохранения: {e}")
        
        status_data['status'] = 'wait_decision'
        status_data['reason'] = f'{mode_text} Gemini рекомендует ожидание.\n' + '\n'.join(impulse_reasons)
        status_data['llm_verdict'] = ai_response
        send_debug_notification(status_data)


def start_watcher():
    logger.info("🛰 Astra Watcher v7.5.1 Enhanced Confirmed Signals инициализирован")


if __name__ == "__main__":
    logger.info("🧪 Ручной запуск анализа v7.5.1...")
    run_analysis_cycle()