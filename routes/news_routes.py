"""
Роуты для работы с экономическими новостями
"""
from flask import Blueprint, jsonify, request
import logging

from services.news_service import news_service

logger = logging.getLogger(__name__)

news_bp = Blueprint('news', __name__)


@news_bp.route('/all')
def get_all():
    """
    Получение всех новостей недели (по умолчанию только High и Medium)
    Query params:
        currency: фильтр по валюте (можно несколько через запятую)
        impact: фильтр по важности (можно несколько через запятую: High,Medium,Low)
    """
    try:
        currencies = request.args.get('currency')
        impact = request.args.get('impact')
        
        # Парсим список валют
        currency_list = None
        if currencies:
            currency_list = [c.strip() for c in currencies.split(',')]
        
        # Парсим список уровней важности
        impact_list = None
        if impact:
            impact_list = [i.strip() for i in impact.split(',')]
        
        all_news = news_service.get_all_news()
        all_news = news_service.filter_news(all_news, currency_list, impact_list)
        
        return jsonify({
            "success": True,
            "count": len(all_news),
            "events": all_news
        })
        
    except Exception as e:
        logger.error(f"Error in /all: {str(e)}")
        return jsonify({"error": str(e)}), 500


@news_bp.route('/upcoming')
def get_upcoming():
    """
    Получение предстоящих новостей (по умолчанию только High и Medium)
    Query params:
        hours: количество часов вперед (по умолчанию 24)
        currency: фильтр по валюте (можно несколько через запятую)
        impact: фильтр по важности (можно несколько через запятую: High,Medium,Low)
    """
    try:
        hours = request.args.get('hours', type=int, default=24)
        currencies = request.args.get('currency')
        impact = request.args.get('impact')
        
        currency_list = None
        if currencies:
            currency_list = [c.strip() for c in currencies.split(',')]
        
        # Парсим список уровней важности
        impact_list = None
        if impact:
            impact_list = [i.strip() for i in impact.split(',')]
        
        upcoming = news_service.get_upcoming_news(hours, currency_list, impact_list)
        
        return jsonify({
            "success": True,
            "count": len(upcoming),
            "hours_ahead": hours,
            "events": upcoming
        })
        
    except Exception as e:
        logger.error(f"Error in /upcoming: {str(e)}")
        return jsonify({"error": str(e)}), 500


@news_bp.route('/today')
def get_today():
    """
    Получение новостей на сегодня (по умолчанию только High и Medium)
    Query params:
        currency: фильтр по валюте (можно несколько через запятую)
        impact: фильтр по важности (можно несколько через запятую: High,Medium,Low)
    """
    try:
        currencies = request.args.get('currency')
        impact = request.args.get('impact')
        
        currency_list = None
        if currencies:
            currency_list = [c.strip() for c in currencies.split(',')]
        
        # Парсим список уровней важности
        impact_list = None
        if impact:
            impact_list = [i.strip() for i in impact.split(',')]
        
        today_news = news_service.get_today_news(currency_list, impact_list)
        
        return jsonify({
            "success": True,
            "count": len(today_news),
            "events": today_news
        })
        
    except Exception as e:
        logger.error(f"Error in /today: {str(e)}")
        return jsonify({"error": str(e)}), 500


@news_bp.route('/high-impact')
def get_high_impact():
    """
    Получение высокоприоритетных новостей
    Query params:
        hours: количество часов вперед (по умолчанию 48)
    """
    try:
        hours = request.args.get('hours', type=int, default=48)
        
        high_impact = news_service.get_high_impact_news(hours)
        
        return jsonify({
            "success": True,
            "count": len(high_impact),
            "hours_ahead": hours,
            "events": high_impact
        })
        
    except Exception as e:
        logger.error(f"Error in /high-impact: {str(e)}")
        return jsonify({"error": str(e)}), 500


@news_bp.route('/gold-relevant')
def get_gold_relevant():
    """
    Получение новостей, релевантных для торговли золотом
    Фокус на USD новости высокой и средней важности
    """
    try:
        hours = request.args.get('hours', type=int, default=48)
        
        # Для золота важны USD новости (High и Medium)
        usd_news = news_service.get_upcoming_news(
            hours=hours,
            currencies=['USD'],
            impact=['High', 'Medium']  # По умолчанию High и Medium
        )
        
        return jsonify({
            "success": True,
            "count": len(usd_news),
            "description": "High and Medium impact USD news relevant for gold trading",
            "events": usd_news
        })
        
    except Exception as e:
        logger.error(f"Error in /gold-relevant: {str(e)}")
        return jsonify({"error": str(e)}), 500


@news_bp.route('/past')
def get_past():
    """
    Получение прошедших новостей с actual значениями
    Query params:
        hours: количество часов назад (по умолчанию 24)
    """
    try:
        hours = request.args.get('hours', type=int, default=24)
        
        past_news = news_service.get_past_news(hours)
        
        return jsonify({
            "success": True,
            "count": len(past_news),
            "hours_back": hours,
            "events": past_news
        })
        
    except Exception as e:
        logger.error(f"Error in /past: {str(e)}")
        return jsonify({"error": str(e)}), 500


@news_bp.route('/geopolitical')
def get_geopolitical():
    """
    Получение геополитических новостей, влияющих на золото
    Query params:
        days: количество дней назад (по умолчанию 7)
    """
    try:
        days = request.args.get('days', type=int, default=7)
        
        geo_news = news_service.get_geopolitical_news(days)
        
        return jsonify({
            "success": True,
            "count": len(geo_news),
            "days_back": days,
            "articles": geo_news
        })
        
    except Exception as e:
        logger.error(f"Error in /geopolitical: {str(e)}")
        return jsonify({"error": str(e)}), 500


@news_bp.route('/feed')
def get_combined_feed():
    """
    Комбинированная лента новостей для трейдинга золотом
    Возвращает:
    - upcoming: предстоящие USD события (12ч)
    - past: прошедшие события с actual (24ч)
    - geopolitical: геополитические новости (7 дней)
    """
    try:
        feed = news_service.get_combined_news_feed()
        return jsonify(feed)
    except Exception as e:
        logger.error(f"Error in /feed: {str(e)}")
        return jsonify({"error": str(e)}), 500


@news_bp.route('/clear-cache', methods=['POST'])
def clear_cache():
    """
    Очистка кеша новостей (принудительное обновление)
    """
    try:
        news_service.clear_cache()
        
        return jsonify({
            "success": True,
            "message": "Cache cleared successfully"
        })
        
    except Exception as e:
        logger.error(f"Error in /clear-cache: {str(e)}")
        return jsonify({"error": str(e)}), 500
