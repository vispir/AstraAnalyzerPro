"""
Настройки приложения
"""
import os
from dotenv import load_dotenv

# Загрузка переменных окружения
load_dotenv()

# API Keys
GEMINI_API_KEY = os.getenv('GEMINI_API_KEY')
OPENROUTER_API_KEY = os.getenv('OPENROUTER_API_KEY')  # Опционально для бесплатной модели
AI_GATEWAY_URL = os.getenv('AI_GATEWAY_URL')  # URL для AI Gateway
AI_GATEWAY_KEY = os.getenv('AI_GATEWAY_KEY')  # API ключ для AI Gateway (если требуется)
TWELVE_DATA_API_KEY = os.getenv('TWELVE_DATA_API_KEY') 
OANDA_API_KEY = os.getenv("OANDA_API_KEY")
OANDA_ACCOUNT_ID = os.getenv("OANDA_ACCOUNT_ID")

# Trading Configuration
SYMBOL = os.getenv('SYMBOL', 'XAUUSD')
# Варианты символов для золота на Yahoo Finance:
# 'GC=F' - Gold Futures
YAHOO_SYMBOL = 'GC=F'

# Server Configuration
FLASK_PORT = int(os.getenv('FLASK_PORT', 5000))
FLASK_DEBUG = os.getenv('FLASK_DEBUG', 'False').lower() == 'true'

# Yahoo Finance Timeframe Mapping
TIMEFRAME_MAP = {
    'M1': '1m',
    'M5': '5m',
    'M15': '15m',
    'M30': '30m',
    'H1': '60m',
    'H4': '1h',  # Запрашиваем H1, агрегируем 4 свечи в одну H4
    'D1': '1d',
    'W1': '1wk',
    'MN': '1mo'
}

# Период данных для каждого таймфрейма
PERIOD_MAP = {
    'M1': '1d',
    'M5': '5d',
    'M15': '5d',
    'M30': '1mo',
    'H1': '1mo',
    'H4': '60d',  # Для H4 берем 60 дней
    'D1': '1y',
    'W1': '2y',
    'MN': '5y'
}

# Количество свечей для AI контекста
AI_CONTEXT_BARS = {
    'M15': 30,
    'H1': 20,
    'H4': 15
}
