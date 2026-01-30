"""
SMC Detector v4.0 - Bar-by-Bar Historical Replay
=================================================
Полная переработка логики на основе рекомендаций:
- Сканирование ВСЕЙ истории свеча за свечой
- Сохранение состояния тренда на каждом баре
- Корректная детекция исторических BOS/CHoCH для графика

Основано на логике LuxAlgo Smart Money Concepts
"""

import pandas as pd
import numpy as np
import logging
from typing import Dict, List, Tuple, Optional, Any
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# ============================================================================
# КОНСТАНТЫ
# ============================================================================

BULLISH = 1
BEARISH = -1
NEUTRAL = 0

# Параметры по умолчанию (как LuxAlgo)
DEFAULT_INTERNAL_LEFT = 5      # Internal structure: 5 баров слева
DEFAULT_INTERNAL_RIGHT = 5     # Internal structure: 5 баров справа
DEFAULT_SWING_LEFT = 50        # Swing structure: 50 баров слева
DEFAULT_SWING_RIGHT = 50       # Swing structure: 50 баров справа

# Для фильтрации "свежих" сигналов для бота
FRESH_SIGNAL_BARS = 10         # Сигналы за последние N баров считаются "свежими"


# ============================================================================
# СТРУКТУРЫ ДАННЫХ
# ============================================================================

@dataclass
class PivotPoint:
    """Pivot точка с полной информацией"""
    price: float = 0.0
    bar_index: int = 0
    bar_time: str = ""
    is_high: bool = True  # True = Pivot High, False = Pivot Low


@dataclass 
class StructureBreak:
    """Событие пробоя структуры (BOS или CHoCH)"""
    break_type: str = ""       # 'BULLISH_BOS', 'BEARISH_BOS', 'BULLISH_CHOCH', 'BEARISH_CHOCH'
    price: float = 0.0         # Цена пробитого уровня
    bar_index: int = 0         # Индекс бара где произошёл пробой
    bar_time: str = ""         # Время бара
    pivot_bar_index: int = 0   # Индекс pivot бара который был пробит
    is_choch: bool = False     # True = смена тренда, False = продолжение
    bars_ago: int = 0          # Сколько баров назад от текущего
    break_by_wick: bool = False  # Пробой фитилём (high/low) без закрытия


@dataclass
class TrendState:
    """Состояние тренда в определённый момент"""
    bias: int = NEUTRAL        # BULLISH, BEARISH, NEUTRAL
    pivot_high: PivotPoint = field(default_factory=PivotPoint)
    pivot_low: PivotPoint = field(default_factory=PivotPoint)
    last_break_index: int = 0  # Индекс последнего пробоя


# ============================================================================
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# ============================================================================

