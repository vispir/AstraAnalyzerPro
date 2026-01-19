"""
Роуты для LLM анализа торговых решений
"""
from flask import Blueprint, jsonify, request
import logging
import requests

from services.llm_service import llm_service
from config.settings import SYMBOL

logger = logging.getLogger(__name__)

llm_bp = Blueprint('llm', __name__)


@llm_bp.route('/analyze', methods=['GET', 'POST'])
def analyze_market():
    """
    Комплексный анализ рынка с помощью LLM
    
    Query params:
        model: Выбор модели - "openrouter" (по умолчанию) или "gemini3"
    
    Собирает данные из всех источников:
    - Технические данные (свечи + SMC анализ) с 3 таймфреймов
    - Новостную ленту
    - Графики (M15, H1, H4) в base64
    
    Отправляет в LLM для принятия торгового решения
    
    Examples:
        /api/llm/analyze                  - OpenRouter (по умолчанию)
        /api/llm/analyze?model=gemini3    - Gemini 3 Pro
    
    Returns:
        {
            "success": true,
            "model": "google/gemini-2.0-flash-exp:free",
            "session_info": {...},
            "timestamp": "2026-01-20 14:30 UTC",
            "response": "LLM ответ...",
            "usage": {...}
        }
    """
    try:
        # Получаем параметр модели
        model = request.args.get('model', 'openrouter').lower()
        
        if model not in ['openrouter', 'gemini3']:
            return jsonify({
                "error": "Invalid model parameter",
                "details": "Use 'openrouter' or 'gemini3'"
            }), 400
        
        logger.info("=" * 60)
        logger.info(f"LLM ANALYSIS REQUEST STARTED (Model: {model})")
        logger.info("=" * 60)
        
        base_url = request.host_url.rstrip('/')
        
        # 1. Получаем технические данные (свечи + SMC)
        logger.info("Step 1/4: Fetching technical data from /api/market/candles")
        try:
            candles_response = requests.get(f"{base_url}/api/market/candles", timeout=30)
            candles_response.raise_for_status()
            technical_data = candles_response.json()
            
            if not technical_data.get('success'):
                return jsonify({
                    "error": "Failed to fetch technical data",
                    "details": technical_data
                }), 500
                
            logger.info(f"✓ Technical data retrieved: {len(technical_data['timeframes'])} timeframes")
        except Exception as e:
            logger.error(f"✗ Error fetching technical data: {str(e)}")
            return jsonify({
                "error": "Failed to fetch technical data",
                "details": str(e)
            }), 500
        
        # 2. Получаем новостную ленту
        logger.info("Step 2/4: Fetching news feed from /api/news/feed")
        try:
            news_response = requests.get(f"{base_url}/api/news/feed", timeout=120)
            news_response.raise_for_status()
            news_data = news_response.json()
            
            if not news_data.get('success'):
                return jsonify({
                    "error": "Failed to fetch news data",
                    "details": news_data
                }), 500
                
            logger.info(f"✓ News data retrieved: {news_data.get('total_events', 0)} events")
        except Exception as e:
            logger.error(f"✗ Error fetching news: {str(e)}")
            return jsonify({
                "error": "Failed to fetch news data",
                "details": str(e)
            }), 500
        
        # 3. Получаем графики для 3 таймфреймов
        logger.info("Step 3/4: Fetching chart images (M15, H1, H4)")
        chart_images = {}
        timeframes = ['M15', 'H1', 'H4']
        
        for tf in timeframes:
            try:
                chart_response = requests.get(
                    f"{base_url}/api/chart/generate",
                    params={'tf': tf, 'limit': 100},
                    timeout=30
                )
                chart_response.raise_for_status()
                chart_data = chart_response.json()
                
                if 'image' in chart_data:
                    chart_images[tf] = chart_data['image']
                    logger.info(f"  ✓ {tf} chart generated")
                else:
                    logger.warning(f"  ✗ {tf} chart missing in response")
                    
            except Exception as e:
                logger.error(f"  ✗ Error generating {tf} chart: {str(e)}")
                # Продолжаем даже если один из графиков не получен
        
        if not chart_images:
            return jsonify({
                "error": "Failed to generate any charts",
                "details": "All chart generation attempts failed"
            }), 500
        
        logger.info(f"✓ Charts generated: {len(chart_images)}/3")
        
        # 4. Вычисляем уровни (PDH/PDL, свинги и т.д.)
        logger.info("Step 4/4: Computing key levels")
        computed_levels = _compute_key_levels(technical_data)
        logger.info(f"✓ Key levels computed: {list(computed_levels.keys())}")
        
        # 5. Отправляем в LLM
        logger.info("=" * 60)
        logger.info(f"SENDING DATA TO LLM ({model.upper()})")
        logger.info("=" * 60)
        
        result = llm_service.analyze_trading_decision(
            technical_data=technical_data,
            news_data=news_data,
            computed_levels=computed_levels,
            chart_images=chart_images,
            model=model
        )
        
        if 'error' in result:
            logger.error(f"✗ LLM analysis failed: {result['error']}")
            return jsonify(result), 500
        
        logger.info("=" * 60)
        logger.info("✓ LLM ANALYSIS COMPLETED SUCCESSFULLY")
        logger.info("=" * 60)
        
        return jsonify(result)
        
    except Exception as e:
        logger.error(f"Error in /analyze: {str(e)}", exc_info=True)
        return jsonify({
            "error": "Internal error during analysis",
            "details": str(e)
        }), 500


