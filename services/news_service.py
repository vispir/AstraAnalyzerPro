"""
Сервис для работы с экономическими новостями через investpy и gnews
"""
import json
import logging
import concurrent.futures
from datetime import datetime, timedelta
from typing import List, Dict, Optional

try:
    import investpy
    INVESTPY_AVAILABLE = True
    _investpy_import_error = None
except ImportError as e:
    INVESTPY_AVAILABLE = False
    _investpy_import_error = str(e)

try:
    from gnews import GNews
    GNEWS_AVAILABLE = True
except ImportError:
    GNEWS_AVAILABLE = False

from services.cache_service import cache_service

try:
    from services.forex_factory_calendar import fetch_forex_factory_events
    FOREX_FACTORY_AVAILABLE = True
except ImportError as e:
    FOREX_FACTORY_AVAILABLE = False
    fetch_forex_factory_events = None
    _ff_import_error = str(e)

logger = logging.getLogger(__name__)

# Логируем отсутствие investpy только один раз за процесс
_investpy_warned = False


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
        
        logger.info(
            f"News service initialized - investpy: {INVESTPY_AVAILABLE}, "
            f"gnews: {GNEWS_AVAILABLE}, forexfactory: {FOREX_FACTORY_AVAILABLE}"
        )
        
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
            global _investpy_warned
            if not _investpy_warned:
                _investpy_warned = True
                logger.info(
                    "Economic calendar unavailable: investpy not installed. "
                    "Install with: pip install investpy>=1.0.8"
                )
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
            
        except json.JSONDecodeError as e:
            # investpy: Investing.com часто возвращает пустой/HTML вместо JSON
            logger.info(
                "Economic calendar temporarily unavailable (Investing.com returned invalid/empty response). "
                "Using empty calendar for this request."
            )
            return []
        except ValueError as e:
            # Некоторые окружения/библиотеки пробрасывают ошибку парсинга JSON как ValueError
            if "Expecting value" in str(e) or ("column" in str(e) and "char" in str(e)):
                logger.info(
                    "Economic calendar temporarily unavailable (Investing.com returned invalid/empty response). "
                    "Using empty calendar for this request."
                )
                return []
            raise
        except Exception as e:
            logger.warning(f"Error fetching calendar from investpy: {str(e)}")
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

    def _fetch_forex_factory_calendar(self, from_date: datetime, to_date: datetime) -> List[Dict]:
        """Календарь Forex Factory за период (с кэшем). Возвращает события в том же формате, что и fetch_calendar."""
        if not FOREX_FACTORY_AVAILABLE or not fetch_forex_factory_events:
            return []
        cache_key = cache_service._generate_key(
            'calendar_ff',
            from_date.strftime('%Y-%m-%d'),
            to_date.strftime('%Y-%m-%d')
        )
        cached = cache_service.get(cache_key)
        if cached is not None:
            logger.info(f"Returning cached Forex Factory calendar ({len(cached)} events)")
            return cached
        try:
            events = fetch_forex_factory_events(from_date, to_date)
            cache_service.set(cache_key, events, self.calendar_cache_ttl)
            return events
        except Exception as e:
            logger.warning("Forex Factory calendar fetch failed: %s", e)
            return []

    def _get_merged_calendar(self, from_date: datetime, to_date: datetime) -> List[Dict]:
        """Объединённый календарь: investpy + Forex Factory. Дедупликация по (timestamp, title), сортировка по времени."""
        investpy_events = self.fetch_calendar(from_date, to_date)
        ff_events = self._fetch_forex_factory_calendar(from_date, to_date)
        seen = set()
        merged = []
        for e in investpy_events + ff_events:
            ts = e.get('timestamp')
            title = (e.get('title') or '').strip()
            key = (ts, title) if ts and title else None
            if key and key in seen:
                continue
            if key:
                seen.add(key)
            merged.append(e)
        merged.sort(key=lambda x: x.get('timestamp') or 0)
        return merged

    def get_all_news(self) -> List[Dict]:
        """
        Получение всех новостей недели (investpy + Forex Factory).
        
        Returns:
            Список всех событий
        """
        now = datetime.now()
        week_later = now + timedelta(days=7)
        return self._get_merged_calendar(now, week_later)
    
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
            
            # Получаем календарь за прошедший период (investpy + Forex Factory)
            calendar_data = self._get_merged_calendar(past_date, now)
            
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
    
    def _fetch_keyword_news(self, keyword: str, days: int) -> List[Dict]:
        """Helper to fetch news for a single keyword (thread-safe)"""
        if not GNEWS_AVAILABLE:
            return []
            
        try:
            # Создаем локальный экземпляр для потокобезопасности
            local_gnews = GNews(
                language='en',
                country='US',
                period=f'{days}d',
                max_results=5
            )
            return local_gnews.get_news(keyword)
        except Exception as e:
            logger.warning(f"Error fetching news for keyword '{keyword}': {str(e)}")
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
            logger.info(f"Fetching geopolitical news for last {days} days (Timeout: 120s)")
            
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
            
            # Параллельный запуск с таймаутом
            with concurrent.futures.ThreadPoolExecutor(max_workers=min(len(keywords), 10)) as executor:
                future_to_keyword = {
                    executor.submit(self._fetch_keyword_news, keyword, days): keyword 
                    for keyword in keywords
                }
                
                # Ждем выполнения с таймаутом 120 секунд
                done, not_done = concurrent.futures.wait(
                    future_to_keyword.keys(), 
                    timeout=120, 
                    return_when=concurrent.futures.ALL_COMPLETED
                )
                
                if not_done:
                    logger.warning(f"⚠️ Geopolitical news fetch timed out! {len(not_done)} keywords incomplete.")
                
                # Собираем результаты
                for future in done:
                    keyword = future_to_keyword[future]
                    try:
                        articles = future.result()
                        if not articles:
                            continue
                            
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
                        logger.error(f"Error processing results for '{keyword}': {e}")
            
            # Сортируем по времени (свежие первыми)
            all_news.sort(key=lambda x: x.get('timestamp', 0) or 0, reverse=True)
            
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
        """Очистка кеша новостей (investpy, Forex Factory, geopolitical)"""
        count = (
            cache_service.clear('calendar')
            + cache_service.clear('calendar_ff')
            + cache_service.clear('geopolitical')
        )
        logger.info(f"News cache cleared ({count} entries)")


# Глобальный экземпляр сервиса
news_service = NewsService()
