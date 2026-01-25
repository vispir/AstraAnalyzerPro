import os
import requests
import logging

logger = logging.getLogger(__name__)

class TelegramService:
    def __init__(self):
        self.bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
        self.api_url = f"https://api.telegram.org/bot{self.bot_token}"

    def get_main_keyboard(self):
        """Стандартные кнопки (внизу)"""
        return {
            "keyboard": [
                [{"text": "📊 Курс Gold"}, {"text": "📈 Тренд M15"}],
                [{"text": "🛡️ Статус системы"}, {"text": "🔔 Последний сигнал"}]
            ],
            "resize_keyboard": True
        }

    def get_inline_menu(self):
        """Красивое меню под сообщением (как на скрине)"""
        return {
            "inline_keyboard": [
                [{"text": "🌐 Открыть Терминал", "url": "http://127.0.0.1"}], 
                [
                    {"text": "💰 Курс", "callback_data": "price"},
                    {"text": "📈 Анализ", "callback_data": "trend"}
                ],
                [{"text": "🛡️ Статус", "callback_data": "status"}, {"text": "🔔 Сигнал", "callback_data": "last"}]
            ]
        }

    def send_message(self, chat_id, text, reply_markup=None):
        """Отправка сообщения. Если reply_markup не передан, кнопки не добавляются."""
        if not self.bot_token: return False
        url = f"{self.api_url}/sendMessage"
        
        payload = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": False
        }
        
        # Если кнопки переданы - добавляем их в запрос
        if reply_markup:
            payload["reply_markup"] = reply_markup
            
        try:
            response = requests.post(url, json=payload, timeout=10)
            return response.status_code == 200
        except Exception as e:
            logger.error(f"❌ Ошибка отправки в TG: {e}")
            return False

    def broadcast_signal(self, user_ids, message):
        """Массовая рассылка по списку ID"""
        success_count = 0
        # Для сигналов по умолчанию не шлем старую клавиатуру
        for user_id in user_ids:
            if self.send_message(user_id, message):
                success_count += 1
        return success_count

    def get_remove_keyboard(self):
        """Метод для удаления обычных кнопок внизу экрана"""
        return {"remove_keyboard": True}

telegram_service = TelegramService()