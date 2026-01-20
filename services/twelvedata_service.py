import requests
import logging
from datetime import datetime
from typing import Dict, List, Optional
from config.settings import TWELVE_DATA_API_KEY, SYMBOL

logger = logging.getLogger(__name__)

class TwelveDataService:
    def __init__(self, api_key=TWELVE_DATA_API_KEY):
        self.api_key = api_key
        self.base_url = "https://api.twelvedata.com"
        # Для Twelve Data золото обычно это XAU/USD
        self.symbol = "XAU/USD" 

    def get_candles(self, timeframe: str = 'M15', period: Optional[str] = None, limit: int = 100) -> Dict:
        """
        Получение свечных данных из Twelve Data.
        Добавлен аргумент 'period' для совместимости с интерфейсом Yahoo.
        """
        try:
            # Мапим таймфреймы под стандарт Twelve Data
            tf_map = {"M15": "15min", "H1": "1h", "H4": "4h"}
            interval = tf_map.get(timeframe, "15min")
            
            url = f"{self.base_url}/time_series"
            params = {
                "symbol": self.symbol,
                "interval": interval,
                "outputsize": limit,
                "apikey": self.api_key
            }
            
            logger.info(f"TwelveData Request: {timeframe} (limit {limit})")
            
            response = requests.get(url, params=params)
            data = response.json()
            
            if data.get("status") != "ok":
                logger.error(f"TwelveData Error: {data.get('message')}")
                return {"error": data.get("message")}

            candles = []
            # Twelve Data отдает данные от НОВЫХ к СТАРЫМ
            for item in data["values"]:
                open_price = round(float(item["open"]), 2)
                high_price = round(float(item["high"]), 2)
                low_price = round(float(item["low"]), 2)
                close_price = round(float(item["close"]), 2)
                
                # Фильтруем плоские (фантомные) свечи - когда рынок закрыт
                # Плоская свеча = когда high и low одинаковые (или разница очень мала)
                price_range = high_price - low_price
                
                # Пропускаем свечи, где нет движения цены (плоские свечи)
                if price_range < 0.01:  # Если разница меньше 0.01, считаем свечу плоской
                    continue
                
                candles.append({
                    "time": int(datetime.strptime(item["datetime"], "%Y-%m-%d %H:%M:%S").timestamp()),
                    "open": open_price,
                    "high": high_price,
                    "low": low_price,
                    "close": close_price,
                    "volume": int(item.get("volume", 0))
                })
            
            # Переворачиваем массив для графика
            candles.reverse() 
            
            return {
                "success": True,
                "symbol": self.symbol,
                "timeframe": timeframe,
                "candles": candles
            }
        except Exception as e:
            logger.error(f"TwelveData Exception: {str(e)}")
            return {"error": str(e)}

twelvedata_service = TwelveDataService()