@llm_bp.route('/session', methods=['GET'])
def get_session():
    """
    Получить информацию о текущей торговой сессии
    
    Returns:
        {
            "session": "London",
            "overlap": "London/New York" | null,
            "hour_utc": 14,
            "is_overlap": true,
            "description": "London/New York Overlap"
        }
    """
    try:
        session_info = llm_service.get_session_info()
        return jsonify(session_info)
    except Exception as e:
        logger.error(f"Error in /session: {str(e)}")
        return jsonify({"error": str(e)}), 500


@llm_bp.route('/status', methods=['GET'])
def get_status():
    """
    Проверка статуса LLM сервиса
    
    Returns:
        {
            "status": "available",
            "model": "google/gemini-2.0-flash-exp:free",
            "session": {...}
        }
    """
    try:
        session_info = llm_service.get_session_info()
        
        return jsonify({
            "status": "available",
            "model": llm_service.MODEL,
            "api_url": llm_service.OPENROUTER_API_URL,
            "current_session": session_info
        })
    except Exception as e:
        logger.error(f"Error in /status: {str(e)}")
        return jsonify({
            "status": "error",
            "error": str(e)
        }), 500


def _compute_key_levels(technical_data: dict) -> dict:
    """
    Вычисляет ключевые уровни из технических данных
    
    Args:
        technical_data: Данные с /api/market/candles
    
    Returns:
        Словарь с вычисленными уровнями (PDH, PDL, свинги и т.д.)
    """
    try:
        timeframes = technical_data.get('timeframes', {})
        
        # Извлекаем последние свечи для каждого таймфрейма
        h4_candles = timeframes.get('H4', {}).get('candles', [])
        h1_candles = timeframes.get('H1', {}).get('candles', [])
        m15_candles = timeframes.get('M15', {}).get('candles', [])
        
        levels = {}
        
        # PDH/PDL (Previous Day High/Low) - берем из последних 24 часов H4
        if h4_candles:
            # Берем последние 6 H4 свечей (24 часа)
            last_24h = h4_candles[-6:] if len(h4_candles) >= 6 else h4_candles
            
            highs = [c['high'] for c in last_24h]
            lows = [c['low'] for c in last_24h]
            
            levels['PDH'] = max(highs) if highs else None
            levels['PDL'] = min(lows) if lows else None
        
        # Текущие свинги (последние 10 свечей M15)
        if m15_candles:
            recent_m15 = m15_candles[-10:]
            
            highs = [c['high'] for c in recent_m15]
            lows = [c['low'] for c in recent_m15]
            
            levels['recent_swing_high'] = max(highs) if highs else None
            levels['recent_swing_low'] = min(lows) if lows else None
        
        # Текущая цена
        if m15_candles:
            levels['current_price'] = m15_candles[-1]['close']
        
        # Анализ из technical_data
        for tf_name in ['H4', 'H1', 'M15']:
            tf_data = timeframes.get(tf_name, {})
            analysis = tf_data.get('analysis', {})
            
            if analysis:
                levels[f'{tf_name}_analysis'] = {
                    'trend': analysis.get('trend'),
                    'strength': analysis.get('strength'),
                    'support': analysis.get('support'),
                    'resistance': analysis.get('resistance')
                }
        
        return levels
        
    except Exception as e:
        logger.error(f"Error computing key levels: {str(e)}")
        return {
            "error": "Failed to compute levels",
            "details": str(e)
        }
