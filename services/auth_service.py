import hashlib
import hmac
import time
import os

class AuthService:
    def __init__(self):
        # Берем токен из переменных окружения
        self.bot_token = os.getenv('TELEGRAM_BOT_TOKEN')

    def verify_telegram_auth(self, auth_data):
        check_hash = auth_data.get('hash')
        if not check_hash or not self.bot_token:
            return False

        # Собираем строку для проверки (все поля кроме hash в алфавитном порядке)
        data_check_list = []
        for key, value in sorted(auth_data.items()):
            if key != 'hash' and value is not None:
                data_check_list.append(f"{key}={value}")
        
        data_check_string = "\n".join(data_check_list)

        # Вычисляем секретный ключ
        secret_key = hashlib.sha256(self.bot_token.encode()).digest()
        
        # Считаем HMAC-SHA256
        hmac_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()

        # Сверяем хеши
        return hmac_hash == check_hash

auth_service = AuthService()