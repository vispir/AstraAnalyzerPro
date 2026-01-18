"""
Сервис для работы с Gemini AI API
"""
import requests
import logging
from typing import Dict, List, Optional

from config.settings import GEMINI_API_KEY

logger = logging.getLogger(__name__)


class GeminiService:
    """Сервис для AI анализа через Gemini API"""
    
    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or GEMINI_API_KEY
        self.base_url = "https://generativelanguage.googleapis.com/v1beta/models"
        self.model = "gemini-2.0-flash-exp"
        
    def analyze_trade(
        self,
        entry: float,
        sl: float,
        tp: float,
        balance: float,
        equity: float,
        lot: float,
        ai_context: Dict
    ) -> Dict:
        """
        Анализ торговой сделки через AI
        
        Args:
            entry: Точка входа
            sl: Stop Loss
            tp: Take Profit
            balance: Баланс счета
            equity: Эквити
            lot: Размер лота
            ai_context: Контекст рынка по разным таймфреймам
            
        Returns:
            Dict с результатом анализа
        """
        if not self.api_key:
            return {"error": "GEMINI_API_KEY не настроен"}
        
        try:
            # Определение направления
            direction = "BUY" if entry > sl else "SELL"
            
            # Форматирование OHLC данных
            def format_pa(candles: List[Dict]) -> str:
                if not candles:
                    return "нет данных"
                formatted = []
                for c in candles[:10]:  # Только последние 10 свечей
                    h = c.get('h', 0)
                    l = c.get('l', 0)
                    cl = c.get('c', 0)
                    formatted.append(f"[H:{h} L:{l} C:{cl}]")
                return "|".join(formatted)
            
            # Формирование промпта
            prompt = f"""
# ROLE
Institutional Advisor. Specialty: XAU/USD. 

# SOURCES OF TRUTH
- Account: ${balance} | Equity: ${equity}.
- Proposed: {direction} at {entry} | SL: {sl} | TP: {tp} | Lot: {lot}.
- Context (OHLC): H4: {format_pa(ai_context.get('H4', []))} | H1: {format_pa(ai_context.get('H1', []))} | M15: {format_pa(ai_context.get('M15', []))}

# TASK
1. Analyze market structure (MSS, Liquidity). 
2. If the user's plan is suboptimal, propose a better "AI SETUP".
3. Provide precise BE level.

# OUTPUT PROTOCOL (ULTRA-CONCISE)
- LANGUAGE: Russian.
- FORMAT: Max 3 short sections. No Markdown, no fillers.
- LIMIT: Max 2 sentences per section.

# FINAL RESPONSE STRUCTURE:

ВЕРДИКТ: [ВХОДИТЬ / ЖДАТЬ / ОТМЕНА] (Риск: X/10)

АНАЛИЗ И ЛОГИКА
(Кратко: состояние ТФ и Price Action. Почему выбран такой вердикт.)

AI SETUP (ПРЕДЛОЖЕНИЕ ИИ)
(Если твой вход плохой, ИИ пишет: Вход: X, SL: Y, TP: Z. Если твой вход ок, пишет: "Твой план оптимален".)

СТРАТЕГИЯ УПРАВЛЕНИЯ
(Уровень безубытка BE и краткое предупреждение.)
""".strip()
            
            # Запрос к Gemini API
            url = f"{self.base_url}/{self.model}:generateContent?key={self.api_key}"
            
            response = requests.post(
                url,
                json={"contents": [{"parts": [{"text": prompt}]}]},
                headers={"Content-Type": "application/json"},
                timeout=30
            )
            
            if response.status_code == 429:
                logger.warning("Gemini API rate limit exceeded")
                return {"error": "ЛИМИТ ЗАПРОСОВ AI", "status": 429}
            
            if response.status_code != 200:
                logger.error(f"Gemini API error: {response.status_code}")
                return {"error": f"Ошибка AI API: {response.status_code}", "status": response.status_code}
            
            res_data = response.json()
            
            if res_data.get('candidates') and res_data['candidates'][0].get('content'):
                text = res_data['candidates'][0]['content']['parts'][0]['text']
                # Убираем Markdown артефакты
                clean_text = text.replace('*', '').replace('#', '').strip()
                return {
                    "success": True,
                    "analysis": clean_text
                }
            else:
                logger.error("Invalid Gemini API response structure")
                return {"error": "Некорректный ответ от AI"}
                
        except requests.Timeout:
            logger.error("Gemini API timeout")
            return {"error": "AI не отвечает. Проверьте соединение.", "status": 504}
        except Exception as e:
            logger.error(f"Error in AI analysis: {str(e)}")
            return {"error": f"Ошибка AI анализа: {str(e)}"}
    
    def is_available(self) -> bool:
        """
        Проверка доступности API ключа
        
        Returns:
            True если ключ настроен
        """
        return bool(self.api_key)


# Глобальный экземпляр сервиса
gemini_service = GeminiService()
