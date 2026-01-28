"""
Astra Analyzer Pro - Backend Server
Модульная архитектура с Yahoo Finance API
"""
from flask import Flask, jsonify, request
from flask_cors import CORS
import logging
import threading
import sys
import io
import os

# Принудительно ставим кодировку UTF-8
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

base_dir = os.path.dirname(os.path.abspath(__file__))
log_file = os.path.join(base_dir, 'astra_server.log')

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(log_file, encoding='utf-8')
    ]
)
logger = logging.getLogger(__name__)

# Импорт конфигурации
from config.settings import FLASK_PORT, FLASK_DEBUG, SYMBOL

# Импорт Telegram сервиса
from services.telegram_service import telegram_service

# --- ИСПРАВЛЕННЫЙ БЛОК ИМПОРТА WATCHER (Файл в корне) ---
try:
    # Так как файл в корне, импортируем напрямую
    from watcher import start_watcher, run_analysis_cycle
    logger.info("✅ Watcher successfully imported from root directory")
except ImportError as e:
    start_watcher = None
    run_analysis_cycle = None
    logger.error(f"❌ CRITICAL: watcher.py not found in root! Error: {e}")

# Импорт роутов
from routes.market_routes import market_bp
from routes.analysis_routes import analysis_bp
from routes.news_routes import news_bp
from routes.chart_routes import chart_bp
from routes.llm_routes import llm_bp
from routes.auth_routes import auth_bp

# Создание приложения Flask
app = Flask(__name__)

