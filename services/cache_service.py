"""
Централизованный сервис кэширования с поддержкой TTL (Time To Live)
"""
import hashlib
import json
import logging
import time
from typing import Any, Optional, Dict
from datetime import datetime, timedelta
from threading import Lock

logger = logging.getLogger(__name__)


class CacheEntry:
    """Запись в кэше с TTL"""
    
    def __init__(self, value: Any, ttl_seconds: Optional[int] = None):
        self.value = value
        self.created_at = time.time()
        self.ttl_seconds = ttl_seconds
        self.access_count = 0
    
    def is_expired(self) -> bool:
        """Проверка истечения срока действия"""
        if self.ttl_seconds is None:
            return False
        return (time.time() - self.created_at) > self.ttl_seconds
    
    def get_age(self) -> float:
        """Возвращает возраст записи в секундах"""
        return time.time() - self.created_at


class CacheService:
    """
    In-memory кэш с поддержкой TTL и автоматической очисткой
    """
    
    def __init__(self):
        self._cache: Dict[str, CacheEntry] = {}
        self._lock = Lock()
        self._stats = {
            'hits': 0,
            'misses': 0,
            'sets': 0,
            'evictions': 0
        }
    
    def _generate_key(self, prefix: str, *args, **kwargs) -> str:
        """
        Генерирует уникальный ключ на основе префикса и параметров
        
        Args:
            prefix: Префикс ключа (например, 'candles', 'news')
            *args: Позиционные аргументы
            **kwargs: Именованные аргументы
            
        Returns:
            Хеш-строка ключа
        """
        # Создаем строку из всех параметров
        key_parts = [prefix]
        
        # Добавляем позиционные аргументы
        for arg in args:
            if isinstance(arg, (dict, list)):
                key_parts.append(json.dumps(arg, sort_keys=True))
            else:
                key_parts.append(str(arg))
        
        # Добавляем именованные аргументы (отсортированные)
        for k in sorted(kwargs.keys()):
            v = kwargs[k]
            if isinstance(v, (dict, list)):
                key_parts.append(f"{k}={json.dumps(v, sort_keys=True)}")
            else:
                key_parts.append(f"{k}={v}")
        
        # Создаем хеш
        key_string = "|".join(key_parts)
        hash_key = hashlib.md5(key_string.encode()).hexdigest()
        
        return f"{prefix}:{hash_key}"
    
    def get(self, key: str) -> Optional[Any]:
        """
        Получение значения из кэша
        
        Args:
            key: Ключ записи
            
        Returns:
            Значение или None если не найдено/истекло
        """
        with self._lock:
            entry = self._cache.get(key)
            
            if entry is None:
                self._stats['misses'] += 1
                return None
            
            # Проверяем истечение
            if entry.is_expired():
                logger.debug(f"Cache expired: {key}")
                del self._cache[key]
                self._stats['misses'] += 1
                self._stats['evictions'] += 1
                return None
            
            # Обновляем статистику
            entry.access_count += 1
            self._stats['hits'] += 1
            
            logger.debug(f"Cache hit: {key} (age: {entry.get_age():.1f}s, accesses: {entry.access_count})")
            return entry.value
    
    def set(self, key: str, value: Any, ttl_seconds: Optional[int] = None) -> None:
        """
        Сохранение значения в кэш
        
        Args:
            key: Ключ записи
            value: Значение для сохранения
            ttl_seconds: Время жизни в секундах (None = бессрочно)
        """
        with self._lock:
            self._cache[key] = CacheEntry(value, ttl_seconds)
            self._stats['sets'] += 1
            
            logger.debug(f"Cache set: {key} (TTL: {ttl_seconds}s)" if ttl_seconds else f"Cache set: {key} (no TTL)")
    
    def delete(self, key: str) -> bool:
        """
        Удаление записи из кэша
        
        Args:
            key: Ключ записи
            
        Returns:
            True если запись была удалена
        """
        with self._lock:
            if key in self._cache:
                del self._cache[key]
                self._stats['evictions'] += 1
                logger.debug(f"Cache deleted: {key}")
                return True
            return False
    
    def clear(self, prefix: Optional[str] = None) -> int:
        """
        Очистка кэша
        
        Args:
            prefix: Если указан, удаляет только записи с этим префиксом
            
        Returns:
            Количество удаленных записей
        """
        with self._lock:
            if prefix is None:
                count = len(self._cache)
                self._cache.clear()
                logger.info(f"Cache cleared completely ({count} entries)")
                return count
            
            # Удаляем только записи с префиксом
            keys_to_delete = [k for k in self._cache.keys() if k.startswith(f"{prefix}:")]
            for key in keys_to_delete:
                del self._cache[key]
            
            logger.info(f"Cache cleared: {prefix} ({len(keys_to_delete)} entries)")
            return len(keys_to_delete)
    
    def cleanup_expired(self) -> int:
        """
        Удаление истекших записей
        
        Returns:
            Количество удаленных записей
        """
        with self._lock:
            expired_keys = [
                key for key, entry in self._cache.items()
                if entry.is_expired()
            ]
            
            for key in expired_keys:
                del self._cache[key]
                self._stats['evictions'] += 1
            
            if expired_keys:
                logger.info(f"Cleaned up {len(expired_keys)} expired cache entries")
            
            return len(expired_keys)
    
    def get_stats(self) -> Dict[str, Any]:
        """
        Получение статистики кэша
        
        Returns:
            Словарь со статистикой
        """
        with self._lock:
            total_requests = self._stats['hits'] + self._stats['misses']
            hit_rate = (self._stats['hits'] / total_requests * 100) if total_requests > 0 else 0
            
            return {
                'size': len(self._cache),
                'hits': self._stats['hits'],
                'misses': self._stats['misses'],
                'sets': self._stats['sets'],
                'evictions': self._stats['evictions'],
                'hit_rate': round(hit_rate, 2),
                'total_requests': total_requests
            }
    
    def get_info(self, prefix: Optional[str] = None) -> Dict[str, Any]:
        """
        Получение детальной информации о кэше
        
        Args:
            prefix: Фильтр по префиксу
            
        Returns:
            Информация о записях в кэше
        """
        with self._lock:
            entries = []
            
            for key, entry in self._cache.items():
                if prefix and not key.startswith(f"{prefix}:"):
                    continue
                
                entries.append({
                    'key': key,
                    'age': round(entry.get_age(), 2),
                    'ttl': entry.ttl_seconds,
                    'expired': entry.is_expired(),
                    'access_count': entry.access_count
                })
            
            # Сортируем по возрасту (старые первыми)
            entries.sort(key=lambda x: x['age'], reverse=True)
            
            return {
                'total_entries': len(entries),
                'entries': entries,
                'stats': self.get_stats()
            }


# Глобальный экземпляр кэша
cache_service = CacheService()
