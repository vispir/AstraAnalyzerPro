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

    def deactivate_user(self, user_id):
        """Деактивирует пользователя (когда он блокирует бота)"""
        if not self.url:
            logger.warning("⚠️ Деактивация невозможна: SUPABASE_URL не настроен")
            return False
        
        try:
            target_url = f"{self.url}/rest/v1/users?id=eq.{user_id}"
            payload = {"is_active": False}
            
            response = requests.patch(target_url, json=payload, headers=self.headers)
            response.raise_for_status()
            
            logger.info(f"🚫 Пользователь {user_id} деактивирован")
            return True
        except Exception as e:
            logger.error(f"❌ Ошибка деактивации пользователя {user_id}: {e}")
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

    # --- МЕТОДЫ ДЛЯ РАБОТЫ С СИГНАЛАМИ ---

    def save_signal(self, signal_data):
        """
        Сохраняет сигнал в таблицу signals
        
        Args:
            signal_data: Dict с данными сигнала
                {
                    'symbol': 'XAU_USD',
                    'signal_type': 'BUY', 'SELL' или 'WAIT',
                    'entry_price': 2650.00,
                    'stop_loss': 2645.00,
                    'take_profit': 2660.00,
                    'trend': 'UPTREND',
                    'zone': 'DISCOUNT',
                    'current_price': 2649.50,
                    'patterns': ['BOS', 'CHOCH', 'OB_RETEST'],
                    'near_structures': 'BULL_OB [2649-2650], PDL @ 2648.50',
                    'smc_summary': {...},
                    'llm_reason': 'Бычий Order Block в зоне Discount...',
                    'llm_confidence': 85,
                    'llm_full_response': 'полный ответ LLM'
                }
        
        Returns:
            int: ID созданного сигнала или None при ошибке
        """
        if not self.url:
            logger.error("❌ Сохранение сигнала невозможно: SUPABASE_URL не настроен")
            return None
        
        target_url = f"{self.url}/rest/v1/signals"
        
        try:
            response = requests.post(
                target_url,
                json=signal_data,
                headers={
                    "apikey": self.key,
                    "Authorization": f"Bearer {self.key}",
                    "Content-Type": "application/json",
                    "Prefer": "return=representation"  # Возвращает созданную запись
                }
            )
            response.raise_for_status()
            result = response.json()
            signal_id = result[0]['id'] if result else None
            
            signal_type = signal_data.get('signal_type', 'N/A')
            price = signal_data.get('entry_price') or signal_data.get('current_price', 0)
            logger.info(f"✅ Сигнал {signal_type} @ {price} сохранен в БД (ID: {signal_id})")
            return signal_id
        except Exception as e:
            logger.error(f"❌ Ошибка сохранения сигнала: {e}")
            return None

    def get_last_signal(self, signal_type=None):
        """
        Получает последний сигнал из БД
        
        Args:
            signal_type: Фильтр по типу ('BUY', 'SELL', 'WAIT') или None для любого типа
        
        Returns:
            Dict с данными сигнала или None
        """
        if not self.url:
            return None
        
        # Формируем URL с фильтром если нужно
        if signal_type:
            target_url = f"{self.url}/rest/v1/signals?select=*&signal_type=eq.{signal_type}&order=timestamp.desc&limit=1"
        else:
            target_url = f"{self.url}/rest/v1/signals?select=*&order=timestamp.desc&limit=1"
        
        try:
            response = requests.get(
                target_url,
                headers={
                    "apikey": self.key,
                    "Authorization": f"Bearer {self.key}"
                }
            )
            response.raise_for_status()
            data = response.json()
            return data[0] if data else None
        except Exception as e:
            logger.error(f"❌ Ошибка получения последнего сигнала: {e}")
            return None

    def get_last_trade_signal_time(self):
        """
        Получает время последнего ТОРГОВОГО сигнала (только BUY/SELL, исключая WAIT)
        Используется для кулдауна
        
        Returns:
            datetime: Время последнего BUY/SELL сигнала
        """
        if not self.url:
            return datetime(2020, 1, 1, tzinfo=timezone.utc)
        
        # Фильтруем только BUY и SELL, исключая WAIT
        target_url = f"{self.url}/rest/v1/signals?select=timestamp&signal_type=in.(BUY,SELL)&order=timestamp.desc&limit=1"
        
        try:
            response = requests.get(
                target_url,
                headers={
                    "apikey": self.key,
                    "Authorization": f"Bearer {self.key}"
                }
            )
            response.raise_for_status()
            data = response.json()
            
            if data and data[0].get('timestamp'):
                ts_str = data[0]['timestamp']
                return datetime.fromisoformat(ts_str.replace('Z', '+00:00'))
        except Exception as e:
            logger.error(f"❌ Ошибка получения времени последнего торгового сигнала: {e}")
        
        return datetime(2020, 1, 1, tzinfo=timezone.utc)

    def get_signals_history(self, limit=10, signal_type=None):
        """
        Получает историю сигналов
        
        Args:
            limit: Количество сигналов (по умолчанию 10)
            signal_type: Фильтр по типу ('BUY', 'SELL', 'WAIT') или None
        
        Returns:
            List[Dict]: Список сигналов
        """
        if not self.url:
            return []
        
        if signal_type:
            target_url = f"{self.url}/rest/v1/signals?select=*&signal_type=eq.{signal_type}&order=timestamp.desc&limit={limit}"
        else:
            target_url = f"{self.url}/rest/v1/signals?select=*&order=timestamp.desc&limit={limit}"
        
        try:
            response = requests.get(
                target_url,
                headers={
                    "apikey": self.key,
                    "Authorization": f"Bearer {self.key}"
                }
            )
            response.raise_for_status()
            return response.json()
        except Exception as e:
            logger.error(f"❌ Ошибка получения истории сигналов: {e}")
            return []

    def get_signals_stats(self):
        """
        Получает статистику по сигналам через представление signals_stats
        
        Returns:
            Dict: Статистика сигналов
        """
        if not self.url:
            return {}
        
        target_url = f"{self.url}/rest/v1/signals_stats?select=*"
        
        try:
            response = requests.get(
                target_url,
                headers={
                    "apikey": self.key,
                    "Authorization": f"Bearer {self.key}"
                }
            )
            response.raise_for_status()
            data = response.json()
            return data[0] if data else {}
        except Exception as e:
            logger.error(f"❌ Ошибка получения статистики сигналов: {e}")
            return {}

    def update_signal_result(self, signal_id, result_pnl, close_price, status='closed'):
        """
        Обновляет результат сигнала (для будущего использования)
        
        Args:
            signal_id: ID сигнала
            result_pnl: Результат в пунктах
            close_price: Цена закрытия
            status: Статус ('closed', 'cancelled')
        
        Returns:
            bool: Успешность операции
        """
        if not self.url:
            return False
        
        target_url = f"{self.url}/rest/v1/signals?id=eq.{signal_id}"
        
        try:
            response = requests.patch(
                target_url,
                json={
                    'result_pnl': result_pnl,
                    'close_price': close_price,
                    'close_timestamp': datetime.now(timezone.utc).isoformat(),
                    'status': status
                },
                headers={
                    "apikey": self.key,
                    "Authorization": f"Bearer {self.key}",
                    "Content-Type": "application/json"
                }
            )
            response.raise_for_status()
            logger.info(f"✅ Результат сигнала {signal_id} обновлен: {result_pnl} pips")
            return True
        except Exception as e:
            logger.error(f"❌ Ошибка обновления результата сигнала: {e}")
            return False

# Создаем экземпляр сервиса
db_service = DBService()