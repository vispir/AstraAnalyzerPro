"""
Роуты для генерации графиков
"""
from flask import Blueprint, jsonify, request
import logging
import pandas as pd

from services.chart_service import chart_service
from services.yfinance_service import yfinance_service
from services.smc_detector import smc_detector

logger = logging.getLogger(__name__)

chart_bp = Blueprint('chart', __name__)


@chart_bp.route('/generate', methods=['GET'])
def generate_chart():
    """
    Генерация графика с автоматическим определением SMC уровней
    
    Query params:
        tf: timeframe (M15, H1, H4) - обязательно
        limit: количество свечей (default: 100)
        width: ширина изображения (default: 1200)
        height: высота изображения (default: 700)
    
    Returns:
        {
            "success": true,
            "image": "base64_string...",
            "format": "png",
            "smc_levels": {...}
        }
    """
    try:
        # Параметры
        timeframe = request.args.get('tf')
        
        if not timeframe:
            return jsonify({
                "error": "Missing required parameter 'tf'",
                "hint": "Usage: /api/chart/generate?tf=H1"
            }), 400
        
        limit = int(request.args.get('limit', 100))
        width = int(request.args.get('width', 1200))
        height = int(request.args.get('height', 700))
        
        logger.info(f"Chart generation request: tf={timeframe}, limit={limit}")
        
        # Получаем данные свечей
        candles_response = yfinance_service.get_candles(timeframe, limit=limit)
        
        if 'error' in candles_response:
            return jsonify({"error": candles_response['error']}), 500
        
        candles = candles_response.get('candles', [])
        
        if not candles:
            return jsonify({"error": "No candles data available"}), 404
        
        # Конвертируем в DataFrame
        df = pd.DataFrame(candles)
        df['Date'] = pd.to_datetime(df['time'], unit='s')
        df.set_index('Date', inplace=True)
        df.rename(columns={
            'open': 'Open',
            'high': 'High',
            'low': 'Low',
            'close': 'Close',
            'volume': 'Volume'
        }, inplace=True)
        
        # Автоматический SMC анализ
        logger.info("Running SMC analysis...")
        smc_data = smc_detector.analyze(df)
        
        # Генерируем график
        logger.info(f"Generating chart image: {width}x{height}")
        base64_image = chart_service.generate_chart_image(
            df=df,
            smc_data=smc_data,
            title=f"XAUUSD {timeframe} - SMC Analysis",
            width=width,
            height=height
        )
        
        logger.info(f"Chart generated successfully")
        
        return jsonify({
            "success": True,
            "image": base64_image,
            "format": "png",
            "size": {
                "width": width,
                "height": height
            },
            "timeframe": timeframe,
            "candles_count": len(candles),
            "market_structure": {
                "trend": smc_data.get('trend', 'NEUTRAL'),
                "choch": len(smc_data.get('choch', [])),
                "bos": len(smc_data.get('bos', []))
            },
            "smc_levels": {
                "order_blocks": len(smc_data.get('order_blocks', [])),
                "fvg": len(smc_data.get('fvg', [])),
                "liquidity": len(smc_data.get('liquidity', [])),
                "eqh": len(smc_data.get('eqh', [])),
                "eql": len(smc_data.get('eql', []))
            }
        })
        
    except ValueError as ve:
        logger.error(f"Validation error: {str(ve)}")
        return jsonify({
            "error": "Invalid parameter",
            "details": str(ve)
        }), 400
    except Exception as e:
        logger.error(f"Error in /generate: {str(e)}", exc_info=True)
        return jsonify({
            "error": "Internal server error",
            "details": str(e)
        }), 500
