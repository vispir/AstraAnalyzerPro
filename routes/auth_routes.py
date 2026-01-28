from flask import Blueprint, request, jsonify
from services.auth_service import auth_service
from services.db_service import db_service
from services.telegram_service import telegram_service
import logging
import uuid

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

@auth_bp.route('/gen-token', methods=['GET'])
def generate_auth_token():
    """
    Генерирует токен для авторизации через бота
    Возвращает ссылку t.me/bot?start=TOKEN
    """
    try:
        # Генерируем уникальный токен
        token = str(uuid.uuid4())
        
        # Сохраняем в БД
        if db_service.create_auth_session(token):
            bot_username = "AstraAnalyzerPro_bot"
            auth_link = f"https://t.me/{bot_username}?start={token}"
            
            logger.info(f"🔑 Сгенерирован токен авторизации: {token[:8]}...")
            
            return jsonify({
                "success": True,
                "token": token,
                "link": auth_link
            })
        else:
            return jsonify({"success": False, "error": "Database error"}), 500
            
    except Exception as e:
        logger.error(f"❌ Ошибка генерации токена: {e}")
        return jsonify({"success": False, "error": str(e)}), 500

@auth_bp.route('/check-session/<token>', methods=['GET'])
def check_auth_session(token):
    """
    Проверяет статус сессии авторизации
    Фронтенд опрашивает этот эндпоинт для проверки завершения авторизации
    """
    try:
        session = db_service.get_auth_session(token)
        
        if not session:
            return jsonify({"success": False, "error": "Session not found"}), 404
        
        if session['status'] == 'completed' and session['tg_user_id']:
            # Получаем данные пользователя
            user = db_service.get_user_by_id(session['tg_user_id'])
            
            if user:
                user_name = f"{user.get('first_name', '')} {user.get('last_name', '')}".strip()
                
                return jsonify({
                    "success": True,
                    "status": "completed",
                    "user": {
                        "id": user['id'],
                        "name": user_name,
                        "photo": user.get('photo_url'),
                        "username": user.get('username')
                    }
                })
            else:
                return jsonify({"success": False, "error": "User not found"}), 404
        else:
            # Сессия еще в статусе pending
            return jsonify({
                "success": True,
                "status": "pending"
            })
            
    except Exception as e:
        logger.error(f"❌ Ошибка проверки сессии: {e}")
        return jsonify({"success": False, "error": str(e)}), 500