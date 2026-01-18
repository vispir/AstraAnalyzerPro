"""
Сервис для работы с Yahoo Finance API
"""
import yfinance as yf
import logging
from datetime import datetime
from typing import Dict, List, Optional

from config.settings import (
    YAHOO_SYMBOL, 
    TIMEFRAME_MAP, 
    PERIOD_MAP,
    AI_CONTEXT_BARS
)

logger = logging.getLogger(__name__)


class YFinanceService:
    """Сервис для получения рыночных данных через Yahoo Finance"""
    
    def __init__(self, symbol: str = YAHOO_SYMBOL):
        self.symbol = symbol
        self.ticker = yf.Ticker(symbol)
        
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
        try:
            # Получаем интервал для Yahoo Finance
            interval = TIMEFRAME_MAP.get(timeframe, '15m')
            if period is None:
                period = PERIOD_MAP.get(timeframe, '5d')
            
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
                candles.append({
                    "time": int(index.timestamp()),
                    "open": float(row['Open']),
                    "high": float(row['High']),
                    "low": float(row['Low']),
                    "close": float(row['Close']),
                    "volume": int(row['Volume']) if 'Volume' in row else 0
                })
            
            logger.info(f"Successfully fetched {len(candles)} candles")
            
            return {
                "success": True,
                "symbol": self.symbol,
                "timeframe": timeframe,
                "candles": candles,
                "count": len(candles)
            }
            
        except Exception as e:
            logger.error(f"Error fetching candles: {str(e)}")
            return {"error": f"Ошибка получения данных: {str(e)}"}
    
    def get_ai_context(self) -> Dict:
        """
        Получение сокращенных данных для AI анализа (несколько таймфреймов)
        
        Returns:
            Dict с данными по разным таймфреймам
        """
        try:
            context = {}
            
            for tf, bars_count in AI_CONTEXT_BARS.items():
                interval = TIMEFRAME_MAP.get(tf, '15m')
                period = PERIOD_MAP.get(tf, '5d')
                
                df = self.ticker.history(period=period, interval=interval)
                
                if not df.empty:
                    # Берем только последние N свечей
                    df_last = df.tail(bars_count)
                    
                    # Сокращенный формат (только H, L, C)
                    context[tf] = [
                        {
                            "h": float(row['High']),
                            "l": float(row['Low']),
                            "c": float(row['Close'])
                        }
                        for _, row in df_last.iterrows()
                    ]
                else:
                    context[tf] = []
                    
            return context
            
        except Exception as e:
            logger.error(f"Error fetching AI context: {str(e)}")
            return {
                "M15": [],
                "H1": [],
                "H4": []
            }
    
    def get_current_price(self) -> Optional[float]:
        """
        Получение текущей цены
        
        Returns:
            Текущая цена или None
        """
        try:
            # Получаем последнюю свечу на минутном интервале
            df = self.ticker.history(period='1d', interval='1m')
            if not df.empty:
                return float(df['Close'].iloc[-1])
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


# Глобальный экземпляр сервиса
yfinance_service = YFinanceService()
