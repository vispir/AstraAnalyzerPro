"""
Роуты для анализа и расчетов
"""
from flask import Blueprint, jsonify, request
import logging

from services.calculator import calculator
from services.yfinance_service import yfinance_service
from services.twelvedata_service import twelvedata_service
from services.cache_service import cache_service
from services.news_service import news_service
from services.chart_service import chart_service

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
        balance = float(data.get('balance', 5000))
        
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
        
        start_balance = float(data.get('start_balance', 5000))
        current_equity = float(data.get('current_equity', 5000))
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
                twelvedata_service.clear_cache()
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


# --- ЭНДПОИНТЫ ДЛЯ РАБОТЫ С СИГНАЛАМИ ---

@analysis_bp.route('/signals/history')
def get_signals_history():
    """
    Получение истории сигналов
    
    Query params:
        limit: количество сигналов (по умолчанию 10, макс 100)
        type: фильтр по типу ('BUY', 'SELL', 'WAIT')
    
    Returns:
        Список сигналов с полной информацией
    """
    try:
        from services.db_service import db_service
        
        limit = min(int(request.args.get('limit', 10)), 100)
        signal_type = request.args.get('type', None)
        
        if signal_type:
            signal_type = signal_type.upper()
            if signal_type not in ['BUY', 'SELL', 'WAIT']:
                return jsonify({"error": "Invalid signal type. Use: BUY, SELL, or WAIT"}), 400
        
        signals = db_service.get_signals_history(limit=limit, signal_type=signal_type)
        
        return jsonify({
            "success": True,
            "count": len(signals),
            "signals": signals
        })
    except Exception as e:
        logger.error(f"Error getting signals history: {str(e)}")
        return jsonify({"error": str(e)}), 500


@analysis_bp.route('/signals/last')
def get_last_signal():
    """
    Получение последнего сигнала
    
    Query params:
        type: фильтр по типу ('BUY', 'SELL', 'WAIT')
    
    Returns:
        Последний сигнал или null
    """
    try:
        from services.db_service import db_service
        
        signal_type = request.args.get('type', None)
        
        if signal_type:
            signal_type = signal_type.upper()
            if signal_type not in ['BUY', 'SELL', 'WAIT']:
                return jsonify({"error": "Invalid signal type. Use: BUY, SELL, or WAIT"}), 400
        
        signal = db_service.get_last_signal(signal_type=signal_type)
        
        return jsonify({
            "success": True,
            "signal": signal
        })
    except Exception as e:
        logger.error(f"Error getting last signal: {str(e)}")
        return jsonify({"error": str(e)}), 500


@analysis_bp.route('/signals/stats')
def get_signals_stats():
    """
    Получение статистики по сигналам
    
    Returns:
        {
            "total_signals": 156,
            "buy_signals": 78,
            "sell_signals": 65,
            "wait_signals": 13,
            "closed_trades": 120,
            "wins": 72,
            "losses": 48,
            "win_rate_percent": 60.00,
            "avg_pnl": 2.5,
            "total_pnl": 300.0
        }
    """
    try:
        from services.db_service import db_service
        
        stats = db_service.get_signals_stats()
        
        return jsonify({
            "success": True,
            "stats": stats
        })
    except Exception as e:
        logger.error(f"Error getting signals stats: {str(e)}")
        return jsonify({"error": str(e)}), 500


@analysis_bp.route('/signals/<int:signal_id>/result', methods=['PATCH'])
def update_signal_result(signal_id):
    """
    Обновление результата сигнала
    
    Path params:
        signal_id: ID сигнала
    
    Body params:
        result_pnl: результат в пунктах (обязательно)
        close_price: цена закрытия (обязательно)
        status: статус ('closed' или 'cancelled', по умолчанию 'closed')
    
    Returns:
        Статус операции
    """
    try:
        from services.db_service import db_service
        
        data = request.get_json()
        
        result_pnl = data.get('result_pnl')
        close_price = data.get('close_price')
        status = data.get('status', 'closed')
        
        if result_pnl is None or close_price is None:
            return jsonify({
                "error": "Missing required parameters: result_pnl and close_price"
            }), 400
        
        if status not in ['closed', 'cancelled']:
            return jsonify({
                "error": "Invalid status. Use: closed or cancelled"
            }), 400
        
        success = db_service.update_signal_result(
            signal_id=signal_id,
            result_pnl=float(result_pnl),
            close_price=float(close_price),
            status=status
        )
        
        if success:
            return jsonify({
                "success": True,
                "message": f"Signal {signal_id} result updated"
            })
        else:
            return jsonify({
                "success": False,
                "error": "Failed to update signal result"
            }), 500
            
    except ValueError as e:
        logger.error(f"Invalid input in /signals/<id>/result: {str(e)}")
        return jsonify({"error": "Invalid numeric values"}), 400
    except Exception as e:
        logger.error(f"Error updating signal result: {str(e)}")
        return jsonify({"error": str(e)}), 500