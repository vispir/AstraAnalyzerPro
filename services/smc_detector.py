"""
SMC Detector v6.0 - LuxAlgo Style
==================================
КРИТИЧЕСКИЕ ИЗМЕНЕНИЯ v6.0:
- Параметры pivot detection как в LuxAlgo (swing: 8/4, internal: 3/2)
- Потенциальные pivot'ы для последних баров (без right confirmation)
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
    """SMC Detector v5.2 Ultra Sensitive"""
    
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
    # BAR-BY-BAR STRUCTURE DETECTION
    # ========================================================================
    
    def _detect_structure_history(self, df: pd.DataFrame, 
                                   pivot_highs: List[PivotPoint],
                                   pivot_lows: List[PivotPoint],
                                   structure_name: str = "swing") -> Tuple[List[StructureBreak], List[StructureBreak], int]:
        """
        Bar-by-bar сканирование истории (v6.0)
        
        Добавлено:
        - confirmed: True если пробой ТЕЛОМ (close), False если только тенью
        - Для TG бота: использовать только confirmed=True
        """
        all_choch = []
        all_bos = []
        
        if not pivot_highs and not pivot_lows:
            return all_choch, all_bos, NEUTRAL
        
        current_trend = NEUTRAL
        active_pivot_high = None
        active_pivot_low = None
        ph_idx = 0
        pl_idx = 0
        
        highs = df['high'].values
        lows = df['low'].values
        closes = df['close'].values
        total_bars = len(df)
        
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
                if current_high > active_pivot_high.price:
                    is_choch = (current_trend == BEARISH)
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
                    
                    current_trend = BULLISH
                    active_pivot_high = None
            
            # ============================================================
            # BEARISH BREAK (пробой вниз)
            # ============================================================
            if active_pivot_low and active_pivot_low.price > 0:
                if current_low < active_pivot_low.price:
                    is_choch = (current_trend == BULLISH)
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
                    
                    current_trend = BEARISH
                    active_pivot_low = None
        
        return all_choch, all_bos, current_trend
    
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
        Определение структуры рынка v6.0
        
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
        
        # ================================================================
        # INTERNAL STRUCTURE (чувствительная, для микро-движений)
        # ================================================================
        int_pivot_highs, int_pivot_lows = self._find_all_pivots(df, self.internal_left, self.internal_right)
        int_all_choch, int_all_bos, int_trend = self._detect_structure_history(df, int_pivot_highs, int_pivot_lows, "internal")
        
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
        sw_all_choch, sw_all_bos, sw_trend = self._detect_structure_history(df, sw_pivot_highs, sw_pivot_lows, "swing")
        
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
        # ЛОГИРОВАНИЕ v6.0
        # ================================================================
        confirmed_count = (len(result['swing_bos_confirmed']) + len(result['swing_choch_confirmed']) +
                          len(result['internal_bos_confirmed']) + len(result['internal_choch_confirmed']))
        
        logger.info(f"v6.0 Structure: Internal pivots H={len(int_pivot_highs)} L={len(int_pivot_lows)}, "
                   f"Swing pivots H={len(sw_pivot_highs)} L={len(sw_pivot_lows)}")
        logger.info(f"v6.0 BOS/CHoCH: Internal BOS={len(int_all_bos)} CHoCH={len(int_all_choch)}, "
                   f"Swing BOS={len(sw_all_bos)} CHoCH={len(sw_all_choch)}")
        logger.info(f"v6.0 CONFIRMED (for TG bot): {confirmed_count} signals | "
                   f"Trends: I={result['internal_trend']}, S={result['swing_trend']}")
        
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
    # РАСЧЁТ ЗОН PREMIUM/DISCOUNT (ИСПРАВЛЕНО ДЛЯ ФРОНТЕНДА!)
    # ========================================================================
    
    def calculate_zones(self, df: pd.DataFrame) -> Dict:
        """
        Расчёт зон Premium/Discount на основе LOOKBACK_BARS свечей
        
        ВАЖНО: Формат для фронтенда AIPanel.jsx:
        analysis.advanced.key_levels.Current_Zone
        """
        try:
            if len(df) < 10:
                return self._get_empty_zones()
            
            # Берём последние LOOKBACK_BARS свечей (или все если меньше)
            lookback = min(LOOKBACK_BARS, len(df))
            recent_df = df.tail(lookback)
            
            # Глобальные экстремумы
            h_max = float(recent_df['high'].max())
            l_min = float(recent_df['low'].min())
            current_close = float(df['close'].iloc[-1])
            
            # Защита от деления на ноль
            if h_max == l_min:
                pos_pct = 50.0
                zone_name = "EQUILIBRIUM"
            else:
                # Позиция в диапазоне (0% = дно, 100% = вершина)
                pos_pct = ((current_close - l_min) / (h_max - l_min)) * 100
                
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
                'range_source': 'LOOKBACK_250',
                'position_in_range_pct': float(round(pos_pct, 2))
            }
            
            logger.info(f"Zones: {zone_name} ({pos_pct:.1f}%) | Range: [{l_min:.2f} - {h_max:.2f}]")
            
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
    # ORDER BLOCKS
    # ========================================================================
    
    def detect_order_blocks(self, df: pd.DataFrame, lookback: int = 50) -> Dict:
        order_blocks = {'internal': [], 'swing': []}
        
        try:
            if len(df) < 10:
                return order_blocks
            
            current_price = float(df['close'].iloc[-1])
            recent_df = df.tail(lookback)
            
            # Вычисляем смещение для конвертации локального индекса в глобальный
            global_offset = len(df) - len(recent_df)
            
            for i in range(2, len(recent_df) - 1):
                curr = recent_df.iloc[i]
                prev = recent_df.iloc[i - 1]
                next_bar = recent_df.iloc[i + 1] if i + 1 < len(recent_df) else None
                
                if next_bar is not None:
                    # Bullish OB
                    if prev['close'] < prev['open'] and next_bar['close'] > curr['high']:
                        if current_price >= prev['low']:
                            order_blocks['internal'].append({
                                'type': 'BULL_OB',
                                'top': float(prev['open']),
                                'bottom': float(prev['low']),
                                'bar_index': global_offset + i - 1,  # Конвертация в глобальный индекс
                                'bars_ago': len(recent_df) - i
                            })
                    
                    # Bearish OB
                    if prev['close'] > prev['open'] and next_bar['close'] < curr['low']:
                        if current_price <= prev['high']:
                            order_blocks['internal'].append({
                                'type': 'BEAR_OB',
                                'top': float(prev['high']),
                                'bottom': float(prev['open']),
                                'bar_index': global_offset + i - 1,  # Конвертация в глобальный индекс
                                'bars_ago': len(recent_df) - i
                            })
            
            order_blocks['internal'] = order_blocks['internal'][-5:]
            order_blocks['swing'] = order_blocks['swing'][-3:]
            
        except Exception as e:
            logger.error(f"Error detecting order blocks: {e}")
        
        return order_blocks
    
    # ========================================================================
    # FAIR VALUE GAPS
    # ========================================================================
    
    def detect_fvg(self, df: pd.DataFrame, lookback: int = 50) -> List[Dict]:
        fvg_list = []
        
        try:
            if len(df) < 3:
                return fvg_list
            
            recent_df = df.tail(lookback).reset_index(drop=True)
            atr = self._calculate_atr(df)
            min_gap = atr * 0.3 if atr > 0 else 1.0
            
            # Вычисляем смещение для конвертации локального индекса в глобальный
            global_offset = len(df) - len(recent_df)
            
            for i in range(1, len(recent_df) - 1):
                candle1 = recent_df.iloc[i - 1]
                candle3 = recent_df.iloc[i + 1]
                
                # Bullish FVG
                if candle3['low'] > candle1['high']:
                    gap_size = candle3['low'] - candle1['high']
                    if gap_size > min_gap:
                        fvg_list.append({
                            'type': 'BULL_FVG',
                            'top': float(candle3['low']),
                            'bottom': float(candle1['high']),
                            'price': float((candle3['low'] + candle1['high']) / 2),
                            'gap_size': float(gap_size),
                            'bar_index': global_offset + i,  # Конвертация в глобальный индекс
                            'bars_ago': len(recent_df) - 1 - i
                        })
                
                # Bearish FVG
                elif candle3['high'] < candle1['low']:
                    gap_size = candle1['low'] - candle3['high']
                    if gap_size > min_gap:
                        fvg_list.append({
                            'type': 'BEAR_FVG',
                            'top': float(candle1['low']),
                            'bottom': float(candle3['high']),
                            'price': float((candle1['low'] + candle3['high']) / 2),
                            'gap_size': float(gap_size),
                            'bar_index': global_offset + i,  # Конвертация в глобальный индекс
                            'bars_ago': len(recent_df) - 1 - i
                        })
            
            fvg_list = fvg_list[-5:]
            
        except Exception as e:
            logger.error(f"Error detecting FVG: {e}")
        
        return fvg_list
    
    # ========================================================================
    # LIQUIDITY
    # ========================================================================
    
    def detect_liquidity(self, df: pd.DataFrame, lookback: int = 100) -> List[Dict]:
        liquidity = []
        
        try:
            if len(df) < 10:
                return liquidity
            
            recent_df = df.tail(lookback)
            highs = recent_df['high'].values
            lows = recent_df['low'].values
            
            for i in range(3, len(recent_df) - 3):
                if highs[i] > max(highs[i-3:i]) and highs[i] > max(highs[i+1:i+4]):
                    liquidity.append({'type': 'RESISTANCE', 'price': float(highs[i]), 'strength': 1})
                
                if lows[i] < min(lows[i-3:i]) and lows[i] < min(lows[i+1:i+4]):
                    liquidity.append({'type': 'SUPPORT', 'price': float(lows[i]), 'strength': 1})
            
            liquidity = sorted(liquidity, key=lambda x: x['price'], reverse=True)[:4]
            
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
            'confirmed_signals_count': 0,  # v6.0
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
        Полный SMC анализ v6.0 LuxAlgo Style
        
        Возвращает две категории сигналов:
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
            
            logger.info(f"=== SMC Analysis v6.0 #{self.analysis_count} | {len(df)} bars | Price: {current_price:.2f} ===")
            
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
            
            # 6. Зоны Premium/Discount
            zones = self.calculate_zones(df)
            
            # 7. Advanced Data
            advanced = self.calculate_advanced_data(df, zones)
            
            # ================================================================
            # СБОРКА РЕЗУЛЬТАТА v6.0
            # ================================================================
            all_order_blocks = order_blocks['internal'] + order_blocks['swing']
            fresh_choch = market_structure['internal_choch'] + market_structure['swing_choch']
            fresh_bos = market_structure['internal_bos'] + market_structure['swing_bos']
            all_choch = market_structure['all_internal_choch'] + market_structure['all_swing_choch']
            all_bos = market_structure['all_internal_bos'] + market_structure['all_swing_bos']
            
            # v6.0: Confirmed сигналы для TG бота
            confirmed_choch = market_structure['internal_choch_confirmed'] + market_structure['swing_choch_confirmed']
            confirmed_bos = market_structure['internal_bos_confirmed'] + market_structure['swing_bos_confirmed']
            
            result = {
                # Order Blocks
                'order_blocks': all_order_blocks,
                'order_blocks_internal': order_blocks['internal'],
                'order_blocks_swing': order_blocks['swing'],
                
                # FVG & Liquidity
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
            
            # Статистика
            total = len(all_order_blocks) + len(fvg) + len(liquidity) + len(all_choch) + len(all_bos)
            confirmed_total = len(confirmed_choch) + len(confirmed_bos)
            result['signals_count'] = total
            result['confirmed_signals_count'] = confirmed_total
            
            logger.info(f"SMC v6.0 Result: Total={total} | CONFIRMED={confirmed_total} | "
                       f"Trend={market_structure['swing_trend']} | Zone={zones['current_zone']} ({zones['position_in_range_pct']:.1f}%)")
            
            return sanitize_for_json(result)
            
        except Exception as e:
            logger.error(f"Error in SMC analysis: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return self._get_empty_result()


# Глобальный экземпляр
smc_detector = SMCDetector()
