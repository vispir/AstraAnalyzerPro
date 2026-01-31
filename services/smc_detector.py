"""
SMC Detector v7.3 - Professional Trading Analyzer with Trend Timeline (CORRECT!)
================================================================================
КРИТИЧЕСКОЕ ИСПРАВЛЕНИЕ v7.3:
- TREND TIMELINE: Тренд теперь ФУНКЦИЯ ВРЕМЕНИ, а не одно значение!
  * v7.2 ОШИБКА: использовал финальный unified_trend для ВСЕЙ истории
  * v7.3 ПРАВИЛЬНО: строим timeline (Dict[bar_index, trend])
  * Для каждого пробоя используется ПРАВИЛЬНЫЙ тренд на момент ДО пробоя
  * Алгоритм: Собираем все пробои → сортируем → идём bar-by-bar → обновляем тренд
  
  Пример работы:
  bar_0: NEUTRAL
  bar_10: Internal пробой вверх → тренд = BULLISH
  bar_15: Internal пробой вниз → тренд = BEARISH
  bar_20: Swing пробой вверх → использует trend[19]=BEARISH → CHoCH ✅
  bar_25: Swing пробой вверх → использует trend[24]=BULLISH → BOS ✅

КРИТИЧЕСКИЕ УЛУЧШЕНИЯ v7.2 (СОХРАНЕНЫ):
- DEDUPE PIVOT: Каждый pivot используется только один раз
- ПРИОРИТЕТ SWING: При конфликте событий на одном баре, swing > internal
  
ИСПРАВЛЕНИЯ v7.1 (СОХРАНЕНЫ):
- Сохранение тренда ДО пробоя (trend_before_break) внутри уровня

УЛУЧШЕНИЯ v7.0 (СОХРАНЕНЫ):
- Узкие зоны Premium/Discount на основе актуальных Swing High/Low (не 250 свечей!)
- Order Blocks с полным lifecycle: active → mitigated → invalidated → breaker
- FVG с fill статусом: open → partially_filled → filled
- Liquidity Pools и Liquidity Sweeps детекция
- ATR-фильтр шума для BOS/CHoCH (убирает микро-пробои)

СОХРАНЕНО из v6.0:
- Параметры pivot detection как в LuxAlgo (swing: 8/4, internal: 3/2)
- Флаг 'confirmed' для фильтрации сигналов TG бота
- Двухуровневая система: визуализация (все) vs сигналы (confirmed only)

Для TG бота:
- confirmed=True: пробой ТЕЛОМ свечи (close), не тенью
- bars_ago <= CONFIRMED_SIGNAL_BARS для торговых решений
"""

import pandas as pd
import numpy as np
import logging
from typing import Dict, List, Tuple, Optional, Any
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# ============================================================================
# КОНСТАНТЫ v6.0 - LuxAlgo Style
# ============================================================================

BULLISH = 1
BEARISH = -1
NEUTRAL = 0

# Параметры структуры v6.0 (как LuxAlgo)
# Internal: очень чувствительные для микро-структуры
DEFAULT_INTERNAL_LEFT = 3    # Быстрое определение internal swing
DEFAULT_INTERNAL_RIGHT = 2   # Минимальное подтверждение справа

# Swing: чувствительные для основной структуры (как LuxAlgo length=5-10)
DEFAULT_SWING_LEFT = 8       # Основные swing points
DEFAULT_SWING_RIGHT = 4      # Быстрое подтверждение (было 20!)

# v6.0 Параметры сигналов
FRESH_SIGNAL_BARS = 25              # Свежий сигнал для визуализации
CONFIRMED_SIGNAL_BARS = 5           # Только для TG бота - очень свежие
LOOKBACK_BARS = 250                 # Глубина анализа

# Параметры детекции импульса v5.2
IMPULSE_CANDLE_THRESHOLD = 1.5      # 1.5x средней свечи = импульс
IMPULSE_LOOKBACK = 15               # Окно для расчёта импульса
BREAKOUT_LOOKBACK = 20              # Пробой = ниже минимума N свечей
VOID_RUN_THRESHOLD = 0.005          # 0.5% от экстремума = void run
IMPULSE_THRESHOLD = 2               # Минимум BOS для IMPULSE_TREND

# Пороги зон
PREMIUM_THRESHOLD = 66.6            # > 66.6% = Premium
DISCOUNT_THRESHOLD = 33.3           # < 33.3% = Discount

# v6.1 Фильтр шума - минимальный порог пробоя
MIN_BREAK_ATR_RATIO = 0.15          # Пробой должен быть минимум 0.15 ATR (убирает микро-шум)
MIN_BREAK_PERCENT = 0.03            # Или минимум 0.03% от цены (для страховки)


# ============================================================================
# СТРУКТУРЫ ДАННЫХ
# ============================================================================

@dataclass
class PivotPoint:
    price: float = 0.0
    bar_index: int = 0
    bar_time: str = ""
    is_high: bool = True


@dataclass
class StructureBreak:
    break_type: str = ""
    price: float = 0.0
    bar_index: int = 0
    bar_time: str = ""
    pivot_bar_index: int = 0
    is_choch: bool = False
    bars_ago: int = 0
    break_by_wick: bool = False
    confirmed: bool = False  # v6.0: пробой телом (для TG бота)


# ============================================================================
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# ============================================================================

