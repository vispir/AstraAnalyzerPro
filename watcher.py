import time
from datetime import datetime
import logging

LAST_SIGNAL_TIME = None
SIGNAL_COOLDOWN_HOURS = 2  # Минимальная пауза между сигналами ИИ в часах

# Список сетапов, которые мы считаем достойными внимания ИИ (усиленный фильтр)
STRONG_SETUPS = ['BOS', 'CHOCH', 'OB_RETEST', 'FVG_FILL', 'SWEEP']

# Импортируем сам объект сервиса
try:
    from services.oanda_service import oanda_service
    from services.llm_service import llm_service
    # Пробуем импортировать радар
    try:
        from services.smc_detector import smc_detector 
    except ImportError:
        smc_detector = None
    print("Watcher: Services connected successfully")
except ImportError as e:
    print(f"Watcher Critical Import Error: {e}")

# Настройка простого логирования без спецсимволов
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s'
)
logger = logging.getLogger("AstraWatcher")

def is_market_active():
    """Проверка активности рынка по Астраханскому времени (GMT+4)"""
    now = datetime.now()
    weekday = now.weekday()
    hour = now.hour

    if weekday == 5: return False # Суббота
    if weekday == 6: return False # Воскресенье
    if weekday == 0 and hour < 2: return False # Открытие в ПН
    if hour == 1: return False # Технический перерыв

    return True

def is_market_open():
    """Базовая проверка на выходные (для обратной совместимости)"""
    return is_market_active()

# Импортируем сервис новостей
try:
    from services.news_service import news_service
except ImportError:
    news_service = None

def is_news_blockactive():
    """
    Проверка: есть ли сейчас или скоро важные новости по USD.
    Блокировка: 45 минут ДО и 15 минут ПОСЛЕ.
    """
    if not news_service:
        return False

    try:
        # Получаем ближайшие новости по USD с высокой важностью (High)
        upcoming_news = news_service.get_upcoming_news(hours=2, currencies=['USD'], impact=['High'])
        
        # Получаем прошедшие новости за последние 30 минут, чтобы проверить окно "ПОСЛЕ"
        past_news = news_service.get_past_news(hours=1)
        
        now_ts = int(datetime.now().timestamp())

        # 1. Проверка окна ДО (45 минут)
        for event in upcoming_news:
            ts = event.get('timestamp')
            if ts and (ts - now_ts) < (45 * 60):
                logger.warning(f"🚫 NEWS BLOCK: До новости '{event['title']}' осталось меньше 45 мин.")
                return True

        # 2. Проверка окна ПОСЛЕ (15 минут)
        for event in past_news:
            ts = event.get('timestamp')
            if ts and (now_ts - ts) < (15 * 60):
                logger.warning(f"🚫 NEWS BLOCK: После новости '{event['title']}' прошло меньше 15 мин.")
                return True

    except Exception as e:
        logger.error(f"Ошибка при проверке новостей: {e}")
    
    return False

def run_analysis_cycle():
    """Снайперский цикл с защитой от спама и новостей"""
    global LAST_SIGNAL_TIME
    
    # --- ФИЛЬТР: НОВОСТИ ---
    if is_news_blockactive():
        return # Выходим сразу, не запрашивая котировки и не дергая Gemini
    
    data = oanda_service.get_candles(timeframe='M15', limit=100)
    if "error" in data: return
    
    candles = data.get("candles", [])
    if not smc_detector:
        logger.warning("SMC Detector not available")
        return

    analysis = smc_detector.analyze(candles)

    # --- ШАГ 1: УСИЛЕННЫЙ ТЕХНИЧЕСКИЙ ФИЛЬТР (До Gemini) ---
    # Проверяем, есть ли среди найденных сигналов те, что входят в STRONG_SETUPS
    # Также проверяем, что тренд не нейтральный
    found_signals = [s.get('type') for s in analysis.get('signals', [])]
    is_worth_it = any(setup in found_signals for setup in STRONG_SETUPS)
    
    # Если сигналов нет или они "слабые" — выходим, не тратя токены
    if analysis.get('signals_count', 0) == 0 or not is_worth_it:
        # logger.info("🛰 Наблюдатель: Сильных сетапов нет. Мониторим дальше...")
        return

    # --- ШАГ 2: ФИЛЬТР ПАУЗЫ (Защита токенов) ---
    if LAST_SIGNAL_TIME:
        time_passed = datetime.now() - LAST_SIGNAL_TIME
        if time_passed.total_seconds() < SIGNAL_COOLDOWN_HOURS * 3600:
            logger.info(f"⏳ Сетап найден, но мы на холде (пауза еще {SIGNAL_COOLDOWN_HOURS}ч).")
            return

    # --- ШАГ 3: ЕСЛИ ПРОШЛИ ФИЛЬТРЫ — ЗОВЕМ GEMINI ---
    logger.info(f"🎯 Снайпер обнаружил сильный сигнал ({found_signals})! Запрашиваем вердикт Gemini 3 Flash...")
    ai_response = llm_service.get_signal_verdict(analysis)

    # --- ШАГ 4: ПРОВЕРКА КОНСЕНСУСА ---
    # Ищем подтверждение входа (BUY/SELL) и отсутствие WAIT
    is_confirmed = '"ACTION": "BUY"' in ai_response.upper() or '"ACTION": "SELL"' in ai_response.upper()
    is_wait = "WAIT" in ai_response.upper() or '"ACTION": "WAIT"' in ai_response.upper()
    
    if is_confirmed and not is_wait:
        logger.info("🔥 КОНСЕНСУС ДОСТИГНУТ! 100% СИГНАЛ ПОДТВЕРЖДЕН.")
        logger.info(f"FINAL VERDICT FROM AI:\n{ai_response}")
        
        # Фиксируем время сигнала, чтобы не спамить
        LAST_SIGNAL_TIME = datetime.now() 
        # Здесь будет отправка: telegram_service.send_signal(ai_response)
    else:
        logger.info("⚖️ ИИ отклонил вход или выдал WAIT. Токены сохранены, сигнал проигнорирован.")

def start_watcher():
    logger.info("ASTRA WATCHER ACTIVATED (Funding Pips Schedule - GMT+4)")
    
    while True:
        try:
            now = datetime.now()
            
            if not is_market_active():
                logger.info("Market is IDLE (Weekend or Rollover). Checking again in 5 minutes...")
                time.sleep(300) 
                continue

            minutes = now.minute
            seconds = now.second
            
            next_check_in_minutes = 15 - (minutes % 15)
            wait_seconds = (next_check_in_minutes * 60) - seconds + 2
            
            logger.info(f"Market is OPEN. Next M15 analysis in {next_check_in_minutes} min {seconds} sec.")
            time.sleep(wait_seconds)
            
            run_analysis_cycle()
            
        except Exception as e:
            logger.error(f"Unexpected error in Watcher loop: {e}")
            time.sleep(60)

if __name__ == "__main__":
    start_watcher()