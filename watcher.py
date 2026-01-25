import os
from datetime import datetime, timedelta, timezone # Добавили timezone
import logging
import json
from services.db_service import db_service
from services.telegram_service import telegram_service 

# Переменная для отслеживания времени последнего сигнала (теперь дублируется в облаке)
LAST_SIGNAL_TIME = None 

# константы
SIGNAL_COOLDOWN_HOURS = 2 
STRONG_SETUPS = ['BOS', 'CHOCH', 'OB_RETEST', 'FVG_FILL', 'SWEEP']

# Подключение сервисов
try:
    from services.oanda_service import oanda_service
    from services.llm_service import llm_service
    try:
        from services.smc_detector import smc_detector 
    except ImportError:
        smc_detector = None
    from services.news_service import news_service
except ImportError as e:
    print(f"Watcher Critical Import Error: {e}")

logger = logging.getLogger("AstraWatcher")

def is_market_active():
    """Проверка активности рынка по Астраханскому времени (GMT+4)"""
    # ФИКС ПУНКТА А: Принудительно используем UTC+4 для точности в облаке
    astrakhan_tz = timezone(timedelta(hours=4))
    now = datetime.now(astrakhan_tz)
    
    weekday = now.weekday()
    hour = now.hour
    if weekday == 5: return False 
    if weekday == 6: return False 
    if weekday == 0 and hour < 2: return False 
    if hour == 1: return False 
    return True

def is_news_blockactive():
    """Твоя логика блокировки по новостям"""
    if not news_service: return False
    try:
        upcoming_news = news_service.get_upcoming_news(hours=2, currencies=['USD'], impact=['High'])
        past_news = news_service.get_past_news(hours=1)
        now_ts = int(datetime.now().timestamp())
        for event in upcoming_news:
            ts = event.get('timestamp')
            if ts and (ts - now_ts) < (45 * 60):
                logger.warning(f"🚫 NEWS BLOCK: {event['title']}")
                return True
        for event in past_news:
            ts = event.get('timestamp')
            if ts and (now_ts - ts) < (15 * 60):
                logger.warning(f"🚫 NEWS BLOCK: {event['title']}")
                return True
    except Exception as e:
        logger.error(f"Ошибка новостей: {e}")
    return False

def check_cooldown():
    """ФИКС ПУНКТА В: Проверка кулдауна через облачную базу Supabase"""
    try:
        # Тянем время последнего сигнала из облака
        last_time = db_service.get_last_signal_time()
        # Сравниваем в формате UTC
        now = datetime.now(timezone.utc)
        
        time_passed = now - last_time
        if time_passed.total_seconds() < (SIGNAL_COOLDOWN_HOURS * 3600):
            # Если 2 часа еще не прошло
            remaining = timedelta(seconds=(SIGNAL_COOLDOWN_HOURS * 3600) - time_passed.total_seconds())
            logger.info(f"⏳ Кулдаун активен (Cloud Sync). Ждем еще {str(remaining).split('.')[0]}")
            return False
        
        return True
    except Exception as e:
        logger.error(f"Ошибка проверки кулдауна через БД: {e}")
        return True # В случае сбоя БД разрешаем анализ, чтобы не "ослепнуть"

def format_signal_message(ai_response):
    """Превращает JSON от Gemini в красивый текст для Телеграма"""
    try:
        # Пытаемся вытащить JSON из ответа Gemini
        start = ai_response.find('{')
        end = ai_response.rfind('}') + 1
        data = json.loads(ai_response[start:end])
        
        action = data.get("ACTION", "N/A")
        emoji = "🟢 BUY" if action == "BUY" else "🔴 SELL"
        
        msg = (
            f"<b>🚀 ASTRA SIGNAL: GOLD (XAU/USD)</b>\n\n"
            f"Направление: <b>{emoji}</b>\n"
            f"Вход: <code>{data.get('ENTRY')}</code>\n"
            f"Стоп: <code>{data.get('SL')}</code>\n"
            f"Тейк: <code>{data.get('TP')}</code>\n\n"
            f"<b>Анализ:</b>\n<i>{data.get('REASON', 'По техническому анализу SMC')}</i>"
        )
        return msg
    except:
        # Если JSON не парсится, шлем как есть
        return f"<b>📢 НОВЫЙ СИГНАЛ XAUUSD:</b>\n\n{ai_response}"

