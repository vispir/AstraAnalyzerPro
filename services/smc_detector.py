"""
Детектор SMC уровней (Order Blocks, FVG, Support/Resistance)
Улучшенная математика и логика
"""
import pandas as pd
import numpy as np
import logging
from typing import Dict, List

logger = logging.getLogger(__name__)


class SMCDetector:
    """Детектор Smart Money Concepts уровней"""
    
    def __init__(self):
        pass
    
    def detect_order_blocks(self, df: pd.DataFrame, lookback: int = 50) -> List[Dict]:
        order_blocks = []
        if len(df) < 5: return []
        
        recent_df = df.tail(lookback)
        current_price = df['Close'].iloc[-1] # Текущая цена для фильтрации
        
        for i in range(len(recent_df) - 3):
            # Берем индексы относительно всего df, чтобы не путаться
            idx = recent_df.index[i]
            current = recent_df.iloc[i]     # Потенциальный OB
            next1 = recent_df.iloc[i + 1]   # Свеча 1 после
            next2 = recent_df.iloc[i + 2]   # Свеча 2 после
            
            # --- BULLISH OB (Sell to Buy) ---
            # Была красная свеча, потом сильный вылет вверх
            if current['Close'] < current['Open']: # Красная
                # Импульс: Следующие свечи пробивают High красной свечи
                # Проверяем не только next2, а совокупный вылет
                break_level = current['High']
                
                # Если next1 или next2 закрылись сильно выше
                if (next1['Close'] > break_level or next2['Close'] > break_level):
                     # Считаем силу движения (тело бычьей свечи относительно OB)
                    move_size = max(next1['Close'], next2['Close']) - current['Low']
                    ob_size = current['High'] - current['Low']
                    
                    # Фильтр: движение хотя бы в 2 раза больше размера OB (Imbalance)
                    if move_size > ob_size * 2:
                        
                        # !!! ГЛАВНЫЙ ФИЛЬТР: ЖИВ ЛИ БЛОК? !!!
                        # Если текущая цена НИЖЕ дна блока -> он пробит (Failed)
                        if current_price < current['Low']:
                            continue
                            
                        order_blocks.append({
                            'type': 'BULL_OB',
                            'top': float(current['High']),
                            'bottom': float(current['Low']),
                            'strength': float(move_size),
                            'time': str(idx)
                        })

            # --- BEARISH OB (Buy to Sell) ---
            # Была зеленая свеча, потом сильный слив
            if current['Close'] > current['Open']: # Зеленая
                break_level = current['Low']
                
                if (next1['Close'] < break_level or next2['Close'] < break_level):
                    move_size = current['High'] - min(next1['Close'], next2['Close'])
                    ob_size = current['High'] - current['Low']
                    
                    if move_size > ob_size * 2:
                        
                        # !!! ГЛАВНЫЙ ФИЛЬТР: ЖИВ ЛИ БЛОК? !!!
                        # Если текущая цена ВЫШЕ верха блока -> он пробит
                        if current_price > current['High']:
                            continue
                            
                        order_blocks.append({
                            'type': 'BEAR_OB',
                            'top': float(current['High']),
                            'bottom': float(current['Low']),
                            'strength': float(move_size),
                            'time': str(idx)
                        })
        
        # Возвращаем только самые свежие или сильные (например, последние 3)
        return order_blocks[-3:]
    
    def detect_fvg(self, df: pd.DataFrame, lookback: int = 50) -> List[Dict]:
        fvg_list = []
        if len(df) < 3: return []
        
        recent_df = df.tail(lookback)
        current_price = df['Close'].iloc[-1]
        
        for i in range(1, len(recent_df) - 1):
            candle1 = recent_df.iloc[i - 1]
            candle2 = recent_df.iloc[i]      # Импульсная свеча
            candle3 = recent_df.iloc[i + 1]
            
            # Минимальный размер гэпа (можно через ATR, но пока % ок для золота)
            min_gap = candle2['Close'] * 0.0005 

            # BULLISH FVG (Gap Up)
            # Low третьей свечи все еще выше High первой
            if candle3['Low'] > candle1['High']:
                gap_size = candle3['Low'] - candle1['High']
                if gap_size > min_gap:
                    
                    # ФИЛЬТР: Если цена уже ниже гэпа - он невалиден (или support стал resistance)
                    # Для простоты пока просто отбрасываем
                    if current_price < candle1['High']:
                        continue
                        
                    fvg_list.append({
                        'type': 'BULL_FVG',
                        'top': float(candle3['Low']),
                        'bottom': float(candle1['High']),
                        'price': float((candle3['Low'] + candle1['High']) / 2), # Середина
                        'gap_size': float(gap_size)
                    })
            
            # BEARISH FVG (Gap Down)
            elif candle3['High'] < candle1['Low']:
                gap_size = candle1['Low'] - candle3['High']
                if gap_size > min_gap:
                    
                    # ФИЛЬТР
                    if current_price > candle1['Low']:
                        continue
                        
                    fvg_list.append({
                        'type': 'BEAR_FVG',
                        'top': float(candle1['Low']),
                        'bottom': float(candle3['High']),
                        'price': float((candle1['Low'] + candle3['High']) / 2),
                        'gap_size': float(gap_size)
                    })
                    
        return fvg_list[-3:] # Топ-3 последних
    
    def detect_liquidity(self, df: pd.DataFrame, lookback: int = 100) -> List[Dict]:
        """
        Определение значимых Support/Resistance уровней
        С кластеризацией и учетом "силы" уровня
        
        Args:
            df: DataFrame с OHLC данными
            lookback: Количество свечей для поиска
            
        Returns:
            Список S/R уровней с силой
        """
        liquidity = []
        
        try:
            if len(df) < 10:
                return liquidity
            
            recent_df = df.tail(lookback)
            
            highs = recent_df['High'].values
            lows = recent_df['Low'].values
            
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
                    # Если уровень близко к текущему кластеру
                    cluster_avg = sum(l['price'] for l in current_cluster) / len(current_cluster)
                    if abs(level['price'] - cluster_avg) / cluster_avg < threshold:
                        current_cluster.append(level)
                    else:
                        # Сохраняем предыдущий кластер
                        avg_price = sum(l['price'] for l in current_cluster) / len(current_cluster)
                        clusters.append({
                            'price': avg_price,
                            'strength': len(current_cluster),
                            'latest_time': max(l['time'] for l in current_cluster)
                        })
                        current_cluster = [level]
                
                # Последний кластер
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
            
            # Формируем итоговые уровни (только значимые)
            for cluster in high_clusters:
                # Берем уровни с силой >= 2 или близкие к экстремумам
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
    
    def detect_market_structure(self, df: pd.DataFrame, lookback: int = 50) -> Dict:
        """
        Определение структуры рынка (CHOCH, BOS)
        
        Args:
            df: DataFrame с OHLC данными
            lookback: Количество свечей для анализа
            
        Returns:
            Dict с CHOCH, BOS и трендом
        """
        structure = {
            'choch': [],
            'bos': [],
            'trend': 'NEUTRAL'
        }
        
        try:
            if len(df) < 10:
                return structure
            
            recent_df = df.tail(lookback)
            
            # Находим swing highs и lows
            swing_highs = []
            swing_lows = []
            
            for i in range(2, len(recent_df) - 2):
                # Swing High
                if (recent_df['High'].iloc[i] > recent_df['High'].iloc[i-1] and
                    recent_df['High'].iloc[i] > recent_df['High'].iloc[i-2] and
                    recent_df['High'].iloc[i] > recent_df['High'].iloc[i+1] and
                    recent_df['High'].iloc[i] > recent_df['High'].iloc[i+2]):
                    
                    swing_highs.append({
                        'price': float(recent_df['High'].iloc[i]),
                        'index': i,
                        'time': recent_df.index[i]
                    })
                
                # Swing Low
                if (recent_df['Low'].iloc[i] < recent_df['Low'].iloc[i-1] and
                    recent_df['Low'].iloc[i] < recent_df['Low'].iloc[i-2] and
                    recent_df['Low'].iloc[i] < recent_df['Low'].iloc[i+1] and
                    recent_df['Low'].iloc[i] < recent_df['Low'].iloc[i+2]):
                    
                    swing_lows.append({
                        'price': float(recent_df['Low'].iloc[i]),
                        'index': i,
                        'time': recent_df.index[i]
                    })
            
            if len(swing_highs) < 2 or len(swing_lows) < 2:
                return structure
            
            # Определяем тренд по последним swing points
            last_high = swing_highs[-1]
            prev_high = swing_highs[-2] if len(swing_highs) >= 2 else last_high
            last_low = swing_lows[-1]
            prev_low = swing_lows[-2] if len(swing_lows) >= 2 else last_low
            
            # UPTREND: Higher Highs и Higher Lows
            higher_highs = last_high['price'] > prev_high['price']
            higher_lows = last_low['price'] > prev_low['price']
            
            # DOWNTREND: Lower Highs и Lower Lows
            lower_highs = last_high['price'] < prev_high['price']
            lower_lows = last_low['price'] < prev_low['price']
            
            if higher_highs and higher_lows:
                structure['trend'] = 'UPTREND'
            elif lower_highs and lower_lows:
                structure['trend'] = 'DOWNTREND'
            
            # Находим CHOCH (Change of Character)
            # CHOCH в UPTREND: когда цена пробивает предыдущий swing low
            # CHOCH в DOWNTREND: когда цена пробивает предыдущий swing high
            
            current_price = float(recent_df['Close'].iloc[-1])
            
            # Ищем последние 3 swing points для определения CHOCH
            recent_highs = swing_highs[-3:] if len(swing_highs) >= 3 else swing_highs
            recent_lows = swing_lows[-3:] if len(swing_lows) >= 3 else swing_lows
            
            # CHOCH: пробой структуры против тренда
            for i in range(len(recent_lows) - 1):
                low = recent_lows[i]
                # Если текущая цена пробила этот low (в uptrend = CHOCH)
                if current_price < low['price'] and structure['trend'] == 'UPTREND':
                    structure['choch'].append({
                        'type': 'BEARISH_CHOCH',
                        'price': float(low['price']),
                        'time': low['time'],
                        'description': 'Break of previous low in uptrend'
                    })
                    break
            
            for i in range(len(recent_highs) - 1):
                high = recent_highs[i]
                # Если текущая цена пробила этот high (в downtrend = CHOCH)
                if current_price > high['price'] and structure['trend'] == 'DOWNTREND':
                    structure['choch'].append({
                        'type': 'BULLISH_CHOCH',
                        'price': float(high['price']),
                        'time': high['time'],
                        'description': 'Break of previous high in downtrend'
                    })
                    break
            
            # BOS (Break of Structure): пробой в направлении тренда
            if structure['trend'] == 'UPTREND' and len(swing_highs) >= 2:
                # BOS в uptrend: пробой предыдущего high
                prev_high = swing_highs[-2]
                if current_price > prev_high['price']:
                    structure['bos'].append({
                        'type': 'BULLISH_BOS',
                        'price': float(prev_high['price']),
                        'time': prev_high['time'],
                        'description': 'Break of structure to upside'
                    })
            
            if structure['trend'] == 'DOWNTREND' and len(swing_lows) >= 2:
                # BOS в downtrend: пробой предыдущего low
                prev_low = swing_lows[-2]
                if current_price < prev_low['price']:
                    structure['bos'].append({
                        'type': 'BEARISH_BOS',
                        'price': float(prev_low['price']),
                        'time': prev_low['time'],
                        'description': 'Break of structure to downside'
                    })
            
            # Ограничиваем до последних 2
            structure['choch'] = structure['choch'][-2:]
            structure['bos'] = structure['bos'][-2:]
            
            logger.info(f"Market Structure: {structure['trend']}, "
                       f"CHOCH: {len(structure['choch'])}, BOS: {len(structure['bos'])}")
            
        except Exception as e:
            logger.error(f"Error detecting market structure: {str(e)}")
        
        return structure
    
    def detect_equal_highs_lows(self, df: pd.DataFrame, lookback: int = 50, tolerance: float = 0.001) -> Dict:
        """
        Определение Equal Highs/Lows (EQH/EQL)
        Зоны ликвидности где цена может сделать sweep
        
        Args:
            df: DataFrame с OHLC данными
            lookback: Количество свечей
            tolerance: Допуск для определения "равных" уровней (0.1%)
            
        Returns:
            Dict с EQH и EQL
        """
        equal_levels = {
            'eqh': [],
            'eql': []
        }
        
        try:
            if len(df) < 10:
                return equal_levels
            
            recent_df = df.tail(lookback)
            
            # Находим локальные максимумы
            highs = []
            for i in range(2, len(recent_df) - 2):
                if (recent_df['High'].iloc[i] > recent_df['High'].iloc[i-1] and
                    recent_df['High'].iloc[i] > recent_df['High'].iloc[i+1]):
                    highs.append({
                        'price': float(recent_df['High'].iloc[i]),
                        'index': i,
                        'time': recent_df.index[i]
                    })
            
            # Находим локальные минимумы
            lows = []
            for i in range(2, len(recent_df) - 2):
                if (recent_df['Low'].iloc[i] < recent_df['Low'].iloc[i-1] and
                    recent_df['Low'].iloc[i] < recent_df['Low'].iloc[i+1]):
                    lows.append({
                        'price': float(recent_df['Low'].iloc[i]),
                        'index': i,
                        'time': recent_df.index[i]
                    })
            
            # Находим Equal Highs (EQH)
            for i in range(len(highs) - 1):
                for j in range(i + 1, len(highs)):
                    price_diff = abs(highs[i]['price'] - highs[j]['price']) / highs[i]['price']
                    
                    # Если разница меньше tolerance - это равные highs
                    if price_diff < tolerance:
                        avg_price = (highs[i]['price'] + highs[j]['price']) / 2
                        
                        # Проверяем, не добавили ли уже этот уровень
                        is_duplicate = False
                        for existing in equal_levels['eqh']:
                            if abs(existing['price'] - avg_price) / avg_price < tolerance:
                                is_duplicate = True
                                break
                        
                        if not is_duplicate:
                            equal_levels['eqh'].append({
                                'price': float(avg_price),
                                'time1': highs[i]['time'],
                                'time2': highs[j]['time'],
                                'touches': 2,
                                'type': 'EQUAL_HIGHS'
                            })
            
            # Находим Equal Lows (EQL)
            for i in range(len(lows) - 1):
                for j in range(i + 1, len(lows)):
                    price_diff = abs(lows[i]['price'] - lows[j]['price']) / lows[i]['price']
                    
                    if price_diff < tolerance:
                        avg_price = (lows[i]['price'] + lows[j]['price']) / 2
                        
                        is_duplicate = False
                        for existing in equal_levels['eql']:
                            if abs(existing['price'] - avg_price) / avg_price < tolerance:
                                is_duplicate = True
                                break
                        
                        if not is_duplicate:
                            equal_levels['eql'].append({
                                'price': float(avg_price),
                                'time1': lows[i]['time'],
                                'time2': lows[j]['time'],
                                'touches': 2,
                                'type': 'EQUAL_LOWS'
                            })
            
            # Ограничиваем до топ-3 по каждому типу
            equal_levels['eqh'] = equal_levels['eqh'][-3:]
            equal_levels['eql'] = equal_levels['eql'][-3:]
            
            logger.info(f"Detected {len(equal_levels['eqh'])} EQH and {len(equal_levels['eql'])} EQL")
            
        except Exception as e:
            logger.error(f"Error detecting EQH/EQL: {str(e)}")
        
        return equal_levels
    
    def analyze(self, df: pd.DataFrame) -> Dict:
        """
        Полный SMC анализ
        
        Args:
            df: DataFrame с OHLC данными
            
        Returns:
            Dict со всеми SMC уровнями и структурой рынка
        """
        try:
            # Основные структуры
            smc_data = {
                'order_blocks': self.detect_order_blocks(df),
                'fvg': self.detect_fvg(df),
                'liquidity': self.detect_liquidity(df)
            }
            
            # Структура рынка (CHOCH, BOS)
            market_structure = self.detect_market_structure(df)
            smc_data['choch'] = market_structure['choch']
            smc_data['bos'] = market_structure['bos']
            smc_data['trend'] = market_structure['trend']
            
            # Equal Highs/Lows
            equal_levels = self.detect_equal_highs_lows(df)
            smc_data['eqh'] = equal_levels['eqh']
            smc_data['eql'] = equal_levels['eql']
            
            total_levels = (len(smc_data['order_blocks']) + 
                          len(smc_data['fvg']) + 
                          len(smc_data['liquidity']) +
                          len(smc_data['choch']) +
                          len(smc_data['bos']) +
                          len(smc_data['eqh']) +
                          len(smc_data['eql']))
            
            logger.info(f"SMC Analysis complete: {total_levels} levels detected | "
                       f"Trend: {smc_data['trend']} | "
                       f"OB:{len(smc_data['order_blocks'])}, "
                       f"FVG:{len(smc_data['fvg'])}, "
                       f"S/R:{len(smc_data['liquidity'])}, "
                       f"CHOCH:{len(smc_data['choch'])}, "
                       f"BOS:{len(smc_data['bos'])}, "
                       f"EQH:{len(smc_data['eqh'])}, "
                       f"EQL:{len(smc_data['eql'])}")
            
            return smc_data
            
        except Exception as e:
            logger.error(f"Error in SMC analysis: {str(e)}")
            return {
                'order_blocks': [],
                'fvg': [],
                'liquidity': [],
                'choch': [],
                'bos': [],
                'eqh': [],
                'eql': [],
                'trend': 'NEUTRAL'
            }


# Глобальный экземпляр
smc_detector = SMCDetector()
