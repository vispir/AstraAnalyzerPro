#!/usr/bin/env python3
"""
Astra Analyzer Pro - Market Watcher Worker
Использует APScheduler для периодического запуска анализа рынка
"""
import os
import sys
import logging
from datetime import datetime
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.events import EVENT_JOB_EXECUTED, EVENT_JOB_ERROR

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler('watcher_worker.log')
    ]
)
logger = logging.getLogger("WatcherWorker")

# Импортируем функцию анализа из watcher.py
try:
    from watcher import run_analysis_cycle
    logger.info("✅ Модуль watcher.py успешно импортирован")
except ImportError as e:
    logger.error(f"❌ Ошибка импорта watcher.py: {e}")
    sys.exit(1)

# Создаем планировщик
scheduler = BlockingScheduler()

@scheduler.scheduled_job(
    CronTrigger.from_crontab('*/15 * * * *'),
    id='market_analysis_job',
    max_instances=1,  # Предотвращаем одновременный запуск
    coalesce=True,    # Если пропустили запуск, не запускаем несколько раз
    misfire_grace_time=300  # Допускаем опоздание до 5 минут
)
def scheduled_market_analysis():
    """
    Запланированная задача анализа рынка
    Выполняется каждые 15 минут
    """
    logger.info("=" * 60)
    logger.info("🔄 Запуск запланированного анализа рынка")
    logger.info(f"⏰ Время: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info("=" * 60)
    
    try:
        # Вызываем функцию анализа из watcher.py
        run_analysis_cycle()
        logger.info("✅ Анализ успешно завершен")
    except Exception as e:
        logger.error(f"❌ Ошибка при выполнении анализа: {e}", exc_info=True)
    
    logger.info("=" * 60)

def job_listener(event):
    """Слушатель событий планировщика для логирования"""
    if event.exception:
        logger.error(f"❌ Задача завершилась с ошибкой: {event.exception}")
    else:
        logger.debug(f"✅ Задача выполнена успешно (Job ID: {event.job_id})")

def main():
    """Главная функция запуска worker'a"""
    logger.info("=" * 60)
    logger.info("🚀 Astra Watcher Worker запускается...")
    logger.info("=" * 60)
    logger.info("📋 Конфигурация:")
    logger.info("   • Расписание: Каждые 15 минут (*/15 * * * *)")
    logger.info("   • Максимум одновременных запусков: 1")
    logger.info("   • Допустимое опоздание: 5 минут")
    logger.info("   • Лог-файл: watcher_worker.log")
    logger.info("=" * 60)
    
    # Добавляем слушатель событий
    scheduler.add_listener(job_listener, EVENT_JOB_EXECUTED | EVENT_JOB_ERROR)
    
    # Показываем следующие запуски
    jobs = scheduler.get_jobs()
    if jobs:
        for job in jobs:
            next_run = job.next_run_time
            if next_run:
                logger.info(f"⏳ Следующий запуск: {next_run.strftime('%Y-%m-%d %H:%M:%S')}")
    
    logger.info("✅ Worker готов к работе. Нажмите Ctrl+C для остановки.")
    logger.info("=" * 60)
    
    try:
        # ОПЦИОНАЛЬНО: Запустить анализ сразу при старте (закомментируйте, если не нужно)
        # logger.info("🔥 Запуск первичного анализа...")
        # run_analysis_cycle()
        
        # Запускаем планировщик (блокирующий вызов)
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("=" * 60)
        logger.info("👋 Получен сигнал остановки")
        logger.info("🛑 Останавливаем Worker...")
        scheduler.shutdown(wait=True)
        logger.info("✅ Worker успешно остановлен")
        logger.info("=" * 60)
    except Exception as e:
        logger.error(f"❌ Критическая ошибка: {e}", exc_info=True)
        sys.exit(1)

if __name__ == "__main__":
    main()
