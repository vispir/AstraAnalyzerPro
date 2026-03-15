"""
LLM Service для анализа торговых решений через OpenRouter
"""
import json
import requests
import logging
import base64
import re
import os
from datetime import datetime, timezone
from typing import Dict, List, Any, Optional, Union
from PIL import Image
import io

from config.settings import (
    OPENROUTER_API_KEY,
    GEMINI_API_KEY,
    GEMINI_API_KEY_MANAGER,
    AI_GATEWAY_URL,
    AI_GATEWAY_KEY
)

logger = logging.getLogger(__name__)


def _build_market_status(utc_time: Optional[datetime] = None) -> str:
    """
    Определяет статус рынка (OPEN/CLOSED) на основе времени UTC.
    
    Args:
        utc_time: время UTC (по умолчанию - текущее время)
    
    Returns:
        "OPEN" или "CLOSED"
    """
    if utc_time is None:
        utc_time = datetime.now(timezone.utc)
    
    weekday = utc_time.weekday()
    hour = utc_time.hour
    
    if weekday == 5:  # Saturday
        return "CLOSED"
    elif weekday == 6 and hour < 22:  # Sunday before 22:00 UTC
        return "CLOSED"
    elif weekday == 4 and hour >= 22:  # Friday after 22:00
        return "CLOSED"
    
    return "OPEN"


