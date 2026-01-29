"""
SMC Detector v2.1 - ИСПРАВЛЕННАЯ ВЕРСИЯ
Детектор SMC уровней (Order Blocks, FVG, BOS, CHoCH, Support/Resistance)
Улучшенная математика на основе LuxAlgo Smart Money Concepts

ИСПРАВЛЕНИЯ v2.0:
1. Правильное определение Pivot High/Low с подтверждением справа
2. Корректная логика BOS/CHoCH с отслеживанием состояния
3. Оптимизированные параметры для XAUUSD M15
4. Улучшенная фильтрация шума

ИСПРАВЛЕНИЯ v2.1:
5. Пробитие BOS/CHoCH по HIGH/LOW (фитилям) вместо CLOSE
   - Для бычьего пробоя: high > pivot_high.price
   - Для медвежьего пробоя: low < pivot_low.price
   Это важно для XAUUSD, где фитили часто снимают ликвидность
   
6. Premium/Discount зоны на основе swing pivot'ов
   - Используем self.swing.pivot_high.price и self.swing.pivot_low.price
   - Fallback на последние 50 свечей (не 250!)
   - Более актуальные зоны для текущего дня
"""
import pandas as pd
import numpy as np
import logging
import json
from typing import Dict, List, Tuple, Optional, Any
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