# Настройка CORS
CORS(app, resources={
    r"/api/*": {
        "origins": "*",
        "methods": ["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        "allow_headers": ["Content-Type", "Authorization"],
        "expose_headers": ["Content-Type"],
        "supports_credentials": False,
        "max_age": 3600
    }
})

@app.after_request
def after_request(response):
    response.headers.add('Access-Control-Allow-Origin', '*')
    response.headers.add('Access-Control-Allow-Headers', 'Content-Type,Authorization')
    response.headers.add('Access-Control-Allow-Methods', 'GET,PUT,POST,DELETE,OPTIONS')
    return response

# --- ЭНДПОИНТ ДЛЯ VERCEL CRON ---
@app.route('/api/cron/watcher', methods=['GET'])
def trigger_watcher():
    auth_header = request.headers.get('Authorization')
    cron_secret = os.getenv('CRON_SECRET')
    
    if cron_secret and auth_header != f"Bearer {cron_secret}":
        return jsonify({"success": False, "error": "Unauthorized"}), 401
    
    if run_analysis_cycle:
        logger.info("⏰ Cron Trigger: Starting analysis cycle...")
        # Вызываем функцию анализа напрямую
        run_analysis_cycle() 
        return jsonify({"success": True, "message": "Analysis complete"}), 200
    
    return jsonify({"success": False, "error": "Watcher service not available"}), 500

# --- TELEGRAM WEBHOOK ENDPOINT ---
@app.route('/api/tg/webhook', methods=['POST', 'GET'])
def telegram_webhook():
    """
    Webhook endpoint для Telegram бота
    POST - обработка updates от Telegram
    GET - проверка что endpoint доступен
    """
    if request.method == 'GET':
        return jsonify({
            "status": "ok",
            "message": "Telegram webhook endpoint is ready"
        }), 200
    
    update = request.json
    if not update:
        return "OK", 200
    
    try:
        telegram_service.process_webhook_update(update)
    except Exception as e:
        logger.error(f"❌ Ошибка обработки Telegram webhook: {e}")
    
    return "OK", 200

@app.route('/api/tg/webhook/info', methods=['GET'])
def telegram_webhook_info():
    """Получение информации о текущем webhook"""
    # Эта функция временно недоступна для pyTelegramBotAPI
    return jsonify({"success": False, "error": "Not implemented for pyTelegramBotAPI"}), 501

@app.route('/api/tg/webhook/setup', methods=['POST'])
def telegram_webhook_setup():
    """Ручная установка webhook (требует авторизации)"""
    auth_header = request.headers.get('Authorization')
    cron_secret = os.getenv('CRON_SECRET')
    
    # Защита от несанкционированного доступа
    if cron_secret and auth_header != f"Bearer {cron_secret}":
        return jsonify({"success": False, "error": "Unauthorized"}), 401
    
    # Опционально: перезагрузить конфигурацию перед установкой
    if request.json and request.json.get('reload_config'):
        telegram_service.reload_config()
    
    # Для pyTelegramBotAPI webhook настраивается вручную через Telegram API
    return jsonify({
        "success": False,
        "error": "Not implemented",
        "message": "Используйте команду: curl -X POST 'https://api.telegram.org/bot<TOKEN>/setWebhook' -d 'url=<WEBHOOK_URL>'"
    }), 501

@app.route('/api/tg/config/reload', methods=['POST'])
def telegram_config_reload():
    """Перезагрузка конфигурации из .env (требует авторизации)"""
    auth_header = request.headers.get('Authorization')
    cron_secret = os.getenv('CRON_SECRET')
    
    # Защита от несанкционированного доступа
    if cron_secret and auth_header != f"Bearer {cron_secret}":
        return jsonify({"success": False, "error": "Unauthorized"}), 401
    
    try:
        telegram_service.reload_config()
        return jsonify({
            "success": True,
            "message": "Конфигурация перезагружена",
            "webhook_url": telegram_service.webhook_url
        }), 200
    except Exception as e:
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500

# Регистрация blueprints
app.register_blueprint(market_bp, url_prefix='/api/market')
app.register_blueprint(analysis_bp, url_prefix='/api/analysis')
app.register_blueprint(news_bp, url_prefix='/api/news')
app.register_blueprint(chart_bp, url_prefix='/api/chart')
app.register_blueprint(llm_bp, url_prefix='/api/llm')
app.register_blueprint(auth_bp, url_prefix='/api/auth')

@app.route('/')
def index():
    return jsonify({
        "name": "Astra Analyzer Pro API",
        "version": "2.1.0",
        "status": "running",
        "symbol": SYMBOL,
        "endpoints": {
            "market": "/api/market/candles",
            "analysis": "/api/analysis/calculate",
            "cron_trigger": "/api/cron/watcher",
            "tg_webhook": "/api/tg/webhook"
        }
    })

# Legacy роуты (твои оригинальные)
@app.route('/config')
def config_legacy():
    from routes.market_routes import get_config
    return get_config()

@app.route('/calculate', methods=['POST'])
def calculate_legacy():
    from routes.analysis_routes import calculate_trade
    return calculate_trade()

@app.route('/analyze', methods=['POST'])
def analyze_legacy():
    from routes.analysis_routes import analyze_trade
    return analyze_trade()

# Обработчики ошибок
@app.errorhandler(404)
def not_found(e):
    return jsonify({"error": "Endpoint not found"}), 404

@app.errorhandler(Exception)
def handle_exception(e):
    logger.error(f"Unhandled exception: {str(e)}", exc_info=True)
    return jsonify({"error": "Unexpected error", "message": str(e)}), 500

def check_network_connectivity():
    try:
        import requests
        response = requests.get('https://api.ipify.org', timeout=5)
        if response.status_code == 200:
            return response.text.strip()
    except:
        return None

def initialize_services():
    from services.yfinance_service import yfinance_service
    from services.llm_service import llm_service
    
    services_status = {"llm": {}, "yfinance": False, "network": None}
    
    external_ip = check_network_connectivity()
    if external_ip:
        services_status["network"] = external_ip
        logger.info(f"✓ Network: {external_ip}")
    
    services_status["llm"]["gemini"] = bool(llm_service.gemini_key)
    
    try:
        if yfinance_service.validate_symbol():
            services_status["yfinance"] = True
            logger.info(f"✓ Symbol {SYMBOL} validated")
    except Exception as e:
        logger.error(f"Symbol validation error: {e}")
    
    return services_status

if __name__ == '__main__':
    import socket
    logger.info("=" * 60)
    logger.info("ASTRA ANALYZER PRO - SERVER STARTING")
    
    initialize_services()
    
    # Настройка Telegram бота (polling или webhook)
    use_polling = os.getenv('USE_TELEGRAM_POLLING', 'false').lower() == 'true'
    webhook_url = os.getenv('TELEGRAM_WEBHOOK_URL')
    
    if use_polling:
        # POLLING РЕЖИМ (для локальной разработки)
        logger.info("🤖 Запуск Telegram бота в POLLING режиме...")
        logger.info("   Webhook не требуется!")
        
        def start_polling_thread():
            """Запускает polling в отдельном потоке"""
            try:
                telegram_service.start_polling()
            except Exception as e:
                logger.error(f"❌ Ошибка polling: {e}")
        
        polling_thread = threading.Thread(target=start_polling_thread, daemon=True)
        polling_thread.start()
        
    elif webhook_url:
        # WEBHOOK РЕЖИМ (для продакшена)
        logger.info(f"📱 Telegram webhook URL: {webhook_url}")
        logger.info("   ⚠️ Webhook нужно настроить вручную через Telegram API:")
        logger.info(f"   curl -X POST 'https://api.telegram.org/bot<TOKEN>/setWebhook' -d 'url={webhook_url}'")
    else:
        logger.info("📱 Telegram бот не настроен")
        logger.info("   Установите USE_TELEGRAM_POLLING=true для polling режима")
        logger.info("   Или TELEGRAM_WEBHOOK_URL для webhook режима")
    
    # Запуск Watcher в фоне (только если НЕ на Vercel)
    # if start_watcher and not os.getenv('VERCEL'):
    #     try:
    #         watcher_thread = threading.Thread(target=start_watcher, daemon=True)
    #         watcher_thread.start()
    #         logger.info("🚀 ASTRA WATCHER STARTED IN BACKGROUND")
    #     except Exception as e:
    #         logger.error(f"Failed to start Watcher: {e}")

    try:
        hostname = socket.gethostname()
        local_ip = socket.gethostbyname(hostname)
        logger.info(f"Server ready at http://{local_ip}:{FLASK_PORT}")
    except:
        pass

    app.run(host='0.0.0.0', port=FLASK_PORT, debug=FLASK_DEBUG, threaded=True)