def _build_common_prompt_section(
    current_time_utc: str,
    session_info: Dict[str, Any],
    news_data: Dict,
    computed_levels: Dict,
    technical_data: Dict,
    balance: float,
    daily_loss_limit: float,
    risk_percent: float,
    language: str,
    user_idea: str = '',
    manual_entry: Optional[str] = None,
    manual_sl: Optional[str] = None,
    manual_tp: Optional[str] = None,
    include_visual_section: bool = True,
    invalidation_data: Optional[Dict] = None,
    htf_context: Optional[Dict] = None
) -> str:
    """
    Формирует общую часть промпта для LLM (используется в build_user_payload и _analyze_with_gemini).
    
    Args:
        current_time_utc: Строка времени "YYYY-MM-DD HH:MM UTC"
        session_info: Информация о сессии
        news_data: JSON с новостями
        computed_levels: JSON с уровнями
        technical_data: JSON с техническими данными
        balance: Баланс счета
        daily_loss_limit: Дневной лимит убытков
        risk_percent: Процент риска
        language: Язык ответа
        user_idea: Торговая идея пользователя
        manual_entry/sl/tp: Ручные уровни пользователя
        include_visual_section: Включить секцию <visual_attachments>
        invalidation_data: Данные об инвалидации и ATR (опционально)
        htf_context: HTF контекст (H4/H1) (опционально)
    
    Returns:
        Строка с промптом
    """
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
    
    market_status = _build_market_status()
    
    # Базовая часть промпта
    prompt = f"""
REPORT GENERATION REQUEST for XAUUSD

<environment>
Current Time (UTC): {current_time_utc}
Market Status: {market_status} (Gold market opens Sunday 22:00 UTC, closes Friday 22:00 UTC)
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
"""
    
    # HTF контекст (если передан)
    if htf_context:
        prompt += f"""
<htf_context>
Higher Timeframe bias for Step 1 (H4 → H1). Use this FIRST to determine dominant trend and zones before looking at M15 entry.
- H4: macro trend and zone (premium/discount).
- H1: intermediate structure and last confirmed BOS/CHoCH.
Do NOT take M15 entries against strong HTF trend unless there is clear reversal confirmation.
{json.dumps(htf_context, indent=2)}
</htf_context>
"""

    # История локальных диапазонов (если передана)
    recent_local_ranges = technical_data.get("recent_local_ranges") if isinstance(technical_data, dict) else None
    if recent_local_ranges:
        prompt += f"""
<local_ranges_history>
These are recent LOCAL consolidation ranges on M15 (now inactive). Each range is a rectangle where price previously
consolidated and then left. You can use their highs and lows as realistic TP candidates (liquidity zones) AFTER you
validate the current Range Breakout and overall context. They are optional targets, not mandatory.
{json.dumps(recent_local_ranges, indent=2)}
</local_ranges_history>
"""
    
    # Блок инвалидации и ATR (если передан)
    if invalidation_data:
        inv_levels = invalidation_data.get("invalidation_levels", {})
        atr_m15 = invalidation_data.get("atr_m15", 0)
        cur_price = invalidation_data.get("current_price", 0)
        
        prompt += f"""
<invalidation_and_atr>
CURRENT_PRICE: {cur_price}
ATR_M15(14): {atr_m15}
RULES:
- For BUY: Stop Loss MUST be at or BELOW invalidation_buy (structure invalidation). Otherwise the setup is invalid → WAIT.
- For SELL: Stop Loss MUST be at or ABOVE invalidation_sell. Otherwise → WAIT.
- Range Breakout SELL: SL = range_low + 1.0×ATR (above broken lower range boundary). For Range Breakout, the range boundary IS the invalidation — do NOT use invalidation_sell from OB/FVG, ignore it completely.
- Range Breakout BUY: SL = range_high - 1.0×ATR (below broken upper range boundary). For Range Breakout, the range boundary IS the invalidation — do NOT use invalidation_buy from OB/FVG, ignore it completely.
- SL width should not exceed 2.0 × ATR (unless structure clearly requires it) to keep risk acceptable.
- Entry: найди ЛОГИЧНУЮ точку входа рядом с CURRENT_PRICE:
  • ретест пробитой границы диапазона (range_high/low), или
  • ближайший OB/FVG по направлению пробоя,
  • но НЕ дальше чем 0.5×ATR или примерно 0.3% от CURRENT_PRICE.
  Если явного уровня рядом нет, используй CURRENT_PRICE.
- Minimum R:R = 1.5. Output calculated R:R in math_debug_log.calculated_rr. If R:R < 1.5 → WAIT.
INVALIDATION LEVELS:
{json.dumps(inv_levels, indent=2)}
</invalidation_and_atr>
"""        
    
    # Range Breakout контекст (подсказки SL/TP), если есть
    rbc = technical_data.get("range_breakout_context") if isinstance(technical_data, dict) else None
    if rbc:
        prompt += f"""
<range_breakout_context>
When 'is_confirmed' is true, the system has detected a confirmed Range Breakout and precomputed recommended levels:
- direction: BUY or SELL (matches breakout_direction)
- entry_hint: approximate entry near current price at confirmation
- suggested_sl: Stop Loss placed just beyond the range boundary with 1.0×ATR buffer
- suggested_tp: Take Profit near the next key level in breakout direction or at least 1.5R from entry_hint
- suggested_rr: approximate R:R based on entry_hint, suggested_sl and suggested_tp

Use these as the BASELINE for your trade plan in Range Breakout scenarios:
- Do NOT place SL closer to price than suggested_sl (it MUST stay beyond the broken range boundary).
- You MAY refine TP based on liquidity/structure, but do not reduce R:R below 1.5 and prefer R:R ≥ 1.5.

JSON:
{json.dumps(rbc, indent=2)}
</range_breakout_context>
"""
    
    # Технические данные — убираем из сырого дампа то, что уже подано отдельными блоками
    # (chart_images, range_breakout_context, recent_local_ranges), чтобы не дублировать и не путать модель.
    exclude_from_raw = {'chart_images', 'range_breakout_context', 'recent_local_ranges'}
    if isinstance(technical_data, dict):
        technical_data_for_text = {k: v for k, v in technical_data.items() if k not in exclude_from_raw}
    else:
        technical_data_for_text = technical_data

    prompt += f"""
<raw_technical_data>
{json.dumps(technical_data_for_text, indent=2)}
</raw_technical_data>
"""
    
    # Визуальная секция (опционально)
    if include_visual_section:
        prompt += """
<visual_attachments>
The following images are attached to this request in order:
1. H4 Chart (Macro Trend & Narrative)
2. H1 Chart (Intermediate Structure)
3. M15 Chart (Execution & Trigger)
</visual_attachments>
"""
    
    prompt += """
<task>
1. Synthesize the News, Visuals, and Data.
2. Validate the setup using the "Decision Protocol" from System Instructions.
3. Output the STRICT JSON decision with the required fields.
</task>
"""
    
    return prompt


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
    Очищает ответ LLM от Markdown обёрток и парсит JSON.
    Улучшенная версия: обрабатывает экранированные кавычки и различные варианты markdown-обёрток.

    Args:
        response_text: Сырой текст ответа от LLM

    Returns:
        Распарсенный JSON объект или None при ошибке
    """
    try:
        # 1. Удаляем Markdown обертки (различные варианты)
        clean_text = response_text
        # Варианты: ```json, ```, ``` JSON, ```json\n
        clean_text = re.sub(r"```(?:json|JSON)?\s*", "", clean_text)
        clean_text = re.sub(r"```\s*$", "", clean_text, flags=re.MULTILINE)
        
        # 2. Находим первую '{' и последнюю '}' для извлечения JSON
        start = clean_text.find('{')
        end = clean_text.rfind('}') + 1
        
        if start == -1 or end <= start:
            logger.debug(f"No JSON object found in response")
            return None
        
        clean_text = clean_text[start:end]

        # 3. Удаляем лишние пробелы по краям
        clean_text = clean_text.strip()
        
        # 4. Пробуем стандартный парсинг
        try:
            return json.loads(clean_text)
        except json.JSONDecodeError as first_error:
            # 5. Если не удалось, пробуем исправить распространённые проблемы
            #    a. Экранированные кавычки внутри строк (например: "reason": "Цена \"пробила\"")
            #    b. Неправильные escape-последовательности
            
            # Пробуем заменить проблемные экранирования
            fixed_text = clean_text
            # Замена \" на " только внутри строк (упрощённо)
            # Это может помочь если LLM неправильно экранировал кавычки
            fixed_text = re.sub(r'(?<!\\)\\(?!["\\/bfnrtu])', r'\\\\', fixed_text)
            
            try:
                return json.loads(fixed_text)
            except json.JSONDecodeError:
                # Если всё ещё ошибка, логируем первую ошибку и возвращаем None
                logger.error(f"JSON Parse Error: {first_error}")
                logger.debug(f"Raw text (first 500 chars): {clean_text[:500]}...")
                return None

    except Exception as e:
        logger.error(f"Unexpected error in parse_json_response: {e}")
        return None


class TradingSession:
    """Определение текущей торговой сессии"""
    
    SESSIONS = {
        'Sydney': {'start': 20, 'end': 5},      # 20:00 - 05:00 UTC (GMT+0)
        'Tokyo': {'start': 0, 'end': 9},       # 00:00 - 09:00 UTC
        'London': {'start': 8, 'end': 17},     # 08:00 - 17:00 UTC
        'New York': {'start': 13, 'end': 22},  # 13:00 - 22:00 UTC
    }
    
    OVERLAPS = {
        'London/New York': {'start': 13, 'end': 17},  # 13:00 - 17:00 UTC
        'Tokyo/London': {'start': 8, 'end': 9},       # 08:00 - 09:00 UTC
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
    "## B+ Setup (Confidence 60-69) — Хороший сетап, можно торговать\n"
    "- Trend is identifiable\n"
    "- At least one SMC confluence (OB OR FVG OR liquidity level)\n"
    "- Internal structure confirmation\n"
    "- R:R >= 1.5\n"
    "\n"
    "## B Setup (Confidence 55-59) — Торгуем осторожно\n"
    "- Basic structure alignment\n"
    "- One clear reason to enter\n"
    "- R:R >= 1.5\n"
    "- ONLY during London or London/NY session. Skip during Tokyo and Off-hours.\n"
    "\n"
    "## C Setup (Confidence < 55) — НЕ ТОРГУЕМ → WAIT\n"
    "- Conflicting timeframes\n"
    "- No clear structure\n"
    "- Price in no-trade zone (middle of range)\n"
    "- Poor R:R (< 1.5)\n"
    "\n"
    "# TRADE IDENTIFICATION\n"
    "\n"
    "## Step 1: HTF Bias (H4 → H1)\n"
    "Determine the dominant trend:\n"
    "- UPTREND: Higher highs, higher lows → look for BUYS in discount\n"
    "- DOWNTREND: Lower highs, lower lows → look for SELLS in premium\n"
    "- RANGING: Trade extremes with reversal confirmation\n"
    "\n"
    "## Step 2: LTF Entry (M15) — COMPLETE SETUP MODELS\n"
    "\n"
    "You MUST identify which model applies. Partial setups = lower confidence.\n"
    "\n"
    "### Model 1: LIQUIDITY SWEEP REVERSAL (Highest probability)\n"
    "Required elements IN THIS ORDER:\n"
    "1. Liquidity taken: price sweeps above/below a significant high/low (EQH/EQL, session high/low, previous swing)\n"
    "2. Structure break: CHoCH confirmed (body close beyond previous internal swing)\n"
    "3. Entry zone: Price retraces to the OB that CAUSED the CHoCH (causative OB), ideally overlapping with the FVG created by the impulse\n"
    "4. Reaction: M15 candle shows rejection from the zone (wick rejection, engulfing, or pin bar)\n"
    "If ALL 4 present → A/A+ grade possible. If 3 of 4 present → B+ grade maximum. If only 1-2 → WAIT\n"
    "\n"
    "### Model 2: BOS CONTINUATION (Trend following)\n"
    "Required elements:\n"
    "1. Swing-level BOS confirmed (not just internal)\n"
    "2. Price pulls back to: the FVG or OB created by the BOS impulse\n"
    "3. Internal structure gives entry signal (internal CHoCH in direction of swing trend)\n"
    "4. Higher timeframe trend aligned\n"
    "If ALL 4 → A grade possible. If missing swing BOS (only internal) → B+ maximum, confidence ≤ 65\n"
    "\n"
    "### Model 3: RANGE SWEEP (Counter-trend with confirmation)\n"
    "Required elements:\n"
    "1. Clear range identified (250-bar high/low or swing range)\n"
    "2. Price sweeps one extreme of the range (takes stops)\n"
    "3. CHoCH confirmed on M15 after the sweep\n"
    "4. OB/FVG for entry exists between sweep point and CHoCH level\n"
    "5. Target = opposite extreme or equilibrium of range\n"
    "This model CAN work against the swing trend, but REQUIRES the sweep + CHoCH. Without the sweep → WAIT\n"
    "\n"
    "### Model 4: ORDER BLOCK RETEST (Pullback entry)\n"
    "Required elements:\n"
    "1. Previously identified quality OB (caused a BOS/CHoCH, unmitigated)\n"
    "2. Price returns to the OB zone\n"
    "3. Reaction visible (at least a wick or engulfing at the zone)\n"
    "4. No opposing structure break between OB creation and current test\n"
    "If the OB has been previously tested (mitigated) → skip, not fresh → WAIT\n"
    "\n"
    "### CRITICAL: What makes an OB quality?\n"
    "- It CAUSED a structural break (BOS or CHoCH) — not just any candle\n"
    "- It has NOT been previously tested (first touch = highest probability)\n"
    "- It overlaps with or is adjacent to an FVG (OB+FVG overlap = premium entry)\n"
    "- It is in the correct zone context (discount OB for buys, premium OB for sells) — bonus, not requirement\n"
    "\n"
    "### CRITICAL: Swing vs Internal Structure\n"
    "- SWING BOS/CHoCH = Major structure. Defines the trend. High confidence signal.\n"
    "- INTERNAL BOS/CHoCH = Minor structure. Can be a pullback within larger trend.\n"
    "RULES:\n"
    "- Internal CHoCH ALONE (no swing support, no liquidity sweep) → MAX confidence 60\n"
    "- Internal CHoCH AGAINST swing trend → MAX confidence 50 (usually WAIT)\n"
    "- Swing CHoCH/BOS = strong signal, can support A/A+ grades\n"
    "- Internal BOS in direction of swing trend = good continuation signal (B+ to A)\n"
    "\n"
    "## Step 3: Trade Parameters\n"
    "- **Entry**: Current price or limit at OB/FVG\n"
    "- **Stop Loss**: Beyond LOCAL (Internal M15) invalidation structure + $0.50-1.00 buffer\n"
    "  * Use NEAREST Internal OB or FVG for SL — NOT Swing High/Low\n"
    "  * EXCEPTION — Range Breakout mode: SL = range boundary ± 1.0×ATR (see RANGE BREAKOUT section). Do NOT use OB/FVG for SL in Range Breakout mode.\n"
    "  * EXCEPTION — Range Rejection mode: SL = range boundary ± 1.0×ATR (see RANGE REJECTION section). Do NOT use OB/FVG for SL in Range Rejection mode.\n"
    "  * Keep SL within 1.0–6.0×ATR from entry (Gold M15 typical: $8–48)\n"
    "  * SL distance from entry < 1.0×ATR → REJECT. SL > 6.0×ATR → WARNING but trade allowed if R:R ≥ 1.5\n"
    "  * Typical SL for Gold M15: 1.5-3.0×ATR ($12-24 with ATR=$8)\n"
    "  * Example CORRECT SL (BUY): entry $5150, nearest BULL OB bottom $5138, ATR $8 → SL = $5138 (1.5×ATR, 12 points) ✓\n"
    "  * Example CORRECT SL (BUY): entry $5150, nearest BULL OB bottom $5138, ATR $8 → SL = $5134 (2.0×ATR, 16 points) ✓\n"
    "  * If Swing Low = $4975 and distance = 170 pts (21×ATR) — IGNORE IT\n"
    "- **Take Profit**: Next liquidity pool, opposing OB, or fixed R:R\n"
    "\n"
    "## Step 4: R:R Check\n"
    "- Minimum acceptable R:R = 1.5 (for B+ or better setups; gold favours slightly higher)\n"
    "- Ideal R:R > 1.5\n"
    "- If R:R < 1.5 → downgrade to WAIT\n"
    "\n"
    "# RANGE BREAKOUT — ГЛАВНЫЙ ПРИОРИТЕТ АНАЛИЗА\n"
    "\n"
    "К этому моменту система уже проверила математически:\n"
    "1. Найден локальный диапазон консолидации (очищен от импульсных свечей)\n"
    "2. ДВУМЯ последовательными свечами цена закрепилась ЗА границей диапазона\n"
    "3. Есть подтверждение структуры (BOS или CHoCH)\n"
    "\n"
    "Это САМЫЙ ВАЖНЫЙ сигнал. Важнее Premium/Discount, важнее типа BOS/CHoCH.\n"
    "Локальный диапазон — зона где цена консолидировалась.\n"
    "Пробой двумя свечами = рынок выбрал направление.\n"
    "\n"
    "В объекте <range_breakout_context.candle_wicks> переданы точные данные по последним 2–3 свечам пробоя диапазона:\n"
    "- first, second (и third при сценарии с доджи) — это последовательные свечи, закрепившиеся ЗА границей диапазона.\n"
    "- Для каждой указаны длины верхней/нижней тени и тела, а также флаги long_upper/long_lower.\n"
    "- long_upper = TRUE означает заметное продавцовое давление сверху (длинная верхняя тень).\n"
    "- long_lower = TRUE означает заметное покупательское давление снизу (длинная нижняя тень).\n"
    "Используй эти флаги вместе со скриншотом, чтобы оценить качество пробоя: усиливают ли тени импульс или, наоборот, показывают выкуп/ослабление.\n"
    "\n"
    "Интерпретация для BUY пробоя:\n"
    "- long_upper=TRUE на свече подтверждения → продавцы отталкивают цену назад → снижай confidence на 10%, предупреди в trade_plan.entry_warning='WEAK_STRUCTURE'.\n"
    "- long_lower=TRUE на свече подтверждения → покупатели поддерживают → повышай confidence на 5%.\n"
    "\n"
    "Интерпретация для SELL пробоя:\n"
    "- long_lower=TRUE на свече подтверждения → покупатели возвращают цену → снижай confidence на 10%, предупреди в trade_plan.entry_warning='WEAK_STRUCTURE'.\n"
    "- long_upper=TRUE на свече подтверждения → продавцы давят → повышай confidence на 5%.\n"
    "\n"
    "ТВОЯ ЗАДАЧА:\n"
    "1. Подтвердить качество пробоя по скриншоту (нет ли возврата назад)\n"
    "2. Найти точку входа: ближайший OB, FVG или ретест границы диапазона\n"
    "3. SL и TP — см. правила ниже\n"
    "\n"
    "РАСЧЁТ SL (стоп-лосс) в режиме Range Breakout:\n"
    "- Всегда используй границы диапазона и ATR как основу.\n"
    "- SL ДОЛЖЕН быть ЗА границей диапазона (BEHIND the boundary), НИКОГДА не внутри.\n"
    "- Минимальный буфер от границы: 1.0×ATR (всегда).\n"
    "- Поле 'is_narrow' в <range_breakout_context> показывает тип диапазона (True = узкий, False = широкий),\n"
    "  а 'suggested_sl' даёт базовую точку для SL, рассчитанную системой.\n"
    "SL placement rules for Range Breakout:\n"
    "- SL must be placed BEHIND the range boundary, not inside the range.\n"
    "- Use ATR-based buffer. Minimum buffer: 1.0×ATR from the boundary.\n"
    "- Narrow range (is_narrow=true): place SL behind the OPPOSITE boundary + minimum 1.0×ATR buffer.\n"
    "- Wide range (is_narrow=false): place SL behind the BROKEN boundary + minimum 1.0×ATR buffer.\n"
    "- You may use a larger buffer (1.5×ATR or 2.0×ATR) if you see high volatility or nearby liquidity that could cause a wick beyond the boundary.\n"
    "- Goal: SL must be far enough that normal market noise does not trigger it, but close enough to maintain R:R ≥ 1.5.\n"
    "- Do NOT place SL inside the range or closer than 1.0×ATR from the boundary.\n"
    "\n"
    "РАСЧЁТ TP (тейк-профит):\n"
    "- Ищи ближайший реальный уровень в направлении пробоя: Swing High/Low, EQH/EQL, DH/DL, пул ликвидности.\n"
    "- TP ставь по ТЕЛУ (close) ключевой свечи, НЕ по тени (high/low).\n"
    "- ПРАВИЛО TP — МАКСИМАЛЬНОЕ РАССТОЯНИЕ:\n"
    "  * TP не может быть дальше 80 пунктов от entry.\n"
    "  * Запрещено использовать как TP следующие уровни, если они дальше 80 пунктов от entry:\n"
    "    - Wick High / Wick Low\n"
    "    - Strong High / Strong Low\n"
    "    - Daily High / Daily Low\n"
    "    - Swing High / Swing Low\n"
    "- Приоритет выбора TP:\n"
    "  1. Локальные диапазоны из <local_ranges_history> — граница ближайшего диапазона в направлении сделки\n"
    "  2. Ближайший OB или FVG в направлении сделки\n"
    "  3. Ближайший SMC уровень с R:R >= 1.5\n"
    "- Если ни один уровень не даёт R:R >= 1.5 в пределах 80 пунктов от entry — выдавай WAIT.\n"
    "- Исключение: если цена на историческом максимуме/минимуме и нет уровней выше/ниже — TP = entry ± 2.0×ATR (но не более 80 пунктов).\n"
    "- Внутри этих ограничений TP выбирай по следующему приоритету:\n"
    "  1. Ищи уровень который даёт R:R >= 2.0 — это идеальная цель\n"
    "  2. Если такого уровня нет — бери ближайший с R:R >= 1.5\n"
    "  3. Если нет уровня даже на 1.5 — WAIT\n"
    "Предпочитай R:R 2.0+ — это повышает итоговую прибыль даже если часть сделок закрывается менеджером на 1R.\n"
    "Если рассматриваешь TP на экстремальном high/low (вик/strong high/low текущего дня или сессии):\n"
    "- Ставь TP на такой хай/лоу ТОЛЬКО ПРИ ВЫСОКОЙ УВЕРЕННОСТИ, что цена реально дойдёт до этого уровня.\n"
    "- Если уверенности нет, НЕ используй вик/strong high/low как TP.\n"
    "- В условиях неопределённости отдавай приоритет реалистичным целям: уровням из <local_ranges_history>,\n"
    "  ближайшим телам (close) ключевых свечей и SMC-уровням с R:R >= 1.5 (предпочтительно >= 2.0).\n"
    "\n"
    "ИСКЛЮЧЕНИЕ — исторический максимум/минимум:\n"
    "Если цена на историческом хае и нет видимых уровней выше —\n"
    "НЕ выдавай WAIT. Используй ATR для TP:\n"
    "- BUY: TP = entry + 2.0×ATR\n"
    "- SELL: TP = entry - 2.0×ATR\n"
    "SL по-прежнему за границей диапазона (range_high − 1.0×ATR для BUY, range_low + 1.0×ATR для SELL).\n"
    "Это даёт адекватный R:R даже без исторических уровней.\n"
    "\n"
    "- При наличии suggested_tp в <range_breakout_context> используй его как базовый таргет и при необходимости скорректируй по структуре.\n"
    "\n"
    "BUY: обе свечи закрылись ВЫШЕ local_range_high → искать вход на покупку\n"
    "SELL: обе свечи закрылись НИЖЕ local_range_low → искать вход на продажу\n"
    "\n"
    "BOS/CHoCH/Premium/Discount — ВТОРИЧНЫЕ факторы.\n"
    "Используй их для уточнения точки входа и уровней SL/TP.\n"
    "Они НЕ являются обязательным условием для сигнала.\n"
    "\n"
    "ВАЖНО: Если R:R ≥ 1.5 и пробой подтверждён свечами —\n"
    "НЕ выдавай WAIT. Выдавай направленный сигнал BUY или SELL.\n"
    "WAIT допустим ТОЛЬКО если выполняется хотя бы одно из условий:\n"
    "- Пробой выглядит ложным (цена уже разворачивается назад в диапазон)\n"
    "- R:R < 1.5\n"
    "- Сразу за точкой входа виден сильный встречный уровень\n"
    "Расстояние от точки пробоя само по себе НЕ является причиной для WAIT,\n"
    "если итоговый R:R по сделке ≥ 1.5.\n"
    "\n"
    "Если пробой качественный — давай сигнал с confidence >= 65%.\n"
    "В режиме Range Breakout по умолчанию ты даёшь BUY или SELL (с entry/SL/TP). WAIT — только исключение при наличии хотя бы одного из: ложный пробой по скриншоту, R:R < 1.5, сильный встречный уровень сразу за входом. Без этих фактов — всегда давай направленный сигнал (BUY или SELL), не WAIT.\n"
    "ЖЁСТКОЕ ПРАВИЛО — ЗАПРЕТ ПЕРФЕКЦИОНИЗМА: Ты НЕ имеешь права давать WAIT только потому, что сетап кажется тебе 'неидеальным', 'не лучшим' или 'можно было бы лучше'. "
    "Если все формальные условия соблюдены (подтверждённый пробой двумя свечами, SL/TP рассчитаны, R:R ≥ 1.5, нет явной структуры фейкоута) — "
    "сигнал класса B+ ОБЯЗАН проходить как BUY или SELL. Субъективная неуверенность без конкретной причины = нарушение правил.\n"
    "БОНУС КАЧЕСТВА: Если свеча подтверждения своей тенью касалась пробитого уровня (ретест), но закрылась за его пределами — это идеальное подтверждение. Повышай confidence на 5-10% при наличии ретеста.\n"
    "\n"
    "ВАЖНО: В executive_summary ВСЕГДА упоминай локальный диапазон явно —\n"
    "укажи его границы и факт пробоя. Например: \"Цена пробила локальный\n"
    "диапазон [5167 - 5186] двумя свечами вниз, закрепившись ниже 5167.\"\n"
    "Это обязательная часть анализа, не опускай её.\n"
    "\n"
    "# RANGE REJECTION — ОТБОЙ ОТ ГРАНИЦЫ ДИАПАЗОНА\n"
    "\n"
    "К этому моменту система определила что цена подошла к границе диапазона СНАРУЖИ и показала отбой.\n"
    "\n"
    "Сценарий: цена была вне диапазона, приблизилась к границе, но не закрепилась внутри — это отбой от уровня.\n"
    "\n"
    "ТВОЯ ЗАДАЧА:\n"
    "1. Подтвердить отбой по скриншоту (wick rejection, engulfing, pin bar у границы)\n"
    "2. Направление: BUY от range_low (отбой снизу вверх), SELL от range_high (отбой сверху вниз)\n"
    "3. Confidence: применяй стандартный scoring, но +10 если виден чёткий rejection candle у границы\n"
    "\n"
    "РАСЧЁТ SL (Range Rejection):\n"
    "- BUY от range_low: SL = range_low − 1.0×ATR (ниже границы)\n"
    "- SELL от range_high: SL = range_high + 1.0×ATR (выше границы)\n"
    "- НЕ используй OB/FVG для SL в этом режиме\n"
    "\n"
    "РАСЧЁТ TP:\n"
    "- Те же правила что для Range Breakout: ближайший уровень в направлении сделки, R:R >= 1.5, не дальше 80 пунктов от entry\n"
    "\n"
    "# HTF REJECTION WATCH — РАЗВОРОТ ПО HTF ТРЕНДУ\n"
    "\n"
    "Этот сценарий срабатывает когда Range Breakout был заблокирован HTF фильтром, цена развернулась и вернулась к границе диапазона. Система уже определила направление по HTF тренду.\n"
    "\n"
    "- H4 UPTREND: сигнал BUY от range_low (цена вернулась к поддержке)\n"
    "- H4 DOWNTREND: сигнал SELL от range_high (цена вернулась к сопротивлению)\n"
    "\n"
    "ТВОЯ ЗАДАЧА:\n"
    "1. Подтвердить что цена действительно вернулась к границе и показывает реакцию\n"
    "2. HTF тренд уже проверен системой — доверяй направлению\n"
    "3. Применяй стандартный confidence scoring + +10 за alignment с H4 трендом\n"
    "\n"
    "РАСЧЁТ SL и TP: те же правила что для Range Rejection.\n"
    "\n"
    "# RANGE REJECTION — ОТБОЙ ОТ ГРАНИЦЫ (контекст из системы)\n"
    "\n"
    "Если is_range_rejection = True (в range_breakout_context):\n"
    "Цена подошла к границе диапазона СНАРУЖИ и не зашла внутрь.\n"
    "Граница стала уровнем сопротивления/поддержки.\n"
    "Если <range_breakout_context.htf_rejection_watch> = True, трактуй ситуацию как возможную охоту за стопами\n"
    "против H4 тренда: отбой от сильной поддержки/сопротивления при совпадении с направлением H4.\n"
    "В этом случае особенно внимательно оцени качество отбоя и структуру, но не переводь такой сетап в WAIT\n"
    "только из-за глубины предыдущего выноса — это специальная стратегия возврата в диапазон после выноса стопов.\n"
    "\n"
    "SELL: range_low = сопротивление → цена отбивается вниз\n"
    "BUY: range_high = поддержка → цена отбивается вверх\n"
    "\n"
    "SL:\n"
    "- SELL: range_low + 1.0×ATR\n"
    "- BUY: range_high - 1.0×ATR\n"
    "\n"
    "TP: ближайший уровень R:R >= 1.5, по телу свечи не по тени.\n"
    "При историческом хай/лоу: TP = entry ± 2.0×ATR\n"
    "\n"
    "Это НЕ пробой диапазона — это отбой от его границы как от уровня.\n"
    "Confidence >= 60% при чистом отбое без длинных теней навстречу.\n"
    "\n"
    "# CONFIDENCE SCORING (v2 — Structure-based)\n"
    "\n"
    "Start from 50 (neutral) and adjust:\n"
    "\n"
    "STRUCTURE (most important):\n"
    "+ Swing BOS/CHoCH confirmed in trade direction → +15\n"
    "+ Internal BOS/CHoCH confirmed + aligned with swing trend → +10\n"
    "+ Internal BOS/CHoCH confirmed but NO swing support → +5 only\n"
    "- Internal CHoCH/BOS AGAINST swing trend → -15\n"
    "- No confirmed structure break at all → -20\n"
    "\n"
    "LIQUIDITY (second most important):\n"
    "+ Liquidity sweep completed BEFORE the structure break → +10\n"
    "+ Liquidity sweep is RECENT (within last 10 bars of M15) → +5 additional\n"
    "+ Clear liquidity pool at TP level (resting stops) → +5\n"
    "- No visible liquidity event → -5\n"
    "- Entering after extended move without pullback (chasing) → -10\n"
    "For entry models that use a liquidity sweep: the sweep must occur BEFORE the current CHoCH/BOS and be recent (e.g. within 10 M15 bars).\n"
    "\n"
    "ORDER FLOW QUALITY:\n"
    "+ Entry at causative OB (the OB that caused the BOS/CHoCH) → +10\n"
    "+ OB + FVG overlap at entry → +5\n"
    "+ Fresh (unmitigated) OB → +5\n"
    "+ Visible reaction candle at zone → +5\n"
    "- Entry at random OB (didn't cause structure break) → +0\n"
    "- OB already tested (mitigated) → -5\n"
    "- No OB/FVG at entry zone → -10\n"
    "\n"
    "CONTEXT:\n"
    "+ HTF (H4/H1) trend aligned → +5\n"
    "+ Trading during London or London/NY overlap → +5\n"
    "+ R:R > 2.0 → +5\n"
    "- HTF trend directly opposed → -10\n"
    "- Low volume session (Sydney, Off-Hours) → -5\n"
    "- Near high-impact news (<30 min) → -10\n"
    "- R:R < 1.5 → -15\n"
    "\n"
    "ZONE (supplementary, NOT primary):\n"
    "+ Entry at extreme of range (< 25% for BUY, > 75% for SELL) → +5\n"
    "- Entry at middle of range (40-60%) without swing break → -5\n"
    "Note: Zone is CONTEXT. A valid sweep+CHoCH in any zone can be traded.\n"
    "In EQUILIBRIUM (middle of range 40-60%): trades are allowed when you have a complete entry model and confirmed structure (BOS/CHoCH); do not WAIT only because price is in equilibrium — if the setup is valid, grade and confidence apply as usual.\n"
    "\n"
    "GRADE ASSIGNMENT:\n"
    "85-100 → A+ (all model elements present + swing confirmation)\n"
    "70-84 → A (strong model with swing or clear sweep+CHoCH)\n"
    "60-69 → B+ (tradeable, internal confirmation with some confluence)\n"
    "< 55 → WAIT (confidence < 55 = do not trade)\n"
    "\n"
    "HARD CAPS:\n"
    "- No swing-level break AND no liquidity sweep → MAX 60 (B+)\n"
    "- Internal-only against swing trend → MAX 50 → WAIT\n"
    "- No identifiable entry model (see Step 2) → MAX 45 → WAIT\n"
    "- SL should be within 1.0–6.0×ATR from entry (Gold M15 typical: $8–48)\n"
    "- SL distance from entry < 1.0×ATR → REJECT (technical error). SL > 6.0×ATR → WARNING only, trade allowed if R:R ≥ 1.5\n"
    "\n"
    "# WHEN TO TRADE\n"
    "✅ Clear trend on HTF with LTF entry signal\n"
    "✅ Price at quality OB or FVG\n"
    "✅ Liquidity sweep + reversal structure\n"
    "✅ BOS/CHoCH with follow-through\n"
    "✅ R:R >= 1.5 with defined invalidation\n"
    "\n"
    "# WHEN TO WAIT\n"
    "❌ Market closed (Sat, Sun before 22:00 UTC, Fri after 22:00 UTC)\n"
    "❌ High-impact news within 30 minutes\n"
    "❌ HTF and LTF in direct conflict\n"
    "❌ Price stuck in equilibrium with no structure break\n"
    "❌ R:R below 1.5\n"
    "❌ No clear SMC setup visible\n"
    "\n"
    "# USER IDEA VALIDATION\n"
    "If '<user_trading_idea>' is provided:\n"
    "- Validate against current market structure\n"
    "- If good idea → confirm and refine if needed\n"
    "- If risky → explain why and suggest improvements\n"
    "\n"
    "# SESSION-AWARE ANALYSIS\n"
    "Use the current session (Tokyo, London, NY, etc.) in your assessment:\n"
    "- Tokyo: Historically profitable for Gold; treat as valid session. Slightly higher confidence threshold (e.g. 55) for marginal setups.\n"
    "- London / London-NY overlap: Highest volume; standard confidence rules. Best for swing confirmations.\n"
    "- NY only / Off-hours: Lower volume; prefer only A/A+ setups with clear liquidity sweep + CHoCH.\n"
    "Session is CONTEXT: a complete model (e.g. LIQUIDITY_SWEEP_REVERSAL with 4/4 elements) at B+ grade or above can be traded in any session. B Setup (confidence 55-59) is restricted to London and London/NY sessions only.\n"
    "\n"
    "# OUTPUT FORMAT (STRICT JSON)\n"
    "\n"
    "## For BUY or SELL:\n"
    "```json\n"
    "{\n"
    '  "executive_summary": "Describe the SMC setup and reasoning",\n'
    '  "signal": {\n'
    '    "action": "BUY" | "SELL",\n'
    '    "confidence": 55-100,\n'
    '    "setup_grade": "A+" | "A" | "B+" | "B",\n'
    '    "setup_type": "OB_RETEST" | "FVG_FILL" | "LIQUIDITY_SWEEP" | "BOS_CONTINUATION" | "CHOCH_REVERSAL",\n'
    '    "model": "LIQUIDITY_SWEEP_REVERSAL" | "BOS_CONTINUATION" | "RANGE_SWEEP" | "OB_RETEST" | "NONE",\n'
    '    "model_completeness": {"element_1": true|false, "element_2": true|false, ...}\n'
    "  },\n"
    '  "confluence": {\n'
    '    "htf_aligned": true,\n'
    '    "ltf_trigger_confirmed": true,\n'
    '    "no_news_soon": true,\n'
    '    "rr_acceptable": true,\n'
    '    "invalidation_respected": true\n'
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
    '    "final_tp1": Float (optional — first target for 50% of position, nearest level; if omitted, single TP used),\n'
    '    "tp_logic": "LIQUIDITY_TARGET" | "OB_TARGET" | "FVG_TARGET" | "FIXED_RR",\n'
    '    "entry_quality": "GOOD" | "OK" | "RISKY",\n'
    '    "entry_warning": "NONE" | "LATE_ENTRY" | "FAR_FROM_LEVEL" | "WEAK_STRUCTURE" | "AGAINST_HTF_TREND",\n'
    '    "invalidation_condition": "What price action invalidates this trade"\n'
    "  }\n"
    "}\n"
    "```\n"
    "CRITICAL: Always specify signal.model (which of the 4 models applies) and signal.model_completeness (which required elements are present/missing). If no complete model applies → model: \"NONE\" → action: \"WAIT\".\n"
    "Entry (final_entry) должен быть БЛИЗКО к CURRENT_PRICE: в пределах 0.5×ATR или примерно 0.3% от него.\n"
    "Не выбирай точку входа слишком далеко от текущей цены, даже если уровень выглядит привлекательным.\n"
    "Если вход формально валиден, но поздний/сомнительный (далеко от уровня, слабая структура, против HTF тренда),\n"
    "всё равно давай BUY/SELL при выполнении правил Range Breakout, НО заполняй trade_plan.entry_quality и\n"
    "trade_plan.entry_warning, чтобы описать риск качества входа. НЕ переводить такой сигнал в WAIT только\n"
    "из-за entry_warning, если остальные правила (R:R, invalidation, структура) соблюдены.\n"
    "If any confluence field is false, action MUST be WAIT.\n"
    "EXCEPTION — Range Breakout / Range Rejection mode: if <range_breakout_context> is present and 'is_confirmed'=true OR 'is_range_rejection'=true, "
    "the system has already mathematically confirmed the breakout before calling you. "
    "Set 'ltf_trigger_confirmed' = true unconditionally — the confirmation has already been done by the system. "
    "Do NOT output WAIT solely because BOS/CHoCH is absent.\n"
    "\n"
    "## For WAIT:\n"
    "```json\n"
    "{\n"
    '  "executive_summary": "Why current conditions are not suitable for trading",\n'
    '  "signal": {\n'
    '    "action": "WAIT",\n'
    '    "confidence": 0-54\n'
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

    MANAGER_SYSTEM_PROMPT = (
        "You are a Trade Manager for Gold (XAUUSD).\n"
        "You manage EXISTING positions only. You NEVER open new trades.\n"
        "\n"
        "Your decisions are based on:\n"
        "1. Current price action relative to the open position\n"
        "2. Market structure changes on M5 that threaten the trade\n"
        "3. HTF (H4/H1) context — if HTF still supports the trade, minor M5 noise is acceptable\n"
        "4. Risk management triggers provided to you\n"
        "\n"
        "You are conservative: prefer HOLD unless there is clear evidence\n"
        "the trade thesis is broken or profit should be locked.\n"
        "\n"
        "## DECISION FRAMEWORK\n"
        "\n"
        "### HOLD (default — choose this unless clear reason not to):\n"
        "- Price is moving toward TP, structure intact\n"
        "- Minor pullback within normal retracement (< 50% of current profit)\n"
        "- HTF trend still supports the position\n"
        "- No active triggers or triggers are minor (e.g., slight consolidation)\n"
        "\n"
        "### MOVE_SL_BE (lock in breakeven):\n"
        "- Recommend when the stop has NOT yet been moved to BE or 1R (if already at BE/1R by automation, prefer HOLD or CLOSE_50/CLOSE_ALL as appropriate).\n"
        "- Price has moved >= 1R in favor AND is now pulling back\n"
        "- Opposite internal structure forming but not confirmed\n"
        "- News approaching within 5 minutes\n"
        "- Price reached 70%+ of path to TP then reversed\n"
        "\n"
        "### CLOSE_50 (partial profit):\n"
        "- Price reached 1.5R+ but showing signs of exhaustion\n"
        "- Strong opposite candle appeared but trend not broken\n"
        "- Taking profit before major news\n"
        "- HTF level/zone reached (potential reversal area)\n"
        "\n"
        "### CLOSE_ALL (exit completely):\n"
        "- Confirmed opposite CHoCH on M5 that breaks the trade thesis\n"
        "- Price broke back through entry significantly\n"
        "- 3+ consecutive opposite candles with increasing volume\n"
        "- HTF structure changed against the position\n"
        "- Price stuck against position for 2+ hours with no progress\n"
        "\n"
        "### IMPORTANT:\n"
        "- If \"reached_1r\" trigger is active: MINIMUM action is MOVE_SL_BE\n"
        "- If HTF still supports trade: prefer HOLD over premature close\n"
        "- Do NOT close just because price pulled back slightly — pullbacks are normal in trends\n"
        "\n"
        "<htf_priority_rule>\n"
        "HTF OVERRIDES M5 in the following way:\n"
        "- If H4 trend = same direction as position AND H1 structure intact:\n"
        "  -> M5 noise is NOT a reason to close. Prefer HOLD.\n"
        "  -> Only CLOSE if M5 shows CONFIRMED opposite CHoCH with full candle body close\n"
        "- If H4 trend = opposite to position:\n"
        "  -> Be more cautious. M5 opposite structure = consider CLOSE_50 or CLOSE_ALL\n"
        "- If H1 structure just broke against position:\n"
        "  -> Seriously consider CLOSE_ALL regardless of M5\n"
        "</htf_priority_rule>\n"
    )

    def __init__(
        self, 
        openrouter_key: Optional[str] = None, 
        gemini_key: Optional[str] = None,
        gemini_manager_key: Optional[str] = None,
        gateway_url: Optional[str] = None,
        gateway_key: Optional[str] = None
    ):
        """
        Инициализация сервиса
        
        Args:
            openrouter_key: OpenRouter API ключ
            gemini_key: Gemini API ключ (анализ, сигналы)
            gemini_manager_key: Отдельный Gemini ключ для менеджера сделок (опционально)
            gateway_url: URL для AI Gateway
            gateway_key: API ключ для AI Gateway (опционально)
        """
        self.openrouter_key = openrouter_key
        self.gemini_key = gemini_key
        self.gemini_manager_key = gemini_manager_key or gemini_key  # fallback на основной ключ
        self.gateway_url = gateway_url
        self.gateway_key = gateway_key
        self.session = TradingSession()
        
        # Логируем статус API ключей
        if self.openrouter_key:
            logger.info("LLM Service: OpenRouter API key configured")
        if self.gemini_key:
            logger.info("LLM Service: Gemini API key configured")
        if self.gemini_manager_key and self.gemini_manager_key != self.gemini_key:
            logger.info("LLM Service: Gemini Manager API key configured (separate from main)")
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
        # Используем общую функцию для формирования промпта
        user_prompt_text = _build_common_prompt_section(
            current_time_utc=current_time_utc,
            session_info=session_info,
            news_data=news_data,
            computed_levels=computed_levels,
            technical_data=technical_data,
            balance=balance,
            daily_loss_limit=daily_loss_limit,
            risk_percent=risk_percent,
            language=language,
            user_idea=user_idea,
            manual_entry=manual_entry,
            manual_sl=manual_sl,
            manual_tp=manual_tp,
            include_visual_section=True
        )

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
                ],
                "max_tokens": 8192
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
                timeout=120
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
        """Анализ через Gemini API с fallback на OpenRouter Gemini 2.0 Flash"""
        if not self.gemini_key:
            return {"error": "GEMINI_API_KEY not configured"}

        # Получаем текущую сессию
        current_time = datetime.now(timezone.utc)
        session_info = self.session.get_current_session(current_time)
        time_str = current_time.strftime("%Y-%m-%d %H:%M UTC")

        # v8.6 MUST-HAVE: инвалидация, ATR, текущая цена — для жёсткой валидации SL и R:R
        inv_levels = (technical_data or {}).get("invalidation_levels") or {}
        atr_m15 = (technical_data or {}).get("atr_m15") or 0
        cur_price = (technical_data or {}).get("current_price") or 0
        
        invalidation_data = None
        if inv_levels or atr_m15 or cur_price:
            invalidation_data = {
                "invalidation_levels": inv_levels,
                "atr_m15": atr_m15,
                "current_price": cur_price
            }
        
        # HTF контекст (H4, H1) для Step 1 — макро-тренд и зоны (если передан охотником)
        htf_context = technical_data.get("htf_context") if isinstance(technical_data, dict) else None

        # Формируем текстовый промпт через общую функцию (секция visual — только при наличии картинок)
        user_prompt_text = _build_common_prompt_section(
            current_time_utc=time_str,
            session_info=session_info,
            news_data=news_data,
            computed_levels=computed_levels,
            technical_data=technical_data,
            balance=balance,
            daily_loss_limit=daily_loss_limit,
            risk_percent=risk_percent,
            language=language,
            user_idea=user_idea,
            manual_entry=manual_entry,
            manual_sl=manual_sl,
            manual_tp=manual_tp,
            include_visual_section=bool(chart_images),
            invalidation_data=invalidation_data,
            htf_context=htf_context
        )
        
        # Добавляем специфичный task для Gemini с учётом invalidation и HTF
        task_addition = """
