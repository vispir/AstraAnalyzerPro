"""
Роуты для анализа и расчетов
"""
from flask import Blueprint, jsonify, request
import logging

from services.calculator import calculator
from services.yfinance_service import yfinance_service
from services.cache_service import cache_service
from services.news_service import news_service
from services.chart_service import chart_service
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


@analysis_bp.route('/cache/stats')
def cache_stats():
    """
    Получение статистики кэша
    
    Returns:
        Общая статистика кэша (размер, попадания, промахи, hit rate)
    """
    try:
        stats = cache_service.get_stats()
        return jsonify({
            "success": True,
            "cache_stats": stats
        })
    except Exception as e:
        logger.error(f"Error getting cache stats: {str(e)}")
        return jsonify({"error": str(e)}), 500


@analysis_bp.route('/cache/info')
def cache_info():
    """
    Получение детальной информации о кэше
    
    Query params:
        prefix: фильтр по префиксу (candles, news, chart_image, etc.)
    
    Returns:
        Список записей в кэше с возрастом, TTL и количеством обращений
    """
    try:
        prefix = request.args.get('prefix', None)
        info = cache_service.get_info(prefix)
        return jsonify({
            "success": True,
            "cache_info": info
        })
    except Exception as e:
        logger.error(f"Error getting cache info: {str(e)}")
        return jsonify({"error": str(e)}), 500


@analysis_bp.route('/cache/clear', methods=['POST'])
def clear_cache():
    """
    Очистка кэша
    
    Body params:
        prefix: префикс для очистки (candles, news, chart_image, calendar, geopolitical)
                если не указан - очищает весь кэш
    
    Returns:
        Количество удаленных записей
    """
    try:
        data = request.get_json() or {}
        prefix = data.get('prefix', None)
        
        if prefix:
            # Очищаем конкретный префикс
            if prefix == 'candles':
                yfinance_service.clear_cache()
            elif prefix == 'chart_image':
                chart_service.clear_cache()
            elif prefix in ['calendar', 'geopolitical']:
                news_service.clear_cache()
            else:
                count = cache_service.clear(prefix)
                return jsonify({
                    "success": True,
                    "message": f"Cache cleared: {prefix}",
                    "entries_removed": count
                })
        else:
            # Очищаем весь кэш
            count = cache_service.clear()
        
        return jsonify({
            "success": True,
            "message": "Cache cleared successfully",
            "entries_removed": count
        })
        
    except Exception as e:
        logger.error(f"Error clearing cache: {str(e)}")
        return jsonify({"error": str(e)}), 500


@analysis_bp.route('/cache/cleanup', methods=['POST'])
def cleanup_cache():
    """
    Удаление истекших записей из кэша
    
    Returns:
        Количество удаленных записей
    """
    try:
        count = cache_service.cleanup_expired()
        return jsonify({
            "success": True,
            "message": "Expired cache entries cleaned up",
            "entries_removed": count
        })
    except Exception as e:
        logger.error(f"Error cleaning up cache: {str(e)}")
        return jsonify({"error": str(e)}), 500
