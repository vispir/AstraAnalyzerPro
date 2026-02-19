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

SIGNAL_COOLDOWN_HOURS = 0.5     # 30 минут после BUY/SELL
WAIT_COOLDOWN_HOURS = 0.25     # 15 минут после WAIT — быстрее переоценка и новый вход
# Менеджер: после N рекомендаций LLM «закрыть сделку» (CLOSE_ALL) — авто-закрытие по причине «слишком рискованно»
MANAGER_CLOSE_ALL_AUTO_CLOSE_AFTER = 3
# Менеджер: после N рекомендаций «частично закрыть» (CLOSE_50) — авто-закрытие; гибрид: также закрытие при откате от 70%+ если LLM уже рекомендовал закрыть
MANAGER_CLOSE_50_AUTO_CLOSE_AFTER = 3
# Гибрид: пороги прогресса к TP — был пик >= PEAK и откат ниже PULLBACK при хотя бы одной рекомендации закрыть
MANAGER_PROGRESS_PEAK = 0.70
MANAGER_PROGRESS_PULLBACK = 0.70
# Минимальный возраст сделки (мин), прежде чем спрашивать LLM: не реагировать на шум M5 сразу после входа
MANAGER_MIN_TRADE_AGE_MINUTES = 10
# Макс. время (мин) ожидания достижения цены входа; если не достигнута — сделка считается отменённой (менеджер скипает, охотник может искать новый вход)
ENTRY_FILL_TIMEOUT_MINUTES = 30
# Минимальный интервал (мин) между вызовами LLM по одной сделке — защита от спама по квоте API
MANAGER_LLM_COOLDOWN_MINUTES = 5
# При 1R+5% (цена прошла 1R с запасом) переводим SL с входа (BE) на уровень 1R — гарантированный плюс даже без LLM
MANAGER_1R_LOCK_MARGIN = 0.05
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

# Менеджер: счётчик рекомендаций CLOSE_ALL по trade_id (при N — авто-закрытие, см. MANAGER_CLOSE_ALL_AUTO_CLOSE_AFTER)
_manager_close_all_count = {}
# Менеджер: счётчик рекомендаций CLOSE_50 по trade_id (при N — авто-закрытие; также для гибрида «откат + хотя бы раз сказал закрыть»)
_manager_close_50_count = {}
# Менеджер: макс. прогресс к TP по trade_id (для гибрида: закрытие при откате от 70%+)
_manager_max_progress = {}
# Менеджер: время последнего вызова LLM по trade_id (кулдаун, чтобы не спамить API)
_manager_llm_last_call_ts = {}
# Менеджер: для каких trade_id уже отправлено одноразовое уведомление «цена входа не достигнута» (сигнальный бот)
_entry_pending_notification_sent = set()
# Менеджер: для каких trade_id уже отправлено одноразовое уведомление «цена входа достигнута, сделка активирована» (Astra Signal Bot)
_entry_filled_notification_sent = set()

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


def compute_atr(candles, period=14):
    """
    ATR(period) по последним свечам. candles = list of dicts с 'high','low','close'.
    Нужно минимум period+1 свечей. Возвращает float или 0.0 при ошибке.
    """
    if not candles or len(candles) < period + 1:
        return 0.0
    try:
        tr_list = []
        for i in range(1, len(candles)):
            h = safe_float(candles[i].get('high'), 0)
            l = safe_float(candles[i].get('low'), 0)
            prev_c = safe_float(candles[i - 1].get('close'), 0)
            if h <= 0 or l <= 0:
                continue
            tr = max(h - l, abs(h - prev_c), abs(l - prev_c))
            tr_list.append(tr)
        if len(tr_list) < period:
            return 0.0
        # ATR = SMA(TR, period) по последним period значениям
        atr = sum(tr_list[-period:]) / period
        return round(atr, 2)
    except Exception as e:
        logger.debug(f"compute_atr error: {e}")
        return 0.0


def get_invalidation_levels(analysis, buffer=0.5):
    """
    Уровни инвалидации сетапа для LLM: SL должен быть за этими уровнями.
    BUY: инвалидация = swing_pivot_low (SL должен быть на или ниже этого уровня).
    SELL: инвалидация = swing_pivot_high (SL должен быть на или выше).
    buffer добавляется к уровню для допуска на вick.
    """
    sw_high = safe_float(analysis.get('swing_pivot_high'), 0)
    sw_low = safe_float(analysis.get('swing_pivot_low'), 0)
    # Дополнительно: из OB/FVG можно взять ближайшие границы (для BUY — минимум низов OB/FVG, для SELL — максимум верхов)
    inv_buy = sw_low - buffer if sw_low > 0 else None
    inv_sell = sw_high + buffer if sw_high > 0 else None
    for ob in analysis.get('order_blocks', [])[:5]:
        bot = safe_float(ob.get('bottom'), 0)
        if bot > 0 and (inv_buy is None or bot < inv_buy + buffer):
            inv_buy = bot - buffer
    for fvg in analysis.get('fvg', [])[:5]:
        bot = safe_float(fvg.get('bottom'), 0)
        if bot > 0 and (inv_buy is None or bot < inv_buy + buffer):
            inv_buy = bot - buffer
    for ob in analysis.get('order_blocks', [])[:5]:
        top = safe_float(ob.get('top'), 0)
        if top > 0 and (inv_sell is None or top > inv_sell - buffer):
            inv_sell = top + buffer
    for fvg in analysis.get('fvg', [])[:5]:
        top = safe_float(fvg.get('top'), 0)
        if top > 0 and (inv_sell is None or top > inv_sell - buffer):
            inv_sell = top + buffer
    return {
        'invalidation_buy': round(inv_buy, 2) if inv_buy is not None else None,
        'invalidation_sell': round(inv_sell, 2) if inv_sell is not None else None,
        'swing_pivot_high': round(sw_high, 2) if sw_high > 0 else None,
        'swing_pivot_low': round(sw_low, 2) if sw_low > 0 else None,
    }


def _trade_open_timestamp(trade):
    """Возвращает Unix timestamp открытия сделки из created_at/timestamp или None."""
    raw = trade.get('created_at') or trade.get('timestamp') or ''
    if not raw:
        return None
    try:
        ts_str = str(raw).strip().replace(' ', 'T').replace('Z', '+00:00')
        if ts_str.endswith('+00') and not ts_str.endswith('+00:00'):
            ts_str = ts_str + ':00'
        created_dt = datetime.fromisoformat(ts_str)
        if created_dt.tzinfo is None:
            created_dt = created_dt.replace(tzinfo=timezone.utc)
        return int(created_dt.timestamp())
    except Exception:
        return None


def is_entry_filled(trade, candles_m5):
    """
    Проверяет, достигла ли цена уровня входа после создания сигнала (по high/low M5-свечей, как детект SL/TP).
    BUY: хотя бы одна свеча с low <= entry_price.
    SELL: хотя бы одна свеча с high >= entry_price.
    """
    entry_price = safe_float(trade.get('entry_price'), 0.0)
    if entry_price <= 0:
        return False
    signal_type = (trade.get('signal_type') or '').upper()
    if signal_type not in ('BUY', 'SELL'):
        return False
    trade_open_ts = _trade_open_timestamp(trade)
    if not trade_open_ts:
        return False
    M5_SEC = 300
    trade_candle_start = (trade_open_ts // M5_SEC) * M5_SEC
    filtered = [c for c in (candles_m5 or []) if c.get('time', 0) >= trade_candle_start]
    if not filtered:
        return False
    if signal_type == 'BUY':
        return any(safe_float(c.get('low'), 0) <= entry_price for c in filtered)
    else:
        return any(safe_float(c.get('high'), 0) >= entry_price for c in filtered)


def is_market_active():
    now = datetime.now(timezone.utc)
    weekday = now.weekday()
    hour = now.hour
    
    if weekday == 5:
        return False
    
    if weekday == 6 and hour < 22:
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


def is_price_near_key_levels(current_price, key_levels, swing_pivot_high=None, swing_pivot_low=None, threshold_percent=0.5):
    """
    Проверяет, находится ли цена близко к ключевым уровням (идея Роберта — чаще запускать анализ).
    Используемые ключевые уровни:
      - Equilibrium_Price — цена равновесия (50% диапазона);
      - High_250 / Low_250 — верх/низ диапазона за 250 баров (LOOKBACK);
      - swing_pivot_high / swing_pivot_low — последний свинг-хай/лоу (Strong/Weak High/Low).
    Возвращает (True, описание) если цена в пределах threshold_percent от любого уровня.
    """
    if not key_levels and swing_pivot_high is None and swing_pivot_low is None:
        return False, "Нет ключевых уровней"
    threshold = current_price * (threshold_percent / 100)
    near = []
    levels_to_check = [
        ("Equilibrium", key_levels.get('Equilibrium_Price') if key_levels else None),
        ("High_250", key_levels.get('High_250') if key_levels else None),
        ("Low_250", key_levels.get('Low_250') if key_levels else None),
        ("Swing High", swing_pivot_high),
        ("Swing Low", swing_pivot_low),
    ]
    for name, level in levels_to_check:
        if level is not None:
            try:
                lv = float(level)
                if lv > 0 and abs(current_price - lv) <= threshold:
                    near.append(f"{name}={lv:.2f}")
            except (TypeError, ValueError):
                pass
    if near:
        return True, ", ".join(near[:5])
    return False, "Далеко от ключевых уровней"


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
    
    # v8.6: confluence и R:R из ответа LLM
    confluence = parsed.get('confluence') or {}
    math_log = parsed.get('math_debug_log') or {}
    calculated_rr = safe_float(math_log.get('calculated_rr'), None)
    
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
        'reason': str(reason)[:4000],
        'low_confidence_override': low_confidence_override,
        'original_action': original_action if low_confidence_override else None,
        'setup_grade': setup_grade,
        'setup_type': setup_type,
        'confluence': confluence,
        'calculated_rr': calculated_rr,
    }