def sanitize_for_json(obj: Any) -> Any:
    """
    Рекурсивно конвертирует numpy типы в стандартные Python типы
    для корректной JSON сериализации.
    
    numpy.bool_ -> bool
    numpy.int64 -> int
    numpy.float64 -> float
    numpy.ndarray -> list
    """
    if isinstance(obj, dict):
        return {k: sanitize_for_json(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [sanitize_for_json(item) for item in obj]
    elif isinstance(obj, tuple):
        return tuple(sanitize_for_json(item) for item in obj)
    elif isinstance(obj, (np.bool_, bool)):
        return bool(obj)
    elif isinstance(obj, (np.integer, int)):
        return int(obj)
    elif isinstance(obj, (np.floating, float)):
        return float(obj)
    elif isinstance(obj, np.ndarray):
        return [sanitize_for_json(item) for item in obj.tolist()]
    elif pd.isna(obj):
        return None
    else:
        return obj

# Константы
BULLISH = 1
BEARISH = -1
NEUTRAL = 0


@dataclass
class PivotPoint:
    """Структура для хранения pivot точки"""
    price: float = 0.0
    index: int = 0
    time: str = ""
    crossed: bool = False
    
    def update(self, price: float, index: int, time: str):
        """Обновление pivot с сбросом флага crossed"""
        if self.price != price:
            self.price = price
            self.index = index
            self.time = time
            self.crossed = False
            return True
        return False


@dataclass
class TrendState:
    """Структура для хранения состояния тренда"""
    bias: int = NEUTRAL
    last_bos_price: float = 0.0
    last_choch_price: float = 0.0
    
    def to_string(self) -> str:
        if self.bias == BULLISH:
            return "UPTREND"
        elif self.bias == BEARISH:
            return "DOWNTREND"
        return "NEUTRAL"


@dataclass
class StructureState:
    """Полное состояние структуры (Internal или Swing)"""
    pivot_high: PivotPoint = field(default_factory=PivotPoint)
    pivot_low: PivotPoint = field(default_factory=PivotPoint)
    trend: TrendState = field(default_factory=TrendState)
    
    # История pivot точек
    pivot_highs_history: List[Dict] = field(default_factory=list)
    pivot_lows_history: List[Dict] = field(default_factory=list)


class SMCDetector:
    """
    Детектор Smart Money Concepts уровней
    Версия 2.0 с исправленной логикой
    """
    
    def __init__(self):
        # Internal структура (краткосрочная)
        self.internal = StructureState()
        
        # Swing структура (долгосрочная)  
        self.swing = StructureState()
        
        # Кэш ATR
        self._cached_atr: float = 0.0
        self._cached_atr_len: int = 0
        
        # Счётчик вызовов для отладки
        self._call_count: int = 0
        
        logger.info("SMCDetector v2.0 initialized")
    
    def reset(self):
        """Полный сброс состояния детектора"""
        self.internal = StructureState()
        self.swing = StructureState()
        self._cached_atr = 0.0
        self._cached_atr_len = 0
        logger.info("SMCDetector state reset")
    
    # ==================== ВСПОМОГАТЕЛЬНЫЕ МЕТОДЫ ====================
    
    def _calculate_atr(self, df: pd.DataFrame, period: int = 14) -> float:
        """Расчет ATR с кэшированием"""
        try:
            if len(df) == self._cached_atr_len and self._cached_atr > 0:
                return self._cached_atr
            
            high = df['high']
            low = df['low']
            close = df['close']
            
            tr1 = high - low
            tr2 = abs(high - close.shift(1))
            tr3 = abs(low - close.shift(1))
            
            tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
            atr = tr.rolling(window=min(period, len(df))).mean().iloc[-1]
            
            self._cached_atr = float(atr) if not pd.isna(atr) else 0.0
            self._cached_atr_len = len(df)
            
            return self._cached_atr
        except Exception as e:
            logger.warning(f"ATR calculation error: {e}")
            return 0.0
    
    def _get_time_str(self, df: pd.DataFrame, index: int) -> str:
        """Безопасное получение времени"""
        try:
            if hasattr(df.index, 'strftime'):
                return str(df.index[index])
            elif hasattr(df.index[index], 'strftime'):
                return df.index[index].strftime('%Y-%m-%d %H:%M:%S')
            return str(index)
        except:
            return str(index)
    
    # ==================== PIVOT POINTS ====================
    
    def _find_pivot_points(self, df: pd.DataFrame, left_bars: int, right_bars: int) -> Tuple[List[Dict], List[Dict]]:
        """
        Правильное определение Pivot High/Low (как в Pine Script ta.pivothigh/ta.pivotlow)
        
        Pivot High на баре i существует, если:
        - high[i] > всех high на барах [i-left_bars, i-1]
        - high[i] >= всех high на барах [i+1, i+right_bars]
        
        Pivot Low на баре i существует, если:
        - low[i] < всех low на барах [i-left_bars, i-1]
        - low[i] <= всех low на барах [i+1, i+right_bars]
        """
        pivot_highs = []
        pivot_lows = []
        
        if len(df) < left_bars + right_bars + 1:
            logger.warning(f"Insufficient data for pivot detection: {len(df)} bars, need {left_bars + right_bars + 1}")
            return pivot_highs, pivot_lows
        
        highs = df['high'].values
        lows = df['low'].values
        
        # Проходим по барам, где есть достаточно данных слева и справа
        for i in range(left_bars, len(df) - right_bars):
            current_high = highs[i]
            current_low = lows[i]
            
            # === Pivot High ===
            # Проверяем левую сторону (строго больше)
            left_highs = highs[i - left_bars:i]
            is_pivot_high = current_high > np.max(left_highs) if len(left_highs) > 0 else False
            
            # Проверяем правую сторону (больше или равно)
            if is_pivot_high and right_bars > 0:
                right_highs = highs[i + 1:i + right_bars + 1]
                is_pivot_high = current_high >= np.max(right_highs) if len(right_highs) > 0 else False
            
            if is_pivot_high:
                pivot_highs.append({
                    'price': float(current_high),
                    'index': i,
                    'time': self._get_time_str(df, i),
                    'confirmed': True
                })
            
            # === Pivot Low ===
            left_lows = lows[i - left_bars:i]
            is_pivot_low = current_low < np.min(left_lows) if len(left_lows) > 0 else False
            
            if is_pivot_low and right_bars > 0:
                right_lows = lows[i + 1:i + right_bars + 1]
                is_pivot_low = current_low <= np.min(right_lows) if len(right_lows) > 0 else False
            
            if is_pivot_low:
                pivot_lows.append({
                    'price': float(current_low),
                    'index': i,
                    'time': self._get_time_str(df, i),
                    'confirmed': True
                })
        
        return pivot_highs, pivot_lows
    
    def _find_unconfirmed_pivots(self, df: pd.DataFrame, left_bars: int) -> Tuple[Optional[Dict], Optional[Dict]]:
        """
        Находит потенциальные (неподтверждённые) pivot точки на последних барах
        Эти точки ещё не имеют достаточно баров справа для подтверждения
        """
        if len(df) < left_bars + 1:
            return None, None
        
        highs = df['high'].values
        lows = df['low'].values
        
        unconfirmed_high = None
        unconfirmed_low = None
        
        # Ищем в последних left_bars барах
        for i in range(len(df) - 1, len(df) - left_bars - 1, -1):
            if i < left_bars:
                break
            
            current_high = highs[i]
            current_low = lows[i]
            
            # Проверяем только левую сторону
            left_highs = highs[i - left_bars:i]
            left_lows = lows[i - left_bars:i]
            
            if unconfirmed_high is None and current_high > np.max(left_highs):
                unconfirmed_high = {
                    'price': float(current_high),
                    'index': i,
                    'time': self._get_time_str(df, i),
                    'confirmed': False
                }
            
            if unconfirmed_low is None and current_low < np.min(left_lows):
                unconfirmed_low = {
                    'price': float(current_low),
                    'index': i,
                    'time': self._get_time_str(df, i),
                    'confirmed': False
                }
        
        return unconfirmed_high, unconfirmed_low
    
    # ==================== STRUCTURE BREAKS (BOS/CHoCH) ====================
    
    def _detect_structure_breaks(self, df: pd.DataFrame, pivot_highs: List[Dict], 
                                  pivot_lows: List[Dict], state: StructureState,
                                  is_internal: bool = False) -> Dict:
        """
        Определение BOS и CHoCH
        
        BOS (Break of Structure): Пробой в направлении текущего тренда
        CHoCH (Change of Character): Пробой против текущего тренда (разворот)
        
        ВАЖНО v2.1: Используем HIGH/LOW для определения пробоя, не CLOSE!
        - Бычий пробой: HIGH > pivot_high.price (фитиль вверх)
        - Медвежий пробой: LOW < pivot_low.price (фитиль вниз)
        
        Почему это важно для XAUUSD:
        - Фитили часто снимают ликвидность без закрытия тела за уровнем
        - Ждать закрытия свечи на M15 = опоздать на 15 минут
        - Институциональные игроки охотятся за стопами фитилями
        
        Returns:
            Dict с ключами 'bos' и 'choch', содержащими списки событий
        """
        breaks = {'bos': [], 'choch': []}
        structure_name = "Internal" if is_internal else "Swing"
        
        if not pivot_highs and not pivot_lows:
            logger.debug(f"{structure_name}: No pivot points for structure break detection")
            return breaks
        
        current_close = float(df['close'].iloc[-1])
        current_high = float(df['high'].iloc[-1])
        current_low = float(df['low'].iloc[-1])
        
        # Берём последний значимый pivot high
        if pivot_highs:
            last_pivot_high = pivot_highs[-1]
            
            # Проверяем, это новый уровень?
            if state.pivot_high.update(
                last_pivot_high['price'], 
                last_pivot_high['index'],
                last_pivot_high['time']
            ):
                logger.debug(f"{structure_name}: New Pivot High at {last_pivot_high['price']:.2f}")
        
        # Берём последний значимый pivot low
        if pivot_lows:
            last_pivot_low = pivot_lows[-1]
            
            if state.pivot_low.update(
                last_pivot_low['price'],
                last_pivot_low['index'],
                last_pivot_low['time']
            ):
                logger.debug(f"{structure_name}: New Pivot Low at {last_pivot_low['price']:.2f}")
        
        # === BULLISH BREAK (пробой pivot high вверх) ===
        # v2.1: Используем HIGH вместо CLOSE для раннего обнаружения
        if state.pivot_high.price > 0 and not state.pivot_high.crossed:
            # Пробой = HIGH выше уровня (фитиль пробил)
            if current_high > state.pivot_high.price:
                if state.trend.bias == BEARISH:
                    # CHoCH - смена тренда с медвежьего на бычий
                    event = {
                        'type': 'BULLISH_CHOCH',
                        'price': state.pivot_high.price,
                        'time': state.pivot_high.time,
                        'break_price': current_high,  # v2.1: фиксируем high, не close
                        'close_price': current_close,
                        'break_by_wick': current_close <= state.pivot_high.price,  # v2.1: флаг пробоя фитилём
                        'internal': is_internal
                    }
                    breaks['choch'].append(event)
                    state.trend.last_choch_price = state.pivot_high.price
                    wick_note = " (WICK)" if current_close <= state.pivot_high.price else ""
                    logger.info(f"🔄 {structure_name} BULLISH CHoCH at {state.pivot_high.price:.2f} (high: {current_high:.2f}){wick_note}")
                else:
                    # BOS - продолжение бычьего тренда
                    event = {
                        'type': 'BULLISH_BOS',
                        'price': state.pivot_high.price,
                        'time': state.pivot_high.time,
                        'break_price': current_high,
                        'close_price': current_close,
                        'break_by_wick': current_close <= state.pivot_high.price,
                        'internal': is_internal
                    }
                    breaks['bos'].append(event)
                    state.trend.last_bos_price = state.pivot_high.price
                    wick_note = " (WICK)" if current_close <= state.pivot_high.price else ""
                    logger.info(f"📈 {structure_name} BULLISH BOS at {state.pivot_high.price:.2f} (high: {current_high:.2f}){wick_note}")
                
                state.pivot_high.crossed = True
                state.trend.bias = BULLISH
        
        # === BEARISH BREAK (пробой pivot low вниз) ===
        # v2.1: Используем LOW вместо CLOSE для раннего обнаружения
        if state.pivot_low.price > 0 and not state.pivot_low.crossed:
            # Пробой = LOW ниже уровня (фитиль пробил)
            if current_low < state.pivot_low.price:
                if state.trend.bias == BULLISH:
                    # CHoCH - смена тренда с бычьего на медвежий
                    event = {
                        'type': 'BEARISH_CHOCH',
                        'price': state.pivot_low.price,
                        'time': state.pivot_low.time,
                        'break_price': current_low,  # v2.1: фиксируем low, не close
                        'close_price': current_close,
                        'break_by_wick': current_close >= state.pivot_low.price,
                        'internal': is_internal
                    }
                    breaks['choch'].append(event)
                    state.trend.last_choch_price = state.pivot_low.price
                    wick_note = " (WICK)" if current_close >= state.pivot_low.price else ""
                    logger.info(f"🔄 {structure_name} BEARISH CHoCH at {state.pivot_low.price:.2f} (low: {current_low:.2f}){wick_note}")
                else:
                    # BOS - продолжение медвежьего тренда
                    event = {
                        'type': 'BEARISH_BOS',
                        'price': state.pivot_low.price,
                        'time': state.pivot_low.time,
                        'break_price': current_low,
                        'close_price': current_close,
                        'break_by_wick': current_close >= state.pivot_low.price,
                        'internal': is_internal
                    }
                    breaks['bos'].append(event)
                    state.trend.last_bos_price = state.pivot_low.price
                    wick_note = " (WICK)" if current_close >= state.pivot_low.price else ""
                    logger.info(f"📉 {structure_name} BEARISH BOS at {state.pivot_low.price:.2f} (low: {current_low:.2f}){wick_note}")
                
                state.pivot_low.crossed = True
                state.trend.bias = BEARISH
        
        return breaks
    
    def detect_market_structure(self, df: pd.DataFrame, 
                                internal_left: int = 5, internal_right: int = 2,
                                swing_left: int = 10, swing_right: int = 5) -> Dict:
        """
        Определение рыночной структуры (CHoCH, BOS) для Internal и Swing
        
        Рекомендуемые параметры для XAUUSD M15:
        - internal_left: 5 (1.25 часа)
        - internal_right: 2 (30 мин) - для более быстрого подтверждения
        - swing_left: 10 (2.5 часа)
        - swing_right: 5 (1.25 часа)
        
        Args:
            df: DataFrame с OHLC данными
            internal_left: Количество баров слева для internal pivot
            internal_right: Количество баров справа для internal pivot
            swing_left: Количество баров слева для swing pivot
            swing_right: Количество баров справа для swing pivot
        
        Returns:
            Dict со структурой рынка
        """
        structure = {
            'internal_choch': [],
            'internal_bos': [],
            'swing_choch': [],
            'swing_bos': [],
            'internal_trend': 'NEUTRAL',
            'swing_trend': 'NEUTRAL',
            'internal_pivot_high': None,
            'internal_pivot_low': None,
            'swing_pivot_high': None,
            'swing_pivot_low': None,
            'debug': {}
        }
        
        min_bars = max(internal_left, swing_left) + max(internal_right, swing_right) + 5
        
        if len(df) < min_bars:
            logger.warning(f"Insufficient data: {len(df)} bars, need at least {min_bars}")
            return structure
        
        # ===== INTERNAL STRUCTURE =====
        int_highs, int_lows = self._find_pivot_points(df, internal_left, internal_right)
        
        # Добавляем неподтверждённые pivot для более быстрой реакции
        unconf_high, unconf_low = self._find_unconfirmed_pivots(df, internal_left)
        
        # Сохраняем историю
        self.internal.pivot_highs_history = int_highs
        self.internal.pivot_lows_history = int_lows
        
        logger.debug(f"Internal pivots: {len(int_highs)} highs, {len(int_lows)} lows")
        
        # Определяем breaks
        int_breaks = self._detect_structure_breaks(df, int_highs, int_lows, self.internal, is_internal=True)
        
        structure['internal_choch'] = int_breaks['choch']
        structure['internal_bos'] = int_breaks['bos']
        structure['internal_trend'] = self.internal.trend.to_string()
        
        if int_highs:
            structure['internal_pivot_high'] = int_highs[-1]
        if int_lows:
            structure['internal_pivot_low'] = int_lows[-1]
        
        # ===== SWING STRUCTURE =====
        sw_highs, sw_lows = self._find_pivot_points(df, swing_left, swing_right)
        
        self.swing.pivot_highs_history = sw_highs
        self.swing.pivot_lows_history = sw_lows
        
        logger.debug(f"Swing pivots: {len(sw_highs)} highs, {len(sw_lows)} lows")
        
        sw_breaks = self._detect_structure_breaks(df, sw_highs, sw_lows, self.swing, is_internal=False)
        
        structure['swing_choch'] = sw_breaks['choch']
        structure['swing_bos'] = sw_breaks['bos']
        structure['swing_trend'] = self.swing.trend.to_string()
        
        if sw_highs:
            structure['swing_pivot_high'] = sw_highs[-1]
        if sw_lows:
            structure['swing_pivot_low'] = sw_lows[-1]
        
        # Debug info
        structure['debug'] = {
            'internal_pivots_count': {'highs': len(int_highs), 'lows': len(int_lows)},
            'swing_pivots_count': {'highs': len(sw_highs), 'lows': len(sw_lows)},
            'current_price': float(df['close'].iloc[-1]),
            'internal_pivot_high_price': self.internal.pivot_high.price,
            'internal_pivot_low_price': self.internal.pivot_low.price,
            'swing_pivot_high_price': self.swing.pivot_high.price,
            'swing_pivot_low_price': self.swing.pivot_low.price
        }
        
        logger.info(f"Structure: Internal={structure['internal_trend']} "
                   f"(PH:{self.internal.pivot_high.price:.2f}, PL:{self.internal.pivot_low.price:.2f}), "
                   f"Swing={structure['swing_trend']} "
                   f"(PH:{self.swing.pivot_high.price:.2f}, PL:{self.swing.pivot_low.price:.2f})")
        
        return structure
    
    # ==================== ORDER BLOCKS ====================
    
    def _find_order_block_candle(self, df: pd.DataFrame, pivot_index: int, 
                                  direction: int, lookback: int = 10) -> Optional[Dict]:
        """
        Находит свечу Order Block рядом с pivot точкой
        
        Для BULLISH pivot (pivot low): Ищем последнюю медвежью свечу перед разворотом
        Для BEARISH pivot (pivot high): Ищем последнюю бычью свечу перед разворотом
        """
        try:
            start_idx = max(0, pivot_index - lookback)
            
            for i in range(pivot_index - 1, start_idx - 1, -1):
                if i < 0:
                    break
                
                candle_open = df['open'].iloc[i]
                candle_close = df['close'].iloc[i]
                candle_high = df['high'].iloc[i]
                candle_low = df['low'].iloc[i]
                
                is_bullish_candle = candle_close > candle_open
                is_bearish_candle = candle_close < candle_open
                
                # Для bullish OB ищем медвежью свечу
                if direction == BULLISH and is_bearish_candle:
                    return {
                        'type': 'BULL_OB',
                        'top': float(candle_high),
                        'bottom': float(candle_low),
                        'open': float(candle_open),
                        'close': float(candle_close),
                        'index': i,
                        'time': self._get_time_str(df, i),
                        'strength': float(candle_high - candle_low)
                    }
                
                # Для bearish OB ищем бычью свечу
                if direction == BEARISH and is_bullish_candle:
                    return {
                        'type': 'BEAR_OB',
                        'top': float(candle_high),
                        'bottom': float(candle_low),
                        'open': float(candle_open),
                        'close': float(candle_close),
                        'index': i,
                        'time': self._get_time_str(df, i),
                        'strength': float(candle_high - candle_low)
                    }
            
            return None
            
        except Exception as e:
            logger.warning(f"Error finding OB candle: {e}")
            return None
    
    def detect_order_blocks(self, df: pd.DataFrame, 
                           internal_left: int = 5, internal_right: int = 2,
                           swing_left: int = 10, swing_right: int = 5) -> Dict:
        """
        Детекция Order Blocks на основе pivot точек
        
        Returns:
            Dict с ключами 'internal' и 'swing'
        """
        order_blocks = {
            'internal': [],
            'swing': []
        }
        
        try:
            if len(df) < 20:
                return order_blocks
            
            current_price = float(df['close'].iloc[-1])
            atr = self._calculate_atr(df)
            
            # Internal Order Blocks
            int_highs, int_lows = self._find_pivot_points(df, internal_left, internal_right)
            
            for pivot in int_lows[-5:]:  # Bullish OB от pivot lows
                ob = self._find_order_block_candle(df, pivot['index'], BULLISH)
                if ob:
                    # Проверяем, что блок ещё "живой" (цена выше low блока)
                    if current_price >= ob['bottom'] - atr * 0.5:
                        ob['internal'] = True
                        ob['pivot_price'] = pivot['price']
                        order_blocks['internal'].append(ob)
            
            for pivot in int_highs[-5:]:  # Bearish OB от pivot highs
                ob = self._find_order_block_candle(df, pivot['index'], BEARISH)
                if ob:
                    if current_price <= ob['top'] + atr * 0.5:
                        ob['internal'] = True
                        ob['pivot_price'] = pivot['price']
                        order_blocks['internal'].append(ob)
            
            # Swing Order Blocks
            sw_highs, sw_lows = self._find_pivot_points(df, swing_left, swing_right)
            
            for pivot in sw_lows[-3:]:
                ob = self._find_order_block_candle(df, pivot['index'], BULLISH, lookback=15)
                if ob:
                    if current_price >= ob['bottom'] - atr:
                        ob['internal'] = False
                        ob['pivot_price'] = pivot['price']
                        order_blocks['swing'].append(ob)
            
            for pivot in sw_highs[-3:]:
                ob = self._find_order_block_candle(df, pivot['index'], BEARISH, lookback=15)
                if ob:
                    if current_price <= ob['top'] + atr:
                        ob['internal'] = False
                        ob['pivot_price'] = pivot['price']
                        order_blocks['swing'].append(ob)
            
            # Ограничиваем количество
            order_blocks['internal'] = order_blocks['internal'][-5:]
            order_blocks['swing'] = order_blocks['swing'][-3:]
            
            logger.info(f"Order Blocks: Internal={len(order_blocks['internal'])}, Swing={len(order_blocks['swing'])}")
            
        except Exception as e:
            logger.error(f"Error detecting order blocks: {e}")
        
        return order_blocks
    
    # ==================== FAIR VALUE GAP ====================
    
    def detect_fvg(self, df: pd.DataFrame, lookback: int = 50, min_gap_atr_ratio: float = 0.3) -> List[Dict]:
        """
        Детекция Fair Value Gaps
        
        FVG образуется когда есть gap между high свечи 1 и low свечи 3
        """
        fvg_list = []
        
        try:
            if len(df) < 3:
                return fvg_list
            
            recent_df = df.tail(lookback).reset_index(drop=True)
            current_price = float(df['close'].iloc[-1])
            atr = self._calculate_atr(df)
            min_gap = atr * min_gap_atr_ratio if atr > 0 else current_price * 0.0003
            
            for i in range(1, len(recent_df) - 1):
                candle1_high = recent_df['high'].iloc[i - 1]
                candle1_low = recent_df['low'].iloc[i - 1]
                candle2_open = recent_df['open'].iloc[i]
                candle2_close = recent_df['close'].iloc[i]
                candle3_high = recent_df['high'].iloc[i + 1]
                candle3_low = recent_df['low'].iloc[i + 1]
                
                # BULLISH FVG: low свечи 3 > high свечи 1
                if candle3_low > candle1_high:
                    gap_size = candle3_low - candle1_high
                    
                    if gap_size >= min_gap:
                        # Проверяем, что FVG ещё не заполнен
                        if current_price >= candle1_high:
                            fvg_list.append({
                                'type': 'BULL_FVG',
                                'top': float(candle3_low),
                                'bottom': float(candle1_high),
                                'price': float((candle3_low + candle1_high) / 2),
                                'gap_size': float(gap_size),
                                'index': i,
                                'filled': current_price <= candle1_high
                            })
                
                # BEARISH FVG: high свечи 3 < low свечи 1
                elif candle3_high < candle1_low:
                    gap_size = candle1_low - candle3_high
                    
                    if gap_size >= min_gap:
                        if current_price <= candle1_low:
                            fvg_list.append({
                                'type': 'BEAR_FVG',
                                'top': float(candle1_low),
                                'bottom': float(candle3_high),
                                'price': float((candle1_low + candle3_high) / 2),
                                'gap_size': float(gap_size),
                                'index': i,
                                'filled': current_price >= candle1_low
                            })
            
            # Сортируем по размеру gap и берём топ-5
            fvg_list = sorted(fvg_list, key=lambda x: x['gap_size'], reverse=True)[:5]
            
            logger.info(f"FVG detected: {len(fvg_list)}")
            
        except Exception as e:
            logger.error(f"Error detecting FVG: {e}")
        
        return fvg_list
    
    # ==================== EQUAL HIGHS/LOWS ====================
    
    def detect_equal_highs_lows(self, df: pd.DataFrame, lookback: int = 50,
                                bars_confirmation: int = 3,
                                threshold_atr_ratio: float = 0.1) -> Dict:
        """
        Детекция Equal Highs/Lows (двойные/тройные вершины/донья)
        """
        equal_levels = {
            'eqh': [],
            'eql': []
        }
        
        try:
            if len(df) < 20:
                return equal_levels
            
            atr = self._calculate_atr(df)
            threshold = atr * threshold_atr_ratio if atr > 0 else df['close'].iloc[-1] * 0.001
            
            recent_df = df.tail(lookback).reset_index(drop=True)
            
            # Находим локальные максимумы
            local_highs = []
            for i in range(bars_confirmation, len(recent_df) - bars_confirmation):
                current = recent_df['high'].iloc[i]
                
                is_local_max = True
                for j in range(1, bars_confirmation + 1):
                    if current <= recent_df['high'].iloc[i - j] or current <= recent_df['high'].iloc[i + j]:
                        is_local_max = False
                        break
                
                if is_local_max:
                    local_highs.append({
                        'price': float(current),
                        'index': i
                    })
            
            # Находим локальные минимумы
            local_lows = []
            for i in range(bars_confirmation, len(recent_df) - bars_confirmation):
                current = recent_df['low'].iloc[i]
                
                is_local_min = True
                for j in range(1, bars_confirmation + 1):
                    if current >= recent_df['low'].iloc[i - j] or current >= recent_df['low'].iloc[i + j]:
                        is_local_min = False
                        break
                
                if is_local_min:
                    local_lows.append({
                        'price': float(current),
                        'index': i
                    })
            
            # Ищем Equal Highs
            for i in range(len(local_highs)):
                for j in range(i + 1, len(local_highs)):
                    if abs(local_highs[i]['price'] - local_highs[j]['price']) < threshold:
                        avg_price = (local_highs[i]['price'] + local_highs[j]['price']) / 2
                        
                        # Проверяем дубликаты
                        is_dup = any(abs(eq['price'] - avg_price) < threshold for eq in equal_levels['eqh'])
                        
                        if not is_dup:
                            equal_levels['eqh'].append({
                                'price': float(avg_price),
                                'type': 'EQUAL_HIGHS',
                                'touches': 2
                            })
            
            # Ищем Equal Lows
            for i in range(len(local_lows)):
                for j in range(i + 1, len(local_lows)):
                    if abs(local_lows[i]['price'] - local_lows[j]['price']) < threshold:
                        avg_price = (local_lows[i]['price'] + local_lows[j]['price']) / 2
                        
                        is_dup = any(abs(eq['price'] - avg_price) < threshold for eq in equal_levels['eql'])
                        
                        if not is_dup:
                            equal_levels['eql'].append({
                                'price': float(avg_price),
                                'type': 'EQUAL_LOWS',
                                'touches': 2
                            })
            
            equal_levels['eqh'] = equal_levels['eqh'][-3:]
            equal_levels['eql'] = equal_levels['eql'][-3:]
            
            logger.info(f"Equal levels: EQH={len(equal_levels['eqh'])}, EQL={len(equal_levels['eql'])}")
            
        except Exception as e:
            logger.error(f"Error detecting equal levels: {e}")
        
        return equal_levels
    
    # ==================== LIQUIDITY / SUPPORT-RESISTANCE ====================
    
    def detect_liquidity(self, df: pd.DataFrame, lookback: int = 100) -> List[Dict]:
        """
        Определение значимых уровней ликвидности (Support/Resistance)
        """
        liquidity = []
        
        try:
            if len(df) < 20:
                return liquidity
            
            recent_df = df.tail(lookback)
            atr = self._calculate_atr(df)
            
            highs = recent_df['high'].values
            lows = recent_df['low'].values
            
            # Находим swing highs (минимум 3 свечи с каждой стороны)
            swing_highs = []
            swing_lows = []
            
            for i in range(3, len(recent_df) - 3):
                # Swing High
                is_swing_high = True
                for j in range(1, 4):
                    if highs[i] <= highs[i - j] or highs[i] <= highs[i + j]:
                        is_swing_high = False
                        break
                
                if is_swing_high:
                    swing_highs.append({'price': float(highs[i]), 'index': i})
                
                # Swing Low
                is_swing_low = True
                for j in range(1, 4):
                    if lows[i] >= lows[i - j] or lows[i] >= lows[i + j]:
                        is_swing_low = False
                        break
                
                if is_swing_low:
                    swing_lows.append({'price': float(lows[i]), 'index': i})
            
            # Кластеризация уровней
            def cluster_levels(levels, threshold_ratio=0.002):
                if not levels:
                    return []
                
                sorted_levels = sorted(levels, key=lambda x: x['price'])
                clusters = []
                current_cluster = [sorted_levels[0]]
                
                for level in sorted_levels[1:]:
                    cluster_avg = sum(l['price'] for l in current_cluster) / len(current_cluster)
                    
                    if abs(level['price'] - cluster_avg) / cluster_avg < threshold_ratio:
                        current_cluster.append(level)
                    else:
                        avg_price = sum(l['price'] for l in current_cluster) / len(current_cluster)
                        clusters.append({
                            'price': avg_price,
                            'strength': len(current_cluster)
                        })
                        current_cluster = [level]
                
                if current_cluster:
                    avg_price = sum(l['price'] for l in current_cluster) / len(current_cluster)
                    clusters.append({
                        'price': avg_price,
                        'strength': len(current_cluster)
                    })
                
                return clusters
            
            high_clusters = cluster_levels(swing_highs)
            low_clusters = cluster_levels(swing_lows)
            
            for cluster in high_clusters:
                if cluster['strength'] >= 2:
                    liquidity.append({
                        'type': 'RESISTANCE',
                        'price': float(cluster['price']),
                        'strength': cluster['strength']
                    })
            
            for cluster in low_clusters:
                if cluster['strength'] >= 2:
                    liquidity.append({
                        'type': 'SUPPORT',
                        'price': float(cluster['price']),
                        'strength': cluster['strength']
                    })
            
            # Топ-4 по силе
            liquidity = sorted(liquidity, key=lambda x: x['strength'], reverse=True)[:4]
            
            logger.info(f"Liquidity levels: {len(liquidity)}")
            
        except Exception as e:
            logger.error(f"Error detecting liquidity: {e}")
        
        return liquidity
    
    # ==================== PREMIUM/DISCOUNT ZONES ====================
    
    def calculate_premium_discount_zones(self, df: pd.DataFrame, lookback: int = 50) -> Dict:
        """
        Расчёт Premium/Discount зон v2.1
        
        Premium: Верхняя 1/3 диапазона (зона продаж)
        Discount: Нижняя 1/3 диапазона (зона покупок)
        Equilibrium: Средняя 1/3 (50% диапазона)
        
        ВАЖНО v2.1: Используем swing pivot'ы для определения диапазона
        - Приоритет: self.swing.pivot_high.price / self.swing.pivot_low.price
        - Fallback: экстремумы последних 50 свечей (НЕ 250!)
        
        Почему это важно:
        - Если считать по 250 свечам, вчерашний лой тянет эквилибриум вниз
        - Цена может казаться в середине, хотя она уже перекуплена
        - Swing pivot'ы дают актуальный структурный диапазон
        """
        try:
            if len(df) < 10:
                return self._get_empty_zones()
            
            current_price = float(df['close'].iloc[-1])
            
            # ===== v2.1: Определяем диапазон на основе SWING PIVOT'ов =====
            swing_high_price = self.swing.pivot_high.price
            swing_low_price = self.swing.pivot_low.price
            
            # Проверяем, что swing pivot'ы валидны (не равны 0)
            use_swing_pivots = swing_high_price > 0 and swing_low_price > 0
            
            if use_swing_pivots:
                range_high = swing_high_price
                range_low = swing_low_price
                range_source = "SWING_PIVOTS"
                logger.debug(f"Zones using SWING PIVOTS: High={range_high:.2f}, Low={range_low:.2f}")
            else:
                # Fallback: используем последние 50 свечей (не весь датафрейм!)
                recent_df = df.tail(min(lookback, 50))  # v2.1: максимум 50 свечей
                range_high = float(recent_df['high'].max())
                range_low = float(recent_df['low'].min())
                range_source = "LAST_50_BARS"
                logger.debug(f"Zones using LAST 50 BARS: High={range_high:.2f}, Low={range_low:.2f}")
            
            range_size = range_high - range_low
            
            if range_size <= 0:
                logger.warning(f"Invalid range size: {range_size}")
                return self._get_empty_zones()
            
            # Equilibrium (50%)
            equilibrium = (range_high + range_low) / 2
            
            # Premium zone (верхняя треть, выше 66.6%)
            premium_bottom = range_low + range_size * 0.666
            premium_top = range_high
            
            # Discount zone (нижняя треть, ниже 33.3%)
            discount_top = range_low + range_size * 0.333
            discount_bottom = range_low
            
            # Equilibrium zone (средняя треть, 33.3% - 66.6%)
            eq_bottom = range_low + range_size * 0.333
            eq_top = range_low + range_size * 0.666
            
            # Определяем текущую зону
            if current_price >= premium_bottom:
                current_zone = "PREMIUM"
            elif current_price <= discount_top:
                current_zone = "DISCOUNT"
            else:
                current_zone = "EQUILIBRIUM"
            
            # Рассчитываем позицию в диапазоне (0% = low, 100% = high)
            position_in_range = ((current_price - range_low) / range_size) * 100 if range_size > 0 else 50.0
            
            zones = {
                'premium': {
                    'top': premium_top,
                    'bottom': premium_bottom
                },
                'equilibrium': {
                    'top': eq_top,
                    'bottom': eq_bottom,
                    'price': equilibrium
                },
                'discount': {
                    'top': discount_top,
                    'bottom': discount_bottom
                },
                'current_zone': current_zone,
                'range_high': range_high,
                'range_low': range_low,
                'range_size': range_size,
                'range_source': range_source,  # v2.1: откуда взят диапазон
                'position_in_range_pct': round(position_in_range, 1)  # v2.1: позиция в %
            }
            
            logger.debug(f"Zones ({range_source}): {current_zone} ({position_in_range:.1f}%), EQ={equilibrium:.2f}")
            
            return zones
            
        except Exception as e:
            logger.error(f"Error calculating zones: {e}")
            return self._get_empty_zones()
    
    # ==================== ADVANCED DATA ====================
    
    def calculate_advanced_smc_data(self, df: pd.DataFrame) -> Dict:
        """
        Расширенные SMC данные (PDH/PDL, Swings, Equilibrium)
        """
        try:
            if len(df) < 10:
                return self._get_empty_advanced_data()
            
            # Daily High/Low
            dh, dl = self._calculate_dh_dl(df)
            
            # Previous Day High/Low
            pdh, pdl = self._calculate_pdh_pdl(df)
            
            # Zones
            zones = self.calculate_premium_discount_zones(df)
            
            # Swing points
            swing_highs = [p['price'] for p in self.swing.pivot_highs_history[-5:]]
            swing_lows = [p['price'] for p in self.swing.pivot_lows_history[-5:]]
            
            advanced_data = {
                "key_levels": {
                    "DH": float(dh),
                    "DL": float(dl),
                    "PDH": float(pdh),
                    "PDL": float(pdl),
                    "Equilibrium_Price": float(zones['equilibrium']['price']),
                    "Current_Zone": zones['current_zone']
                },
                "structure_points": {
                    "nearest_swing_high": float(swing_highs[-1]) if swing_highs else float(dh),
                    "nearest_swing_low": float(swing_lows[-1]) if swing_lows else float(dl),
                    "all_swing_highs": swing_highs,
                    "all_swing_lows": swing_lows
                },
                "range": {
                    "high": zones['range_high'],
                    "low": zones['range_low'],
                    "size": zones['range_size']
                },
                "zones": zones
            }
            
            return advanced_data
            
        except Exception as e:
            logger.error(f"Error calculating advanced SMC: {e}")
            return self._get_empty_advanced_data()
    
    def _calculate_dh_dl(self, df: pd.DataFrame) -> Tuple[float, float]:
        """Daily High/Low"""
        try:
            if isinstance(df.index, pd.DatetimeIndex):
                last_date = df.index[-1].date()
                today_data = df[df.index.date == last_date]
                
                if len(today_data) > 0:
                    return float(today_data['high'].max()), float(today_data['low'].min())
            
            # Fallback: последние 24 бара (на M15 = 6 часов)
            return float(df['high'].tail(24).max()), float(df['low'].tail(24).min())
            
        except Exception as e:
            logger.warning(f"Error in DH/DL: {e}")
            return float(df['high'].max()), float(df['low'].min())
    
    def _calculate_pdh_pdl(self, df: pd.DataFrame) -> Tuple[float, float]:
        """Previous Day High/Low"""
        try:
            if isinstance(df.index, pd.DatetimeIndex):
                last_date = df.index[-1]
                
                for days_back in range(1, 4):
                    prev_date = last_date - pd.Timedelta(days=days_back)
                    prev_data = df[df.index.date == prev_date.date()]
                    
                    if len(prev_data) > 0:
                        return float(prev_data['high'].max()), float(prev_data['low'].min())
            
            # Fallback
            return float(df['high'].tail(96).max()), float(df['low'].tail(96).min())
            
        except Exception as e:
            logger.warning(f"Error in PDH/PDL: {e}")
            return float(df['high'].max()), float(df['low'].min())
    
    # ==================== EMPTY STRUCTURES ====================
    
    def _get_empty_zones(self) -> Dict:
        return {
            'premium': {'top': 0.0, 'bottom': 0.0},
            'equilibrium': {'top': 0.0, 'bottom': 0.0, 'price': 0.0},
            'discount': {'top': 0.0, 'bottom': 0.0},
            'current_zone': 'UNKNOWN',
            'range_high': 0.0,
            'range_low': 0.0,
            'range_size': 0.0,
            'range_source': 'NONE',
            'position_in_range_pct': 50.0
        }
    
    def _get_empty_advanced_data(self) -> Dict:
        return {
            "key_levels": {
                "DH": 0.0, "DL": 0.0, "PDH": 0.0, "PDL": 0.0,
                "Equilibrium_Price": 0.0, "Current_Zone": "UNKNOWN"
            },
            "structure_points": {
                "nearest_swing_high": 0.0, "nearest_swing_low": 0.0,
                "all_swing_highs": [], "all_swing_lows": []
            },
            "range": {"high": 0.0, "low": 0.0, "size": 0.0},
            "zones": self._get_empty_zones()
        }
    
    def _get_empty_result(self) -> Dict:
        return {
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
            'eqh': [],
            'eql': [],
            'trend': 'NEUTRAL',
            'internal_trend': 'NEUTRAL',
            'advanced': self._get_empty_advanced_data(),
            'signals_count': 0,
            'debug': {}
        }
    
    # ==================== MAIN ANALYZE METHOD ====================
    
    def analyze(self, df, 
                internal_left: int = 5, internal_right: int = 2,
                swing_left: int = 10, swing_right: int = 5) -> Dict:
        """
        Полный SMC анализ
        
        Параметры для XAUUSD M15:
        - internal_left: 5 (1.25 часа слева)
        - internal_right: 2 (30 мин справа для быстрого подтверждения)
        - swing_left: 10 (2.5 часа слева)
        - swing_right: 5 (1.25 часа справа)
        
        Args:
            df: DataFrame или список словарей с OHLC
            internal_left: Бары слева для internal pivot
            internal_right: Бары справа для internal pivot
            swing_left: Бары слева для swing pivot
            swing_right: Бары справа для swing pivot
        """
        self._call_count += 1
        
        try:
            # Преобразуем list в DataFrame
            if isinstance(df, list):
                if not df:
                    logger.warning("Empty data list")
                    return self._get_empty_result()
                df = pd.DataFrame(df)
            
            if not isinstance(df, pd.DataFrame):
                logger.error(f"Invalid data type: {type(df)}")
                return self._get_empty_result()
            
            # Проверяем колонки
            required = ['open', 'high', 'low', 'close']
            missing = [c for c in required if c not in df.columns]
            
            if missing:
                logger.error(f"Missing columns: {missing}")
                return self._get_empty_result()
            
            # Минимум данных
            min_bars = max(internal_left, swing_left) + max(internal_right, swing_right) + 10
            
            if len(df) < min_bars:
                logger.warning(f"Insufficient data: {len(df)} bars, need {min_bars}")
                return self._get_empty_result()
            
            logger.info(f"=== SMC Analysis #{self._call_count} | {len(df)} bars | "
                       f"Price: {df['close'].iloc[-1]:.2f} ===")
            
            # 1. Market Structure (BOS/CHoCH)
            market_structure = self.detect_market_structure(
                df, internal_left, internal_right, swing_left, swing_right
            )
            
            # 2. Order Blocks
            order_blocks = self.detect_order_blocks(
                df, internal_left, internal_right, swing_left, swing_right
            )
            
            # 3. Fair Value Gaps
            fvg = self.detect_fvg(df)
            
            # 4. Liquidity (S/R)
            liquidity = self.detect_liquidity(df)
            
            # 5. Equal Highs/Lows
            equal_levels = self.detect_equal_highs_lows(df)
            
            # 6. Advanced Data
            advanced_data = self.calculate_advanced_smc_data(df)
            
            # Объединяем результаты
            all_order_blocks = order_blocks['internal'] + order_blocks['swing']
            all_choch = market_structure['internal_choch'] + market_structure['swing_choch']
            all_bos = market_structure['internal_bos'] + market_structure['swing_bos']
            
            smc_data = {
                'order_blocks': all_order_blocks,
                'order_blocks_internal': order_blocks['internal'],
                'order_blocks_swing': order_blocks['swing'],
                'fvg': fvg,
                'liquidity': liquidity,
                'choch': all_choch,
                'bos': all_bos,
                'internal_choch': market_structure['internal_choch'],
                'internal_bos': market_structure['internal_bos'],
                'swing_choch': market_structure['swing_choch'],
                'swing_bos': market_structure['swing_bos'],
                'trend': market_structure['swing_trend'],
                'internal_trend': market_structure['internal_trend'],
                'eqh': equal_levels['eqh'],
                'eql': equal_levels['eql'],
                'advanced': advanced_data,
                'pivot_high_internal': market_structure.get('internal_pivot_high'),
                'pivot_low_internal': market_structure.get('internal_pivot_low'),
                'pivot_high_swing': market_structure.get('swing_pivot_high'),
                'pivot_low_swing': market_structure.get('swing_pivot_low'),
                'debug': market_structure.get('debug', {})
            }
            
            total_signals = (len(all_order_blocks) + len(fvg) + len(liquidity) +
                           len(all_choch) + len(all_bos) +
                           len(equal_levels['eqh']) + len(equal_levels['eql']))
            
            smc_data['signals_count'] = total_signals
            
            logger.info(f"SMC Result: Signals={total_signals} | "
                       f"Trend: I={market_structure['internal_trend']}, S={market_structure['swing_trend']} | "
                       f"Zone={advanced_data['key_levels']['Current_Zone']} | "
                       f"OB:{len(all_order_blocks)} FVG:{len(fvg)} S/R:{len(liquidity)} "
                       f"CHoCH:{len(all_choch)} BOS:{len(all_bos)}")
            
            # 🔧 КРИТИЧНО v2.1.1: Конвертируем numpy типы в стандартные Python типы
            # Это НЕОБХОДИМО для корректной JSON сериализации в Flask API
            # Без этого Flask выдаёт: "Object of type bool is not JSON serializable"
            return sanitize_for_json(smc_data)
            
        except Exception as e:
            logger.error(f"SMC Analysis error: {e}")
            import traceback
            logger.error(traceback.format_exc())
            # Санитизируем даже пустой результат на всякий случай
            return sanitize_for_json(self._get_empty_result())


# Глобальный экземпляр
smc_detector = SMCDetector()


# ==================== ТЕСТОВЫЙ КОД ====================

def test_detector():
    """
    Тест детектора на синтетических данных v2.1
    """
    import random
    
    # Генерируем тестовые данные (симуляция XAUUSD)
    np.random.seed(42)
    n_bars = 200
    
    # Базовая цена
    base_price = 2650.0
    prices = [base_price]
    
    # Генерируем случайное движение с трендом
    for i in range(n_bars - 1):
        # Добавляем тренд + шум
        if i < 50:
            trend = 0.5  # Рост
        elif i < 100:
            trend = -0.3  # Падение
        elif i < 150:
            trend = 0.4  # Рост
        else:
            trend = -0.2  # Падение
        
        change = trend + np.random.normal(0, 2)
        prices.append(prices[-1] + change)
    
    # Создаём OHLC с выраженными фитилями для теста v2.1
    data = []
    for i, close in enumerate(prices):
        # Делаем длинные фитили для теста пробоя по high/low
        wick_size = abs(np.random.normal(0, 3))  # Увеличенные фитили
        body_size = abs(np.random.normal(0, 1))
        
        if np.random.random() > 0.5:
            # Бычья свеча с длинным верхним фитилём
            open_price = close - body_size
            high = close + wick_size
            low = open_price - abs(np.random.normal(0, 0.5))
        else:
            # Медвежья свеча с длинным нижним фитилём
            open_price = close + body_size
            high = open_price + abs(np.random.normal(0, 0.5))
            low = close - wick_size
        
        data.append({
            'open': open_price,
            'high': high,
            'low': low,
            'close': close
        })
    
    df = pd.DataFrame(data)
    df.index = pd.date_range(start='2024-01-01', periods=len(df), freq='15min')
    
    # Тестируем
    detector = SMCDetector()
    
    print("\n" + "="*70)
    print("SMC DETECTOR v2.1 TEST")
    print("="*70)
    print("\nНовые функции v2.1:")
    print("  ✅ Пробой BOS/CHoCH по HIGH/LOW (фитилям)")
    print("  ✅ Premium/Discount на основе Swing Pivot'ов")
    print("="*70)
    
    result = detector.analyze(df)
    
    print(f"\n📊 Тренды:")
    print(f"  Internal Trend: {result['internal_trend']}")
    print(f"  Swing Trend: {result['swing_trend']}")
    
    # v2.1: Показываем информацию о зонах
    zones = result['advanced']['zones']
    print(f"\n🎯 Зоны (v2.1):")
    print(f"  Источник диапазона: {zones.get('range_source', 'N/A')}")
    print(f"  Range High: {zones['range_high']:.2f}")
    print(f"  Range Low: {zones['range_low']:.2f}")
    print(f"  Equilibrium: {zones['equilibrium']['price']:.2f}")
    print(f"  Текущая зона: {zones['current_zone']}")
    print(f"  Позиция в диапазоне: {zones.get('position_in_range_pct', 50):.1f}%")
    
    print(f"\n📈 Сигналы:")
    print(f"  CHoCH: {len(result['choch'])} (Internal: {len(result['internal_choch'])}, Swing: {len(result['swing_choch'])})")
    print(f"  BOS: {len(result['bos'])} (Internal: {len(result['internal_bos'])}, Swing: {len(result['swing_bos'])})")
    print(f"  Order Blocks: {len(result['order_blocks'])}")
    print(f"  FVG: {len(result['fvg'])}")
    print(f"  Liquidity: {len(result['liquidity'])}")
    print(f"  EQH: {len(result['eqh'])}, EQL: {len(result['eql'])}")
    
    print(f"\n🔍 Pivot точки:")
    print(f"  Internal Pivot High: {result['debug'].get('internal_pivot_high_price', 0):.2f}")
    print(f"  Internal Pivot Low: {result['debug'].get('internal_pivot_low_price', 0):.2f}")
    print(f"  Swing Pivot High: {result['debug'].get('swing_pivot_high_price', 0):.2f}")
    print(f"  Swing Pivot Low: {result['debug'].get('swing_pivot_low_price', 0):.2f}")
    
    # v2.1: Показываем детали пробоев с информацией о фитилях
    if result['choch']:
        print(f"\n🔄 CHoCH (пробои разворота):")
        for ch in result['choch']:
            wick_note = " [WICK]" if ch.get('break_by_wick', False) else " [BODY]"
            print(f"  - {ch['type']} at {ch['price']:.2f}, break by {ch.get('break_price', 0):.2f}{wick_note}")
    
    if result['bos']:
        print(f"\n📈 BOS (пробои структуры):")
        for b in result['bos']:
            wick_note = " [WICK]" if b.get('break_by_wick', False) else " [BODY]"
            print(f"  - {b['type']} at {b['price']:.2f}, break by {b.get('break_price', 0):.2f}{wick_note}")
    
    print("\n" + "="*70)
    print("TEST COMPLETE - v2.1 with WICK detection and SWING-based zones")
    print("="*70)


if __name__ == "__main__":
    # Настраиваем логирование
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s | %(levelname)s | %(message)s'
    )
    
    test_detector()