<task>
1. If <htf_context> is present: use it for Step 1 (HTF Bias). Align M15 setup with H4/H1 trend and zone.
2. Synthesize the News, Data, and Technical Analysis (M15 + HTF).
3. Validate the setup using the "Decision Protocol" from System Instructions.
4. RESPECT <invalidation_and_atr>: SL beyond invalidation, R:R >= 1.5, entry near current price.
5. If outputting BUY/SELL, include "confluence" object with: htf_aligned, ltf_trigger_confirmed, no_news_soon, rr_acceptable, invalidation_respected (all true for valid trade).
6. Output the STRICT JSON decision with the required fields.
</task>
"""
        # Заменяем стандартный <task> на расширенную версию
        if "<task>" in user_prompt_text:
            start = user_prompt_text.find("<task>")
            end = user_prompt_text.find("</task>") + len("</task>")
            user_prompt_text = user_prompt_text[:start] + task_addition + user_prompt_text[end:]

        # Формируем полный промпт (системный + пользовательский)
        full_prompt = self.SYSTEM_PROMPT + "\n\n" + user_prompt_text

        # Собираем части запроса (текст + опциональные картинки)
        parts = [{"text": full_prompt}]
        if chart_images:
            for _key, b64 in chart_images.items():
                if b64:
                    parts.append({"inline_data": {"mime_type": "image/png", "data": b64}})

        def _call_gemini() -> Dict[str, Any]:
            """Внутренняя функция вызова прямого Gemini API."""
            logger.info(f"Sending request to Gemini (model: {self.GEMINI_MODEL})")

            url = f"{self.GEMINI_API_URL}/{self.GEMINI_MODEL}:generateContent?key={self.gemini_key}"
            logger.debug(f"Gemini API URL: {self.GEMINI_API_URL}/{self.GEMINI_MODEL}:generateContent")

            payload = {
                "contents": [{"parts": parts}],
                "generationConfig": {
                    "maxOutputTokens": 8192,
                    "temperature": 0.7,
                }
            }
            logger.info(f"Payload size: {len(full_prompt)} characters")

            current_ip = get_current_ip()
            if current_ip:
                logger.info(f"Current external IP: {current_ip}")

            response = requests.post(
                url,
                json=payload,
                headers={"Content-Type": "application/json"},
                timeout=120
            )

            if response.status_code == 429:
                logger.warning("Gemini API rate limit exceeded")
                return {"error": "Gemini API rate limit exceeded", "status": 429}

            if response.status_code != 200:
                logger.error(f"Gemini API error: {response.status_code}")
                try:
                    error_details = response.json()
                    logger.error(f"Gemini Error Details: {json.dumps(error_details, indent=2, ensure_ascii=False)}")
                except Exception:
                    logger.error(f"Gemini Error Raw Body: {response.text}")
                return {
                    "error": f"Gemini API error: {response.status_code}",
                    "status": response.status_code,
                    "details": response.text,
                }

            result = response.json()
            logger.info("Successfully received response from Gemini")

            if result.get("candidates") and result["candidates"][0].get("content"):
                assistant_message = result["candidates"][0]["content"]["parts"][0]["text"]
                logger.info(f"Response received: {len(assistant_message)} characters")
                parsed_json = parse_json_response(assistant_message)
                response_data = {
                    "success": True,
                    "model": self.GEMINI_MODEL,
                    "session_info": session_info,
                    "timestamp": time_str,
                    "response": assistant_message,
                    "usage": result.get("usageMetadata", {}),
                    "raw_response": result,
                }
                if parsed_json:
                    response_data["parsed_decision"] = parsed_json
                    logger.info("✓ Successfully parsed JSON from Gemini response")
                    logger.debug(f"Parsed decision: {json.dumps(parsed_json, indent=2, ensure_ascii=False)}")
                else:
                    logger.warning("⚠ Failed to parse JSON from Gemini response - returning raw text")
                return response_data

            logger.error("Invalid Gemini API response structure")
            return {"error": "Invalid Gemini API response structure"}

        def _call_openrouter_fallback() -> Dict[str, Any]:
            """Fallback: вызов OpenRouter с моделью Gemini 2.0 Flash при ошибке Gemini."""
            openrouter_key = os.environ.get("OPENROUTER_API_KEY") or os.environ.get("OPENROUTER_API_KEY".lower())
            if not openrouter_key:
                logger.warning("⚠️ OpenRouter fallback requested but OPENROUTER_API_KEY is not configured")
                return {"error": "OpenRouter API key not configured"}

            logger.warning("⚠️ Gemini 503/timeout — переключаемся на OpenRouter Gemini 2.0 Flash")

            # Собираем контент в формате OpenRouter (OpenAI-совместимый)
            content = [{"type": "text", "text": full_prompt}]
            if chart_images:
                for _key, b64 in chart_images.items():
                    if b64:
                        content.append(
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:image/png;base64,{b64}",
                                },
                            }
                        )

            payload = {
                "model": "google/gemini-2.0-flash-001",
                "messages": [
                    {
                        "role": "user",
                        "content": content,
                    }
                ],
                "max_tokens": 4000,
            }

            try:
                response = requests.post(
                    self.OPENROUTER_API_URL,
                    json=payload,
                    headers={
                        "Content-Type": "application/json",
                        "Authorization": f"Bearer {openrouter_key}",
                    },
                    timeout=120,
                )
                response.raise_for_status()
                result = response.json()

                if result.get("choices"):
                    assistant_message = result["choices"][0]["message"]["content"]
                    logger.info("✅ OpenRouter fallback успешен")
                    parsed_json = parse_json_response(assistant_message)
                    data = {
                        "success": True,
                        "model": "google/gemini-2.0-flash-001",
                        "session_info": session_info,
                        "timestamp": time_str,
                        "response": assistant_message,
                        "usage": result.get("usage", {}),
                        "raw_response": result,
                    }
                    if parsed_json:
                        data["parsed_decision"] = parsed_json
                    return data

                logger.error("Invalid OpenRouter fallback response structure")
                return {"error": "Invalid OpenRouter fallback response"}
            except Exception as e:
                logger.error(f"❌ OpenRouter fallback тоже упал: {e}")
                return {
                    "error": "OpenRouter fallback failed",
                    "details": str(e),
                }

        # Основной вызов Gemini с обработкой ошибок и fallback
        try:
            primary_result = _call_gemini()
        except requests.exceptions.Timeout:
            logger.error("Gemini API timeout")
            primary_result = {"error": "Gemini API timeout", "status": 504}
        except requests.exceptions.RequestException as e:
            logger.error(f"Request error: {str(e)}")
            primary_result = {
                "error": "Failed to connect to Gemini",
                "details": str(e),
            }
        except Exception as e:
            logger.error(f"Error in _analyze_with_gemini: {str(e)}", exc_info=True)
            primary_result = {
                "error": "Internal error during Gemini analysis",
                "details": str(e),
            }

        status_code = primary_result.get("status")
        error_text = str(primary_result.get("error", "")).lower()

        should_fallback = (
            status_code in (503, 429)
            or "503" in error_text
            or "429" in error_text
            or "timeout" in error_text
            or "rate limit" in error_text
        )

        if should_fallback:
            fallback_result = _call_openrouter_fallback()
            if fallback_result.get("success"):
                return fallback_result
            # Если fallback тоже упал — возвращаем исходную ошибку Gemini/OpenRouter
            return fallback_result

        return primary_result
    
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
                ],
                "max_tokens": 8192
            }
            
            # Логируем IP адрес перед запросом
            current_ip = get_current_ip()
            if current_ip:
                logger.info(f"Current external IP: {current_ip}")
            
            logger.info(f"Sending request to AI Gateway ({gateway_url}) with model: {self.GATEWAY_MODEL}")
            logger.debug(f"Request URL: {gateway_url}")
            
            # Отправляем запрос с таймаутом (не блокируем Render)
            response = requests.post(
                gateway_url,
                headers=headers,
                json=payload,
                timeout=120
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

    def get_signal_verdict(self, analysis_data: Dict) -> Union[Dict[str, str], str]:
        """
        Специальный метод для Наблюдателя.
        Использует ПОЛНЫЙ системный промт и Gemini 3 Flash для авто-анализа.
        При успехе возвращает {"response": "...", "model": "..."}; при ошибке — строку с JSON WAIT.
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
                chart_images=analysis_data.get('chart_images', {}),
                language='ru'
            )

            if result.get("success"):
                # Возвращаем ответ и модель, чтобы в отчёте корректно отображалась платная/бесплатная LLM
                return {
                    "response": result.get("response", ""),
                    "model": result.get("model", "")
                }

            # Оба LLM (Gemini и OpenRouter) недоступны — возвращаем None, чтобы watcher отправил уведомление в TG
            logger.warning("⚠️ Gemini и OpenRouter не дали ответ — возвращаем None")
            return None

        except (requests.exceptions.Timeout, Exception) as e:
            logger.warning(f"⚠️ Gemini таймаут/ошибка: {e} — переключаемся на OpenRouter или возвращаем None")
            return None

    def manage_active_trade(
        self,
        trade_context: Dict,
        technical_context: Dict,
        triggers: Dict,
        recommendation_context: Optional[str] = None,
    ) -> Optional[str]:
        """
        Менеджер-агент для управления уже ОТКРЫТОЙ сделкой.

        recommendation_context: optional string with previous manager decisions (Task 9).

        Ожидаемый JSON-ответ от модели:
        {
          "manager_action": "HOLD" | "MOVE_SL_BE" | "CLOSE_50" | "CLOSE_ALL",
          "reason": "текстовое объяснение"
        }
        """
        try:
            manager_key = self.gemini_manager_key or self.gemini_key
            if not manager_key:
                return json.dumps({
                    "manager_action": "HOLD",
                    "reason": "GEMINI_API_KEY / GEMINI_API_KEY_MANAGER not configured, fallback to HOLD."
                }, ensure_ascii=False)

            current_time = datetime.now(timezone.utc)
            time_str = current_time.strftime("%Y-%m-%d %H:%M UTC")

            # Определяем статус рынка (как в _analyze_with_gemini)
            weekday = current_time.weekday()  # 0=Monday, 6=Sunday
            hour = current_time.hour
            market_status = "OPEN"
            if weekday == 5:
                market_status = "CLOSED"
            elif weekday == 6 and hour < 22:
                market_status = "CLOSED"
            elif weekday == 4 and hour >= 22:
                market_status = "CLOSED"

            # Сессия
            session_info = self.session.get_current_session(current_time)

            # Собираем человекочитаемый контекст сделки
            direction = trade_context.get('signal_type')
            entry = trade_context.get('entry_price')
            sl = trade_context.get('stop_loss')
            tp = trade_context.get('take_profit')
            tp1 = trade_context.get('take_profit_1')  # Double TP: первый тейк на 50% позиции
            current_price = trade_context.get('current_price')
            progress_pct = (trade_context.get('progress_ratio') or 0.0) * 100.0

            # Описание триггеров
            trigger_desc = []
            if triggers.get('stuck_against'):
                trigger_desc.append("Price is consolidating against the position on M5.")
            if triggers.get('news_soon'):
                trigger_desc.append("High-impact USD news within 5 minutes.")
            if triggers.get('opposite_structure'):
                trigger_desc.append("Opposite SMC structure detected on M5.")
            if triggers.get('reached_1r'):
                trigger_desc.append("Price has reached or passed 1R in favor of the position (unrealized profit >= 1R).")

            triggers_text = "\n".join(f"- {t}" for t in trigger_desc) if trigger_desc else "None detected."

            # HTF (H4, H1) контекст для менеджера — если передан, добавляем в промпт
            htf_block = ""
            htf_context = technical_context.get("htf_context") if isinstance(technical_context, dict) else None
            if htf_context:
                htf_block = f"""
<htf_context>
Higher timeframe bias (H4 → H1). Use for context: if HTF trend and zone still support the position, do not overreact to M5 noise or local pullbacks.
{json.dumps(htf_context, indent=2)}
</htf_context>
"""
            # M5 контекст без htf_context в выводе (он уже в отдельном блоке)
            technical_m5_for_prompt = {k: v for k, v in technical_context.items() if k != "htf_context"} if isinstance(technical_context, dict) else technical_context

            # Double TP информация для менеджера
            tp1_info = f"\nTake Profit 1 (50%): {tp1}" if tp1 and tp1 > 0 else ""

            manager_prompt = f"""
TRADE MANAGEMENT REQUEST for XAUUSD

<environment>
Current Time (UTC): {time_str}
Market Status: {market_status} (Gold market opens Sunday 22:00 UTC, closes Friday 22:00 UTC)
Active Session: {session_info['description']}
Session Details: {json.dumps(session_info, indent=2)}
</environment>

<active_trade>
Direction: {direction}
Entry Price: {entry}
Stop Loss: {sl}
Take Profit: {tp}{tp1_info}
Current Price: {current_price}
Progress To TP: {progress_pct:.1f}%
</active_trade>

<manager_triggers>
The following management triggers are currently active:
{triggers_text}
</manager_triggers>
{htf_block}
<technical_context_m5>
Last M5 candles and SMC context (use for entry-level structure and triggers):
{json.dumps(technical_m5_for_prompt, indent=2)}
</technical_context_m5>

<task>
You are NOT allowed to open new trades.
You ONLY manage the existing position described above.

Use <htf_context> if present for bias (H4/H1 trend and zone). Do not close the position only because of M5 noise if HTF trend and zone still support the position. Then consider M5 triggers and candles.
When "reached_1r" (price at or above 1R profit) is among the triggers, you MAY recommend CLOSE_ALL or CLOSE_50 to lock profit if structure or momentum no longer justify holding to TP; otherwise HOLD or MOVE_SL_BE.
Decide ONE of the following management actions:
1) HOLD         → keep position and all levels unchanged
2) MOVE_SL_BE   → move Stop Loss to BreakEven (only if stop is not already at BE or 1R by automation)
3) CLOSE_50     → close 50% of the position size
4) CLOSE_ALL    → close the full position now

CRITICAL:
- You MUST respond in STRICT JSON.
- Top-level fields:
  - manager_action: one of "HOLD", "MOVE_SL_BE", "CLOSE_50", "CLOSE_ALL"
  - reason: short explanation in Russian language.

Example:
{{
  "manager_action": "HOLD",
  "reason": "Цена откатила, но структура тренда вверх сохраняется, TP реалистичен."
}}
</task>
"""
            if recommendation_context:
                manager_prompt += f"\n\n{recommendation_context}"

            full_prompt = self.MANAGER_SYSTEM_PROMPT + "\n\n" + manager_prompt

            logger.info("Sending trade management request to Gemini (Manager Agent).")
            url = f"{self.GEMINI_API_URL}/{self.GEMINI_MODEL}:generateContent?key={manager_key}"
            logger.debug(f"Gemini Manager API URL: {self.GEMINI_API_URL}/{self.GEMINI_MODEL}:generateContent")

            payload = {
                "contents": [{"parts": [{"text": full_prompt}]}],
                "generationConfig": {
                    "maxOutputTokens": 2048,
                    "temperature": 0.4,
                }
            }

            current_ip = get_current_ip()
            if current_ip:
                logger.info(f"Manager Agent external IP: {current_ip}")

            response = requests.post(url, json=payload, timeout=120)
            if response.status_code == 429:
                logger.warning(
                    "Manager Agent: 429 Too Many Requests from Gemini API. "
                    "Skipping verdict this cycle (no fake HOLD)."
                )
                return None
            response.raise_for_status()
            result = response.json()

            if (
                'candidates' in result and
                result['candidates'] and
                'content' in result['candidates'][0] and
                'parts' in result['candidates'][0]['content'] and
                result['candidates'][0]['content']['parts']
            ):
                assistant_message = result['candidates'][0]['content']['parts'][0].get('text', '')
                logger.info("Manager Agent: successfully received response from Gemini.")
                return assistant_message

            logger.error(f"Manager Agent: unexpected Gemini response format: {result}")
            return json.dumps({
                "manager_action": "HOLD",
                "reason": "Unexpected Gemini response format, defaulting to HOLD."
            }, ensure_ascii=False)

        except requests.exceptions.HTTPError as e:
            if e.response is not None and e.response.status_code == 429:
                logger.warning("Manager Agent: 429 from Gemini (HTTPError). Skipping verdict this cycle.")
                return None
            logger.error(f"Error in manage_active_trade: {e}", exc_info=True)
            fallback = {
                "manager_action": "HOLD",
                "reason": f"Ошибка при анализе менеджера сделки: {str(e)}"
            }
            return json.dumps(fallback, ensure_ascii=False)
        except Exception as e:
            logger.error(f"Error in manage_active_trade: {e}", exc_info=True)
            fallback = {
                "manager_action": "HOLD",
                "reason": f"Ошибка при анализе менеджера сделки: {str(e)}"
            }
            return json.dumps(fallback, ensure_ascii=False)

# Синглтон инстанс
llm_service = LLMService(
    openrouter_key=OPENROUTER_API_KEY, 
    gemini_key=GEMINI_API_KEY,
    gemini_manager_key=GEMINI_API_KEY_MANAGER,
    gateway_url=AI_GATEWAY_URL,
    gateway_key=AI_GATEWAY_KEY
)
