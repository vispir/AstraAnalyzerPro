"""
Роуты для генерации графиков
"""
from flask import Blueprint, jsonify, request
import logging
import pandas as pd

from services.chart_service import chart_service
from services.twelvedata_service import twelvedata_service
from services.smc_detector import smc_detector
from services.oanda_service import oanda_service

logger = logging.getLogger(__name__)

chart_bp = Blueprint('chart', __name__)


@chart_bp.route('/generate', methods=['GET'])
def generate_chart():
    """
    Генерация графика с автоматическим определением SMC уровней
    
    Query params:
        tf: timeframe (M15, H1, H4) - обязательно
        source: источник данных (oanda, twelvedata) - default: twelvedata
        limit: количество свечей (default: 300)
        width: ширина изображения (default: 1200)
        height: высота изображения (default: 700)
    
    Returns:
        {
            "success": true,
            "image": "base64_string...",
            "format": "png"
        }
    """
    try:
        # 1. Получаем параметры
        timeframe = request.args.get('tf')
        # Добавляем выбор источника
        provider = request.args.get('source', 'twelvedata').lower()
        
        if not timeframe:
            return jsonify({
                "error": "Missing required parameter 'tf'",
                "hint": "Usage: /api/chart/generate?tf=H1"
            }), 400
        
        limit = int(request.args.get('limit', 300))
        width = int(request.args.get('width', 1200))
        height = int(request.args.get('height', 700))
        
        logger.info(f"Chart generation request: tf={timeframe}, provider={provider}, limit={limit}")
        
        # 2. Выбор провайдера данных
        if provider == 'oanda':
            candles_response = oanda_service.get_candles(timeframe, limit=limit)
        else:
            candles_response = twelvedata_service.get_candles(timeframe, limit=limit)
        
        # Проверка ошибок от сервисов
        if 'error' in candles_response:
            return jsonify({"error": candles_response['error']}), 500
        
        candles = candles_response.get('candles', [])
        
        if not candles:
            return jsonify({"error": "No candles data available"}), 404
        
        # 3. Подготовка данных (DataFrame)
        df = pd.DataFrame(candles)
        df['Date'] = pd.to_datetime(df['time'], unit='s')
        df.set_index('Date', inplace=True)
        
        # 4. Автоматический SMC анализ
        logger.info(f"Running SMC analysis on {provider} data...")
        smc_data = smc_detector.analyze(df)
        
        # 5. Переименовываем колонки для визуализации
        df.rename(columns={
            'open': 'Open',
            'high': 'High',
            'low': 'Low',
            'close': 'Close',
            'volume': 'Volume'
        }, inplace=True)
        
        # 6. Генерируем изображение
        logger.info(f"Generating chart image: {width}x{height}")
        base64_image = chart_service.generate_chart_image(
            df=df,
            smc_data=smc_data,
            title=f"XAUUSD {timeframe} ({provider.upper()}) - SMC Analysis",
            width=width,
            height=height
        )
        
        logger.info(f"Chart generated successfully")
        
        # Возвращаем полный ответ, как и было в оригинале
        return jsonify({
            "success": True,
            "image": base64_image,
            "format": "png"
        })
        
    except ValueError as ve:
        logger.error(f"Validation error: {str(ve)}")
        return jsonify({
            "success": False,
            "error": "Invalid parameter",
            "details": str(ve)
        }), 400
    except Exception as e:
        logger.error(f"Error in /generate: {str(e)}", exc_info=True)
        return jsonify({
            "success": False,
            "error": "Internal server error",
            "details": str(e)
        }), 500
