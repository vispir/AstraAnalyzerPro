"""
LLM Service для анализа торговых решений через OpenRouter
"""
import json
import requests
import logging
import base64
import re
from datetime import datetime, timezone
from typing import Dict, List, Any, Optional
from PIL import Image
import io

from config.settings import (
    OPENROUTER_API_KEY,
    GEMINI_API_KEY,
    AI_GATEWAY_URL,
    AI_GATEWAY_KEY,
    START_BALANCE,
    DAILY_LOSS_LIMIT,
    MAX_LOT_SIZE,
    RISK_PERCENT
)

logger = logging.getLogger(__name__)


def parse_json_response(response_text: str) -> Optional[Dict]:
    """
    Очищает ответ LLM от Markdown обёрток и парсит JSON
    
    Args:
        response_text: Сырой текст ответа от LLM
        
    Returns:
        Распарсенный JSON объект или None при ошибке
    """
    try:
        # 1. Удаляем Markdown обертку ```json и ```
        clean_text = re.sub(r"```json\s*", "", response_text)
        clean_text = re.sub(r"```\s*$", "", clean_text)
        
        # 2. Удаляем лишние пробелы по краям
        clean_text = clean_text.strip()
        
        # 3. Парсим JSON
        return json.loads(clean_text)
    except json.JSONDecodeError as e:
        logger.error(f"JSON Parse Error: {e}")
        logger.debug(f"Raw text: {response_text[:500]}...")
        return None


class TradingSession:
    """Определение текущей торговой сессии"""
    
    SESSIONS = {
        'Sydney': {'start': 21, 'end': 6},      # 21:00 - 06:00 UTC
        'Tokyo': {'start': 0, 'end': 9},        # 00:00 - 09:00 UTC
        'London': {'start': 7, 'end': 16},      # 07:00 - 16:00 UTC
        'New York': {'start': 12, 'end': 21},   # 12:00 - 21:00 UTC
    }
    
    OVERLAPS = {
        'London/New York': {'start': 12, 'end': 16},  # 12:00 - 16:00 UTC
        'Tokyo/London': {'start': 7, 'end': 9},       # 07:00 - 09:00 UTC
    }
    
    @classmethod
    def get_current_session(cls, utc_time: Optional[datetime] = None) -> Dict[str, Any]:
        """
        Определяет текущую торговую сессию
        
        Args:
            utc_time: время UTC (по умолчанию - текущее время)
            
        Returns:
            {
                'session': 'London',
                'overlap': 'London/New York' | None,
                'hour_utc': 14,
                'is_overlap': True/False
            }
        """
        if utc_time is None:
            utc_time = datetime.now(timezone.utc)
        
        hour = utc_time.hour
        
        # Проверяем overlaps (они приоритетнее)
        for overlap_name, times in cls.OVERLAPS.items():
            if cls._is_in_range(hour, times['start'], times['end']):
                primary_session = overlap_name.split('/')[0]  # Берем первую сессию
                return {
                    'session': primary_session,
                    'overlap': overlap_name,
                    'hour_utc': hour,
                    'is_overlap': True,
                    'description': f"{overlap_name} Overlap"
                }
        
        # Проверяем обычные сессии
        for session_name, times in cls.SESSIONS.items():
            if cls._is_in_range(hour, times['start'], times['end']):
                return {
                    'session': session_name,
                    'overlap': None,
                    'hour_utc': hour,
                    'is_overlap': False,
                    'description': f"{session_name} Session"
                }
        
        # Если не попали ни в одну сессию (между Sydney и Tokyo)
        return {
            'session': 'Off-Hours',
            'overlap': None,
            'hour_utc': hour,
            'is_overlap': False,
            'description': 'Off-Hours'
        }
    
    @staticmethod
    def _is_in_range(hour: int, start: int, end: int) -> bool:
        """Проверяет, попадает ли час в диапазон (с учетом перехода через полночь)"""
        if start <= end:
            return start <= hour < end
        else:  # Переход через полночь
            return hour >= start or hour < end