def sanitize_for_json(obj: Any) -> Any:
    """Рекурсивная конвертация numpy типов в стандартные Python типы"""
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
    """
    SMC Detector v4.0 с Bar-by-Bar Historical Replay
    
    Ключевое отличие от v3.0:
    - Сканируем ВСЮ историю свеча за свечой
    - Сохраняем ВСЕ события BOS/CHoCH с их индексами
    - Можно отрисовать на графике как LuxAlgo
    """
    
    def __init__(self):
        self.analysis_count = 0
        
        # Параметры структуры
        self.internal_left = DEFAULT_INTERNAL_LEFT
        self.internal_right = DEFAULT_INTERNAL_RIGHT
        self.swing_left = DEFAULT_SWING_LEFT
        self.swing_right = DEFAULT_SWING_RIGHT
    
    def reset(self):
        """Сброс состояния детектора"""
        self.analysis_count = 0
        logger.debug("SMC Detector reset")
    
    # ========================================================================
    # PIVOT DETECTION
    # ========================================================================
    
    def _find_all_pivots(self, df: pd.DataFrame, left_bars: int, right_bars: int) -> Tuple[List[PivotPoint], List[PivotPoint]]:
        """
        Находит ВСЕ pivot точки в истории
        
        Pivot High на баре i существует, если:
        - high[i] > всех high на барах [i-left, i-1]
        - high[i] >= всех high на барах [i+1, i+right]
        
        Args:
            df: DataFrame с OHLC данными
            left_bars: Количество баров слева для подтверждения
            right_bars: Количество баров справа для подтверждения
            
        Returns:
            (pivot_highs, pivot_lows) - списки PivotPoint
        """
        pivot_highs = []
        pivot_lows = []
        
        if len(df) < left_bars + right_bars + 1:
            return pivot_highs, pivot_lows
        
        highs = df['high'].values
        lows = df['low'].values
        
        # Сканируем историю (исключаем края где нет достаточно баров)
        for i in range(left_bars, len(df) - right_bars):
            current_high = highs[i]
            current_low = lows[i]
            
            # Проверяем Pivot High
            left_highs = highs[i - left_bars:i]
            right_highs = highs[i + 1:i + right_bars + 1]
            
            if len(left_highs) > 0 and len(right_highs) > 0:
                # Строго выше слева, >= справа (как в Pine Script)
                if current_high > np.max(left_highs) and current_high >= np.max(right_highs):
                    bar_time = str(df.index[i]) if hasattr(df.index, '__getitem__') else str(i)
                    pivot_highs.append(PivotPoint(
                        price=float(current_high),
                        bar_index=i,
                        bar_time=bar_time,
                        is_high=True
                    ))
            
            # Проверяем Pivot Low
            left_lows = lows[i - left_bars:i]
            right_lows = lows[i + 1:i + right_bars + 1]
            
            if len(left_lows) > 0 and len(right_lows) > 0:
                # Строго ниже слева, <= справа
                if current_low < np.min(left_lows) and current_low <= np.min(right_lows):
                    bar_time = str(df.index[i]) if hasattr(df.index, '__getitem__') else str(i)
                    pivot_lows.append(PivotPoint(
                        price=float(current_low),
                        bar_index=i,
                        bar_time=bar_time,
                        is_high=False
                    ))
        
        return pivot_highs, pivot_lows
    
    # ========================================================================
    # BAR-BY-BAR STRUCTURE DETECTION (КЛЮЧЕВОЕ ИЗМЕНЕНИЕ!)
    # ========================================================================
    
    def _detect_structure_history(self, df: pd.DataFrame, 
                                   pivot_highs: List[PivotPoint],
                                   pivot_lows: List[PivotPoint],
                                   structure_name: str = "swing") -> Tuple[List[StructureBreak], List[StructureBreak], int]:
        """
        🔥 ГЛАВНЫЙ МЕТОД: Bar-by-bar сканирование истории
        
        Логика:
        1. Начинаем с NEUTRAL тренда
        2. Идём по каждой свече от начала до конца
        3. На каждом баре проверяем: пробит ли текущий pivot high/low?
        4. Если пробит:
           - Записываем событие (BOS или CHoCH)
           - Обновляем тренд
           - Ищем новый pivot для отслеживания
        
        Returns:
            (all_choch, all_bos, final_trend_bias)
        """
        all_choch: List[StructureBreak] = []
        all_bos: List[StructureBreak] = []
        
        if not pivot_highs and not pivot_lows:
            return all_choch, all_bos, NEUTRAL
        
        # Инициализация состояния
        current_trend = NEUTRAL
        
        # Текущие pivot'ы для отслеживания (будут обновляться)
        active_pivot_high: Optional[PivotPoint] = None
        active_pivot_low: Optional[PivotPoint] = None
        
        # Индексы для поиска следующего pivot
        ph_idx = 0  # Индекс в списке pivot_highs
        pl_idx = 0  # Индекс в списке pivot_lows
        
        # Массивы OHLC
        highs = df['high'].values
        lows = df['low'].values
        closes = df['close'].values
        total_bars = len(df)
        
        # ====================================================================
        # ГЛАВНЫЙ ЦИКЛ: Идём по каждому бару
        # ====================================================================
        for bar_i in range(total_bars):
            current_high = highs[bar_i]
            current_low = lows[bar_i]
            current_close = closes[bar_i]
            bar_time = str(df.index[bar_i]) if hasattr(df.index, '__getitem__') else str(bar_i)
            
            # ------------------------------------------------------------------
            # 1. Обновляем активные pivot'ы (берём последний подтверждённый до текущего бара)
            # ------------------------------------------------------------------
            
            # Найти последний pivot high, который УЖЕ подтверждён (его bar_index < bar_i)
            while ph_idx < len(pivot_highs) and pivot_highs[ph_idx].bar_index < bar_i:
                active_pivot_high = pivot_highs[ph_idx]
                ph_idx += 1
            
            # Найти последний pivot low
            while pl_idx < len(pivot_lows) and pivot_lows[pl_idx].bar_index < bar_i:
                active_pivot_low = pivot_lows[pl_idx]
                pl_idx += 1
            
            # ------------------------------------------------------------------
            # 2. Проверяем пробой Pivot High (Bullish break)
            # ------------------------------------------------------------------
            if active_pivot_high and active_pivot_high.price > 0:
                # Пробой по HIGH (фитиль достиг уровня)
                if current_high > active_pivot_high.price:
                    # Определяем тип: CHoCH если тренд был BEARISH, иначе BOS
                    is_choch = (current_trend == BEARISH)
                    break_type = 'BULLISH_CHOCH' if is_choch else 'BULLISH_BOS'
                    
                    # Пробой фитилём без закрытия?
                    break_by_wick = current_close <= active_pivot_high.price
                    
                    event = StructureBreak(
                        break_type=break_type,
                        price=active_pivot_high.price,
                        bar_index=bar_i,
                        bar_time=bar_time,
                        pivot_bar_index=active_pivot_high.bar_index,
                        is_choch=is_choch,
                        bars_ago=total_bars - 1 - bar_i,  # От последнего бара
                        break_by_wick=break_by_wick
                    )
                    
                    if is_choch:
                        all_choch.append(event)
                    else:
                        all_bos.append(event)
                    
                    # Обновляем тренд
                    current_trend = BULLISH
                    
                    # Сбрасываем pivot high (ищем новый)
                    active_pivot_high = None
            
            # ------------------------------------------------------------------
            # 3. Проверяем пробой Pivot Low (Bearish break)
            # ------------------------------------------------------------------
            if active_pivot_low and active_pivot_low.price > 0:
                # Пробой по LOW
                if current_low < active_pivot_low.price:
                    is_choch = (current_trend == BULLISH)
                    break_type = 'BEARISH_CHOCH' if is_choch else 'BEARISH_BOS'
                    
                    break_by_wick = current_close >= active_pivot_low.price
                    
                    event = StructureBreak(
                        break_type=break_type,
                        price=active_pivot_low.price,
                        bar_index=bar_i,
                        bar_time=bar_time,
                        pivot_bar_index=active_pivot_low.bar_index,
                        is_choch=is_choch,
                        bars_ago=total_bars - 1 - bar_i,
                        break_by_wick=break_by_wick
                    )
                    
                    if is_choch:
                        all_choch.append(event)
                    else:
                        all_bos.append(event)
                    
                    current_trend = BEARISH
                    active_pivot_low = None
        
        return all_choch, all_bos, current_trend
    
    def _structure_break_to_dict(self, sb: StructureBreak) -> Dict:
        """Конвертация StructureBreak в словарь для JSON"""
        return {
            'type': sb.break_type,
            'price': float(sb.price),
            'bar_index': int(sb.bar_index),
            'time': sb.bar_time,
            'pivot_bar_index': int(sb.pivot_bar_index),
            'is_choch': bool(sb.is_choch),
            'bars_ago': int(sb.bars_ago),
            'break_by_wick': bool(sb.break_by_wick)
        }
    
    # ========================================================================
    # MARKET STRUCTURE DETECTION (ОБНОВЛЁННЫЙ)
    # ========================================================================
    
    def detect_market_structure(self, df: pd.DataFrame) -> Dict:
        """
        Определение структуры рынка с bar-by-bar replay
        
        Возвращает:
        - Все исторические BOS/CHoCH (для графика)
        - Свежие BOS/CHoCH (для бота, последние N баров)
        - Текущий тренд
        - Активные pivot уровни
        """
        result = {
            # Все исторические события (для графика)
            'all_internal_choch': [],
            'all_internal_bos': [],
            'all_swing_choch': [],
            'all_swing_bos': [],
            
            # Свежие события (для бота, последние FRESH_SIGNAL_BARS баров)
            'internal_choch': [],
            'internal_bos': [],
            'swing_choch': [],
            'swing_bos': [],
            
            # Тренды
            'internal_trend': 'NEUTRAL',
            'swing_trend': 'NEUTRAL',
            
            # Pivot уровни
            'internal_pivot_high': 0.0,
            'internal_pivot_low': 0.0,
            'swing_pivot_high': 0.0,
            'swing_pivot_low': 0.0
        }
        
        if len(df) < 20:
            return result
        
        # ====================================================================
        # INTERNAL STRUCTURE (size=5)
        # ====================================================================
        int_pivot_highs, int_pivot_lows = self._find_all_pivots(
            df, self.internal_left, self.internal_right
        )
        
        int_all_choch, int_all_bos, int_trend = self._detect_structure_history(
            df, int_pivot_highs, int_pivot_lows, "internal"
        )
        
        # Конвертируем в словари
        result['all_internal_choch'] = [self._structure_break_to_dict(sb) for sb in int_all_choch]
        result['all_internal_bos'] = [self._structure_break_to_dict(sb) for sb in int_all_bos]
        
        # Фильтруем свежие (последние FRESH_SIGNAL_BARS баров)
        result['internal_choch'] = [
            self._structure_break_to_dict(sb) for sb in int_all_choch 
            if sb.bars_ago <= FRESH_SIGNAL_BARS
        ]
        result['internal_bos'] = [
            self._structure_break_to_dict(sb) for sb in int_all_bos 
            if sb.bars_ago <= FRESH_SIGNAL_BARS
        ]
        
        # Тренд
        result['internal_trend'] = 'UPTREND' if int_trend == BULLISH else \
                                   'DOWNTREND' if int_trend == BEARISH else 'NEUTRAL'
        
        # Последние pivot'ы
        if int_pivot_highs:
            result['internal_pivot_high'] = int_pivot_highs[-1].price
        if int_pivot_lows:
            result['internal_pivot_low'] = int_pivot_lows[-1].price
        
        # ====================================================================
        # SWING STRUCTURE (size=50)
        # ====================================================================
        sw_pivot_highs, sw_pivot_lows = self._find_all_pivots(
            df, self.swing_left, self.swing_right
        )
        
        sw_all_choch, sw_all_bos, sw_trend = self._detect_structure_history(
            df, sw_pivot_highs, sw_pivot_lows, "swing"
        )
        
        result['all_swing_choch'] = [self._structure_break_to_dict(sb) for sb in sw_all_choch]
        result['all_swing_bos'] = [self._structure_break_to_dict(sb) for sb in sw_all_bos]
        
        result['swing_choch'] = [
            self._structure_break_to_dict(sb) for sb in sw_all_choch 
            if sb.bars_ago <= FRESH_SIGNAL_BARS
        ]
        result['swing_bos'] = [
            self._structure_break_to_dict(sb) for sb in sw_all_bos 
            if sb.bars_ago <= FRESH_SIGNAL_BARS
        ]
        
        result['swing_trend'] = 'UPTREND' if sw_trend == BULLISH else \
                                'DOWNTREND' if sw_trend == BEARISH else 'NEUTRAL'
        
        if sw_pivot_highs:
            result['swing_pivot_high'] = sw_pivot_highs[-1].price
        if sw_pivot_lows:
            result['swing_pivot_low'] = sw_pivot_lows[-1].price
        
        # Логирование
        logger.info(f"Structure: Internal={result['internal_trend']} "
                   f"(PH:{result['internal_pivot_high']:.2f}, PL:{result['internal_pivot_low']:.2f}), "
                   f"Swing={result['swing_trend']} "
                   f"(PH:{result['swing_pivot_high']:.2f}, PL:{result['swing_pivot_low']:.2f})")
        logger.info(f"History: I-CHoCH:{len(result['all_internal_choch'])}, I-BOS:{len(result['all_internal_bos'])}, "
                   f"S-CHoCH:{len(result['all_swing_choch'])}, S-BOS:{len(result['all_swing_bos'])}")
        logger.info(f"Fresh (last {FRESH_SIGNAL_BARS} bars): I-CHoCH:{len(result['internal_choch'])}, "
                   f"I-BOS:{len(result['internal_bos'])}, S-CHoCH:{len(result['swing_choch'])}, "
                   f"S-BOS:{len(result['swing_bos'])}")
        
        return result
    
    # ========================================================================
    # ORDER BLOCKS
    # ========================================================================
    
    def detect_order_blocks(self, df: pd.DataFrame, lookback: int = 50) -> Dict:
        """Детекция Order Blocks"""
        order_blocks = {'internal': [], 'swing': []}
        
        try:
            if len(df) < 10:
                return order_blocks
            
            current_price = float(df['close'].iloc[-1])
            recent_df = df.tail(lookback)
            
            for i in range(2, len(recent_df) - 1):
                # Bullish OB: медвежья свеча перед бычьим движением
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
                                'bar_index': i - 1,
                                'bars_ago': len(recent_df) - i
                            })
                    
                    # Bearish OB
                    if prev['close'] > prev['open'] and next_bar['close'] < curr['low']:
                        if current_price <= prev['high']:
                            order_blocks['internal'].append({
                                'type': 'BEAR_OB',
                                'top': float(prev['high']),
                                'bottom': float(prev['open']),
                                'bar_index': i - 1,
                                'bars_ago': len(recent_df) - i
                            })
            
            # Ограничиваем количество
            order_blocks['internal'] = order_blocks['internal'][-5:]
            order_blocks['swing'] = order_blocks['swing'][-3:]
            
            logger.info(f"Order Blocks: Internal={len(order_blocks['internal'])}, Swing={len(order_blocks['swing'])}")
            
        except Exception as e:
            logger.error(f"Error detecting order blocks: {e}")
        
        return order_blocks
    
    # ========================================================================
    # FAIR VALUE GAPS
    # ========================================================================
    
    def detect_fvg(self, df: pd.DataFrame, lookback: int = 50) -> List[Dict]:
        """Детекция Fair Value Gaps"""
        fvg_list = []
        
        try:
            if len(df) < 3:
                return fvg_list
            
            recent_df = df.tail(lookback).reset_index(drop=True)
            current_price = float(df['close'].iloc[-1])
            
            # ATR для минимального размера gap
            atr = self._calculate_atr(df)
            min_gap = atr * 0.3 if atr > 0 else current_price * 0.0003
            
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
                            'bar_index': i,
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
                            'bar_index': i,
                            'bars_ago': len(recent_df) - 1 - i
                        })
            
            fvg_list = fvg_list[-5:]
            logger.info(f"FVG detected: {len(fvg_list)}")
            
        except Exception as e:
            logger.error(f"Error detecting FVG: {e}")
        
        return fvg_list
    
    # ========================================================================
    # LIQUIDITY (SUPPORT/RESISTANCE)
    # ========================================================================
    
    def detect_liquidity(self, df: pd.DataFrame, lookback: int = 100) -> List[Dict]:
        """Детекция уровней ликвидности (S/R)"""
        liquidity = []
        
        try:
            if len(df) < 10:
                return liquidity
            
            recent_df = df.tail(lookback)
            highs = recent_df['high'].values
            lows = recent_df['low'].values
            
            # Swing Highs
            for i in range(3, len(recent_df) - 3):
                if highs[i] > max(highs[i-3:i]) and highs[i] > max(highs[i+1:i+4]):
                    liquidity.append({
                        'type': 'RESISTANCE',
                        'price': float(highs[i]),
                        'strength': 1
                    })
            
            # Swing Lows
            for i in range(3, len(recent_df) - 3):
                if lows[i] < min(lows[i-3:i]) and lows[i] < min(lows[i+1:i+4]):
                    liquidity.append({
                        'type': 'SUPPORT',
                        'price': float(lows[i]),
                        'strength': 1
                    })
            
            # Кластеризация близких уровней
            liquidity = self._cluster_levels(liquidity)
            liquidity = sorted(liquidity, key=lambda x: x['strength'], reverse=True)[:4]
            
            logger.info(f"Liquidity levels: {len(liquidity)}")
            
        except Exception as e:
            logger.error(f"Error detecting liquidity: {e}")
        
        return liquidity
    
    def _cluster_levels(self, levels: List[Dict], threshold: float = 0.002) -> List[Dict]:
        """Группировка близких уровней"""
        if not levels:
            return []
        
        sorted_levels = sorted(levels, key=lambda x: x['price'])
        clusters = []
        current_cluster = [sorted_levels[0]]
        
        for level in sorted_levels[1:]:
            cluster_avg = sum(l['price'] for l in current_cluster) / len(current_cluster)
            if abs(level['price'] - cluster_avg) / cluster_avg < threshold:
                current_cluster.append(level)
            else:
                avg_price = sum(l['price'] for l in current_cluster) / len(current_cluster)
                clusters.append({
                    'type': current_cluster[0]['type'],
                    'price': float(avg_price),
                    'strength': len(current_cluster)
                })
                current_cluster = [level]
        
        if current_cluster:
            avg_price = sum(l['price'] for l in current_cluster) / len(current_cluster)
            clusters.append({
                'type': current_cluster[0]['type'],
                'price': float(avg_price),
                'strength': len(current_cluster)
            })
        
        return clusters
    
    # ========================================================================
    # EQUAL HIGHS/LOWS
    # ========================================================================
    
    def detect_equal_highs_lows(self, df: pd.DataFrame, lookback: int = 50) -> Dict:
        """Детекция Equal Highs/Lows"""
        equal_levels = {'eqh': [], 'eql': []}
        
        try:
            if len(df) < 10:
                return equal_levels
            
            atr = self._calculate_atr(df)
            threshold = atr * 0.1 if atr > 0 else df['close'].iloc[-1] * 0.001
            
            recent_df = df.tail(lookback)
            
            # Находим swing highs
            swing_highs = []
            for i in range(2, len(recent_df) - 2):
                if recent_df['high'].iloc[i] > recent_df['high'].iloc[i-1] and \
                   recent_df['high'].iloc[i] > recent_df['high'].iloc[i+1]:
                    swing_highs.append({'price': float(recent_df['high'].iloc[i]), 'index': i})
            
            # Equal Highs
            for i in range(len(swing_highs) - 1):
                for j in range(i + 1, len(swing_highs)):
                    if abs(swing_highs[i]['price'] - swing_highs[j]['price']) < threshold:
                        avg_price = (swing_highs[i]['price'] + swing_highs[j]['price']) / 2
                        if not any(abs(eq['price'] - avg_price) < threshold for eq in equal_levels['eqh']):
                            equal_levels['eqh'].append({
                                'price': float(avg_price),
                                'type': 'EQUAL_HIGHS',
                                'touches': 2
                            })
            
            # Swing Lows и Equal Lows
            swing_lows = []
            for i in range(2, len(recent_df) - 2):
                if recent_df['low'].iloc[i] < recent_df['low'].iloc[i-1] and \
                   recent_df['low'].iloc[i] < recent_df['low'].iloc[i+1]:
                    swing_lows.append({'price': float(recent_df['low'].iloc[i]), 'index': i})
            
            for i in range(len(swing_lows) - 1):
                for j in range(i + 1, len(swing_lows)):
                    if abs(swing_lows[i]['price'] - swing_lows[j]['price']) < threshold:
                        avg_price = (swing_lows[i]['price'] + swing_lows[j]['price']) / 2
                        if not any(abs(eq['price'] - avg_price) < threshold for eq in equal_levels['eql']):
                            equal_levels['eql'].append({
                                'price': float(avg_price),
                                'type': 'EQUAL_LOWS',
                                'touches': 2
                            })
            
            equal_levels['eqh'] = equal_levels['eqh'][-3:]
            equal_levels['eql'] = equal_levels['eql'][-3:]
            
            logger.info(f"Equal levels: EQH={len(equal_levels['eqh'])}, EQL={len(equal_levels['eql'])}")
            
        except Exception as e:
            logger.error(f"Error detecting EQH/EQL: {e}")
        
        return equal_levels
    
    # ========================================================================
    # PREMIUM/DISCOUNT ZONES
    # ========================================================================
    
    def calculate_premium_discount_zones(self, df: pd.DataFrame, 
                                          swing_pivot_high: float = 0,
                                          swing_pivot_low: float = 0) -> Dict:
        """
        Расчёт Premium/Discount зон на основе Swing Pivot'ов
        """
        try:
            if len(df) < 10:
                return self._get_empty_zones()
            
            current_price = float(df['close'].iloc[-1])
            
            # Используем Swing Pivot'ы если доступны, иначе последние 50 баров
            if swing_pivot_high > 0 and swing_pivot_low > 0:
                range_high = swing_pivot_high
                range_low = swing_pivot_low
                range_source = "SWING_PIVOTS"
            else:
                recent = df.tail(50)
                range_high = float(recent['high'].max())
                range_low = float(recent['low'].min())
                range_source = "LAST_50_BARS"
            
            if range_high <= range_low:
                return self._get_empty_zones()
            
            range_size = range_high - range_low
            equilibrium = (range_high + range_low) / 2
            
            # Зоны
            premium_top = range_high
            premium_bottom = range_high - (range_size * 0.236)  # 23.6% от верха
            
            discount_bottom = range_low
            discount_top = range_low + (range_size * 0.236)  # 23.6% от низа
            
            equilibrium_top = equilibrium + (range_size * 0.05)
            equilibrium_bottom = equilibrium - (range_size * 0.05)
            
            # Определяем текущую зону
            if current_price >= premium_bottom:
                current_zone = "PREMIUM"
            elif current_price <= discount_top:
                current_zone = "DISCOUNT"
            else:
                current_zone = "EQUILIBRIUM"
            
            # Позиция в диапазоне (0-100%)
            position_pct = ((current_price - range_low) / range_size) * 100 if range_size > 0 else 50
            
            return {
                'premium': {'top': float(premium_top), 'bottom': float(premium_bottom)},
                'equilibrium': {'top': float(equilibrium_top), 'bottom': float(equilibrium_bottom), 'price': float(equilibrium)},
                'discount': {'top': float(discount_top), 'bottom': float(discount_bottom)},
                'current_zone': current_zone,
                'range_high': float(range_high),
                'range_low': float(range_low),
                'range_source': range_source,
                'position_in_range_pct': float(position_pct)
            }
            
        except Exception as e:
            logger.error(f"Error calculating zones: {e}")
            return self._get_empty_zones()
    
    # ========================================================================
    # ADVANCED DATA
    # ========================================================================
    
    def calculate_advanced_smc_data(self, df: pd.DataFrame, zones: Dict) -> Dict:
        """Расширенные SMC данные"""
        try:
            if len(df) < 10:
                return self._get_empty_advanced_data()
            
            dh, dl = self._calculate_dh_dl(df)
            pdh, pdl = self._calculate_pdh_pdl(df)
            
            return {
                "key_levels": {
                    "DH": float(dh),
                    "DL": float(dl),
                    "PDH": float(pdh),
                    "PDL": float(pdl),
                    "Equilibrium_Price": zones.get('equilibrium', {}).get('price', 0),
                    "Current_Zone": zones.get('current_zone', 'UNKNOWN')
                },
                "structure_points": {
                    "nearest_swing_high": zones.get('range_high', 0),
                    "nearest_swing_low": zones.get('range_low', 0)
                },
                "range": {
                    "high": zones.get('range_high', 0),
                    "low": zones.get('range_low', 0),
                    "size": zones.get('range_high', 0) - zones.get('range_low', 0),
                    "source": zones.get('range_source', 'UNKNOWN')
                },
                "zones": zones
            }
        except Exception as e:
            logger.error(f"Error in advanced data: {e}")
            return self._get_empty_advanced_data()
    
    # ========================================================================
    # HELPER METHODS
    # ========================================================================
    
    def _calculate_atr(self, df: pd.DataFrame, period: int = 14) -> float:
        """Расчёт ATR"""
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
    
    def _calculate_dh_dl(self, df: pd.DataFrame) -> Tuple[float, float]:
        """Daily High/Low"""
        try:
            if isinstance(df.index, pd.DatetimeIndex):
                today = df.index[-1].date()
                today_data = df[df.index.date == today]
                if len(today_data) > 0:
                    return float(today_data['high'].max()), float(today_data['low'].min())
            return float(df.tail(24)['high'].max()), float(df.tail(24)['low'].min())
        except:
            return float(df['high'].max()), float(df['low'].min())
    
    def _calculate_pdh_pdl(self, df: pd.DataFrame) -> Tuple[float, float]:
        """Previous Day High/Low"""
        try:
            if isinstance(df.index, pd.DatetimeIndex):
                last_date = df.index[-1].date()
                for days_back in range(1, 4):
                    prev_date = last_date - pd.Timedelta(days=days_back)
                    prev_data = df[df.index.date == prev_date]
                    if len(prev_data) > 0:
                        return float(prev_data['high'].max()), float(prev_data['low'].min())
            return float(df.tail(96)['high'].max()), float(df.tail(96)['low'].min())
        except:
            return float(df['high'].max()), float(df['low'].min())
    
    def _get_empty_zones(self) -> Dict:
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
    
    def _get_empty_advanced_data(self) -> Dict:
        return {
            "key_levels": {"DH": 0.0, "DL": 0.0, "PDH": 0.0, "PDL": 0.0, 
                          "Equilibrium_Price": 0.0, "Current_Zone": "UNKNOWN"},
            "structure_points": {"nearest_swing_high": 0.0, "nearest_swing_low": 0.0},
            "range": {"high": 0.0, "low": 0.0, "size": 0.0, "source": "NONE"},
            "zones": self._get_empty_zones()
        }
    
    def _get_empty_result(self) -> Dict:
        return sanitize_for_json({
            'order_blocks': [],
            'order_blocks_internal': [],
            'order_blocks_swing': [],
            'fvg': [],
            'liquidity': [],
            'choch': [],
            'bos': [],
            'internal_choch': [],
            'internal_bos': [],
            'swing_choch': [],
            'swing_bos': [],
            'all_choch': [],
            'all_bos': [],
            'all_internal_choch': [],
            'all_internal_bos': [],
            'all_swing_choch': [],
            'all_swing_bos': [],
            'eqh': [],
            'eql': [],
            'trend': 'NEUTRAL',
            'internal_trend': 'NEUTRAL',
            'internal_pivot_high': 0.0,
            'internal_pivot_low': 0.0,
            'swing_pivot_high': 0.0,
            'swing_pivot_low': 0.0,
            'advanced': self._get_empty_advanced_data(),
            'signals_count': 0
        })
    
    # ========================================================================
    # ГЛАВНЫЙ МЕТОД АНАЛИЗА
    # ========================================================================
    
    def analyze(self, df) -> Dict:
        """
        Полный SMC анализ v4.0 с Bar-by-Bar Replay
        
        Args:
            df: DataFrame или список OHLC данных
            
        Returns:
            Полный результат анализа со ВСЕМИ историческими событиями
        """
        try:
            # Преобразуем в DataFrame если нужно
            if isinstance(df, list):
                if not df:
                    return self._get_empty_result()
                df = pd.DataFrame(df)
            
            if not isinstance(df, pd.DataFrame):
                logger.error(f"Invalid data type: {type(df)}")
                return self._get_empty_result()
            
            required = ['open', 'high', 'low', 'close']
            if not all(col in df.columns for col in required):
                logger.error("Missing required columns")
                return self._get_empty_result()
            
            if len(df) < 20:
                logger.warning(f"Insufficient data: {len(df)} bars")
                return self._get_empty_result()
            
            self.analysis_count += 1
            current_price = float(df['close'].iloc[-1])
            
            logger.info(f"=== SMC Analysis #{self.analysis_count} | {len(df)} bars | Price: {current_price:.2f} ===")
            
            # ================================================================
            # 1. MARKET STRUCTURE (Bar-by-Bar Replay)
            # ================================================================
            market_structure = self.detect_market_structure(df)
            
            # ================================================================
            # 2. ORDER BLOCKS
            # ================================================================
            order_blocks = self.detect_order_blocks(df)
            
            # ================================================================
            # 3. FAIR VALUE GAPS
            # ================================================================
            fvg = self.detect_fvg(df)
            
            # ================================================================
            # 4. LIQUIDITY
            # ================================================================
            liquidity = self.detect_liquidity(df)
            
            # ================================================================
            # 5. EQUAL HIGHS/LOWS
            # ================================================================
            equal_levels = self.detect_equal_highs_lows(df)
            
            # ================================================================
            # 6. PREMIUM/DISCOUNT ZONES
            # ================================================================
            zones = self.calculate_premium_discount_zones(
                df,
                swing_pivot_high=market_structure.get('swing_pivot_high', 0),
                swing_pivot_low=market_structure.get('swing_pivot_low', 0)
            )
            
            # ================================================================
            # 7. ADVANCED DATA
            # ================================================================
            advanced = self.calculate_advanced_smc_data(df, zones)
            
            # ================================================================
            # СБОРКА РЕЗУЛЬТАТА
            # ================================================================
            all_order_blocks = order_blocks['internal'] + order_blocks['swing']
            
            # Объединяем свежие события для совместимости
            fresh_choch = market_structure['internal_choch'] + market_structure['swing_choch']
            fresh_bos = market_structure['internal_bos'] + market_structure['swing_bos']
            
            # Объединяем ВСЮ историю
            all_choch = market_structure['all_internal_choch'] + market_structure['all_swing_choch']
            all_bos = market_structure['all_internal_bos'] + market_structure['all_swing_bos']
            
            result = {
                # Order Blocks
                'order_blocks': all_order_blocks,
                'order_blocks_internal': order_blocks['internal'],
                'order_blocks_swing': order_blocks['swing'],
                
                # FVG & Liquidity
                'fvg': fvg,
                'liquidity': liquidity,
                
                # Свежие события (для бота)
                'choch': fresh_choch,
                'bos': fresh_bos,
                'internal_choch': market_structure['internal_choch'],
                'internal_bos': market_structure['internal_bos'],
                'swing_choch': market_structure['swing_choch'],
                'swing_bos': market_structure['swing_bos'],
                
                # ВСЯ ИСТОРИЯ (для графика)
                'all_choch': all_choch,
                'all_bos': all_bos,
                'all_internal_choch': market_structure['all_internal_choch'],
                'all_internal_bos': market_structure['all_internal_bos'],
                'all_swing_choch': market_structure['all_swing_choch'],
                'all_swing_bos': market_structure['all_swing_bos'],
                
                # Тренды
                'trend': market_structure['swing_trend'],
                'internal_trend': market_structure['internal_trend'],
                
                # Pivot уровни
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
            
            # Счётчик сигналов
            total = (len(all_order_blocks) + len(fvg) + len(liquidity) + 
                    len(all_choch) + len(all_bos) + 
                    len(equal_levels['eqh']) + len(equal_levels['eql']))
            
            result['signals_count'] = total
            
            logger.info(f"SMC Result: Signals={total} | "
                       f"Trend: I={market_structure['internal_trend']}, S={market_structure['swing_trend']} | "
                       f"Zone={zones['current_zone']} | "
                       f"OB:{len(all_order_blocks)} FVG:{len(fvg)} S/R:{len(liquidity)} "
                       f"CHoCH:{len(all_choch)} BOS:{len(all_bos)}")
            
            return sanitize_for_json(result)
            
        except Exception as e:
            logger.error(f"Error in SMC analysis: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return self._get_empty_result()


# Глобальный экземпляр
smc_detector = SMCDetector()


# ============================================================================
# ТЕСТ
# ============================================================================

if __name__ == "__main__":
    print("=" * 60)
    print("SMC Detector v4.0 - Bar-by-Bar Historical Replay")
    print("=" * 60)
    
    # Генерируем тестовые данные с трендом
    np.random.seed(42)
    n = 250
    
    # Симулируем XAUUSD с трендом
    base_price = 2650
    prices = [base_price]
    
    for i in range(1, n):
        # Добавляем тренд + шум
        trend = 0.1 if i < 100 else -0.15 if i < 180 else 0.2
        change = trend + np.random.randn() * 2
        prices.append(prices[-1] + change)
    
    # Создаём OHLC
    data = []
    for i, close in enumerate(prices):
        high = close + abs(np.random.randn()) * 3
        low = close - abs(np.random.randn()) * 3
        open_price = prices[i-1] if i > 0 else close
        data.append({
            'open': open_price,
            'high': high,
            'low': low,
            'close': close
        })
    
    df = pd.DataFrame(data)
    
    # Анализ
    result = smc_detector.analyze(df)
    
    print(f"\n📊 РЕЗУЛЬТАТ АНАЛИЗА:")
    print(f"   Текущая цена: ${df['close'].iloc[-1]:.2f}")
    print(f"   Internal Trend: {result['internal_trend']}")
    print(f"   Swing Trend: {result['trend']}")
    print(f"   Zone: {result['advanced']['key_levels']['Current_Zone']}")
    
    print(f"\n📍 PIVOT УРОВНИ:")
    print(f"   Internal PH: ${result['internal_pivot_high']:.2f}")
    print(f"   Internal PL: ${result['internal_pivot_low']:.2f}")
    print(f"   Swing PH: ${result['swing_pivot_high']:.2f}")
    print(f"   Swing PL: ${result['swing_pivot_low']:.2f}")
    
    print(f"\n📜 ВСЯ ИСТОРИЯ:")
    print(f"   Internal CHoCH: {len(result['all_internal_choch'])}")
    print(f"   Internal BOS: {len(result['all_internal_bos'])}")
    print(f"   Swing CHoCH: {len(result['all_swing_choch'])}")
    print(f"   Swing BOS: {len(result['all_swing_bos'])}")
    
    print(f"\n🔥 СВЕЖИЕ (последние {FRESH_SIGNAL_BARS} баров):")
    print(f"   CHoCH: {len(result['choch'])}")
    print(f"   BOS: {len(result['bos'])}")
    
    if result['all_internal_choch']:
        print(f"\n📌 Примеры Internal CHoCH:")
        for ch in result['all_internal_choch'][-3:]:
            print(f"   → {ch['type']} @ ${ch['price']:.2f} (bar {ch['bar_index']}, {ch['bars_ago']} bars ago)")
    
    print("\n✅ Тест пройден!")
