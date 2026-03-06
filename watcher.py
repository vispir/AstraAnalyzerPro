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
from services.chart_service import chart_service
import pandas as pd

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

# Range Manager: в прошлом цикле подтвердили пробой активного диапазона — не воскрешать старый диапазон (Проблема 4)
_breakout_confirmed_previous_cycle = False
_htf_rejection_watch = None  # хранит спец-сценарий HTF фильтра для последующего Range Rejection

# Task 7: Cooldown after stop loss (prevent cascade entries). 30 min для BUY/SELL после закрытия по SL.
_last_stop_loss_time = {}
_last_stop_loss_structure = {}
COOLDOWN_MINUTES_AFTER_SL = 30

# Task 8: Trade limits — 3 total per day; 2 per direction per SESSION (Fix: за сессию)
# Лимит 3/3 сбрасывается в 02:00 по Астрахани (UTC+4), чтобы не тратить лимит на сделки после полуночи до открытия следующего торгового дня
TRADE_LIMIT_TZ_OFFSET_HOURS = 4  # Астрахань UTC+4
TRADE_LIMIT_RESET_HOUR_ASTRAKHAN = 2  # 02:00 по Астрахани — сброс

def _get_trade_limit_date():
    """Дата «торгового дня» для лимита 3/3: по Астрахани (UTC+4), сброс в 02:00. До 02:00 считаем предыдущий календарный день."""
    now_utc = datetime.now(timezone.utc)
    now_local = now_utc + timedelta(hours=TRADE_LIMIT_TZ_OFFSET_HOURS)
    if now_local.hour < TRADE_LIMIT_RESET_HOUR_ASTRAKHAN:
        return (now_local - timedelta(days=1)).date()
    return now_local.date()

_trades_today = {
    'count': 0,
    'reset_date': _get_trade_limit_date()
}
_trades_by_session = {}  # session_key (date_sessionname) -> {'BUY': 0, 'SELL': 0}

# Task 9: Manager recommendation history (for escalation logic)
_recommendation_history = {}

# P2 Double TP: trade_ids for which TP1 was already reached (50% partial profit locked)
_trade_tp1_reached = set()


def add_recommendation(trade_id, action, reasoning):
    """Task 9: Store manager recommendation for this trade. Keep last 5."""
    if trade_id not in _recommendation_history:
        _recommendation_history[trade_id] = []
    _recommendation_history[trade_id].append({
        'timestamp': datetime.now(timezone.utc),
        'action': (action or '').upper(),
        'reasoning': (reasoning or '')[:200]
    })
    _recommendation_history[trade_id] = _recommendation_history[trade_id][-5:]


def get_recommendation_context(trade_id):
    """Task 9: Format last 3 recommendations as context for LLM."""
    if trade_id not in _recommendation_history:
        return "<previous_recommendations>No previous recommendations for this trade.</previous_recommendations>"
    history = _recommendation_history[trade_id][-3:]
    if not history:
        return "<previous_recommendations>No previous recommendations.</previous_recommendations>"
    lines = ["<previous_recommendations>", f"Last {len(history)} manager decisions for this trade:\n"]
    for i, rec in enumerate(history, 1):
        mins = (datetime.now(timezone.utc) - rec['timestamp']).total_seconds() / 60
        time_str = f"{int(mins)} min ago" if mins < 60 else f"{mins/60:.1f} hours ago"
        lines.append(f"{i}. [{time_str}] {rec['action']}\n   Reasoning: \"{rec['reasoning']}\"\n\n")
    lines.append("ESCALATION LOGIC: If previous calls suggested caution (MOVE_SL_BE, CLOSE_50) and situation hasn't improved, escalate. If 2+ consecutive HOLD but trade isn't progressing, consider exit. If already CLOSE_50 and price stuck, CLOSE_ALL.\n</previous_recommendations>")
    return "".join(lines)


