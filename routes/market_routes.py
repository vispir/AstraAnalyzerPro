"""
Роуты для рыночных данных с выбором источника (OANDA / Twelve Data / Yahoo Finance)
"""
from flask import Blueprint, jsonify, request
import logging
import requests

from services.yfinance_service import yfinance_service
from services.twelvedata_service import twelvedata_service
from services.oanda_service import oanda_service  # Добавили наш новый сервис
from services.calculator import calculator
from config.settings import SYMBOL

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
        source: источник данных (oanda/twelvedata/yfinance) - опционально
    """
    try:
        timeframe = request.args.get('tf')
        # Источник данных: по умолчанию twelvedata
        source = request.args.get('source', 'twelvedata').lower()
        limit = request.args.get('limit', type=int)
        period = request.args.get('period')

        # Выбираем сервис (Добавлена поддержка OANDA)
        if source == 'oanda':
            market_service = oanda_service
        elif source == 'twelvedata':
            market_service = twelvedata_service
        else:
            market_service = yfinance_service
        
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
        source = request.args.get('source', 'twelvedata').lower()
        
        # Выбираем сервис
        if source == 'oanda':
            market_service = oanda_service
        elif source == 'twelvedata':
            market_service = twelvedata_service
        else:
            market_service = yfinance_service
        
        # Пытаемся взять быструю цену
        price = None
        if source in ['twelvedata', 'oanda']:
            # Оба сервиса используют одинаковую логику получения последней цены из свечи
            res = market_service.get_candles(limit=1)
            if "candles" in res and res["candles"]:
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
            "symbol": SYMBOL
        })
    except Exception as e:
        logger.error(f"Error in /config: {str(e)}")
        return jsonify({"error": str(e)}), 500


@market_bp.route('/health')
def health_check():
    """
    Network Health Check - проверка работоспособности сервисов и сетевого подключения
    """
    import time
    from services.llm_service import get_current_ip, llm_service
    
    health_status = {
        "status": "healthy",
        "timestamp": int(time.time()),
        "symbol": SYMBOL,
        "services": {},
        "network": {}
    }
    
    # Проверка сетевого подключения
    try:
        current_ip = get_current_ip()
        health_status["network"]["external_ip"] = current_ip if current_ip else "unavailable"
        health_status["network"]["ip_check"] = current_ip is not None
    except Exception as e:
        logger.error(f"IP check failed: {e}")
        health_status["network"]["ip_check"] = False
        health_status["network"]["error"] = str(e)
    
    # Проверка Yahoo Finance
    try:
        yf_start = time.time()
        yf_ok = yfinance_service.validate_symbol()
        yf_time = time.time() - yf_start
        health_status["services"]["yfinance"] = {
            "active": yf_ok,
            "response_time_ms": round(yf_time * 1000, 2)
        }
    except Exception as e:
        logger.error(f"Yahoo Finance health check failed: {e}")
        health_status["services"]["yfinance"] = {
            "active": False,
            "error": str(e)
        }
    
    # Проверка TwelveData
    try:
        td_start = time.time()
        td_result = twelvedata_service.get_candles(limit=1)
        td_time = time.time() - td_start
        td_ok = td_result.get("success", False)
        health_status["services"]["twelvedata"] = {
            "active": td_ok,
            "response_time_ms": round(td_time * 1000, 2)
        }
    except Exception as e:
        logger.error(f"TwelveData health check failed: {e}")
        health_status["services"]["twelvedata"] = {
            "active": False,
            "error": str(e)
        }

    # Проверка OANDA
    try:
        oa_start = time.time()
        oa_result = oanda_service.get_candles(limit=1)
        oa_time = time.time() - oa_start
        oa_ok = oa_result.get("success", False)
        health_status["services"]["oanda"] = {
            "active": oa_ok,
            "response_time_ms": round(oa_time * 1000, 2)
        }
    except Exception as e:
        logger.error(f"OANDA health check failed: {e}")
        health_status["services"]["oanda"] = {
            "active": False,
            "error": str(e)
        }
    
    # Проверка Gemini API (если настроен)
    try:
        if llm_service.gemini_key:
            gemini_start = time.time()
            test_url = f"https://generativelanguage.googleapis.com/v1beta/models/{llm_service.GEMINI_MODEL}:generateContent"
            test_response = requests.get(test_url, timeout=5)
            gemini_time = time.time() - gemini_start
            health_status["services"]["gemini"] = {
                "configured": True,
                "reachable": test_response.status_code < 500,
                "response_time_ms": round(gemini_time * 1000, 2)
            }
        else:
            health_status["services"]["gemini"] = {
                "configured": False,
                "note": "GEMINI_API_KEY not set"
            }
    except Exception as e:
        logger.debug(f"Gemini health check failed: {e}")
        if llm_service.gemini_key:
            health_status["services"]["gemini"] = {
                "configured": True,
                "reachable": False,
                "error": str(e)
            }
    
    # Определяем общий статус (теперь учитываем и OANDA)
    all_critical_ok = (
        health_status["services"].get("twelvedata", {}).get("active", False) or
        health_status["services"].get("yfinance", {}).get("active", False) or
        health_status["services"].get("oanda", {}).get("active", False)
    )
    
    if not all_critical_ok:
        health_status["status"] = "unhealthy"
    elif not health_status["network"].get("ip_check", False):
        health_status["status"] = "degraded"
    
    status_code = 200 if health_status["status"] == "healthy" else (503 if health_status["status"] == "unhealthy" else 200)
    
    return jsonify(health_status), status_code