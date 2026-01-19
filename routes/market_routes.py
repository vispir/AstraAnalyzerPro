"""
Роуты для рыночных данных
"""
from flask import Blueprint, jsonify, request
import logging

from services.yfinance_service import yfinance_service
from services.calculator import calculator
from config.settings import (
    SYMBOL,
    START_BALANCE,
    DAILY_LOSS_LIMIT,
    MAX_LOT_SIZE,
    RISK_PERCENT
)

logger = logging.getLogger(__name__)

market_bp = Blueprint('market', __name__)


@market_bp.route('/candles')
def get_candles():
    """
    Получение свечных данных
    
    Без параметров:
        Возвращает данные по всем таймфреймам:
        - H4: 10 последних свечей
        - H1: 20 последних свечей
        - M15: 50 последних свечей
    
    С параметрами:
        tf: таймфрейм (M15, H1, H4, etc.)
        period: период (1d, 5d, 1mo, etc.) - опционально
        limit: количество последних свечей - опционально
    """
    try:
        timeframe = request.args.get('tf')
        
        # Если таймфрейм не указан - возвращаем данные по всем ТФ
        if not timeframe:
            logger.info("Candles request: all timeframes (default)")
            
            # Получаем данные по трем таймфреймам
            h4_data = yfinance_service.get_candles('H4', limit=10)
            h1_data = yfinance_service.get_candles('H1', limit=20)
            m15_data = yfinance_service.get_candles('M15', limit=50)

            h4_raw = yfinance_service.get_candles('H4', limit=40)
            h1_raw = yfinance_service.get_candles('H1', limit=40)
            m15_raw = yfinance_service.get_candles('M15', limit=60)
            
            # Проверяем на ошибки
            if "error" in h4_data or "error" in h1_data or "error" in m15_data or "error" in h4_raw or "error" in h1_raw or "error" in m15_raw:
                return jsonify({
                    "error": "Ошибка получения данных",
                    "details": {
                        "H4": h4_data.get("error"),
                        "H1": h1_data.get("error"),
                        "M15": m15_data.get("error")
                    }
                }), 500

            h4_analysis = calculator.get_market_analysis(h4_raw.get("candles", []))
            h1_analysis = calculator.get_market_analysis(h1_raw.get("candles", []))
            m15_analysis = calculator.get_market_analysis(m15_raw.get("candles", []))
            
            # Формируем ответ
            return jsonify({
                "success": True,
                "symbol": SYMBOL,
                "timeframes": {
                    "H4": {
                        "candles": h4_data.get("candles", []),
                        "count": len(h4_data.get("candles", [])),
                        "analysis": h4_analysis
                    },
                    "H1": {
                        "candles": h1_data.get("candles", []),
                        "count": len(h1_data.get("candles", [])),
                        "analysis": h1_analysis
                    },
                    "M15": {
                        "candles": m15_data.get("candles", []),
                        "count": len(m15_data.get("candles", [])),
                        "analysis": m15_analysis
                    }
                }
            })
        
        # Если таймфрейм указан - работаем как раньше
        period = request.args.get('period')
        limit = request.args.get('limit', type=int)
        
        logger.info(f"Candles request: tf={timeframe}, period={period}, limit={limit}")
        
        result = yfinance_service.get_candles(timeframe, period, limit)
        
        if "error" in result:
            return jsonify(result), 500

        if "candles" in result:
            result["analysis"] = calculator.get_market_analysis(result["candles"])
            
        return jsonify(result)
        
    except Exception as e:
        logger.error(f"Error in /candles: {str(e)}")
        return jsonify({"error": str(e)}), 500


@market_bp.route('/ticker-info')
def get_ticker_info():
    """
    Получение информации о тикере
    """
    try:
        info = yfinance_service.get_ticker_info()
        return jsonify(info)
        
    except Exception as e:
        logger.error(f"Error in /ticker-info: {str(e)}")
        return jsonify({"error": str(e)}), 500


@market_bp.route('/current-price')
def get_current_price():
    """
    Получение текущей цены
    """
    try:
        price = yfinance_service.get_current_price()
        
        if price is None:
            return jsonify({"error": "Не удалось получить цену"}), 500
            
        return jsonify({
            "symbol": SYMBOL,
            "price": price,
            "timestamp": int(__import__('time').time())
        })
        
    except Exception as e:
        logger.error(f"Error in /current-price: {str(e)}")
        return jsonify({"error": str(e)}), 500


@market_bp.route('/config')
def get_config():
    """
    Получение конфигурации приложения
    """
    try:
        return jsonify({
            "symbol": SYMBOL,
            "start_balance": START_BALANCE,
            "daily_loss_limit": DAILY_LOSS_LIMIT,
            "max_lot_size": MAX_LOT_SIZE,
            "risk_percent": RISK_PERCENT
        })
        
    except Exception as e:
        logger.error(f"Error in /config: {str(e)}")
        return jsonify({"error": str(e)}), 500


@market_bp.route('/health')
def health_check():
    """
    Проверка работоспособности сервиса
    """
    try:
        is_valid = yfinance_service.validate_symbol()
        
        return jsonify({
            "status": "healthy" if is_valid else "degraded",
            "symbol": SYMBOL,
            "symbol_available": is_valid
        })
        
    except Exception as e:
        logger.error(f"Error in /health: {str(e)}")
        return jsonify({
            "status": "unhealthy",
            "error": str(e)
        }), 500
