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
from routes.llm_routes import llm_bp

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

# Настройка CORS для разрешения запросов с любых источников
CORS(app, resources={
    r"/api/*": {
        "origins": "*",  # Разрешить все origins
        "methods": ["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        "allow_headers": ["Content-Type", "Authorization"],
        "expose_headers": ["Content-Type"],
        "supports_credentials": False,
        "max_age": 3600
    }
})

# Дополнительный middleware для явной установки CORS заголовков
@app.after_request
def after_request(response):
    """Добавляем CORS заголовки ко всем ответам"""
    response.headers.add('Access-Control-Allow-Origin', '*')
    response.headers.add('Access-Control-Allow-Headers', 'Content-Type,Authorization')
    response.headers.add('Access-Control-Allow-Methods', 'GET,PUT,POST,DELETE,OPTIONS')
    return response

# Регистрация blueprints
app.register_blueprint(market_bp, url_prefix='/api/market')
app.register_blueprint(analysis_bp, url_prefix='/api/analysis')
app.register_blueprint(news_bp, url_prefix='/api/news')
app.register_blueprint(chart_bp, url_prefix='/api/chart')
app.register_blueprint(llm_bp, url_prefix='/api/llm')


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
                "gold_relevant": "/api/news/gold-relevant",
                "feed": "/api/news/feed"
            },
            "chart": {
                "generate": "/api/chart/generate?tf=M15 (M15, H1, H4)"
            },
            "llm": {
                "analyze": "/api/llm/analyze (GET/POST) - Full market analysis with LLM",
                "session": "/api/llm/session - Current trading session info",
                "status": "/api/llm/status - LLM service status"
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


def check_network_connectivity():
    """Проверка сетевого подключения и получение внешнего IP"""
    try:
        import requests
        response = requests.get('https://api.ipify.org', timeout=5)
        if response.status_code == 200:
            return response.text.strip()
    except Exception as e:
        logger.debug(f"Network connectivity check failed: {e}")
    return None


def initialize_services():
    """Инициализация и проверка сервисов с обработкой ошибок"""
    from services.yfinance_service import yfinance_service
    from services.llm_service import llm_service
    
    services_status = {
        "llm": {},
        "yfinance": False,
        "network": None
    }
    
    # Проверка сетевого подключения
    logger.info("Checking network connectivity...")
    external_ip = check_network_connectivity()
    if external_ip:
        logger.info(f"✓ Network: External IP = {external_ip}")
        services_status["network"] = external_ip
    else:
        logger.warning("⚠ Network: Could not determine external IP (VPN may be required)")
    
    # Проверка LLM сервисов
    logger.info("Checking LLM services configuration...")
    
    if llm_service.gemini_key:
        logger.info("✓ Gemini API key configured")
        services_status["llm"]["gemini"] = True
    else:
        logger.warning("⚠ GEMINI_API_KEY not set! Gemini3 model will not work.")
        logger.info("  → Add GEMINI_API_KEY to .env file to enable Gemini")
        services_status["llm"]["gemini"] = False
    
    if llm_service.openrouter_key:
        logger.info("✓ OpenRouter API key configured")
        services_status["llm"]["openrouter"] = True
    else:
        logger.warning("⚠ OPENROUTER_API_KEY not set! OpenRouter model may have limitations.")
        logger.info("  → Add OPENROUTER_API_KEY to .env file to enable OpenRouter")
        services_status["llm"]["openrouter"] = False
    
    if llm_service.gateway_url:
        logger.info(f"✓ AI Gateway configured at {llm_service.gateway_url}")
        services_status["llm"]["gateway"] = True
        if llm_service.gateway_key:
            logger.info("✓ AI Gateway API key configured")
        else:
            logger.warning("⚠ AI_GATEWAY_KEY not set (may be optional)")
    else:
        logger.warning("⚠ AI_GATEWAY_URL not set! Gateway model will not work.")
        logger.info("  → Add AI_GATEWAY_URL to .env file to enable AI Gateway")
        services_status["llm"]["gateway"] = False
    
    # Проверка Yahoo Finance с детальной обработкой ошибок
    logger.info(f"Validating Yahoo Finance symbol: {SYMBOL}...")
    try:
        yf_valid = yfinance_service.validate_symbol()
        if yf_valid:
            logger.info(f"✓ Yahoo Finance: {SYMBOL} is available")
            services_status["yfinance"] = True
        else:
            logger.warning(f"⚠ Yahoo Finance: {SYMBOL} validation failed")
            logger.info("  → This may be due to VPN/network issues. Try using source=twelvedata")
            services_status["yfinance"] = False
    except Exception as e:
        logger.error(f"✗ Yahoo Finance validation error: {type(e).__name__}: {str(e)}")
        logger.debug("Full error traceback:", exc_info=True)
        services_status["yfinance"] = False
    
    return services_status


if __name__ == '__main__':
    import socket
    
    logger.info("=" * 60)
    logger.info("ASTRA ANALYZER PRO - SERVER STARTING")
    logger.info("=" * 60)
    logger.info(f"Symbol: {SYMBOL}")
    logger.info(f"Port: {FLASK_PORT}")
    logger.info(f"Debug: {FLASK_DEBUG}")
    logger.info(f"Host: 0.0.0.0 (listening on all interfaces)")
    logger.info(f"Data Source: Yahoo Finance API / TwelveData")
    logger.info("=" * 60)
    
    # Инициализация сервисов
    try:
        services_status = initialize_services()
    except Exception as e:
        logger.error(f"Critical error during service initialization: {e}", exc_info=True)
        logger.warning("Server will start anyway, but some features may not work")
        services_status = {}
    
    logger.info("=" * 60)
    
    # Определяем доступные адреса
    try:
        hostname = socket.gethostname()
        local_ip = socket.gethostbyname(hostname)
        
        logger.info("Server ready! Access API at:")
        logger.info(f"  - http://127.0.0.1:{FLASK_PORT}/ (localhost)")
        logger.info(f"  - http://{local_ip}:{FLASK_PORT}/ (local network)")
        
        # Пытаемся найти VPN интерфейс (172.18.x.x)
        try:
            import netifaces  # type: ignore
            for interface in netifaces.interfaces():
                addrs = netifaces.ifaddresses(interface)
                if netifaces.AF_INET in addrs:
                    for addr_info in addrs[netifaces.AF_INET]:
                        ip = addr_info.get('addr', '')
                        if ip.startswith('172.18.'):
                            logger.info(f"  - http://{ip}:{FLASK_PORT}/ (VPN)")
        except ImportError:
            # netifaces не установлен, пробуем другой способ
            try:
                import subprocess
                result = subprocess.run(['ipconfig'], capture_output=True, text=True, timeout=2)
                if '172.18.' in result.stdout:
                    # Извлекаем IP из вывода
                    for line in result.stdout.split('\n'):
                        if '172.18.' in line:
                            # Простой поиск IP адреса
                            import re
                            ip_match = re.search(r'172\.18\.\d+\.\d+', line)
                            if ip_match:
                                logger.info(f"  - http://{ip_match.group()}:{FLASK_PORT}/ (VPN)")
            except Exception:
                pass
    except Exception as e:
        logger.debug(f"Could not determine all network addresses: {e}")
        logger.info(f"Server ready! Access API at http://0.0.0.0:{FLASK_PORT}/")
    
    logger.info("=" * 60)
    logger.info("Health check endpoint: /api/market/health")
    logger.info("=" * 60)
    
    # Запуск сервера
    try:
        app.run(
            host='0.0.0.0',  # Слушаем на всех интерфейсах
            port=FLASK_PORT,
            debug=FLASK_DEBUG,
            threaded=True  # Включаем многопоточность для обработки параллельных запросов
        )
    except OSError as e:
        if "Address already in use" in str(e):
            logger.error(f"Port {FLASK_PORT} is already in use!")
            logger.error("Please stop the other service or change FLASK_PORT in .env")
        else:
            logger.error(f"Failed to start server: {e}")
        raise