def sanitize_for_json(obj: Any) -> Any:
    """Конвертация numpy типов в Python типы для JSON"""
    if isinstance(obj, dict):
        return {k: sanitize_for_json(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [sanitize_for_json(item) for item in obj]
    elif isinstance(obj, (np.bool_, bool)):
        return bool(obj)
    elif isinstance(obj, (np.integer, int)):
        return int(obj)
    elif isinstance(obj, (np.floating, float)):
        val = float(obj)
        if np.isnan(val) or np.isinf(val):
            return 0.0
        return val
    elif isinstance(obj, np.ndarray):
        return [sanitize_for_json(item) for item in obj.tolist()]
    elif obj is None or (isinstance(obj, float) and (np.isnan(obj) or np.isinf(obj))):
        return None
    else:
        return obj


# ============================================================================
# ГЛАВНЫЙ КЛАСС
# ============================================================================

class SMCDetector:
    """SMC Detector v7.3 - Trend Timeline (CORRECT)"""
    
    def __init__(self):
        self.analysis_count = 0
        self.internal_left = DEFAULT_INTERNAL_LEFT
        self.internal_right = DEFAULT_INTERNAL_RIGHT
        self.swing_left = DEFAULT_SWING_LEFT
        self.swing_right = DEFAULT_SWING_RIGHT
    
    def reset(self):
        self.analysis_count = 0
        logger.debug("SMC Detector reset")
    
    # ========================================================================
    # PIVOT DETECTION
    # ========================================================================
    
    def _find_all_pivots(self, df: pd.DataFrame, left_bars: int, right_bars: int) -> Tuple[List[PivotPoint], List[PivotPoint]]:
        """
        Находит ВСЕ pivot точки в истории (v6.0 LuxAlgo style)
        
        Включает:
        1. Подтверждённые pivot'ы (есть данные слева И справа)
        2. Потенциальные pivot'ы для последних баров (только слева, partial confirmation)
        """
        pivot_highs = []
        pivot_lows = []
        
        if len(df) < left_bars + 1:
            return pivot_highs, pivot_lows
        
        highs = df['high'].values
        lows = df['low'].values
        total_bars = len(df)
        
        # ================================================================
        # 1. ПОДТВЕРЖДЁННЫЕ PIVOT'ы (полная валидация слева И справа)
        # ================================================================
        confirmed_end = total_bars - right_bars
        
        for i in range(left_bars, confirmed_end):
            current_high = highs[i]
            current_low = lows[i]
            
            # Pivot High
            left_highs = highs[i - left_bars:i]
            right_highs = highs[i + 1:i + right_bars + 1]
            
            if len(left_highs) > 0 and len(right_highs) > 0:
                if current_high > np.max(left_highs) and current_high >= np.max(right_highs):
                    bar_time = str(df.index[i]) if hasattr(df.index, '__getitem__') else str(i)
                    pivot_highs.append(PivotPoint(
                        price=float(current_high),
                        bar_index=i,
                        bar_time=bar_time,
                        is_high=True
                    ))
            
            # Pivot Low
            left_lows = lows[i - left_bars:i]
            right_lows = lows[i + 1:i + right_bars + 1]
            
            if len(left_lows) > 0 and len(right_lows) > 0:
                if current_low < np.min(left_lows) and current_low <= np.min(right_lows):
                    bar_time = str(df.index[i]) if hasattr(df.index, '__getitem__') else str(i)
                    pivot_lows.append(PivotPoint(
                        price=float(current_low),
                        bar_index=i,
                        bar_time=bar_time,
                        is_high=False
                    ))
        
        # ================================================================
        # 2. ПОТЕНЦИАЛЬНЫЕ PIVOT'ы для последних баров (v6.0 LuxAlgo style)
        # Проверяем только левую сторону + частичную правую (сколько есть)
        # ================================================================
        min_right_confirm = max(1, right_bars // 2)  # Минимум 50% правых баров
        
        for i in range(confirmed_end, total_bars - min_right_confirm):
            current_high = highs[i]
            current_low = lows[i]
            available_right = total_bars - i - 1
            
            # Проверяем левую сторону полностью
            left_highs = highs[i - left_bars:i]
            left_lows = lows[i - left_bars:i]
            
            # Проверяем доступную правую сторону
            if available_right > 0:
                right_highs = highs[i + 1:i + 1 + available_right]
                right_lows = lows[i + 1:i + 1 + available_right]
            else:
                right_highs = np.array([])
                right_lows = np.array([])
            
            # Потенциальный Pivot High
            if len(left_highs) > 0:
                is_left_valid = current_high > np.max(left_highs)
                is_right_valid = len(right_highs) == 0 or current_high >= np.max(right_highs)
                
                if is_left_valid and is_right_valid:
                    bar_time = str(df.index[i]) if hasattr(df.index, '__getitem__') else str(i)
                    pivot_highs.append(PivotPoint(
                        price=float(current_high),
                        bar_index=i,
                        bar_time=bar_time,
                        is_high=True
                    ))
            
            # Потенциальный Pivot Low
            if len(left_lows) > 0:
                is_left_valid = current_low < np.min(left_lows)
                is_right_valid = len(right_lows) == 0 or current_low <= np.min(right_lows)
                
                if is_left_valid and is_right_valid:
                    bar_time = str(df.index[i]) if hasattr(df.index, '__getitem__') else str(i)
                    pivot_lows.append(PivotPoint(
                        price=float(current_low),
                        bar_index=i,
                        bar_time=bar_time,
                        is_high=False
                    ))
        
        return pivot_highs, pivot_lows
    
    # ========================================================================
    # UNIFIED TREND TIMELINE v7.3 - ПРАВИЛЬНАЯ РЕАЛИЗАЦИЯ
    # ========================================================================
    
    def _build_unified_trend_timeline(self, df: pd.DataFrame, atr: float = 0.0) -> Dict[int, int]:
        """
        v7.3: Строим unified trend как ФУНКЦИЮ времени (bar_index → trend)
        
        КРИТИЧЕСКОЕ ИСПРАВЛЕНИЕ v7.3:
        - v7.2 использовал ОДИН unified_trend для ВСЕЙ истории (неправильно!)
        - v7.3 строит тренд для КАЖДОГО бара (правильно!)
        
        Алгоритм:
        1. Собираем ВСЕ пробои (internal + swing) с bar_index
        2. Сортируем по времени
        3. Идём по истории bar-by-bar:
           - Если есть пробой на этом баре → обновляем тренд
           - Приоритет: Swing > Internal (если оба на одном баре)
        4. Возвращаем Dict[bar_index, trend_at_that_bar]
        
        Теперь для каждого пробоя используется ПРАВИЛЬНЫЙ тренд
        на момент ДО этого пробоя!
        """
        if len(df) < 15:
            return {}
        
        # Получаем все pivot точки
        int_pivot_highs, int_pivot_lows = self._find_all_pivots(df, self.internal_left, self.internal_right)
        sw_pivot_highs, sw_pivot_lows = self._find_all_pivots(df, self.swing_left, self.swing_right)
        
        highs = df['high'].values
        lows = df['low'].values
        total_bars = len(df)
        
        min_break_threshold = self._get_min_break_threshold(df, atr)
        
        # Собираем все пробои с их bar_index и direction
        all_breaks = []
        
        def collect_breaks(pivot_highs, pivot_lows, level_name):
            """Собираем пробои с метаданными"""
            breaks = []
            active_pivot_high = None
            active_pivot_low = None
            ph_idx = 0
            pl_idx = 0
            
            for bar_i in range(total_bars):
                # Обновляем активные pivots
                while ph_idx < len(pivot_highs) and pivot_highs[ph_idx].bar_index < bar_i:
                    active_pivot_high = pivot_highs[ph_idx]
                    ph_idx += 1
                
                while pl_idx < len(pivot_lows) and pivot_lows[pl_idx].bar_index < bar_i:
                    active_pivot_low = pivot_lows[pl_idx]
                    pl_idx += 1
                
                # Bullish break
                if active_pivot_high and active_pivot_high.price > 0:
                    if highs[bar_i] - active_pivot_high.price > min_break_threshold:
                        breaks.append({
                            'bar_index': bar_i,
                            'direction': BULLISH,
                            'level': level_name,
                            'pivot_index': active_pivot_high.bar_index
                        })
                        active_pivot_high = None
                
                # Bearish break
                if active_pivot_low and active_pivot_low.price > 0:
                    if active_pivot_low.price - lows[bar_i] > min_break_threshold:
                        breaks.append({
                            'bar_index': bar_i,
                            'direction': BEARISH,
                            'level': level_name,
                            'pivot_index': active_pivot_low.bar_index
                        })
                        active_pivot_low = None
            
            return breaks
        
        # Собираем с обоих уровней
        internal_breaks = collect_breaks(int_pivot_highs, int_pivot_lows, 'internal')
        swing_breaks = collect_breaks(sw_pivot_highs, sw_pivot_lows, 'swing')
        
        all_breaks = internal_breaks + swing_breaks
        all_breaks.sort(key=lambda x: (x['bar_index'], 0 if x['level'] == 'swing' else 1))  # swing приоритет
        
        # Строим timeline: bar_index → trend
        trend_timeline = {}
        current_trend = NEUTRAL
        
        break_idx = 0
        for bar_i in range(total_bars):
            # Проверяем есть ли пробои на этом баре
            breaks_on_this_bar = []
            while break_idx < len(all_breaks) and all_breaks[break_idx]['bar_index'] == bar_i:
                breaks_on_this_bar.append(all_breaks[break_idx])
                break_idx += 1
            
            # Если есть пробои - обновляем тренд
            if breaks_on_this_bar:
                # Приоритет swing (уже отсортировано)
                current_trend = breaks_on_this_bar[0]['direction']
            
            # Сохраняем тренд для этого бара
            trend_timeline[bar_i] = current_trend
        
        return trend_timeline
    
    def _get_min_break_threshold(self, df: pd.DataFrame, atr: float) -> float:
        """Helper: расчёт минимального порога пробоя"""
        closes = df['close'].values
        current_price = closes[-1] if len(closes) > 0 else 0
        min_break_atr = atr * MIN_BREAK_ATR_RATIO if atr > 0 else 0
        min_break_pct = current_price * (MIN_BREAK_PERCENT / 100) if current_price > 0 else 0
        return max(min_break_atr, min_break_pct)
    
    # ========================================================================
    # BAR-BY-BAR STRUCTURE DETECTION
    # ========================================================================
    
    def _detect_structure_history(self, df: pd.DataFrame, 
                                   pivot_highs: List[PivotPoint],
                                   pivot_lows: List[PivotPoint],
                                   structure_name: str = "swing",
                                   atr: float = 0.0,
                                   trend_timeline: Dict[int, int] = None) -> Tuple[List[StructureBreak], List[StructureBreak], int]:
        """
        Bar-by-bar сканирование истории (v7.3)
        
        v7.3 КРИТИЧЕСКОЕ ИСПРАВЛЕНИЕ:
        - Теперь принимает trend_timeline (Dict[bar_index, trend])
        - Для каждого пробоя использует ПРАВИЛЬНЫЙ тренд на момент ДО пробоя
        - Не один unified_trend на всю историю, а тренд для каждого бара!
        
        v7.2: Добавлен unified_trend для правильного определения CHoCH/BOS (УСТАРЕЛО)
        - Если unified_trend задан, используем его вместо локального current_trend
        - Это решает проблему когда internal меняет тренд, а swing должен видеть BOS
        
        Добавлено v6.0:
        - confirmed: True если пробой ТЕЛОМ (close), False если только тенью
        - Для TG бота: использовать только confirmed=True
        
        Добавлено v6.1:
        - ATR-фильтр: игнорируем микро-пробои меньше MIN_BREAK_ATR_RATIO * ATR
        - Убирает шум на графике
        """
        all_choch = []
        all_bos = []
        
        if not pivot_highs and not pivot_lows:
            return all_choch, all_bos, NEUTRAL
        
        # v7.3: Используем trend_timeline если задан
        use_timeline = trend_timeline is not None and len(trend_timeline) > 0
        current_trend = NEUTRAL  # Локальный тренд (если нет timeline)
        local_trend = NEUTRAL  # Локальный тренд для возврата
        
        # v7.2: Dedupe pivot - отслеживаем использованные pivot'ы
        used_pivot_indices = set()
        
        active_pivot_high = None
        active_pivot_low = None
        ph_idx = 0
        pl_idx = 0
        
        highs = df['high'].values
        lows = df['low'].values
        closes = df['close'].values
        total_bars = len(df)
        
        # v6.1: Минимальный порог пробоя (ATR-based или процентный)
        min_break_threshold = self._get_min_break_threshold(df, atr)
        
        for bar_i in range(total_bars):
            current_high = highs[bar_i]
            current_low = lows[bar_i]
            current_close = closes[bar_i]
            bar_time = str(df.index[bar_i]) if hasattr(df.index, '__getitem__') else str(bar_i)
            
            # Обновляем активные pivot'ы
            while ph_idx < len(pivot_highs) and pivot_highs[ph_idx].bar_index < bar_i:
                active_pivot_high = pivot_highs[ph_idx]
                ph_idx += 1
            
            while pl_idx < len(pivot_lows) and pivot_lows[pl_idx].bar_index < bar_i:
                active_pivot_low = pivot_lows[pl_idx]
                pl_idx += 1
            
            # ============================================================
            # BULLISH BREAK (пробой вверх)
            # ============================================================
            if active_pivot_high and active_pivot_high.price > 0:
                # v7.2 DEDUPE: Проверяем что этот pivot ещё не использовался
                if active_pivot_high.bar_index not in used_pivot_indices:
                    break_distance = current_high - active_pivot_high.price
                    
                    # v6.1: Пробой должен быть значимым (больше порога)
                    if break_distance > min_break_threshold:
                        # v7.3: Используем тренд НА МОМЕНТ ЭТОГО БАРА из timeline
                        if use_timeline:
                            # Берём тренд ДО текущего бара (bar_i - 1)
                            trend_before_break = trend_timeline.get(bar_i - 1, NEUTRAL) if bar_i > 0 else NEUTRAL
                        else:
                            # Fallback на локальный тренд
                            trend_before_break = current_trend
                        
                        is_choch = (trend_before_break == BEARISH)
                        break_type = 'BULLISH_CHOCH' if is_choch else 'BULLISH_BOS'
                        
                        # v6.0: confirmed = пробой ТЕЛОМ свечи (close > pivot)
                        # break_by_wick = пробой только тенью (close <= pivot)
                        break_by_wick = current_close <= active_pivot_high.price
                        confirmed = not break_by_wick  # confirmed если close > pivot
                        
                        event = StructureBreak(
                            break_type=break_type,
                            price=active_pivot_high.price,
                            bar_index=bar_i,
                            bar_time=bar_time,
                            pivot_bar_index=active_pivot_high.bar_index,
                            is_choch=is_choch,
                            bars_ago=total_bars - 1 - bar_i,
                            break_by_wick=break_by_wick,
                            confirmed=confirmed
                        )
                        
                        if is_choch:
                            all_choch.append(event)
                        else:
                            all_bos.append(event)
                        
                        # v7.3: Обновляем ТОЛЬКО локальный тренд (не timeline!)
                        if not use_timeline:
                            current_trend = BULLISH
                        local_trend = BULLISH
                        
                        # v7.2: Помечаем pivot как использованный
                        used_pivot_indices.add(active_pivot_high.bar_index)
                        active_pivot_high = None
            
            # ============================================================
            # BEARISH BREAK (пробой вниз)
            # ============================================================
            if active_pivot_low and active_pivot_low.price > 0:
                # v7.2 DEDUPE: Проверяем что этот pivot ещё не использовался
                if active_pivot_low.bar_index not in used_pivot_indices:
                    break_distance = active_pivot_low.price - current_low
                    
                    # v6.1: Пробой должен быть значимым (больше порога)
                    if break_distance > min_break_threshold:
                        # v7.3: Используем тренд НА МОМЕНТ ЭТОГО БАРА из timeline
                        if use_timeline:
                            # Берём тренд ДО текущего бара (bar_i - 1)
                            trend_before_break = trend_timeline.get(bar_i - 1, NEUTRAL) if bar_i > 0 else NEUTRAL
                        else:
                            # Fallback на локальный тренд
                            trend_before_break = current_trend
                        
                        is_choch = (trend_before_break == BULLISH)
                        break_type = 'BEARISH_CHOCH' if is_choch else 'BEARISH_BOS'
                        
                        # v6.0: confirmed = пробой ТЕЛОМ свечи (close < pivot)
                        break_by_wick = current_close >= active_pivot_low.price
                        confirmed = not break_by_wick  # confirmed если close < pivot
                        
                        event = StructureBreak(
                            break_type=break_type,
                            price=active_pivot_low.price,
                            bar_index=bar_i,
                            bar_time=bar_time,
                            pivot_bar_index=active_pivot_low.bar_index,
                            is_choch=is_choch,
                            bars_ago=total_bars - 1 - bar_i,
                            break_by_wick=break_by_wick,
                            confirmed=confirmed
                        )
                        
                        if is_choch:
                            all_choch.append(event)
                        else:
                            all_bos.append(event)
                        
                        # v7.3: Обновляем ТОЛЬКО локальный тренд (не timeline!)
                        if not use_timeline:
                            current_trend = BEARISH
                        local_trend = BEARISH
                        
                        # v7.2: Помечаем pivot как использованный
                        used_pivot_indices.add(active_pivot_low.bar_index)
                        active_pivot_low = None
        
        # v7.2: Возвращаем локальный тренд (для обратной совместимости)
        return all_choch, all_bos, local_trend
    
    def _structure_break_to_dict(self, sb: StructureBreak) -> Dict:
        return {
            'type': sb.break_type,
            'price': float(sb.price),
            'bar_index': int(sb.bar_index),
            'time': sb.bar_time,
            'pivot_bar_index': int(sb.pivot_bar_index),
            'is_choch': bool(sb.is_choch),
            'bars_ago': int(sb.bars_ago),
            'break_by_wick': bool(sb.break_by_wick),
            'confirmed': bool(sb.confirmed)  # v6.0: для TG бота
        }
    
    # ========================================================================
    # MARKET STRUCTURE DETECTION
    # ========================================================================
    
    def detect_market_structure(self, df: pd.DataFrame) -> Dict:
        """
        Определение структуры рынка v7.3
        
        v7.3 КРИТИЧЕСКОЕ ИСПРАВЛЕНИЕ:
        - Unified Trend как ФУНКЦИЯ ВРЕМЕНИ (timeline), а не одно значение!
        - Для каждого пробоя используется ПРАВИЛЬНЫЙ тренд на момент ДО пробоя
        - Исправлена ошибка v7.2 (использовал финальный тренд для всей истории)
        
        v7.2 (УСТАРЕЛО):
        - Унифицированный тренд для Internal и Swing (НО ОДИН для всей истории!)
        - Dedupe pivot (каждый pivot используется один раз)
        
        Возвращает:
        - all_*: ВСЕ события для визуализации на графике
        - *_fresh: свежие события (bars_ago <= FRESH_SIGNAL_BARS) для визуализации
        - *_confirmed: ПОДТВЕРЖДЁННЫЕ свежие события для TG бота
        """
        result = {
            'all_internal_choch': [], 'all_internal_bos': [],
            'all_swing_choch': [], 'all_swing_bos': [],
            'internal_choch': [], 'internal_bos': [],
            'swing_choch': [], 'swing_bos': [],
            # v6.0: Подтверждённые сигналы для TG бота
            'internal_choch_confirmed': [], 'internal_bos_confirmed': [],
            'swing_choch_confirmed': [], 'swing_bos_confirmed': [],
            'internal_trend': 'NEUTRAL', 'swing_trend': 'NEUTRAL',
            'internal_pivot_high': 0.0, 'internal_pivot_low': 0.0,
            'swing_pivot_high': 0.0, 'swing_pivot_low': 0.0
        }
        
        if len(df) < 15:
            return result
        
        # v6.1: Рассчитываем ATR для фильтрации шума
        atr = self._calculate_atr(df)
        
        # ================================================================
        # v7.3: UNIFIED TREND TIMELINE - Тренд на КАЖДЫЙ бар
        # ================================================================
        trend_timeline = self._build_unified_trend_timeline(df, atr)
        
        # Финальный тренд для логирования
        final_trend = trend_timeline.get(len(df) - 1, NEUTRAL) if trend_timeline else NEUTRAL
        trend_name = 'BULLISH' if final_trend == BULLISH else 'BEARISH' if final_trend == BEARISH else 'NEUTRAL'
        logger.info(f"v7.3 Unified Trend Timeline: {len(trend_timeline)} bars | Final trend: {trend_name}")
        
        # ================================================================
        # INTERNAL STRUCTURE (чувствительная, для микро-движений)
        # ================================================================
        int_pivot_highs, int_pivot_lows = self._find_all_pivots(df, self.internal_left, self.internal_right)
        int_all_choch, int_all_bos, int_trend = self._detect_structure_history(
            df, int_pivot_highs, int_pivot_lows, "internal", atr, trend_timeline
        )
        
        result['all_internal_choch'] = [self._structure_break_to_dict(sb) for sb in int_all_choch]
        result['all_internal_bos'] = [self._structure_break_to_dict(sb) for sb in int_all_bos]
        
        # Свежие (для визуализации)
        result['internal_choch'] = [self._structure_break_to_dict(sb) for sb in int_all_choch if sb.bars_ago <= FRESH_SIGNAL_BARS]
        result['internal_bos'] = [self._structure_break_to_dict(sb) for sb in int_all_bos if sb.bars_ago <= FRESH_SIGNAL_BARS]
        
        # v6.0: Подтверждённые (для TG бота)
        result['internal_choch_confirmed'] = [
            self._structure_break_to_dict(sb) for sb in int_all_choch 
            if sb.confirmed and sb.bars_ago <= CONFIRMED_SIGNAL_BARS
        ]
        result['internal_bos_confirmed'] = [
            self._structure_break_to_dict(sb) for sb in int_all_bos 
            if sb.confirmed and sb.bars_ago <= CONFIRMED_SIGNAL_BARS
        ]
        
        result['internal_trend'] = 'UPTREND' if int_trend == BULLISH else 'DOWNTREND' if int_trend == BEARISH else 'NEUTRAL'
        
        if int_pivot_highs:
            result['internal_pivot_high'] = int_pivot_highs[-1].price
        if int_pivot_lows:
            result['internal_pivot_low'] = int_pivot_lows[-1].price
        
        # ================================================================
        # SWING STRUCTURE (основная структура)
        # ================================================================
        sw_pivot_highs, sw_pivot_lows = self._find_all_pivots(df, self.swing_left, self.swing_right)
        sw_all_choch, sw_all_bos, sw_trend = self._detect_structure_history(
            df, sw_pivot_highs, sw_pivot_lows, "swing", atr, trend_timeline
        )
        
        result['all_swing_choch'] = [self._structure_break_to_dict(sb) for sb in sw_all_choch]
        result['all_swing_bos'] = [self._structure_break_to_dict(sb) for sb in sw_all_bos]
        
        # Свежие (для визуализации)
        result['swing_choch'] = [self._structure_break_to_dict(sb) for sb in sw_all_choch if sb.bars_ago <= FRESH_SIGNAL_BARS]
        result['swing_bos'] = [self._structure_break_to_dict(sb) for sb in sw_all_bos if sb.bars_ago <= FRESH_SIGNAL_BARS]
        
        # v6.0: Подтверждённые (для TG бота)
        result['swing_choch_confirmed'] = [
            self._structure_break_to_dict(sb) for sb in sw_all_choch 
            if sb.confirmed and sb.bars_ago <= CONFIRMED_SIGNAL_BARS
        ]
        result['swing_bos_confirmed'] = [
            self._structure_break_to_dict(sb) for sb in sw_all_bos 
            if sb.confirmed and sb.bars_ago <= CONFIRMED_SIGNAL_BARS
        ]
        
        result['swing_trend'] = 'UPTREND' if sw_trend == BULLISH else 'DOWNTREND' if sw_trend == BEARISH else 'NEUTRAL'
        
        if sw_pivot_highs:
            result['swing_pivot_high'] = sw_pivot_highs[-1].price
        if sw_pivot_lows:
            result['swing_pivot_low'] = sw_pivot_lows[-1].price
        
        # ================================================================
        # ЛОГИРОВАНИЕ v7.3
        # ================================================================
        confirmed_count = (len(result['swing_bos_confirmed']) + len(result['swing_choch_confirmed']) +
                          len(result['internal_bos_confirmed']) + len(result['internal_choch_confirmed']))
        
        min_break = atr * MIN_BREAK_ATR_RATIO if atr > 0 else 0
        logger.info(f"v7.3 Structure: ATR={atr:.2f}, min_break={min_break:.2f} | Timeline bars: {len(trend_timeline)}")
        logger.info(f"v7.3 Pivots: Internal H={len(int_pivot_highs)} L={len(int_pivot_lows)}, Swing H={len(sw_pivot_highs)} L={len(sw_pivot_lows)}")
        logger.info(f"v7.3 BOS/CHoCH: Internal BOS={len(int_all_bos)} CHoCH={len(int_all_choch)}, Swing BOS={len(sw_all_bos)} CHoCH={len(sw_all_choch)}")
        logger.info(f"v7.3 CONFIRMED (for TG bot): {confirmed_count} signals | Trends: I={result['internal_trend']}, S={result['swing_trend']}")
        
        # Логируем последние события
        if sw_all_bos:
            last_bos = sw_all_bos[-1]
            logger.debug(f"Last Swing BOS: {last_bos.break_type}, price={last_bos.price:.2f}, "
                        f"confirmed={last_bos.confirmed}, bars_ago={last_bos.bars_ago}")
        if sw_all_choch:
            last_choch = sw_all_choch[-1]
            logger.debug(f"Last Swing CHoCH: {last_choch.break_type}, price={last_choch.price:.2f}, "
                        f"confirmed={last_choch.confirmed}, bars_ago={last_choch.bars_ago}")
        
        return result
    
    # ========================================================================
    # РАСЧЁТ ЗОН PREMIUM/DISCOUNT v7.0 (SWING-BASED)
    # ========================================================================
    
    def calculate_zones(self, df: pd.DataFrame, swing_high: float = 0, swing_low: float = 0) -> Dict:
        """
        Расчёт зон Premium/Discount v7.0
        
        УЛУЧШЕНИЯ:
        - Использует актуальные Swing High/Low вместо 250 свечей
        - Более узкие и точные зоны
        - Fallback на последние 50 свечей если swing не найден
        
        ВАЖНО: Формат для фронтенда AIPanel.jsx:
        analysis.advanced.key_levels.Current_Zone
        """
        try:
            if len(df) < 10:
                return self._get_empty_zones()
            
            current_close = float(df['close'].iloc[-1])
            
            # ================================================================
            # v7.0: Используем Swing High/Low если переданы
            # Это даёт более точный и узкий диапазон
            # ================================================================
            if swing_high > 0 and swing_low > 0 and swing_high > swing_low:
                h_max = swing_high
                l_min = swing_low
                range_source = 'SWING_POINTS'
            else:
                # Fallback: используем последние 50 свечей (не 250!)
                lookback = min(50, len(df))
                recent_df = df.tail(lookback)
                h_max = float(recent_df['high'].max())
                l_min = float(recent_df['low'].min())
                range_source = 'LOOKBACK_50'
            
            # Защита от деления на ноль
            if h_max == l_min:
                pos_pct = 50.0
                zone_name = "EQUILIBRIUM"
            else:
                # Позиция в диапазоне (0% = дно, 100% = вершина)
                pos_pct = ((current_close - l_min) / (h_max - l_min)) * 100
                
                # Ограничиваем 0-100% (цена может выйти за пределы swing)
                pos_pct = max(0, min(100, pos_pct))
                
                # Определяем зону
                if pos_pct > PREMIUM_THRESHOLD:
                    zone_name = "PREMIUM"
                elif pos_pct < DISCOUNT_THRESHOLD:
                    zone_name = "DISCOUNT"
                else:
                    zone_name = "EQUILIBRIUM"
            
            # Расчёт уровней для зон
            range_size = h_max - l_min
            equilibrium_price = (h_max + l_min) / 2
            premium_bottom = l_min + (range_size * PREMIUM_THRESHOLD / 100)
            discount_top = l_min + (range_size * DISCOUNT_THRESHOLD / 100)
            
            zones = {
                'premium': {
                    'top': float(h_max),
                    'bottom': float(premium_bottom)
                },
                'equilibrium': {
                    'top': float(premium_bottom),
                    'bottom': float(discount_top),
                    'price': float(equilibrium_price)
                },
                'discount': {
                    'top': float(discount_top),
                    'bottom': float(l_min)
                },
                'current_zone': zone_name,
                'range_high': float(h_max),
                'range_low': float(l_min),
                'range_source': range_source,
                'position_in_range_pct': float(round(pos_pct, 2))
            }
            
            logger.info(f"v7.0 Zones ({range_source}): {zone_name} ({pos_pct:.1f}%) | Range: [{l_min:.2f} - {h_max:.2f}] (size: {range_size:.2f})")
            
            return zones
            
        except Exception as e:
            logger.error(f"Error calculating zones: {e}")
            return self._get_empty_zones()
    
    def _get_empty_zones(self) -> Dict:
        """Пустая структура зон"""
        return {
            'premium': {'top': 0.0, 'bottom': 0.0},
            'equilibrium': {'top': 0.0, 'bottom': 0.0, 'price': 0.0},
            'discount': {'top': 0.0, 'bottom': 0.0},
            'current_zone': 'UNKNOWN',
            'range_high': 0.0,
            'range_low': 0.0,
            'range_source': 'NONE',
            'position_in_range_pct': 50.0
        }
    
    # ========================================================================
    # РАСЧЁТ ADVANCED DATA (ДЛЯ ФРОНТЕНДА!)
    # ========================================================================
    
    def calculate_advanced_data(self, df: pd.DataFrame, zones: Dict) -> Dict:
        """
        Расчёт advanced данных для фронтенда
        
        ФОРМАТ ДЛЯ AIPanel.jsx:
        analysis.advanced.key_levels.Current_Zone
        analysis.advanced.key_levels.Range_Percent
        """
        try:
            if len(df) < 10:
                return self._get_empty_advanced()
            
            # Daily High/Low
            dh = float(df.tail(96)['high'].max())  # ~24 часа на M15
            dl = float(df.tail(96)['low'].min())
            
            # Previous Day (приблизительно)
            if len(df) > 96:
                prev_df = df.iloc[-192:-96]
                pdh = float(prev_df['high'].max())
                pdl = float(prev_df['low'].min())
            else:
                pdh = dh
                pdl = dl
            
            advanced = {
                'key_levels': {
                    'Current_Zone': zones.get('current_zone', 'UNKNOWN'),
                    'Range_Percent': zones.get('position_in_range_pct', 50.0),
                    'High_250': zones.get('range_high', 0.0),
                    'Low_250': zones.get('range_low', 0.0),
                    'DH': float(dh),
                    'DL': float(dl),
                    'PDH': float(pdh),
                    'PDL': float(pdl),
                    'Equilibrium_Price': zones.get('equilibrium', {}).get('price', 0.0)
                },
                'structure_points': {
                    'nearest_swing_high': zones.get('range_high', 0.0),
                    'nearest_swing_low': zones.get('range_low', 0.0)
                },
                'range': {
                    'high': zones.get('range_high', 0.0),
                    'low': zones.get('range_low', 0.0),
                    'size': zones.get('range_high', 0.0) - zones.get('range_low', 0.0),
                    'source': zones.get('range_source', 'LOOKBACK_250')
                },
                'zones': zones
            }
            
            return advanced
            
        except Exception as e:
            logger.error(f"Error calculating advanced data: {e}")
            return self._get_empty_advanced()
    
    def _get_empty_advanced(self) -> Dict:
        """Пустая структура advanced"""
        return {
            'key_levels': {
                'Current_Zone': 'UNKNOWN',
                'Range_Percent': 50.0,
                'High_250': 0.0,
                'Low_250': 0.0,
                'DH': 0.0,
                'DL': 0.0,
                'PDH': 0.0,
                'PDL': 0.0,
                'Equilibrium_Price': 0.0
            },
            'structure_points': {
                'nearest_swing_high': 0.0,
                'nearest_swing_low': 0.0
            },
            'range': {
                'high': 0.0,
                'low': 0.0,
                'size': 0.0,
                'source': 'NONE'
            },
            'zones': self._get_empty_zones()
        }
    
    # ========================================================================
    # v5.2 ULTRA SENSITIVE IMPULSE DETECTION
    # ========================================================================
    
    def detect_impulse_context_v52(self, df: pd.DataFrame, analysis_result: Dict) -> Dict:
        """
        v5.2 Ultra Sensitive детекция импульса
        
        Флаги:
        - has_breakout: цена НИЖЕ минимума последних 20 свечей (или ВЫШЕ максимума)
        - is_void_run: цена в пределах 0.5% от глобального экстремума
        - is_impulse: движение > 1.5x средней свечи за 15 баров
        """
        context = {
            'market_condition': 'RANGING',
            'has_breakout': False,
            'is_void_run': False,
            'is_impulse': False,
            'impulse_strength': 0,
            'impulse_direction': 'NONE',
            'allow_discount_sell': False,
            'allow_premium_buy': False,
            'breakout_type': None,
            'void_run_type': None,
            'override_reason': ''
        }
        
        try:
            if len(df) < LOOKBACK_BARS:
                return context
            
            current_close = float(df['close'].iloc[-1])
            current_high = float(df['high'].iloc[-1])
            current_low = float(df['low'].iloc[-1])
            
            # Глобальные экстремумы (250 свечей)
            global_high = float(df['high'].max())
            global_low = float(df['low'].min())
            
            # Локальные экстремумы (20 свечей для breakout)
            recent_df = df.tail(BREAKOUT_LOOKBACK)
            local_high = float(recent_df['high'].max())
            local_low = float(recent_df['low'].min())
            
            # Средняя свеча за IMPULSE_LOOKBACK баров
            impulse_df = df.tail(IMPULSE_LOOKBACK)
            candle_ranges = (impulse_df['high'] - impulse_df['low']).values
            avg_candle = float(np.mean(candle_ranges)) if len(candle_ranges) > 0 else 0
            
            # Движение за IMPULSE_LOOKBACK баров
            impulse_high = float(impulse_df['high'].max())
            impulse_low = float(impulse_df['low'].min())
            recent_move = impulse_high - impulse_low
            
            override_reasons = []
            
            # ================================================================
            # 1. HAS_BREAKOUT: Цена пробила локальный экстремум
            # ================================================================
            
            # Исключаем текущую свечу из расчёта локального минимума
            if len(df) > BREAKOUT_LOOKBACK:
                lookback_df = df.iloc[-(BREAKOUT_LOOKBACK + 1):-1]  # 20 свечей БЕЗ текущей
                local_low_excl = float(lookback_df['low'].min())
                local_high_excl = float(lookback_df['high'].max())
            else:
                local_low_excl = local_low
                local_high_excl = local_high
            
            # Bearish breakout: текущая цена НИЖЕ минимума предыдущих 20 свечей
            if current_close < local_low_excl:
                context['has_breakout'] = True
                context['breakout_type'] = 'BEARISH'
                context['allow_discount_sell'] = True
                override_reasons.append(f"📉 Пробой минимума 20 свечей (цена {current_close:.2f} < {local_low_excl:.2f})")
                logger.info(f"v5.2 BREAKOUT DETECTED: {current_close:.2f} < {local_low_excl:.2f}")
            
            # Bullish breakout: текущая цена ВЫШЕ максимума предыдущих 20 свечей
            if current_close > local_high_excl:
                context['has_breakout'] = True
                context['breakout_type'] = 'BULLISH'
                context['allow_premium_buy'] = True
                override_reasons.append(f"📈 Пробой максимума 20 свечей (цена {current_close:.2f} > {local_high_excl:.2f})")
                logger.info(f"v5.2 BREAKOUT DETECTED: {current_close:.2f} > {local_high_excl:.2f}")
            
            # ================================================================
            # 2. IS_VOID_RUN: Цена у глобального экстремума (0.5%)
            # ================================================================
            
            void_low_threshold = global_low * (1 + VOID_RUN_THRESHOLD)  # +0.5%
            void_high_threshold = global_high * (1 - VOID_RUN_THRESHOLD)  # -0.5%
            
            # Bearish void run: у самого дна
            if current_close <= void_low_threshold:
                context['is_void_run'] = True
                context['void_run_type'] = 'BEARISH'
                context['allow_discount_sell'] = True
                override_reasons.append(f"🕳️ Void Run: цена {current_close:.2f} у дна {global_low:.2f} (порог {void_low_threshold:.2f})")
                logger.info(f"v5.2 VOID RUN DETECTED: {current_close:.2f} <= {void_low_threshold:.2f}")
            
            # Bullish void run: у самого верха
            if current_close >= void_high_threshold:
                context['is_void_run'] = True
                context['void_run_type'] = 'BULLISH'
                context['allow_premium_buy'] = True
                override_reasons.append(f"🕳️ Void Run: цена {current_close:.2f} у вершины {global_high:.2f}")
                logger.info(f"v5.2 VOID RUN DETECTED: {current_close:.2f} >= {void_high_threshold:.2f}")
            
            # ================================================================
            # 3. IS_IMPULSE: Сильное движение за последние свечи
            # ================================================================
            
            if avg_candle > 0:
                impulse_ratio = recent_move / avg_candle
                
                if impulse_ratio >= IMPULSE_CANDLE_THRESHOLD:
                    context['is_impulse'] = True
                    context['impulse_strength'] = min(100, int(impulse_ratio * 30))
                    
                    # Определяем направление импульса
                    first_close = float(impulse_df['close'].iloc[0])
                    last_close = float(impulse_df['close'].iloc[-1])
                    
                    if last_close < first_close:
                        context['impulse_direction'] = 'BEARISH'
                        context['allow_discount_sell'] = True
                        override_reasons.append(f"⚡ Медвежий импульс {context['impulse_strength']}% (движение {recent_move:.2f} > {avg_candle * IMPULSE_CANDLE_THRESHOLD:.2f})")
                    else:
                        context['impulse_direction'] = 'BULLISH'
                        context['allow_premium_buy'] = True
                        override_reasons.append(f"⚡ Бычий импульс {context['impulse_strength']}%")
                    
                    logger.info(f"v5.2 IMPULSE DETECTED: ratio={impulse_ratio:.2f}, strength={context['impulse_strength']}%")
            
            # ================================================================
            # 4. MARKET CONDITION
            # ================================================================
            
            swing_bos_count = len(analysis_result.get('swing_bos', []))
            swing_choch_count = len(analysis_result.get('swing_choch', []))
            
            if context['has_breakout'] or context['is_void_run'] or swing_bos_count >= IMPULSE_THRESHOLD:
                context['market_condition'] = 'IMPULSE_TREND'
            elif context['is_impulse'] or swing_bos_count >= 1:
                context['market_condition'] = 'STRONG_TREND'
            else:
                context['market_condition'] = 'RANGING'
            
            context['override_reason'] = ' | '.join(override_reasons) if override_reasons else ''
            
            logger.info(f"v5.2 Impulse Context: condition={context['market_condition']}, "
                       f"breakout={context['has_breakout']}, void_run={context['is_void_run']}, "
                       f"impulse={context['is_impulse']} ({context['impulse_strength']}%)")
            
        except Exception as e:
            logger.error(f"Error in detect_impulse_context_v52: {e}")
        
        return context
    
    # ========================================================================
    # ORDER BLOCKS v7.0 (MITIGATION / INVALIDATION / BREAKER)
    # ========================================================================
    
    def detect_order_blocks(self, df: pd.DataFrame, lookback: int = 50) -> Dict:
        """
        Order Blocks v7.0 с профессиональной логикой:
        
        status:
        - 'active': OB активен, цена ещё не касалась его
        - 'mitigated': цена вернулась к OB (хорошая зона входа)
        - 'invalidated': цена прошла через OB полностью (OB больше не актуален)
        - 'breaker': пробитый OB стал зоной противоположного направления
        """
        order_blocks = {'internal': [], 'swing': [], 'breakers': []}
        
        try:
            if len(df) < 10:
                return order_blocks
            
            current_price = float(df['close'].iloc[-1])
            atr = self._calculate_atr(df)
            min_ob_size = atr * 0.2 if atr > 0 else 0  # Минимальный размер OB
            
            recent_df = df.tail(lookback).reset_index(drop=True)
            global_offset = len(df) - len(recent_df)
            
            raw_obs = []
            
            for i in range(2, len(recent_df) - 1):
                curr = recent_df.iloc[i]
                prev = recent_df.iloc[i - 1]
                next_bar = recent_df.iloc[i + 1]
                
                # ============================================================
                # BULLISH ORDER BLOCK
                # Условие: медвежья свеча перед сильным бычьим импульсом
                # ============================================================
                if prev['close'] < prev['open']:  # Медвежья свеча
                    impulse_up = next_bar['close'] > curr['high']  # Импульс вверх
                    body_size = abs(prev['open'] - prev['close'])
                    
                    if impulse_up and body_size >= min_ob_size:
                        ob_top = float(prev['open'])
                        ob_bottom = float(prev['low'])
                        
                        raw_obs.append({
                            'type': 'BULL_OB',
                            'top': ob_top,
                            'bottom': ob_bottom,
                            'bar_index': global_offset + i - 1,
                            'bars_ago': len(recent_df) - i,
                            'formation_bar': i - 1
                        })
                
                # ============================================================
                # BEARISH ORDER BLOCK
                # Условие: бычья свеча перед сильным медвежьим импульсом
                # ============================================================
                if prev['close'] > prev['open']:  # Бычья свеча
                    impulse_down = next_bar['close'] < curr['low']  # Импульс вниз
                    body_size = abs(prev['close'] - prev['open'])
                    
                    if impulse_down and body_size >= min_ob_size:
                        ob_top = float(prev['high'])
                        ob_bottom = float(prev['open'])
                        
                        raw_obs.append({
                            'type': 'BEAR_OB',
                            'top': ob_top,
                            'bottom': ob_bottom,
                            'bar_index': global_offset + i - 1,
                            'bars_ago': len(recent_df) - i,
                            'formation_bar': i - 1
                        })
            
            # ============================================================
            # ПРОВЕРКА СТАТУСА КАЖДОГО OB (mitigation / invalidation)
            # ============================================================
            for ob in raw_obs:
                formation_bar = ob['formation_bar']
                ob_top = ob['top']
                ob_bottom = ob['bottom']
                is_bull = ob['type'] == 'BULL_OB'
                
                status = 'active'
                mitigated_at = None
                
                # Проверяем все свечи ПОСЛЕ формирования OB
                for j in range(formation_bar + 2, len(recent_df)):
                    candle = recent_df.iloc[j]
                    candle_high = candle['high']
                    candle_low = candle['low']
                    candle_close = candle['close']
                    
                    if is_bull:
                        # BULL OB: ждём возврат цены к зоне сверху
                        if candle_low <= ob_top and candle_low >= ob_bottom:
                            # Цена коснулась OB — mitigated
                            if status == 'active':
                                status = 'mitigated'
                                mitigated_at = global_offset + j
                        
                        # Цена прошла НИЖЕ OB полностью — invalidated
                        if candle_close < ob_bottom:
                            status = 'invalidated'
                            break
                    else:
                        # BEAR OB: ждём возврат цены к зоне снизу
                        if candle_high >= ob_bottom and candle_high <= ob_top:
                            # Цена коснулась OB — mitigated
                            if status == 'active':
                                status = 'mitigated'
                                mitigated_at = global_offset + j
                        
                        # Цена прошла ВЫШЕ OB полностью — invalidated
                        if candle_close > ob_top:
                            status = 'invalidated'
                            break
                
                # Добавляем OB с статусом
                ob_data = {
                    'type': ob['type'],
                    'top': ob['top'],
                    'bottom': ob['bottom'],
                    'bar_index': ob['bar_index'],
                    'bars_ago': ob['bars_ago'],
                    'status': status,
                    'mitigated_at': mitigated_at
                }
                
                # Показываем только активные и mitigated OB (не invalidated)
                if status in ['active', 'mitigated']:
                    order_blocks['internal'].append(ob_data)
                elif status == 'invalidated':
                    # Превращаем в Breaker Block (противоположная зона)
                    breaker_type = 'BEAR_BREAKER' if is_bull else 'BULL_BREAKER'
                    order_blocks['breakers'].append({
                        'type': breaker_type,
                        'top': ob['top'],
                        'bottom': ob['bottom'],
                        'bar_index': ob['bar_index'],
                        'original_type': ob['type']
                    })
            
            # Ограничиваем количество
            order_blocks['internal'] = order_blocks['internal'][-5:]
            order_blocks['breakers'] = order_blocks['breakers'][-3:]
            
            logger.debug(f"v7.0 Order Blocks: {len(order_blocks['internal'])} active/mitigated, "
                        f"{len(order_blocks['breakers'])} breakers")
            
        except Exception as e:
            logger.error(f"Error detecting order blocks: {e}")
        
        return order_blocks
    
    # ========================================================================
    # FAIR VALUE GAPS v7.0 (MITIGATION STATUS)
    # ========================================================================
    
    def detect_fvg(self, df: pd.DataFrame, lookback: int = 50) -> List[Dict]:
        """
        Fair Value Gaps v7.0 с mitigation статусом:
        
        status:
        - 'open': FVG не заполнен
        - 'partially_filled': FVG частично заполнен
        - 'filled': FVG полностью заполнен (больше не актуален)
        """
        fvg_list = []
        
        try:
            if len(df) < 3:
                return fvg_list
            
            recent_df = df.tail(lookback).reset_index(drop=True)
            atr = self._calculate_atr(df)
            min_gap = atr * 0.2 if atr > 0 else 0.5  # Уменьшил порог
            
            global_offset = len(df) - len(recent_df)
            raw_fvgs = []
            
            for i in range(1, len(recent_df) - 1):
                candle1 = recent_df.iloc[i - 1]
                candle3 = recent_df.iloc[i + 1]
                
                # Bullish FVG (gap up)
                if candle3['low'] > candle1['high']:
                    gap_size = candle3['low'] - candle1['high']
                    if gap_size >= min_gap:
                        raw_fvgs.append({
                            'type': 'BULL_FVG',
                            'top': float(candle3['low']),
                            'bottom': float(candle1['high']),
                            'price': float((candle3['low'] + candle1['high']) / 2),
                            'gap_size': float(gap_size),
                            'bar_index': global_offset + i,
                            'bars_ago': len(recent_df) - 1 - i,
                            'formation_bar': i
                        })
                
                # Bearish FVG (gap down)
                elif candle3['high'] < candle1['low']:
                    gap_size = candle1['low'] - candle3['high']
                    if gap_size >= min_gap:
                        raw_fvgs.append({
                            'type': 'BEAR_FVG',
                            'top': float(candle1['low']),
                            'bottom': float(candle3['high']),
                            'price': float((candle1['low'] + candle3['high']) / 2),
                            'gap_size': float(gap_size),
                            'bar_index': global_offset + i,
                            'bars_ago': len(recent_df) - 1 - i,
                            'formation_bar': i
                        })
            
            # ============================================================
            # ПРОВЕРКА MITIGATION СТАТУСА
            # ============================================================
            for fvg in raw_fvgs:
                formation_bar = fvg['formation_bar']
                fvg_top = fvg['top']
                fvg_bottom = fvg['bottom']
                is_bull = fvg['type'] == 'BULL_FVG'
                
                status = 'open'
                fill_percent = 0.0
                
                # Проверяем свечи после формирования FVG
                for j in range(formation_bar + 2, len(recent_df)):
                    candle = recent_df.iloc[j]
                    
                    if is_bull:
                        # BULL FVG: заполняется когда цена падает в gap
                        if candle['low'] <= fvg_top:
                            # Расчёт процента заполнения
                            penetration = fvg_top - max(candle['low'], fvg_bottom)
                            fill_percent = max(fill_percent, (penetration / (fvg_top - fvg_bottom)) * 100)
                            
                            if candle['low'] <= fvg_bottom:
                                status = 'filled'
                                fill_percent = 100.0
                                break
                            else:
                                status = 'partially_filled'
                    else:
                        # BEAR FVG: заполняется когда цена растёт в gap
                        if candle['high'] >= fvg_bottom:
                            penetration = min(candle['high'], fvg_top) - fvg_bottom
                            fill_percent = max(fill_percent, (penetration / (fvg_top - fvg_bottom)) * 100)
                            
                            if candle['high'] >= fvg_top:
                                status = 'filled'
                                fill_percent = 100.0
                                break
                            else:
                                status = 'partially_filled'
                
                # Добавляем только open и partially_filled FVG
                if status != 'filled':
                    fvg_data = {
                        'type': fvg['type'],
                        'top': fvg['top'],
                        'bottom': fvg['bottom'],
                        'price': fvg['price'],
                        'gap_size': fvg['gap_size'],
                        'bar_index': fvg['bar_index'],
                        'bars_ago': fvg['bars_ago'],
                        'status': status,
                        'fill_percent': round(fill_percent, 1)
                    }
                    fvg_list.append(fvg_data)
            
            # Ограничиваем количество
            fvg_list = fvg_list[-5:]
            
            logger.debug(f"v7.0 FVG: {len(fvg_list)} active gaps")
            
        except Exception as e:
            logger.error(f"Error detecting FVG: {e}")
        
        return fvg_list
    
    # ========================================================================
    # LIQUIDITY v7.0 (POOLS + SWEEPS)
    # ========================================================================
    
    def detect_liquidity(self, df: pd.DataFrame, lookback: int = 100) -> List[Dict]:
        """
        Liquidity v7.0:
        - Liquidity Pools: зоны где сосредоточены стопы (равные хаи/лои)
        - Liquidity Sweeps: когда цена выметает ликвидность и разворачивается
        """
        liquidity = []
        
        try:
            if len(df) < 10:
                return liquidity
            
            recent_df = df.tail(lookback).reset_index(drop=True)
            global_offset = len(df) - len(recent_df)
            highs = recent_df['high'].values
            lows = recent_df['low'].values
            closes = recent_df['close'].values
            
            # ============================================================
            # LIQUIDITY POOLS (зоны скопления стопов)
            # ============================================================
            for i in range(3, len(recent_df) - 3):
                # Swing High = Resistance / Sell-side liquidity
                if highs[i] > max(highs[i-3:i]) and highs[i] > max(highs[i+1:i+4]):
                    liquidity.append({
                        'type': 'SELL_SIDE_LIQ',
                        'price': float(highs[i]),
                        'bar_index': global_offset + i,
                        'strength': 1,
                        'swept': False
                    })
                
                # Swing Low = Support / Buy-side liquidity
                if lows[i] < min(lows[i-3:i]) and lows[i] < min(lows[i+1:i+4]):
                    liquidity.append({
                        'type': 'BUY_SIDE_LIQ',
                        'price': float(lows[i]),
                        'bar_index': global_offset + i,
                        'strength': 1,
                        'swept': False
                    })
            
            # ============================================================
            # LIQUIDITY SWEEPS (выметание ликвидности)
            # ============================================================
            # Проверяем каждый уровень ликвидности
            for liq in liquidity:
                liq_bar = liq['bar_index'] - global_offset
                liq_price = liq['price']
                is_sell_side = liq['type'] == 'SELL_SIDE_LIQ'
                
                # Проверяем свечи после формирования уровня
                for j in range(liq_bar + 1, len(recent_df)):
                    candle_high = highs[j]
                    candle_low = lows[j]
                    candle_close = closes[j]
                    
                    if is_sell_side:
                        # Sweep of highs: цена пробила high, но закрылась ниже
                        if candle_high > liq_price and candle_close < liq_price:
                            liq['swept'] = True
                            liq['sweep_bar'] = global_offset + j
                            liq['type'] = 'SWEPT_HIGH'
                            break
                    else:
                        # Sweep of lows: цена пробила low, но закрылась выше
                        if candle_low < liq_price and candle_close > liq_price:
                            liq['swept'] = True
                            liq['sweep_bar'] = global_offset + j
                            liq['type'] = 'SWEPT_LOW'
                            break
            
            # Сортируем: swept уровни важнее
            liquidity = sorted(liquidity, key=lambda x: (x.get('swept', False), x['price']), reverse=True)[:6]
            
            logger.debug(f"v7.0 Liquidity: {len(liquidity)} levels, "
                        f"swept={sum(1 for l in liquidity if l.get('swept', False))}")
            
        except Exception as e:
            logger.error(f"Error detecting liquidity: {e}")
        
        return liquidity
    
    # ========================================================================
    # EQUAL HIGHS/LOWS
    # ========================================================================
    
    def detect_equal_highs_lows(self, df: pd.DataFrame, lookback: int = 50) -> Dict:
        equal_levels = {'eqh': [], 'eql': []}
        
        try:
            if len(df) < 10:
                return equal_levels
            
            atr = self._calculate_atr(df)
            threshold = atr * 0.1 if atr > 0 else df['close'].iloc[-1] * 0.001
            recent_df = df.tail(lookback)
            
            swing_highs = []
            swing_lows = []
            
            for i in range(2, len(recent_df) - 2):
                if recent_df['high'].iloc[i] > recent_df['high'].iloc[i-1] and recent_df['high'].iloc[i] > recent_df['high'].iloc[i+1]:
                    swing_highs.append({'price': float(recent_df['high'].iloc[i]), 'index': i})
                
                if recent_df['low'].iloc[i] < recent_df['low'].iloc[i-1] and recent_df['low'].iloc[i] < recent_df['low'].iloc[i+1]:
                    swing_lows.append({'price': float(recent_df['low'].iloc[i]), 'index': i})
            
            # Equal Highs
            for i in range(len(swing_highs) - 1):
                for j in range(i + 1, len(swing_highs)):
                    if abs(swing_highs[i]['price'] - swing_highs[j]['price']) < threshold:
                        avg_price = (swing_highs[i]['price'] + swing_highs[j]['price']) / 2
                        if not any(abs(eq['price'] - avg_price) < threshold for eq in equal_levels['eqh']):
                            equal_levels['eqh'].append({'price': float(avg_price), 'type': 'EQUAL_HIGHS', 'touches': 2})
            
            # Equal Lows
            for i in range(len(swing_lows) - 1):
                for j in range(i + 1, len(swing_lows)):
                    if abs(swing_lows[i]['price'] - swing_lows[j]['price']) < threshold:
                        avg_price = (swing_lows[i]['price'] + swing_lows[j]['price']) / 2
                        if not any(abs(eq['price'] - avg_price) < threshold for eq in equal_levels['eql']):
                            equal_levels['eql'].append({'price': float(avg_price), 'type': 'EQUAL_LOWS', 'touches': 2})
            
            equal_levels['eqh'] = equal_levels['eqh'][-3:]
            equal_levels['eql'] = equal_levels['eql'][-3:]
            
        except Exception as e:
            logger.error(f"Error detecting EQH/EQL: {e}")
        
        return equal_levels
    
    # ========================================================================
    # HELPER METHODS
    # ========================================================================
    
    def _calculate_atr(self, df: pd.DataFrame, period: int = 14) -> float:
        try:
            high = df['high']
            low = df['low']
            close = df['close']
            
            tr1 = high - low
            tr2 = abs(high - close.shift(1))
            tr3 = abs(low - close.shift(1))
            
            tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
            atr = tr.rolling(window=min(period, len(df))).mean().iloc[-1]
            
            return float(atr) if not pd.isna(atr) else 0.0
        except:
            return 0.0
    
    def _get_empty_result(self) -> Dict:
        return sanitize_for_json({
            'order_blocks': [], 'order_blocks_internal': [], 'order_blocks_swing': [],
            'breaker_blocks': [],  # v7.0
            'fvg': [], 'liquidity': [],
            'choch': [], 'bos': [],
            'internal_choch': [], 'internal_bos': [],
            'swing_choch': [], 'swing_bos': [],
            'all_choch': [], 'all_bos': [],
            'all_internal_choch': [], 'all_internal_bos': [],
            'all_swing_choch': [], 'all_swing_bos': [],
            # v6.0: Confirmed сигналы для TG бота
            'choch_confirmed': [], 'bos_confirmed': [],
            'internal_choch_confirmed': [], 'internal_bos_confirmed': [],
            'swing_choch_confirmed': [], 'swing_bos_confirmed': [],
            'eqh': [], 'eql': [],
            'trend': 'NEUTRAL', 'internal_trend': 'NEUTRAL',
            'internal_pivot_high': 0.0, 'internal_pivot_low': 0.0,
            'swing_pivot_high': 0.0, 'swing_pivot_low': 0.0,
            'advanced': self._get_empty_advanced(),
            'signals_count': 0,
            'confirmed_signals_count': 0,
            'impulse_context': {
                'market_condition': 'RANGING',
                'has_breakout': False, 'is_void_run': False, 'is_impulse': False,
                'impulse_strength': 0, 'impulse_direction': 'NONE',
                'allow_discount_sell': False, 'allow_premium_buy': False
            }
        })
    
    # ========================================================================
    # ГЛАВНЫЙ МЕТОД АНАЛИЗА
    # ========================================================================
    
    def analyze(self, df) -> Dict:
        """
        Полный SMC анализ v7.0 Professional
        
        Улучшения v7.0:
        - Узкие зоны Premium/Discount на основе Swing Points
        - Order Blocks с mitigation/invalidation/breaker статусом
        - FVG с fill статусом
        - Liquidity Sweeps
        - ATR-фильтр шума
        
        Возвращает:
        1. ВСЕ сигналы (all_*, fresh) - для визуализации на графике
        2. CONFIRMED сигналы (*_confirmed) - для TG бота (консервативные)
        """
        try:
            if isinstance(df, list):
                if not df:
                    return self._get_empty_result()
                df = pd.DataFrame(df)
            
            if not isinstance(df, pd.DataFrame):
                return self._get_empty_result()
            
            required = ['open', 'high', 'low', 'close']
            if not all(col in df.columns for col in required):
                return self._get_empty_result()
            
            if len(df) < 15:
                return self._get_empty_result()
            
            self.analysis_count += 1
            current_price = float(df['close'].iloc[-1])
            
            logger.info(f"=== SMC Analysis v7.3 #{self.analysis_count} | {len(df)} bars | Price: {current_price:.2f} ===")
            
            # 1. Market Structure (v6.0 с confirmed флагами)
            market_structure = self.detect_market_structure(df)
            
            # 2. Order Blocks
            order_blocks = self.detect_order_blocks(df)
            
            # 3. FVG
            fvg = self.detect_fvg(df)
            
            # 4. Liquidity
            liquidity = self.detect_liquidity(df)
            
            # 5. Equal Highs/Lows
            equal_levels = self.detect_equal_highs_lows(df)
            
            # 6. Зоны Premium/Discount v7.0 (используем swing points для узких зон)
            swing_high = market_structure.get('swing_pivot_high', 0)
            swing_low = market_structure.get('swing_pivot_low', 0)
            zones = self.calculate_zones(df, swing_high, swing_low)
            
            # 7. Advanced Data
            advanced = self.calculate_advanced_data(df, zones)
            
            # ================================================================
            # СБОРКА РЕЗУЛЬТАТА v7.0
            # ================================================================
            all_order_blocks = order_blocks['internal'] + order_blocks['swing']
            breaker_blocks = order_blocks.get('breakers', [])
            fresh_choch = market_structure['internal_choch'] + market_structure['swing_choch']
            fresh_bos = market_structure['internal_bos'] + market_structure['swing_bos']
            all_choch = market_structure['all_internal_choch'] + market_structure['all_swing_choch']
            all_bos = market_structure['all_internal_bos'] + market_structure['all_swing_bos']
            
            # Confirmed сигналы для TG бота
            confirmed_choch = market_structure['internal_choch_confirmed'] + market_structure['swing_choch_confirmed']
            confirmed_bos = market_structure['internal_bos_confirmed'] + market_structure['swing_bos_confirmed']
            
            result = {
                # Order Blocks v7.0 (с mitigation статусом)
                'order_blocks': all_order_blocks,
                'order_blocks_internal': order_blocks['internal'],
                'order_blocks_swing': order_blocks['swing'],
                'breaker_blocks': breaker_blocks,  # v7.0: пробитые OB
                
                # FVG & Liquidity v7.0 (с fill статусом и sweeps)
                'fvg': fvg,
                'liquidity': liquidity,
                
                # ============================================================
                # BOS/CHoCH для ВИЗУАЛИЗАЦИИ (все уровни на графике)
                # ============================================================
                'choch': fresh_choch,
                'bos': fresh_bos,
                'internal_choch': market_structure['internal_choch'],
                'internal_bos': market_structure['internal_bos'],
                'swing_choch': market_structure['swing_choch'],
                'swing_bos': market_structure['swing_bos'],
                'all_choch': all_choch,
                'all_bos': all_bos,
                'all_internal_choch': market_structure['all_internal_choch'],
                'all_internal_bos': market_structure['all_internal_bos'],
                'all_swing_choch': market_structure['all_swing_choch'],
                'all_swing_bos': market_structure['all_swing_bos'],
                
                # ============================================================
                # v6.0: CONFIRMED сигналы для TG БОТА (консервативные)
                # Только пробой ТЕЛОМ + bars_ago <= 5
                # ============================================================
                'choch_confirmed': confirmed_choch,
                'bos_confirmed': confirmed_bos,
                'internal_choch_confirmed': market_structure['internal_choch_confirmed'],
                'internal_bos_confirmed': market_structure['internal_bos_confirmed'],
                'swing_choch_confirmed': market_structure['swing_choch_confirmed'],
                'swing_bos_confirmed': market_structure['swing_bos_confirmed'],
                
                # Trends & Pivots
                'trend': market_structure['swing_trend'],
                'internal_trend': market_structure['internal_trend'],
                'internal_pivot_high': market_structure['internal_pivot_high'],
                'internal_pivot_low': market_structure['internal_pivot_low'],
                'swing_pivot_high': market_structure['swing_pivot_high'],
                'swing_pivot_low': market_structure['swing_pivot_low'],
                
                # Equal Highs/Lows
                'eqh': equal_levels['eqh'],
                'eql': equal_levels['eql'],
                
                # Advanced
                'advanced': advanced
            }
            
            # 8. Impulse Context
            impulse_context = self.detect_impulse_context_v52(df, result)
            result['impulse_context'] = impulse_context
            
            # Статистика v7.0
            total = len(all_order_blocks) + len(fvg) + len(liquidity) + len(all_choch) + len(all_bos)
            confirmed_total = len(confirmed_choch) + len(confirmed_bos)
            result['signals_count'] = total
            result['confirmed_signals_count'] = confirmed_total
            
            # Подсчёт активных/mitigated OB
            active_obs = sum(1 for ob in all_order_blocks if ob.get('status') == 'active')
            mitigated_obs = sum(1 for ob in all_order_blocks if ob.get('status') == 'mitigated')
            
            logger.info(f"SMC v7.3 Result: OB={len(all_order_blocks)} (active={active_obs}, mitigated={mitigated_obs}, breakers={len(breaker_blocks)}) | "
                       f"FVG={len(fvg)} | Liq={len(liquidity)} | CONFIRMED={confirmed_total}")
            logger.info(f"SMC v7.3 Zone ({zones['range_source']}): {zones['current_zone']} ({zones['position_in_range_pct']:.1f}%) | "
                       f"Range: [{zones['range_low']:.2f} - {zones['range_high']:.2f}]")
            
            return sanitize_for_json(result)
            
        except Exception as e:
            logger.error(f"Error in SMC analysis: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return self._get_empty_result()


# Глобальный экземпляр
smc_detector = SMCDetector()