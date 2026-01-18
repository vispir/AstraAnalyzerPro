import os
import sys

# 1. КРИТИЧЕСКИЙ ФИКС КИРИЛЛИЦЫ (Администратор)
# Указываем путь к сертификату без русских букв
# Если на шаге 1 ты копировал в другое место, исправь путь тут
cert_path = "C:\\cacert.pem" 
os.environ['SSL_CERT_FILE'] = cert_path
os.environ['REQUESTS_CA_BUNDLE'] = cert_path
os.environ['CURL_CA_BUNDLE'] = cert_path

import ssl
try:
    ssl._create_default_https_context = ssl._create_unverified_context
except:
    pass

from flask import Flask, jsonify, request
from flask_cors import CORS
import logging
from config.settings import FLASK_PORT, FLASK_DEBUG, SYMBOL

app = Flask(__name__)
CORS(app)

# Импортируем роуты
from routes.market_routes import market_bp
from routes.analysis_routes import analysis_bp

app.register_blueprint(market_bp, url_prefix='/api/market')
app.register_blueprint(analysis_bp, url_prefix='/api/analysis')

@app.route('/')
def index():
    return jsonify({"status": "running", "symbol": SYMBOL})

if __name__ == '__main__':
    print(f"--- SERVER STARTED WITH SSL FIX ---")
    app.run(host='127.0.0.1', port=5000, debug=FLASK_DEBUG)