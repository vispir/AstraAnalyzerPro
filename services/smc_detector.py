"""
SMC Detector v4.1 - Adaptive Impulse
=====================================
Изменения v4.1:
- FRESH_SIGNAL_BARS = 25 (было 10)
- Новый метод detect_impulse_context()
- Определение IMPULSE_TREND, is_void_run
- Поддержка Breakout входов
"""

import pandas as pd
import numpy as np
import logging
from typing import Dict, List, Tuple, Optional, Any
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# ============================================================================
# КОНСТАНТЫ v4.1
# ============================================================================

BULLISH = 1
BEARISH = -1
NEUTRAL = 0

# Параметры структуры (как LuxAlgo)
DEFAULT_INTERNAL_LEFT = 5
DEFAULT_INTERNAL_RIGHT = 5
DEFAULT_SWING_LEFT = 50
DEFAULT_SWING_RIGHT = 50

# v4.1: Увеличен порог свежести для импульсов
FRESH_SIGNAL_BARS = 25         # Было 10, стало 25 — помним пробой дольше
LOOKBACK_BARS = 250            # Глубина анализа
IMPULSE_THRESHOLD = 3          # Минимум BOS для определения импульса
VOID_RUN_THRESHOLD = 0.02      # 2% за пределами исторического экстремума


# ============================================================================
# СТРУКТУРЫ ДАННЫХ
# ============================================================================

@dataclass
class PivotPoint:
    """Pivot точка с полной информацией"""
    price: float = 0.0
    bar_index: int = 0
    bar_time: str = ""
    is_high: bool = True


@dataclass 
class StructureBreak:
    """Событие пробоя структуры"""
    break_type: str = ""
    price: float = 0.0
    bar_index: int = 0
    bar_time: str = ""
    pivot_bar_index: int = 0
    is_choch: bool = False
    bars_ago: int = 0
    break_by_wick: bool = False


