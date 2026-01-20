"""
Роуты для рыночных данных с выбором источника (Twelve Data / Yahoo Finance)
"""
from flask import Blueprint, jsonify, request
import logging

from services.yfinance_service import yfinance_service
from services.twelvedata_service import twelvedata_service
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
        source: источник данных (twelvedata/yfinance) - опционально
    """
    try:
        timeframe = request.args.get('tf')
        # Источник данных: по умолчанию twelvedata
        source = request.args.get('source', 'twelvedata')
        limit = request.args.get('limit', type=int)
        period = request.args.get('period')

        # Выбираем сервис
        market_service = twelvedata_service if source == 'twelvedata' else yfinance_service
        
        logger.info(f"Candles request: source={source}, tf={timeframe or 'ALL'}")

        # ВАРИАНТ 1: Если таймфрейм не указан - возвращаем данные по всем ТФ (H4, H1, M15)
        if not timeframe:
            # Запрашиваем данные с запасом для анализа
            h4_raw = market_service.get_candles('H4', limit=max(limit or 40, 40))
            h1_raw = market_service.get_candles('H1', limit=max(limit or 40, 40))
            m15_raw = market_service.get_candles('M15', limit=max(limit or 60, 60))
            
            if "error" in h4_raw or "error" in h1_raw or "error" in m15_raw:
                return jsonify({
                    "error": "Ошибка получения данных",
                    "source": source,
                    "details": {
                        "H4": h4_raw.get("error"), 
                        "H1": h1_raw.get("error"), 
                        "M15": m15_raw.get("error")
                    }
                }), 500

            # Прогоняем через калькулятор структур
            h4_analysis = calculator.get_market_analysis(h4_raw.get("candles", []))
            h1_analysis = calculator.get_market_analysis(h1_raw.get("candles", []))
            m15_analysis = calculator.get_market_analysis(m15_raw.get("candles", []))
            
            return jsonify({
                "success": True,
                "symbol": SYMBOL,
                "source": source,
                "timeframes": {
                    "H4": {
                        "candles": h4_raw.get("candles", [])[-10:],  # Отдаем 10 для легкого веса
                        "analysis": h4_analysis
                    },
                    "H1": {
                        "candles": h1_raw.get("candles", [])[-20:],  # Отдаем 20
                        "analysis": h1_analysis
                    },
                    "M15": {
                        "candles": m15_raw.get("candles", [])[-50:],  # Отдаем 50
                        "analysis": m15_analysis
                    }
                }
            })
        
        # ВАРИАНТ 2: Запрос конкретного таймфрейма
        calc_limit = max(limit or 100, 100)  # Минимум 100 для точности анализа
        result = market_service.get_candles(timeframe, period, calc_limit)
        
        if "error" in result:
            return jsonify(result), 500

        if "candles" in result:
            # Добавляем анализ структур
            result["analysis"] = calculator.get_market_analysis(result["candles"])
            # Обрезаем свечи до того количества, которое просил юзер в limit
            if limit:
                result["candles"] = result["candles"][-limit:]
            result["source"] = source
            
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
        # Инфо о тикере пока оставим через Yahoo, там больше описательных данных
        info = yfinance_service.get_ticker_info()
        return jsonify(info)
        
    except Exception as e:
        logger.error(f"Error in /ticker-info: {str(e)}")
        return jsonify({"error": str(e)}), 500


@market_bp.route('/current-price')
def get_current_price():
    """
    Получение текущей цены с выбором источника
    """
    try:
        source = request.args.get('source', 'twelvedata')
        market_service = twelvedata_service if source == 'twelvedata' else yfinance_service
        
        # Пытаемся взять быструю цену
        price = None
        if source == 'twelvedata':
            # У TwelveData есть быстрый эндпоинт для цены, но для простоты возьмем из свечей
            res = twelvedata_service.get_candles(limit=1)
            if "candles" in res:
                price = res["candles"][-1]["close"]
        else:
            price = yfinance_service.get_current_price()
        
        if price is None:
            return jsonify({"error": "Не удалось получить цену"}), 500
            
        return jsonify({
            "symbol": SYMBOL,
            "price": price,
            "source": source,
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
        # Проверяем оба сервиса
        yf_ok = yfinance_service.validate_symbol()
        td_ok = twelvedata_service.get_candles(limit=1).get("success", False)
        
        return jsonify({
            "status": "healthy" if td_ok else "degraded",
            "twelvedata_active": td_ok,
            "yfinance_active": yf_ok,
            "symbol": SYMBOL
        })
        
    except Exception as e:
        logger.error(f"Error in /health: {str(e)}")
        return jsonify({
            "status": "unhealthy",
            "error": str(e)
        }), 500
