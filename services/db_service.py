import os
import requests
import logging
from datetime import datetime, timezone # Добавили timezone для корректной работы с облаком

logger = logging.getLogger(__name__)

class DBService:
    def __init__(self):
        # Тянем ключи из .env
        self.url = os.getenv("SUPABASE_URL")
        self.key = os.getenv("SUPABASE_KEY")
        
        if not self.url or not self.key:
            logger.error("❌ API ключи Supabase не найдены в .env!")
        else:
            self.url = self.url.rstrip('/')
            # Настройка заголовков для Supabase REST API
            self.headers = {
                "apikey": self.key,
                "Authorization": f"Bearer {self.key}",
                "Content-Type": "application/json",
                # Prefer: resolution=merge-duplicates — это аналог upsert в REST API Supabase
                "Prefer": "return=minimal, resolution=merge-duplicates"
            }
            logger.info("✅ DB Service переключен на Supabase Cloud API")

    def save_user(self, data):
        """Сохраняет или обновляет данные пользователя в облаке (UPSERT)"""
        if not self.url: 
            logger.error("❌ Сохранение невозможно: SUPABASE_URL не настроен")
            return False
        
        user_id = data.get('id')
        if not user_id:
            logger.warning("⚠️ Попытка сохранить юзера без ID")
            return False

        # Готовим данные точно под твою структуру в SQL
        user_payload = {
            "id": user_id,
            "username": data.get('username'),
            "first_name": data.get('first_name'),
            "last_name": data.get('last_name'),
            "photo_url": data.get('photo_url'),
            "auth_date": data.get('auth_date'),
            "is_active": True
        }

        try:
            # Для работы upsert через POST в Supabase нужно передать заголовок resolution=merge-duplicates
            # и указать в URL, что мы работаем с таблицей users
            target_url = f"{self.url}/rest/v1/users"
            
            response = requests.post(target_url, json=user_payload, headers=self.headers)
            response.raise_for_status()
            
            logger.info(f"👤 Юзер {user_id} успешно синхронизирован с облаком (upsert)")
            return True
        except Exception as e:
            logger.error(f"❌ Ошибка сохранения в облако: {e}")
            return False

    def get_all_active_users(self):
        """Для рассылки сигналов всем активным юзерам из облака"""
        if not self.url: return []
        
        # Запрос к Supabase с фильтром по активности
        target_url = f"{self.url}/rest/v1/users?select=id&is_active=eq.true"
        try:
            # Для GET запроса Prefer: return=minimal не нужен, но заголовки авторизации обязательны
            response = requests.get(target_url, headers={"apikey": self.key, "Authorization": f"Bearer {self.key}"})
            response.raise_for_status()
            
            # Возвращаем список ID
            users = response.json()
            return [row['id'] for row in users]
        except Exception as e:
            logger.error(f"❌ Ошибка получения списка юзеров: {e}")
            return []

    # --- НОВЫЕ МЕТОДЫ ДЛЯ ПАМЯТИ БОТА ---

    def get_last_signal_time(self):
        """Получает время последнего сигнала из таблицы bot_metadata"""
        if not self.url: 
            return datetime(2020, 1, 1, tzinfo=timezone.utc)
        
        target_url = f"{self.url}/rest/v1/bot_metadata?select=value_timestamp&key=eq.last_signal_time"
        try:
            response = requests.get(target_url, headers={"apikey": self.key, "Authorization": f"Bearer {self.key}"})
            response.raise_for_status()
            data = response.json()
            if data and data[0].get('value_timestamp'):
                # Парсим время из формата базы в объект Python
                ts_str = data[0]['value_timestamp']
                return datetime.fromisoformat(ts_str.replace('Z', '+00:00'))
        except Exception as e:
            logger.error(f"❌ Ошибка получения кулдауна из Supabase: {e}")
        
        return datetime(2020, 1, 1, tzinfo=timezone.utc)

    def update_last_signal_time(self):
        """Обновляет метку времени последнего сигнала в облаке"""
        if not self.url: 
            return False
            
        now_iso = datetime.now(timezone.utc).isoformat()
        target_url = f"{self.url}/rest/v1/bot_metadata?key=eq.last_signal_time"
        
        try:
            # Используем PATCH для обновления конкретной строки
            response = requests.patch(
                target_url, 
                json={"value_timestamp": now_iso}, 
                headers={"apikey": self.key, "Authorization": f"Bearer {self.key}", "Content-Type": "application/json"}
            )
            response.raise_for_status()
            logger.info("🕒 Время последнего сигнала успешно обновлено в облаке")
            return True
        except Exception as e:
            logger.error(f"❌ Ошибка обновления кулдауна в Supabase: {e}")
            return False

# Создаем экземпляр сервиса
db_service = DBService()