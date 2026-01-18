"""
Роуты для рыночных данных с интегрированным математическим анализом
"""
from flask import Blueprint, jsonify, request
import logging

from services.yfinance_service import yfinance_service
from services.calculator import calculator  # Импортируем наш калькулятор
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
    Получение свечных данных + Математический анализ структур
    """
    try:
        timeframe = request.args.get('tf')
        
        # ВАРИАНТ 1: Если таймфрейм не указан - возвращаем ВСЕ таймфреймы с анализом каждого
        if not timeframe:
            logger.info("Candles request: all timeframes with math analysis")
            
            # Получаем сырые данные
            h4_raw = yfinance_service.get_candles('H4', limit=40)
            h1_raw = yfinance_service.get_candles('H1', limit=40)
            m15_raw = yfinance_service.get_candles('M15', limit=60)
            
            if "error" in h4_raw or "error" in h1_raw or "error" in m15_raw:
                return jsonify({"error": "Ошибка получения данных от Yahoo Finance"}), 500
            
            # Прогоняем каждый ТФ через наш калькулятор структур
            h4_analysis = calculator.get_market_analysis(h4_raw.get("candles", []))
            h1_analysis = calculator.get_market_analysis(h1_raw.get("candles", []))
            m15_analysis = calculator.get_market_analysis(m15_raw.get("candles", []))

            return jsonify({
                "success": True,
                "symbol": SYMBOL,
                "timeframes": {
                    "H4": {
                        "candles": h4_raw.get("candles", []),
                        "analysis": h4_analysis # Результаты BOS, FVG, OB для H4
                    },
                    "H1": {
                        "candles": h1_raw.get("candles", []),
                        "analysis": h1_analysis # Результаты BOS, FVG, OB для H1
                    },
                    "M15": {
                        "candles": m15_raw.get("candles", []),
                        "analysis": m15_analysis # Результаты BOS, FVG, OB для M15
                    }
                }
            })
        
        # ВАРИАНТ 2: Если указан конкретный таймфрейм
        period = request.args.get('period')
        limit = request.args.get('limit', type=int) or 100
        
        logger.info(f"Candles request: tf={timeframe}, limit={limit}")
        
        result = yfinance_service.get_candles(timeframe, period, limit)
        
        if "error" in result:
            return jsonify(result), 500
            
        # Добавляем математический анализ структур для выбранного ТФ
        if "candles" in result:
            result["analysis"] = calculator.get_market_analysis(result["candles"])

        return jsonify(result)
        
    except Exception as e:
        logger.error(f"Error in /candles: {str(e)}")
        return jsonify({"error": str(e)}), 500


@market_bp.route('/ticker-info')
def get_ticker_info():
    try:
        info = yfinance_service.get_ticker_info()
        return jsonify(info)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@market_bp.route('/current-price')
def get_current_price():
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
        return jsonify({"error": str(e)}), 500


@market_bp.route('/config')
def get_config():
    try:
        return jsonify({
            "symbol": SYMBOL,
            "start_balance": START_BALANCE,
            "daily_loss_limit": DAILY_LOSS_LIMIT,
            "max_lot_size": MAX_LOT_SIZE,
            "risk_percent": RISK_PERCENT
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@market_bp.route('/health')
def health_check():
    try:
        is_valid = yfinance_service.validate_symbol()
        return jsonify({
            "status": "healthy" if is_valid else "degraded",
            "symbol": SYMBOL,
            "symbol_available": is_valid
        })
    except Exception as e:
        return jsonify({"status": "unhealthy", "error": str(e)}), 500