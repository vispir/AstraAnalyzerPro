import yfinance as yf
import logging
import pandas as pd
import os
from datetime import datetime
from typing import Dict, List, Optional

from config.settings import (
    YAHOO_SYMBOL, 
    TIMEFRAME_MAP, 
    PERIOD_MAP
)

# Фикс для кириллицы: принудительно отключаем проверку сертификатов на уровне ОС
os.environ['WDM_SSL_VERIFY'] = '0'
os.environ['CURL_CA_BUNDLE'] = ''

logger = logging.getLogger(__name__)

class YFinanceService:
    def __init__(self, symbol: str = YAHOO_SYMBOL):
        self.symbol = symbol
        # Мы НЕ создаем сессию здесь, чтобы не злить yfinance
        self.ticker = yf.Ticker(symbol)
        
    def get_candles(self, timeframe: str = 'M15', period: Optional[str] = None, limit: Optional[int] = None) -> Dict:
        try:
            interval = TIMEFRAME_MAP.get(timeframe, '15m')
            if period is None:
                period = PERIOD_MAP.get(timeframe, '5d')
            
            # ВАЖНО: просим yfinance НЕ использовать curl_cffi (проблема кириллицы)
            # Мы просто качаем данные напрямую
            df = yf.download(
                self.symbol, 
                period=period, 
                interval=interval, 
                progress=False, 
                ignore_tz=True
            )
            
            if df is None or df.empty:
                return {"error": "Нет данных. Возможно, нужен VPN."}
            
            # Логика H4 от Роберта
            if timeframe == 'H4':
                if limit: df = df.tail(limit * 4)
                df_h4_list = []
                for i in range(0, len(df), 4):
                    chunk = df.iloc[i:i+4]
                    if len(chunk) == 4:
                        h4_candle = {
                            'Open': chunk.iloc[0]['Open'],
                            'High': chunk['High'].max(),
                            'Low': chunk['Low'].min(),
                            'Close': chunk.iloc[-1]['Close'],
                            'Volume': chunk['Volume'].sum()
                        }
                        df_h4_list.append((chunk.index[-1], h4_candle))
                if df_h4_list:
                    df = pd.DataFrame([c for _, c in df_h4_list], index=[ts for ts, _ in df_h4_list])
            else:
                if limit: df = df.tail(limit)
            
            candles = []
            for index, row in df.iterrows():
                def get_val(val):
                    return val.iloc[0] if hasattr(val, 'iloc') else val
                
                open_price = round(float(get_val(row['Open'])), 2)
                high_price = round(float(get_val(row['High'])), 2)
                low_price = round(float(get_val(row['Low'])), 2)
                close_price = round(float(get_val(row['Close'])), 2)
                
                # Фильтруем плоские (фантомные) свечи - когда рынок закрыт
                # Плоская свеча = когда high и low одинаковые (или разница очень мала)
                price_range = high_price - low_price
                
                # Пропускаем свечи, где нет движения цены (плоские свечи)
                if price_range < 0.01:  # Если разница меньше 0.01, считаем свечу плоской
                    continue
                
                candles.append({
                    "time": int(index.timestamp()),
                    "open": open_price,
                    "high": high_price,
                    "low": low_price,
                    "close": close_price,
                    "volume": int(float(get_val(row['Volume']))) if 'Volume' in row else 0
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
            logger.error(f"Error: {str(e)}")
            return {"error": str(e)}

    def get_ai_context(self) -> Dict:
        # Простое получение данных для ИИ
        context = {}
        for tf, count in AI_CONTEXT_BARS.items():
            try:
                df = yf.download(self.symbol, period="5d", interval=TIMEFRAME_MAP.get(tf, '15m'), progress=False)
                if not df.empty:
                    last = df.tail(count)
                    context[tf] = [{"h": float(r['High']), "l": float(r['Low']), "c": float(r['Close'])} for _, r in last.iterrows()]
                else: context[tf] = []
            except: context[tf] = []
        return context

    def validate_symbol(self) -> bool:
        try:
            df = yf.download(self.symbol, period="1d", interval="1h", progress=False)
            return not df.empty
        except: return False

yfinance_service = YFinanceService()