def check_escalation_triggers(trade_id):
    """Task 9: If manager recommended close 2+ times in last 3 calls, auto-escalate to CLOSE_ALL."""
    if trade_id not in _recommendation_history:
        return False, ""
    history = _recommendation_history[trade_id][-3:]
    if len(history) < 2:
        return False, ""
    close_count = sum(1 for h in history if 'CLOSE' in (h.get('action') or ''))
    if close_count >= 2:
        return True, f"Manager recommended close {close_count} times in last 3 calls. Auto-escalating to CLOSE_ALL."
    return False, ""

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
    inv_buy = None
    inv_sell = None
    current_price = safe_float(analysis.get('current_price'), 0)
    atr = safe_float(analysis.get('atr_m15'), 8.0) or 8.0
    max_sl_distance = atr * 4.5

    # BUY: ищем ближайший BULL OB/FVG ниже цены
    for ob in analysis.get('order_blocks', []):
        if 'BULL' in (ob.get('type') or ob.get('ob_type') or '').upper():
            bot = safe_float(ob.get('bottom'), 0)
            if bot <= 0:
                continue
            if current_price > 0 and (current_price - bot) > max_sl_distance:
                continue  # слишком далеко
            if bot > 0 and (inv_buy is None or bot > inv_buy):
                inv_buy = bot - buffer  # берём ближайший (самый высокий снизу)

    for fvg in analysis.get('fvg', []):
        if 'BULL' in (fvg.get('type') or '').upper():
            bot = safe_float(fvg.get('bottom'), 0)
            if bot <= 0:
                continue
            if current_price > 0 and (current_price - bot) > max_sl_distance:
                continue
            if inv_buy is None or bot > inv_buy:
                inv_buy = bot - buffer

    # SELL: ищем ближайший BEAR OB/FVG выше цены
    for ob in analysis.get('order_blocks', []):
        if 'BEAR' in (ob.get('type') or ob.get('ob_type') or '').upper():
            top = safe_float(ob.get('top'), 0)
            if top <= 0:
                continue
            if current_price > 0 and (top - current_price) > max_sl_distance:
                continue
            if top > 0 and (inv_sell is None or top < inv_sell):
                inv_sell = top + buffer  # берём ближайший (самый низкий сверху)

    for fvg in analysis.get('fvg', []):
        if 'BEAR' in (fvg.get('type') or '').upper():
            top = safe_float(fvg.get('top'), 0)
            if top <= 0:
                continue
            if current_price > 0 and (top - current_price) > max_sl_distance:
                continue
            if inv_sell is None or top < inv_sell:
                inv_sell = top + buffer

    # Fallback: ATR-based если OB/FVG не найдены
    if inv_buy is None and current_price > 0:
        inv_buy = round(current_price - atr * 2.0, 2)
    if inv_sell is None and current_price > 0:
        inv_sell = round(current_price + atr * 2.0, 2)

    sw_high = safe_float(analysis.get('swing_pivot_high'), 0)
    sw_low = safe_float(analysis.get('swing_pivot_low'), 0)

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
    
    Fallback: если key_levels пустой, используем только swing_pivot_high/low.
    """
    # Fallback: если key_levels пустой, проверяем только swing pivots
    has_any_levels = bool(key_levels) or swing_pivot_high is not None or swing_pivot_low is not None
    if not has_any_levels:
        return False, "Нет ключевых уровней"
    
    threshold = current_price * (threshold_percent / 100)
    near = []
    
    # Уровни из key_levels (если есть)
    if key_levels:
        levels_from_key = [
            ("Equilibrium", key_levels.get('Equilibrium_Price')),
            ("High_250", key_levels.get('High_250')),
            ("Low_250", key_levels.get('Low_250')),
        ]
        for name, level in levels_from_key:
            if level is not None:
                try:
                    lv = float(level)
                    if lv > 0 and abs(current_price - lv) <= threshold:
                        near.append(f"{name}={lv:.2f}")
                except (TypeError, ValueError):
                    pass
    
    # Swing pivots (всегда проверяем, даже если key_levels пустой)
    if swing_pivot_high is not None:
        try:
            lv = float(swing_pivot_high)
            if lv > 0 and abs(current_price - lv) <= threshold:
                near.append(f"Swing High={lv:.2f}")
        except (TypeError, ValueError):
            pass
    
    if swing_pivot_low is not None:
        try:
            lv = float(swing_pivot_low)
            if lv > 0 and abs(current_price - lv) <= threshold:
                near.append(f"Swing Low={lv:.2f}")
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
    
    ВАЖНО: Если confidence < 55, сигнал автоматически преобразуется в WAIT!
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
    tp1 = trade_plan.get('final_tp1')  # optional first target (50% position); if set, close 50% at TP1 then rest at TP
    
    confidence = signal.get('confidence') or parsed.get('CONFIDENCE') or 0
    try:
        confidence = int(confidence) if confidence is not None else 0
    except (TypeError, ValueError):
        confidence = 0
    
    # Грейд, тип сетапа, модель и полнота модели (Task 10)
    setup_grade = signal.get('setup_grade', None)
    setup_type = signal.get('setup_type', None)
    model = signal.get('model', 'NONE')
    model_completeness = signal.get('model_completeness') or {}
    
    reason = parsed.get('executive_summary') or parsed.get('REASON', '')
    
    # v8.6: confluence и R:R из ответа LLM
    confluence = parsed.get('confluence') or {}
    math_log = parsed.get('math_debug_log') or {}
    calculated_rr = safe_float(math_log.get('calculated_rr'), None)

    # Качество входа (entry_quality / entry_warning) из trade_plan
    entry_quality = trade_plan.get('entry_quality', 'OK')
    entry_warning = trade_plan.get('entry_warning', 'NONE')
    
    # КРИТИЧЕСКАЯ ПРОВЕРКА: если confidence < 55, преобразуем в WAIT (Fix.txt: Confidence < 55 = WAIT)
    low_confidence_override = False
    original_action = action
    
    if action in ('BUY', 'SELL') and confidence < 55:
        logger.warning(f"⚠️ LOW CONFIDENCE OVERRIDE: {action} → WAIT (confidence={confidence}% < 55)")
        low_confidence_override = True
        action = 'WAIT'
        reason = f"[LOW CONFIDENCE: {confidence}%] Оригинальный сигнал: {original_action}. {reason}"
    
    return {
        'action': action, 
        'entry': entry, 
        'sl': sl, 
        'tp': tp, 
        'tp1': tp1, 
        'confidence': confidence, 
        'reason': str(reason)[:4000],
        'low_confidence_override': low_confidence_override,
        'original_action': original_action if low_confidence_override else None,
        'setup_grade': setup_grade,
        'setup_type': setup_type,
        'model': model,
        'model_completeness': model_completeness,
        'confluence': confluence,
        'calculated_rr': calculated_rr,
        'entry_quality': entry_quality,
        'entry_warning': entry_warning,
    }


def validate_stop_loss(entry, sl, atr, tp=None):
    """
    Мягкий страж SL: только абсолютный минимум $5 (защита от бага LLM).
    Узкий/широкий SL по ATR — только WARNING в лог, не отклонение.
    Главный фильтр плохих сделок — R:R ≥ 1.2 (и 1.5 при широком SL).

    Args:
        entry, sl, atr: уровни и ATR M15
        tp: опционально — take profit для проверки R:R

    Returns:
        Tuple: (is_valid: bool, reason: str)
    """
    try:
        entry_f = safe_float(entry, 0)
        sl_f = safe_float(sl, 0)
        atr_f = safe_float(atr, 10.0)
        if entry_f <= 0 or sl_f <= 0:
            return True, "SL valid"
        sl_distance = abs(entry_f - sl_f)
        if atr_f <= 0:
            return True, "SL valid"
        sl_in_atr = round(sl_distance / atr_f, 2)

        MIN_SL_FIXED = 5.0
        MIN_SL_ATR_MULTIPLIER = 1.0   # мягкий порог (только WARNING если < 1.0×ATR)
        MAX_SL_ATR_MULTIPLIER = 6.0   # мягкий порог (только WARNING если > 6.0×ATR)
        SL_RR_THRESHOLD_ATR = 3.5     # выше этого порога требуем R:R ≥ 1.5

        # Абсолютный минимум $5 (защита от бага LLM)
        if sl_distance < MIN_SL_FIXED:
            return False, f"SL too small: ${sl_distance:.2f} < $5.00 (technical error)"

        # Мягкие пределы ATR (только WARNING, не отклонение)
        if sl_in_atr < 1.0:
            logger.warning(
                f"⚠️ SL узкий: ${sl_distance:.2f} ({sl_in_atr:.2f}×ATR < 1.0×ATR), но LLM знает что делает"
            )
        if sl_in_atr > MAX_SL_ATR_MULTIPLIER:
            logger.warning(
                f"⚠️ SL широкий: ${sl_distance:.2f} ({sl_in_atr:.2f}×ATR > {MAX_SL_ATR_MULTIPLIER}×ATR), но LLM знает что делает"
            )

        # R:R — главный фильтр (отсечёт плохие сделки)
        tp_f = safe_float(tp, 0) if tp is not None else None
        if tp_f and tp_f > 0 and sl_distance > 0:
            reward = abs(tp_f - entry_f)
            rr = reward / sl_distance
            min_rr_required = 1.5 if sl_in_atr > SL_RR_THRESHOLD_ATR else 1.2
            if rr < min_rr_required:
                return False, f"R:R={rr:.2f} < {min_rr_required} (требуется для SL={sl_in_atr:.1f}×ATR)"

        return True, "SL valid (LLM judgment)"
    except Exception as e:
        logger.debug(f"validate_stop_loss error: {e}")
        return True, "SL valid"


def fact_check_llm(verdict, analysis):
    """
    Уровень 1 защиты "Доверяй, но проверяй": проверяет утверждения LLM против данных smc_detector.
    Если LLM упоминает CHoCH/BOS/sweep/fresh OB, но в данных этого нет — снижаем confidence.
    Если confidence < 55 → WAIT (low_confidence_override).
    """
    confidence = verdict.get('confidence', 0)
    reason = (verdict.get('reason') or '').lower()
    action = verdict.get('action', 'WAIT')

    # Проверка 1: CHoCH
    if 'choch' in reason and action in ('BUY', 'SELL'):
        choch_confirmed = analysis.get('choch_confirmed') or []
        int_choch_confirmed = analysis.get('internal_choch_confirmed') or []
        has_real_choch = len(choch_confirmed) > 0 or len(int_choch_confirmed) > 0
        if not has_real_choch:
            confidence -= 15
            logger.warning("⚠️ LLM утверждает CHoCH, но в данных нет confirmed CHoCH")

    # Проверка 2: Liquidity sweep
    if 'sweep' in reason or 'liquidity' in reason:
        liquidity = analysis.get('liquidity') or []
        has_swept = any(liq.get('swept') for liq in liquidity)
        if not has_swept:
            confidence -= 10
            logger.warning("⚠️ LLM утверждает sweep ликвидности, но в данных нет swept=true")

    # Проверка 3: Fresh OB
    if 'fresh ob' in reason or 'unmitigated' in reason:
        order_blocks = analysis.get('order_blocks') or []
        has_fresh_ob = any(not ob.get('mitigated') for ob in order_blocks)
        if not has_fresh_ob:
            confidence -= 10
            logger.warning("⚠️ LLM утверждает свежий OB, но все OB mitigated")

    # Проверка 4: BOS
    if 'bos' in reason and action in ('BUY', 'SELL'):
        bos_confirmed = analysis.get('bos_confirmed') or []
        int_bos_confirmed = analysis.get('internal_bos_confirmed') or []
        has_real_bos = len(bos_confirmed) > 0 or len(int_bos_confirmed) > 0
        if not has_real_bos:
            confidence -= 15
            logger.warning("⚠️ LLM утверждает BOS, но в данных нет confirmed BOS")

    confidence = max(0, min(100, confidence))

    if confidence < 55 and verdict.get('action') in ('BUY', 'SELL'):
        verdict['low_confidence_override'] = True
        verdict['original_action'] = verdict.get('action')
        verdict['action'] = 'WAIT'
        verdict['reason'] = f"[Fact-check failed] {verdict.get('reason', '')}"

    verdict['confidence'] = confidence
    return verdict


def check_real_facts(analysis, action):
    """
    Уровень 2 защиты: хотя бы один реальный факт для входа (swept liquidity, confirmed BOS/CHoCH, цена у структуры).
    Если нет ни одного — возвращаем False (вызывающий выставит WAIT).
    """
    if action not in ('BUY', 'SELL'):
        return True, "WAIT не требует фактов"

    # Закрепление закрытием за границей локального диапазона (Range Breakout)
    # Используем active_range из БД (приоритет) или fallback на local_range из детектора
    active_range = analysis.get('active_range') or {}
    local_range = active_range if active_range else (analysis.get('local_range') or {})
    local_high = safe_float(local_range.get('range_high') or local_range.get('local_range_high'), None)
    local_low = safe_float(local_range.get('range_low') or local_range.get('local_range_low'), None)
    close = safe_float(analysis.get('current_price'), 0)
    if action == 'BUY' and local_high is not None and close > local_high:
        return True, "range_breakout_confirmed"
    if action == 'SELL' and local_low is not None and close < local_low:
        return True, "range_breakout_confirmed"

    real_facts = []

    liquidity = analysis.get('liquidity') or []
    if any(liq.get('swept') for liq in liquidity):
        real_facts.append('swept_liquidity')

    if len(analysis.get('bos_confirmed') or []) > 0:
        real_facts.append('bos_confirmed')
    if len(analysis.get('choch_confirmed') or []) > 0:
        real_facts.append('choch_confirmed')
    if len(analysis.get('internal_bos_confirmed') or []) > 0:
        real_facts.append('int_bos_confirmed')
    if len(analysis.get('internal_choch_confirmed') or []) > 0:
        real_facts.append('int_choch_confirmed')

    current_price = safe_float(analysis.get('current_price'), 0)
    order_blocks = analysis.get('order_blocks') or []
    fvg_list = analysis.get('fvg') or []
    price_near_structure = False
    threshold = current_price * 0.005 if current_price > 0 else 0

    for ob in order_blocks:
        top = safe_float(ob.get('top'), 0)
        bottom = safe_float(ob.get('bottom'), 0)
        if top > 0 and bottom > 0 and bottom - threshold <= current_price <= top + threshold:
            price_near_structure = True
            break
    for fvg in fvg_list:
        top = safe_float(fvg.get('top'), 0)
        bottom = safe_float(fvg.get('bottom'), 0)
        if top > 0 and bottom > 0 and bottom - threshold <= current_price <= top + threshold:
            price_near_structure = True
            break

    if price_near_structure:
        real_facts.append('price_near_structure')

    if len(real_facts) == 0:
        return False, "Нет реальных фактов для входа (no swept liquidity, no confirmed BOS/CHoCH, no price near structure)"

    return True, f"Реальные факты: {', '.join(real_facts)}"


def check_cooldown_after_sl(direction, current_time, current_structure_breaks):
    """
    Task 7: Check if enough time has passed since last SL in this direction
    and ensure there's a NEW structure break (not the same one).
    Returns: (can_trade: bool, reason: str)
    """
    direction = (direction or '').upper()
    if direction not in ('BUY', 'SELL'):
        return True, "OK"
    if direction in _last_stop_loss_time:
        elapsed_min = (current_time - _last_stop_loss_time[direction]).total_seconds() / 60
        if elapsed_min < COOLDOWN_MINUTES_AFTER_SL:
            return False, f"Cooldown active: {elapsed_min:.0f}min < {COOLDOWN_MINUTES_AFTER_SL}min since last {direction} SL"
    if direction in _last_stop_loss_structure and current_structure_breaks:
        last_id = _last_stop_loss_structure.get(direction)
        # Use MOST RECENT break (max bar_index); lists from SMC are chronological (oldest first)
        def _bar_idx(b):
            if isinstance(b, dict):
                return b.get('bar_index') or 0
            return getattr(b, 'bar_index', 0) or 0
        latest = max(current_structure_breaks, key=_bar_idx)
        if latest is not None:
            bi = latest.get('bar_index') if isinstance(latest, dict) else getattr(latest, 'bar_index', None)
            st = latest.get('structure') or latest.get('type') if isinstance(latest, dict) else getattr(latest, 'structure', None)
            if bi is not None and st is not None:
                current_id = f"{st}_{bi}"
                if current_id == last_id:
                    return False, f"Same structure break as previous {direction} SL trade"
    return True, "Cooldown passed and structure is new"


def on_stop_loss_hit(trade_data):
    """
    Task 7: Call when a stop loss is hit to record for cooldown tracking.
    trade_data: dict with 'direction' and optionally 'structure_breaks' (list).
    """
    direction = (trade_data or {}).get('direction', '').upper()
    if direction not in ('BUY', 'SELL'):
        return
    _last_stop_loss_time[direction] = datetime.now(timezone.utc)
    structure_breaks = (trade_data or {}).get('structure_breaks', [])
    if structure_breaks:
        latest = structure_breaks[0]
        if isinstance(latest, dict):
            st = latest.get('structure') or latest.get('type')
            bi = latest.get('bar_index', 0)
            if st is not None:
                _last_stop_loss_structure[direction] = f"{st}_{bi}"
        elif hasattr(latest, 'bar_index') and hasattr(latest, 'structure'):
            _last_stop_loss_structure[direction] = f"{latest.structure}_{latest.bar_index}"
    logger.info(f"📝 SL recorded for {direction} cooldown tracking")


def _get_session_key():
    """Session key for limits: date + session name (Tokyo/London/NY). 2 per direction per session."""
    now = datetime.now(timezone.utc)
    today = now.strftime("%Y-%m-%d")
    try:
        session_info = llm_service.get_session_info()
        session_name = (session_info or {}).get('session', 'Unknown') if isinstance(session_info, dict) else 'Unknown'
    except Exception:
        session_name = 'Unknown'
    return f"{today}_{session_name}"


def is_daily_trade_limit_reached():
    """
    Task 8: True если лимит 3/3 сделок за день уже исчерпан.
    День по Астрахани (UTC+4), сброс в 02:00 по нашему времени.
    """
    global _trades_today
    today = _get_trade_limit_date()
    if _trades_today.get('reset_date') != today:
        _trades_today = {'count': 0, 'reset_date': today}  # reset at 02:00 Astrakhan
    # Временно отключено для диагностики (сбор статистики 2-3 дня)
    # return _trades_today.get('count', 0) >= 3
    return False  # Лимит 3/3 отключен


def check_trade_limits(direction):
    """
    Task 8: Max 2 same direction per SESSION, max 3 total per day (Fix.txt).
    День для лимита 3/3 — по Астрахани (UTC+4), сброс в 02:00 по нашему времени.
    Returns: (can_trade: bool, reason: str)
    """
    global _trades_today, _trades_by_session
    direction = (direction or '').upper()
    if direction not in ('BUY', 'SELL'):
        return True, "OK"
    today = _get_trade_limit_date()
    if _trades_today['reset_date'] != today:
        logger.info(f"🔄 New trading day (Astrakhan 02:00): {today}. Resetting daily counter.")
        _trades_today = {'count': 0, 'reset_date': today}
    session_key = _get_session_key()
    if session_key not in _trades_by_session:
        _trades_by_session[session_key] = {'BUY': 0, 'SELL': 0}
    by_dir = _trades_by_session[session_key].get(direction, 0)
    # Временно отключено для диагностики (сбор статистики 2-3 дня)
    # if _trades_today['count'] >= 3:
    #     return False, f"Daily limit reached: {_trades_today['count']}/3 trades today"
    # if by_dir >= 2:
    #     return False, f"Direction limit reached: {by_dir}/2 {direction} this session"
    return True, f"Within limits (лимиты отключены): {_trades_today['count']}/3 today, {by_dir}/2 {direction} this session"


def increment_trade_counter(direction):
    """Task 8: Call when a trade is executed. Updates daily total and per-session direction."""
    global _trades_today, _trades_by_session
    direction = (direction or '').upper()
    if direction not in ('BUY', 'SELL'):
        return
    _trades_today['count'] = _trades_today.get('count', 0) + 1
    session_key = _get_session_key()
    _trades_by_session.setdefault(session_key, {'BUY': 0, 'SELL': 0})
    _trades_by_session[session_key][direction] = _trades_by_session[session_key].get(direction, 0) + 1
    by_dir = _trades_by_session[session_key].get(direction, 0)
    logger.info(f"📊 Trade counter: {_trades_today['count']}/3 today | {direction}: {by_dir}/2 this session")


def validate_llm_verdict_strict(verdict, current_price, invalidation_levels, min_rr=1.2, entry_tolerance_pct=0.5):
    """
    v8.6 MUST-HAVE: пост-валидация вердикта LLM.
    - R:R >= min_rr иначе WAIT.
    - SL за уровнем инвалидации (BUY: SL <= inv_buy; SELL: SL >= inv_sell).
    - Entry в пределах entry_tolerance_pct от current_price, иначе подменяем на current_price.
    - Если confluence любой false → WAIT.
    
    Double TP поддержка: если указан tp1, R:R считается по tp1 (первый тейк на 50% позиции).
    Возвращает (action, entry, sl, tp, reason_override).
    """
    action = verdict.get('action', 'WAIT')
    entry = verdict.get('entry')
    sl = verdict.get('sl')
    tp = verdict.get('tp')
    tp1 = verdict.get('tp1')
    reason_override = None
    inv_buy = invalidation_levels.get('invalidation_buy')
    inv_sell = invalidation_levels.get('invalidation_sell')

    if action not in ('BUY', 'SELL'):
        return action, entry, sl, tp, reason_override

    entry_f = safe_float(entry, 0)
    sl_f = safe_float(sl, 0)
    tp_f = safe_float(tp, 0)
    tp1_f = safe_float(tp1, 0) if tp1 is not None else None
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

    # Double TP валидация: проверяем порядок уровней (tp1 должен быть между entry и tp)
    if tp1_f and tp1_f > 0:
        if action == 'BUY':
            # Для BUY: entry < tp1 <= tp
            if not (entry_f < tp1_f <= tp_f):
                logger.warning(f"⚠️ tp1={tp1_f:.2f} некорректен для BUY (entry={entry_f:.2f}, tp={tp_f:.2f}) — игнорируем tp1")
                tp1_f = None
        else:  # SELL
            # Для SELL: entry > tp1 >= tp
            if not (entry_f > tp1_f >= tp_f):
                logger.warning(f"⚠️ tp1={tp1_f:.2f} некорректен для SELL (entry={entry_f:.2f}, tp={tp_f:.2f}) — игнорируем tp1")
                tp1_f = None

    # R:R: если указан tp1 (и прошёл валидацию), считаем по нему (первый тейк на 50% позиции)
    reward_tp = abs(tp_f - entry_f)
    reward_tp1 = abs(tp1_f - entry_f) if tp1_f and tp1_f > 0 else None
    risk = abs(entry_f - sl_f)
    
    # Для Double TP: минимальный R:R проверяем по tp1 (более консервативно)
    if reward_tp1 and reward_tp1 > 0:
        rr = reward_tp1 / risk if risk > 0 else 0
        rr_source = f"tp1 ({rr:.2f})"
        logger.debug(f"📊 R:R расчёт по tp1: risk={risk:.2f}, reward_tp1={reward_tp1:.2f}, rr={rr:.2f}")
    else:
        rr = reward_tp / risk if risk > 0 else 0
        rr_source = f"tp ({rr:.2f})"
        logger.debug(f"📊 R:R расчёт по tp: risk={risk:.2f}, reward_tp={reward_tp:.2f}, rr={rr:.2f}")
    
    if rr < min_rr:
        tp_used = "tp1" if reward_tp1 else "tp"
        return 'WAIT', entry_f, sl_f, tp_f, f'R:R={rr:.2f} по {tp_used} < {min_rr} (минимальный порог). Риск/прибыль недопустимы.'
    
    # Инвалидация: BUY — SL должен быть на или ниже invalidation_buy; SELL — SL на или выше invalidation_sell
    if action == 'BUY' and inv_buy is not None and sl_f > inv_buy + 0.5:
        return 'WAIT', entry_f, sl_f, tp_f, f'SL для BUY ({sl_f:.2f}) выше уровня инвалидации ({inv_buy:.2f}). Стоп должен быть за структурой.'
    if action == 'SELL' and inv_sell is not None and sl_f < inv_sell - 0.5:
        return 'WAIT', entry_f, sl_f, tp_f, f'SL для SELL ({sl_f:.2f}) ниже уровня инвалидации ({inv_sell:.2f}). Стоп должен быть за структурой.'
    
    # Confluence: если любой false — WAIT
    # ltf_trigger_confirmed исключён — покрывается fact_check_llm() через штраф к confidence
    # и check_real_facts() через требование реальных фактов
    confluence = verdict.get('confluence') or {}
    for key in ('htf_aligned', 'rr_acceptable', 'invalidation_respected'):
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
        entry_quality = escape_html(verdict.get('entry_quality', 'OK'))
        entry_warning = escape_html(verdict.get('entry_warning', 'NONE'))
        
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
        conf_emoji = "🟢" if int(confidence) >= 70 else "🟡" if int(confidence) >= 55 else "🔴"
        
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
        if entry_warning and entry_warning != 'NONE':
            msg += f"\n⚠️ Качество входа: {entry_quality} | {entry_warning}\n"
        return msg
    
    elif verdict and verdict['action'] == 'WAIT':
        # Форматируем WAIT сигнал с полными данными (полный executive_summary как для BUY/SELL)
        # Включаем как текст LLM, так и техническую причину, по которой система отклонила сделку
        full_reason = parsed_data.get('executive_summary', '') if parsed_data else ''
        system_reason = verdict.get('reason', '')
        if full_reason and system_reason:
            combined_reason = f"{full_reason}\n\nТехническая причина WAIT: {system_reason}"
        else:
            combined_reason = full_reason or system_reason or 'Ожидание лучшей возможности'
        reason = escape_html(combined_reason)
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
        'range_breakout_trigger': '📐',  # Подтверждённый пробой локального диапазона
        'smc_sweet_spot': '🎯',  # v8.0: Идеальный SMC сетап
        'no_confirmed_signal': '⏳',  # v6.0: Нет уверенного пробоя
        'impulse_no_confirmation': '⚠️',  # v7.5.2: Impulse/Reversal без internal confirmed
        'low_confidence_wait': '📉',  # v7.6: Низкая уверенность (< 55%)
        'active_trade': '🛑',  # Manager: есть активная сделка
        'trade_closed_sl': '🛑', 'trade_closed_tp': '✅', 'trade_closed_manager_news': '⚠️',
        'trade_closed_manager_risky': '🔴', 'trade_closed_manager_pullback': '✅', 'trade_closed_manager_50': '✅',
        'trade_closed_manager_1r': '✅',
        'move_sl_be': '🔒',
        'trade_cancelled_no_fill': '⏱',
        'trade_limit_reached': '🛑',
        'hunter_memory_skip': '🧠',
        'range_internal': '⚪',
        'range_breakout_wait_confirmation': '⏳',
        'range_breakout_no_structure': '⚠️',
        'range_breakout_overheated': '🌡️',
        'range_breakout_doji': '🕯️',
        'range_no_touch': '⚠️',
        'range_rejection_doji': '🕯️',
        'range_rejection_overheated': '🌡️',
        'range_rejection_confirmed': '↩️',
        'range_rejection_trigger': '↩️',
        'htf_filter_blocked': '🚫',
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
        'range_breakout_trigger': '📐 RANGE BREAKOUT: Подтверждённый пробой локального диапазона — вызов LLM',
        'smc_sweet_spot': '🎯 SMC SWEET SPOT: Идеальный сетап',  # v8.0
        'no_confirmed_signal': 'SKIP - Нет CONFIRMED пробоя (LLM не вызван)',  # v6.0
        'impulse_no_confirmation': 'SKIP - Impulse/Reversal без internal confirmed',  # v7.5.2
        'low_confidence_wait': '📉 LOW CONFIDENCE: Сигнал отклонён (< 55%)',  # v7.6
        'active_trade': 'Manager: активная сделка, охотник отключён',
        'trade_closed_sl': 'Manager: Сделка закрыта по SL',
        'trade_closed_tp': 'Manager: Сделка закрыта по TP',
        'trade_closed_manager_news': 'Manager: Сделка закрыта перед новостями',
        'trade_closed_manager_risky': 'Manager: Сделка закрыта (3/3 рекомендаций закрыть полностью)',
        'trade_closed_manager_pullback': 'Manager: Сделка закрыта (откат от пика к TP)',
        'trade_closed_manager_50': 'Manager: Сделка закрыта (3/3 рекомендаций частично закрыть)',
        'trade_closed_manager_1r': 'Manager: Сделка закрыта по рекомендации LLM при 1R (фиксация прибыли)',
        'move_sl_be': 'Manager: SL переведён в безубыток',
        'trade_cancelled_no_fill': 'Manager: Сделка отменена — Entry не достигнут за таймаут',
        'trade_limit_reached': 'Лимит сделок за день (3/3) — анализ не выполняется, вызов LLM пропущен',
        'hunter_memory_skip': 'Цена мало изменилась — вызов LLM пропущен (память охотника)',
        'range_internal': '⚪ Цена внутри диапазона (нет пробоя)',
        'range_breakout_wait_confirmation': '⏳ Range Breakout: ждём второй свечи',
        'range_breakout_no_structure': '⚠️ Range Breakout без BOS/CHoCH — скип',
        'range_breakout_overheated': 'Range Breakout: перегрев — цена слишком далеко от уровня',
        'range_breakout_doji': 'Range Breakout: доджи — нет подтверждения силы',
        'range_no_touch': '⚠️ В диапазоне не закрылись 2 свечи — пробой пропущен',
        'htf_filter_blocked': '🚫 Range сигнал заблокирован (против H4 тренда)',
        'range_rejection_doji': '🕯️ Range Rejection: доджи',
        'range_rejection_overheated': '🌡️ Range Rejection: перегрев',
        'range_rejection_confirmed': '↩️ Range Rejection подтверждён',
        'range_rejection_trigger': '↩️ RANGE REJECTION: Отбой от границы диапазона — вызов LLM',
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
        msg += f"├ Цена: <code>${status_data['price']:.2f}</code> (Open)\n"
        # ATR и SL×ATR только для торгового сигнала (BUY/SELL)
        if status_data.get('status') == 'signal_sent':
            atr_val = status_data.get('atr_m15') or 0
            sl_ratio = status_data.get('sl_atr_ratio')
            if atr_val and atr_val > 0:
                msg += f"├ ATR (14): <code>${atr_val:.2f}</code>\n"
            if sl_ratio is not None:
                msg += f"├ SL: <code>{sl_ratio:.2f}×ATR</code> (лимит 4.5×ATR)\n"
        
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
    
    # Локальный диапазон из БД — всегда в отчёте (живёт до 24h / смены диапазона)
    ar = status_data.get('active_range')
    if ar and ar.get('is_manual'):
        msg += "<b>📐 Локальный диапазон (ручной ввод трейдера):</b>\n"
    else:
        msg += "<b>📐 Локальный диапазон (БД):</b>\n"
    if ar:
        rh = ar.get('range_high')
        rl = ar.get('range_low')
        if rh is not None and rl is not None:
            try:
                rh_f, rl_f = float(rh), float(rl)
                msg += f"├ High: <code>${rh_f:.3f}</code>\n"
                msg += f"├ Low: <code>${rl_f:.3f}</code>\n"
                price = status_data.get('price') or 0
                if price > 0:
                    if rl_f <= price <= rh_f:
                        msg += f"└ Цена: внутри диапазона\n\n"
                    elif price > rh_f:
                        msg += f"└ Цена: вне диапазона (выше)\n\n"
                    else:
                        msg += f"└ Цена: вне диапазона (ниже)\n\n"
                else:
                    msg += "\n"
            except (TypeError, ValueError):
                msg += "└ —\n\n"
        else:
            msg += "└ —\n\n"
    else:
        reason = status_data.get('local_range_no_reason')
        reason_text = None
        if reason == 'too_few_candles':
            n = status_data.get('local_range_consolidation_candles')
            reason_text = f"Детектор: консолидация только {n} свечей (< 5) — цена уже в движении" if n is not None else "Детектор: мало свечей консолидации"
        elif reason == 'high_volatility':
            reason_text = "Детектор: высокая волатильность в окне"
        elif reason == 'no_range':
            reason_text = "Детектор: диапазон не найден"
        elif reason == 'range_too_wide':
            sz = status_data.get('local_range_size_rejected')
            lim = status_data.get('local_range_atr_limit')
            reason_text = f"Диапазон шире 2×ATR (size {sz} > {lim})" if sz is not None and lim is not None else "Диапазон шире 2×ATR — не сохранён"
        elif reason == 'price_too_far':
            reason_text = "Цена ушла дальше 1 ширины диапазона — деактивирован"
        elif reason == 'expired_24h':
            reason_text = "Диапазон истёк (24h без касания)"
        elif reason == 'new_range_formed':
            reason_text = "Новый диапазон сформирован (перекрытие < 30%)"
        msg += "└ Нет активного диапазона\n"
        if reason_text:
            msg += f"<i>{escape_html(reason_text)}</i>\n"
        msg += "\n"
    
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
    take_profit_1 = verdict.get('tp1')  # optional: first target for 50% (double TP)
    if take_profit_1 is not None:
        take_profit_1 = safe_float(take_profit_1, 0.0)
    else:
        take_profit_1 = 0.0
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
        'take_profit_1': take_profit_1,
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
    global _breakout_confirmed_previous_cycle
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
    
    # Опорные свечи и текущая цена для анализа
    current_candle = candles[-1] if candles else {}
    signal_candle = candles[-2] if candles and len(candles) >= 2 else {}
    breakout_candle = candles[-3] if candles and len(candles) >= 3 else {}
    
    # v8.x: используем цену ОТКРЫТИЯ новой свечи как опорную (стабильна в течение бара)
    current_price = safe_float(
        current_candle.get('open', current_candle.get('Open', 0)),
        0.0
    )
    # Пробрасываем актуальную цену в analysis, чтобы все последующие расчёты
    # использовали одно и то же значение current_price
    if isinstance(analysis, dict):
        analysis['current_price'] = current_price
    
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
            logger.warning(f"⚠️ zone_source={zone_source}: Используем старую логику calculate_forced_zones (fallback) — детектор не вернул зоны и key_levels пустой. Range: [{global_low:.2f} - {global_high:.2f}], pos={position_in_range_pct:.1f}%")
    
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
    
    # ATR M15 для отчёта и валидации (доступен в format_debug_report для любого статуса)
    atr_m15_initial = compute_atr(candles, 14) if candles else 0.0
    
    # Базовый статус
    status_data = {
        'price': current_price,
        'atr_m15': atr_m15_initial,
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
    
    # Локальный диапазон для debug отчёта
    _lr = analysis.get('local_range') or {}
    if _lr.get('local_range_high') and _lr.get('local_range_low'):
        status_data['local_range_high'] = _lr['local_range_high']
        status_data['local_range_low'] = _lr['local_range_low']
    
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

    # ---------- Range Manager (локальный диапазон из БД + двухсвечное закрепление) ----------
    RANGE_SYMBOL = 'XAUUSD'
    RANGE_TIMEFRAME = 'M15'
    MAX_LOCAL_RANGE_ATR = 2.0  # согласовано с MAX_RANGE_ATR в calculate_local_range
    status_data['active_range'] = None
    status_data['is_range_breakout_confirmed'] = False
    status_data['breakout_direction'] = None
    status_data['is_range_rejection_confirmed'] = False
    status_data['rejection_direction'] = None
    status_data['rejection_level'] = None

    active_range = None
    try:
        active_range = db_service.get_active_range(symbol=RANGE_SYMBOL, timeframe=RANGE_TIMEFRAME)
    except Exception as e:
        logger.warning(f"⚠️ get_active_range error: {e}")

    manual_range_active = False
    try:
        manual_range_active = db_service.has_active_manual_range(symbol=RANGE_SYMBOL)
    except Exception as e:
        logger.warning(f"⚠️ has_active_manual_range error: {e}")
    if manual_range_active:
        logger.info("📐 Ручной диапазон активен — автодетектор пропускается")

    if active_range:
        range_width = safe_float(active_range.get('range_size'), 0) or 0
        rh = safe_float(active_range.get('range_high'), 0)
        rl = safe_float(active_range.get('range_low'), 0)
        if range_width <= 0 and rh and rl:
            range_width = rh - rl
        rid = active_range.get('id')
        is_manual_range = bool(active_range.get('is_manual'))

        # Ручной диапазон не деактивируем по (а)(б)(в) — только через кнопку «Авто режим» или при определённых статусах
        if not is_manual_range:
            # (а) Цена вышла из диапазона: деактивируем, если цена за границей более чем на 1 ширину
            if range_width > 0:
                if current_price < rl - range_width or current_price > rh + range_width:
                    db_service.deactivate_range(rid, 'price_too_far')
                    active_range = None
                    status_data['local_range_no_reason'] = 'price_too_far'
                    logger.info(
                        "📐 Диапазон деактивирован: цена вне диапазона (ниже/выше более чем на 1 ширину)"
                    )

            # (б) Прошло более 24 часов с last_touch_at
            if active_range:
                last_touch_raw = active_range.get('last_touch_at') or active_range.get('created_at')
                if last_touch_raw:
                    try:
                        last_touch = datetime.fromisoformat(str(last_touch_raw).replace('Z', '+00:00'))
                        if last_touch.tzinfo is None:
                            last_touch = last_touch.replace(tzinfo=timezone.utc)
                        if (datetime.now(timezone.utc) - last_touch).total_seconds() > 24 * 3600:
                            db_service.deactivate_range(rid, 'expired_24h')
                            active_range = None
                            status_data['local_range_no_reason'] = 'expired_24h'
                            logger.info("📐 Диапазон деактивирован: истёк 24h (expired_24h)")
                    except Exception:
                        pass

            # (в) Новый диапазон из детектора не перекрывается со старым > 30%
            if active_range and range_width > 0:
                new_range = analysis.get('local_range') or {}
                new_high = new_range.get('local_range_high')
                new_low = new_range.get('local_range_low')
                if new_high is not None and new_low is not None:
                    overlap = max(0, min(new_high, rh) - max(new_low, rl))
                    overlap_pct = overlap / range_width
                    if overlap_pct < 0.30:
                        db_service.deactivate_range(rid, 'new_range_formed')
                        active_range = None
                        status_data['local_range_no_reason'] = 'new_range_formed'
                        logger.info("📐 Диапазон деактивирован: новый диапазон сформирован (overlap < 30%)")

    skip_range_creation_this_cycle = False  # Проблема 4: после подтверждённого пробоя один цикл не создаём новый диапазон
    if not active_range:
        # Проблема 4: только что подтвердили пробой — не воскрешать старый диапазон, не создавать новый; дать приоритет вызову LLM
        if _breakout_confirmed_previous_cycle:
            _breakout_confirmed_previous_cycle = False
            skip_range_creation_this_cycle = True
            status_data['is_range_breakout_confirmed'] = True
            try:
                db_service.deactivate_manual_ranges(symbol=RANGE_SYMBOL)
            except Exception:
                pass
            logger.info("✅ Пробой активного диапазона подтверждён — старые диапазоны игнорируются")
        else:
            # Воскрешение старых диапазонов: только если две последние закрытые свечи строго внутри [range_low, range_high] (без буфера ATR)
            try:
                inactive_ranges = db_service.get_recent_inactive_ranges(
                    symbol=RANGE_SYMBOL, timeframe=RANGE_TIMEFRAME, hours=72
                )
                if candles and len(candles) >= 3:
                    signal_candle = candles[-2]
                    breakout_candle = candles[-3]
                    signal_close = safe_float(signal_candle.get('close'), 0)
                    breakout_close = safe_float(breakout_candle.get('close'), 0)

                    best_for_log = None  # диапазон с макс. количеством свечей внутри для лога
                    for old_range in inactive_ranges:
                        r_high = safe_float(old_range.get('range_high'), 0)
                        r_low = safe_float(old_range.get('range_low'), 0)
                        r_size = safe_float(old_range.get('range_size'), 0) or (r_high - r_low if (r_high and r_low) else 0)
                        if r_size <= 0:
                            continue
                        rid = old_range.get('id')
                        # Строго внутри границ [range_low, range_high], без буфера ATR
                        inside_signal = r_low <= signal_close <= r_high
                        inside_breakout = r_low <= breakout_close <= r_high
                        both_inside = inside_signal and inside_breakout

                        if both_inside:
                            if db_service.reactivate_range(rid):
                                db_service.update_range_touch(rid, 2)
                                active_range = dict(old_range)
                                active_range['is_active'] = True
                                active_range['candles_inside'] = 2
                                logger.info(
                                    f"🔄 Диапазон воскрешён: две свечи закрылись внутри [{r_low:.3f} - {r_high:.3f}]"
                                )
                            break
                        count_inside = (1 if inside_signal else 0) + (1 if inside_breakout else 0)
                        if best_for_log is None or count_inside > best_for_log[2]:
                            best_for_log = (r_low, r_high, count_inside)
                    if not active_range and best_for_log is not None:
                        r_low, r_high, count_inside = best_for_log
                        logger.info(
                            f"⏳ Ожидание воскрешения диапазона [{r_low:.3f} - {r_high:.3f}]: "
                            f"нужно 2 свечи внутри, пока {count_inside} из 2"
                        )
            except Exception as e:
                logger.warning(f"⚠️ get_recent_inactive_ranges / reactivate error: {e}")

    if not active_range and not skip_range_creation_this_cycle:
        new_range = analysis.get('local_range') or {}
        no_reason = new_range.get('no_range_reason')
        status_data['local_range_no_reason'] = None  # причина отсутствия диапазона в БД (для отчёта)

        n_high = new_range.get('local_range_high') if new_range else None
        n_low  = new_range.get('local_range_low')  if new_range else None

        if n_high is None or n_low is None:
            # Детектор не нашёл диапазон (too_few_candles или пустой результат)
            cons_cnt = new_range.get('consolidation_candles') if new_range else None
            status_data['local_range_no_reason'] = no_reason or 'no_range'
            status_data['local_range_consolidation_candles'] = cons_cnt
            logger.info(
                f"⚠️ Локальный диапазон не найден ({no_reason or 'no_range'}"
                + (f", {cons_cnt} св. консолидации" if cons_cnt is not None else "")
                + "), Range фильтр пропущен"
            )
        else:
            range_size_new = new_range.get('range_size') or (n_high - n_low)
            atr_m15 = atr_m15_initial or 0.0
            # Детектор уже фильтрует по MAX_RANGE_ATR=2.5 внутри; здесь второй рубеж не нужен,
            # но оставляем на случай рассогласования данных
            if atr_m15 > 0 and range_size_new > MAX_LOCAL_RANGE_ATR * atr_m15:
                logger.info(
                    f"⚠️ Локальный диапазон слишком широкий (range_size={range_size_new:.2f} > "
                    f"{MAX_LOCAL_RANGE_ATR}×ATR={MAX_LOCAL_RANGE_ATR * atr_m15:.2f}), Range фильтр пропущен"
                )
                status_data['local_range_no_reason'] = 'range_too_wide'
                status_data['local_range_size_rejected'] = round(range_size_new, 2)
                status_data['local_range_atr_limit'] = round(MAX_LOCAL_RANGE_ATR * atr_m15, 2)
            else:
                saved = db_service.save_range(n_high, n_low, symbol=RANGE_SYMBOL, timeframe=RANGE_TIMEFRAME)
                if saved:
                    active_range = saved
                    logger.info(f"📐 Новый локальный диапазон создан: [{n_low:.3f} - {n_high:.3f}]")

    status_data['active_range'] = active_range
    # История локальных диапазонов для LLM (до 4 уникальных прямоугольников за последние 72 часа)
    recent_local_ranges_for_llm = []
    try:
        inactive_ranges_for_llm = db_service.get_recent_inactive_ranges(
            symbol=RANGE_SYMBOL, timeframe=RANGE_TIMEFRAME, hours=72
        ) or []
        seen_keys = set()
        for r in inactive_ranges_for_llm:
            rh_llm = safe_float(r.get('range_high'), 0.0)
            rl_llm = safe_float(r.get('range_low'), 0.0)
            if rh_llm <= 0 or rl_llm <= 0:
                continue
            key = (round(rh_llm, 3), round(rl_llm, 3))
            if key in seen_keys:
                continue
            seen_keys.add(key)
            recent_local_ranges_for_llm.append({
                'range_high': rh_llm,
                'range_low': rl_llm,
                'range_size': safe_float(r.get('range_size'), rh_llm - rl_llm),
                'death_reason': r.get('death_reason'),
                'updated_at': str(r.get('updated_at') or r.get('created_at') or ''),
            })
            if len(recent_local_ranges_for_llm) >= 4:
                break
    except Exception as e:
        logger.warning(f"⚠️ get_recent_inactive_ranges for LLM error: {e}")
    status_data['recent_local_ranges'] = recent_local_ranges_for_llm
    
    if active_range:
        range_high = safe_float(active_range.get('range_high'), 0)
        range_low = safe_float(active_range.get('range_low'), 0)
        status_data['local_range_high'] = range_high
        status_data['local_range_low'] = range_low

        # Спец-сценарий HTF: если ранее Range Breakout был заблокирован против H4 тренда
        # (_htf_rejection_watch установлен), и цена вернулась к границе диапазона,
        # рассматриваем это как Range Rejection по направлению H4 (охота за стопами).
        global _htf_rejection_watch
        if _htf_rejection_watch:
            watch_dir = _htf_rejection_watch.get('watch_direction')
            watch_level = safe_float(_htf_rejection_watch.get('level'), 0.0)
            trend_htf = _htf_rejection_watch.get('trend')
            # BUY от нижней границы при H4 UPTREND: цена вернулась выше range_low
            if watch_dir == 'BUY' and trend_htf == 'UPTREND' and current_price >= range_low > 0:
                status_data['is_range_rejection_confirmed'] = True
                status_data['rejection_direction'] = 'BUY'
                status_data['rejection_level'] = range_low
                status_data['htf_rejection_watch'] = True
                is_near = True
                try:
                    db_service.deactivate_manual_ranges(symbol=RANGE_SYMBOL)
                except Exception:
                    pass
                logger.info(
                    f"↩️ HTF Range Rejection BUY: цена вернулась выше поддержки range_low={range_low:.3f} при H4 UPTREND "
                    f"(watch_level={watch_level:.3f}, current_price={current_price:.3f})"
                )
                _htf_rejection_watch = None
            # SELL от верхней границы при H4 DOWNTREND: цена вернулась ниже range_high
            elif watch_dir == 'SELL' and trend_htf == 'DOWNTREND' and current_price <= range_high > 0:
                status_data['is_range_rejection_confirmed'] = True
                status_data['rejection_direction'] = 'SELL'
                status_data['rejection_level'] = range_high
                status_data['htf_rejection_watch'] = True
                is_near = True
                try:
                    db_service.deactivate_manual_ranges(symbol=RANGE_SYMBOL)
                except Exception:
                    pass
                logger.info(
                    f"↩️ HTF Range Rejection SELL: цена вернулась ниже сопротивления range_high={range_high:.3f} при H4 DOWNTREND "
                    f"(watch_level={watch_level:.3f}, current_price={current_price:.3f})"
                )
                _htf_rejection_watch = None

        # Рабочие свечи для анализа диапазона:
        # - breakout_candle: первая свеча потенциального пробоя (полностью закрыта)
        # - signal_candle: вторая свеча (подтверждение / отмена пробоя)
        # - current_candle: новая, только что открывшаяся свеча (используется только по open)
        if candles and len(candles) >= 3:
            breakout_c = candles[-3]
            signal_c = candles[-2]
        elif candles and len(candles) >= 2:
            # Недостаточно истории для двух свечей пробоя — считаем, что пробоя нет
            breakout_c = candles[-2]
            signal_c = candles[-2]
        else:
            breakout_c = {}
            signal_c = {}
    
        breakout_close = safe_float(breakout_c.get('close'), 0)
        signal_close = safe_float(signal_c.get('close'), 0)

        # Проблема 3: считаем только закрытия завершённых свечей внутри диапазона (именно close, не open)
        # signal_close = close последней завершённой свечи (candles[-2]); один цикл = одна свеча M15
        if candles and len(candles) >= 2 and range_low <= signal_close <= range_high:
            candles_inside = (active_range.get('candles_inside') or 0) + 1
            db_service.update_range_touch(active_range['id'], candles_inside)
            active_range['candles_inside'] = candles_inside
    
        breakout_up   = (breakout_close > range_high and signal_close > range_high)
        breakout_down = (breakout_close < range_low  and signal_close < range_low)

        # Проблема 3: пробой валиден только если минимум 2 свечи закрылись внутри диапазона (не «галлюцинация»)
        price_was_inside = (active_range.get('candles_inside') or 0) >= 2
        skip_range_breakout = False
        if not price_was_inside:
            status_data['status'] = 'range_no_touch'
            status_data['reason'] = (
                f'Диапазон [{range_low:.3f} - {range_high:.3f}]: меньше 2 свечей закрылись внутри — пробой невалиден'
            )
            logger.warning(
                f"⚠️ Range пробой пропущен: в диапазоне [{range_low:.3f} - {range_high:.3f}] не закрылись 2 свечи"
            )
            skip_range_breakout = True

        # ---------- RANGE REJECTION: отбой от границы снаружи (цена не жила внутри) ----------
        if skip_range_breakout and candles and len(candles) >= 4:
            atr_m15 = atr_m15_initial or 0.0
            c4 = candles[-4]
            signal_high = safe_float(signal_c.get('high', signal_c.get('High', 0)))
            signal_low = safe_float(signal_c.get('low', signal_c.get('Low', 0)))
            breakout_high = safe_float(breakout_c.get('high', breakout_c.get('High', 0)))
            breakout_low = safe_float(breakout_c.get('low', breakout_c.get('Low', 0)))
            c4_high = safe_float(c4.get('high', c4.get('High', 0)))
            c4_low = safe_float(c4.get('low', c4.get('Low', 0)))
            # Подход к границе снаружи: хотя бы одна из 3 свечей дотянулась до зоны
            approach_sell = (
                signal_high >= range_low - 0.5 * atr_m15
                or breakout_high >= range_low - 0.5 * atr_m15
                or c4_high >= range_low - 0.5 * atr_m15
            )
            approach_buy = (
                signal_low <= range_high + 0.5 * atr_m15
                or breakout_low <= range_high + 0.5 * atr_m15
                or c4_low <= range_high + 0.5 * atr_m15
            )
            two_closes_sell = signal_close < range_low and breakout_close < range_low
            two_closes_buy = signal_close > range_high and breakout_close > range_high
            rejection_sell = approach_sell and two_closes_sell
            rejection_buy = approach_buy and two_closes_buy
            if rejection_sell:
                if current_price <= range_low - 1.5 * atr_m15:
                    status_data['status'] = 'range_rejection_overheated'
                    status_data['reason'] = (
                        f"Range Rejection: цена слишком далеко от range_low "
                        f"({current_price:.2f} <= {range_low - 1.5 * atr_m15:.2f})"
                    )
                    try:
                        db_service.deactivate_manual_ranges(symbol=RANGE_SYMBOL)
                    except Exception:
                        pass
                    send_debug_notification(status_data)
                    return
                body_threshold = 0.25 * atr_m15 if atr_m15 > 0 else 0.0
                signal_open = safe_float(signal_c.get('open', signal_c.get('Open', 0)))
                candle_body = abs(signal_close - signal_open)
                if candle_body < body_threshold:
                    status_data['status'] = 'range_rejection_doji'
                    status_data['reason'] = f"Range Rejection: свеча подтверждения — доджи (тело {candle_body:.2f} < {body_threshold:.2f})"
                    send_debug_notification(status_data)
                    return
                status_data['is_range_rejection_confirmed'] = True
                status_data['rejection_direction'] = 'SELL'
                status_data['rejection_level'] = range_low
                is_near = True
                try:
                    db_service.deactivate_manual_ranges(symbol=RANGE_SYMBOL)
                except Exception:
                    pass
                logger.info(f"↩️ Range Rejection подтверждён: SELL от уровня {range_low}")
            elif rejection_buy:
                if current_price >= range_high + 1.5 * atr_m15:
                    status_data['status'] = 'range_rejection_overheated'
                    status_data['reason'] = (
                        f"Range Rejection: цена слишком далеко от range_high "
                        f"({current_price:.2f} >= {range_high + 1.5 * atr_m15:.2f})"
                    )
                    try:
                        db_service.deactivate_manual_ranges(symbol=RANGE_SYMBOL)
                    except Exception:
                        pass
                    send_debug_notification(status_data)
                    return
                body_threshold = 0.25 * atr_m15 if atr_m15 > 0 else 0.0
                signal_open = safe_float(signal_c.get('open', signal_c.get('Open', 0)))
                candle_body = abs(signal_close - signal_open)
                if candle_body < body_threshold:
                    status_data['status'] = 'range_rejection_doji'
                    status_data['reason'] = f"Range Rejection: свеча подтверждения — доджи (тело {candle_body:.2f} < {body_threshold:.2f})"
                    send_debug_notification(status_data)
                    return
                status_data['is_range_rejection_confirmed'] = True
                status_data['rejection_direction'] = 'BUY'
                status_data['rejection_level'] = range_high
                is_near = True
                try:
                    db_service.deactivate_manual_ranges(symbol=RANGE_SYMBOL)
                except Exception:
                    pass
                logger.info(f"↩️ Range Rejection подтверждён: BUY от уровня {range_high}")
    
        if not skip_range_breakout:
            # ── Ложный пробой (FAKEOUT): первая свеча вышла за границу, вторая вернулась внутрь ──
            fakeout_up = breakout_close > range_high and signal_close <= range_high
            fakeout_down = breakout_close < range_low and signal_close >= range_low
            if fakeout_up or fakeout_down:
                db_service.update_range_touch(active_range['id'])
                direction_txt = 'вверх' if fakeout_up else 'вниз'
                logger.info(
                    f"↩️ Ложный пробой {direction_txt}: цена вернулась в диапазон "
                    f"[{range_low:.3f} - {range_high:.3f}], ждём следующей попытки"
                )
                status_data['status'] = 'range_internal'
                status_data['reason'] = (
                    f'↩️ Ложный пробой {direction_txt} — цена вернулась в диапазон '
                    f'[{range_low:.3f} - {range_high:.3f}]. Ждём следующей попытки.'
                )
                send_debug_notification(status_data)
                return

            if breakout_up or breakout_down:
                boundary = range_high if breakout_up else range_low
                direction_txt = 'BUY (вверх)' if breakout_up else 'SELL (вниз)'

                # ФИЛЬТР 1 — перегрев: подтверждение слишком далеко от границы
                atr_m15 = atr_m15_initial or 0.0
                if atr_m15 > 0:
                    distance = (signal_close - range_high) if breakout_up else (range_low - signal_close)
                    if distance > 1.5 * atr_m15:
                        status_data['status'] = 'range_breakout_overheated'
                        status_data['reason'] = (
                            f"Range Breakout: подтверждение слишком далеко от уровня "
                            f"({distance:.2f} > 1.5×ATR={1.5 * atr_m15:.2f}) — перегрев, скип"
                        )
                        try:
                            db_service.deactivate_manual_ranges(symbol=RANGE_SYMBOL)
                        except Exception:
                            pass
                        logger.warning(f"⚠️ {status_data['reason']}")
                        send_debug_notification(status_data)
                        return

                # ФИЛЬТР 2 — доджи: тело < 25% ATR
                body_threshold = 0.25 * atr_m15 if atr_m15 > 0 else 0.0
                signal_open = safe_float(signal_c.get('open', signal_c.get('Open', 0)))
                candle_body = abs(signal_close - signal_open)
                signal_is_doji = candle_body < body_threshold

                # Трёхсвечное подтверждение: 1-я за границей, 2-я доджи за границей, 3-я за границей → LLM в любом случае
                three_candle_ok = False
                if signal_is_doji and candles and len(candles) >= 4:
                    c4 = candles[-4]
                    c4_close = safe_float(c4.get('close', c4.get('Close', 0)))
                    c4_out_up = c4_close > range_high
                    c4_out_down = c4_close < range_low
                    breakout_open = safe_float(breakout_c.get('open', breakout_c.get('Open', 0)))
                    breakout_body = abs(breakout_close - breakout_open)
                    breakout_is_doji = breakout_body < body_threshold
                    if breakout_up and c4_out_up and breakout_is_doji:
                        three_candle_ok = True
                    if breakout_down and c4_out_down and breakout_is_doji:
                        three_candle_ok = True
                    if three_candle_ok:
                        logger.info(
                            f"✅ ПРОБОЙ ПОДТВЕРЖДЁН (3 свечи): 1-я и 2-я (доджи) и 3-я за границей — вызываем LLM"
                        )

                if signal_is_doji and not three_candle_ok:
                    status_data['status'] = 'range_breakout_doji'
                    status_data['reason'] = (
                        f"Range Breakout: свеча подтверждения — доджи "
                        f"(тело {candle_body:.2f} < {body_threshold:.2f} пт) — ждём третью свечу за границей"
                    )
                    logger.warning(f"⚠️ {status_data['reason']}")
                    send_debug_notification(status_data)
                    return

                # ✅ Истинный пробой (2 свечи или 3 свечи с доджи) — деактивируем старый диапазон
                try:
                    db_service.deactivate_range(active_range['id'], 'replaced_by_breakout')
                    logger.info(
                        f"📐 Диапазон [{range_low:.3f} - {range_high:.3f}] пробит и деактивирован "
                        f"(replaced_by_breakout). Следующий цикл создаст новый."
                    )
                except Exception as _e:
                    logger.warning(f"⚠️ deactivate_range(replaced_by_breakout) error: {_e}")

                # Проблема 4: следующий цикл не воскрешать старый диапазон — приоритет вызову LLM
                _breakout_confirmed_previous_cycle = True

                status_data['is_range_breakout_confirmed'] = True
                status_data['breakout_direction'] = 'BUY' if breakout_up else 'SELL'
                try:
                    db_service.deactivate_manual_ranges(symbol=RANGE_SYMBOL)
                except Exception:
                    pass

                # Wick-профиль последних 2–3 свечей пробоя для LLM (длины теней и тела)
                try:
                    def _rb_wick_profile(cndl):
                        o = safe_float(cndl.get('open', cndl.get('Open', 0)))
                        h = safe_float(cndl.get('high', cndl.get('High', 0)))
                        l = safe_float(cndl.get('low', cndl.get('Low', 0)))
                        c = safe_float(cndl.get('close', cndl.get('Close', 0)))
                        body = abs(c - o)
                        upper = max(h - max(o, c), 0.0)
                        lower = max(min(o, c) - l, 0.0)
                        # Порог «длинной» тени: не меньше тела и базового порога по ATR/пунктам
                        if atr_m15 and atr_m15 > 0:
                            wick_threshold = max(body, 0.3 * atr_m15, 3.0)
                        else:
                            wick_threshold = max(body, 3.0)
                        return {
                            'open': o,
                            'high': h,
                            'low': l,
                            'close': c,
                            'body': body,
                            'upper_wick': upper,
                            'lower_wick': lower,
                            'long_upper': upper > wick_threshold,
                            'long_lower': lower > wick_threshold,
                        }

                    # Для 2-свечного сценария: breakout_c (1-я) и signal_c (2-я).
                    # Для 3-свечного: candles[-4] (1-я), breakout_c (2-я, доджи), signal_c (3-я).
                    first_c = breakout_c
                    second_c = signal_c
                    third_c = None
                    pattern = 'two_candle'
                    if three_candle_ok and candles and len(candles) >= 4:
                        first_c = candles[-4]
                        second_c = breakout_c
                        third_c = signal_c
                        pattern = 'three_candle_doji'

                    rb_wicks = {
                        'pattern': pattern,
                        'direction': 'BUY' if breakout_up else 'SELL',
                        'first': _rb_wick_profile(first_c) if first_c else None,
                        'second': _rb_wick_profile(second_c) if second_c else None,
                    }
                    if third_c:
                        rb_wicks['third'] = _rb_wick_profile(third_c)
                    status_data['range_breakout_wicks'] = rb_wicks
                except Exception as e:
                    logger.warning(f"⚠️ Ошибка расчёта wick-профиля Range Breakout: {e}")

                logger.info(
                    f"✅ ПРОБОЙ ПОДТВЕРЖДЁН {direction_txt}: две свечи за границей {boundary:.3f} | "
                    f"breakout={breakout_close:.3f}, signal={signal_close:.3f}"
                )
                # Пробой диапазона сам по себе является триггером для LLM
                is_near = True
            else:
                # Первый пробой: вторая свеча вышла за границу, первая ещё была внутри
                only_signal_above = signal_close > range_high and breakout_close <= range_high
                only_signal_below = signal_close < range_low and breakout_close >= range_low
                if only_signal_above or only_signal_below:
                    direction_txt = 'вверх' if only_signal_above else 'вниз'
                    boundary = range_high if only_signal_above else range_low
                    db_service.update_range_touch(active_range['id'])
                    logger.info(
                        f"⏳ Первый пробой {direction_txt}: breakout={breakout_close:.3f}, "
                        f"signal={signal_close:.3f}, граница={boundary:.3f} — ждём второй свечи"
                    )
                    status_data['status'] = 'range_breakout_wait_confirmation'
                    status_data['reason'] = (
                        f'⏳ Первый пробой {direction_txt}: signal={signal_close:.3f}, '
                        f'граница={boundary:.3f} — ждём закрепления второй свечой'
                    )
                    send_debug_notification(status_data)
                    return

                # Цена внутри диапазона (не первый пробой)
                db_service.update_range_touch(active_range['id'])
                logger.info(
                    f"⬜ Цена внутри диапазона [{range_low:.3f} - {range_high:.3f}], "
                    f"ждём пробоя"
                )
                status_data['status'] = 'range_internal'
                status_data['reason'] = (
                    f'Цена внутри локального диапазона [{range_low:.3f} - {range_high:.3f}]. '
                    f'Нет закрепления за границей.'
                )
                send_debug_notification(status_data)
                return

        # Range Breakout подтверждён — BOS/CHoCH не требуются, идём к вызову LLM
    else:
        status_data['local_range_high'] = status_data.get('local_range_high')
        status_data['local_range_low'] = status_data.get('local_range_low')

    # Локальный флаг: подтверждён ли Range Breakout (двумя свечами) или Range Rejection (отбой от границы)
    is_range_breakout_confirmed = status_data.get('is_range_breakout_confirmed', False)
    is_range_rejection_confirmed = status_data.get('is_range_rejection_confirmed', False)
    
    # Близость к структурам OB/FVG или к ключевым уровням (пропускаем при импульсе или confirmed break/rejection)
    # v8.5: триггер LLM также при цене близко к ключевым уровням (Equilibrium, High_250, Low_250, Swing High/Low)
    if (
        not is_near
        and not is_near_key_levels
        and not is_breakout_impulse
        and not has_swing_break_confirmed
        and not is_range_breakout_confirmed
        and not is_range_rejection_confirmed
    ):
        status_data['status'] = 'not_near_structure'
        status_data['reason'] = (
            f'Цена ${current_price:.2f} далеко от SMC структур (OB/FVG) и от ключевых уровней. '
            f'Нет confirmed break.'
        )
        send_debug_notification(status_data)
        return
    
    # Equilibrium (пропускаем при импульсе ИЛИ при CONFIRMED ИЛИ при пробое 48–52% + internal ИЛИ при близости к уровню)
    # v8.1 FIX: CONFIRMED сигналы снимают запрет Equilibrium
    # v8.2: Пробой полосы 48–52% (eq_top/eq_bottom) + internal confirmed — разрешаем сделку от уровня равновесия
    # v8.6: Близость к OB/FVG/ликвидности/ключевым уровням — разрешаем вызов LLM в Equilibrium; решение за моделью
    has_equilibrium_breakout = False
    eq = (zones or {}).get('equilibrium') or {}
    eq_top = safe_float(eq.get('top'), 0)
    eq_bottom = safe_float(eq.get('bottom'), 0)
    if eq_top > 0 and eq_bottom > 0:
        if current_price > eq_top or current_price < eq_bottom:
            has_equilibrium_breakout = True
    proximity_ok = is_near or is_near_key_levels
    allow_equilibrium = (
        is_breakout_impulse or has_breakout or has_swing_break_confirmed or has_internal_break_confirmed
        or (has_equilibrium_breakout and has_internal_break_confirmed)
        or proximity_ok
        or is_range_breakout_confirmed
        or is_range_rejection_confirmed
    )
    if current_zone == "EQUILIBRIUM" and (has_equilibrium_breakout and has_internal_break_confirmed):
        logger.info(
            f"⚪ Equilibrium: пробой полосы 48–52% (price={current_price:.2f}, eq_top={eq_top:.2f}, eq_bottom={eq_bottom:.2f}) + internal confirmed — разрешаем сделку."
        )
    if current_zone == "EQUILIBRIUM" and proximity_ok and not (has_equilibrium_breakout or has_swing_break_confirmed or has_internal_break_confirmed):
        logger.info(f"⚪ Equilibrium: цена у структуры/уровня (proximity) — разрешаем вызов LLM, решение за моделью.")
    if current_zone == "EQUILIBRIUM" and not allow_equilibrium:
        status_data['status'] = 'equilibrium_zone'
        status_data['reason'] = f'Цена в Equilibrium ({position_in_range_pct:.1f}%) без структуры/пробоя/близости к уровням'
        send_debug_notification(status_data)
        return
    
    # NEUTRAL требует Swing (пропускаем при импульсе, но допускаем Range Breakout / Range Rejection)
    if swing_trend == "NEUTRAL" and not is_breakout_impulse and not is_range_breakout_confirmed and not is_range_rejection_confirmed:
        # v6.0: Требуем confirmed swing break
        if not has_swing_break_confirmed:
            status_data['status'] = 'neutral_no_swing'
            status_data['reason'] = 'Нейтральный тренд без CONFIRMED Swing пробоя'
            send_debug_notification(status_data)
            return
    
    # Сильные паттерны
    has_strong_swing = any('SWING' in s for s in swing_signals)
    has_strong_internal = any('INT' in s or 'OB' in s for s in internal_signals)
    
    if not all_signals and not is_breakout_impulse and not is_range_breakout_confirmed and not is_range_rejection_confirmed:
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
        
        # КРИТЕРИЙ 5: Range Breakout подтверждён (две свечи за границей) — главный приоритет
        if is_range_breakout_confirmed:
            override_cooldown = True
            override_reasons.append("📐 Range Breakout подтверждён")
        
        # КРИТЕРИЙ 5b: Range Rejection подтверждён (отбой от границы снаружи)
        if is_range_rejection_confirmed:
            override_cooldown = True
            override_reasons.append("↩️ Range Rejection подтверждён")
        
        # КРИТЕРИЙ 6: Цена у OB/FVG или ключевых уровней (Proximity)
        if is_near or is_near_key_levels:
            override_cooldown = True
            override_reasons.append("🎯 Proximity (OB/FVG/ключевые уровни)")
        
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
                f'Confirmed: {confirmed_count} | Swing BOS={has_swing_bos_confirmed}, CHoCH={has_swing_choch_confirmed}\n'
                f'Override: Range Breakout подтверждён ИЛИ Proximity (OB/FVG/уровни) ИЛИ Swing confirmed ИЛИ '
                f'{OVERRIDE_MIN_CONFIRMED}+ confirmed ИЛИ сильный импульс'
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
        has_swing_bos_confirmed 
        or has_swing_choch_confirmed 
        or has_int_bos_confirmed 
        or has_int_choch_confirmed
    )
    
    # v8.5: близость к SMC или ключевым уровням — достаточное условие для вызова LLM (без обязательного confirmed)
    allow_llm_by_proximity = is_near or is_near_key_levels
    
    # v7.5.2 FIX: Impulse/Reversal режимы тоже требуют хотя бы internal confirmed
    impulse_needs_confirmation = (is_breakout_impulse or is_reversal_setup) and not has_internal_break_confirmed
    
    if (
        not has_any_confirmed
        and not (is_breakout_impulse or is_reversal_setup)
        and not allow_llm_by_proximity
        and not is_range_breakout_confirmed
        and not is_range_rejection_confirmed
    ):
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
            
            # Последний WAIT из-за ошибки API — не считаем вердиктом, не пропускаем LLM (следующий цикл вызовет LLM)
            last_reason = (last_signal.get('llm_reason') or '') + (last_signal.get('llm_full_response') or '')
            last_reason_lower = last_reason.lower()
            last_was_error_fallback = (
                'не смог сформировать' in last_reason or
                'Ошибка анализа' in last_reason or
                'ИИ не смог' in last_reason or
                'сфотографировать' in last_reason or
                'API error' in last_reason_lower or
                '503' in last_reason or
                'overloaded' in last_reason_lower or
                'high demand' in last_reason_lower or
                'unavailable' in last_reason_lower
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
    
    # Task 8: Лимит 3/3 за день — не вызываем LLM, экономим токены; пишем в TG
    # Временно отключено для диагностики (сбор статистики 2-3 дня)
    # if is_daily_trade_limit_reached():
    #     n = _trades_today.get('count', 0)
    #     reason = f"Лимит сделок {n}/3 за день. Анализ не выполняется (вызов LLM пропущен)."
    #     logger.info(f"🛑 {reason}")
    #     send_debug_notification({
    #         'status': 'trade_limit_reached',
    #         'reason': reason,
    #         'price': current_price,
    #         'trade_limit_today': f"{n}/3"
    #     })
    #     return

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
    elif is_range_breakout_confirmed:
        # Проблема 2: в отчёте показывать Range Breakout, а не PROXIMITY, когда триггер — пробой диапазона
        mode_text = "📐 RANGE BREAKOUT (подтверждённый пробой локального диапазона)"
        status_data['status'] = 'range_breakout_trigger'
    elif is_range_rejection_confirmed:
        mode_text = "↩️ RANGE REJECTION (отбой от границы диапазона снаружи)"
        status_data['status'] = 'range_rejection_trigger'
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
        # HTF фильтр для Range Breakout / Range Rejection:
        # - при конфликте с H4 трендом блокируем текущий Range-сигнал
        # - дополнительно ставим флаг _htf_rejection_watch, чтобы в следующем цикле
        #   рассмотреть отбой (Range Rejection) по направлению H4 тренда
        h4 = htf_context.get('H4') or {}
        h4_trend = (h4.get('trend') or 'NEUTRAL').upper()
        breakout_dir = status_data.get('breakout_direction') or status_data.get('rejection_direction')
        if breakout_dir:
            breakout_dir = breakout_dir.upper()
        if h4_trend in ('UPTREND', 'DOWNTREND') and breakout_dir in ('BUY', 'SELL'):
            if h4_trend == 'UPTREND' and breakout_dir == 'SELL':
                logger.warning("🚫 Range сигнал заблокирован: SELL против H4 UPTREND")
                status_data['is_range_breakout_confirmed'] = False
                status_data['is_range_rejection_confirmed'] = False
                status_data['status'] = 'htf_filter_blocked'
                status_data['reason'] = (
                    "SELL заблокирован: H4 тренд UPTREND. "
                    "Торгуем только по направлению старшего тренда."
                )
                try:
                    db_service.deactivate_manual_ranges(symbol=RANGE_SYMBOL)
                except Exception:
                    pass
                # В следующем цикле, если цена вернётся выше нижней границы диапазона,
                # рассматриваем Range Rejection BUY от поддержки при H4 UPTREND.
                level = safe_float(status_data.get('local_range_low') or 0.0)
                _htf_rejection_watch = {
                    'trend': h4_trend,
                    'watch_direction': 'BUY',
                    'level': level,
                }
            elif h4_trend == 'DOWNTREND' and breakout_dir == 'BUY':
                logger.warning("🚫 Range сигнал заблокирован: BUY против H4 DOWNTREND")
                status_data['is_range_breakout_confirmed'] = False
                status_data['is_range_rejection_confirmed'] = False
                status_data['status'] = 'htf_filter_blocked'
                status_data['reason'] = (
                    "BUY заблокирован: H4 тренд DOWNTREND. "
                    "Торгуем только по направлению старшего тренда."
                )
                try:
                    db_service.deactivate_manual_ranges(symbol=RANGE_SYMBOL)
                except Exception:
                    pass
                # В следующем цикле, если цена вернётся ниже верхней границы диапазона,
                # рассматриваем Range Rejection SELL от сопротивления при H4 DOWNTREND.
                level = safe_float(status_data.get('local_range_high') or 0.0)
                _htf_rejection_watch = {
                    'trend': h4_trend,
                    'watch_direction': 'SELL',
                    'level': level,
                }

    # ========================================================================
    # v8.4: Создаем оптимизированную версию analysis для LLM (убираем all_* массивы)
    # v8.6 MUST-HAVE: инвалидация, ATR, текущая цена для валидации SL/R:R/entry
    # ========================================================================
    logger.info("📊 Подготавливаем оптимизированные данные M15 для LLM...")
    
    m15_candles = data.get('candles', [])[-50:] if 'error' not in data and data.get('candles') else []
    atr_m15 = compute_atr(m15_candles, 14) if m15_candles else 0.0
    analysis_with_price = {**analysis, 'current_price': current_price, 'atr_m15': atr_m15}
    invalidation = get_invalidation_levels(analysis_with_price, buffer=0.5)
    if atr_m15 > 0:
        logger.info(f"✓ ATR(14) M15 = {atr_m15:.2f} | Invalidation BUY<={invalidation.get('invalidation_buy')} SELL>={invalidation.get('invalidation_sell')}")

    # ========================================================================
    # Range Breakout: подсказки SL/TP для LLM (на основе активного диапазона)
    # ========================================================================
    range_breakout_context = None
    if status_data.get('is_range_breakout_confirmed') and status_data.get('active_range'):
        try:
            active_range_rb = status_data.get('active_range') or {}
            direction_rb = status_data.get('breakout_direction')
            rh = safe_float(active_range_rb.get('range_high'), 0.0)
            rl = safe_float(active_range_rb.get('range_low'), 0.0)
            buf = 0.3 * atr_m15 if atr_m15 and atr_m15 > 0 else 0.0
            entry_hint = current_price  # ориентировочный вход — текущая цена при пробое

            suggested_sl = None
            if direction_rb == 'BUY' and rh:
                suggested_sl = rh - buf if buf > 0 else rh
            elif direction_rb == 'SELL' and rl:
                suggested_sl = rl + buf if buf > 0 else rl

            # Подбор целевого уровня TP по ключевым уровням и структуре
            suggested_tp = None
            suggested_rr = None

            # Риск в деньгах по подсказанному SL
            risk_amount_rb = None
            if suggested_sl is not None:
                risk_amount_rb = abs(entry_hint - suggested_sl)
            elif atr_m15 > 0:
                risk_amount_rb = atr_m15

            adv = analysis.get('advanced') or {}
            kl_adv = adv.get('key_levels', {}) if isinstance(adv, dict) else {}
            sp_adv = adv.get('structure_points', {}) if isinstance(adv, dict) else {}

            levels_candidates = []

            def _add_level(val):
                try:
                    v = float(val)
                    if v > 0:
                        levels_candidates.append(v)
                except (TypeError, ValueError):
                    pass

            if direction_rb == 'BUY':
                # Ключевые уровни сверху: свинг-хай, High_250, DH/PDH, EQ если выше цены
                _add_level(sp_adv.get('nearest_swing_high'))
                _add_level(kl_adv.get('High_250'))
                _add_level(kl_adv.get('DH'))
                _add_level(kl_adv.get('PDH'))
                _add_level(kl_adv.get('Equilibrium_Price'))
                upper_levels = [lv for lv in levels_candidates if lv > entry_hint]
                tp_candidate = min(upper_levels) if upper_levels else None
                if tp_candidate:
                    suggested_tp = tp_candidate
            elif direction_rb == 'SELL':
                # Ключевые уровни снизу: свинг-лоу, Low_250, DL/PDL, EQ если ниже цены
                _add_level(sp_adv.get('nearest_swing_low'))
                _add_level(kl_adv.get('Low_250'))
                _add_level(kl_adv.get('DL'))
                _add_level(kl_adv.get('PDL'))
                _add_level(kl_adv.get('Equilibrium_Price'))
                lower_levels = [lv for lv in levels_candidates if lv < entry_hint]
                tp_candidate = max(lower_levels) if lower_levels else None
                if tp_candidate:
                    suggested_tp = tp_candidate

            # Если есть риск и TP, оцениваем R:R; при слишком маленьком R подсказка TP сдвигается на 1.5R
            if risk_amount_rb and risk_amount_rb > 0 and direction_rb in ('BUY', 'SELL'):
                if suggested_tp is not None:
                    if direction_rb == 'BUY':
                        suggested_rr = (suggested_tp - entry_hint) / risk_amount_rb
                    else:
                        suggested_rr = (entry_hint - suggested_tp) / risk_amount_rb
                # Если TP отсутствует или R:R слишком мал — предлагаем минимум 1.5R от entry_hint
                if suggested_tp is None or (suggested_rr is not None and suggested_rr < 1.5):
                    if direction_rb == 'BUY':
                        suggested_tp = entry_hint + 1.5 * risk_amount_rb
                    else:
                        suggested_tp = entry_hint - 1.5 * risk_amount_rb
                    suggested_rr = 1.5

            range_breakout_context = {
                'is_confirmed': True,
                'direction': direction_rb,
                'range_high': rh,
                'range_low': rl,
                'atr_m15': atr_m15,
                'entry_hint': entry_hint,
                'suggested_sl': suggested_sl,
                'suggested_tp': suggested_tp,
                'suggested_rr': suggested_rr,
                # Wick-профиль свечей пробоя (first/second/third) из status_data, если он был рассчитан
                'candle_wicks': status_data.get('range_breakout_wicks'),
                'is_range_rejection': status_data.get('is_range_rejection_confirmed', False),
                'rejection_direction': status_data.get('rejection_direction'),
                'rejection_level': status_data.get('rejection_level'),
            }
            logger.info(
                f"📐 Range Breakout context: dir={direction_rb}, "
                f"SL≈{suggested_sl}, TP≈{suggested_tp}, RR≈{suggested_rr}"
            )
        except Exception as e:
            logger.warning(f"⚠️ Ошибка расчёта Range Breakout контекста для LLM: {e}")
    elif status_data.get('is_range_rejection_confirmed') and status_data.get('active_range'):
        # Range Rejection (отбой от границы снаружи) — контекст для LLM
        try:
            active_range_rj = status_data.get('active_range') or {}
            direction_rj = status_data.get('rejection_direction')
            rej_level = status_data.get('rejection_level')
            rh = safe_float(active_range_rj.get('range_high'), 0.0)
            rl = safe_float(active_range_rj.get('range_low'), 0.0)
            buf = 0.3 * atr_m15 if atr_m15 and atr_m15 > 0 else 0.0
            entry_hint = current_price
            suggested_sl = None
            if direction_rj == 'BUY' and rej_level is not None:
                suggested_sl = rej_level - buf if buf > 0 else rej_level
            elif direction_rj == 'SELL' and rej_level is not None:
                suggested_sl = rej_level + buf if buf > 0 else rej_level
            range_breakout_context = {
                'is_confirmed': False,
                'is_range_rejection': True,
                'htf_rejection_watch': status_data.get('htf_rejection_watch', False),
                'rejection_direction': direction_rj,
                'rejection_level': rej_level,
                'direction': direction_rj,
                'range_high': rh,
                'range_low': rl,
                'atr_m15': atr_m15,
                'entry_hint': entry_hint,
                'suggested_sl': suggested_sl,
                'suggested_tp': None,
                'suggested_rr': None,
            }
            logger.info(
                f"↩️ Range Rejection context: dir={direction_rj}, level={rej_level}, SL≈{suggested_sl}"
            )
        except Exception as e:
            logger.warning(f"⚠️ Ошибка расчёта Range Rejection контекста для LLM: {e}")
    
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
        # Range Manager: активный диапазон из БД и флаги пробоя
        'active_range': status_data.get('active_range'),
        'is_range_breakout_confirmed': status_data.get('is_range_breakout_confirmed', False),
        'breakout_direction': status_data.get('breakout_direction'),
        # Подсказки для Range Breakout (SL/TP/entry)
        'range_breakout_context': range_breakout_context,
        # История недавних локальных диапазонов для поиска реалистичных TP (уникальные прямоугольники)
        'recent_local_ranges': status_data.get('recent_local_ranges') or [],
    }
    if htf_context:
        analysis_light['htf_context'] = htf_context
    
    # Добавляем свечи
    if m15_candles:
        analysis_light['candles'] = m15_candles
        logger.info(f"✓ M15 данные подготовлены: {len(m15_candles)} свечей")
    
    # Генерация скриншота M15 для LLM (Task 4)
    chart_images_b64 = {}
    if m15_candles:
        try:
            df = pd.DataFrame(m15_candles)
            if 'open' in df.columns and 'Open' not in df.columns:
                df = df.rename(columns={'open': 'Open', 'high': 'High', 'low': 'Low', 'close': 'Close'})
            if 'time' in df.columns:
                df['time'] = pd.to_datetime(df['time'])
                df = df.set_index('time')
            df = df.reset_index(drop=True)
            smc_data_for_chart = {**analysis}
            if status_data.get('active_range'):
                ar = status_data['active_range']
                smc_data_for_chart['local_range'] = {
                    'local_range_high': ar.get('range_high'),
                    'local_range_low': ar.get('range_low'),
                    'range_size': ar.get('range_size'),
                    'lookback': 30,
                }
            b64 = chart_service.generate_chart_image(df, smc_data=smc_data_for_chart, title="XAUUSD M15")
            chart_images_b64['M15'] = b64
            logger.info("✅ Скриншот M15 сгенерирован")
        except Exception as e:
            logger.error(f"❌ Ошибка генерации скриншота M15: {e}")
            chart_images_b64['M15'] = None
    analysis_light['chart_images'] = chart_images_b64

    # Логируем размер payload
    payload_size = len(json.dumps(analysis_light, ensure_ascii=False))
    logger.info(f"📦 Размер payload для LLM: {payload_size:,} символов ({payload_size/1024:.1f} KB)")
    
    # Вызов Gemini с оптимизированным analysis
    ai_response = llm_service.get_signal_verdict(analysis_light)
    
    # Парсим ответ (поддержка ОБОИХ форматов через extract_llm_verdict)
    parsed_llm = parse_llm_response(ai_response)
    verdict = extract_llm_verdict(parsed_llm)

    # Уровень 1: Факт-чекинг утверждений LLM (снижение confidence при галлюцинациях)
    verdict = fact_check_llm(verdict, analysis_light)

    # Уровень 2: Минимум реальных фактов (swept / BOS/CHoCH / price near structure)
    has_facts, facts_reason = check_real_facts(analysis_light, verdict.get('action'))
    if not has_facts:
        verdict['action'] = 'WAIT'
        verdict['reason'] = f"[No real facts] {facts_reason}. {verdict.get('reason', '')}"

    # v8.6 MUST-HAVE: пост-валидация R:R, инвалидации SL, confluence, entry (Уровень 3)
    invalidation_levels = (analysis_light.get('invalidation_levels') or {})
    strict_action, strict_entry, strict_sl, strict_tp, strict_reason = validate_llm_verdict_strict(
        verdict, current_price, invalidation_levels, min_rr=1.2, entry_tolerance_pct=0.5
    )
    
    # 🔍 DEBUG: Логируем что было ДО валидации
    logger.info(f"🔍 LLM Verdict ДО валидации: action={verdict.get('action')}, confidence={verdict.get('confidence')}%, entry={verdict.get('entry')}, sl={verdict.get('sl')}, tp={verdict.get('tp')}")
    
    if strict_reason:
        logger.warning(f"🛑 v8.6 Strict validation: {verdict.get('action')} → WAIT. Причина: {strict_reason}")
        logger.warning(f"   Entry: {strict_entry:.2f}, SL: {strict_sl:.2f}, TP: {strict_tp:.2f}")
        verdict['action'] = 'WAIT'
        verdict['reason'] = f"[v8.6] {strict_reason}. " + (verdict.get('reason') or '')
        verdict['entry'] = strict_entry
        verdict['sl'] = strict_sl
        verdict['tp'] = strict_tp
    else:
        logger.info(f"✅ v8.6 Strict validation: {verdict.get('action')} прошёл валидацию")
        verdict['entry'] = strict_entry
        verdict['sl'] = strict_sl
        verdict['tp'] = strict_tp
    
    # Task 6: Minimum SL validation (reject too tight or too wide SL)
    if strict_action in ('BUY', 'SELL') and strict_entry and strict_sl:
        atr_m15 = analysis_light.get('atr_m15', 10.0) or 10.0
        is_valid_sl, sl_reason = validate_stop_loss(strict_entry, strict_sl, atr_m15, tp=strict_tp)
        if not is_valid_sl:
            logger.warning(f"⚠️ Trade REJECTED Task 6: {sl_reason}")
            logger.warning(f"   Entry: {strict_entry:.2f}, SL: {strict_sl:.2f}, ATR: {atr_m15:.2f}")
            verdict['action'] = 'WAIT'
            verdict['reason'] = (verdict.get('reason') or '') + f" | REJECTED: {sl_reason}"
        else:
            logger.info(f"✅ Task 6 SL validation: SL={strict_sl:.2f} корректен (ATR={atr_m15:.2f})")

    llm_action = verdict['action']  # BUY / SELL / WAIT
    is_confirmed = llm_action in ['BUY', 'SELL']
    
    # Task 10: Audit log model and model_completeness
    model_name = verdict.get('model', 'NONE')
    model_completeness = verdict.get('model_completeness') or {}
    logger.info(f"🎯 Model identified: {model_name}")
    if model_completeness and isinstance(model_completeness, dict):
        present = sum(1 for v in model_completeness.values() if v)
        total = len(model_completeness)
        logger.info(f"📋 Model completeness: {present}/{total} elements present")
    
    # Task 7 & 8: Cooldown after SL and trade limits (before confirming signal)
    if is_confirmed:
        logger.info(f"🔍 Task 7: Проверка кулдауна после SL для {llm_action}...")
        structure_breaks = (analysis.get('all_swing_choch') or []) + (analysis.get('all_swing_bos') or [])
        can_cooldown, cooldown_reason = check_cooldown_after_sl(llm_action, datetime.now(timezone.utc), structure_breaks)
        if not can_cooldown:
            logger.warning(f"⚠️ Trade REJECTED Task 7: {cooldown_reason}")
            verdict['action'] = 'WAIT'
            verdict['reason'] = (verdict.get('reason') or '') + f" | {cooldown_reason}"
            llm_action = 'WAIT'
            is_confirmed = False
        else:
            logger.info(f"✅ Task 7 Cooldown: пройден")
            logger.info(f"🔍 Task 8: Проверка лимитов для {llm_action}...")
            can_limits, limit_reason = check_trade_limits(llm_action)
            if not can_limits:
                logger.warning(f"⚠️ Trade REJECTED Task 8: {limit_reason}")
                verdict['action'] = 'WAIT'
                verdict['reason'] = (verdict.get('reason') or '') + f" | {limit_reason}"
                llm_action = 'WAIT'
                is_confirmed = False
            else:
                logger.info(f"✅ Task 8 Trade limits: {limit_reason}")

        # Fix #8: Sweep freshness gate — для моделей со свипом требуем свежий свип (≤10 бар M15)
        if is_confirmed and model_name == 'LIQUIDITY_SWEEP_REVERSAL':
            logger.info(f"🔍 Fix #8: Проверка свежести свипа для модели {model_name}...")
            liquidity_list = analysis.get('liquidity') or []
            if llm_action == 'BUY':
                has_recent_sweep = any(
                    liq.get('swept') and liq.get('is_recent') and liq.get('type') == 'SWEPT_LOW'
                    for liq in liquidity_list
                )
            else:
                has_recent_sweep = any(
                    liq.get('swept') and liq.get('is_recent') and liq.get('type') == 'SWEPT_HIGH'
                    for liq in liquidity_list
                )
            if not has_recent_sweep:
                sweep_reason = (
                    f"Fix #8: модель {model_name} требует свежий свип ликвидности (≤10 бар M15). "
                    f"Для {llm_action}: нет недавнего {'SWEPT_LOW' if llm_action == 'BUY' else 'SWEPT_HIGH'}."
                )
                logger.warning(f"⚠️ Trade REJECTED Fix #8: {sweep_reason}")
                verdict['action'] = 'WAIT'
                verdict['reason'] = (verdict.get('reason') or '') + f" | {sweep_reason}"
                llm_action = 'WAIT'
                is_confirmed = False
            else:
                logger.info(f"✅ Fix #8: Свежий свип найден")
    
    low_conf_override = verdict.get('low_confidence_override', False)
    original_action = verdict.get('original_action')
    
    # WAIT из-за ошибки API — сохраняем в БД; в GUARD 1 следующий цикл увидит last_was_error_fallback и вызовет LLM снова
    ai_lower = (ai_response or '').lower()
    is_error_fallback = bool(
        ai_response and (
            'не смог сформировать' in ai_response or
            'Ошибка анализа' in ai_response or
            'ИИ не смог' in ai_response or
            'сфотографировать' in ai_response or
            'API error' in ai_lower or
            '503' in ai_response or
            'overloaded' in ai_lower or
            'high demand' in ai_lower or
            'unavailable' in ai_lower
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
    
    # Task 7 (Fix #6): сохраняем триггерную структуру для кулдауна «новый CHoCH/BOS»
    if llm_action in ('BUY', 'SELL'):
        _sb = (analysis.get('all_swing_choch') or []) + (analysis.get('all_swing_bos') or [])
        if _sb:
            def _bar_idx(b):
                if isinstance(b, dict):
                    return b.get('bar_index') or 0
                return getattr(b, 'bar_index', 0) or 0
            _latest = max(_sb, key=_bar_idx)
            _smc = dict(smc_summary) if isinstance(smc_summary, dict) else {}
            if isinstance(_latest, dict):
                _smc['_trigger_structure'] = {'structure': _latest.get('structure') or _latest.get('type'), 'bar_index': _latest.get('bar_index', 0)}
            else:
                _smc['_trigger_structure'] = {'structure': getattr(_latest, 'structure', None) or getattr(_latest, 'type', None), 'bar_index': _bar_idx(_latest)}
            smc_summary = _smc

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
        
        increment_trade_counter(llm_action)
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
        atr_m15_val = analysis_light.get('atr_m15') or 0
        status_data['atr_m15'] = atr_m15_val
        if verdict.get('entry') is not None and verdict.get('sl') is not None and atr_m15_val > 0:
            status_data['sl_atr_ratio'] = round(abs(float(verdict['entry']) - float(verdict['sl'])) / atr_m15_val, 2)
        else:
            status_data['sl_atr_ratio'] = None
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
        
        # Не обновляем кулдаун WAIT при ошибке API — следующий цикл должен вызвать LLM снова (без "Кулдаун активен")
        if not is_error_fallback:
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
        # WAIT от LLM при любом из трёх режимов (Range Breakout / Range Rejection / HTF Rejection) → деактивировать ручной диапазон
        if status_data.get('is_range_breakout_confirmed') or status_data.get('is_range_rejection_confirmed'):
            try:
                db_service.deactivate_manual_ranges(symbol='XAUUSD')
            except Exception:
                pass
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
    take_profit_1 = safe_float(trade.get('take_profit_1'), 0.0)  # P2: first target (50% position); 0 = not set

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
                    f"id={trade_id} | Нет даты создания сиг��ала — отменяем. Охотник снова ищет сделку."
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
    # Один раз в Astra Signal Bot: цена входа достигнута, сделка активирована (проверяем БД — без дубля после рестарта)
    already_notified_in_db = False
    try:
        already_notified_in_db = db_service.get_signal_entry_notified(trade_id)
    except Exception as e:
        logger.warning(f"⚠️ Не удалось прочитать entry_notified для сигнала {trade_id}: {e}")
    if (trade_id not in _entry_filled_notification_sent) and not already_notified_in_db:
        _entry_filled_notification_sent.add(trade_id)
        user_ids_filled = db_service.get_all_active_users()
        if user_ids_filled:
            msg_filled = (
                f"✅ <b>Вход достигнут — сделка активирована</b>\n\n"
                f"id={trade_id} | {signal_type} | цена входа <b>{entry_price:.2f}</b> достигнута.\n"
                f"Текущая цена: {current_price:.2f}. Менеджер ведёт сделку (SL/TP)."
            )
            telegram_service.broadcast_deals_only(user_ids_filled, msg_filled)
        db_service.mark_entry_notified(trade_id)
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
        # Task 7 (Fix #6): Record SL for cooldown; pass trigger structure for "new CHoCH/BOS" check
        trigger = (trade.get('smc_summary') or {}).get('_trigger_structure')
        on_stop_loss_hit({'direction': signal_type, 'structure_breaks': [trigger] if trigger else []})
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
        _recommendation_history.pop(trade_id, None)
        _trade_tp1_reached.discard(trade_id)
        # Определяем тип закрытия для сообщения по факту PnL и уровню закрытия (Проблема 1: при убытке не писать «1R»)
        close_label = "Stop Loss"
        if result_pnl < 0:
            # Убыток — закрытие по изначальному SL или хуже; всегда «по SL»
            close_label = "по SL"
        else:
            # Нулевой или плюс — смотрим, на каком уровне стоял SL (BE или 1R)
            be_threshold = entry_price * 0.0005  # ~0.05% от цены
            if abs(stop_loss - entry_price) <= be_threshold:
                close_label = "BE (безубыток)"
            elif risk_amount > 0 and abs(abs(stop_loss - entry_price) - risk_amount) <= be_threshold:
                close_label = "1R"

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
                f"🛑 <b>Сделка закрыта: {close_label}</b>\n"
                f"id={trade_id} | {signal_type} | entry={entry_price:.2f} → close={close_price_sl:.2f} (уровень SL)\n"
                f"PnL={result_pnl:.2f}\n"
                f"Данные сохранены в БД для анализа и обучения моделей."
            )
            telegram_service.broadcast_deals_only(user_ids_sl, msg_sl)
        return

    # 3.1a. P2 Double TP: если задан TP1 (первый уровень для 50%) и ещё не достигнут — проверяем TP1
    if take_profit_1 > 0 and trade_id not in _trade_tp1_reached:
        hit_tp1 = False
        for c in recent_m5:
            h = safe_float(c.get('high'), 0.0)
            l = safe_float(c.get('low'), 0.0)
            if signal_type == 'BUY' and h >= take_profit_1:
                hit_tp1 = True
                break
            if signal_type == 'SELL' and l <= take_profit_1:
                hit_tp1 = True
                break
        if not hit_tp1 and signal_type == 'BUY' and current_price >= take_profit_1:
            hit_tp1 = True
        if not hit_tp1 and signal_type == 'SELL' and current_price <= take_profit_1:
            hit_tp1 = True
        if hit_tp1:
            _trade_tp1_reached.add(trade_id)
            # При достижении TP1 переносим SL на уровень 1R (не ухудшаем уже поднятый стоп)
            sl_1r = entry_price + risk_amount if signal_type == 'BUY' else entry_price - risk_amount
            if signal_type == 'BUY':
                new_sl = max(sl_1r, stop_loss)
            else:
                new_sl = min(sl_1r, stop_loss)
            db_service.update_signal_sl_and_status(trade_id, new_sl, status='tp1_reached')
            partial_pnl = (take_profit_1 - entry_price) if signal_type == 'BUY' else (entry_price - take_profit_1)
            logger.info(
                f"✅ Manager: достигнут TP1={take_profit_1:.2f}. Частичный профит зафиксирован (50%), SL на 1R={new_sl:.2f}. PnL(50%)≈{partial_pnl:.2f}"
            )
            user_ids_tp1 = db_service.get_all_active_users()
            if user_ids_tp1:
                msg_tp1 = (
                    f"📈 <b>TP1 достигнут (50% позиции)</b>\n"
                    f"id={trade_id} | {signal_type} | TP1={take_profit_1:.2f} | SL переведён на 1R ({new_sl:.2f}).\n"
                    f"Оставшаяся часть — до TP2={take_profit:.2f}."
                )
                telegram_service.broadcast_deals_only(user_ids_tp1, msg_tp1)

    # 3.1b. Take Profit (TP2 или единственный TP) достигнут: закрываем сделку
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
        _trade_tp1_reached.discard(trade_id)
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
        # Уведомление в Astra Signal Bot (менеджерский бот)
        users_be = db_service.get_all_active_users()
        if users_be:
            msg_be = (
                f"🔒 <b>ASTRA Manager:</b> SL переведён в BE по правилу 1R.\n\n"
                f"id={trade_id} | {signal_type}\n"
                f"SL: {stop_loss:.2f} → {new_sl:.2f}\n"
                f"TP: {take_profit:.2f}"
            )
            telegram_service.broadcast_deals_only(users_be, msg_be)
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
        users_be_progress = db_service.get_all_active_users()
        if users_be_progress:
            msg_be_progress = (
                f"🔒 <b>ASTRA Manager:</b> SL переведён в BE.\n\n"
                f"id={trade_id} | {signal_type}\n"
                f"Цена прошла {progress_ratio*100:.1f}% пути до TP.\n"
                f"SL: {stop_loss:.2f} → {new_sl:.2f}\n"
                f"TP: {take_profit:.2f}"
            )
            telegram_service.broadcast_deals_only(users_be_progress, msg_be_progress)
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
            users_lock = db_service.get_all_active_users()
            if users_lock:
                msg_lock = (
                    f"🔒 <b>ASTRA Manager:</b> SL перенесён на уровень 1R.\n\n"
                    f"id={trade_id} | {signal_type}\n"
                    f"Цена прошла 1R+{MANAGER_1R_LOCK_MARGIN*100:.0f}%.\n"
                    f"SL: {stop_loss:.2f} → {sl_1r_level:.2f}\n"
                    f"TP: {take_profit:.2f}"
                )
                telegram_service.broadcast_deals_only(users_lock, msg_lock)

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

    rec_context = get_recommendation_context(trade_id)
    try:
        ai_response = llm_service.manage_active_trade(
            trade_context=trade_context,
            technical_context=technical_context,
            triggers=triggers,
            recommendation_context=rec_context,
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

    add_recommendation(trade_id, mgr_action, mgr_reason)
    should_escalate, escalation_reason = check_escalation_triggers(trade_id)
    if should_escalate:
        logger.warning(f"⚠️ AUTO-ESCALATION: {escalation_reason}")
        mgr_action = 'CLOSE_ALL'
        mgr_reason = escalation_reason

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
            f"Причина: {escape_html(mgr_reason)}"
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
            _recommendation_history.pop(trade_id, None)
            _trade_tp1_reached.discard(trade_id)
            send_debug_notification({
                'status': 'trade_closed_manager_1r',
                'reason': f'LLM рекомендовала закрыть при 1R — фиксация прибыли. PnL={result_pnl:.2f}',
                'trade_id': trade_id, 'entry': entry_price, 'sl': stop_loss, 'tp': take_profit,
                'current_price': current_price, 'signal_type': signal_type
            })
            msg = (
                f"✅ ASTRA Manager: сделка id={trade_id} закрыта по рекомендации LLM при 1R (фиксация прибыли).\n"
                f"type={signal_type}, entry={entry_price:.2f}, close={current_price:.2f}, PnL={result_pnl:.2f}\n"
                f"Причина: {escape_html(mgr_reason)}\n"
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
                _recommendation_history.pop(trade_id, None)
                _trade_tp1_reached.discard(trade_id)
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
                    f"Причина: {escape_html(mgr_reason)}\n\n"
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
            _recommendation_history.pop(trade_id, None)
            _trade_tp1_reached.discard(trade_id)
            send_debug_notification({
                'status': 'trade_closed_manager_1r',
                'reason': f'LLM рекомендовала зафиксировать прибыль при 1R. PnL={result_pnl:.2f}',
                'trade_id': trade_id, 'entry': entry_price, 'sl': stop_loss, 'tp': take_profit,
                'current_price': current_price, 'signal_type': signal_type
            })
            msg = (
                f"✅ ASTRA Manager: сделка id={trade_id} закрыта по рекомендации LLM при 1R (фиксация прибыли).\n"
                f"type={signal_type}, entry={entry_price:.2f}, close={current_price:.2f}, PnL={result_pnl:.2f}\n"
                f"Причина: {escape_html(mgr_reason)}\n"
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
                    f"Причина: {escape_html(mgr_reason)}\n\n"
                    f"После {MANAGER_CLOSE_50_AUTO_CLOSE_AFTER} рекомендаций (3/3) сделка будет закрыта автоматически."
                )
                if user_ids:
                    telegram_service.broadcast_deals_only(user_ids, msg)

    else:
        # HOLD или неизвестное действие — просто логируем и, опционально, уведомляем
        logger.info(f"🤝 Manager: действие LLM — HOLD/NO_ACTION (action={mgr_action}).")