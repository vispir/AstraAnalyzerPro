"""
LLM Rate Limiter v1.0 - Защита от превышения лимитов
====================================================
Отслеживает количество вызовов LLM и блокирует при приближении к лимиту.
"""

import os
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Лимиты Gemini 2.0 Flash Experimental
MAX_REQUESTS_PER_DAY = 1500
SAFE_REQUESTS_PER_DAY = 100  # Безопасный лимит для watcher (оставляем запас)

# Файл для хранения статистики
STATS_FILE = Path(__file__).parent / 'llm_usage_stats.json'

def load_stats():
    """Загружаем статистику вызовов"""
    if not STATS_FILE.exists():
        return {'date': None, 'count': 0, 'history': []}
    
    try:
        with open(STATS_FILE, 'r') as f:
            return json.load(f)
    except:
        return {'date': None, 'count': 0, 'history': []}

def save_stats(stats):
    """Сохраняем статистику"""
    try:
        with open(STATS_FILE, 'w') as f:
            json.dump(stats, f, indent=2)
    except Exception as e:
        print(f"Warning: Could not save LLM stats: {e}")

def can_make_request():
    """
    Проверяет можно ли делать запрос к LLM
    
    Returns:
        tuple: (can_request: bool, reason: str, current_count: int, limit: int)
    """
    stats = load_stats()
    now = datetime.now(timezone.utc)
    today = now.strftime('%Y-%m-%d')
    
    # Сброс счётчика если новый день
    if stats['date'] != today:
        stats = {
            'date': today,
            'count': 0,
            'history': []
        }
    
    current_count = stats['count']
    
    # Проверка лимита
    if current_count >= SAFE_REQUESTS_PER_DAY:
        reason = f"⛔ ЛИМИТ! {current_count}/{SAFE_REQUESTS_PER_DAY} requests сегодня. Watcher остановлен до завтра."
        return False, reason, current_count, SAFE_REQUESTS_PER_DAY
    
    # Предупреждение при приближении к лимиту
    if current_count >= SAFE_REQUESTS_PER_DAY * 0.8:  # 80%
        reason = f"⚠️ ВНИМАНИЕ! {current_count}/{SAFE_REQUESTS_PER_DAY} requests. Приближаемся к лимиту!"
        return True, reason, current_count, SAFE_REQUESTS_PER_DAY
    
    # Всё ОК
    reason = f"✅ OK: {current_count}/{SAFE_REQUESTS_PER_DAY} requests сегодня"
    return True, reason, current_count, SAFE_REQUESTS_PER_DAY

def record_request():
    """Записываем успешный запрос к LLM"""
    stats = load_stats()
    now = datetime.now(timezone.utc)
    today = now.strftime('%Y-%m-%d')
    
    # Сброс если новый день
    if stats['date'] != today:
        stats = {
            'date': today,
            'count': 0,
            'history': []
        }
    
    # Увеличиваем счётчик
    stats['count'] += 1
    stats['history'].append({
        'timestamp': now.isoformat(),
        'count': stats['count']
    })
    
    # Оставляем только последние 200 записей
    stats['history'] = stats['history'][-200:]
    
    save_stats(stats)
    
    return stats['count']

def get_daily_stats():
    """Получить статистику за сегодня"""
    stats = load_stats()
    now = datetime.now(timezone.utc)
    today = now.strftime('%Y-%m-%d')
    
    if stats['date'] != today:
        return {
            'date': today,
            'count': 0,
            'limit': SAFE_REQUESTS_PER_DAY,
            'max_limit': MAX_REQUESTS_PER_DAY,
            'percentage': 0
        }
    
    return {
        'date': stats['date'],
        'count': stats['count'],
        'limit': SAFE_REQUESTS_PER_DAY,
        'max_limit': MAX_REQUESTS_PER_DAY,
        'percentage': round((stats['count'] / SAFE_REQUESTS_PER_DAY) * 100, 1)
    }

if __name__ == '__main__':
    # Тест
    can, reason, count, limit = can_make_request()
    print(f"Can request: {can}")
    print(f"Reason: {reason}")
    print(f"Count: {count}/{limit}")
    
    if can:
        new_count = record_request()
        print(f"Request recorded. New count: {new_count}")
    
    stats = get_daily_stats()
    print(f"\nDaily stats: {stats}")