@dataclass
class TrendState:
    """Состояние тренда"""
    bias: int = NEUTRAL
    pivot_high: PivotPoint = field(default_factory=PivotPoint)
    pivot_low: PivotPoint = field(default_factory=PivotPoint)
    last_break_index: int = 0


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
    SMC Detector v4.1 с поддержкой Adaptive Impulse
    """
    
    def __init__(self):
        self.analysis_count = 0
        self.internal_left = DEFAULT_INTERNAL_LEFT
        self.internal_right = DEFAULT_INTERNAL_RIGHT
        self.swing_left = DEFAULT_SWING_LEFT
        self.swing_right = DEFAULT_SWING_RIGHT
    
    def reset(self):
        """Сброс состояния"""
        self.analysis_count = 0
        logger.debug("SMC Detector reset")
    
    # ========================================================================
    # PIVOT DETECTION
    # ========================================================================
    
    def _find_all_pivots(self, df: pd.DataFrame, left_bars: int, right_bars: int) -> Tuple[List[PivotPoint], List[PivotPoint]]:
        """Находит ВСЕ pivot точки в истории"""
        pivot_highs = []
        pivot_lows = []
        
        if len(df) < left_bars + right_bars + 1:
            return pivot_highs, pivot_lows
        
        highs = df['high'].values
        lows = df['low'].values
        
        for i in range(left_bars, len(df) - right_bars):
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
        
        return pivot_highs, pivot_lows
    
    # ========================================================================
    # BAR-BY-BAR STRUCTURE DETECTION
    # ========================================================================
    
    def _detect_structure_history(self, df: pd.DataFrame, 
                                   pivot_highs: List[PivotPoint],
                                   pivot_lows: List[PivotPoint],
                                   structure_name: str = "swing") -> Tuple[List[StructureBreak], List[StructureBreak], int]:
        """Bar-by-bar сканирование истории для BOS/CHoCH"""
        all_choch: List[StructureBreak] = []
        all_bos: List[StructureBreak] = []
        
        if not pivot_highs and not pivot_lows:
            return all_choch, all_bos, NEUTRAL
        
        current_trend = NEUTRAL
        active_pivot_high: Optional[PivotPoint] = None
        active_pivot_low: Optional[PivotPoint] = None
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
            
            # Проверяем пробой Pivot High (Bullish break)
            if active_pivot_high and active_pivot_high.price > 0:
                if current_high > active_pivot_high.price:
                    is_choch = (current_trend == BEARISH)
                    break_type = 'BULLISH_CHOCH' if is_choch else 'BULLISH_BOS'
                    break_by_wick = current_close <= active_pivot_high.price
                    
                    event = StructureBreak(
                        break_type=break_type,
                        price=active_pivot_high.price,
                        bar_index=bar_i,
                        bar_time=bar_time,
                        pivot_bar_index=active_pivot_high.bar_index,
                        is_choch=is_choch,
                        bars_ago=total_bars - 1 - bar_i,
                        break_by_wick=break_by_wick
                    )
                    
                    if is_choch:
                        all_choch.append(event)
                    else:
                        all_bos.append(event)
                    
                    current_trend = BULLISH
                    active_pivot_high = None
            
            # Проверяем пробой Pivot Low (Bearish break)
            if active_pivot_low and active_pivot_low.price > 0:
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
        """Конвертация StructureBreak в словарь"""
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
    # MARKET STRUCTURE DETECTION
    # ========================================================================
    
    def detect_market_structure(self, df: pd.DataFrame) -> Dict:
        """Определение структуры рынка с bar-by-bar replay"""
        result = {
            'all_internal_choch': [], 'all_internal_bos': [],
            'all_swing_choch': [], 'all_swing_bos': [],
            'internal_choch': [], 'internal_bos': [],
            'swing_choch': [], 'swing_bos': [],
            'internal_trend': 'NEUTRAL', 'swing_trend': 'NEUTRAL',
            'internal_pivot_high': 0.0, 'internal_pivot_low': 0.0,
            'swing_pivot_high': 0.0, 'swing_pivot_low': 0.0
        }
        
        if len(df) < 20:
            return result
        
        # Internal Structure
        int_pivot_highs, int_pivot_lows = self._find_all_pivots(df, self.internal_left, self.internal_right)
        int_all_choch, int_all_bos, int_trend = self._detect_structure_history(df, int_pivot_highs, int_pivot_lows, "internal")
        
        result['all_internal_choch'] = [self._structure_break_to_dict(sb) for sb in int_all_choch]
        result['all_internal_bos'] = [self._structure_break_to_dict(sb) for sb in int_all_bos]
        result['internal_choch'] = [self._structure_break_to_dict(sb) for sb in int_all_choch if sb.bars_ago <= FRESH_SIGNAL_BARS]
        result['internal_bos'] = [self._structure_break_to_dict(sb) for sb in int_all_bos if sb.bars_ago <= FRESH_SIGNAL_BARS]
        result['internal_trend'] = 'UPTREND' if int_trend == BULLISH else 'DOWNTREND' if int_trend == BEARISH else 'NEUTRAL'
        
        if int_pivot_highs:
            result['internal_pivot_high'] = int_pivot_highs[-1].price
        if int_pivot_lows:
            result['internal_pivot_low'] = int_pivot_lows[-1].price
        
        # Swing Structure
        sw_pivot_highs, sw_pivot_lows = self._find_all_pivots(df, self.swing_left, self.swing_right)
        sw_all_choch, sw_all_bos, sw_trend = self._detect_structure_history(df, sw_pivot_highs, sw_pivot_lows, "swing")
        
        result['all_swing_choch'] = [self._structure_break_to_dict(sb) for sb in sw_all_choch]
        result['all_swing_bos'] = [self._structure_break_to_dict(sb) for sb in sw_all_bos]
        result['swing_choch'] = [self._structure_break_to_dict(sb) for sb in sw_all_choch if sb.bars_ago <= FRESH_SIGNAL_BARS]
        result['swing_bos'] = [self._structure_break_to_dict(sb) for sb in sw_all_bos if sb.bars_ago <= FRESH_SIGNAL_BARS]
        result['swing_trend'] = 'UPTREND' if sw_trend == BULLISH else 'DOWNTREND' if sw_trend == BEARISH else 'NEUTRAL'
        
        if sw_pivot_highs:
            result['swing_pivot_high'] = sw_pivot_highs[-1].price
        if sw_pivot_lows:
            result['swing_pivot_low'] = sw_pivot_lows[-1].price
        
        logger.info(f"Structure: Internal={result['internal_trend']} (PH:{result['internal_pivot_high']:.2f}, PL:{result['internal_pivot_low']:.2f}), "
                   f"Swing={result['swing_trend']} (PH:{result['swing_pivot_high']:.2f}, PL:{result['swing_pivot_low']:.2f})")
        
        return result
    
    # ========================================================================
    # IMPULSE CONTEXT DETECTION (v4.1 NEW!)
    # ========================================================================
    
    def detect_impulse_context(self, df: pd.DataFrame, analysis_result: Dict) -> Dict:
        """
        🔥 НОВЫЙ МЕТОД v4.1: Определяет контекст импульса
        
        Используется для Impulse Override в watcher.py
        
        Returns:
            {
                'market_condition': 'IMPULSE_TREND' | 'STRONG_TREND' | 'RANGING',
                'is_void_run': True/False,
                'impulse_strength': 0-100,
                'impulse_direction': 'BULLISH' | 'BEARISH' | 'NONE',
                'allow_discount_sell': True/False,
                'allow_premium_buy': True/False,
                'fresh_bos_count': int,
                'consecutive_bos': int
            }
        """
        context = {
            'market_condition': 'RANGING',
            'is_void_run': False,
            'impulse_strength': 0,
            'impulse_direction': 'NONE',
            'allow_discount_sell': False,
            'allow_premium_buy': False,
            'fresh_bos_count': 0,
            'consecutive_bos': 0
        }
        
        try:
            if len(df) < 50:
                return context
            
            current_price = float(df['close'].iloc[-1])
            
            # Исторические экстремумы за LOOKBACK_BARS
            lookback_df = df.tail(LOOKBACK_BARS)
            historical_high = float(lookback_df['high'].max())
            historical_low = float(lookback_df['low'].min())
            
            # Проверка Void Run (пробой исторического экстремума)
            low_threshold = historical_low * (1 + VOID_RUN_THRESHOLD)
            high_threshold = historical_high * (1 - VOID_RUN_THRESHOLD)
            
            if current_price < low_threshold:
                context['is_void_run'] = True
                context['impulse_direction'] = 'BEARISH'
                logger.info(f"🔥 VOID RUN DETECTED: Price {current_price:.2f} < Historical Low {historical_low:.2f}")
            elif current_price > high_threshold:
                context['is_void_run'] = True
                context['impulse_direction'] = 'BULLISH'
                logger.info(f"🔥 VOID RUN DETECTED: Price {current_price:.2f} > Historical High {historical_high:.2f}")
            
            # Подсчёт свежих BOS
            fresh_swing_bos = analysis_result.get('swing_bos', [])
            fresh_internal_bos = analysis_result.get('internal_bos', [])
            
            bearish_bos_count = sum(1 for b in fresh_swing_bos if 'BEARISH' in b.get('type', ''))
            bullish_bos_count = sum(1 for b in fresh_swing_bos if 'BULLISH' in b.get('type', ''))
            
            context['fresh_bos_count'] = len(fresh_swing_bos)
            
            # Определение направления импульса
            if bearish_bos_count >= IMPULSE_THRESHOLD:
                context['market_condition'] = 'IMPULSE_TREND'
                context['impulse_direction'] = 'BEARISH'
                context['impulse_strength'] = min(100, bearish_bos_count * 25)
                context['allow_discount_sell'] = True
                context['consecutive_bos'] = bearish_bos_count
                logger.info(f"⚡ IMPULSE TREND BEARISH: {bearish_bos_count} BOS detected")
                
            elif bullish_bos_count >= IMPULSE_THRESHOLD:
                context['market_condition'] = 'IMPULSE_TREND'
                context['impulse_direction'] = 'BULLISH'
                context['impulse_strength'] = min(100, bullish_bos_count * 25)
                context['allow_premium_buy'] = True
                context['consecutive_bos'] = bullish_bos_count
                logger.info(f"⚡ IMPULSE TREND BULLISH: {bullish_bos_count} BOS detected")
                
            elif len(fresh_swing_bos) >= 1:
                context['market_condition'] = 'STRONG_TREND'
                context['impulse_strength'] = min(75, len(fresh_swing_bos) * 20)
                
                # Определяем направление по последнему BOS
                if fresh_swing_bos:
                    last_bos = fresh_swing_bos[-1]
                    if 'BEARISH' in last_bos.get('type', ''):
                        context['impulse_direction'] = 'BEARISH'
                        context['allow_discount_sell'] = True
                    elif 'BULLISH' in last_bos.get('type', ''):
                        context['impulse_direction'] = 'BULLISH'
                        context['allow_premium_buy'] = True
            
            # Void Run автоматически разрешает торговлю в экстремальных зонах
            if context['is_void_run']:
                context['market_condition'] = 'IMPULSE_TREND'
                context['impulse_strength'] = max(context['impulse_strength'], 80)
                if context['impulse_direction'] == 'BEARISH':
                    context['allow_discount_sell'] = True
                elif context['impulse_direction'] == 'BULLISH':
                    context['allow_premium_buy'] = True
            
            logger.info(f"Impulse Context: {context['market_condition']}, "
                       f"Direction: {context['impulse_direction']}, "
                       f"Strength: {context['impulse_strength']}%, "
                       f"VoidRun: {context['is_void_run']}")
            
        except Exception as e:
            logger.error(f"Error in detect_impulse_context: {e}")
        
        return context
    
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
    # LIQUIDITY
    # ========================================================================
    
    def detect_liquidity(self, df: pd.DataFrame, lookback: int = 100) -> List[Dict]:
        """Детекция уровней ликвидности"""
        liquidity = []
        
        try:
            if len(df) < 10:
                return liquidity
            
            recent_df = df.tail(lookback)
            highs = recent_df['high'].values
            lows = recent_df['low'].values
            
            for i in range(3, len(recent_df) - 3):
                if highs[i] > max(highs[i-3:i]) and highs[i] > max(highs[i+1:i+4]):
                    liquidity.append({
                        'type': 'RESISTANCE',
                        'price': float(highs[i]),
                        'strength': 1
                    })
            
            for i in range(3, len(recent_df) - 3):
                if lows[i] < min(lows[i-3:i]) and lows[i] < min(lows[i+1:i+4]):
                    liquidity.append({
                        'type': 'SUPPORT',
                        'price': float(lows[i]),
                        'strength': 1
                    })
            
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
            
            # Swing highs
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
            
            # Swing lows
            swing_lows = []
            for i in range(2, len(recent_df) - 2):
                if recent_df['low'].iloc[i] < recent_df['low'].iloc[i-1] and \
                   recent_df['low'].iloc[i] < recent_df['low'].iloc[i+1]:
                    swing_lows.append({'price': float(recent_df['low'].iloc[i]), 'index': i})
            
            # Equal Lows
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
            'order_blocks': [], 'order_blocks_internal': [], 'order_blocks_swing': [],
            'fvg': [], 'liquidity': [],
            'choch': [], 'bos': [],
            'internal_choch': [], 'internal_bos': [],
            'swing_choch': [], 'swing_bos': [],
            'all_choch': [], 'all_bos': [],
            'all_internal_choch': [], 'all_internal_bos': [],
            'all_swing_choch': [], 'all_swing_bos': [],
            'eqh': [], 'eql': [],
            'trend': 'NEUTRAL', 'internal_trend': 'NEUTRAL',
            'internal_pivot_high': 0.0, 'internal_pivot_low': 0.0,
            'swing_pivot_high': 0.0, 'swing_pivot_low': 0.0,
            'advanced': self._get_empty_advanced_data(),
            'impulse_context': {
                'market_condition': 'RANGING',
                'is_void_run': False,
                'impulse_strength': 0,
                'impulse_direction': 'NONE',
                'allow_discount_sell': False,
                'allow_premium_buy': False
            },
            'signals_count': 0
        })
    
    # ========================================================================
    # ГЛАВНЫЙ МЕТОД АНАЛИЗА
    # ========================================================================
    
    def analyze(self, df) -> Dict:
        """
        Полный SMC анализ v4.1 с Impulse Context
        """
        try:
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
            
            # 1. Market Structure
            market_structure = self.detect_market_structure(df)
            
            # 2. Order Blocks
            order_blocks = self.detect_order_blocks(df)
            
            # 3. FVG
            fvg = self.detect_fvg(df)
            
            # 4. Liquidity
            liquidity = self.detect_liquidity(df)
            
            # 5. Equal Highs/Lows
            equal_levels = self.detect_equal_highs_lows(df)
            
            # Сборка результата
            all_order_blocks = order_blocks['internal'] + order_blocks['swing']
            fresh_choch = market_structure['internal_choch'] + market_structure['swing_choch']
            fresh_bos = market_structure['internal_bos'] + market_structure['swing_bos']
            all_choch = market_structure['all_internal_choch'] + market_structure['all_swing_choch']
            all_bos = market_structure['all_internal_bos'] + market_structure['all_swing_bos']
            
            result = {
                'order_blocks': all_order_blocks,
                'order_blocks_internal': order_blocks['internal'],
                'order_blocks_swing': order_blocks['swing'],
                'fvg': fvg,
                'liquidity': liquidity,
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
                'trend': market_structure['swing_trend'],
                'internal_trend': market_structure['internal_trend'],
                'internal_pivot_high': market_structure['internal_pivot_high'],
                'internal_pivot_low': market_structure['internal_pivot_low'],
                'swing_pivot_high': market_structure['swing_pivot_high'],
                'swing_pivot_low': market_structure['swing_pivot_low'],
                'eqh': equal_levels['eqh'],
                'eql': equal_levels['eql'],
                'advanced': self._get_empty_advanced_data()
            }
            
            # 6. Impulse Context (v4.1 NEW!)
            impulse_context = self.detect_impulse_context(df, result)
            result['impulse_context'] = impulse_context
            
            # Счётчик
            total = (len(all_order_blocks) + len(fvg) + len(liquidity) + 
                    len(all_choch) + len(all_bos) + 
                    len(equal_levels['eqh']) + len(equal_levels['eql']))
            result['signals_count'] = total
            
            logger.info(f"SMC Result: Signals={total} | "
                       f"Trend: I={market_structure['internal_trend']}, S={market_structure['swing_trend']} | "
                       f"Impulse: {impulse_context['market_condition']} ({impulse_context['impulse_strength']}%) | "
                       f"OB:{len(all_order_blocks)} FVG:{len(fvg)} CHoCH:{len(all_choch)} BOS:{len(all_bos)}")
            
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
    print("SMC Detector v4.1 - Adaptive Impulse")
    print(f"FRESH_SIGNAL_BARS = {FRESH_SIGNAL_BARS}")
    print(f"IMPULSE_THRESHOLD = {IMPULSE_THRESHOLD}")
    print("=" * 60)
    
    # Тест
    import numpy as np
    np.random.seed(42)
    n = 250
    
    base_price = 2650
    prices = [base_price]
    for i in range(1, n):
        trend = 0.1 if i < 100 else -0.15 if i < 180 else 0.2
        change = trend + np.random.randn() * 2
        prices.append(prices[-1] + change)
    
    data = []
    for i, close in enumerate(prices):
        high = close + abs(np.random.randn()) * 3
        low = close - abs(np.random.randn()) * 3
        open_price = prices[i-1] if i > 0 else close
        data.append({'open': open_price, 'high': high, 'low': low, 'close': close})
    
    df = pd.DataFrame(data)
    result = smc_detector.analyze(df)
    
    print(f"\n📊 РЕЗУЛЬТАТ:")
    print(f"   Цена: ${df['close'].iloc[-1]:.2f}")
    print(f"   Swing Trend: {result['trend']}")
    print(f"   Impulse: {result['impulse_context']['market_condition']}")
    print(f"   Strength: {result['impulse_context']['impulse_strength']}%")
    print(f"   VoidRun: {result['impulse_context']['is_void_run']}")
    print(f"   Fresh BOS: {len(result['swing_bos'])}")
    print("\n✅ Тест пройден!")
