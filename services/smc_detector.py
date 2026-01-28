"""
Детектор SMC уровней (Order Blocks, FVG, Support/Resistance)
Улучшенная математика и логика на основе LuxAlgo Smart Money Concepts
"""
import pandas as pd
import numpy as np
import logging
from typing import Dict, List, Tuple, Optional
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# Константы
BULLISH_LEG = 1
BEARISH_LEG = 0
BULLISH = 1
BEARISH = -1
NEUTRAL = 0


@dataclass
class Pivot:
    """Структура для хранения pivot точек"""
    current_level: float = 0.0
    last_level: float = 0.0
    crossed: bool = False
    bar_time: Optional[str] = None
    bar_index: int = 0


@dataclass
class Trend:
    """Структура для хранения тренда"""
    bias: int = NEUTRAL


@dataclass
class TrailingExtremes:
    """Структура для отслеживания trailing экстремумов"""
    top: float = 0.0
    bottom: float = 0.0
    bar_time: Optional[str] = None
    bar_index: int = 0
    last_top_time: Optional[str] = None
    last_bottom_time: Optional[str] = None


class SMCDetector:
    """Детектор Smart Money Concepts уровней"""
    
    def __init__(self):
        # Internal структуры (краткосрочные)
        self.internal_high = Pivot()
        self.internal_low = Pivot()
        self.internal_trend = Trend()
        
        # Swing структуры (долгосрочные)
        self.swing_high = Pivot()
        self.swing_low = Pivot()
        self.swing_trend = Trend()
        
        # Equal Highs/Lows
        self.equal_high = Pivot()
        self.equal_low = Pivot()
        
        # Trailing extremes
        self.trailing = TrailingExtremes()
        
        # Хранилища
        self.parsed_highs: List[float] = []
        self.parsed_lows: List[float] = []
        self.highs: List[float] = []
        self.lows: List[float] = []
        self.times: List[str] = []
        
        # Order Blocks
        self.swing_order_blocks: List[Dict] = []
        self.internal_order_blocks: List[Dict] = []
    
    def reset(self):
        """
        Сброс всех флагов и состояния детектора
        Полезно для начала нового цикла анализа
        """
        # Сброс Internal структур
        self.internal_high.crossed = False
        self.internal_high.current_level = 0.0
        self.internal_low.crossed = False
        self.internal_low.current_level = 0.0
        self.internal_trend.bias = NEUTRAL
        
        # Сброс Swing структур
        self.swing_high.crossed = False
        self.swing_high.current_level = 0.0
        self.swing_low.crossed = False
        self.swing_low.current_level = 0.0
        self.swing_trend.bias = NEUTRAL
        
        logger.debug("SMC Detector state reset")
    
    def _calculate_atr(self, df: pd.DataFrame, period: int = 200) -> float:
        """Расчет ATR для фильтрации"""
        try:
            high = df['high']
            low = df['low']
            close = df['close']
            
            # True Range
            tr1 = high - low
            tr2 = abs(high - close.shift(1))
            tr3 = abs(low - close.shift(1))
            
            tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
            atr = tr.rolling(window=min(period, len(df))).mean().iloc[-1]
            
            return float(atr) if not pd.isna(atr) else 0.0
        except Exception as e:
            logger.warning(f"Error calculating ATR: {e}")
            return 0.0
    
    def _calculate_cumulative_mean_range(self, df: pd.DataFrame) -> float:
        """Расчет cumulative mean range"""
        try:
            tr = df['high'] - df['low']
            cmr = tr.expanding().mean().iloc[-1]
            return float(cmr) if not pd.isna(cmr) else 0.0
        except Exception as e:
            logger.warning(f"Error calculating CMR: {e}")
            return 0.0
    
    def _get_leg(self, df: pd.DataFrame, size: int) -> List[int]:
        """
        Определение legs (ног) - механизм из Pine Script
        Leg = 0 (BEARISH) или 1 (BULLISH)
        """
        legs = []
        leg = 0
        
        for i in range(size, len(df)):
            # Проверяем, является ли текущий high новым maximum за последние size баров
            new_leg_high = df['high'].iloc[i - size] > df['high'].iloc[i - size - 1:i].max()
            
            # Проверяем, является ли текущий low новым minimum за последние size баров
            new_leg_low = df['low'].iloc[i - size] < df['low'].iloc[i - size - 1:i].min()
            
            if new_leg_high:
                leg = BEARISH_LEG
            elif new_leg_low:
                leg = BULLISH_LEG
            
            legs.append(leg)
        
        return legs
    
    def _detect_leg_changes(self, legs: List[int]) -> List[Tuple[int, bool, bool]]:
        """
        Определение изменений в legs
        Returns: List[(index, is_pivot_high, is_pivot_low)]
        """
        changes = []
        
        for i in range(1, len(legs)):
            change = legs[i] - legs[i - 1]
            
            if change == -1:  # Start of bearish leg (был bullish -> стал bearish)
                changes.append((i, True, False))  # Pivot High
            elif change == 1:  # Start of bullish leg (был bearish -> стал bullish)
                changes.append((i, False, True))  # Pivot Low
        
        return changes
    
    def _prepare_parsed_data(self, df: pd.DataFrame, atr: float, filter_type: str = 'atr') -> Tuple[List[float], List[float]]:
        """
        Подготовка parsed highs/lows с фильтрацией высоковолатильных баров
        Логика из Pine Script
        """
        parsed_highs = []
        parsed_lows = []
        
        # Volatility measure
        if filter_type == 'atr':
            volatility_measure = atr
        else:
            volatility_measure = self._calculate_cumulative_mean_range(df)
        
        for i in range(len(df)):
            high = df['high'].iloc[i]
            low = df['low'].iloc[i]
            bar_range = high - low
            
            # High volatility bar фильтр
            high_volatility_bar = bar_range >= (2 * volatility_measure)
            
            if high_volatility_bar:
                # Если бар высоковолатильный, используем консервативные значения
                parsed_highs.append(low)
                parsed_lows.append(high)
            else:
                parsed_highs.append(high)
                parsed_lows.append(low)
        
        return parsed_highs, parsed_lows
    
    def _get_current_structure(self, df: pd.DataFrame, size: int, internal: bool = False) -> Dict:
        """
        Определение текущей структуры (Internal или Swing)
        На основе legs из Pine Script
        """
        structure = {
            'pivot_highs': [],
            'pivot_lows': [],
            'legs': []
        }
        
        try:
            if len(df) < size + 5:
                return structure
            
            # Получаем legs
            legs = self._get_leg(df, size)
            leg_changes = self._detect_leg_changes(legs)
            
            # Определяем pivot точки
            for idx, is_high, is_low in leg_changes:
                actual_idx = idx + size  # Корректировка индекса
                
                if actual_idx >= len(df):
                    continue
                
                if is_high:
                    structure['pivot_highs'].append({
                        'price': float(df['high'].iloc[actual_idx]),
                        'index': actual_idx,
                        'time': str(df.index[actual_idx]) if hasattr(df.index[actual_idx], 'strftime') else str(actual_idx)
                    })
                
                if is_low:
                    structure['pivot_lows'].append({
                        'price': float(df['low'].iloc[actual_idx]),
                        'index': actual_idx,
                        'time': str(df.index[actual_idx]) if hasattr(df.index[actual_idx], 'strftime') else str(actual_idx)
                    })
            
            structure['legs'] = legs
            
        except Exception as e:
            logger.error(f"Error in get_current_structure: {e}")
        
        return structure
    
    def _detect_structure_breaks(self, df: pd.DataFrame, structure: Dict, internal: bool = False) -> Dict:
        """
        Определение BOS и CHOCH через crossover/crossunder
        Логика из Pine Script с исправлением сброса флагов
        """
        breaks = {
            'bos': [],
            'choch': []
        }
        
        try:
            if not structure['pivot_highs'] or not structure['pivot_lows']:
                return breaks
            
            current_price = float(df['close'].iloc[-1])
            
            # Получаем последние pivot точки
            pivot_high = structure['pivot_highs'][-1] if structure['pivot_highs'] else None
            pivot_low = structure['pivot_lows'][-1] if structure['pivot_lows'] else None
            
            # Выбираем тренд
            trend = self.internal_trend if internal else self.swing_trend
            pivot_h = self.internal_high if internal else self.swing_high
            pivot_l = self.internal_low if internal else self.swing_low
            
            # 🔧 ИСПРАВЛЕНИЕ: Сброс флага crossed при появлении нового pivot уровня
            if pivot_high and pivot_h.current_level != pivot_high['price']:
                pivot_h.current_level = pivot_high['price']
                pivot_h.crossed = False
                logger.debug(f"New {'Internal' if internal else 'Swing'} Pivot High: {pivot_high['price']:.2f} (reset crossed flag)")
            
            if pivot_low and pivot_l.current_level != pivot_low['price']:
                pivot_l.current_level = pivot_low['price']
                pivot_l.crossed = False
                logger.debug(f"New {'Internal' if internal else 'Swing'} Pivot Low: {pivot_low['price']:.2f} (reset crossed flag)")
            
            # Bullish structure break (crossover pivot high)
            if pivot_high and current_price > pivot_high['price'] and not pivot_h.crossed:
                tag = 'CHOCH' if trend.bias == BEARISH else 'BOS'
                
                breaks['choch' if tag == 'CHOCH' else 'bos'].append({
                    'type': f'BULLISH_{tag}',
                    'price': pivot_high['price'],
                    'time': pivot_high['time'],
                    'internal': internal
                })
                
                pivot_h.crossed = True
                trend.bias = BULLISH
                logger.info(f"{'Internal' if internal else 'Swing'} BULLISH {tag} detected at {pivot_high['price']:.2f}")
            
            # Bearish structure break (crossunder pivot low)
            if pivot_low and current_price < pivot_low['price'] and not pivot_l.crossed:
                tag = 'CHOCH' if trend.bias == BULLISH else 'BOS'
                
                breaks['choch' if tag == 'CHOCH' else 'bos'].append({
                    'type': f'BEARISH_{tag}',
                    'price': pivot_low['price'],
                    'time': pivot_low['time'],
                    'internal': internal
                })
                
                pivot_l.crossed = True
                trend.bias = BEARISH
                logger.info(f"{'Internal' if internal else 'Swing'} BEARISH {tag} detected at {pivot_low['price']:.2f}")
        
        except Exception as e:
            logger.error(f"Error detecting structure breaks: {e}")
        
        return breaks
    
    def _store_order_block(self, df: pd.DataFrame, pivot_info: Dict, bias: int, 
                          parsed_highs: List[float], parsed_lows: List[float], 
                          internal: bool = False) -> Optional[Dict]:
        """
        Сохранение Order Block с использованием parsed данных
        Логика из Pine Script
        """
        try:
            pivot_index = pivot_info['index']
            
            # Находим экстремум в диапазоне от pivot до текущей свечи
            if bias == BEARISH:
                # Ищем максимум в parsed_highs
                slice_data = parsed_highs[pivot_index:min(pivot_index + 20, len(parsed_highs))]
                if not slice_data:
                    return None
                max_val = max(slice_data)
                parsed_index = pivot_index + slice_data.index(max_val)
            else:
                # Ищем минимум в parsed_lows
                slice_data = parsed_lows[pivot_index:min(pivot_index + 20, len(parsed_lows))]
                if not slice_data:
                    return None
                min_val = min(slice_data)
                parsed_index = pivot_index + slice_data.index(min_val)
            
            if parsed_index >= len(df):
                return None
            
            order_block = {
                'type': 'BEAR_OB' if bias == BEARISH else 'BULL_OB',
                'top': float(df['high'].iloc[parsed_index]),
                'bottom': float(df['low'].iloc[parsed_index]),
                'time': str(df.index[parsed_index]) if hasattr(df.index[parsed_index], 'strftime') else str(parsed_index),
                'bias': bias,
                'internal': internal,
                'strength': float(df['high'].iloc[parsed_index] - df['low'].iloc[parsed_index])
            }
            
            return order_block
            
        except Exception as e:
            logger.error(f"Error storing order block: {e}")
            return None
    
    def detect_order_blocks(self, df: pd.DataFrame, lookback: int = 50, 
                           internal_size: int = 3, swing_size: int = 50) -> Dict:
        """
        Детекция Order Blocks (Internal и Swing) с логикой Pine Script
        
        Args:
            internal_size: Размер для Internal OB (3 бара = 45 мин на M15, оптимально для XAUUSD)
            swing_size: Размер для Swing OB (50 баров = 12.5 часов)
        """
        order_blocks = {
            'internal': [],
            'swing': []
        }
        
        try:
            if len(df) < 10:
                return order_blocks
            
            # ATR для фильтрации
            atr = self._calculate_atr(df)
            
            # Подготовка parsed данных
            parsed_highs, parsed_lows = self._prepare_parsed_data(df, atr)
            
            # Текущая цена для фильтрации "живых" блоков
            current_price = df['close'].iloc[-1]
            
            # Internal Order Blocks (size=3, оптимально для XAUUSD M15)
            internal_structure = self._get_current_structure(df, internal_size, internal=True)
            
            for pivot_high in internal_structure['pivot_highs'][-3:]:
                ob = self._store_order_block(df, pivot_high, BULLISH, parsed_highs, parsed_lows, internal=True)
                if ob and current_price >= ob['bottom']:  # Проверка "живой" блок
                    order_blocks['internal'].append(ob)
            
            for pivot_low in internal_structure['pivot_lows'][-3:]:
                ob = self._store_order_block(df, pivot_low, BEARISH, parsed_highs, parsed_lows, internal=True)
                if ob and current_price <= ob['top']:  # Проверка "живой" блок
                    order_blocks['internal'].append(ob)
            
            # Swing Order Blocks (size=50)
            swing_structure = self._get_current_structure(df, swing_size, internal=False)
            
            for pivot_high in swing_structure['pivot_highs'][-3:]:
                ob = self._store_order_block(df, pivot_high, BULLISH, parsed_highs, parsed_lows, internal=False)
                if ob and current_price >= ob['bottom']:
                    order_blocks['swing'].append(ob)
            
            for pivot_low in swing_structure['pivot_lows'][-3:]:
                ob = self._store_order_block(df, pivot_low, BEARISH, parsed_highs, parsed_lows, internal=False)
                if ob and current_price <= ob['top']:
                    order_blocks['swing'].append(ob)
            
            # Ограничиваем количество
            order_blocks['internal'] = order_blocks['internal'][-5:]
            order_blocks['swing'] = order_blocks['swing'][-5:]
            
            logger.info(f"Order Blocks detected: Internal={len(order_blocks['internal'])}, Swing={len(order_blocks['swing'])}")
            
        except Exception as e:
            logger.error(f"Error detecting order blocks: {e}")
        
        return order_blocks
    
    def detect_fvg(self, df: pd.DataFrame, lookback: int = 50, auto_threshold: bool = True) -> List[Dict]:
        """
        Детекция Fair Value Gaps с ATR фильтрацией
        """
        fvg_list = []
        
        try:
            if len(df) < 3:
                return fvg_list
            
            recent_df = df.tail(lookback).reset_index(drop=True)
            current_price = df['close'].iloc[-1]
            
            # ATR для порога
            atr = self._calculate_atr(df)
            min_gap = atr * 0.5 if auto_threshold and atr > 0 else current_price * 0.0005
            
            for i in range(1, len(recent_df) - 1):
                candle1 = recent_df.iloc[i - 1]
                candle2 = recent_df.iloc[i]  # Импульсная свеча
                candle3 = recent_df.iloc[i + 1]
                
                # BULLISH FVG
                if candle3['low'] > candle1['high']:
                    gap_size = candle3['low'] - candle1['high']
                    
                    if gap_size > min_gap and current_price >= candle1['high']:
                        fvg_list.append({
                            'type': 'BULL_FVG',
                            'top': float(candle3['low']),
                            'bottom': float(candle1['high']),
                            'price': float((candle3['low'] + candle1['high']) / 2),
                            'gap_size': float(gap_size)
                        })
                
                # BEARISH FVG
                elif candle3['high'] < candle1['low']:
                    gap_size = candle1['low'] - candle3['high']
                    
                    if gap_size > min_gap and current_price <= candle1['low']:
                        fvg_list.append({
                            'type': 'BEAR_FVG',
                            'top': float(candle1['low']),
                            'bottom': float(candle3['high']),
                            'price': float((candle1['low'] + candle3['high']) / 2),
                            'gap_size': float(gap_size)
                        })
            
            return fvg_list[-3:]
            
        except Exception as e:
            logger.error(f"Error detecting FVG: {e}")
            return []
    
    def detect_equal_highs_lows(self, df: pd.DataFrame, lookback: int = 50, 
                               bars_confirmation: int = 3) -> Dict:
        """
        Детекция Equal Highs/Lows с ATR порогом (из Pine Script)
        """
        equal_levels = {
            'eqh': [],
            'eql': []
        }
        
        try:
            if len(df) < 10:
                return equal_levels
            
            # ATR для порога
            atr = self._calculate_atr(df)
            threshold = atr * 0.1 if atr > 0 else df['close'].iloc[-1] * 0.001
            
            recent_df = df.tail(lookback).reset_index(drop=True)
            
            # Находим локальные максимумы
            highs = []
            for i in range(bars_confirmation, len(recent_df) - bars_confirmation):
                is_high = True
                for j in range(1, bars_confirmation + 1):
                    if recent_df['high'].iloc[i] <= recent_df['high'].iloc[i - j] or \
                       recent_df['high'].iloc[i] <= recent_df['high'].iloc[i + j]:
                        is_high = False
                        break
                
                if is_high:
                    highs.append({
                        'price': float(recent_df['high'].iloc[i]),
                        'index': i,
                        'time': str(df.index[-(len(recent_df) - i)])
                    })
            
            # Находим локальные минимумы
            lows = []
            for i in range(bars_confirmation, len(recent_df) - bars_confirmation):
                is_low = True
                for j in range(1, bars_confirmation + 1):
                    if recent_df['low'].iloc[i] >= recent_df['low'].iloc[i - j] or \
                       recent_df['low'].iloc[i] >= recent_df['low'].iloc[i + j]:
                        is_low = False
                        break
                
                if is_low:
                    lows.append({
                        'price': float(recent_df['low'].iloc[i]),
                        'index': i,
                        'time': str(df.index[-(len(recent_df) - i)])
                    })
            
            # Equal Highs
            for i in range(len(highs) - 1):
                for j in range(i + 1, len(highs)):
                    if abs(highs[i]['price'] - highs[j]['price']) < threshold:
                        avg_price = (highs[i]['price'] + highs[j]['price']) / 2
                        
                        # Проверка дубликатов
                        is_duplicate = any(abs(eq['price'] - avg_price) < threshold for eq in equal_levels['eqh'])
                        
                        if not is_duplicate:
                            equal_levels['eqh'].append({
                                'price': float(avg_price),
                                'time1': highs[i]['time'],
                                'time2': highs[j]['time'],
                                'touches': 2,
                                'type': 'EQUAL_HIGHS'
                            })
            
            # Equal Lows
            for i in range(len(lows) - 1):
                for j in range(i + 1, len(lows)):
                    if abs(lows[i]['price'] - lows[j]['price']) < threshold:
                        avg_price = (lows[i]['price'] + lows[j]['price']) / 2
                        
                        is_duplicate = any(abs(eq['price'] - avg_price) < threshold for eq in equal_levels['eql'])
                        
                        if not is_duplicate:
                            equal_levels['eql'].append({
                                'price': float(avg_price),
                                'time1': lows[i]['time'],
                                'time2': lows[j]['time'],
                                'touches': 2,
                                'type': 'EQUAL_LOWS'
                            })
            
            equal_levels['eqh'] = equal_levels['eqh'][-3:]
            equal_levels['eql'] = equal_levels['eql'][-3:]
            
            logger.info(f"Equal Levels detected: EQH={len(equal_levels['eqh'])}, EQL={len(equal_levels['eql'])}")
            
        except Exception as e:
            logger.error(f"Error detecting EQH/EQL: {e}")
        
        return equal_levels
    
    def detect_market_structure(self, df: pd.DataFrame, internal_size: int = 3, 
                               swing_size: int = 50) -> Dict:
        """
        Определение структуры рынка (CHOCH, BOS) для Internal и Swing
        С механизмом legs из Pine Script
        
        Args:
            internal_size: Размер окна для Internal структуры (3 бара = 45 мин на M15)
            swing_size: Размер окна для Swing структуры (50 баров = 12.5 часов на M15)
        """
        structure = {
            'internal_choch': [],
            'internal_bos': [],
            'swing_choch': [],
            'swing_bos': [],
            'internal_trend': 'NEUTRAL',
            'swing_trend': 'NEUTRAL'
        }
        
        try:
            if len(df) < 10:
                return structure
            
            # Internal структура (size=3, быстрое обнаружение для XAUUSD)
            internal_struct = self._get_current_structure(df, internal_size, internal=True)
            internal_breaks = self._detect_structure_breaks(df, internal_struct, internal=True)
            
            structure['internal_choch'] = internal_breaks['choch']
            structure['internal_bos'] = internal_breaks['bos']
            
            if self.internal_trend.bias == BULLISH:
                structure['internal_trend'] = 'UPTREND'
            elif self.internal_trend.bias == BEARISH:
                structure['internal_trend'] = 'DOWNTREND'
            
            # Swing структура (size=50)
            swing_struct = self._get_current_structure(df, swing_size, internal=False)
            swing_breaks = self._detect_structure_breaks(df, swing_struct, internal=False)
            
            structure['swing_choch'] = swing_breaks['choch']
            structure['swing_bos'] = swing_breaks['bos']
            
            if self.swing_trend.bias == BULLISH:
                structure['swing_trend'] = 'UPTREND'
            elif self.swing_trend.bias == BEARISH:
                structure['swing_trend'] = 'DOWNTREND'
            
            logger.info(f"Market Structure: Internal={structure['internal_trend']}, "
                       f"Swing={structure['swing_trend']}, "
                       f"I-CHOCH={len(structure['internal_choch'])}, "
                       f"S-CHOCH={len(structure['swing_choch'])}")
            
        except Exception as e:
            logger.error(f"Error detecting market structure: {e}")
        
        return structure
    
    def detect_liquidity(self, df: pd.DataFrame, lookback: int = 100) -> List[Dict]:
        """
        Определение значимых Support/Resistance уровней
        С кластеризацией и учетом "силы" уровня
        """
        liquidity = []
        
        try:
            if len(df) < 10:
                return liquidity
            
            recent_df = df.tail(lookback)
            
            highs = recent_df['high'].values
            lows = recent_df['low'].values
            
            swing_highs = []
            swing_lows = []
            
            # Находим swing highs (минимум 3 свечи с каждой стороны)
            for i in range(3, len(recent_df) - 3):
                is_swing_high = True
                for j in range(1, 4):
                    if highs[i] <= highs[i-j] or highs[i] <= highs[i+j]:
                        is_swing_high = False
                        break
                
                if is_swing_high:
                    swing_highs.append({
                        'price': float(highs[i]),
                        'index': i,
                        'time': recent_df.index[i]
                    })
            
            # Находим swing lows (минимум 3 свечи с каждой стороны)
            for i in range(3, len(recent_df) - 3):
                is_swing_low = True
                for j in range(1, 4):
                    if lows[i] >= lows[i-j] or lows[i] >= lows[i+j]:
                        is_swing_low = False
                        break
                
                if is_swing_low:
                    swing_lows.append({
                        'price': float(lows[i]),
                        'index': i,
                        'time': recent_df.index[i]
                    })
            
            # Функция кластеризации близких уровней
            def cluster_levels(levels, threshold=0.001):
                """Группируем уровни в пределах threshold (0.1%)"""
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
                            'price': avg_price,
                            'strength': len(current_cluster),
                            'latest_time': max(l['time'] for l in current_cluster)
                        })
                        current_cluster = [level]
                
                if current_cluster:
                    avg_price = sum(l['price'] for l in current_cluster) / len(current_cluster)
                    clusters.append({
                        'price': avg_price,
                        'strength': len(current_cluster),
                        'latest_time': max(l['time'] for l in current_cluster)
                    })
                
                return clusters
            
            # Кластеризуем уровни
            high_clusters = cluster_levels(swing_highs)
            low_clusters = cluster_levels(swing_lows)
            
            # Формируем итоговые уровни
            for cluster in high_clusters:
                if cluster['strength'] >= 2 or cluster['price'] >= np.max(highs) * 0.998:
                    liquidity.append({
                        'type': 'RESISTANCE',
                        'price': float(cluster['price']),
                        'strength': cluster['strength']
                    })
            
            for cluster in low_clusters:
                if cluster['strength'] >= 2 or cluster['price'] <= np.min(lows) * 1.002:
                    liquidity.append({
                        'type': 'SUPPORT',
                        'price': float(cluster['price']),
                        'strength': cluster['strength']
                    })
            
            # Сортируем по силе и берем топ-4
            liquidity = sorted(liquidity, key=lambda x: x['strength'], reverse=True)[:4]
            
            logger.info(f"Detected {len(liquidity)} significant S/R levels")
            
        except Exception as e:
            logger.error(f"Error detecting Liquidity: {str(e)}")
        
        return liquidity
    
    def calculate_premium_discount_zones(self, df: pd.DataFrame, lookback: int = 50) -> Dict:
        """
        Расчет Premium/Discount зон на основе trailing extremes
        Логика из Pine Script
        """
        try:
            if len(df) < 10:
                return self._get_empty_zones()
            
            # Обновляем trailing extremes
            recent_df = df.tail(lookback)
            
            trailing_high = recent_df['high'].max()
            trailing_low = recent_df['low'].min()
            
            equilibrium = (trailing_high + trailing_low) / 2
            current_price = df['close'].iloc[-1]
            
            # Premium зона (верхняя часть диапазона)
            premium_top = trailing_high
            premium_bottom = 0.95 * trailing_high + 0.05 * trailing_low
            
            # Discount зона (нижняя часть диапазона)
            discount_bottom = trailing_low
            discount_top = 0.95 * trailing_low + 0.05 * trailing_high
            
            # Equilibrium зона (середина)
            equilibrium_top = 0.525 * trailing_high + 0.475 * trailing_low
            equilibrium_bottom = 0.525 * trailing_low + 0.475 * trailing_high
            
            # Определяем текущую зону
            if current_price >= premium_bottom:
                current_zone = "PREMIUM"
            elif current_price <= discount_top:
                current_zone = "DISCOUNT"
            else:
                current_zone = "EQUILIBRIUM"
            
            zones = {
                'premium': {
                    'top': float(premium_top),
                    'bottom': float(premium_bottom)
                },
                'equilibrium': {
                    'top': float(equilibrium_top),
                    'bottom': float(equilibrium_bottom),
                    'price': float(equilibrium)
                },
                'discount': {
                    'top': float(discount_top),
                    'bottom': float(discount_bottom)
                },
                'current_zone': current_zone,
                'range_high': float(trailing_high),
                'range_low': float(trailing_low)
            }
            
            logger.info(f"Zones calculated: Current={current_zone}, EQ={equilibrium:.2f}")
            
            return zones
            
        except Exception as e:
            logger.error(f"Error calculating zones: {e}")
            return self._get_empty_zones()
    
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
            
            # Structural Swings
            swing_highs, swing_lows = self._calculate_structural_swings(df)
            
            # Premium/Discount зоны
            zones = self.calculate_premium_discount_zones(df)
            
            last_structural_high = swing_highs[-1] if swing_highs else pdh
            last_structural_low = swing_lows[-1] if swing_lows else pdl
            
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
                    "nearest_swing_high": float(last_structural_high),
                    "nearest_swing_low": float(last_structural_low),
                    "all_swing_highs": [float(h) for h in swing_highs[-5:]],
                    "all_swing_lows": [float(l) for l in swing_lows[-5:]]
                },
                "range": {
                    "high": float(zones['range_high']),
                    "low": float(zones['range_low']),
                    "size": float(zones['range_high'] - zones['range_low'])
                },
                "zones": zones
            }
            
            logger.info(f"Advanced SMC: DH={dh:.2f}, DL={dl:.2f}, "
                       f"PDH={pdh:.2f}, PDL={pdl:.2f}, "
                       f"Zone={zones['current_zone']}")
            
            return advanced_data
            
        except Exception as e:
            logger.error(f"Error calculating advanced SMC: {e}")
            return self._get_empty_advanced_data()
    
    def _calculate_pdh_pdl(self, df: pd.DataFrame) -> tuple:
        """Расчет Previous Day High/Low"""
        try:
            if not isinstance(df.index, pd.DatetimeIndex):
                pdh = df['high'].tail(50).max()
                pdl = df['low'].tail(50).min()
                return pdh, pdl
            
            last_date = df.index[-1]
            
            for days_back in range(1, 4):
                prev_date = last_date - pd.Timedelta(days=days_back)
                mask = df.index.date == prev_date.date()
                prev_day_data = df[mask]
                
                if len(prev_day_data) > 0:
                    pdh = prev_day_data['high'].max()
                    pdl = prev_day_data['low'].min()
                    return pdh, pdl
            
            pdh = df['high'].tail(50).max()
            pdl = df['low'].tail(50).min()
            return pdh, pdl
            
        except Exception as e:
            logger.warning(f"Error in PDH/PDL: {e}")
            return df['high'].max(), df['low'].min()
    
    def _calculate_dh_dl(self, df: pd.DataFrame) -> tuple:
        """Расчет Daily High/Low"""
        try:
            if not isinstance(df.index, pd.DatetimeIndex):
                dh = df['high'].tail(24).max()
                dl = df['low'].tail(24).min()
                return dh, dl
            
            last_date = df.index[-1].date()
            mask = df.index.date == last_date
            today_data = df[mask]
            
            if len(today_data) > 0:
                dh = today_data['high'].max()
                dl = today_data['low'].min()
                return dh, dl
            
            dh = df['high'].tail(24).max()
            dl = df['low'].tail(24).min()
            return dh, dl
            
        except Exception as e:
            logger.warning(f"Error in DH/DL: {e}")
            return df['high'].tail(24).max(), df['low'].tail(24).min()
    
    def _calculate_structural_swings(self, df: pd.DataFrame) -> tuple:
        """Находит фрактальные свинги"""
        swing_highs = []
        swing_lows = []
        
        try:
            for i in range(2, len(df) - 2):
                current_high = df['high'].iloc[i]
                current_low = df['low'].iloc[i]
                
                if (df['high'].iloc[i-1] < current_high > df['high'].iloc[i+1]) and \
                   (df['high'].iloc[i-2] < current_high > df['high'].iloc[i+2]):
                    swing_highs.append(current_high)
                
                if (df['low'].iloc[i-1] > current_low < df['low'].iloc[i+1]) and \
                   (df['low'].iloc[i-2] > current_low < df['low'].iloc[i+2]):
                    swing_lows.append(current_low)
            
            if not swing_highs:
                swing_highs = [df['high'].tail(20).max()]
            if not swing_lows:
                swing_lows = [df['low'].tail(20).min()]
                
        except Exception as e:
            logger.warning(f"Error in swings: {e}")
            swing_highs = [df['high'].max()]
            swing_lows = [df['low'].min()]
        
        return swing_highs, swing_lows
    
    def _get_empty_zones(self) -> Dict:
        """Пустая структура зон"""
        return {
            'premium': {'top': 0.0, 'bottom': 0.0},
            'equilibrium': {'top': 0.0, 'bottom': 0.0, 'price': 0.0},
            'discount': {'top': 0.0, 'bottom': 0.0},
            'current_zone': 'UNKNOWN',
            'range_high': 0.0,
            'range_low': 0.0
        }
    
    def _get_empty_advanced_data(self) -> Dict:
        """Пустая структура advanced data"""
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
        """Полная пустая структура результата для ошибок"""
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
            'signals_count': 0
        }
    
    def analyze(self, df) -> Dict:
        """
        Полный SMC анализ с улучшенной логикой из Pine Script
        
        Args:
            df: DataFrame или список словарей с OHLC данными
        """
        try:
            # Преобразуем list в DataFrame, если необходимо
            if isinstance(df, list):
                if not df:
                    return self._get_empty_result()
                df = pd.DataFrame(df)
            
            # Проверяем, что это DataFrame
            if not isinstance(df, pd.DataFrame):
                logger.error(f"Invalid data type: {type(df)}")
                return self._get_empty_result()
            
            # Проверяем наличие необходимых колонок
            required_columns = ['open', 'high', 'low', 'close']
            missing_columns = [col for col in required_columns if col not in df.columns]
            
            if missing_columns:
                logger.error(f"Missing required columns: {missing_columns}")
                return self._get_empty_result()
            
            # Проверяем минимальное количество данных
            if len(df) < 10:
                logger.warning("Insufficient data for SMC analysis")
                return self._get_empty_result()
            # Order Blocks (Internal + Swing)
            order_blocks_data = self.detect_order_blocks(df)
            
            # Fair Value Gaps
            fvg = self.detect_fvg(df)
            
            # Liquidity (S/R)
            liquidity = self.detect_liquidity(df)
            
            # Market Structure (CHOCH, BOS)
            market_structure = self.detect_market_structure(df)
            
            # Equal Highs/Lows
            equal_levels = self.detect_equal_highs_lows(df)
            
            # Advanced Data
            advanced_data = self.calculate_advanced_smc_data(df)
            
            # Объединяем Order Blocks для обратной совместимости
            all_order_blocks = order_blocks_data['internal'] + order_blocks_data['swing']
            
            smc_data = {
                'order_blocks': all_order_blocks,
                'order_blocks_internal': order_blocks_data['internal'],
                'order_blocks_swing': order_blocks_data['swing'],
                'fvg': fvg,
                'liquidity': liquidity,
                'choch': market_structure['swing_choch'] + market_structure['internal_choch'],
                'bos': market_structure['swing_bos'] + market_structure['internal_bos'],
                'internal_choch': market_structure['internal_choch'],
                'internal_bos': market_structure['internal_bos'],
                'swing_choch': market_structure['swing_choch'],
                'swing_bos': market_structure['swing_bos'],
                'trend': market_structure['swing_trend'],
                'internal_trend': market_structure['internal_trend'],
                'eqh': equal_levels['eqh'],
                'eql': equal_levels['eql'],
                'advanced': advanced_data
            }
            
            total_levels = (len(all_order_blocks) + len(fvg) + len(liquidity) + 
                          len(smc_data['choch']) + len(smc_data['bos']) + 
                          len(equal_levels['eqh']) + len(equal_levels['eql']))
            
            # Добавляем signals_count для обратной совместимости
            smc_data['signals_count'] = total_levels
            
            logger.info(f"SMC Analysis (LuxAlgo Enhanced): {total_levels} levels | "
                       f"Trend: I={market_structure['internal_trend']}, S={market_structure['swing_trend']} | "
                       f"Zone: {advanced_data['key_levels']['Current_Zone']} | "
                       f"OB:{len(all_order_blocks)}(I:{len(order_blocks_data['internal'])}/S:{len(order_blocks_data['swing'])}), "
                       f"FVG:{len(fvg)}, S/R:{len(liquidity)}, "
                       f"CHOCH:{len(smc_data['choch'])}(I:{len(market_structure['internal_choch'])}/S:{len(market_structure['swing_choch'])}), "
                       f"BOS:{len(smc_data['bos'])}(I:{len(market_structure['internal_bos'])}/S:{len(market_structure['swing_bos'])}), "
                       f"EQH:{len(equal_levels['eqh'])}, EQL:{len(equal_levels['eql'])}")
            
            return smc_data
            
        except Exception as e:
            logger.error(f"Error in SMC analysis: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return self._get_empty_result()


# Глобальный экземпляр
smc_detector = SMCDetector()
