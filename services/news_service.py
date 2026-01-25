"""
Сервис для работы с экономическими новостями через investpy и gnews
"""
import logging
from datetime import datetime, timedelta
from typing import List, Dict, Optional

try:
    import investpy
    INVESTPY_AVAILABLE = True
except ImportError:
    INVESTPY_AVAILABLE = False

try:
    from gnews import GNews
    GNEWS_AVAILABLE = True
except ImportError:
    GNEWS_AVAILABLE = False

from services.cache_service import cache_service

logger = logging.getLogger(__name__)


class NewsService:
    """Сервис для получения экономических новостей"""
    
    def __init__(self):
        # TTL для разных типов новостей
        self.calendar_cache_ttl = 1800  # 30 минут для экономического календаря
        self.geopolitical_cache_ttl = 3600  # 60 минут для геополитических новостей
        
        # Инициализация GNews
        if GNEWS_AVAILABLE:
            self.gnews = GNews(
                language='en',
                country='US',
                period='7d',  # За неделю
                max_results=20
            )
        
        logger.info(f"News service initialized - investpy: {INVESTPY_AVAILABLE}, gnews: {GNEWS_AVAILABLE}")
        
    def fetch_calendar(self, from_date: datetime, to_date: datetime) -> List[Dict]:
        """
        Загрузка экономического календаря через investpy
        
        Args:
            from_date: Начальная дата
            to_date: Конечная дата
            
        Returns:
            Список событий
        """
        if not INVESTPY_AVAILABLE:
            logger.error("investpy library is not installed")
            return []
        
        # Проверяем кэш
        cache_key = cache_service._generate_key(
            'calendar',
            from_date.strftime('%Y-%m-%d'),
            to_date.strftime('%Y-%m-%d')
        )
        cached_data = cache_service.get(cache_key)
        if cached_data is not None:
            logger.info(f"Returning cached calendar data ({len(cached_data)} events)")
            return cached_data
        
        try:
            # Убеждаемся, что даты различаются хотя бы на один день
            # investpy требует, чтобы to_date > from_date после форматирования в dd/mm/yyyy
            from_date_only = from_date.replace(hour=0, minute=0, second=0, microsecond=0)
            to_date_only = to_date.replace(hour=0, minute=0, second=0, microsecond=0)
            
            # Если даты одинаковые, добавляем один день к to_date
            if from_date_only >= to_date_only:
                to_date_only = from_date_only + timedelta(days=1)
                logger.info(f"Adjusting to_date to ensure it's greater than from_date")
            
            logger.info(f"Fetching economic calendar from {from_date_only.date()} to {to_date_only.date()}")
            
            # Получаем календарь через investpy
            # investpy.news.economic_calendar возвращает DataFrame
            calendar_df = investpy.news.economic_calendar(
                time_zone=None,
                time_filter='time_only',
                countries=None,  # Все страны
                importances=None,  # Все уровни важности
                categories=None,
                from_date=from_date_only.strftime('%d/%m/%Y'),
                to_date=to_date_only.strftime('%d/%m/%Y')
            )
            
            events = []
            if calendar_df is not None and len(calendar_df) > 0:
                for _, row in calendar_df.iterrows():
                    try:
                        # Парсим данные из investpy
                        event = {
                            'title': str(row.get('event', '')).strip(),
                            'country': str(row.get('zone', '')).strip(),
                            'currency': self._get_currency_from_zone(str(row.get('zone', ''))),
                            'date': str(row.get('date', '')).strip(),
                            'time': str(row.get('time', '')).strip(),
                            'impact': self._normalize_importance(row.get('importance', '')),
                            'forecast': str(row.get('forecast', '')).strip(),
                            'previous': str(row.get('previous', '')).strip(),
                            'actual': str(row.get('actual', '')).strip(),
                        }
                        
                        # Создаем timestamp
                        try:
                            date_str = event['date']
                            time_str = event['time']
                            if date_str and time_str:
                                # Обработка "All Day" событий
                                if 'all day' in time_str.lower() or time_str.lower() == 'all day':
                                    # Для целодневных событий используем начало дня
                                    dt = datetime.strptime(date_str, "%d/%m/%Y")
                                    event['time'] = '00:00'  # Форматируем для отображения
                                else:
                                    # investpy использует формат DD/MM/YYYY и HH:MM
                                    dt = datetime.strptime(f"{date_str} {time_str}", "%d/%m/%Y %H:%M")
                                
                                event['timestamp'] = int(dt.timestamp())
                                event['datetime'] = dt.isoformat()
                            else:
                                event['timestamp'] = None
                                event['datetime'] = None
                        except Exception as e:
                            logger.warning(f"Error parsing date/time for event '{event.get('title', '')}': {str(e)}")
                            event['timestamp'] = None
                            event['datetime'] = None
                        
                        events.append(event)
                    except Exception as e:
                        logger.warning(f"Error parsing event: {str(e)}")
                        continue
            
            logger.info(f"Successfully fetched {len(events)} events from investpy")
            
            # Сохраняем в кэш
            cache_service.set(cache_key, events, self.calendar_cache_ttl)
            
            return events
            
        except Exception as e:
            logger.error(f"Error fetching calendar from investpy: {str(e)}")
            return []
    
    def _get_currency_from_zone(self, zone: str) -> str:
        """
        Получение кода валюты по названию страны/зоны
        
        Args:
            zone: Название зоны (например, 'united states', 'euro zone')
            
        Returns:
            Код валюты (USD, EUR, etc.)
        """
        zone_lower = zone.lower()
        currency_map = {
            'united states': 'USD',
            'euro zone': 'EUR',
            'germany': 'EUR',
            'france': 'EUR',
            'italy': 'EUR',
            'spain': 'EUR',
            'united kingdom': 'GBP',
            'japan': 'JPY',
            'china': 'CNY',
            'australia': 'AUD',
            'canada': 'CAD',
            'switzerland': 'CHF',
            'new zealand': 'NZD',
        }
        
        for country, currency in currency_map.items():
            if country in zone_lower:
                return currency
        
        return zone[:3].upper()  # Fallback - первые 3 символа
    
    def _normalize_importance(self, importance) -> str:
        """
        Нормализация уровня важности
        
        Args:
            importance: Важность от investpy (low, medium, high)
            
        Returns:
            Normalized impact (High, Medium, Low)
        """
        importance_str = str(importance).lower().strip()
        
        if 'high' in importance_str:
            return "High"
        elif 'medium' in importance_str:
            return "Medium"
        else:
            return "Low"
    
    def get_all_news(self) -> List[Dict]:
        """
        Получение всех новостей недели
        
        Returns:
            Список всех событий
        """
        # Получаем календарь на неделю вперед
        now = datetime.now()
        week_later = now + timedelta(days=7)
        
        return self.fetch_calendar(now, week_later)
    
    def filter_news(
        self,
        events: List[Dict],
        currencies: Optional[List[str]] = None,
        impact: Optional[List[str]] = None,
        hours_ahead: Optional[int] = None
    ) -> List[Dict]:
        """
        Фильтрация новостей
        
        Args:
            events: Список событий
            currencies: Фильтр по валютам (например, ['USD', 'EUR'])
            impact: Фильтр по важности (список: ['High', 'Medium']). По умолчанию ['High', 'Medium']
            hours_ahead: Показать только события в ближайшие N часов
            
        Returns:
            Отфильтрованный список событий
        """
        filtered = events.copy()
        
        # Фильтр по валюте
        if currencies:
            currencies_upper = [c.upper() for c in currencies]
            filtered = [e for e in filtered if e.get('currency', '').upper() in currencies_upper]
        
        # Фильтр по важности (по умолчанию High и Medium)
        if impact is None:
            impact = ['High', 'Medium']  # По умолчанию только важные новости
        
        if impact:
            impact_lower = [i.lower() for i in impact]
            filtered = [e for e in filtered if e.get('impact', '').lower() in impact_lower]
        
        # Фильтр по времени (только будущие события в N часов)
        if hours_ahead:
            now = datetime.now()
            cutoff = now + timedelta(hours=hours_ahead)
            filtered = [
                e for e in filtered 
                if e.get('timestamp') and datetime.fromtimestamp(e['timestamp']) <= cutoff
            ]
        
        # Только будущие события
        now_ts = int(datetime.now().timestamp())
        filtered = [e for e in filtered if e.get('timestamp') and e['timestamp'] > now_ts]
        
        # Сортировка по времени
        filtered.sort(key=lambda x: x.get('timestamp', 0))
        
        return filtered
    
    def get_upcoming_news(
        self,
        hours: int = 24,
        currencies: Optional[List[str]] = None,
        impact: Optional[List[str]] = None
    ) -> List[Dict]:
        """
        Получение предстоящих новостей
        
        Args:
            hours: Количество часов вперед
            currencies: Фильтр по валютам
            impact: Фильтр по важности (список). По умолчанию ['High', 'Medium']
            
        Returns:
            Список предстоящих событий
        """
        all_events = self.get_all_news()
        return self.filter_news(all_events, currencies, impact, hours)
    
    def get_today_news(
        self,
        currencies: Optional[List[str]] = None,
        impact: Optional[List[str]] = None
    ) -> List[Dict]:
        """
        Получение новостей на сегодня
        
        Args:
            currencies: Фильтр по валютам
            impact: Фильтр по важности (список). По умолчанию ['High', 'Medium']
            
        Returns:
            Список событий на сегодня
        """
        all_events = self.get_all_news()
        
        # Фильтруем только сегодняшние события
        today = datetime.now().date()
        today_events = [
            e for e in all_events 
            if e.get('timestamp') and datetime.fromtimestamp(e['timestamp']).date() == today
        ]
        
        return self.filter_news(today_events, currencies, impact)
    
    def get_high_impact_news(self, hours: int = 48) -> List[Dict]:
        """
        Получение высокоприоритетных новостей (только High)
        
        Args:
            hours: Количество часов вперед
            
        Returns:
            Список важных событий
        """
        return self.get_upcoming_news(hours=hours, impact=['High'])
    
    def get_past_news(self, hours: int = 24) -> List[Dict]:
        """
        Получение прошедших новостей релевантных для золота
        Только USD новости High и Medium важности
        
        Args:
            hours: Количество часов назад
            
        Returns:
            Список прошедших USD событий (High и Medium)
        """
        try:
            now = datetime.now()
            past_date = now - timedelta(hours=hours)
            
            # Получаем календарь за прошедший период
            calendar_data = self.fetch_calendar(past_date, now)
            
            # Фильтруем только прошедшие USD события High и Medium важности
            past_events = []
            now_ts = int(now.timestamp())
            
            for event in calendar_data:
                # Проверяем timestamp
                if not event.get('timestamp') or event['timestamp'] >= now_ts:
                    continue
                
                # Только USD (релевантно для золота)
                if event.get('currency', '').upper() != 'USD':
                    continue
                
                # Только High и Medium
                if event.get('impact') not in ['High', 'Medium']:
                    continue
                
                past_events.append(event)
            
            # Сортируем по времени (свежие первыми)
            past_events.sort(key=lambda x: x.get('timestamp', 0), reverse=True)
            
            logger.info(f"Found {len(past_events)} USD past events (High/Medium) in last {hours}h")
            return past_events
            
        except Exception as e:
            logger.error(f"Error fetching past news: {str(e)}")
            return []
    
    def get_geopolitical_news(self, days: int = 7) -> List[Dict]:
        """
        Получение геополитических новостей, влияющих на золото
        
        Args:
            days: Количество дней назад для поиска
            
        Returns:
            Список новостей (только заголовки и ссылки)
        """
        if not GNEWS_AVAILABLE:
            logger.error("gnews library is not installed")
            return []
        
        # Проверяем кэш
        cache_key = cache_service._generate_key('geopolitical', days=days)
        cached_data = cache_service.get(cache_key)
        if cached_data is not None:
            logger.info(f"Returning cached geopolitical news ({len(cached_data)} articles)")
            return cached_data
        
        try:
            logger.info(f"Fetching geopolitical news for last {days} days")
            
            # Ключевые слова для золота
            keywords = [
                'gold price',
                'fed interest rate',
                'inflation',
                'dollar index',
                'geopolitical tension',
                'central bank',
                'recession'
            ]
            
            all_news = []
            seen_descriptions = set()  # Для дедупликации по description
            
            for keyword in keywords:
                try:
                    # Настраиваем период
                    self.gnews.period = f'{days}d'
                    self.gnews.max_results = 5  # По 5 новостей на ключевое слово
                    
                    articles = self.gnews.get_news(keyword)
                    
                    for article in articles:
                        description = article.get('description', '').strip()
                        
                        # Пропускаем дубликаты и пустые
                        if not description or description in seen_descriptions:
                            continue
                        
                        seen_descriptions.add(description)
                        
                        # Парсим дату публикации
                        published_date = article.get('published date', '')
                        timestamp = None
                        try:
                            if published_date:
                                # GNews возвращает формат "Mon, 20 Jan 2026 12:00:00 GMT"
                                dt = datetime.strptime(published_date, "%a, %d %b %Y %H:%M:%S %Z")
                                timestamp = int(dt.timestamp())
                        except:
                            pass
                        
                        # Оставляем только 4 основных поля
                        all_news.append({
                            'description': description,
                            'keyword': keyword,
                            'published_date': published_date,
                            'publisher': article.get('publisher', {}).get('title', ''),
                            'timestamp': timestamp
                        })
                    
                except Exception as e:
                    logger.warning(f"Error fetching news for keyword '{keyword}': {str(e)}")
                    continue
            
            # Сортируем по времени (свежие первыми)
            all_news.sort(key=lambda x: x.get('timestamp', 0), reverse=True)
            
            # Ограничиваем до 20 новостей
            all_news = all_news[:20]
            
            logger.info(f"Found {len(all_news)} geopolitical news articles")
            
            # Сохраняем в кэш
            cache_service.set(cache_key, all_news, self.geopolitical_cache_ttl)
            
            return all_news
            
        except Exception as e:
            logger.error(f"Error fetching geopolitical news: {str(e)}")
            return []
    
    def get_combined_news_feed(self) -> Dict:
        """
        Комбинированная лента новостей для трейдинга золотом
        
        Returns:
            Dict с тремя категориями новостей:
            - upcoming: предстоящие события (12ч)
            - past: прошедшие события (24ч)
            - geopolitical: геополитические новости (неделя)
        """
        try:
            logger.info("Generating combined news feed for gold trading")
            
            # 1. Предстоящие новости на 12 часов (только USD, High и Medium)
            upcoming = self.get_upcoming_news(
                hours=12,
                currencies=['USD'],
                impact=['High', 'Medium']
            )
            
            # 2. Прошедшие новости за 24 часа
            past = self.get_past_news(hours=24)
            
            # 3. Геополитические новости за неделю
            geopolitical = self.get_geopolitical_news(days=7)
            
            return {
                'success': True,
                'generated_at': datetime.now().isoformat(),
                'upcoming': {
                    'description': 'Upcoming USD economic events (next 12 hours)',
                    'count': len(upcoming),
                    'events': upcoming
                },
                'past': {
                    'description': 'Past economic events with actual values (last 24 hours)',
                    'count': len(past),
                    'events': past
                },
                'geopolitical': {
                    'description': 'Geopolitical news affecting gold (last 7 days)',
                    'count': len(geopolitical),
                    'articles': geopolitical
                }
            }
            
        except Exception as e:
            logger.error(f"Error generating combined news feed: {str(e)}")
            return {
                'success': False,
                'error': str(e),
                'upcoming': {'count': 0, 'events': []},
                'past': {'count': 0, 'events': []},
                'geopolitical': {'count': 0, 'articles': []}
            }
    
    def clear_cache(self):
        """Очистка кеша новостей"""
        count = cache_service.clear('calendar') + cache_service.clear('geopolitical')
        logger.info(f"News cache cleared ({count} entries)")


# Глобальный экземпляр сервиса
news_service = NewsService()