def run_analysis_cycle():
    """Основная функция, которую будет дергать Vercel Cron"""
    global LAST_SIGNAL_TIME
    logger.info("📡 Запуск цикла анализа (Vercel Triggered)")
    
    if not is_market_active():
        logger.info("Market is IDLE. Skipping...")
        return

    if is_news_blockactive():
        return 
    
    data = oanda_service.get_candles(timeframe='M15', limit=100)
    if "error" in data: return
    
    candles = data.get("candles", [])
    if not smc_detector: return

    # 1. Сначала делаем технический анализ
    analysis = smc_detector.analyze(candles)
    
    # 2. Получаем текущие параметры структуры
    found_signals = [s.get('type') for s in analysis.get('signals', [])]
    trend = analysis.get('trend', 'NEUTRAL')
    current_zone = analysis.get('advanced', {}).get('key_levels', {}).get('Current_Zone', 'N/A')

    # --- ШАГ 1: ТЕХНИЧЕСКИЙ ФИЛЬТР ПО ПАТТЕРНАМ ---
    is_worth_it = any(setup in found_signals for setup in STRONG_SETUPS)
    
    if analysis.get('signals_count', 0) == 0 or not is_worth_it:
        # logger.info("🛰 Сильных сетапов нет.")
        return

    # --- ШАГ 2: ПРЕД-ФИЛЬТР ВЫГОДНОЙ ЦЕНЫ (Save LLM Tokens) ---
    # Мы не будем даже беспокоить Gemini, если математика зон против нас
    if trend == "UPTREND" and current_zone == "PREMIUM":
        logger.info(f"🛑 Пропуск: Тренд UP, но цена в PREMIUM. Слишком дорого для покупки.")
        return
    
    if trend == "DOWNTREND" and current_zone == "DISCOUNT":
        logger.info(f"🛑 Пропуск: Тренд DOWN, но цена в DISCOUNT. Слишком дешево для продажи.")
        return

    # --- ШАГ 3: ФИЛЬТР КУЛДАУНА ---
    if not check_cooldown():
        return

    # --- ШАГ 4: ЕСЛИ ВСЁ ОК — ЗОВЕМ GEMINI ---
    logger.info(f"🎯 Математика подтверждена ({trend} в зоне {current_zone})! Запрашиваем вердикт Gemini...")
    ai_response = llm_service.get_signal_verdict(analysis)

    # Проверка консенсуса ИИ
    is_confirmed = '"ACTION": "BUY"' in ai_response.upper() or '"ACTION": "SELL"' in ai_response.upper()
    is_wait = "WAIT" in ai_response.upper() or '"ACTION": "WAIT"' in ai_response.upper()
    
    if is_confirmed and not is_wait:
        logger.info("🔥 КОНСЕНСУС ДОСТИГНУТ!")
        
        # 1. ОБНОВЛЯЕМ ВРЕМЯ ПОСЛЕДНЕГО СИГНАЛА В SUPABASE (Cloud Memory)
        db_service.update_last_signal_time()
        
        # 2. Получаем всех активных юзеров из Supabase
        user_ids = db_service.get_all_active_users()
        
        if user_ids:
            logger.info(f"📤 Рассылка сигнала {len(user_ids)} пользователям...")
            formatted_msg = format_signal_message(ai_response)
            
            # 3. Рассылаем через телеграм сервис
            success_count = telegram_service.broadcast_signal(user_ids, formatted_msg)
            logger.info(f"✅ Успешно доставлено: {success_count}")
        else:
            logger.warning("⚠️ Нет активных пользователей в базе для рассылки.")
    else:
        logger.info("⚖️ ИИ отклонил вход или выдал WAIT.")

def start_watcher():
    run_analysis_cycle()

if __name__ == "__main__":
    start_watcher()