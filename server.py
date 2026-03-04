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

# Импорт Telegram сервиса и вспомогательных сервисов
from services.telegram_service import telegram_service
from services.db_service import db_service
from services.oanda_service import oanda_service

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

# --- ЭНДПОИНТЫ ДЛЯ CRON ---
@app.route('/api/cron/watcher', methods=['GET'])
def trigger_watcher():
    auth_header = request.headers.get('Authorization')
    cron_secret = os.getenv('CRON_SECRET')
    
    if cron_secret and auth_header != f"Bearer {cron_secret}":
        return jsonify({"success": False, "error": "Unauthorized"}), 401
    
    if run_analysis_cycle:
        logger.info("⏰ Cron Trigger: Starting analysis cycle (Watcher)...")
        # Вызываем функцию анализа напрямую
        run_analysis_cycle() 
        return jsonify({"success": True, "message": "Analysis complete"}), 200
    
    return jsonify({"success": False, "error": "Watcher service not available"}), 500


@app.route('/api/cron/manager', methods=['GET'])
def trigger_manager():
    """
    Cron endpoint для Trade Manager (управление активной сделкой).
    Рекомендуется вызывать чаще, чем watcher (например, раз в 1–2 минуты).
    """
    auth_header = request.headers.get('Authorization')
    cron_secret = os.getenv('CRON_SECRET')
    
    if cron_secret and auth_header != f"Bearer {cron_secret}":
        return jsonify({"success": False, "error": "Unauthorized"}), 401
    
    if run_analysis_cycle:
        logger.info("⏰ Cron Trigger: Starting trade manager cycle...")
        try:
            from watcher import run_trade_manager_cycle
            run_trade_manager_cycle()
        except Exception as e:
            logger.error(f"❌ Error in trade manager cycle: {e}", exc_info=True)
            return jsonify({"success": False, "error": str(e)}), 500
        return jsonify({"success": True, "message": "Trade manager cycle complete"}), 200
    
    return jsonify({"success": False, "error": "Watcher/Manager service not available"}), 500

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
            "manager_cron": "/api/cron/manager",
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