def validate_llm_verdict_strict(verdict, current_price, invalidation_levels, min_rr=1.0, entry_tolerance_pct=0.5):
    """
    v8.6 MUST-HAVE: пост-валидация вердикта LLM.
    - R:R >= min_rr иначе WAIT.
    - SL за уровнем инвалидации (BUY: SL <= inv_buy; SELL: SL >= inv_sell).
    - Entry в пределах entry_tolerance_pct от current_price, иначе подменяем на current_price.
    - Если confluence любой false → WAIT.
    Возвращает (action, entry, sl, tp, reason_override).
    """
    action = verdict.get('action', 'WAIT')
    entry = verdict.get('entry')
    sl = verdict.get('sl')
    tp = verdict.get('tp')
    reason_override = None
    inv_buy = invalidation_levels.get('invalidation_buy')
    inv_sell = invalidation_levels.get('invalidation_sell')
    
    if action not in ('BUY', 'SELL'):
        return action, entry, sl, tp, reason_override
    
    entry_f = safe_float(entry, 0)
    sl_f = safe_float(sl, 0)
    tp_f = safe_float(tp, 0)
    if entry_f <= 0:
        entry_f = current_price
    if sl_f <= 0 or tp_f <= 0:
        return 'WAIT', entry_f, sl_f, tp_f, 'Отсутствуют SL или TP в ответе LLM.'
    
    # Entry: если далеко от текущей цены — подменяем на current_price
    if current_price > 0:
        pct_diff = abs(entry_f - current_price) / current_price * 100
        if pct_diff > entry_tolerance_pct:
            logger.info(f"🔄 v8.6: Entry скорректирован с {entry_f:.2f} на current_price {current_price:.2f} (отклонение {pct_diff:.1f}%)")
            entry_f = current_price
    
    # R:R
    risk = abs(entry_f - sl_f)
    reward = abs(tp_f - entry_f)
    rr = reward / risk if risk > 0 else 0
    if rr < min_rr:
        return 'WAIT', entry_f, sl_f, tp_f, f'R:R={rr:.2f} < {min_rr} (минимальный порог). Риск/прибыль недопустимы.'
    
    # Инвалидация: BUY — SL должен быть на или ниже invalidation_buy; SELL — SL на или выше invalidation_sell
    if action == 'BUY' and inv_buy is not None and sl_f > inv_buy + 0.5:
        return 'WAIT', entry_f, sl_f, tp_f, f'SL для BUY ({sl_f:.2f}) выше уровня инвалидации ({inv_buy:.2f}). Стоп должен быть за структурой.'
    if action == 'SELL' and inv_sell is not None and sl_f < inv_sell - 0.5:
        return 'WAIT', entry_f, sl_f, tp_f, f'SL для SELL ({sl_f:.2f}) ниже уровня инвалидации ({inv_sell:.2f}). Стоп должен быть за структурой.'
    
    # Confluence: если любой false — WAIT
    confluence = verdict.get('confluence') or {}
    for key in ('htf_aligned', 'ltf_trigger_confirmed', 'rr_acceptable', 'invalidation_respected'):
        if key in confluence and confluence[key] is False:
            return 'WAIT', entry_f, sl_f, tp_f, f'Confluence: {key}=false. Условия входа не выполнены.'
    
    return action, entry_f, sl_f, tp_f, reason_override


