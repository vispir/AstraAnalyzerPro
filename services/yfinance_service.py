"""
Сервис для работы с Yahoo Finance API
"""
import yfinance as yf
import logging
import hashlib
import json
import time
import requests
from datetime import datetime
from typing import Dict, List, Optional

from config.settings import (
    YAHOO_SYMBOL, 
    TIMEFRAME_MAP, 
    PERIOD_MAP
)
from services.cache_service import cache_service

logger = logging.getLogger(__name__)

# Browser-like headers для обхода блокировок
BROWSER_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
    'Accept-Language': 'en-US,en;q=0.5',
    'Accept-Encoding': 'gzip, deflate, br',
    'Connection': 'keep-alive',
    'Upgrade-Insecure-Requests': '1',
}


class YFinanceService:
    """Сервис для получения рыночных данных через Yahoo Finance"""
    
    def __init__(self, symbol: str = YAHOO_SYMBOL):
        self.symbol = symbol
        self.ticker = yf.Ticker(symbol)
        self.candles_cache_ttl = 300  # 5 минут (300 секунд)
        
    def get_candles(self, timeframe: str = 'M15', period: Optional[str] = None, limit: Optional[int] = None) -> Dict:
        """
        Получение свечных данных
        
        Args:
            timeframe: Таймфрейм (M15, H1, H4, etc.)
            period: Период данных (если None, берется из PERIOD_MAP)
            limit: Количество последних свечей (если None, возвращаются все)
            
        Returns:
            Dict с данными свечей
        """
        # Получаем интервал для Yahoo Finance
        interval = TIMEFRAME_MAP.get(timeframe, '15m')
        if period is None:
            period = PERIOD_MAP.get(timeframe, '5d')
        
        # Проверяем кэш
        cache_key = cache_service._generate_key(
            'candles',
            self.symbol,
            timeframe=timeframe,
            period=period,
            limit=limit
        )
        cached_data = cache_service.get(cache_key)
        if cached_data is not None:
            try:
                count = cached_data.get('count', 0) if isinstance(cached_data, dict) else 0
                logger.info(f"Returning cached candles data ({count} candles)")
                return cached_data
            except Exception as e:
                logger.warning(f"Error accessing cached data: {e}, type: {type(cached_data)}")
                # Продолжаем выполнение, если кэш поврежден
        
        # Retry механизм для получения данных
        max_retries = 3
        retry_delay = 2
        original_requests = None
        
        for attempt in range(1, max_retries + 1):
            df = None
            yf_utils_patched = False
            try:
                logger.info(f"Fetching {self.symbol} data: interval={interval}, period={period}, limit={limit} (attempt {attempt}/{max_retries})")
                
                # Получаем данные с browser headers через monkey patching
                try:
                    import yfinance.utils as yf_utils
                    original_requests = getattr(yf_utils, '_requests', requests)
                    
                    # Создаем сессию с browser headers
                    session = requests.Session()
                    session.headers.update(BROWSER_HEADERS)
                    yf_utils._requests = session
                    yf_utils_patched = True
                except Exception as patch_error:
                    logger.debug(f"Could not patch yfinance requests: {patch_error}")
                
                # Получаем данные
                df = self.ticker.history(period=period, interval=interval)
                logger.debug(f"History call completed. Result type: {type(df)}")
                
            except TypeError as hist_error:
                if "'NoneType' object is not subscriptable" in str(hist_error):
                    logger.error(f"Yahoo Finance API returned None - service may be blocked or unavailable (attempt {attempt}/{max_retries})")
                    if attempt < max_retries:
                        time.sleep(retry_delay)
                        continue
                    return {
                        "error": "Yahoo Finance недоступен. Используйте source=twelvedata",
                        "details": "Yahoo Finance API заблокирован или недоступен в вашем регионе. Попробуйте добавить параметр ?source=twelvedata к запросу."
                    }
                logger.error(f"TypeError during history() call (attempt {attempt}/{max_retries}): {hist_error}")
                if attempt < max_retries:
                    time.sleep(retry_delay)
                    continue
                import traceback
                logger.error(traceback.format_exc())
                return {"error": f"Ошибка получения данных: {str(hist_error)}"}
            except requests.exceptions.HTTPError as http_error:
                status_code = http_error.response.status_code if hasattr(http_error, 'response') and http_error.response else None
                logger.error(f"HTTP Error during history() call (attempt {attempt}/{max_retries}): {status_code} - {str(http_error)}")
                if status_code == 403:
                    logger.error("403 Forbidden - Yahoo Finance blocking requests. Check VPN/network.")
                if attempt < max_retries:
                    time.sleep(retry_delay)
                    continue
                return {"error": f"HTTP {status_code}: Yahoo Finance недоступен. Используйте source=twelvedata"}
            except requests.exceptions.Timeout as timeout_error:
                logger.error(f"Timeout during history() call (attempt {attempt}/{max_retries}): {str(timeout_error)}")
                if attempt < max_retries:
                    time.sleep(retry_delay)
                    continue
                return {"error": f"Таймаут при получении данных: {str(timeout_error)}"}
            except requests.exceptions.ConnectionError as conn_error:
                logger.error(f"Connection error during history() call (attempt {attempt}/{max_retries}): {str(conn_error)}")
                if attempt < max_retries:
                    time.sleep(retry_delay)
                    continue
                return {"error": f"Ошибка подключения: {str(conn_error)}. Проверьте интернет и VPN."}
            except Exception as hist_error:
                logger.error(f"Exception during history() call (attempt {attempt}/{max_retries}): {type(hist_error).__name__}: {str(hist_error)}")
                if attempt < max_retries:
                    time.sleep(retry_delay)
                    continue
                import traceback
                logger.error(traceback.format_exc())
                return {"error": f"Ошибка при получении данных: {str(hist_error)}"}
            finally:
                # Восстанавливаем оригинальный requests
                if yf_utils_patched and original_requests is not None:
                    try:
                        import yfinance.utils as yf_utils
                        yf_utils._requests = original_requests
                    except:
                        pass
            
            # Если df не получен, пробуем еще раз
            if df is None:
                logger.error(f"History returned None for {self.symbol} (attempt {attempt}/{max_retries})")
                if attempt < max_retries:
                    time.sleep(retry_delay)
                    continue
                return {"error": f"Нет данных для {timeframe} (history returned None)"}
            
            # Проверяем, что данные получены и не пустые
            try:
                is_empty = df.empty
                logger.debug(f"DataFrame empty check: {is_empty}")
            except Exception as e:
                logger.error(f"Error checking if DataFrame is empty: {e}")
                if attempt < max_retries:
                    time.sleep(retry_delay)
                    continue
                import traceback
                logger.error(traceback.format_exc())
                return {"error": f"Ошибка при проверке данных: {str(e)}"}
            
            if is_empty:
                logger.error(f"Empty DataFrame received for {self.symbol} (attempt {attempt}/{max_retries})")
                if attempt < max_retries:
                    time.sleep(retry_delay)
                    continue
                return {"error": f"Нет данных для {timeframe} (empty DataFrame)"}
            
            # Проверяем наличие необходимых колонок
            required_columns = ['Open', 'High', 'Low', 'Close']
            try:
                df_columns = df.columns
                logger.debug(f"DataFrame columns: {list(df_columns)}")
                logger.debug(f"DataFrame shape: {df.shape}")
                logger.debug(f"DataFrame index type: {type(df.index)}")
            except Exception as e:
                logger.error(f"Error accessing DataFrame columns: {e}")
                import traceback
                logger.error(traceback.format_exc())
                return {"error": f"Ошибка при проверке колонок: {str(e)}"}
            
            missing_columns = [col for col in required_columns if col not in df_columns]
            if missing_columns:
                logger.error(f"Missing required columns: {missing_columns}, available: {list(df_columns)}")
                return {"error": f"Отсутствуют необходимые колонки: {missing_columns}"}
            
            # Специальная обработка для H4 (агрегация 4 часовых свечей в одну)
            if timeframe == 'H4':
                # Для H4 запрашиваем H1 данные и агрегируем
                # limit * 4 чтобы получить нужное количество H4 свечей
                if limit:
                    # Берем в 4 раза больше H1 свечей
                    df = df.tail(limit * 4)
                
                # Агрегируем каждые 4 свечи в одну H4 свечу
                # Группируем по индексу (каждые 4 строки)
                df_h4_list = []
                for i in range(0, len(df), 4):
                    try:
                        chunk = df.iloc[i:i+4]
                        if len(chunk) == 4:  # Только полные 4-часовые блоки
                            # Проверяем, что chunk не пустой и содержит данные
                            if chunk.empty:
                                logger.warning(f"Empty chunk at index {i}")
                                continue
                            
                            # Проверяем наличие необходимых колонок
                            if not all(col in chunk.columns for col in required_columns):
                                logger.warning(f"Chunk missing required columns at index {i}")
                                continue
                            
                            # Безопасное извлечение значений
                            first_row = chunk.iloc[0]
                            last_row = chunk.iloc[-1]
                            
                            if first_row is None or last_row is None:
                                logger.warning(f"None row in chunk at index {i}")
                                continue
                            
                            try:
                                open_val = first_row['Open'] if 'Open' in first_row.index else None
                                close_val = last_row['Close'] if 'Close' in last_row.index else None
                                
                                if open_val is None or close_val is None:
                                    logger.warning(f"None values in chunk at index {i}")
                                    continue
                                
                                h4_candle = {
                                    'Open': open_val,
                                    'High': chunk['High'].max(),
                                    'Low': chunk['Low'].min(),
                                    'Close': close_val,
                                    'Volume': chunk['Volume'].sum() if 'Volume' in chunk.columns else 0
                                }
                                
                                # Используем timestamp последней свечи в блоке
                                chunk_index = chunk.index[-1]
                                if chunk_index is None:
                                    logger.warning(f"None index in chunk at index {i}")
                                    continue
                                
                                df_h4_list.append((chunk_index, h4_candle))
                            except (KeyError, IndexError, TypeError) as e:
                                logger.error(f"Error processing chunk at index {i}: {e}")
                                continue
                    except Exception as e:
                        logger.error(f"Error accessing chunk at index {i}: {e}")
                        import traceback
                        logger.error(traceback.format_exc())
                        continue
                
                # Создаем новый DataFrame из агрегированных данных
                if df_h4_list:
                    import pandas as pd
                    df = pd.DataFrame([candle for _, candle in df_h4_list], 
                                     index=[ts for ts, _ in df_h4_list])
                else:
                    logger.error("No H4 candles aggregated")
                    return {"error": "Не удалось агрегировать H4 свечи"}
            else:
                # Применяем лимит для остальных таймфреймов
                if limit and limit > 0:
                    df = df.tail(limit)
            
            # Конвертируем в формат для frontend
            candles = []
            logger.debug(f"Starting to process {len(df)} rows")
            
            try:
                for idx, (index, row) in enumerate(df.iterrows()):
                    try:
                        # Проверяем, что row не None и содержит необходимые данные
                        if row is None:
                            logger.warning(f"Skipping None row at index {index} (iteration {idx})")
                            continue
                        
                        # Проверяем, что row это Series или dict-like объект
                        if not hasattr(row, '__getitem__'):
                            logger.warning(f"Row at index {index} is not subscriptable, type: {type(row)}")
                            continue
                        
                        # Проверяем наличие необходимых колонок в строке
                        row_index = row.index if hasattr(row, 'index') else None
                        if row_index is None:
                            logger.warning(f"Row at index {index} has no index attribute")
                            continue
                            
                        if not all(col in row_index for col in required_columns):
                            logger.warning(f"Skipping row with missing columns at index {index}, available: {list(row_index)}")
                            continue
                        
                        # Обрабатываем timestamp индекса
                        try:
                            if hasattr(index, 'timestamp'):
                                timestamp = int(index.timestamp())
                            elif hasattr(index, 'to_pydatetime'):
                                timestamp = int(index.to_pydatetime().timestamp())
                            else:
                                # Пытаемся преобразовать в datetime
                                import pandas as pd
                                if isinstance(index, str):
                                    dt = pd.to_datetime(index)
                                    timestamp = int(dt.timestamp())
                                else:
                                    logger.error(f"Cannot convert index to timestamp: {type(index)}, value: {index}")
                                    continue
                        except Exception as e:
                            logger.error(f"Error converting index to timestamp: {e}, index type: {type(index)}, value: {index}")
                            continue
                        
                        # Извлекаем значения из row с проверками
                        try:
                            open_val = row['Open'] if 'Open' in row_index else None
                            high_val = row['High'] if 'High' in row_index else None
                            low_val = row['Low'] if 'Low' in row_index else None
                            close_val = row['Close'] if 'Close' in row_index else None
                            
                            if any(v is None for v in [open_val, high_val, low_val, close_val]):
                                logger.warning(f"Skipping row with None values at index {index}")
                                continue
                            
                            candle_data = {
                                "time": timestamp,
                                "open": round(float(open_val), 2),
                                "high": round(float(high_val), 2),
                                "low": round(float(low_val), 2),
                                "close": round(float(close_val), 2)
                            }
                            
                            # Добавляем volume только если он не 0
                            volume = int(row['Volume']) if 'Volume' in row_index else 0
                            if volume > 0:
                                candle_data["volume"] = volume
                                
                            candles.append(candle_data)
                        except (ValueError, TypeError, KeyError, IndexError) as e:
                            logger.error(f"Error processing row data at index {index}: {e}, row type: {type(row)}, row: {row}")
                            continue
                    except Exception as e:
                        logger.error(f"Unexpected error processing row {idx}: {e}, index: {index}, row: {row}")
                        import traceback
                        logger.error(traceback.format_exc())
                        continue
            except Exception as e:
                logger.error(f"Error in iterrows loop: {e}")
                import traceback
                logger.error(traceback.format_exc())
                raise
            
                if not candles:
                    logger.error(f"No candles processed for {self.symbol} timeframe {timeframe} (attempt {attempt}/{max_retries})")
                    if attempt < max_retries:
                        time.sleep(retry_delay)
                        continue
                    return {"error": f"Не удалось обработать данные для {timeframe}"}
                
                logger.info(f"Successfully fetched {len(candles)} candles")
                
                result = {
                    "success": True,
                    "symbol": self.symbol,
                    "timeframe": timeframe,
                    "candles": candles,
                    "count": len(candles)
                }
                
                # Сохраняем в кэш с TTL 15 минут
                cache_service.set(cache_key, result, self.candles_cache_ttl)
                
                # Успешно получили данные - выходим из retry цикла
                return result
                
            except Exception as e:
                logger.error(f"Error in retry attempt {attempt}/{max_retries}: {type(e).__name__}: {str(e)}")
                if attempt < max_retries:
                    time.sleep(retry_delay)
                    continue
                logger.error(f"All retry attempts failed. Last error: {str(e)}")
                import traceback
                logger.error(traceback.format_exc())
                return {"error": f"Ошибка получения данных после {max_retries} попыток: {str(e)}"}
        
        # Если дошли сюда, значит все попытки исчерпаны
        return {"error": f"Не удалось получить данные для {self.symbol} после {max_retries} попыток"}
    
    def get_current_price(self) -> Optional[float]:
        """
        Получение текущей цены
        
        Returns:
            Текущая цена (округлена до 2 знаков) или None
        """
        try:
            # Получаем последнюю свечу на минутном интервале
            df = self.ticker.history(period='1d', interval='1m')
            if not df.empty:
                return round(float(df['Close'].iloc[-1]), 2)
            return None
        except Exception as e:
            logger.error(f"Error fetching current price: {str(e)}")
            return None
    
    def get_ticker_info(self) -> Dict:
        """
        Получение информации о тикере
        
        Returns:
            Dict с информацией о тикере
        """
        try:
            info = self.ticker.info
            return {
                "symbol": self.symbol,
                "name": info.get('longName', 'Gold Futures'),
                "currency": info.get('currency', 'USD'),
                "exchange": info.get('exchange', 'CME'),
                "current_price": self.get_current_price()
            }
        except Exception as e:
            logger.error(f"Error fetching ticker info: {str(e)}")
            return {
                "symbol": self.symbol,
                "name": "Gold Futures",
                "currency": "USD"
            }
    
    def validate_symbol(self) -> bool:
        """
        Проверка доступности символа с retry механизмом и browser-like headers
        
        Returns:
            True если символ доступен, False иначе
        """
        max_retries = 3
        retry_delay = 2  # секунды между попытками
        
        # Настраиваем yfinance с browser-like headers через monkey patching
        try:
            import yfinance.utils as yf_utils
            # Сохраняем оригинальную функцию если есть
            if hasattr(yf_utils, '_requests'):
                original_requests = yf_utils._requests
            else:
                original_requests = requests
        except:
            original_requests = requests
        
        for attempt in range(1, max_retries + 1):
            try:
                logger.info(f"Validating symbol {self.symbol} (attempt {attempt}/{max_retries})")
                
                # Настраиваем requests session с browser headers для yfinance
                session = requests.Session()
                session.headers.update(BROWSER_HEADERS)
                
                # Временно патчим yfinance для использования нашей сессии
                try:
                    import yfinance.utils as yf_utils
                    yf_utils._requests = session
                except:
                    pass
                
                # Пытаемся получить данные
                df = self.ticker.history(period='1d', interval='1h')
                
                # Восстанавливаем оригинальный requests
                try:
                    yf_utils._requests = original_requests
                except:
                    pass
                
                if df is not None and not df.empty:
                    logger.info(f"✓ Symbol {self.symbol} validated successfully (got {len(df)} rows)")
                    return True
                else:
                    logger.warning(f"Symbol {self.symbol} validation returned empty data (attempt {attempt}/{max_retries})")
                    if attempt < max_retries:
                        time.sleep(retry_delay)
                        continue
                    return False
                    
            except requests.exceptions.HTTPError as e:
                status_code = e.response.status_code if hasattr(e, 'response') and e.response else None
                error_msg = f"HTTP {status_code}: {str(e)}" if status_code else str(e)
                logger.error(f"Yahoo Finance validation failed (attempt {attempt}/{max_retries}): {error_msg}")
                
                if status_code == 403:
                    logger.error("403 Forbidden - Yahoo Finance may be blocking requests. Check VPN/network.")
                elif status_code == 429:
                    logger.warning("429 Too Many Requests - Rate limit exceeded. Waiting before retry...")
                    if attempt < max_retries:
                        time.sleep(retry_delay * 2)  # Увеличиваем задержку при rate limit
                        continue
                elif status_code:
                    logger.error(f"HTTP Error {status_code}: {error_msg}")
                
                if attempt < max_retries:
                    time.sleep(retry_delay)
                    continue
                return False
                
            except requests.exceptions.Timeout as e:
                logger.error(f"Yahoo Finance validation timeout (attempt {attempt}/{max_retries}): {str(e)}")
                if attempt < max_retries:
                    time.sleep(retry_delay)
                    continue
                return False
                
            except requests.exceptions.ConnectionError as e:
                logger.error(f"Yahoo Finance connection error (attempt {attempt}/{max_retries}): {str(e)}")
                logger.error("Check your internet connection and VPN status")
                if attempt < max_retries:
                    time.sleep(retry_delay)
                    continue
                return False
                
            except Exception as e:
                error_type = type(e).__name__
                error_msg = str(e)
                logger.error(f"Yahoo Finance validation error (attempt {attempt}/{max_retries}): {error_type}: {error_msg}")
                logger.debug("Full error traceback:", exc_info=True)
                
                if attempt < max_retries:
                    time.sleep(retry_delay)
                    continue
                return False
            finally:
                # Всегда восстанавливаем оригинальный requests
                try:
                    import yfinance.utils as yf_utils
                    yf_utils._requests = original_requests
                except:
                    pass
        
        logger.error(f"Symbol {self.symbol} validation failed after {max_retries} attempts")
        return False
    
    def clear_cache(self):
        """Очистка кэша свечных данных"""
        count = cache_service.clear('candles')
        logger.info(f"Candles cache cleared ({count} entries)")
    
    def get_candles_hash(self, timeframe: str = 'M15', period: Optional[str] = None, limit: Optional[int] = None) -> str:
        """
        Генерирует хеш для данных свечей (для проверки изменений)
        
        Args:
            timeframe: Таймфрейм
            period: Период данных
            limit: Количество свечей
            
        Returns:
            MD5 хеш параметров запроса
        """
        cache_key = cache_service._generate_key(
            'candles',
            self.symbol,
            timeframe=timeframe,
            period=period,
            limit=limit
        )
        return hashlib.md5(cache_key.encode()).hexdigest()


# Глобальный экземпляр сервиса
yfinance_service = YFinanceService()
