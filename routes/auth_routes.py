from flask import Blueprint, request, jsonify
from services.auth_service import auth_service

auth_bp = Blueprint('auth', __name__)

@auth_bp.route('/telegram', methods=['POST'])
def telegram_auth():
    data = request.json
    if not data:
        return jsonify({"success": False, "error": "No data"}), 400

    if auth_service.verify_telegram_auth(data):
        # Если проверка прошла, возвращаем данные юзера фронтенду
        return jsonify({
            "success": True,
            "user": {
                "id": data.get('id'),
                "name": f"{data.get('first_name', '')} {data.get('last_name', '')}".strip(),
                "photo": data.get('photo_url'),
                "username": data.get('username')
            }
        })
    
    return jsonify({"success": False, "error": "Invalid signature"}), 401