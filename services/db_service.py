import os
import math
import requests
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional
from urllib.parse import quote

logger = logging.getLogger(__name__)


def safe_float(value, default=0.0):
    """Безопасное преобразование в float с проверкой на NaN/Inf"""
    try:
        result = float(value) if value is not None else default
        if math.isnan(result) or math.isinf(result):
            return default
        return result
    except (TypeError, ValueError):
        return default


def safe_int(value, default=0):
    """Безопасное преобразование в int"""
    try:
        return int(value) if value is not None else default
    except (TypeError, ValueError):
        return default

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

    def save_user(self, data, partial_update=False):
        """
        Сохраняет или обновляет данные пользователя в облаке (UPSERT)
        
        Args:
            data: Словарь с данными пользователя
            partial_update: Если True, обновляет только переданные поля (не затирает существующие)
        """
        if not self.url: 
            logger.error("❌ Сохранение невозможно: SUPABASE_URL не настроен")
            return False
        
        user_id = data.get('id')
        if not user_id:
            logger.warning("⚠️ Попытка сохранить юзера без ID")
            return False

        try:
            if partial_update:
                # PARTIAL UPDATE: Обновляем только is_active, не трогая остальные поля
                # Используем PATCH для обновления существующей записи
                target_url = f"{self.url}/rest/v1/users?id=eq.{user_id}"
                
                # Формируем payload только с переданными полями
                update_payload = {}
                if 'is_active' in data:
                    update_payload['is_active'] = data['is_active']
                if 'username' in data and data.get('username'):
                    update_payload['username'] = data['username']
                if 'first_name' in data and data.get('first_name'):
                    update_payload['first_name'] = data['first_name']
                if 'last_name' in data and data.get('last_name'):
                    update_payload['last_name'] = data['last_name']
                
                # Отправляем PATCH запрос
                response = requests.patch(
                    target_url,
                    json=update_payload,
                    headers={
                        "apikey": self.key,
                        "Authorization": f"Bearer {self.key}",
                        "Content-Type": "application/json"
                    }
                )
                response.raise_for_status()
                logger.info(f"👤 Юзер {user_id} обновлен (partial update)")
                return True
            else:
                # FULL UPSERT: Создаем новую запись или обновляем все поля
                user_payload = {
                    "id": user_id,
                    "username": data.get('username'),
                    "first_name": data.get('first_name'),
                    "last_name": data.get('last_name'),
                    "photo_url": data.get('photo_url'),
                    "auth_date": data.get('auth_date'),
                    "is_active": True
                }
                
                target_url = f"{self.url}/rest/v1/users"
                response = requests.post(target_url, json=user_payload, headers=self.headers)
                response.raise_for_status()
                
                logger.info(f"👤 Юзер {user_id} успешно синхронизирован с облаком (full upsert)")
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

    def get_last_wait_time(self):
        """Получает время последнего вердикта WAIT из Supabase"""
        if not self.url: return datetime(2020, 1, 1, tzinfo=timezone.utc)
        target_url = f"{self.url}/rest/v1/bot_metadata?select=value_timestamp&key=eq.last_wait_time"
        try:
            response = requests.get(target_url, headers={"apikey": self.key, "Authorization": f"Bearer {self.key}"})
            response.raise_for_status()
            data = response.json()
            if data and data[0].get('value_timestamp'):
                return datetime.fromisoformat(data[0]['value_timestamp'].replace('Z', '+00:00'))
        except: pass
        return datetime(2020, 1, 1, tzinfo=timezone.utc)

    def update_last_wait_time(self):
        """Записывает время вердикта WAIT в облако"""
        if not self.url: return False
        now_iso = datetime.now(timezone.utc).isoformat()
        target_url = f"{self.url}/rest/v1/bot_metadata?key=eq.last_wait_time"
        try:
            requests.patch(target_url, json={"value_timestamp": now_iso}, headers=self.headers)
            logger.info("⚖️ Кулдаун WAIT обновлен в облаке")
            return True
        except: return False

    def get_website_authorized_users(self):
        """Берет ID только тех, кто реально авторизовался через виджет на сайте (есть photo_url)"""
        if not self.url: return []
        # photo_url=not.is.null — это признак того, что юзер прошел через сайт
        target_url = f"{self.url}/rest/v1/users?select=id&is_active=eq.true&photo_url=not.is.null"
        try:
            response = requests.get(target_url, headers={"apikey": self.key, "Authorization": f"Bearer {self.key}"})
            response.raise_for_status()
            return [row['id'] for row in response.json()]
        except: return []

    # --- МЕТОДЫ ДЛЯ AUTH SESSIONS (ВХОД ЧЕРЕЗ БОТА) ---

    def create_auth_session(self, token):
        """
        Создает новую сессию авторизации для входа через бота
        """
        if not self.url:
            logger.error("❌ Создание auth_session невозможно: SUPABASE_URL не настроен")
            return False
        
        target_url = f"{self.url}/rest/v1/auth_sessions"
        
        session_data = {
            "token": token,
            "status": "pending",
            "tg_user_id": None,
            "created_at": datetime.now(timezone.utc).isoformat()
        }
        
        try:
            response = requests.post(
                target_url,
                json=session_data,
                headers=self.headers
            )
            response.raise_for_status()
            logger.info(f"✅ Auth session создан: {token[:8]}...")
            return True
        except Exception as e:
            logger.error(f"❌ Ошибка создания auth_session: {e}")
            return False

    def get_auth_session(self, token):
        """
        Получает статус сессии авторизации
        """
        if not self.url:
            return None
        
        target_url = f"{self.url}/rest/v1/auth_sessions?token=eq.{token}&select=*"
        
        try:
            response = requests.get(
                target_url,
                headers={"apikey": self.key, "Authorization": f"Bearer {self.key}"}
            )
            response.raise_for_status()
            data = response.json()
            return data[0] if data else None
        except Exception as e:
            logger.error(f"❌ Ошибка получения auth_session: {e}")
            return None

    def complete_auth_session(self, token, tg_user_id):
        """
        Обновляет сессию авторизации как завершенную
        """
        if not self.url:
            return False
        
        target_url = f"{self.url}/rest/v1/auth_sessions?token=eq.{token}"
        
        try:
            response = requests.patch(
                target_url,
                json={
                    "status": "completed",
                    "tg_user_id": tg_user_id,
                    "completed_at": datetime.now(timezone.utc).isoformat()
                },
                headers={
                    "apikey": self.key,
                    "Authorization": f"Bearer {self.key}",
                    "Content-Type": "application/json"
                }
            )
            response.raise_for_status()
            logger.info(f"✅ Auth session завершен: {token[:8]}... → user {tg_user_id}")
            return True
        except Exception as e:
            logger.error(f"❌ Ошибка завершения auth_session: {e}")
            return False

    def get_user_by_id(self, user_id):
        """
        Получает данные пользователя по ID
        """
        if not self.url:
            return None
        
        target_url = f"{self.url}/rest/v1/users?id=eq.{user_id}&select=*"
        
        try:
            response = requests.get(
                target_url,
                headers={"apikey": self.key, "Authorization": f"Bearer {self.key}"}
            )
            response.raise_for_status()
            data = response.json()
            return data[0] if data else None
        except Exception as e:
            logger.error(f"❌ Ошибка получения пользователя: {e}")
            return None

    # --- МЕТОДЫ ДЛЯ РАБОТЫ С СИГНАЛАМИ ---

    def save_signal(self, signal_data):
        """
        Сохраняет сигнал в таблицу signals
        
        КРИТИЧЕСКИЕ ТРЕБОВАНИЯ SUPABASE:
        - Заголовки: apikey, Authorization (Bearer), Content-Type, Prefer
        - Типизация: float для цен, int для confidence
        - Формат даты: ISO 8601 с timezone
        """
        if not self.url or not self.key:
            logger.error("❌ Сохранение сигнала невозможно: SUPABASE_URL или SUPABASE_KEY не настроен")
            return None
        
        target_url = f"{self.url}/rest/v1/signals"
        
        # ============================================================
        # ТИПИЗАЦИЯ ДАННЫХ (защита от ошибок 400)
        # Соответствие РЕАЛЬНОЙ схеме таблицы signals в Supabase:
        # - signal_label НЕТ (вычисляется в VIEW latest_signals)
        # - internal_trend НЕТ  
        # - patterns = text[] (массив строк!)
        # ============================================================
        
        # patterns — колонка _text (text[]) в БД: передаём массив строк, не одну строку
        patterns_raw = signal_data.get('patterns', [])
        if isinstance(patterns_raw, list):
            patterns_list = [str(p) for p in patterns_raw] if patterns_raw else []
        else:
            patterns_list = [str(patterns_raw)] if patterns_raw else []
        
        sanitized_data = {
            'symbol': str(signal_data.get('symbol', 'XAU_USD')),
            'signal_type': str(signal_data.get('signal_type', 'WAIT')),
            # signal_label УБРАН — вычисляется в VIEW
            'status': str(signal_data.get('status', 'active')),
            
            # Цены — СТРОГО float, проверка на NaN
            'entry_price': safe_float(signal_data.get('entry_price'), 0.0),
            'current_price': safe_float(signal_data.get('current_price'), 0.0),
            'stop_loss': safe_float(signal_data.get('stop_loss'), 0.0),
            'take_profit': safe_float(signal_data.get('take_profit'), 0.0),
            # Double TP (Fix P2): первый уровень для 50% позиции; если в таблице нет колонки take_profit_1 — добавить её или убрать эту строку
            'take_profit_1': safe_float(signal_data.get('take_profit_1'), 0.0),
            
            # Тренд и зона — строки (internal_trend УБРАН — нет в таблице)
            'trend': str(signal_data.get('trend', 'NEUTRAL')),
            'zone': str(signal_data.get('zone', 'UNKNOWN')),
            
            # Паттерны — массив строк (text[] в БД)
            'patterns': patterns_list,
            
            # SMC Summary — jsonb
            'smc_summary': dict(signal_data.get('smc_summary', {})) if isinstance(signal_data.get('smc_summary'), dict) else {},
            
            # LLM данные (увеличены лимиты)
            'llm_full_response': str(signal_data.get('llm_full_response', ''))[:4000],
            'llm_reason': str(signal_data.get('llm_reason', ''))[:1000],
            'llm_confidence': safe_int(signal_data.get('llm_confidence'), 0),
            
            # Timestamp — ISO 8601 формат с timezone
            'timestamp': datetime.now(timezone.utc).isoformat()
        }
        
        # ============================================================
        # ЗАГОЛОВКИ (КРИТИЧНО для Supabase!)
        # ============================================================
        headers = {
            "apikey": self.key,
            "Authorization": f"Bearer {self.key}",
            "Content-Type": "application/json",
            "Prefer": "return=representation"  # Гарантирует ответ от API
        }
        
        try:
            logger.debug(f"📤 Отправка сигнала в Supabase: {sanitized_data.get('signal_type')} @ {sanitized_data.get('current_price')}")
            
            response = requests.post(
                target_url,
                json=sanitized_data,
                headers=headers,
                timeout=10
            )
            
            # Детальная диагностика ошибок
            if response.status_code >= 400:
                logger.error(f"❌ Supabase Error {response.status_code}: {response.text}")
                response.raise_for_status()
            
            result = response.json()
            signal_id = result[0]['id'] if result else None
            
            logger.info(f"✅ Сигнал {sanitized_data['signal_type']} @ {sanitized_data['current_price']:.2f} сохранен (ID: {signal_id})")
            return signal_id
            
        except requests.exceptions.HTTPError as e:
            logger.error(f"❌ HTTP Error при сохранении сигнала: {e}")
            logger.error(f"   Response: {e.response.text if e.response else 'No response'}")
            return None
        except Exception as e:
            logger.error(f"❌ Ошибка сохранения сигнала: {e}")
            return None

    def get_last_signal(self, signal_type=None):
        """
        Получает последний сигнал из БД
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

    def get_last_trade_signal(self, symbol: str = None):
        """
        Получает последний ТОРГОВЫЙ сигнал (BUY/SELL), включая все поля.
        Используется для логики активной сделки (Manager / Hunter guard).
        """
        if not self.url:
            return None
        
        # Фильтруем только BUY и SELL, исключая WAIT
        base_url = f"{self.url}/rest/v1/signals?select=*&signal_type=in.(BUY,SELL)&order=timestamp.desc&limit=1"
        if symbol:
            target_url = base_url + f"&symbol=eq.{symbol}"
        else:
            target_url = base_url
        
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
            logger.error(f"❌ Ошибка получения последнего торгового сигнала (полные данные): {e}")
            return None

    def get_signals_history(self, limit=10, signal_type=None):
        """
        Получает историю сигналов
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

    def update_signal_sl_and_status(self, signal_id, new_sl_price, status: str = None):
        """
        Обновляет Stop Loss и, опционально, статус сигнала.
        Используется Manager-агентом (перевод в безубыток, пометка как BE_SET и т.п.).
        """
        if not self.url:
            return False
        
        target_url = f"{self.url}/rest/v1/signals?id=eq.{signal_id}"
        
        payload = {
            'stop_loss': new_sl_price,
            'updated_at': datetime.now(timezone.utc).isoformat()
        }
        if status:
            payload['status'] = status
        
        try:
            response = requests.patch(
                target_url,
                json=payload,
                headers={
                    "apikey": self.key,
                    "Authorization": f"Bearer {self.key}",
                    "Content-Type": "application/json"
                }
            )
            response.raise_for_status()
            logger.info(f"✅ SL сигнала {signal_id} обновлён до {new_sl_price} (status={status})")
            return True
        except Exception as e:
            logger.error(f"❌ Ошибка обновления SL сигнала {signal_id}: {e}")
            return False

    def get_signal_entry_notified(self, signal_id: int) -> bool:
        """
        Возвращает True, если по сигналу уже было отправлено уведомление «Вход достигнут».
        Используется Manager-агентом, чтобы не дублировать сообщение после рестарта процесса.
        """
        if not self.url or not signal_id:
            return False
        target_url = f"{self.url}/rest/v1/signals?select=entry_notified&id=eq.{signal_id}&limit=1"
        try:
            response = requests.get(
                target_url,
                headers={"apikey": self.key, "Authorization": f"Bearer {self.key}"},
            )
            response.raise_for_status()
            data = response.json()
            if data and len(data) > 0:
                return bool(data[0].get("entry_notified", False))
            return False
        except Exception as e:
            logger.warning(f"⚠️ get_signal_entry_notified({signal_id}): {e}")
            return False

    def mark_entry_notified(self, signal_id: int) -> bool:
        """
        Помечает сигнал как уже уведомлённый по входу (entry_notified = true).
        Используется Manager-агентом после отправки «Вход достигнут», чтобы не слать дубль.
        """
        if not self.url or not signal_id:
            return False
        target_url = f"{self.url}/rest/v1/signals?id=eq.{signal_id}"
        payload = {
            "entry_notified": True,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        try:
            response = requests.patch(
                target_url,
                json=payload,
                headers={
                    "apikey": self.key,
                    "Authorization": f"Bearer {self.key}",
                    "Content-Type": "application/json",
                },
            )
            response.raise_for_status()
            logger.info(f"✅ Сигнал {signal_id} помечен entry_notified=True")
            return True
        except Exception as e:
            logger.error(f"❌ Ошибка обновления entry_notified для сигнала {signal_id}: {e}")
            return False

    # ----------------------------------------------------------------------
    # Методы для работы с локальными диапазонами (local_ranges)
    # ----------------------------------------------------------------------

    def get_active_range(self, symbol: str = 'XAUUSD', timeframe: str = 'M15'):
        """
        Возвращает АКТИВНЫЙ локальный диапазон для инструмента/таймфрейма
        (is_active = true) или None, если диапазона нет.
        """
        if not self.url:
            logger.warning("⚠️ get_active_range: SUPABASE_URL не настроен")
            return None

        target_url = (
            f"{self.url}/rest/v1/local_ranges"
            f"?symbol=eq.{symbol}&timeframe=eq.{timeframe}"
            f"&is_active=eq.true&select=*"
            f"&order=created_at.desc&limit=1"
        )
        try:
            response = requests.get(
                target_url,
                headers={"apikey": self.key, "Authorization": f"Bearer {self.key}"}
            )
            response.raise_for_status()
            rows = response.json()
            if rows:
                return rows[0]
            return None
        except Exception as e:
            logger.error(f"❌ get_active_range error: {e}")
            return None

    def save_range(self, range_high: float, range_low: float,
                   symbol: str = 'XAUUSD', timeframe: str = 'M15'):
        """
        Деактивирует все старые диапазоны для инструмента/таймфрейма
        и создаёт новый активный диапазон.
        """
        if not self.url:
            logger.warning("⚠️ save_range: SUPABASE_URL не настроен")
            return None

        # 1) Деактивируем старые диапазоны
        try:
            deactivate_url = (
                f"{self.url}/rest/v1/local_ranges"
                f"?symbol=eq.{symbol}&timeframe=eq.{timeframe}"
                f"&is_active=eq.true"
            )
            deactivate_payload = {
                "is_active": False,
                "death_reason": "replaced",
                "updated_at": datetime.now(timezone.utc).isoformat()
            }
            requests.patch(
                deactivate_url,
                json=deactivate_payload,
                headers={
                    "apikey": self.key,
                    "Authorization": f"Bearer {self.key}",
                    "Content-Type": "application/json"
                }
            )
        except Exception as e:
            logger.error(f"❌ save_range: ошибка деактивации старых диапазонов: {e}")

        # 2) Создаём новый диапазон (candles_inside=0 — Проблема 3; в Supabase: ALTER TABLE local_ranges ADD COLUMN IF NOT EXISTS candles_inside INTEGER DEFAULT 0;)
        range_size = (range_high - range_low) if range_high is not None and range_low is not None else None
        payload = {
            "symbol": symbol,
            "timeframe": timeframe,
            "range_high": range_high,
            "range_low": range_low,
            "range_size": range_size,
            "is_active": True,
            "candles_inside": 0,
        }

        try:
            target_url = f"{self.url}/rest/v1/local_ranges"
            # Для получения id новой записи нам нужна полная репрезентация
            headers = {
                "apikey": self.key,
                "Authorization": f"Bearer {self.key}",
                "Content-Type": "application/json",
                "Prefer": "return=representation"
            }
            response = requests.post(target_url, json=payload, headers=headers)
            response.raise_for_status()
            rows = response.json()
            new_range = rows[0] if isinstance(rows, list) and rows else rows
            logger.info(
                f"📐 Новый локальный диапазон сохранён в Supabase: {symbol} {timeframe} "
                f"[{range_low} - {range_high}] size={range_size}"
            )
            return new_range
        except Exception as e:
            logger.error(f"❌ save_range: ошибка создания диапазона: {e}")
            return None

    def update_range_touch(self, range_id: int, new_candles_inside: Optional[int] = None):
        """
        Обновляет last_touch_at для диапазона.
        Если передан new_candles_inside (например current+1), обновляет и candles_inside (колонка в Supabase).
        """
        if not self.url or not range_id:
            return False
        target_url = f"{self.url}/rest/v1/local_ranges?id=eq.{range_id}"
        payload = {"last_touch_at": datetime.now(timezone.utc).isoformat()}
        if new_candles_inside is not None:
            payload["candles_inside"] = new_candles_inside
        try:
            response = requests.patch(
                target_url,
                json=payload,
                headers={
                    "apikey": self.key,
                    "Authorization": f"Bearer {self.key}",
                    "Content-Type": "application/json"
                }
            )
            response.raise_for_status()
            return True
        except Exception as e:
            logger.error(f"❌ update_range_touch error (id={range_id}): {e}")
            return False

    def deactivate_range(self, range_id: int, death_reason: str):
        """Деактивирует диапазон (is_active = false) с указанием причины."""
        if not self.url or not range_id:
            return False
        target_url = f"{self.url}/rest/v1/local_ranges?id=eq.{range_id}"
        payload = {
            "is_active": False,
            "death_reason": death_reason,
            "updated_at": datetime.now(timezone.utc).isoformat()
        }
        try:
            response = requests.patch(
                target_url,
                json=payload,
                headers={
                    "apikey": self.key,
                    "Authorization": f"Bearer {self.key}",
                    "Content-Type": "application/json"
                }
            )
            response.raise_for_status()
            logger.info(f"📐 Диапазон {range_id} деактивирован (reason={death_reason})")
            return True
        except Exception as e:
            logger.error(f"❌ deactivate_range error (id={range_id}): {e}")
            return False

    def get_recent_inactive_ranges(
        self, symbol: str = 'XAUUSD', timeframe: str = 'M15', hours: int = 72
    ) -> list:
        """
        Возвращает деактивированные диапазоны за последние N часов.
        Исключает: death_reason='expired_24h';
        исключает death_reason='replaced_by_breakout' если старше 24 часов.
        Воскрешать можно только: price_too_far, new_range_formed,
        или replaced_by_breakout если updated_at в пределах 24h.
        Сортировка: сначала свежие (updated_at DESC). Лимит: 10 записей.
        """
        if not self.url:
            return []
        now = datetime.now(timezone.utc)
        from_dt = now - timedelta(hours=hours)
        from_iso = from_dt.isoformat()
        # Кодируем дату для URL: + в timezone иначе интерпретируется как пробел → 400 Bad Request
        from_iso_encoded = quote(from_iso, safe=".")
        cutoff_24h = (now - timedelta(hours=24)).isoformat()

        target_url = (
            f"{self.url}/rest/v1/local_ranges"
            f"?symbol=eq.{symbol}&timeframe=eq.{timeframe}"
            f"&is_active=eq.false"
            f"&updated_at=gte.{from_iso_encoded}"
            f"&select=*"
            f"&order=updated_at.desc"
            f"&limit=10"
        )
        try:
            response = requests.get(
                target_url,
                headers={"apikey": self.key, "Authorization": f"Bearer {self.key}"}
            )
            response.raise_for_status()
            rows = response.json() or []
        except Exception as e:
            logger.warning(f"⚠️ get_recent_inactive_ranges error: {e}")
            return []

        out = []
        for r in rows:
            reason = r.get('death_reason')
            if reason == 'expired_24h':
                continue
            if reason == 'replaced_by_breakout':
                updated = r.get('updated_at')
                if updated and str(updated) < cutoff_24h:
                    continue
            out.append(r)
        return out

    def reactivate_range(self, range_id: int):
        """
        Переактивирует старый диапазон:
        is_active=True, death_reason=null, last_touch_at=now, updated_at=now.
        """
        if not self.url or not range_id:
            return False
        now = datetime.now(timezone.utc).isoformat()
        target_url = f"{self.url}/rest/v1/local_ranges?id=eq.{range_id}"
        payload = {
            "is_active": True,
            "death_reason": None,
            "last_touch_at": now,
            "updated_at": now
        }
        try:
            response = requests.patch(
                target_url,
                json=payload,
                headers={
                    "apikey": self.key,
                    "Authorization": f"Bearer {self.key}",
                    "Content-Type": "application/json"
                }
            )
            response.raise_for_status()
            logger.info(f"📐 Диапазон {range_id} реактивирован (воскрешён)")
            return True
        except Exception as e:
            logger.error(f"❌ reactivate_range error (id={range_id}): {e}")
            return False

# Создаем экземпляр сервиса
db_service = DBService()