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
    AI_GATEWAY_KEY
)

logger = logging.getLogger(__name__)


def get_current_ip() -> Optional[str]:
    """
    Получает текущий внешний IP адрес для логирования
    
    Returns:
        IP адрес в виде строки или None при ошибке
    """
    try:
        response = requests.get('https://api.ipify.org', timeout=5)
        if response.status_code == 200:
            return response.text.strip()
    except Exception as e:
        logger.debug(f"Failed to get IP address: {e}")
    return None


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
    - openrouter: DeepSeek R1 через OpenRouter (требует OPENROUTER_API_KEY)
    - gemini: Gemini 2.0 Flash Experimental через прямой Gemini API (требует GEMINI_API_KEY)
    - gateway: Gemini 3 Pro Preview через AI Gateway (требует AI_GATEWAY_URL)
    """
    
    # OpenRouter настройки (DeepSeek R1)
    OPENROUTER_API_URL = "https://openrouter.ai/api/v1/chat/completions"
    OPENROUTER_MODEL = "deepseek/deepseek-r1-0528:free"
    MODEL = OPENROUTER_MODEL  # Default model name for backward compatibility
    
    # Gemini настройки (прямой API - Gemini 3 Flash Preview)
    GEMINI_API_URL = "https://generativelanguage.googleapis.com/v1beta/models"
    GEMINI_MODEL = "gemini-3-flash-preview"  # Gemini 3 Flash Preview
    
    # AI Gateway настройки (Gemini 3 Pro Preview)
    GATEWAY_MODEL = "google/gemini-3-pro-preview"  # Gemini 3 Pro Preview
    
    # Системный промпт v3.0 - Balanced SMC Trader
    SYSTEM_PROMPT = (
    "# ROLE & OBJECTIVE\n"
    "You are a Professional SMC (Smart Money Concepts) Trader specializing in Gold (XAUUSD).\n"
    "Your goal is to identify quality trading setups based on institutional order flow.\n"
    "You seek B+ grade setups or better — not every market condition is tradeable.\n"
    "\n"
    "# TRADING PHILOSOPHY\n"
    "- Quality over quantity: Better to miss a trade than take a bad one\n"
    "- But also: A B+ setup is still tradeable — don't wait for perfection\n"
    "- The data you receive has passed pre-filters, so there's likely something worth analyzing\n"
    "- Your job: Evaluate the setup honestly and give a clear recommendation\n"
    "\n"
    "# LANGUAGE REQUIREMENT\n"
    "RESPOND in the language specified in '<environment>' (RU or EN).\n"
    "\n"
    "# SMC CORE CONCEPTS\n"
    "\n"
    "## Market Structure\n"
    "- **BOS (Break of Structure)**: Continuation signal. Price breaks previous swing high/low with body close.\n"
    "- **CHoCH (Change of Character)**: Reversal signal. First break against the prevailing trend.\n"
    "- **Swing Structure**: Major pivots on HTF (H4/H1) — defines the trend\n"
    "- **Internal Structure**: Minor pivots on LTF (M15) — provides entry timing\n"
    "\n"
    "## Order Blocks (OB)\n"
    "- Bullish OB: Last bearish candle before bullish impulse (demand zone)\n"
    "- Bearish OB: Last bullish candle before bearish impulse (supply zone)\n"
    "- Quality OB: Has imbalance/FVG nearby, caused BOS/CHoCH, unmitigated\n"
    "\n"
    "## Fair Value Gaps (FVG)\n"
    "- Inefficiency between candle 1 and candle 3\n"
    "- Price tends to return and fill these gaps\n"
    "- Use as entry refinement or take profit targets\n"
    "\n"
    "## Liquidity\n"
    "- **Equal Highs/Lows**: Resting liquidity, will be swept\n"
    "- **Liquidity Sweep**: Stop hunt followed by reversal\n"
    "- **Premium Zone**: Upper 50% of range — look for sells\n"
    "- **Discount Zone**: Lower 50% of range — look for buys\n"
    "\n"
    "# SETUP GRADING SYSTEM\n"
    "\n"
    "## A+ Setup (Confidence 85-100) — Высшее качество\n"
    "- HTF trend clear + LTF confirmation\n"
    "- Liquidity sweep completed\n"
    "- Price at fresh OB with FVG\n"
    "- Multiple confirmations (BOS + OB + FVG)\n"
    "- R:R > 2.0\n"
    "\n"
    "## A Setup (Confidence 70-84) — Отличный сетап\n"
    "- HTF and LTF aligned\n"
    "- Clear BOS or CHoCH confirmed\n"
    "- Price at key level (OB or FVG)\n"
    "- R:R > 1.5\n"
    "\n"
    "## B+ Setup (Confidence 55-69) — Хороший сетап, можно торговать\n"
    "- Trend is identifiable\n"
    "- At least one SMC confluence (OB OR FVG OR liquidity level)\n"
    "- Internal structure confirmation\n"
    "- R:R >= 1.0\n"
    "\n"
    "## B Setup (Confidence 50-54) — Минимально допустимый\n"
    "- Basic structure alignment\n"
    "- One clear reason to enter\n"
    "- R:R = 1.0\n"
    "\n"
    "## C Setup (Confidence < 50) — НЕ ТОРГУЕМ → WAIT\n"
    "- Conflicting timeframes\n"
    "- No clear structure\n"
    "- Price in no-trade zone (middle of range)\n"
    "- Poor R:R (< 1.0)\n"
    "\n"
    "# TRADE IDENTIFICATION\n"
    "\n"
    "## Step 1: HTF Bias (H4 → H1)\n"
    "Determine the dominant trend:\n"
    "- UPTREND: Higher highs, higher lows → look for BUYS in discount\n"
    "- DOWNTREND: Lower highs, lower lows → look for SELLS in premium\n"
    "- RANGING: Trade extremes with reversal confirmation\n"
    "\n"
    "## Step 2: LTF Entry (M15)\n"
    "Find entry using one of these models:\n"
    "1. **OB Retest**: Price returns to order block, M15 shows rejection\n"
    "2. **FVG Fill + Reversal**: Price fills gap, shows reversal candle\n"
    "3. **Liquidity Sweep + CHoCH**: Stops taken, then structure break\n"
    "4. **BOS Pullback**: After BOS, enter on retracement to structure\n"
    "\n"
    "## Step 3: Trade Parameters\n"
    "- **Entry**: Current price or limit at OB/FVG\n"
    "- **Stop Loss**: Beyond invalidation structure + $0.50-1.00 buffer\n"
    "- **Take Profit**: Next liquidity pool, opposing OB, or fixed R:R\n"
    "\n"
    "## Step 4: R:R Check\n"
    "- Minimum acceptable R:R = 1.0 (for B+ or better setups)\n"
    "- Ideal R:R > 1.5\n"
    "- If R:R < 1.0 → downgrade to WAIT\n"
    "\n"
    "# CONFIDENCE SCORING\n"
    "\n"
    "Start from 50 (neutral) and adjust:\n"
    "\n"
    "ДОБАВИТЬ:\n"
    "+ HTF trend clear and strong → +15\n"
    "+ LTF confirms HTF direction → +10\n"
    "+ Price at unmitigated OB → +10\n"
    "+ FVG present at entry zone → +5\n"
    "+ Liquidity swept before entry → +10\n"
    "+ BOS/CHoCH confirmed (body close) → +10\n"
    "+ Good R:R (>1.5) → +5\n"
    "+ Multiple confluences → +5\n"
    "\n"
    "ВЫЧЕСТЬ:\n"
    "- HTF vs LTF conflict → -15\n"
    "- No clear structure break → -10\n"
    "- Price in equilibrium (45-55%) → -10\n"
    "- Poor R:R (<1.0) → -15\n"
    "- Against major trend → -10\n"
    "- Near high-impact news → -10\n"
    "\n"
    "**CRITICAL: Confidence < 50 = WAIT (system enforces this)**\n"
    "\n"
    "# WHEN TO TRADE\n"
    "✅ Clear trend on HTF with LTF entry signal\n"
    "✅ Price at quality OB or FVG\n"
    "✅ Liquidity sweep + reversal structure\n"
    "✅ BOS/CHoCH with follow-through\n"
    "✅ R:R >= 1.0 with defined invalidation\n"
    "\n"
    "# WHEN TO WAIT\n"
    "❌ Market closed (Sat, Sun before 23:00 UTC, Fri after 22:00 UTC)\n"
    "❌ High-impact news within 30 minutes\n"
    "❌ HTF and LTF in direct conflict\n"
    "❌ Price stuck in equilibrium with no structure break\n"
    "❌ R:R below 1.0\n"
    "❌ No clear SMC setup visible\n"
    "\n"
    "# USER IDEA VALIDATION\n"
    "If '<user_trading_idea>' is provided:\n"
    "- Validate against current market structure\n"
    "- If good idea → confirm and refine if needed\n"
    "- If risky → explain why and suggest improvements\n"
    "\n"
    "# OUTPUT FORMAT (STRICT JSON)\n"
    "\n"
    "## For BUY or SELL:\n"
    "```json\n"
    "{\n"
    '  "executive_summary": "Describe the SMC setup and reasoning",\n'
    '  "signal": {\n'
    '    "action": "BUY" | "SELL",\n'
    '    "confidence": 50-100,\n'
    '    "setup_grade": "A+" | "A" | "B+" | "B",\n'
    '    "setup_type": "OB_RETEST" | "FVG_FILL" | "LIQUIDITY_SWEEP" | "BOS_CONTINUATION" | "CHOCH_REVERSAL"\n'
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
    '    "tp_logic": "LIQUIDITY_TARGET" | "OB_TARGET" | "FVG_TARGET" | "FIXED_RR",\n'
    '    "invalidation_condition": "What price action invalidates this trade"\n'
    "  }\n"
    "}\n"
    "```\n"
    "\n"
    "## For WAIT:\n"
    "```json\n"
    "{\n"
    '  "executive_summary": "Why current conditions are not suitable for trading",\n'
    '  "signal": {\n'
    '    "action": "WAIT",\n'
    '    "confidence": 0-49\n'
    "  },\n"
    '  "wait_metadata": {\n'
    '    "trigger_condition": "What needs to happen for a valid setup",\n'
    '    "estimated_wait_time": "Timeframe or specific condition",\n'
    '    "wait_reason_code": "NEWS_FILTER" | "MARKET_CLOSED" | "CONFLICTING_STRUCTURE" | "NO_CLEAR_SETUP" | "POOR_RR" | "EQUILIBRIUM_ZONE",\n'
    '    "potential_direction": "BUY" | "SELL" | "UNCLEAR"\n'
    "  }\n"
    "}\n"
    "```\n"
    "\n"
    "Be honest in your assessment. A B+ setup is good enough to trade — don't wait for perfection.\n"
    "But also don't force trades where there's no clear edge.\n"
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
        chart_images_b64: Dict[str, str],
        balance: float = 5000,
        daily_loss_limit: float = 250,
        risk_percent: float = 0.5,
        language: str = 'ru',
        user_idea: str = '',
        manual_entry: Optional[str] = None,
        manual_sl: Optional[str] = None,
        manual_tp: Optional[str] = None
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
            balance: Баланс счета
            daily_loss_limit: Дневной лимит убытков
            risk_percent: Процент риска на сделку
            language: Язык ответа ('ru' или 'en')
            user_idea: Торговая идея пользователя
            manual_entry: Точка входа пользователя
            manual_sl: Stop Loss пользователя
            manual_tp: Take Profit пользователя
        
        Returns:
            List с текстовым контекстом и изображениями
        """
        
        # 1. Формируем текстовую часть (контекст)
        risk_amount = balance * (risk_percent / 100)
        
        # Секция с пользовательской идеей, если она есть
        user_idea_context = ""
        if user_idea or manual_entry or manual_sl or manual_tp:
            user_idea_context = f"""
<user_trading_idea>
The user has proposed their own trading idea:
Idea/Thoughts: {user_idea if user_idea else "No text provided"}
Proposed Entry: {manual_entry if manual_entry else "Not specified"}
Proposed Stop Loss: {manual_sl if manual_sl else "Not specified"}
Proposed Take Profit: {manual_tp if manual_tp else "Not specified"}

TASK: You MUST validate this user idea. Compare it with your own market analysis. 
If the user idea is dangerous or contradicts the technical/news data, explain why in the 'executive_summary'.
If the user idea is good, you can use it as a basis for your trade plan.
</user_trading_idea>
"""
        
        # Определяем статус рынка для LLM
        from datetime import datetime as dt_module
        try:
            utc_now = dt_module.strptime(current_time_utc, "%Y-%m-%d %H:%M UTC")
            weekday = utc_now.weekday()  # 0=Monday, 6=Sunday
            hour = utc_now.hour
            
            market_status = "OPEN"
            if weekday == 5:  # Saturday
                market_status = "CLOSED"
            elif weekday == 6 and hour < 23:  # Sunday before 23:00
                market_status = "CLOSED"
            elif weekday == 4 and hour >= 22:  # Friday after 22:00
                market_status = "CLOSED"
        except:
            market_status = "UNKNOWN"

        user_prompt_text = f"""
REPORT GENERATION REQUEST for XAUUSD

<environment>
Current Time (UTC): {current_time_utc}
Market Status: {market_status} (Gold market opens Sunday 23:00 UTC, closes Friday 22:00 UTC)
Active Session: {session_info['description']}
Session Details: {json.dumps(session_info, indent=2)}
Language Requirement: Please provide the 'executive_summary' and all string descriptions in {language.upper()} language.
</environment>

<account_config>
Balance: ${balance}
Daily Loss Limit: ${daily_loss_limit}
Risk Per Trade: {risk_percent}% (${risk_amount:.2f})
</account_config>

{user_idea_context}

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
        model: str = "openrouter",
        balance: float = 5000,
        daily_loss_limit: float = 250,
        risk_percent: float = 0.5,
        language: str = 'ru',
        user_idea: str = '',
        manual_entry: Optional[str] = None,
        manual_sl: Optional[str] = None,
        manual_tp: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Отправляет данные в LLM для анализа торгового решения
        
        Args:
            technical_data: Технические данные (свечи, SMC уровни)
            news_data: Новости
            computed_levels: Вычисленные уровни (PDH/PDL, свинги)
            chart_images: Изображения графиков в base64
            model: Выбор модели - "openrouter", "gemini" или "gateway"
            balance: Баланс счета
            daily_loss_limit: Дневной лимит убытков
            risk_percent: Процент риска на сделку
            language: Язык ответа ('ru' или 'en')
            user_idea: Торговая идея пользователя
            manual_entry: Точка входа пользователя
            manual_sl: Stop Loss пользователя
            manual_tp: Take Profit пользователя
        
        Returns:
            Ответ от LLM модели
        """
        params = {
            "technical_data": technical_data,
            "news_data": news_data,
            "computed_levels": computed_levels,
            "chart_images": chart_images,
            "balance": balance,
            "daily_loss_limit": daily_loss_limit,
            "risk_percent": risk_percent,
            "language": language,
            "user_idea": user_idea,
            "manual_entry": manual_entry,
            "manual_sl": manual_sl,
            "manual_tp": manual_tp
        }
        
        if model == "gemini":
            return self._analyze_with_gemini(**params)
        elif model == "gateway":
            return self._analyze_with_gateway(**params)
        else:
            return self._analyze_with_openrouter(**params)
    
    def _analyze_with_openrouter(
        self,
        technical_data: Dict,
        news_data: Dict,
        computed_levels: Dict,
        chart_images: Dict[str, str],
        balance: float = 5000,
        daily_loss_limit: float = 250,
        risk_percent: float = 0.5,
        language: str = 'ru',
        user_idea: str = '',
        manual_entry: Optional[str] = None,
        manual_sl: Optional[str] = None,
        manual_tp: Optional[str] = None
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
                chart_images_b64=chart_images,
                balance=balance,
                daily_loss_limit=daily_loss_limit,
                risk_percent=risk_percent,
                language=language,
                user_idea=user_idea,
                manual_entry=manual_entry,
                manual_sl=manual_sl,
                manual_tp=manual_tp
            )
            
            if user_content is None:
                return {"error": "Failed to build user payload"}
            
            # Формируем запрос к OpenRouter
            headers = {
                "Content-Type": "application/json",
                "HTTP-Referer": "https://github.com/astra-analyzer-pro",
                "X-Title": "Astra Analyzer Pro - Trading Bot"
            }
            
            # Добавляем API ключ если есть
            if self.openrouter_key:
                headers["Authorization"] = f"Bearer {self.openrouter_key}"
                logger.info("Using OpenRouter API key for authentication")
            else:
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
            
            # Логируем IP адрес перед запросом
            current_ip = get_current_ip()
            if current_ip:
                logger.info(f"Current external IP: {current_ip}")
            
            logger.info(f"Sending request to OpenRouter (model: {self.OPENROUTER_MODEL})")
            logger.debug(f"Request URL: {self.OPENROUTER_API_URL}")
            
            # Отправляем запрос с увеличенным таймаутом
            response = requests.post(
                self.OPENROUTER_API_URL,
                headers=headers,
                json=payload,
                timeout=120  # Увеличено до 120 секунд для VPN окружения
            )
            
            response.raise_for_status()
            result = response.json()
            
            logger.info("Successfully received response from OpenRouter")
            
            # Извлекаем ответ модели
            if 'choices' in result and len(result['choices']) > 0:
                assistant_message = result['choices'][0]['message']['content']
                
                # Логируем длину ответа
                logger.info(f"Response received: {len(assistant_message)} characters")
                
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
                    logger.info("✓ Successfully parsed JSON from LLM response")
                    logger.debug(f"Parsed decision: {json.dumps(parsed_json, indent=2, ensure_ascii=False)}")
                else:
                    logger.warning("⚠ Failed to parse JSON from LLM response - returning raw text")
                
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
        chart_images: Dict[str, str],
        balance: float = 5000,
        daily_loss_limit: float = 250,
        risk_percent: float = 0.5,
        language: str = 'ru',
        user_idea: str = '',
        manual_entry: Optional[str] = None,
        manual_sl: Optional[str] = None,
        manual_tp: Optional[str] = None
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
            risk_amount = balance * (risk_percent / 100)
            
            # Секция с пользовательской идеей
            user_idea_context = ""
            if user_idea or manual_entry or manual_sl or manual_tp:
                user_idea_context = f"""
<user_trading_idea>
The user has proposed their own trading idea:
Idea/Thoughts: {user_idea if user_idea else "No text provided"}
Proposed Entry: {manual_entry if manual_entry else "Not specified"}
Proposed Stop Loss: {manual_sl if manual_sl else "Not specified"}
Proposed Take Profit: {manual_tp if manual_tp else "Not specified"}

TASK: You MUST validate this user idea. Compare it with your own market analysis. 
If the user idea is dangerous or contradicts the technical/news data, explain why in the 'executive_summary'.
If the user idea is good, you can use it as a basis for your trade plan.
</user_trading_idea>
"""
            
            # Определяем статус рынка для LLM
            weekday = current_time.weekday()  # 0=Monday, 6=Sunday
            hour = current_time.hour
            
            market_status = "OPEN"
            if weekday == 5:  # Saturday
                market_status = "CLOSED"
            elif weekday == 6 and hour < 23:  # Sunday before 23:00
                market_status = "CLOSED"
            elif weekday == 4 and hour >= 22:  # Friday after 22:00
                market_status = "CLOSED"

            user_prompt_text = f"""
REPORT GENERATION REQUEST for XAUUSD

<environment>
Current Time (UTC): {time_str}
Market Status: {market_status} (Gold market opens Sunday 23:00 UTC, closes Friday 22:00 UTC)
Active Session: {session_info['description']}
Session Details: {json.dumps(session_info, indent=2)}
Language Requirement: Please provide the 'executive_summary' and all string descriptions in {language.upper()} language.
</environment>

<account_config>
Balance: ${balance}
Daily Loss Limit: ${daily_loss_limit}
Risk Per Trade: {risk_percent}% (${risk_amount:.2f})
</account_config>

{user_idea_context}

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
            
            # Логируем URL (без ключа)
            logger.debug(f"Gemini API URL: {self.GEMINI_API_URL}/{self.GEMINI_MODEL}:generateContent")
            
            payload = {"contents": [{"parts": [{"text": full_prompt}]}]}
            logger.info(f"Payload size: {len(full_prompt)} characters")
            
            # Логируем IP адрес перед запросом
            current_ip = get_current_ip()
            if current_ip:
                logger.info(f"Current external IP: {current_ip}")
            
            logger.info(f"Sending request to Gemini API (model: {self.GEMINI_MODEL})")
            logger.debug(f"Request URL: {url}")
            
            response = requests.post(
                url,
                json=payload,
                headers={"Content-Type": "application/json"},
                timeout=120  # Увеличено до 120 секунд для VPN окружения
            )
            
            if response.status_code == 429:
                logger.warning("Gemini API rate limit exceeded")
                return {"error": "Gemini API rate limit exceeded", "status": 429}
            
            if response.status_code != 200:
                logger.error(f"Gemini API error: {response.status_code}")
                try:
                    error_details = response.json()
                    logger.error(f"Gemini Error Details: {json.dumps(error_details, indent=2, ensure_ascii=False)}")
                except:
                    logger.error(f"Gemini Error Raw Body: {response.text}")
                
                return {"error": f"Gemini API error: {response.status_code}", "status": response.status_code, "details": response.text}
            
            result = response.json()
            
            logger.info("Successfully received response from Gemini")
            
            # Извлекаем ответ модели
            if result.get('candidates') and result['candidates'][0].get('content'):
                assistant_message = result['candidates'][0]['content']['parts'][0]['text']
                
                # Логируем длину ответа
                logger.info(f"Response received: {len(assistant_message)} characters")
                
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
                    logger.info("✓ Successfully parsed JSON from Gemini response")
                    logger.debug(f"Parsed decision: {json.dumps(parsed_json, indent=2, ensure_ascii=False)}")
                else:
                    logger.warning("⚠ Failed to parse JSON from Gemini response - returning raw text")
                
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
        chart_images: Dict[str, str],
        balance: float = 5000,
        daily_loss_limit: float = 250,
        risk_percent: float = 0.5,
        language: str = 'ru',
        user_idea: str = '',
        manual_entry: Optional[str] = None,
        manual_sl: Optional[str] = None,
        manual_tp: Optional[str] = None
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
                chart_images_b64=chart_images,
                balance=balance,
                daily_loss_limit=daily_loss_limit,
                risk_percent=risk_percent,
                language=language,
                user_idea=user_idea,
                manual_entry=manual_entry,
                manual_sl=manual_sl,
                manual_tp=manual_tp
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
            
            # Логируем IP адрес перед запросом
            current_ip = get_current_ip()
            if current_ip:
                logger.info(f"Current external IP: {current_ip}")
            
            logger.info(f"Sending request to AI Gateway ({gateway_url}) with model: {self.GATEWAY_MODEL}")
            logger.debug(f"Request URL: {gateway_url}")
            
            # Отправляем запрос с увеличенным таймаутом
            response = requests.post(
                gateway_url,
                headers=headers,
                json=payload,
                timeout=120  # Увеличено до 120 секунд для VPN окружения
            )
            
            response.raise_for_status()
            result = response.json()
            
            logger.info("Successfully received response from AI Gateway")
            
            # Извлекаем ответ модели (формат как у OpenRouter)
            if 'choices' in result and len(result['choices']) > 0:
                assistant_message = result['choices'][0]['message']['content']
                
                # Логируем длину ответа
                logger.info(f"Response received: {len(assistant_message)} characters")
                
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
                    logger.info("✓ Successfully parsed JSON from AI Gateway response")
                    logger.debug(f"Parsed decision: {json.dumps(parsed_json, indent=2, ensure_ascii=False)}")
                else:
                    logger.warning("⚠ Failed to parse JSON from AI Gateway response - returning raw text")
                
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

    def get_signal_verdict(self, analysis_data: Dict) -> str:
        """
        Специальный метод для Наблюдателя.
        Использует ПОЛНЫЙ системный промт и Gemini 3 Flash для авто-анализа.
        Возвращает сырой JSON ответ от LLM для дальнейшей обработки.
        """
        try:
            time_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
            
            # Мы вызываем уже существующий метод _analyze_with_gemini.
            # Он внутри сам склеит self.SYSTEM_PROMPT и данные.
            
            # v8.2 FIX: Извлекаем key_levels из новой структуры с timeframes
            computed_levels = {}
            if 'timeframes' in analysis_data and 'M15' in analysis_data['timeframes']:
                m15_analysis = analysis_data['timeframes']['M15'].get('analysis', {})
                computed_levels = m15_analysis.get('advanced', {}).get('key_levels', {})
            else:
                # Fallback для старой структуры
                computed_levels = analysis_data.get('advanced', {}).get('key_levels', {})
            
            result = self._analyze_with_gemini(
                technical_data=analysis_data,
                news_data={"info": "Automated Watcher Alert - No high impact news checked"},
                computed_levels=computed_levels,
                chart_images={}, # В фоновом режиме пока без картинок для скорости
                language='ru'
            )

            if result.get("success"):
                # Возвращаем ПОЛНЫЙ сырой ответ для парсинга в watcher.py
                # Это позволит сохранить полное описание без обрезания
                return result.get("response", "")
            
            # Если ошибка, возвращаем JSON с WAIT
            error_response = {
                "executive_summary": "ИИ не смог сформировать четкий вердикт по сигналу.",
                "signal": {"action": "WAIT"},
                "wait_metadata": {
                    "trigger_condition": "N/A",
                    "estimated_wait_time": "N/A",
                    "wait_reason_code": "NO_SETUP"
                }
            }
            return json.dumps(error_response, ensure_ascii=False)

        except Exception as e:
            logger.error(f"Error in automated verdict: {e}")
            error_response = {
                "executive_summary": f"Ошибка анализа: {str(e)}",
                "signal": {"action": "WAIT"},
                "wait_metadata": {
                    "trigger_condition": "N/A",
                    "estimated_wait_time": "N/A",
                    "wait_reason_code": "NO_SETUP"
                }
            }
            return json.dumps(error_response, ensure_ascii=False)

# Синглтон инстанс
llm_service = LLMService(
    openrouter_key=OPENROUTER_API_KEY, 
    gemini_key=GEMINI_API_KEY,
    gateway_url=AI_GATEWAY_URL,
    gateway_key=AI_GATEWAY_KEY
)
