"""
Сервис для работы с Yahoo Finance API
"""
import yfinance as yf
import logging
import hashlib
import json
from datetime import datetime
from typing import Dict, List, Optional

from config.settings import (
    YAHOO_SYMBOL, 
    TIMEFRAME_MAP, 
    PERIOD_MAP
)
from services.cache_service import cache_service

logger = logging.getLogger(__name__)


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
            logger.info(f"Returning cached candles data ({cached_data['count']} candles)")
            return cached_data
        
        try:
            logger.info(f"Fetching {self.symbol} data: interval={interval}, period={period}, limit={limit}")
            
            # Получаем данные
            df = self.ticker.history(period=period, interval=interval)
            
            if df.empty:
                logger.error(f"No data received for {self.symbol}")
                return {"error": f"Нет данных для {timeframe}"}
            
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
                    chunk = df.iloc[i:i+4]
                    if len(chunk) == 4:  # Только полные 4-часовые блоки
                        h4_candle = {
                            'Open': chunk.iloc[0]['Open'],
                            'High': chunk['High'].max(),
                            'Low': chunk['Low'].min(),
                            'Close': chunk.iloc[-1]['Close'],
                            'Volume': chunk['Volume'].sum()
                        }
                        # Используем timestamp последней свечи в блоке
                        df_h4_list.append((chunk.index[-1], h4_candle))
                
                # Создаем новый DataFrame из агрегированных данных
                if df_h4_list:
                    import pandas as pd
                    df = pd.DataFrame([candle for _, candle in df_h4_list], 
                                     index=[ts for ts, _ in df_h4_list])
            else:
                # Применяем лимит для остальных таймфреймов
                if limit and limit > 0:
                    df = df.tail(limit)
            
            # Конвертируем в формат для frontend
            candles = []
            for index, row in df.iterrows():
                candle_data = {
                    "time": int(index.timestamp()),
                    "open": round(float(row['Open']), 2),
                    "high": round(float(row['High']), 2),
                    "low": round(float(row['Low']), 2),
                    "close": round(float(row['Close']), 2)
                }
                
                # Добавляем volume только если он не 0
                volume = int(row['Volume']) if 'Volume' in row else 0
                if volume > 0:
                    candle_data["volume"] = volume
                    
                candles.append(candle_data)
            
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
            
            return result
            
        except Exception as e:
            logger.error(f"Error fetching candles: {str(e)}")
            return {"error": f"Ошибка получения данных: {str(e)}"}
    
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
        Проверка доступности символа
        
        Returns:
            True если символ доступен
        """
        try:
            df = self.ticker.history(period='1d', interval='1h')
            return not df.empty
        except Exception:
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