def extract_executive_summary(ai_response):
    parsed = parse_llm_response(ai_response)
    if parsed and 'executive_summary' in parsed:
        return parsed['executive_summary']
    
    cleaned = ai_response.replace('```json', '').replace('```', '').strip()
    return cleaned


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
        
        # Получаем дополнительные данные
        signal_data = parsed_data.get('signal', {}) if parsed_data else {}
        trade_plan = parsed_data.get('trade_plan', {}) if parsed_data else {}
        math_log = parsed_data.get('math_debug_log', {}) if parsed_data else {}
        
        # ВАЖНО: Используем ПОЛНЫЙ executive_summary из parsed_data для Telegram сообщения
        # (не обрезанный reason из verdict, который ограничен 500 символами для БД)
        full_reason = parsed_data.get('executive_summary', '') if parsed_data else ''
        if not full_reason:
            # Fallback на обрезанный reason из verdict, если executive_summary нет
            full_reason = verdict.get('reason', 'SMC Confirmation')
        reason = escape_html(full_reason)
        
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
        # Форматируем WAIT сигнал с полными данными (полный executive_summary как для BUY/SELL)
        full_reason = parsed_data.get('executive_summary', '') if parsed_data else ''
        reason = escape_html(full_reason or verdict.get('reason', '') or 'Ожидание лучшей возможности')
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
        'proximity_trigger': '🎯',  # v8.5: Вызов LLM по близости к OB/FVG/ликвидности/ключевым уровням
        'smc_sweet_spot': '🎯',  # v8.0: Идеальный SMC сетап
        'no_confirmed_signal': '⏳',  # v6.0: Нет уверенного пробоя
        'impulse_no_confirmation': '⚠️',  # v7.5.2: Impulse/Reversal без internal confirmed
        'low_confidence_wait': '📉',  # v7.6: Низкая уверенность (< 50%)
        'active_trade': '🛑',  # Manager: есть активная сделка
        'trade_closed_sl': '🛑', 'trade_closed_tp': '✅', 'trade_closed_manager_news': '⚠️',
        'trade_closed_manager_risky': '🔴', 'trade_closed_manager_pullback': '✅', 'trade_closed_manager_50': '✅',
        'trade_closed_manager_1r': '✅',
        'move_sl_be': '🔒',
        'trade_cancelled_no_fill': '⏱'
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
        'proximity_trigger': '🎯 PROXIMITY: Близость к OB/FVG/ликвидности/ключевым уровням — вызов LLM',  # v8.5
        'smc_sweet_spot': '🎯 SMC SWEET SPOT: Идеальный сетап',  # v8.0
        'no_confirmed_signal': 'SKIP - Нет CONFIRMED пробоя (LLM не вызван)',  # v6.0
        'impulse_no_confirmation': 'SKIP - Impulse/Reversal без internal confirmed',  # v7.5.2
        'low_confidence_wait': '📉 LOW CONFIDENCE: Сигнал отклонён (< 50%)',  # v7.6
        'active_trade': 'Manager: активная сделка, охотник отключён',
        'trade_closed_sl': 'Manager: Сделка закрыта по SL',
        'trade_closed_tp': 'Manager: Сделка закрыта по TP',
        'trade_closed_manager_news': 'Manager: Сделка закрыта перед новостями',
        'trade_closed_manager_risky': 'Manager: Сделка закрыта (3/3 рекомендаций закрыть полностью)',
        'trade_closed_manager_pullback': 'Manager: Сделка закрыта (откат от пика к TP)',
        'trade_closed_manager_50': 'Manager: Сделка закрыта (3/3 рекомендаций частично закрыть)',
        'trade_closed_manager_1r': 'Manager: Сделка закрыта по рекомендации LLM при 1R (фиксация прибыли)',
        'move_sl_be': 'Manager: SL переведён в безубыток',
        'trade_cancelled_no_fill': 'Manager: Сделка отменена — Entry не достигнут за таймаут'
    }
    
    status = status_data.get('status', 'unknown')
    emoji = status_emoji.get(status, '❓')
    
    now_utc = datetime.now(timezone.utc)
    msg = f"<b>{emoji} ASTRA WATCHER v8.5</b>\n"
    msg += f"<code>UTC: {now_utc.strftime('%Y-%m-%d %H:%M:%S')}</code>\n"
    msg += "━" * 32 + "\n\n"
    
    # v7.5.2: Показываем если был cooldown override
    if status_data.get('cooldown_override'):
        msg += "<b>🔓 COOLDOWN OVERRIDE:</b>\n"
        for reason in status_data.get('override_reasons', []):
            msg += f"└ {escape_html(reason)}\n"
        msg += "\n"
    
    msg += f"<b>📋 Решение:</b> {status_texts.get(status, 'Неизвестно')}\n\n"
    
    # v8.5: явно показываем, почему запустился анализ (триггер: OB/FVG или ключевые уровни)
    trigger_parts = []
    if status_data.get('near_structures') and status_data.get('near_structures') != "Нет близких SMC структур":
        trigger_parts.append("OB/FVG/ликвидность")
    if status_data.get('near_key_levels') and status_data.get('near_key_levels_desc'):
        trigger_parts.append("ключевые уровни")
    if trigger_parts:
        msg += f"<b>🔔 Триггер анализа v8.5:</b> {', '.join(trigger_parts)}\n\n"
    
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
        msg += "<b>📊 SMC Паттерны v8.5:</b>\n"
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
        msg += f"<b>🎯 Уровни рядом (SMC):</b>\n{escape_html(status_data['near_structures'])}\n\n"
    if status_data.get('near_key_levels') and status_data.get('near_key_levels_desc'):
        msg += f"<b>🎯 Ключевые уровни рядом:</b>\n{escape_html(status_data['near_key_levels_desc'])}\n\n"

    # Блок ближайших новостей (используется, в т.ч. при news_block)
    upcoming_news = status_data.get('upcoming_news') or []
    if upcoming_news:
        msg += "<b>📰 Ближайшие важные новости (до 1 часа):</b>\n"
        for item in upcoming_news:
            msg += f"└ {escape_html(str(item))}\n"
        msg += "\n"
    
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
                                swing_signals, internal_signals, smc_summary,
                                verdict=None):
    """
    Готовит данные сигнала для сохранения в БД.
    Если передан verdict (после v8.6 — провалидированный), используются entry/sl/tp/reason из него.
    Иначе извлекаются из parsed_llm (обратная совместимость).
    """
    all_patterns = swing_signals + internal_signals
    patterns_list = list(all_patterns) if all_patterns else []
    signal_label = get_signal_label(llm_action)
    
    if verdict is None:
        verdict = extract_llm_verdict(parsed_llm)
    
    entry_price = safe_float(verdict.get('entry') or current_price, safe_float(current_price, 0.0))
    stop_loss = safe_float(verdict.get('sl'), 0.0)
    take_profit = safe_float(verdict.get('tp'), 0.0)
    confidence = verdict.get('confidence') or 0
    reason = verdict.get('reason') or ""
    
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
    
    # ------------------------------------------------------------------------
    # GUARD 0: Активная позиция (простая проверка по последнему BUY/SELL)
    # ------------------------------------------------------------------------
    try:
        last_trade = db_service.get_last_trade_signal(symbol="XAU_USD")
    except Exception as e:
        last_trade = None
        logger.error(f"Ошибка проверки активной сделки: {e}")
    
    # Если последний торговый сигнал BUY/SELL ещё не помечен как закрытый/отменённый,
    # считаем что сделка активна и Охотник новые входы не ищет.
    if last_trade:
        status = (last_trade.get('status') or '').lower()
        close_ts = last_trade.get('close_timestamp')
        is_terminal = status.startswith('closed') or status.startswith('cancelled') or bool(close_ts)
        if not is_terminal:
            logger.info(
                f"🛑 Active trade detected (id={last_trade.get('id')}, "
                f"type={last_trade.get('signal_type')}, entry={last_trade.get('entry_price')}). "
                f"Hunter is disabled until this trade is closed."
            )
            send_debug_notification({
                'status': 'active_trade',
                'reason': 'Обнаружена активная сделка BUY/SELL. Охотник пропускает поиск нового входа.'
            })
            return
    
    if not is_market_active():
        now_utc = datetime.now(timezone.utc)
        weekday = now_utc.weekday()
        hour = now_utc.hour
        
        if weekday in [0, 1, 2, 3] and hour == 22:
            reason = '⏸ Rollover час (22:00-23:00 UTC)'
        elif weekday == 5:
            reason = 'Суббота - рынок закрыт'
        elif weekday == 6 and hour < 22:
            reason = f'Воскресенье - откроется в 22:00 UTC'
        elif weekday == 4 and hour >= 22:
            reason = 'Пятница после 22:00 - рынок закрыт'
        else:
            reason = 'Рынок закрыт'
        
        send_debug_notification({'status': 'market_closed', 'reason': reason})
        return
    
    if is_news_blockactive():
        # При блокировке по новостям дополнительно собираем список ближайших важных событий
        upcoming_descriptions = []
        if news_service:
            try:
                upcoming_events = news_service.get_upcoming_news(
                    hours=1,
                    currencies=['USD'],
                    impact=['High']
                )
                now_ts = int(datetime.now().timestamp())
                for ev in upcoming_events:
                    ts = ev.get('timestamp')
                    if not ts or ts <= now_ts:
                        continue
                    minutes_left = int((ts - now_ts) / 60)
                    title = (ev.get('title') or '').strip() or 'High impact event'
                    currency = (ev.get('currency') or '').upper()
                    impact = ev.get('impact') or 'High'
                    # Время локально по серверу (для простоты)
                    time_str = datetime.fromtimestamp(ts).strftime('%H:%M')
                    upcoming_descriptions.append(
                        f"{minutes_left} мин ({time_str}) — {title} [{currency} / {impact}]"
                    )
                # Ограничиваемся 5 ближайшими событиями
                upcoming_descriptions = upcoming_descriptions[:5]
            except Exception as e:
                logger.error(f"Ошибка получения списка ближайших новостей: {e}")

        send_debug_notification({
            'status': 'news_block',
            'reason': 'Важные новости USD',
            'upcoming_news': upcoming_descriptions
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
    key_levels = analysis.get('advanced', {}).get('key_levels', {})
    is_near_key_levels, near_key_levels_desc = is_price_near_key_levels(
        current_price, key_levels,
        analysis.get('swing_pivot_high'), analysis.get('swing_pivot_low'),
        threshold_percent=0.5
    )
    
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
        'near_key_levels': is_near_key_levels,
        'near_key_levels_desc': near_key_levels_desc,
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
    
    # Близость к структурам OB/FVG или к ключевым уровням (пропускаем при импульсе или confirmed break)
    # v8.5: триггер LLM также при цене близко к ключевым уровням (Equilibrium, High_250, Low_250, Swing High/Low)
    if not is_near and not is_near_key_levels and not is_breakout_impulse and not has_swing_break_confirmed:
        status_data['status'] = 'not_near_structure'
        status_data['reason'] = (
            f'Цена ${current_price:.2f} далеко от SMC структур (OB/FVG) и от ключевых уровней. '
            f'Нет confirmed break.'
        )
        send_debug_notification(status_data)
        return
    
    # Equilibrium (пропускаем при импульсе ИЛИ при CONFIRMED сигналах ИЛИ при пробое 20 свечей)
    # v8.1 FIX: CONFIRMED сигналы и пробой 20 свечей снимают запрет Equilibrium
    # v8.2: Пробой полосы 48–52% (eq_top/eq_bottom) + internal confirmed — разрешаем сделку от уровня равновесия
    has_equilibrium_breakout = False
    eq = (zones or {}).get('equilibrium') or {}
    eq_top = safe_float(eq.get('top'), 0)
    eq_bottom = safe_float(eq.get('bottom'), 0)
    if eq_top > 0 and eq_bottom > 0:
        if current_price > eq_top or current_price < eq_bottom:
            has_equilibrium_breakout = True
    allow_equilibrium = (
        is_breakout_impulse or has_breakout or has_swing_break_confirmed or has_internal_break_confirmed
        or (has_equilibrium_breakout and has_internal_break_confirmed)
    )
    if current_zone == "EQUILIBRIUM" and (has_equilibrium_breakout and has_internal_break_confirmed):
        logger.info(
            f"⚪ Equilibrium: пробой полосы 48–52% (price={current_price:.2f}, eq_top={eq_top:.2f}, eq_bottom={eq_bottom:.2f}) + internal confirmed — разрешаем сделку."
        )
    if current_zone == "EQUILIBRIUM" and not allow_equilibrium:
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
    # v6.0 + v8.5: УСЛОВИЯ ВЫЗОВА LLM
    # ========================================================================
    # LLM вызывается если выполняется ХОТЯ БЫ ОДНО:
    # 1. Есть хотя бы один CONFIRMED BOS/CHoCH (пробой телом свечи)
    # 2. ИЛИ активен impulse override + есть internal confirmed (v7.5.2)
    # 3. ИЛИ есть reversal setup + есть internal confirmed (v7.5.2)
    # 4. ИЛИ цена близко к OB / FVG / уровню ликвидности (v8.5 — по запросу, чаще анализы)
    # 5. ИЛИ цена близко к ключевым уровням (Equilibrium, High_250, Low_250, Swing High/Low) — идея Роберта
    
    has_any_confirmed = (
        has_swing_bos_confirmed or 
        has_swing_choch_confirmed or 
        has_int_bos_confirmed or 
        has_int_choch_confirmed
    )
    
    # v8.5: близость к SMC или ключевым уровням — достаточное условие для вызова LLM (без обязательного confirmed)
    allow_llm_by_proximity = is_near or is_near_key_levels
    
    # v7.5.2 FIX: Impulse/Reversal режимы тоже требуют хотя бы internal confirmed
    impulse_needs_confirmation = (is_breakout_impulse or is_reversal_setup) and not has_internal_break_confirmed
    
    if not has_any_confirmed and not (is_breakout_impulse or is_reversal_setup) and not allow_llm_by_proximity:
        status_data['status'] = 'no_confirmed_signal'
        status_data['reason'] = (
            f'⏳ Нет CONFIRMED сигналов (confirmed_total={confirmed_count}).\n'
            f'LLM не вызывается без уверенного пробоя (телом свечи) и без близости к OB/FVG/ликвидности/ключевым уровням.\n'
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
    
    if allow_llm_by_proximity and not has_any_confirmed and not (is_breakout_impulse or is_reversal_setup):
        logger.info(f"✅ PROXIMITY TRIGGER (OB/FVG/ликвидность или ключевые уровни) | Preparing to call LLM...")
    else:
        logger.info(f"✅ CONFIRMED SIGNALS: {confirmed_count} | Preparing to call LLM...")

    # ------------------------------------------------------------------------
    # GUARD 1: Память охотника — избегаем повторного LLM вызова,
    # если рынок почти не изменился с последнего анализа и там уже был WAIT.
    # ------------------------------------------------------------------------
    try:
        last_signal = db_service.get_last_signal()
    except Exception as e:
        last_signal = None
        logger.error(f"Ошибка получения последнего сигнала для памяти LLM: {e}")

    if last_signal:
        try:
            last_action = (last_signal.get('signal_type') or '').upper()
            last_price = safe_float(last_signal.get('current_price'), 0.0)
            last_zone = last_signal.get('zone')
            last_trend = last_signal.get('trend') or last_signal.get('swing_trend')
            last_ts_raw = last_signal.get('timestamp')
            
            # Пытаемся распарсить timestamp, но не падаем если формат другой
            last_ts = None
            if last_ts_raw:
                try:
                    last_ts = datetime.fromisoformat(str(last_ts_raw).replace('Z', '+00:00'))
                except Exception:
                    last_ts = None
            
            # Считаем дельту цены и времени
            price_delta = abs(current_price - last_price) if last_price > 0 else None
            price_threshold = current_price * 0.001  # ~0.1% движения
            
            time_ok = True
            if last_ts:
                minutes_ago = (datetime.now(timezone.utc) - last_ts).total_seconds() / 60.0
                # Если анализ был очень давно (> 120 минут), лучше не полагаться на старый контекст
                time_ok = minutes_ago <= 120
            
            # Последний WAIT из-за ошибки API (Gemini не дал ответ) — не считаем вердиктом, не пропускаем LLM
            last_reason = (last_signal.get('llm_reason') or '') + (last_signal.get('llm_full_response') or '')
            last_was_error_fallback = (
                'не смог сформировать' in last_reason or 'Ошибка анализа' in last_reason
            )
            
            if (
                last_action == 'WAIT'
                and not last_was_error_fallback
                and time_ok
                and price_delta is not None
                and price_delta < price_threshold
                and last_zone == current_zone
                and (last_trend or '').upper() == (swing_trend or '').upper()
            ):
                reason = (
                    f"Рынок мало изменился с последнего WAIT сигнала: "
                    f"Δprice={price_delta:.2f} (< {price_threshold:.2f}), "
                    f"zone: {last_zone} → {current_zone}, "
                    f"trend: {last_trend} → {swing_trend}. "
                    f"Пропускаем вызов LLM, чтобы не дублировать предыдущий вердикт."
                )
                logger.info(f"🧠 Hunter memory: skipping LLM call. {reason}")
                status_data['status'] = 'hunter_memory_skip'
                status_data['reason'] = reason
                send_debug_notification(status_data)
                return
        except Exception as e:
            logger.error(f"Ошибка логики памяти LLM (hunter guard): {e}")
    
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
    elif allow_llm_by_proximity:
        mode_text = "🎯 PROXIMITY MODE (OB/FVG/ликвидность/ключевые уровни)"
        status_data['status'] = 'proximity_trigger'
    
    logger.info("=" * 60)
    logger.info(f"🎯 {mode_text} ВСЕ ФИЛЬТРЫ ПРОЙДЕНЫ! Запрашиваем Gemini...")
    logger.info(f"   💰 Цена: ${current_price:.2f}")
    logger.info(f"   📈 Swing Тренд: {swing_trend}")
    logger.info(f"   🎯 Зона: {current_zone} ({position_in_range_pct:.1f}%)")
    if impulse_reasons:
        logger.info(f"   ⚡ Причины: {', '.join(impulse_reasons)}")
    logger.info("=" * 60)
    
    # ========================================================================
    # HTF контекст (H4, H1) для LLM — компактный summary без сырых свечей
    # ========================================================================
    htf_context = {}
    for tf_name, tf_key, bar_limit in [('H4', 'H4', 80), ('H1', 'H1', 120)]:
        try:
            tf_data = oanda_service.get_candles(timeframe=tf_key, limit=bar_limit)
            if 'error' in tf_data or not tf_data.get('candles'):
                continue
            tf_candles = tf_data.get('candles', [])
            tf_analysis = smc_detector.analyze(tf_candles, timeframe=tf_key)
            adv = tf_analysis.get('advanced', {}) or {}
            kl = adv.get('key_levels', {}) or {}
            zones = adv.get('zones', {}) or {}
            zone_name = kl.get('Current_Zone') or zones.get('current_zone', 'UNKNOWN')
            sw_bos = tf_analysis.get('swing_bos_confirmed') or []
            sw_choch = tf_analysis.get('swing_choch_confirmed') or []
            last_bos = sw_bos[-1] if sw_bos else {}
            last_choch = sw_choch[-1] if sw_choch else {}
            # Цены для JSON (избегаем numpy)
            ph = tf_analysis.get('swing_pivot_high')
            pl = tf_analysis.get('swing_pivot_low')
            if ph is not None and (isinstance(ph, float) and (ph != ph or abs(ph) == float('inf'))):
                ph = None
            if pl is not None and (isinstance(pl, float) and (pl != pl or abs(pl) == float('inf'))):
                pl = None
            htf_context[tf_name] = {
                'trend': tf_analysis.get('trend', 'NEUTRAL'),
                'zone': zone_name,
                'key_levels': {
                    'Current_Zone': zone_name,
                    'High_Type': kl.get('High_Type', ''),
                    'Low_Type': kl.get('Low_Type', ''),
                    'swing_pivot_high': float(ph) if ph is not None else None,
                    'swing_pivot_low': float(pl) if pl is not None else None,
                },
                'swing_bos_confirmed_count': len(sw_bos),
                'swing_choch_confirmed_count': len(sw_choch),
                'last_swing_bos': {'type': last_bos.get('type'), 'price': last_bos.get('price'), 'bars_ago': last_bos.get('bars_ago')} if last_bos else None,
                'last_swing_choch': {'type': last_choch.get('type'), 'price': last_choch.get('price'), 'bars_ago': last_choch.get('bars_ago')} if last_choch else None,
            }
        except Exception as e:
            logger.warning(f"⚠️ HTF контекст {tf_name} не собран: {e}")
    if htf_context:
        logger.info(f"✓ HTF контекст для LLM: {list(htf_context.keys())}")

    # ========================================================================
    # v8.4: Создаем оптимизированную версию analysis для LLM (убираем all_* массивы)
    # v8.6 MUST-HAVE: инвалидация, ATR, текущая цена для валидации SL/R:R/entry
    # ========================================================================
    logger.info("📊 Подготавливаем оптимизированные данные M15 для LLM...")
    
    m15_candles = data.get('candles', [])[-50:] if 'error' not in data and data.get('candles') else []
    atr_m15 = compute_atr(m15_candles, 14) if m15_candles else 0.0
    invalidation = get_invalidation_levels(analysis, buffer=0.5)
    if atr_m15 > 0:
        logger.info(f"✓ ATR(14) M15 = {atr_m15:.2f} | Invalidation BUY<={invalidation.get('invalidation_buy')} SELL>={invalidation.get('invalidation_sell')}")
    
    # Создаем легкую версию analysis без массивов all_* (они только для визуализации)
    analysis_light = {
        # Основные данные
        'order_blocks': analysis.get('order_blocks', []),
        'breaker_blocks': analysis.get('breaker_blocks', []),
        'fvg': analysis.get('fvg', []),
        'liquidity': analysis.get('liquidity', []),
        'trend': analysis.get('trend', 'NEUTRAL'),
        'zones': analysis.get('zones', {}),
        
        # Только свежие BOS/CHoCH (не все для визуализации)
        'choch': analysis.get('choch', []),
        'bos': analysis.get('bos', []),
        'internal_choch': analysis.get('internal_choch', []),
        'internal_bos': analysis.get('internal_bos', []),
        'swing_choch': analysis.get('swing_choch', []),
        'swing_bos': analysis.get('swing_bos', []),
        
        # CONFIRMED сигналы
        'choch_confirmed': analysis.get('choch_confirmed', []),
        'bos_confirmed': analysis.get('bos_confirmed', []),
        'internal_choch_confirmed': analysis.get('internal_choch_confirmed', []),
        'internal_bos_confirmed': analysis.get('internal_bos_confirmed', []),
        
        # Advanced данные (key_levels и т.д.)
        'advanced': analysis.get('advanced', {}),
        
        # Импульс контекст (режим импульса, breakout, причины override — для LLM)
        'impulse_context': analysis.get('impulse_context', {}),
        'impulse': analysis.get('impulse', {}),
        
        # v8.6 MUST-HAVE: для валидации SL, R:R и entry
        'current_price': current_price,
        'atr_m15': atr_m15,
        'invalidation_levels': invalidation,
    }
    if htf_context:
        analysis_light['htf_context'] = htf_context
    
    # Добавляем свечи
    if m15_candles:
        analysis_light['candles'] = m15_candles
        logger.info(f"✓ M15 данные подготовлены: {len(m15_candles)} свечей")
    
    # Логируем размер payload
    import json
    payload_size = len(json.dumps(analysis_light, ensure_ascii=False))
    logger.info(f"📦 Размер payload для LLM: {payload_size:,} символов ({payload_size/1024:.1f} KB)")
    
    # Вызов Gemini с оптимизированным analysis
    ai_response = llm_service.get_signal_verdict(analysis_light)
    
    # Парсим ответ (поддержка ОБОИХ форматов через extract_llm_verdict)
    parsed_llm = parse_llm_response(ai_response)
    verdict = extract_llm_verdict(parsed_llm)
    
    # v8.6 MUST-HAVE: пост-валидация R:R, инвалидации SL, confluence, entry
    invalidation_levels = (analysis_light.get('invalidation_levels') or {})
    strict_action, strict_entry, strict_sl, strict_tp, strict_reason = validate_llm_verdict_strict(
        verdict, current_price, invalidation_levels, min_rr=1.0, entry_tolerance_pct=0.5
    )
    if strict_reason:
        logger.warning(f"🛑 v8.6 Strict validation: {verdict.get('action')} → WAIT. {strict_reason}")
        verdict['action'] = 'WAIT'
        verdict['reason'] = f"[v8.6] {strict_reason}. " + (verdict.get('reason') or '')
        verdict['entry'] = strict_entry
        verdict['sl'] = strict_sl
        verdict['tp'] = strict_tp
    else:
        verdict['entry'] = strict_entry
        verdict['sl'] = strict_sl
        verdict['tp'] = strict_tp
    
    llm_action = verdict['action']  # BUY / SELL / WAIT
    is_confirmed = llm_action in ['BUY', 'SELL']
    low_conf_override = verdict.get('low_confidence_override', False)
    original_action = verdict.get('original_action')
    
    # WAIT из-за ошибки API (Gemini не вернул ответ) — сохраняем в БД; в GUARD 1 следующий цикл
    # увидит last_was_error_fallback и вызовет LLM снова независимо от изменения цены
    is_error_fallback = bool(
        ai_response and (
            'не смог сформировать' in ai_response or 'Ошибка анализа' in ai_response
        )
    )
    if is_error_fallback:
        logger.warning("⚠️ Ответ LLM — fallback из-за ошибки API. Сохраняем WAIT; следующий цикл вызовет LLM снова.")
    
    # Логируем с информацией о confidence override
    if low_conf_override:
        logger.warning(f"⚠️ LLM Verdict: {original_action} → WAIT (LOW CONFIDENCE: {verdict['confidence']}%)")
        logger.warning(f"   Entry={verdict['entry']}, SL={verdict['sl']}, TP={verdict['tp']}")
    else:
        logger.info(f"📊 LLM Verdict: action={llm_action}, confidence={verdict['confidence']}%, entry={verdict['entry']}, sl={verdict['sl']}, tp={verdict['tp']}")
    
    # Подготовка данных для БД (v8.6: используем провалидированный verdict — корректные entry/sl/tp)
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
        verdict=verdict
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
            # v8.6: используем провалидированный verdict (скорректированные entry/sl/tp)
            formatted_msg = format_signal_message_with_verdict(ai_response, verdict)
            telegram_service.broadcast_signal(user_ids, formatted_msg)
            telegram_service.broadcast_deals_only(user_ids, formatted_msg)  # Astra Signal Bot — только BUY/SELL
            logger.info(f"📤 Сигнал отправлен {len(user_ids)} пользователям (в т.ч. Signal Bot)")
        
        status_data['status'] = 'signal_sent'
        status_data['reason'] = f'{mode_text} Gemini подтвердил {llm_action}!\n' + '\n'.join(impulse_reasons)
        status_data['llm_verdict'] = ai_response
        send_debug_notification(status_data)
    
    else:
        # WAIT (в т.ч. fallback из-за ошибки API) — сохраняем в БД, чтобы следующий цикл в GUARD 1
        # увидел last_was_error_fallback и вызвал LLM снова независимо от изменения цены
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
        user_ids = db_service.get_all_active_users()
        if user_ids:
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


# ============================================================================
# TRADE MANAGER v1.0 — Управление активной сделкой (без открытия новых)
# ============================================================================

def run_trade_manager_cycle():
    """
    Astra Trade Manager v1.0

    Задача:
    - Управлять ТЕКУЩЕЙ активной сделкой (BUY/SELL):
      - жёсткие правила: стоп, перевод в безубыток;
      - при особых триггерах — запросить LLM для рекомендации (HOLD / MOVE_SL_BE / CLOSE_50 / CLOSE_ALL).

    Триггер: вызывать этот цикл отдельно (например, cron раз в 1–2 минуты).
    """
    logger.info("📡 [TRIGGER] Trade Manager cycle v1.0 запущен")

    # 0. Не работаем в выходные и в ежедневную паузу (22:00–23:00 UTC = 02:00–03:00 UTC+4)
    if not is_market_active():
        logger.info("👀 Manager: рынок закрыт (выходные или пауза 22:00–23:00 UTC), пропуск цикла.")
        return

    # 1. Проверяем, есть ли вообще торговый сигнал BUY/SELL, который ещё не закрыт
    try:
        trade = db_service.get_last_trade_signal(symbol="XAU_USD")
    except Exception as e:
        logger.error(f"❌ Manager: ошибка получения последнего торгового сигнала: {e}")
        return

    if not trade:
        logger.info("👀 Manager: активных сделок нет (последний торговый сигнал не найден).")
        return

    orig_status = trade.get('status') or ''
    status = orig_status.lower()
    close_ts = trade.get('close_timestamp')
    is_terminal = status.startswith('closed') or status.startswith('cancelled') or bool(close_ts)
    if is_terminal:
        logger.info(f"👀 Manager: последний сигнал id={trade.get('id')} уже закрыт/отменён (status={status}).")
        return

    signal_type = (trade.get('signal_type') or '').upper()
    if signal_type not in ('BUY', 'SELL'):
        logger.info(f"👀 Manager: последний сигнал не торговый (type={signal_type}), выходим.")
        return

    trade_id = trade.get('id')
    entry_price = safe_float(trade.get('entry_price'), 0.0)
    stop_loss = safe_float(trade.get('stop_loss'), 0.0)
    take_profit = safe_float(trade.get('take_profit'), 0.0)

    if entry_price <= 0 or stop_loss <= 0 or take_profit <= 0:
        logger.warning(
            f"⚠️ Manager: у сигнала id={trade_id} не заданы entry/sl/tp корректно "
            f"(entry={entry_price}, sl={stop_loss}, tp={take_profit}). Пропускаем."
        )
        return

    risk_amount = abs(entry_price - stop_loss)  # 1R в деньгах (для правила 1R → BE и триггера reached_1r)

    # 2. Получаем текущую цену по M5 (более частый менеджмент)
    try:
        data_m5 = oanda_service.get_candles(timeframe='M5', limit=20)
    except Exception as e:
        logger.error(f"❌ Manager: ошибка получения свечей M5: {e}")
        return

    if "error" in data_m5:
        logger.error(f"❌ Manager: OANDA ошибка M5: {data_m5.get('error')}")
        return

    candles_m5 = data_m5.get('candles') or []
    if not candles_m5:
        logger.warning("⚠️ Manager: пустой массив свечей M5, выходим.")
        return

    current_price = safe_float(candles_m5[-1].get('close'), 0.0)
    # Только свечи после открытия сделки — избегаем фейковых SL/TP по прошлым свечам
    trade_open_ts = None
    trade_created_raw = trade.get('created_at') or trade.get('timestamp') or ''
    if trade_created_raw:
        try:
            ts_str = str(trade_created_raw).strip().replace(' ', 'T').replace('Z', '+00:00')
            if ts_str.endswith('+00') and not ts_str.endswith('+00:00'):
                ts_str = ts_str + ':00'
            created_dt = datetime.fromisoformat(ts_str)
            if created_dt.tzinfo is None:
                created_dt = created_dt.replace(tzinfo=timezone.utc)
            trade_open_ts = int(created_dt.timestamp())
        except Exception:
            trade_open_ts = None
    # Начало M5-свечи, в которую попал вход (5 мин = 300 сек)
    M5_SEC = 300
    trade_candle_start = (trade_open_ts // M5_SEC) * M5_SEC if trade_open_ts else 0
    # Берём последние 6 свечей, но только те, что начались не раньше свечи входа
    MANAGER_CANDLES_LOOKBACK = 6
    recent_m5_raw = candles_m5[-MANAGER_CANDLES_LOOKBACK:] if len(candles_m5) >= MANAGER_CANDLES_LOOKBACK else candles_m5
    recent_m5 = [c for c in recent_m5_raw if c.get('time', 0) >= trade_candle_start] if trade_candle_start else recent_m5_raw
    if not recent_m5:
        recent_m5 = candles_m5[-1:]  # хотя бы текущая свеча

    # 2.0. Детект достижения цены входа (как SL/TP — по high/low свечи): управляем только если вход прокнул
    if not is_entry_filled(trade, candles_m5):
        now_ts = int(datetime.now(timezone.utc).timestamp())
        age_minutes = (now_ts - trade_open_ts) / 60.0 if trade_open_ts else 0
        # Нет created_at — нельзя определить возраст/вход; отменяем, чтобы охотник не блокировался навсегда
        if trade_open_ts is None:
            logger.warning(
                f"⏱ Manager: сделка id={trade_id} без даты создания (created_at). Отменяем — охотник разблокируется."
            )
            db_service.update_signal_result(trade_id, 0.0, current_price, status='cancelled_no_fill')
            _manager_close_all_count.pop(trade_id, None)
            _manager_close_50_count.pop(trade_id, None)
            _manager_max_progress.pop(trade_id, None)
            _manager_llm_last_call_ts.pop(trade_id, None)
            _entry_pending_notification_sent.discard(trade_id)
            _entry_filled_notification_sent.discard(trade_id)
            send_debug_notification({
                'status': 'trade_cancelled_no_fill',
                'reason': 'Нет даты создания сигнала — сделка отменена. Охотник возобновляет поиск.',
                'trade_id': trade_id,
            })
            user_ids_cancel = db_service.get_all_active_users()
            if user_ids_cancel:
                msg_cancel = (
                    f"⏱ <b>Сделка отменена</b>\n"
                    f"id={trade_id} | Нет даты создания сигнала — отменяем. Охотник снова ищет сделку."
                )
                telegram_service.broadcast_deals_only(user_ids_cancel, msg_cancel)
            return
        if age_minutes >= ENTRY_FILL_TIMEOUT_MINUTES:
            # ============================================================
            # КРИТИЧЕСКОЕ ИСПРАВЛЕНИЕ: entry не прокнуло за N минут —
            # формально ОТМЕНЯЕМ сделку в БД, чтобы Охотник (Guard 0)
            # перестал считать её активной и начал искать новый вход.
            # ============================================================
            logger.warning(
                f"⏱ Manager: сделка id={trade_id} ОТМЕНЕНА — цена входа {entry_price:.2f} "
                f"не достигнута за {ENTRY_FILL_TIMEOUT_MINUTES} мин (возраст {age_minutes:.0f} мин). "
                f"Закрываем со статусом cancelled_no_fill."
            )
            db_service.update_signal_result(trade_id, 0.0, current_price, status='cancelled_no_fill')
            _manager_close_all_count.pop(trade_id, None)
            _manager_close_50_count.pop(trade_id, None)
            _manager_max_progress.pop(trade_id, None)
            _manager_llm_last_call_ts.pop(trade_id, None)
            _entry_pending_notification_sent.discard(trade_id)
            _entry_filled_notification_sent.discard(trade_id)
            send_debug_notification({
                'status': 'trade_cancelled_no_fill',
                'reason': (
                    f'Цена входа {entry_price:.2f} не была достигнута за '
                    f'{ENTRY_FILL_TIMEOUT_MINUTES} мин. Сделка отменена. '
                    f'Охотник возобновляет поиск нового входа.'
                ),
                'trade_id': trade_id,
                'entry': entry_price,
                'sl': stop_loss,
                'tp': take_profit,
                'current_price': current_price,
                'signal_type': signal_type
            })
            user_ids_cancel = db_service.get_all_active_users()
            if user_ids_cancel:
                msg_cancel = (
                    f"⏱ <b>Сделка отменена: Entry не достигнут</b>\n"
                    f"id={trade_id} | {signal_type} | entry={entry_price:.2f}\n"
                    f"Текущая цена: {current_price:.2f}\n"
                    f"Таймаут {ENTRY_FILL_TIMEOUT_MINUTES} мин истёк — цена не дошла до уровня входа.\n"
                    f"Охотник снова ищет новую сделку."
                )
                telegram_service.broadcast_deals_only(user_ids_cancel, msg_cancel)
        else:
            logger.info(
                f"⏳ Manager: цена входа {entry_price:.2f} ещё не достигнута по M5 "
                f"(сделка id={trade_id}, возраст {age_minutes:.0f} мин / {ENTRY_FILL_TIMEOUT_MINUTES} мин). Ожидаем."
            )
            # Один раз в сигнальный бот: уведомление о том, что ждём достижения цены входа
            if trade_id not in _entry_pending_notification_sent:
                _entry_pending_notification_sent.add(trade_id)
                user_ids_pending = db_service.get_all_active_users()
                if user_ids_pending:
                    msg_pending = (
                        f"⏳ <b>Ожидание входа</b>\n\n"
                        f"Цена входа: <b>{entry_price:.2f}</b> — не достигнута.\n"
                        f"Ожидаем, пока цена дойдёт до этого уровня. Если цена не дойдёт туда в течение {ENTRY_FILL_TIMEOUT_MINUTES} минут, "
                        f"сделка закроется автоматически."
                    )
                    telegram_service.broadcast_deals_only(user_ids_pending, msg_pending)
        return

    _entry_pending_notification_sent.discard(trade_id)  # вход прокнул — снимаем с учёта одноразового уведомления
    # Один раз в Astra Signal Bot: цена входа достигнута, сделка активирована
    if trade_id not in _entry_filled_notification_sent:
        _entry_filled_notification_sent.add(trade_id)
        user_ids_filled = db_service.get_all_active_users()
        if user_ids_filled:
            msg_filled = (
                f"✅ <b>Вход достигнут — сделка активирована</b>\n\n"
                f"id={trade_id} | {signal_type} | цена входа <b>{entry_price:.2f}</b> достигнута.\n"
                f"Текущая цена: {current_price:.2f}. Менеджер ведёт сделку (SL/TP)."
            )
            telegram_service.broadcast_deals_only(user_ids_filled, msg_filled)
    logger.info(
        f"💼 Manager: активная сделка id={trade_id}, type={signal_type}, "
        f"entry={entry_price:.2f}, sl={stop_loss:.2f}, tp={take_profit:.2f}, "
        f"current={current_price:.2f}"
    )

    # 2.1. Дополнительный SMC-контекст по M5 (для LLM и триггеров)
    analysis_m5 = None
    smc_trend_m5 = 'NEUTRAL'
    smc_zone_m5 = 'UNKNOWN'
    has_opposite_ob = False
    has_opposite_choch = False
    try:
        if smc_detector:
            analysis_m5 = smc_detector.analyze(candles_m5)
            smc_trend_m5 = analysis_m5.get('trend', 'NEUTRAL')
            adv_m5 = analysis_m5.get('advanced', {})
            kl_m5 = adv_m5.get('key_levels', {})
            smc_zone_m5 = kl_m5.get('Current_Zone', 'UNKNOWN')

            # Проверка OB против позиции
            all_obs_m5 = analysis_m5.get('order_blocks', [])
            for ob in all_obs_m5 or []:
                ob_type = (ob.get('type') or '').upper()
                ob_top = safe_float(ob.get('top'), 0.0)
                ob_bottom = safe_float(ob.get('bottom'), 0.0)
                ob_status = ob.get('status', 'active')
                if ob_status not in ('active', 'mitigated'):
                    continue
                if signal_type == 'BUY':
                    # Против BUY считаем важным BEAR_OB над текущей ценой
                    if ob_type == 'BEAR_OB' and ob_bottom >= current_price:
                        has_opposite_ob = True
                        break
                else:
                    # Против SELL считаем важным BULL_OB под текущей ценой
                    if ob_type == 'BULL_OB' and ob_top <= current_price:
                        has_opposite_ob = True
                        break

            # Проверка CHoCH против позиции
            chochs_m5 = (analysis_m5.get('internal_choch_confirmed', []) or []) + \
                        (analysis_m5.get('swing_choch_confirmed', []) or [])
            opp_tag = 'BEARISH' if signal_type == 'BUY' else 'BULLISH'
            for ch in chochs_m5:
                ch_type = ch.get('type') or ''
                if opp_tag in ch_type:
                    has_opposite_choch = True
                    break

            if has_opposite_ob or has_opposite_choch:
                logger.info(
                    f"⚠️ Manager SMC M5: opposite structure detected "
                    f"(OB={has_opposite_ob}, CHoCH={has_opposite_choch}), trend={smc_trend_m5}, zone={smc_zone_m5}"
                )
    except Exception as e:
        logger.error(f"❌ Manager: ошибка SMC-анализа M5: {e}")

    # ------------------------------------------------------------------
    # 3. HARD-CODED CHECKS — Stop Loss и перевод в безубыток
    # ------------------------------------------------------------------

    # 3.1. Стоп-лосс сработал: проверяем по low/high свечей (тень) — если цена коснулась SL, закрываем
    hit_stop = False
    for c in recent_m5:
        h = safe_float(c.get('high'), 0.0)
        l = safe_float(c.get('low'), 0.0)
        if signal_type == 'BUY' and l <= stop_loss:
            hit_stop = True
            break
        if signal_type == 'SELL' and h >= stop_loss:
            hit_stop = True
            break
    if not hit_stop:
        if signal_type == 'BUY' and current_price <= stop_loss:
            hit_stop = True
        elif signal_type == 'SELL' and current_price >= stop_loss:
            hit_stop = True

    if hit_stop:
        # PnL и цена закрытия считаем по уровню SL (как выставил охотник), а не по текущей цене — избегаем проскальзывания в отчёте
        if signal_type == 'BUY':
            result_pnl = stop_loss - entry_price
        else:
            result_pnl = entry_price - stop_loss
        close_price_sl = stop_loss

        logger.warning(
            f"🛑 Manager: цена достигла SL. Закрываем сделку id={trade_id} по уровню SL={close_price_sl:.2f} "
            f"(PnL={result_pnl:.2f}, текущая цена M5={current_price:.2f})."
        )
        db_service.update_signal_result(trade_id, result_pnl, close_price_sl, status='closed_sl')
        _manager_close_all_count.pop(trade_id, None)
        _manager_close_50_count.pop(trade_id, None)
        _manager_max_progress.pop(trade_id, None)
        _manager_llm_last_call_ts.pop(trade_id, None)
        _entry_filled_notification_sent.discard(trade_id)
        send_debug_notification({
            'status': 'trade_closed_sl',
            'reason': f'Цена достигла SL. Сделка закрыта по уровню SL. PnL={result_pnl:.2f}',
            'trade_id': trade_id,
            'entry': entry_price,
            'sl': stop_loss,
            'tp': take_profit,
            'current_price': close_price_sl,
            'signal_type': signal_type
        })
        user_ids_sl = db_service.get_all_active_users()
        if user_ids_sl:
            msg_sl = (
                f"🛑 <b>Сделка закрыта: Stop Loss</b>\n"
                f"id={trade_id} | {signal_type} | entry={entry_price:.2f} → close={close_price_sl:.2f} (уровень SL)\n"
                f"PnL={result_pnl:.2f}\n"
                f"Данные сохранены в БД для анализа и обучения моделей."
            )
            telegram_service.broadcast_deals_only(user_ids_sl, msg_sl)
        return

    # 3.1b. Take Profit достигнут: проверяем по high/low свечей (тень) — если цена коснулась TP, закрываем
    hit_tp = False
    for c in recent_m5:
        h = safe_float(c.get('high'), 0.0)
        l = safe_float(c.get('low'), 0.0)
        if signal_type == 'BUY' and h >= take_profit:
            hit_tp = True
            break
        if signal_type == 'SELL' and l <= take_profit:
            hit_tp = True
            break
    if not hit_tp:
        if signal_type == 'BUY' and current_price >= take_profit:
            hit_tp = True
        elif signal_type == 'SELL' and current_price <= take_profit:
            hit_tp = True

    if hit_tp:
        # PnL и цена закрытия считаем по уровню TP (как выставил охотник), а не по текущей цене — без проскальзывания в отчёте
        if signal_type == 'BUY':
            result_pnl = take_profit - entry_price
        else:
            result_pnl = entry_price - take_profit
        close_price_tp = take_profit

        logger.info(
            f"✅ Manager: цена достигла TP. Закрываем сделку id={trade_id} по уровню TP={close_price_tp:.2f} "
            f"(PnL={result_pnl:.2f}, текущая цена M5={current_price:.2f})."
        )
        db_service.update_signal_result(trade_id, result_pnl, close_price_tp, status='closed_tp')
        _manager_close_all_count.pop(trade_id, None)
        _manager_close_50_count.pop(trade_id, None)
        _manager_max_progress.pop(trade_id, None)
        _manager_llm_last_call_ts.pop(trade_id, None)
        _entry_filled_notification_sent.discard(trade_id)
        send_debug_notification({
            'status': 'trade_closed_tp',
            'reason': f'Цена достигла TP. Сделка закрыта по уровню TP. PnL={result_pnl:.2f}',
            'trade_id': trade_id,
            'entry': entry_price,
            'sl': stop_loss,
            'tp': take_profit,
            'current_price': close_price_tp,
            'signal_type': signal_type
        })
        user_ids_tp = db_service.get_all_active_users()
        if user_ids_tp:
            msg_tp = (
                f"✅ <b>Сделка закрыта: Take Profit</b>\n"
                f"id={trade_id} | {signal_type} | entry={entry_price:.2f} → close={close_price_tp:.2f} (уровень TP)\n"
                f"PnL=+{result_pnl:.2f}\n"
                f"Данные сохранены в БД для анализа и обучения моделей."
            )
            telegram_service.broadcast_deals_only(user_ids_tp, msg_tp)
        return

    # 3.2. Цена прошла >= 70% пути до TP → двигаем SL в безубыток (если ещё не в BE)
    if signal_type == 'BUY':
        total_path = take_profit - entry_price
        progressed = current_price - entry_price
    else:
        total_path = entry_price - take_profit
        progressed = entry_price - current_price

    if total_path > 0:
        progress_ratio = progressed / total_path
    else:
        progress_ratio = 0.0

    # Проверяем, что SL ещё не в безубытке (допускаем небольшое отклонение)
    be_threshold = entry_price * 0.0005  # ~0.05% от цены
    sl_is_be = abs(stop_loss - entry_price) <= be_threshold

    # 3.2a. Правило 1R → BE: как только цена дала 1R в нашу сторону, переводим SL в BE (защита от разворота к SL)
    at_1r = False
    if risk_amount > 0:
        if signal_type == 'BUY':
            at_1r = current_price >= entry_price + risk_amount
        else:
            at_1r = current_price <= entry_price - risk_amount
    if at_1r and not sl_is_be:
        new_sl = entry_price
        logger.info(
            f"🔒 Manager: достигнут 1R в плюс. Переводим SL в BE: {stop_loss:.2f} → {new_sl:.2f}."
        )
        db_service.update_signal_sl_and_status(trade_id, new_sl, status='be_set')
        send_debug_notification({
            'status': 'move_sl_be',
            'reason': 'Цена достигла 1R в плюс. SL переведён в безубыток.',
            'trade_id': trade_id,
            'entry': entry_price,
            'old_sl': stop_loss,
            'new_sl': new_sl,
            'tp': take_profit,
            'current_price': current_price,
            'signal_type': signal_type
        })
        sl_is_be = True  # чтобы блок 70% не дублировал

    if progress_ratio >= 0.7 and not sl_is_be:
        new_sl = entry_price
        logger.info(
            f"🔒 Manager: цена прошла {progress_ratio*100:.1f}% пути до TP. "
            f"Переводим SL в BE: {stop_loss:.2f} → {new_sl:.2f}."
        )
        db_service.update_signal_sl_and_status(trade_id, new_sl, status='be_set')
        send_debug_notification({
            'status': 'move_sl_be',
            'reason': (
                f'Цена прошла {progress_ratio*100:.1f}% до TP. SL переведён в безубыток.'
            ),
            'trade_id': trade_id,
            'entry': entry_price,
            'old_sl': stop_loss,
            'new_sl': new_sl,
            'tp': take_profit,
            'current_price': current_price,
            'signal_type': signal_type
        })
        # После перевода в BE продолжаем менеджмент (вдруг есть LLM-триггеры)

    # 3.2b. При 1R+5%: если SL уже в BE, переводим SL на уровень 1R (гарантированный плюс, защита если LLM не сработает)
    if risk_amount > 0 and sl_is_be:
        at_1r_plus_margin = False
        sl_1r_level = entry_price + risk_amount if signal_type == 'BUY' else entry_price - risk_amount
        if signal_type == 'BUY':
            at_1r_plus_margin = current_price >= entry_price + risk_amount * (1.0 + MANAGER_1R_LOCK_MARGIN)
        else:
            at_1r_plus_margin = current_price <= entry_price - risk_amount * (1.0 + MANAGER_1R_LOCK_MARGIN)
        if at_1r_plus_margin and abs(stop_loss - sl_1r_level) > be_threshold:
            logger.info(
                f"🔒 Manager: цена прошла 1R+{MANAGER_1R_LOCK_MARGIN*100:.0f}%. Переносим SL на уровень 1R: {stop_loss:.2f} → {sl_1r_level:.2f}."
            )
            db_service.update_signal_sl_and_status(trade_id, sl_1r_level, status='be_set')
            send_debug_notification({
                'status': 'move_sl_be',
                'reason': f'Цена прошла 1R+{MANAGER_1R_LOCK_MARGIN*100:.0f}%. SL на уровне 1R — гарантированный плюс.',
                'trade_id': trade_id, 'entry': entry_price, 'old_sl': stop_loss, 'new_sl': sl_1r_level,
                'tp': take_profit, 'current_price': current_price, 'signal_type': signal_type
            })

    # Гибрид: обновляем макс. прогресс к TP и проверяем откат (закрытие при откате от 70%+ если LLM уже рекомендовал закрыть)
    _manager_max_progress[trade_id] = max(_manager_max_progress.get(trade_id, 0.0), progress_ratio)
    max_prog = _manager_max_progress[trade_id]
    close_50_cnt = _manager_close_50_count.get(trade_id, 0)
    close_all_cnt = _manager_close_all_count.get(trade_id, 0)
    if (
        max_prog >= MANAGER_PROGRESS_PEAK
        and progress_ratio < MANAGER_PROGRESS_PULLBACK
        and (close_50_cnt >= 1 or close_all_cnt >= 1)
    ):
        result_pnl = (current_price - entry_price) if signal_type == 'BUY' else (entry_price - current_price)
        db_service.update_signal_result(trade_id, result_pnl, current_price, status='closed_manager')
        _manager_close_all_count.pop(trade_id, None)
        _manager_close_50_count.pop(trade_id, None)
        _manager_max_progress.pop(trade_id, None)
        _manager_llm_last_call_ts.pop(trade_id, None)
        _entry_filled_notification_sent.discard(trade_id)
        send_debug_notification({
            'status': 'trade_closed_manager_pullback',
            'reason': f'Откат от {max_prog*100:.0f}% к TP (сейчас {progress_ratio*100:.0f}%). LLM уже рекомендовал закрыть — фиксация. PnL={result_pnl:.2f}',
            'trade_id': trade_id, 'entry': entry_price, 'sl': stop_loss, 'tp': take_profit,
            'current_price': current_price, 'signal_type': signal_type
        })
        user_ids_pb = db_service.get_all_active_users()
        if user_ids_pb:
            msg_pb = (
                f"✅ ASTRA Manager: сделка id={trade_id} закрыта (откат от пика к TP).\n"
                f"type={signal_type}, entry={entry_price:.2f}, close={current_price:.2f}, PnL={result_pnl:.2f}\n"
                f"Причина: цена была на {max_prog*100:.0f}% пути к TP, откатила до {progress_ratio*100:.0f}%. LLM уже рекомендовал закрыть — фиксация профита.\n"
                f"Охотник снова ищет новую сделку."
            )
            telegram_service.broadcast_deals_only(user_ids_pb, msg_pb)
        logger.info(f"🤖 Manager: сделка id={trade_id} закрыта по гибриду (откат от {max_prog*100:.0f}% к TP).")
        return

    # ------------------------------------------------------------------
    # 4. LLM TRIGGERS — когда вообще имеет смысл спрашивать ИИ
    # ------------------------------------------------------------------

    # 4.1. Цена "застряла" 3 свечи M5 против нас (простая эвристика)
    stuck_against = False
    if len(candles_m5) >= 4:  # 1 текущая + 3 предыдущих
        last_closes = [safe_float(c.get('close'), 0.0) for c in candles_m5[-4:-1]]
        max_close = max(last_closes)
        min_close = min(last_closes)
        range_size = max_close - min_close

        small_range = current_price * 0.001  # ~0.1% диапазона

        if signal_type == 'BUY':
            # Консолидация ниже входа и без прогресса вверх
            if all(c <= entry_price for c in last_closes) and range_size < small_range:
                stuck_against = True
        else:
            # Консолидация выше входа и без прогресса вниз
            if all(c >= entry_price for c in last_closes) and range_size < small_range:
                stuck_against = True

    # 4.2. Приближение новостей (High-impact USD в ближайшие 5 минут)
    news_soon = False
    try:
        if news_service:
            upcoming_news = news_service.get_upcoming_news(hours=1, currencies=['USD'], impact=['High'])
            now_ts = int(datetime.now(timezone.utc).timestamp())
            for event in upcoming_news:
                ts = event.get('timestamp')
                if ts and 0 < (ts - now_ts) <= 5 * 60:
                    news_soon = True
                    break
    except Exception as e:
        logger.error(f"❌ Manager: ошибка проверки новостей: {e}")

    # 4.3. Противоположная структура SMC на M5 (OB/CHoCH против позиции)
    opposite_structure_trigger = has_opposite_ob or has_opposite_choch

    # 4.3b. Триггер «достигнут 1R» — цена в плюсе на 1R (для LLM: можно рекомендовать закрыть и зафиксировать прибыль)
    reached_1r = False
    if risk_amount > 0:
        if signal_type == 'BUY':
            reached_1r = current_price >= entry_price + risk_amount
        else:
            reached_1r = current_price <= entry_price - risk_amount

    # 4.4. Авто-закрытие перед новостями при большом профите
    if news_soon and progress_ratio >= 0.9:
        if signal_type == 'BUY':
            result_pnl = current_price - entry_price
        else:
            result_pnl = entry_price - current_price

        logger.warning(
            f"🛑 Manager: крупные новости и сделка почти у TP "
            f"(progress={progress_ratio*100:.1f}%). Закрываем id={trade_id} по {current_price:.2f}, "
            f"PnL={result_pnl:.2f}."
        )
        db_service.update_signal_result(trade_id, result_pnl, current_price, status='closed_manager')
        _manager_close_all_count.pop(trade_id, None)
        _manager_close_50_count.pop(trade_id, None)
        _manager_max_progress.pop(trade_id, None)
        _manager_llm_last_call_ts.pop(trade_id, None)
        _entry_filled_notification_sent.discard(trade_id)
        send_debug_notification({
            'status': 'trade_closed_manager_news',
            'reason': (
                f'High-impact новости в ближайшие минуты, а цена прошла {progress_ratio*100:.1f}% '
                f'пути до TP. Сделка закрыта менеджером.'
            ),
            'trade_id': trade_id,
            'entry': entry_price,
            'sl': stop_loss,
            'tp': take_profit,
            'current_price': current_price,
            'signal_type': signal_type
        })
        user_ids_news = db_service.get_all_active_users()
        if user_ids_news:
            msg_news = (
                f"⚠️ ASTRA Manager: сделка id={trade_id} закрыта перед важными новостями.\n"
                f"type={signal_type}, entry={entry_price:.2f}, close={current_price:.2f}, "
                f"PnL={result_pnl:.2f}\n"
                f"Причина: крупные новости и высокий прогресс к TP."
            )
            telegram_service.broadcast_deals_only(user_ids_news, msg_news)
        return

    # Если нет ни одного триггера — менеджер ограничивается жёсткими правилами
    if not (stuck_against or news_soon or opposite_structure_trigger or reached_1r):
        logger.info("🤖 Manager: LLM не вызываем — нет триггеров (stuck/news/structure/reached_1r).")
        return

    # Сделка не должна быть "свежей": не спрашиваем LLM сразу после входа (шум M5, ложные BE/CLOSE_50)
    trade_created = trade.get('created_at') or trade.get('timestamp') or ''
    trade_age_minutes = None
    try:
        if trade_created:
            # Supabase: "2026-02-11 17:12:25.271836+00" или ISO с T
            ts_str = str(trade_created).strip().replace(' ', 'T').replace('Z', '+00:00')
            if ts_str.endswith('+00') and not ts_str.endswith('+00:00'):
                ts_str = ts_str + ':00'
            created_dt = datetime.fromisoformat(ts_str)
            if created_dt.tzinfo is None:
                created_dt = created_dt.replace(tzinfo=timezone.utc)
            trade_age_minutes = (datetime.now(timezone.utc) - created_dt).total_seconds() / 60.0
    except Exception as e:
        logger.debug(f"Manager: не удалось вычислить возраст сделки: {e}")
    if trade_age_minutes is not None and trade_age_minutes < MANAGER_MIN_TRADE_AGE_MINUTES:
        logger.info(
            f"🤖 Manager: сделка id={trade_id} свежая ({trade_age_minutes:.0f} мин < {MANAGER_MIN_TRADE_AGE_MINUTES} мин), "
            "LLM не вызываем — даём сделке время развиться."
        )
        return

    # Кулдаун вызовов LLM по одной сделке — не чаще чем раз в N минут (защита квоты API)
    now_ts = int(datetime.now(timezone.utc).timestamp())
    last_llm_ts = _manager_llm_last_call_ts.get(trade_id, 0)
    cooldown_sec = MANAGER_LLM_COOLDOWN_MINUTES * 60
    if last_llm_ts and (now_ts - last_llm_ts) < cooldown_sec:
        logger.info(
            f"🤖 Manager: кулдаун LLM для id={trade_id} "
            f"(прошло {(now_ts - last_llm_ts) // 60} мин < {MANAGER_LLM_COOLDOWN_MINUTES} мин). Пропускаем вызов."
        )
        return

    # ------------------------------------------------------------------
    # HTF контекст (H4, H1) для менеджера — компактный summary для LLM
    # ------------------------------------------------------------------
    manager_htf_context = {}
    if smc_detector:
        for tf_name, tf_key, bar_limit in [('H4', 'H4', 80), ('H1', 'H1', 120)]:
            try:
                tf_data = oanda_service.get_candles(timeframe=tf_key, limit=bar_limit)
                if 'error' in tf_data or not tf_data.get('candles'):
                    continue
                tf_candles = tf_data.get('candles', [])
                tf_analysis = smc_detector.analyze(tf_candles, timeframe=tf_key)
                adv = tf_analysis.get('advanced', {}) or {}
                kl = adv.get('key_levels', {}) or {}
                zones = adv.get('zones', {}) or {}
                zone_name = kl.get('Current_Zone') or zones.get('current_zone', 'UNKNOWN')
                sw_bos = tf_analysis.get('swing_bos_confirmed') or []
                sw_choch = tf_analysis.get('swing_choch_confirmed') or []
                last_bos = sw_bos[-1] if sw_bos else {}
                last_choch = sw_choch[-1] if sw_choch else {}
                ph = tf_analysis.get('swing_pivot_high')
                pl = tf_analysis.get('swing_pivot_low')
                if ph is not None and isinstance(ph, float) and (ph != ph or abs(ph) == float('inf')):
                    ph = None
                if pl is not None and isinstance(pl, float) and (pl != pl or abs(pl) == float('inf')):
                    pl = None
                manager_htf_context[tf_name] = {
                    'trend': tf_analysis.get('trend', 'NEUTRAL'),
                    'zone': zone_name,
                    'key_levels': {
                        'Current_Zone': zone_name,
                        'High_Type': kl.get('High_Type', ''),
                        'Low_Type': kl.get('Low_Type', ''),
                        'swing_pivot_high': float(ph) if ph is not None else None,
                        'swing_pivot_low': float(pl) if pl is not None else None,
                    },
                    'swing_bos_confirmed_count': len(sw_bos),
                    'swing_choch_confirmed_count': len(sw_choch),
                    'last_swing_bos': {'type': last_bos.get('type'), 'price': last_bos.get('price'), 'bars_ago': last_bos.get('bars_ago')} if last_bos else None,
                    'last_swing_choch': {'type': last_choch.get('type'), 'price': last_choch.get('price'), 'bars_ago': last_choch.get('bars_ago')} if last_choch else None,
                }
            except Exception as e:
                logger.debug(f"Manager HTF {tf_name}: {e}")
    if manager_htf_context:
        logger.info(f"🤖 Manager: HTF контекст для LLM — {list(manager_htf_context.keys())}")

    # ------------------------------------------------------------------
    # 5. Вызов LLM Manager-агента
    # ------------------------------------------------------------------
    trade_context = {
        'id': trade_id,
        'signal_type': signal_type,
        'entry_price': entry_price,
        'stop_loss': stop_loss,
        'take_profit': take_profit,
        'current_price': current_price,
        'progress_ratio': progress_ratio,
    }

    triggers = {
        'stuck_against': stuck_against,
        'news_soon': news_soon,
        'opposite_structure': opposite_structure_trigger,
        'reached_1r': reached_1r,
    }

    # Технический контекст для LLM (M5 + опционально H4/H1 summary)
    technical_context = {
        'timeframe': 'M5',
        'candles': candles_m5[-20:],
        'smc_m5': {
            'trend': smc_trend_m5,
            'zone': smc_zone_m5,
            'has_opposite_ob': has_opposite_ob,
            'has_opposite_choch': has_opposite_choch
        }
    }
    if manager_htf_context:
        technical_context['htf_context'] = manager_htf_context

    try:
        ai_response = llm_service.manage_active_trade(
            trade_context=trade_context,
            technical_context=technical_context,
            triggers=triggers
        )
    except Exception as e:
        logger.error(f"❌ Manager: ошибка вызова LLM manage_active_trade: {e}")
        return

    if not ai_response:
        logger.warning("⚠️ Manager: пустой ответ от LLM (manage_active_trade).")
        return

    _manager_llm_last_call_ts[trade_id] = now_ts
    parsed = parse_llm_response(ai_response)
    # Поддерживаем как 'manager_action', так и просто 'action'
    mgr_action = (parsed.get('manager_action') or parsed.get('action') or '').upper()
    mgr_reason = parsed.get('reason') or parsed.get('executive_summary') or ''

    logger.info(f"🤖 Manager LLM Verdict: action={mgr_action}, reason={mgr_reason}")

    # Реакция на решение LLM:
    # - MOVE_SL_BE: реально двигаем SL в BE (если ещё не там)
    # - CLOSE_ALL: уведомление в TG каждый раз; после N-й рекомендации — авто-закрытие (MANAGER_CLOSE_ALL_AUTO_CLOSE_AFTER)
    # - CLOSE_50: только рекомендация через Telegram
    # - HOLD: ничего не делаем

    # ВАЖНО: уведомления от Manager отправляем ТОЛЬКО в Astra Signal Bot
    # (второй бот, который отвечает за торговые сигналы), поэтому
    # используем broadcast_deals_only, а не основной broadcast_signal.
    user_ids = db_service.get_all_active_users()

    if mgr_action == 'MOVE_SL_BE' and not sl_is_be:
        new_sl = entry_price
        db_service.update_signal_sl_and_status(trade_id, new_sl, status='be_set')
        msg = (
            f"🛡 ASTRA Manager: LLM рекомендует перевести SL в BE.\n"
            f"Сделка id={trade_id}, type={signal_type}\n"
            f"SL: {stop_loss:.2f} → {new_sl:.2f}\n"
            f"Причина: {mgr_reason}"
        )
        if user_ids:
            telegram_service.broadcast_deals_only(user_ids, msg)

    elif mgr_action == 'CLOSE_ALL':
        # Исключение: при 1R в плюс одна рекомендация «закрыть» — сразу фиксируем прибыль (не ждём 3/3)
        if reached_1r:
            result_pnl = (current_price - entry_price) if signal_type == 'BUY' else (entry_price - current_price)
            db_service.update_signal_result(trade_id, result_pnl, current_price, status='closed_manager')
            _manager_close_all_count.pop(trade_id, None)
            _manager_close_50_count.pop(trade_id, None)
            _manager_max_progress.pop(trade_id, None)
            _manager_llm_last_call_ts.pop(trade_id, None)
            _entry_filled_notification_sent.discard(trade_id)
            send_debug_notification({
                'status': 'trade_closed_manager_1r',
                'reason': f'LLM рекомендовала закрыть при 1R — фиксация прибыли. PnL={result_pnl:.2f}',
                'trade_id': trade_id, 'entry': entry_price, 'sl': stop_loss, 'tp': take_profit,
                'current_price': current_price, 'signal_type': signal_type
            })
            msg = (
                f"✅ ASTRA Manager: сделка id={trade_id} закрыта по рекомендации LLM при 1R (фиксация прибыли).\n"
                f"type={signal_type}, entry={entry_price:.2f}, close={current_price:.2f}, PnL={result_pnl:.2f}\n"
                f"Причина: {mgr_reason}\n"
                f"Охотник снова ищет новую сделку."
            )
            if user_ids:
                telegram_service.broadcast_deals_only(user_ids, msg)
            logger.info(f"🤖 Manager: сделка id={trade_id} закрыта при 1R по рекомендации LLM (CLOSE_ALL).")
        else:
            # Считаем рекомендации «закрыть сделку». После N-й — авто-закрытие (3/3)
            _manager_close_all_count[trade_id] = _manager_close_all_count.get(trade_id, 0) + 1
            count = _manager_close_all_count[trade_id]

            if count >= MANAGER_CLOSE_ALL_AUTO_CLOSE_AFTER:
                # Авто-закрытие: слишком рискованно (N рекомендаций на закрытие) — 3/3
                result_pnl = (current_price - entry_price) if signal_type == 'BUY' else (entry_price - current_price)
                db_service.update_signal_result(trade_id, result_pnl, current_price, status='closed_manager')
                _manager_close_all_count.pop(trade_id, None)
                _manager_close_50_count.pop(trade_id, None)
                _manager_max_progress.pop(trade_id, None)
                _manager_llm_last_call_ts.pop(trade_id, None)
                _entry_filled_notification_sent.discard(trade_id)
                send_debug_notification({
                    'status': 'trade_closed_manager_risky',
                    'reason': f'Слишком рискованно: {MANAGER_CLOSE_ALL_AUTO_CLOSE_AFTER} рекомендаций LLM на закрытие (3/3). PnL={result_pnl:.2f}',
                    'trade_id': trade_id,
                    'entry': entry_price,
                    'sl': stop_loss,
                    'tp': take_profit,
                    'current_price': current_price,
                    'signal_type': signal_type
                })
                msg = (
                    f"🔴 ASTRA Manager: сделка id={trade_id} закрыта автоматически (3/3).\n"
                    f"type={signal_type}, entry={entry_price:.2f}, close={current_price:.2f}, PnL={result_pnl:.2f}\n"
                    f"Причина: LLM 3 раза рекомендовала закрыть сделку полностью — слишком рискованно.\n"
                    f"Охотник снова ищет новую сделку."
                )
                if user_ids:
                    telegram_service.broadcast_deals_only(user_ids, msg)
                logger.info(f"🤖 Manager: сделка id={trade_id} закрыта по {count}-й рекомендации CLOSE_ALL (3/3).")
            else:
                # Уведомление с нумерацией 1/3, 2/3
                msg = (
                    f"⚠️ ASTRA Manager: LLM рекомендует ЗАКРЫТЬ сделку id={trade_id} полностью ({count}/{MANAGER_CLOSE_ALL_AUTO_CLOSE_AFTER}).\n"
                    f"type={signal_type}, entry={entry_price:.2f}, current={current_price:.2f}\n"
                    f"Причина: {mgr_reason}\n\n"
                    f"После {MANAGER_CLOSE_ALL_AUTO_CLOSE_AFTER} рекомендаций (3/3) сделка будет закрыта автоматически."
                )
                if user_ids:
                    telegram_service.broadcast_deals_only(user_ids, msg)

    elif mgr_action == 'CLOSE_50':
        # Исключение: при 1R в плюс одна рекомендация «частично закрыть» — закрываем сразу (фиксация прибыли)
        if reached_1r:
            result_pnl = (current_price - entry_price) if signal_type == 'BUY' else (entry_price - current_price)
            db_service.update_signal_result(trade_id, result_pnl, current_price, status='closed_manager')
            _manager_close_all_count.pop(trade_id, None)
            _manager_close_50_count.pop(trade_id, None)
            _manager_max_progress.pop(trade_id, None)
            _manager_llm_last_call_ts.pop(trade_id, None)
            _entry_filled_notification_sent.discard(trade_id)
            send_debug_notification({
                'status': 'trade_closed_manager_1r',
                'reason': f'LLM рекомендовала зафиксировать прибыль при 1R. PnL={result_pnl:.2f}',
                'trade_id': trade_id, 'entry': entry_price, 'sl': stop_loss, 'tp': take_profit,
                'current_price': current_price, 'signal_type': signal_type
            })
            msg = (
                f"✅ ASTRA Manager: сделка id={trade_id} закрыта по рекомендации LLM при 1R (фиксация прибыли).\n"
                f"type={signal_type}, entry={entry_price:.2f}, close={current_price:.2f}, PnL={result_pnl:.2f}\n"
                f"Причина: {mgr_reason}\n"
                f"Охотник снова ищет новую сделку."
            )
            if user_ids:
                telegram_service.broadcast_deals_only(user_ids, msg)
            logger.info(f"🤖 Manager: сделка id={trade_id} закрыта при 1R по рекомендации LLM (CLOSE_50).")
        else:
            _manager_close_50_count[trade_id] = _manager_close_50_count.get(trade_id, 0) + 1
            count_50 = _manager_close_50_count[trade_id]

            if count_50 >= MANAGER_CLOSE_50_AUTO_CLOSE_AFTER:
                # 3/3 — авто-закрытие по третьей рекомендации «частично закрыть»
                result_pnl = (current_price - entry_price) if signal_type == 'BUY' else (entry_price - current_price)
                db_service.update_signal_result(trade_id, result_pnl, current_price, status='closed_manager')
                _manager_close_all_count.pop(trade_id, None)
                _manager_close_50_count.pop(trade_id, None)
                _manager_max_progress.pop(trade_id, None)
                _manager_llm_last_call_ts.pop(trade_id, None)
                _entry_filled_notification_sent.discard(trade_id)
                send_debug_notification({
                    'status': 'trade_closed_manager_50',
                    'reason': f'{MANAGER_CLOSE_50_AUTO_CLOSE_AFTER} рекомендаций частично закрыть (3/3). Сделка закрыта. PnL={result_pnl:.2f}',
                    'trade_id': trade_id, 'entry': entry_price, 'sl': stop_loss, 'tp': take_profit,
                    'current_price': current_price, 'signal_type': signal_type
                })
                msg = (
                    f"✅ ASTRA Manager: сделка id={trade_id} закрыта автоматически (3/3).\n"
                    f"type={signal_type}, entry={entry_price:.2f}, close={current_price:.2f}, PnL={result_pnl:.2f}\n"
                    f"Причина: LLM 3 раза рекомендовала частично закрыть — сделка закрыта по текущей цене.\n"
                    f"Охотник снова ищет новую сделку."
                )
                if user_ids:
                    telegram_service.broadcast_deals_only(user_ids, msg)
                logger.info(f"🤖 Manager: сделка id={trade_id} закрыта по {count_50}-й рекомендации CLOSE_50 (3/3).")
            else:
                msg = (
                    f"ℹ️ ASTRA Manager: LLM рекомендует частично закрыть (50%) сделку id={trade_id} ({count_50}/{MANAGER_CLOSE_50_AUTO_CLOSE_AFTER}).\n"
                    f"type={signal_type}, entry={entry_price:.2f}, current={current_price:.2f}\n"
                    f"Причина: {mgr_reason}\n\n"
                    f"После {MANAGER_CLOSE_50_AUTO_CLOSE_AFTER} рекомендаций (3/3) сделка будет закрыта автоматически."
                )
                if user_ids:
                    telegram_service.broadcast_deals_only(user_ids, msg)

    else:
        # HOLD или неизвестное действие — просто логируем и, опционально, уведомляем
        logger.info(f"🤝 Manager: действие LLM — HOLD/NO_ACTION (action={mgr_action}).")