class LLMService:
    """
    Сервис для работы с LLM через OpenRouter, Gemini API или AI Gateway
    
    Поддерживаемые модели:
    - openrouter: google/gemini-2.0-flash-exp:free (требует OPENROUTER_API_KEY)
    - gemini3: gemini-3-pro-preview (Gemini 3 Pro через прямой API, требует GEMINI_API_KEY)
    - gateway: google/gemini-3-pro-preview через AI Gateway (требует AI_GATEWAY_URL)
    """
    
    # OpenRouter настройки
    OPENROUTER_API_URL = "https://openrouter.ai/api/v1/chat/completions"
    OPENROUTER_MODEL = "google/gemini-2.0-flash-exp:free"
    
    # Gemini настройки (прямой API)
    GEMINI_API_URL = "https://generativelanguage.googleapis.com/v1beta/models"
    GEMINI_MODEL = "gemini-3-pro-preview"  # Gemini 3 Pro
    
    # AI Gateway настройки
    GATEWAY_MODEL = "google/gemini-3-pro-preview"  # Модель для Gateway
    
    # Системный промпт
    SYSTEM_PROMPT = (
    "# ROLE & OBJECTIVE\n"
    "You are a Senior Intraday Trader specializing in Gold (XAUUSD). \n"
    "Your task is to execute high-probability trades by synthesizing Visual Price Action with Raw Data precision.\n"
    "Your operation mode is mechanical: Assess Context -> Identify Setup -> Validate Math -> Execute.\n"
    "\n"
    "# INPUT DATA CONTEXT\n"
    "<visuals>\n"
    "M15, H1, H4 Charts. Use these for pattern recognition (Structure, Sweeps) and trend alignment.\n"
    "</visuals>\n"
    "<raw_data>\n"
    "OHLCV Arrays. This is the **Source of Truth** for specific prices. Never guess prices from images.\n"
    "</raw_data>\n"
    "<computed_levels>\n"
    "PDH/PDL (Previous Day High/Low), Swing Points. Use these for structural targets.\n"
    "</computed_levels>\n"
    "<news>\n"
    "Economic Calendar and Sentiment data.\n"
    "</news>\n"
    "\n"
    "# EXECUTION ALGORITHM\n"
    "\n"
    "## 1. Global Filters (Safety Protocols)\n"
    "*   **Time Lock:** Check upcoming High Impact USD News. If time to news is < 45 minutes, STOP. Signal is **WAIT**.\n"
    "*   **Liquidity Check:** If current time is End of Asia or Late NY (low vol), STOP. Signal is **WAIT**.\n"
    "\n"
    "## 2. Narrative & Bias\n"
    "*   **Conflict Resolution:** Chart Structure > News Sentiment.\n"
    "*   **Regime:** Trending = Flow; Ranging = Premium/Discount.\n"
    "\n"
    "## 3. Setup Identification (Visual)\n"
    "Scan M15 for: Sweep & Reclaim, Displacement (High Vol), or OB Retest.\n"
    "*   If NO setup found -> Signal is **WAIT**.\n"
    "\n"
    "## 4. Precision Mapping (Only if Setup Found)\n"
    "*   **Trigger:** Index -1 Candle in Raw Data.\n"
    "*   **SL:** Structural pivot +/- $0.30 buffer.\n"
    "*   **TP:** Next Liquidity Pool or Fixed 1:3.\n"
    "\n"
    "## 5. Mathematical Validation (The Gatekeeper)\n"
    "*   Calculate R:R = Reward / Risk.\n"
    "*   **Constraint:** If Ratio < 1.5 -> Signal becomes **WAIT** (Reason: Poor R:R).\n"
    "\n"
    "# OUTPUT FORMAT (CONDITIONAL JSON)\n"
    "Respond with a strict JSON object. Choose ONE structure based on your decision.\n"
    "\n"
    "### SCENARIO A: ACTION IS 'BUY' OR 'SELL'\n"
    "{\n"
    '  "executive_summary": "Concise reasoning explaining the trigger and logic.",\n'
    '  "signal": {\n'
    '    "action": "BUY" | "SELL",\n'
    '    "confidence": 0-100,\n'
    '    "setup_type": "SESSION_SWEEP" | "TREND_FLOW" | "OB_RETEST"\n'
    "  },\n"
    '  "math_debug_log": {\n'
    '    "entry_price": Float,\n'
    '    "buffered_stop_loss": Float,\n'
    '    "target_price": Float,\n'
    '    "risk_amount": Float,\n'
    '    "reward_amount": Float,\n'
    '    "calculated_rr": Float\n'
    "  },\n"
    '  "trade_plan": {\n'
    '    "final_entry": Float,\n'
    '    "final_sl": Float,\n'
    '    "final_tp": Float,\n'
    '    "tp_logic": "LIQUIDITY_TARGET" | "BLUE_SKY_1_TO_3",\n'
    '    "invalidation_condition": "String"\n'
    "  }\n"
    "}\n"
    "\n"
    "### SCENARIO B: ACTION IS 'WAIT'\n"
    "{\n"
    '  "executive_summary": "Explain WHY we are waiting (e.g., News blackout, No setup, Poor R:R).",\n'
    '  "signal": {\n'
    '    "action": "WAIT"\n'
    "  },\n"
    '  "wait_metadata": {\n'
    '    "trigger_condition": "Specific price action needed to turn this into a trade (e.g., \'Break above 2040\').",\n'
    '    "estimated_wait_time": "String format: \'X minutes\' or \'Until [Event]\'. Do not be vague.",\n'
    '    "wait_reason_code": "NEWS_FILTER" | "POOR_RR" | "NO_SETUP" | "LOW_LIQUIDITY"\n'
    "  }\n"
    "}\n"
)
    
    def __init__(
        self, 
        openrouter_key: Optional[str] = None, 
        gemini_key: Optional[str] = None,
        gateway_url: Optional[str] = None,
        gateway_key: Optional[str] = None
    ):
        """
        Инициализация сервиса
        
        Args:
            openrouter_key: OpenRouter API ключ
            gemini_key: Gemini API ключ
            gateway_url: URL для AI Gateway
            gateway_key: API ключ для AI Gateway (опционально)
        """
        self.openrouter_key = openrouter_key
        self.gemini_key = gemini_key
        self.gateway_url = gateway_url
        self.gateway_key = gateway_key
        self.session = TradingSession()
        
        # Логируем статус API ключей
        if self.openrouter_key:
            logger.info("LLM Service: OpenRouter API key configured")
        if self.gemini_key:
            logger.info("LLM Service: Gemini API key configured")
        if self.gateway_url:
            logger.info(f"LLM Service: AI Gateway configured ({self.gateway_url})")
        if not self.openrouter_key and not self.gemini_key and not self.gateway_url:
            logger.warning("LLM Service: No API keys or Gateway configured!")
    
    def build_user_payload(
        self,
        current_time_utc: str,
        session_info: Dict[str, Any],
        news_data: Dict,
        technical_data: Dict,
        computed_levels: Dict,
        chart_images_b64: Dict[str, str]
    ) -> List[Any]:
        """
        Формирует payload для LLM модели
        
        Args:
            current_time_utc: Строка времени "YYYY-MM-DD HH:MM UTC"
            session_info: Информация о сессии из get_current_session()
            news_data: JSON с новостями
            technical_data: JSON с OHLCV и Algo-SMC
            computed_levels: JSON с PDH/PDL и свингами
            chart_images_b64: Словарь с base64 картинками {'H4': '...', 'H1': '...', 'M15': '...'}
        
        Returns:
            List с текстовым контекстом и изображениями
        """
        
        # 1. Формируем текстовую часть (контекст)
        user_prompt_text = f"""
REPORT GENERATION REQUEST for XAUUSD

<environment>
Current Time (UTC): {current_time_utc}
Active Session: {session_info['description']}
Session Details: {json.dumps(session_info, indent=2)}
</environment>

<account_config>
Balance: ${START_BALANCE}
Daily Loss Limit: ${DAILY_LOSS_LIMIT}
Max Lot Size: {MAX_LOT_SIZE}
Risk Per Trade: {RISK_PERCENT * 100}% (${START_BALANCE * RISK_PERCENT})
</account_config>

<news_context>
{json.dumps(news_data, indent=2)}
</news_context>

<computed_levels>
IMPORTANT: Use these exact values for Targets (TP) and Structural Stops (SL).
{json.dumps(computed_levels, indent=2)}
</computed_levels>

<raw_technical_data>
Contains OHLCV arrays and Algo-Levels for M15, H1, H4. 
Use this data to find the specific High/Low/Close of the trigger candle.
{json.dumps(technical_data, indent=2)}
</raw_technical_data>

<visual_attachments>
The following images are attached to this request in order:
1. H4 Chart (Macro Trend & Narrative)
2. H1 Chart (Intermediate Structure)
3. M15 Chart (Execution & Trigger)
</visual_attachments>

<task>
1. Synthesize the News, Visuals, and Data.
2. Validate the setup using the "Decision Protocol" from System Instructions.
3. Output the STRICT JSON decision with the required fields.
</task>
"""
        
        # 2. Подготавливаем картинки
        images = []
        try:
            # Порядок важен! Он должен совпадать с описанием в <visual_attachments>
            order = ['H4', 'H1', 'M15']
            
            for tf in order:
                b64_str = chart_images_b64.get(tf) or chart_images_b64.get(tf.lower())
                if b64_str:
                    # Для OpenRouter нужен data URI формат
                    images.append({
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/png;base64,{b64_str}"
                        }
                    })
                else:
                    logger.warning(f"Missing chart image for {tf}")
        
        except Exception as e:
            logger.error(f"Error processing images: {e}")
            return None
        
        # 3. Собираем финальный payload для OpenRouter
        # Формат: [{"type": "text", "text": "..."}, {"type": "image_url", "image_url": {...}}, ...]
        content = [{"type": "text", "text": user_prompt_text}] + images
        
        return content
    
    def analyze_trading_decision(
        self,
        technical_data: Dict,
        news_data: Dict,
        computed_levels: Dict,
        chart_images: Dict[str, str],
        model: str = "openrouter"
    ) -> Dict[str, Any]:
        """
        Отправляет данные в LLM для анализа торгового решения
        
        Args:
            technical_data: Технические данные (свечи, SMC уровни)
            news_data: Новости
            computed_levels: Вычисленные уровни (PDH/PDL, свинги)
            chart_images: Изображения графиков в base64
            model: Выбор модели - "openrouter", "gemini3" или "gateway"
        
        Returns:
            Ответ от LLM модели
        """
        if model == "gemini3":
            return self._analyze_with_gemini(technical_data, news_data, computed_levels, chart_images)
        elif model == "gateway":
            return self._analyze_with_gateway(technical_data, news_data, computed_levels, chart_images)
        else:
            return self._analyze_with_openrouter(technical_data, news_data, computed_levels, chart_images)
    
    def _analyze_with_openrouter(
        self,
        technical_data: Dict,
        news_data: Dict,
        computed_levels: Dict,
        chart_images: Dict[str, str]
    ) -> Dict[str, Any]:
        """Анализ через OpenRouter API"""
        try:
            # Получаем текущую сессию
            current_time = datetime.now(timezone.utc)
            session_info = self.session.get_current_session(current_time)
            time_str = current_time.strftime("%Y-%m-%d %H:%M UTC")
            
            # Формируем user payload
            user_content = self.build_user_payload(
                current_time_utc=time_str,
                session_info=session_info,
                news_data=news_data,
                technical_data=technical_data,
                computed_levels=computed_levels,
                chart_images_b64=chart_images
            )
            
            if user_content is None:
                return {"error": "Failed to build user payload"}
            
            # Формируем запрос к OpenRouter
            headers = {
                "Content-Type": "application/json",
            }
            
            # Добавляем API ключ если есть
            if self.openrouter_key:
                headers["Authorization"] = f"Bearer {self.openrouter_key}"
                logger.info("Using OpenRouter API key for authentication")
            else:
                # Для работы без API ключа добавляем идентификационные заголовки
                headers["HTTP-Referer"] = "https://github.com/astra-analyzer-pro"
                headers["X-Title"] = "Astra Analyzer Pro - Trading Bot"
                logger.warning("No OpenRouter API key found! Using free tier with referrer headers")
            
            payload = {
                "model": self.OPENROUTER_MODEL,
                "messages": [
                    {
                        "role": "system",
                        "content": self.SYSTEM_PROMPT
                    },
                    {
                        "role": "user",
                        "content": user_content
                    }
                ]
            }
            
            logger.info(f"Sending request to OpenRouter (model: {self.OPENROUTER_MODEL})")
            
            # Отправляем запрос
            response = requests.post(
                self.OPENROUTER_API_URL,
                headers=headers,
                json=payload,
                timeout=60
            )
            
            response.raise_for_status()
            result = response.json()
            
            logger.info("Successfully received response from OpenRouter")
            
            # Извлекаем ответ модели
            if 'choices' in result and len(result['choices']) > 0:
                assistant_message = result['choices'][0]['message']['content']
                
                # Пытаемся распарсить JSON из ответа (если LLM вернул JSON)
                parsed_json = parse_json_response(assistant_message)
                
                response_data = {
                    "success": True,
                    "model": self.OPENROUTER_MODEL,
                    "session_info": session_info,
                    "timestamp": time_str,
                    "response": assistant_message,
                    "usage": result.get('usage', {}),
                    "raw_response": result
                }
                
                # Добавляем распарсенный JSON если удалось
                if parsed_json:
                    response_data["parsed_decision"] = parsed_json
                    logger.info("Successfully parsed JSON from LLM response")
                
                return response_data
            else:
                return {
                    "error": "Invalid response format from OpenRouter",
                    "raw_response": result
                }
        
        except requests.exceptions.RequestException as e:
            logger.error(f"Request error: {str(e)}")
            return {
                "error": "Failed to connect to OpenRouter",
                "details": str(e)
            }
        except Exception as e:
            logger.error(f"Error in analyze_trading_decision: {str(e)}", exc_info=True)
            return {
                "error": "Internal error during LLM analysis",
                "details": str(e)
            }
    
    def _analyze_with_gemini(
        self,
        technical_data: Dict,
        news_data: Dict,
        computed_levels: Dict,
        chart_images: Dict[str, str]
    ) -> Dict[str, Any]:
        """Анализ через Gemini API"""
        try:
            if not self.gemini_key:
                return {"error": "GEMINI_API_KEY not configured"}
            
            # Получаем текущую сессию
            current_time = datetime.now(timezone.utc)
            session_info = self.session.get_current_session(current_time)
            time_str = current_time.strftime("%Y-%m-%d %H:%M UTC")
            
            # Формируем текстовый промпт (без изображений для Gemini)
            user_prompt_text = f"""
REPORT GENERATION REQUEST for XAUUSD

<environment>
Current Time (UTC): {time_str}
Active Session: {session_info['description']}
Session Details: {json.dumps(session_info, indent=2)}
</environment>

<account_config>
Balance: ${START_BALANCE}
Daily Loss Limit: ${DAILY_LOSS_LIMIT}
Max Lot Size: {MAX_LOT_SIZE}
Risk Per Trade: {RISK_PERCENT * 100}% (${START_BALANCE * RISK_PERCENT})
</account_config>

<news_context>
{json.dumps(news_data, indent=2)}
</news_context>

<computed_levels>
IMPORTANT: Use these exact values for Targets (TP) and Structural Stops (SL).
{json.dumps(computed_levels, indent=2)}
</computed_levels>

<raw_technical_data>
Contains OHLCV arrays and Algo-Levels for M15, H1, H4. 
Use this data to find the specific High/Low/Close of the trigger candle.
{json.dumps(technical_data, indent=2)}
</raw_technical_data>

<task>
1. Synthesize the News, Data, and Technical Analysis.
2. Validate the setup using the "Decision Protocol" from System Instructions.
3. Output the STRICT JSON decision with the required fields.
</task>
"""
            
            # Формируем полный промпт (системный + пользовательский)
            full_prompt = self.SYSTEM_PROMPT + "\n\n" + user_prompt_text
            
            logger.info(f"Sending request to Gemini (model: {self.GEMINI_MODEL})")
            
            # Запрос к Gemini API
            url = f"{self.GEMINI_API_URL}/{self.GEMINI_MODEL}:generateContent?key={self.gemini_key}"
            
            response = requests.post(
                url,
                json={"contents": [{"parts": [{"text": full_prompt}]}]},
                headers={"Content-Type": "application/json"},
                timeout=60
            )
            
            if response.status_code == 429:
                logger.warning("Gemini API rate limit exceeded")
                return {"error": "Gemini API rate limit exceeded", "status": 429}
            
            if response.status_code != 200:
                logger.error(f"Gemini API error: {response.status_code}")
                return {"error": f"Gemini API error: {response.status_code}", "status": response.status_code}
            
            result = response.json()
            
            logger.info("Successfully received response from Gemini")
            
            # Извлекаем ответ модели
            if result.get('candidates') and result['candidates'][0].get('content'):
                assistant_message = result['candidates'][0]['content']['parts'][0]['text']
                
                # Пытаемся распарсить JSON из ответа
                parsed_json = parse_json_response(assistant_message)
                
                response_data = {
                    "success": True,
                    "model": self.GEMINI_MODEL,
                    "session_info": session_info,
                    "timestamp": time_str,
                    "response": assistant_message,
                    "usage": result.get('usageMetadata', {}),
                    "raw_response": result
                }
                
                # Добавляем распарсенный JSON если удалось
                if parsed_json:
                    response_data["parsed_decision"] = parsed_json
                    logger.info("Successfully parsed JSON from Gemini response")
                
                return response_data
            else:
                logger.error("Invalid Gemini API response structure")
                return {"error": "Invalid Gemini API response structure"}
        
        except requests.exceptions.Timeout:
            logger.error("Gemini API timeout")
            return {"error": "Gemini API timeout", "status": 504}
        except requests.exceptions.RequestException as e:
            logger.error(f"Request error: {str(e)}")
            return {
                "error": "Failed to connect to Gemini",
                "details": str(e)
            }
        except Exception as e:
            logger.error(f"Error in _analyze_with_gemini: {str(e)}", exc_info=True)
            return {
                "error": "Internal error during Gemini analysis",
                "details": str(e)
            }
    
    def _analyze_with_gateway(
        self,
        technical_data: Dict,
        news_data: Dict,
        computed_levels: Dict,
        chart_images: Dict[str, str]
    ) -> Dict[str, Any]:
        """Анализ через AI Gateway"""
        try:
            if not self.gateway_url:
                return {"error": "AI_GATEWAY_URL not configured"}
            
            # Формируем правильный URL (добавляем /chat/completions если нужно)
            gateway_url = self.gateway_url
            if not gateway_url.endswith('/chat/completions'):
                if gateway_url.endswith('/'):
                    gateway_url = gateway_url + 'chat/completions'
                else:
                    gateway_url = gateway_url + '/chat/completions'
            
            # Получаем текущую сессию
            current_time = datetime.now(timezone.utc)
            session_info = self.session.get_current_session(current_time)
            time_str = current_time.strftime("%Y-%m-%d %H:%M UTC")
            
            # Формируем user payload (с изображениями как в OpenRouter)
            user_content = self.build_user_payload(
                current_time_utc=time_str,
                session_info=session_info,
                news_data=news_data,
                technical_data=technical_data,
                computed_levels=computed_levels,
                chart_images_b64=chart_images
            )
            
            if user_content is None:
                return {"error": "Failed to build user payload"}
            
            # Формируем запрос к AI Gateway
            headers = {
                "Content-Type": "application/json",
            }
            
            # Добавляем API ключ если есть
            if self.gateway_key:
                headers["Authorization"] = f"Bearer {self.gateway_key}"
                logger.info("Using AI Gateway API key for authentication")
            else:
                logger.info("Using AI Gateway without API key")
            
            payload = {
                "model": self.GATEWAY_MODEL,
                "messages": [
                    {
                        "role": "system",
                        "content": self.SYSTEM_PROMPT
                    },
                    {
                        "role": "user",
                        "content": user_content
                    }
                ]
            }
            
            logger.info(f"Sending request to AI Gateway ({gateway_url}) with model: {self.GATEWAY_MODEL}")
            
            # Отправляем запрос
            response = requests.post(
                gateway_url,
                headers=headers,
                json=payload,
                timeout=60
            )
            
            response.raise_for_status()
            result = response.json()
            
            logger.info("Successfully received response from AI Gateway")
            
            # Извлекаем ответ модели (формат как у OpenRouter)
            if 'choices' in result and len(result['choices']) > 0:
                assistant_message = result['choices'][0]['message']['content']
                
                # Пытаемся распарсить JSON из ответа
                parsed_json = parse_json_response(assistant_message)
                
                response_data = {
                    "success": True,
                    "model": f"AI Gateway ({self.GATEWAY_MODEL})",
                    "session_info": session_info,
                    "timestamp": time_str,
                    "response": assistant_message,
                    "usage": result.get('usage', {}),
                    "raw_response": result
                }
                
                # Добавляем распарсенный JSON если удалось
                if parsed_json:
                    response_data["parsed_decision"] = parsed_json
                    logger.info("Successfully parsed JSON from AI Gateway response")
                
                return response_data
            else:
                return {
                    "error": "Invalid response format from AI Gateway",
                    "raw_response": result
                }
        
        except requests.exceptions.RequestException as e:
            logger.error(f"Request error: {str(e)}")
            return {
                "error": "Failed to connect to AI Gateway",
                "details": str(e)
            }
        except Exception as e:
            logger.error(f"Error in _analyze_with_gateway: {str(e)}", exc_info=True)
            return {
                "error": "Internal error during AI Gateway analysis",
                "details": str(e)
            }
    
    def get_session_info(self) -> Dict[str, Any]:
        """Получить информацию о текущей торговой сессии"""
        return self.session.get_current_session()


# Синглтон инстанс
llm_service = LLMService(
    openrouter_key=OPENROUTER_API_KEY, 
    gemini_key=GEMINI_API_KEY,
    gateway_url=AI_GATEWAY_URL,
    gateway_key=AI_GATEWAY_KEY
)
