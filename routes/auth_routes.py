from flask import Blueprint, request, jsonify
from services.auth_service import auth_service
from services.db_service import db_service
from services.telegram_service import telegram_service
import logging

logger = logging.getLogger(__name__)
auth_bp = Blueprint('auth', __name__)

@auth_bp.route('/telegram', methods=['POST'])
def telegram_auth():
    data = request.json
    if not data:
        return jsonify({"success": False, "error": "No data"}), 400

    if auth_service.verify_telegram_auth(data):
        user_id = data.get('id')
        user_name = f"{data.get('first_name', '')} {data.get('last_name', '')}".strip()
        
        try:
            # Сохраняем пользователя в БД
            db_service.save_user(data)
            logger.info(f"✅ Пользователь {user_name} (ID: {user_id}) авторизован через Telegram")
        except Exception as e:
            # Если база упадет, не ломаем авторизацию юзеру, просто логаем
            logger.error(f"❌ Database error при сохранении юзера: {e}")
        
        # Отправляем уведомление в Telegram с кнопкой подтверждения
        try:
            telegram_service.send_approval_notification(user_id, user_name)
            logger.info(f"📱 Уведомление о входе отправлено в Telegram (user_id={user_id})")
        except Exception as e:
            logger.error(f"❌ Ошибка отправки уведомления в Telegram: {e}")

        return jsonify({
            "success": True,
            "user": {
                "id": user_id,
                "name": user_name,
                "photo": data.get('photo_url'),
                "username": data.get('username')
            }
        })
    
    return jsonify({"success": False, "error": "Invalid signature"}), 401