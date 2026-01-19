"""
Роуты для анализа и расчетов
"""
from flask import Blueprint, jsonify, request
import logging

from services.calculator import calculator
from services.yfinance_service import yfinance_service
from config.settings import START_BALANCE

logger = logging.getLogger(__name__)

analysis_bp = Blueprint('analysis', __name__)


@analysis_bp.route('/calculate', methods=['POST'])
def calculate_trade():
    """
    Расчет параметров сделки (лот, R:R, риск)
    
    Body params:
        entry: точка входа
        sl: stop loss
        tp: take profit
        balance: баланс счета
    """
    try:
        data = request.get_json()
        
        entry = float(data.get('entry', 0))
        sl = float(data.get('sl', 0))
        tp = float(data.get('tp', 0))
        balance = float(data.get('balance', START_BALANCE))
        
        if not all([entry, sl, tp]):
            return jsonify({"error": "Недостаточно параметров"}), 400
        
        result = calculator.calculate_trade_params(entry, sl, tp, balance)
        
        if "error" in result:
            return jsonify(result), 400
            
        return jsonify(result)
        
    except ValueError as e:
        logger.error(f"Invalid input in /calculate: {str(e)}")
        return jsonify({"error": "Некорректные входные данные"}), 400
    except Exception as e:
        logger.error(f"Error in /calculate: {str(e)}")
        return jsonify({"error": str(e)}), 500


@analysis_bp.route('/analyze', methods=['POST'])
def analyze_trade():
    """
    AI анализ торговой сделки (DEPRECATED)
    
    ⚠️ DEPRECATED: Используйте новый endpoint /api/llm/analyze
    
    Этот endpoint был заменен на более мощный LLM сервис:
    - GET /api/llm/analyze - Полный анализ с графиками, новостями и SMC уровнями
    - GET /api/llm/analyze?model=gemini3 - Gemini 3 Pro
    - GET /api/llm/analyze?model=openrouter - OpenRouter (по умолчанию)
    """
    return jsonify({
        "error": "This endpoint is deprecated",
        "message": "Use /api/llm/analyze instead",
        "new_endpoint": {
            "url": "/api/llm/analyze",
            "method": "GET",
            "params": {
                "model": "gemini3 or openrouter (default)"
            },
            "examples": [
                "/api/llm/analyze",
                "/api/llm/analyze?model=gemini3"
            ]
        }
    }), 410  # 410 Gone - endpoint устарел


@analysis_bp.route('/breakeven', methods=['POST'])
def calculate_breakeven():
    """
    Расчет уровня безубытка
    
    Body params:
        entry: точка входа
        sl: stop loss
        commission: комиссия (опционально)
    """
    try:
        data = request.get_json()
        
        entry = float(data.get('entry', 0))
        sl = float(data.get('sl', 0))
        commission = float(data.get('commission', 0))
        
        if not all([entry, sl]):
            return jsonify({"error": "Недостаточно параметров"}), 400
        
        be_level = calculator.calculate_breakeven(entry, sl, commission)
        
        return jsonify({
            "breakeven_level": be_level,
            "entry": entry,
            "sl": sl
        })
        
    except Exception as e:
        logger.error(f"Error in /breakeven: {str(e)}")
        return jsonify({"error": str(e)}), 500


@analysis_bp.route('/drawdown', methods=['POST'])
def calculate_drawdown():
    """
    Расчет дневной просадки
    
    Body params:
        start_balance: начальный баланс
        current_equity: текущий эквити
        daily_limit: лимит дневной просадки
    """
    try:
        data = request.get_json()
        
        start_balance = float(data.get('start_balance', START_BALANCE))
        current_equity = float(data.get('current_equity', START_BALANCE))
        daily_limit = float(data.get('daily_limit', 250))
        
        result = calculator.calculate_daily_drawdown(
            start_balance,
            current_equity,
            daily_limit
        )
        
        return jsonify(result)
        
    except Exception as e:
        logger.error(f"Error in /drawdown: {str(e)}")
        return jsonify({"error": str(e)}), 500


@analysis_bp.route('/ai-status')
def ai_status():
    """
    Проверка доступности AI сервиса (DEPRECATED)
    
    ⚠️ DEPRECATED: Используйте /api/llm/status
    """
    return jsonify({
        "error": "This endpoint is deprecated",
        "message": "Use /api/llm/status instead",
        "new_endpoint": "/api/llm/status"
    }), 410  # 410 Gone
