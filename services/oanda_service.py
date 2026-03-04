import requests
import logging
from datetime import datetime
from typing import Dict, List, Optional
# Не забудь добавить эти переменные в config/settings.py
from config.settings import OANDA_API_KEY, OANDA_ACCOUNT_ID, SYMBOL 
from services.cache_service import cache_service

logger = logging.getLogger(__name__)

class OandaService:
    def __init__(self, api_key=OANDA_API_KEY, account_id=OANDA_ACCOUNT_ID):
        self.api_key = api_key
        self.account_id = account_id
        # Для OANDA золото — это XAU_USD
        self.symbol = "XAU_USD"
        self.base_url = "https://api-fxpractice.oanda.com/v3" # Demo URL
        self.candles_cache_ttl = 30  # 30 секунд

    def get_candles(self, timeframe: str = 'M15', period: Optional[str] = None, limit: int = 100) -> Dict:
        """
        Получение свечных данных из OANDA v20.
        Полная совместимость с интерфейсом TwelveData.

        Важно:
        - Для минут/часов (M1,M5,M15,H1,H4,D1) используем кэш (TTL 30 сек).
        - Для секундных таймфреймов (например S5) кэш отключён, чтобы Price Monitor
          всегда работал с актуальной ценой.
        """
        timeframe_str = str(timeframe).upper() if timeframe is not None else 'M15'
        use_cache = not timeframe_str.startswith('S')

        # Проверяем кэш (только для минут/часов)
        cache_key = None
        if use_cache:
            cache_key = cache_service._generate_key(
                'candles_oanda',
                self.symbol,
                timeframe=timeframe_str,
                period=period,
                limit=limit
            )
            cached_data = cache_service.get(cache_key)
            if cached_data is not None:
                logger.info(f"✓ Returning cached OANDA candles ({cached_data.get('count', 0)} candles)")
                return cached_data

        try:
            # Мапим таймфреймы под стандарт OANDA
            # OANDA понимает S5, M1, M5, M15, H1, H4, D
            tf_map = {
                "S5": "S5",
                "M1": "M1",
                "M5": "M5",
                "M15": "M15",
                "H1": "H1",
                "H4": "H4",
                "D1": "D",
            }
            granularity = tf_map.get(timeframe_str, "M15")

            url = f"{self.base_url}/accounts/{self.account_id}/instruments/{self.symbol}/candles"
            
            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json"
            }
            
            params = {
                "count": limit,
                "granularity": granularity,
                "price": "M"  # Midpoint свечи (среднее между Bid и Ask)
            }

            logger.info(f"OANDA Request: {timeframe} (limit {limit}) - FETCHING FROM API")
            
            response = requests.get(url, headers=headers, params=params)
            
            if response.status_code != 200:
                error_msg = response.json().get('errorMessage', 'Unknown OANDA Error')
                logger.error(f"OANDA Error: {error_msg}")
                return {"error": error_msg}

            data = response.json()
            candles = []

            # OANDA отдает данные от СТАРЫХ к НОВЫМ (уже в нужном для графика порядке)
            for item in data.get("candles", []):
                # if not item.get("complete"): continue # Пропускаем неполные свечи если нужно

                mid = item["mid"]
                open_price = round(float(mid["o"]), 2)
                high_price = round(float(mid["h"]), 2)
                low_price = round(float(mid["l"]), 2)
                close_price = round(float(mid["c"]), 2)

                # Не фильтруем плоские свечи — TradingView их показывает.
                # Пропуск баров сдвигает индексы → уровни не совпадают с LuxAlgo.

                # OANDA отдает время в формате "2026-01-23T21:45:00.000000000Z"
                # Обрезаем наносекунды для корректного парсинга
                time_str = item["time"].split('.')[0].replace('Z', '')
                dt = datetime.strptime(time_str, "%Y-%m-%dT%H:%M:%S")
                
                candle_data = {
                    "time": int(dt.timestamp()),
                    "open": open_price,
                    "high": high_price,
                    "low": low_price,
                    "close": close_price,
                    "volume": int(item["volume"])
                }
                
                candles.append(candle_data)

            result = {
                "success": True,
                "symbol": self.symbol,
                "timeframe": timeframe,
                "candles": candles,
                "count": len(candles)
            }

            # Сохраняем в кэш только для минут/часов (не для S5 и других секундных)
            if use_cache and cache_key is not None:
                cache_service.set(cache_key, result, self.candles_cache_ttl)
                logger.info(f"✓ Cached OANDA response ({len(candles)} candles)")
            
            return result

        except Exception as e:
            logger.error(f"OandaService Exception: {str(e)}")
            return {"error": str(e)}

    def clear_cache(self):
        count = cache_service.clear('candles_oanda')
        logger.info(f"OANDA candles cache cleared ({count} entries)")

oanda_service = OandaService()