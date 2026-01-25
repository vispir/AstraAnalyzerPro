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
import requests # Добавили для работы с API Telegram напрямую
from datetime import datetime, timedelta, timezone # Добавили для работы с временем в боте

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

# Импорт сервисов для работы бота
from services.telegram_service import telegram_service
from services.oanda_service import oanda_service
from services.db_service import db_service
try:
    from services.smc_detector import smc_detector
except ImportError:
    smc_detector = None

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

# --- НОВЫЙ ЭНДПОИНТ: TELEGRAM WEBHOOK ---
@app.route('/api/tg/webhook', methods=['POST'])
def telegram_webhook():
    """Обработчик входящих сообщений и нажатий кнопок в Telegram"""
    update = request.json
    if not update:
        return "OK", 200

    chat_id = None
    text = ""

    # ЛОГИКА ОБРАБОТКИ НАЖАТИЙ КРАСИВЫХ КНОПОК (Inline Buttons)
    if "callback_query" in update:
        query = update["callback_query"]
        chat_id = query["message"]["chat"]["id"]
        callback_data = query["data"]
        
        # 1. Сразу отвечаем Телеграму, чтобы убрать крутилку на кнопке
        try:
            requests.post(f"{telegram_service.api_url}/answerCallbackQuery", 
                          json={"callback_query_id": query["id"]})
        except:
            pass

        # Перенаправляем сигнал в переменную text, чтобы сработала старая логика
        if callback_data == "price": text = "📊 Курс Gold"
        elif callback_data == "trend": text = "📈 Тренд M15"
        elif callback_data == "status": text = "🛡️ Статус системы"
        elif callback_data == "last": text = "🔔 Последний сигнал"
    
    # ЛОГИКА ОБРАБОТКИ ОБЫЧНОГО ТЕКСТА
    elif "message" in update:
        message = update["message"]
        chat_id = message["chat"]["id"]
        text = message.get("text", "")
    
    if not chat_id:
        return "OK", 200

    # 1. Команда /start
    if text == "/start":
        welcome = (
            "<b>💎 ASTRA ANALYZER PRO</b>\n"
            "————————————————\n"
            "Привет, Трейдер! 🚀\n\n"
            "Я твой автономный терминал для торговли золотом. "
            "Я использую <b>SMC структуру</b> и <b>AI</b> для поиска снайперских входов.\n\n"
            "<b>Доступные функции:</b>\n"
            "🔹 <code>Курс</code> — Живая цена OANDA\n"
            "🔹 <code>Анализ</code> — Тренд и уровни M15\n"
            "🔹 <code>Статус</code> — Мониторинг систем\n"
            "————————————————\n"
            "<b>Выбери действие:</b>"
        )
        telegram_service.send_message(chat_id, welcome, reply_markup=telegram_service.get_inline_menu())

    # 2. Кнопка: 📊 Курс Gold
    elif text == "📊 Курс Gold":
        data = oanda_service.get_candles(timeframe='M15', limit=1)
        if "candles" in data and len(data["candles"]) > 0:
            price = data["candles"][-1]["close"]
            telegram_service.send_message(chat_id, f"<b>💰 Текущая цена XAU/USD (Live):</b> <code>{price}</code>")
        else:
            telegram_service.send_message(chat_id, "❌ Ошибка получения котировок из OANDA.")

    # 3. Кнопка: 📈 Тренд M15
    elif text == "📈 Тренд M15":
        data = oanda_service.get_candles(timeframe='M15', limit=100)
        if "candles" in data and smc_detector:
            analysis = smc_detector.analyze(data["candles"])
            trend = analysis.get('trend', 'N/A')
            emoji = "🐂 BULLISH" if "UP" in trend.upper() else "🐻 BEARISH" if "DOWN" in trend.upper() else "↔️ RANGING"
            
            resp = (
                f"<b>📈 Структура рынка (M15):</b>\n\n"
                f"Тренд: <b>{emoji}</b>\n"
                f"Сигналов SMC: <code>{analysis.get('signals_count', 0)}</code>\n"
                f"Зона: <code>{analysis.get('advanced', {}).get('key_levels', {}).get('Current_Zone', 'N/A')}</code>"
            )
            telegram_service.send_message(chat_id, resp)
        else:
            telegram_service.send_message(chat_id, "❌ SMC детектор временно недоступен.")

    # 4. Кнопка: 🛡️ Статус системы
    elif text == "🛡️ Статус системы":
        last_sig = db_service.get_last_signal_time()
        # Время в Астрахани (UTC+4)
        local_now = datetime.now(timezone.utc) + timedelta(hours=4)
        last_sig_local = last_sig + timedelta(hours=4)
        
        status_msg = (
            f"<b>🛡️ Статус Astra Analyzer:</b>\n\n"
            f"✅ Система: <b>ONLINE</b>\n"
            f"🛰️ Наблюдатель: <b>ACTIVE</b>\n"
            f"🔔 Последний сигнал: <code>{last_sig_local.strftime('%H:%M:%S')}</code>\n"
            f"📍 Время сервера: <code>{local_now.strftime('%H:%M:%S')} (UTC+4)</code>"
        )
        telegram_service.send_message(chat_id, status_msg)
        
    # 5. Кнопка: 🔔 Последний сигнал
    elif text == "🔔 Последний сигнал":
        last_sig = db_service.get_last_signal_time()
        diff = datetime.now(timezone.utc) - last_sig
        minutes = int(diff.total_seconds() // 60)
        
        telegram_service.send_message(chat_id, f"🔔 Последний подтвержденный сигнал был отправлен <b>{minutes} мин. назад</b>.\n\nСледующий анализ через 15 минут.")

    return "OK", 200

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
    
    # Запуск Watcher в фоне (только если НЕ на Vercel)
    if start_watcher and not os.getenv('VERCEL'):
        try:
            watcher_thread = threading.Thread(target=start_watcher, daemon=True)
            watcher_thread.start()
            logger.info("🚀 ASTRA WATCHER STARTED IN BACKGROUND")
        except Exception as e:
            logger.error(f"Failed to start Watcher: {e}")

    try:
        hostname = socket.gethostname()
        local_ip = socket.gethostbyname(hostname)
        logger.info(f"Server ready at http://{local_ip}:{FLASK_PORT}")
    except:
        pass

    app.run(host='0.0.0.0', port=FLASK_PORT, debug=FLASK_DEBUG, threaded=True)