def price_monitor_loop():
    """
    Фоновый поток Price Monitor: раз в 5 секунд проверяет активную сделку
    по текущей цене (без LLM) и:
    - закрывает по SL / TP
    Логика 1R/BE/1R+5% реализована в Watcher/Manager и здесь не дублируется.
    """
    import time

    def _to_float(val, default=0.0):
        try:
            return float(val)
        except (TypeError, ValueError):
            return default

    logger.info("🧵 Price Monitor thread started (interval = 5s)")
    while True:
        try:
            # Получаем последнюю АКТИВНУЮ торговую сделку (BUY/SELL) для XAU_USD
            trade = db_service.get_active_trade(symbol="XAU_USD")
            if not trade:
                time.sleep(5)
                continue

            signal_type = (trade.get("signal_type") or "").upper()
            if signal_type not in ("BUY", "SELL"):
                time.sleep(5)
                continue

            entry_price = _to_float(trade.get("entry_price"))
            stop_loss = _to_float(trade.get("stop_loss"))
            take_profit = _to_float(trade.get("take_profit"))
            signal_id = trade.get("id")

            if not signal_id or entry_price == 0 or (stop_loss == 0 and take_profit == 0):
                time.sleep(5)
                continue

            # Получаем текущую цену по S5 из OANDA (нужна для логики и для closed_price при закрытии)
            # Используем секундный таймфрейм S5 для более точного мониторинга
            price_data = oanda_service.get_candles(timeframe="S5", limit=1)
            candles = price_data.get("candles") if isinstance(price_data, dict) else None
            if not candles:
                time.sleep(5)
                continue

            last_candle = candles[-1]
            current_price = _to_float(last_candle.get("close"))
            if current_price == 0:
                time.sleep(5)
                continue

            # Был ли достигнут entry (сделка активирована)
            entry_notified = False
            try:
                entry_notified = db_service.get_signal_entry_notified(signal_id)
            except Exception as e:
                logger.warning(f"⚠️ Price Monitor: get_signal_entry_notified error for id={signal_id}: {e}")

            # Не активирована и старше 24ч → закрыть как cancelled_no_fill без TG
            if not entry_notified:
                from datetime import datetime, timezone, timedelta
                trade_ts_raw = trade.get("timestamp") or trade.get("created_at")
                if trade_ts_raw:
                    try:
                        trade_ts = datetime.fromisoformat(str(trade_ts_raw).replace("Z", "+00:00"))
                        if trade_ts.tzinfo is None:
                            trade_ts = trade_ts.replace(tzinfo=timezone.utc)
                        if (datetime.now(timezone.utc) - trade_ts) > timedelta(hours=24):
                            db_service.update_signal_result(
                                signal_id, 0.0, current_price, status="cancelled_no_fill"
                            )
                            logger.info(
                                f"⏳ Price Monitor: сделка id={signal_id} не активирована за 24ч — "
                                f"закрыта как cancelled_no_fill (без TG)"
                            )
                            time.sleep(5)
                            continue
                    except Exception:
                        pass

            # 1. Проверяем достижение цены входа (активация сделки)
            crossed_entry = False
            if signal_type == "BUY" and current_price >= entry_price > 0:
                crossed_entry = True
            elif signal_type == "SELL" and current_price <= entry_price > 0:
                crossed_entry = True
            if crossed_entry and not entry_notified:
                db_service.mark_entry_notified(signal_id)
                user_ids_filled = db_service.get_all_active_users()
                if user_ids_filled:
                    msg_filled = (
                        f"✅ <b>Вход достигнут — сделка активирована</b>\n\n"
                        f"id={signal_id} | {signal_type} | цена входа <b>{entry_price:.2f}</b> достигнута.\n"
                        f"Текущая цена: {current_price:.2f}. Менеджер/монитор ведут сделку (SL/TP)."
                    )
                    telegram_service.broadcast_deals_only(user_ids_filled, msg_filled)

            # 2. Правила 1R → BE и 1R+5% → SL на 1R (по текущей цене M1)
            risk_amount = abs(entry_price - stop_loss)
            be_threshold = entry_price * 0.0005  # ~0.05% от цены
            sl_is_be = abs(stop_loss - entry_price) <= be_threshold
            at_1r = False
            if risk_amount > 0:
                if signal_type == "BUY":
                    at_1r = current_price >= entry_price + risk_amount
                else:
                    at_1r = current_price <= entry_price - risk_amount
            if at_1r and not sl_is_be:
                new_sl = entry_price
                logger.info(
                    f"🔒 Price Monitor: достигнут 1R в плюс. Переводим SL в BE: {stop_loss:.2f} → {new_sl:.2f}."
                )
                db_service.update_signal_sl_and_status(signal_id, new_sl, status=None)
                users_be = db_service.get_all_active_users()
                if users_be:
                    msg_be = (
                        f"🔒 <b>ASTRA Monitor:</b> SL переведён в BE по правилу 1R.\n\n"
                        f"id={signal_id} | {signal_type}\n"
                        f"SL: {stop_loss:.2f} → {new_sl:.2f}\n"
                        f"TP: {take_profit:.2f}"
                    )
                    telegram_service.broadcast_deals_only(users_be, msg_be)
                stop_loss = new_sl
                sl_is_be = True

            # 1R+5%: перенос SL на уровень 1R, если уже в BE
            MANAGER_1R_LOCK_MARGIN = 0.05
            if risk_amount > 0 and sl_is_be:
                sl_1r_level = entry_price + risk_amount if signal_type == "BUY" else entry_price - risk_amount
                at_1r_plus_margin = False
                if signal_type == "BUY":
                    at_1r_plus_margin = current_price >= entry_price + risk_amount * (1.0 + MANAGER_1R_LOCK_MARGIN)
                else:
                    at_1r_plus_margin = current_price <= entry_price - risk_amount * (1.0 + MANAGER_1R_LOCK_MARGIN)
                if at_1r_plus_margin and abs(stop_loss - sl_1r_level) > be_threshold:
                    logger.info(
                        f"🔒 Price Monitor: цена прошла 1R+{MANAGER_1R_LOCK_MARGIN*100:.0f}%. "
                        f"Переносим SL на уровень 1R: {stop_loss:.2f} → {sl_1r_level:.2f}."
                    )
                    db_service.update_signal_sl_and_status(signal_id, sl_1r_level, status=None)
                    users_lock = db_service.get_all_active_users()
                    if users_lock:
                        msg_lock = (
                            f"🔒 <b>ASTRA Monitor:</b> SL перенесён на уровень 1R.\n\n"
                            f"id={signal_id} | {signal_type}\n"
                            f"SL: {stop_loss:.2f} → {sl_1r_level:.2f}\n"
                            f"TP: {take_profit:.2f}"
                        )
                        telegram_service.broadcast_deals_only(users_lock, msg_lock)
                    stop_loss = sl_1r_level

            # 3. Проверяем SL / TP для фактического закрытия
            hit_sl = (
                (signal_type == "BUY" and current_price <= stop_loss)
                or (signal_type == "SELL" and current_price >= stop_loss)
            )
            hit_tp = (
                (signal_type == "BUY" and current_price >= take_profit)
                or (signal_type == "SELL" and current_price <= take_profit)
            )

            if not (hit_sl or hit_tp):
                time.sleep(5)
                continue

            # Защита от двойного срабатывания: перечитаем актуальную активную сделку перед закрытием
            latest = db_service.get_active_trade(symbol="XAU_USD")
            if not latest or latest.get("id") != signal_id:
                time.sleep(5)
                continue

            # Расчёт фактического PnL
            if signal_type == "BUY":
                result_pnl = current_price - entry_price
            else:
                result_pnl = entry_price - current_price

            user_ids = db_service.get_all_active_users()

            if hit_sl:
                logger.info(
                    f"🛑 Price Monitor: SL hit for trade id={signal_id} "
                    f"type={signal_type} entry={entry_price:.2f} sl={stop_loss:.2f} price={current_price:.2f}"
                )
                db_service.update_signal_result(
                    signal_id, result_pnl, current_price, status="closed_sl"
                )
                if user_ids:
                    msg = (
                        f"🛑 ASTRA Price Monitor: сделка id={signal_id} закрыта по SL.\n"
                        f"Тип: {signal_type}\n"
                        f"Вход: {entry_price:.2f}\n"
                        f"SL: {stop_loss:.2f}\n"
                        f"Фактическая цена закрытия: {current_price:.2f}\n"
                        f"P/L: {result_pnl:.2f}"
                    )
                    telegram_service.broadcast_deals_only(user_ids, msg)
            elif hit_tp:
                logger.info(
                    f"✅ Price Monitor: TP hit for trade id={signal_id} "
                    f"type={signal_type} entry={entry_price:.2f} tp={take_profit:.2f} price={current_price:.2f}"
                )
                db_service.update_signal_result(
                    signal_id, result_pnl, current_price, status="closed_tp"
                )
                if user_ids:
                    msg = (
                        f"✅ ASTRA Price Monitor: сделка id={signal_id} закрыта по TP 🎯\n"
                        f"Тип: {signal_type}\n"
                        f"Вход: {entry_price:.2f}\n"
                        f"TP: {take_profit:.2f}\n"
                        f"Фактическая цена закрытия: {current_price:.2f}\n"
                        f"P/L: {result_pnl:.2f}"
                    )
                    telegram_service.broadcast_deals_only(user_ids, msg)

        except Exception as e:
            logger.error(f"❌ Price Monitor error: {e}", exc_info=True)
        finally:
            time.sleep(5)

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

    # Запускаем фоновый монитор цены (отдельный поток-демон)
    try:
        monitor_thread = threading.Thread(target=price_monitor_loop, daemon=True)
        monitor_thread.start()
    except Exception as e:
        logger.error(f"Failed to start Price Monitor thread: {e}")

    try:
        hostname = socket.gethostname()
        local_ip = socket.gethostbyname(hostname)
        logger.info(f"Server ready at http://{local_ip}:{FLASK_PORT}")
    except:
        pass

    app.run(host='0.0.0.0', port=FLASK_PORT, debug=FLASK_DEBUG, threaded=True)