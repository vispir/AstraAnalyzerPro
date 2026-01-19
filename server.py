"""
Astra Analyzer Pro - Backend Server
Модульная архитектура с Yahoo Finance API
"""
from flask import Flask, jsonify
from flask_cors import CORS
import logging

# Импорт конфигурации
from config.settings import FLASK_PORT, FLASK_DEBUG, SYMBOL

# Импорт роутов
from routes.market_routes import market_bp
from routes.analysis_routes import analysis_bp
from routes.news_routes import news_bp
from routes.chart_routes import chart_bp

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler('astra_server.log')
    ]
)
logger = logging.getLogger(__name__)

# Создание приложения Flask
app = Flask(__name__)
CORS(app)

# Регистрация blueprints
app.register_blueprint(market_bp, url_prefix='/api/market')
app.register_blueprint(analysis_bp, url_prefix='/api/analysis')
app.register_blueprint(news_bp, url_prefix='/api/news')
app.register_blueprint(chart_bp, url_prefix='/api/chart')


# Корневой роут (для обратной совместимости)
@app.route('/')
def index():
    """Главная страница API"""
    return jsonify({
        "name": "Astra Analyzer Pro API",
        "version": "2.0.0",
        "status": "running",
        "symbol": SYMBOL,
        "endpoints": {
            "market": {
                "candles_all": "/api/market/candles (H4:10, H1:20, M15:50)",
                "candles_single": "/api/market/candles?tf=M15&limit=100",
                "ticker_info": "/api/market/ticker-info",
                "current_price": "/api/market/current-price",
                "config": "/api/market/config",
                "health": "/api/market/health"
            },
            "analysis": {
                "calculate": "/api/analysis/calculate (POST)",
                "analyze": "/api/analysis/analyze (POST)",
                "breakeven": "/api/analysis/breakeven (POST)",
                "drawdown": "/api/analysis/drawdown (POST)",
                "ai_status": "/api/analysis/ai-status"
            },
            "news": {
                "all": "/api/news/all?currency=USD&impact=High",
                "upcoming": "/api/news/upcoming?hours=24",
                "today": "/api/news/today",
                "high_impact": "/api/news/high-impact",
                "gold_relevant": "/api/news/gold-relevant"
            }
        }
    })


# Старые роуты для обратной совместимости (перенаправление на новые)
@app.route('/config')
def config_legacy():
    """Legacy endpoint - перенаправление на новый API"""
    from routes.market_routes import get_config
    return get_config()


@app.route('/calculate', methods=['POST'])
def calculate_legacy():
    """Legacy endpoint - перенаправление на новый API"""
    from routes.analysis_routes import calculate_trade
    return calculate_trade()


@app.route('/analyze', methods=['POST'])
def analyze_legacy():
    """Legacy endpoint - перенаправление на новый API"""
    from routes.analysis_routes import analyze_trade
    return analyze_trade()


# Обработчики ошибок
@app.errorhandler(404)
def not_found(e):
    """Обработка 404 ошибки"""
    return jsonify({
        "error": "Endpoint not found",
        "message": "Проверьте документацию API на главной странице"
    }), 404


@app.errorhandler(500)
def internal_error(e):
    """Обработка 500 ошибки"""
    logger.error(f"Internal server error: {str(e)}")
    return jsonify({
        "error": "Internal server error",
        "message": "Проверьте логи сервера"
    }), 500


@app.errorhandler(Exception)
def handle_exception(e):
    """Общий обработчик исключений"""
    logger.error(f"Unhandled exception: {str(e)}", exc_info=True)
    return jsonify({
        "error": "Unexpected error",
        "message": str(e)
    }), 500


if __name__ == '__main__':
    logger.info("=" * 60)
    logger.info("ASTRA ANALYZER PRO - SERVER STARTING")
    logger.info("=" * 60)
    logger.info(f"Symbol: {SYMBOL}")
    logger.info(f"Port: {FLASK_PORT}")
    logger.info(f"Debug: {FLASK_DEBUG}")
    logger.info(f"Data Source: Yahoo Finance API")
    logger.info("=" * 60)
    
    # Проверка доступности сервисов
    from services.yfinance_service import yfinance_service
    from services.gemini_service import gemini_service
    
    if not gemini_service.is_available():
        logger.warning("WARNING: GEMINI_API_KEY not set! AI analysis will not work.")
    else:
        logger.info("OK: Gemini AI service available")
    
    if yfinance_service.validate_symbol():
        logger.info(f"OK: Yahoo Finance: {SYMBOL} is available")
    else:
        logger.warning(f"WARNING: Yahoo Finance: {SYMBOL} validation failed")
    
    logger.info("=" * 60)
    logger.info("Server ready! Access API at http://127.0.0.1:{}/".format(FLASK_PORT))
    logger.info("=" * 60)
    
    # Запуск сервера
    app.run(
        host='127.0.0.1',
        port=FLASK_PORT,
        debug=FLASK_DEBUG
    )
