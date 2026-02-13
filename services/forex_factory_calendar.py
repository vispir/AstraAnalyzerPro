"""
Календарь Forex Factory: только High-impact USD события.
Парсинг страницы календаря, без API. Время на сайте — Eastern (US), конвертируем в UTC.
Для обхода 403 используем curl_cffi (TLS-отпечаток Chrome), иначе fallback на requests.
"""
import logging
import re
from datetime import datetime, timedelta
from typing import List, Dict, Optional
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

try:
    from curl_cffi import requests as curl_requests
    CURL_CFFI_AVAILABLE = True
except ImportError:
    CURL_CFFI_AVAILABLE = False
    curl_requests = None

logger = logging.getLogger(__name__)

BASE_URL = "https://www.forexfactory.com/calendar"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
REQUEST_TIMEOUT = 15
REQUEST_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Referer": "https://www.forexfactory.com/",
}
EASTERN = ZoneInfo("America/New_York")
UTC = ZoneInfo("UTC")


def _day_param(d: datetime) -> str:
    """Параметр day для URL: month abbrev + day + . + year, e.g. feb13.2026"""
    return d.strftime("%b%d.%Y").lower()


def _parse_impact_class(class_list: List[str]) -> str:
    """Из классов элемента impact (calendar__impact--high) -> High/Medium/Low"""
    for c in class_list or []:
        c = (c or "").lower()
        if "high" in c:
            return "High"
        if "medium" in c:
            return "Medium"
        if "low" in c:
            return "Low"
    return "Low"


def _parse_time_cell(text: str, base_date: datetime) -> Optional[datetime]:
    """
    Парсим время из ячейки (например "8:30am" или "All Day").
    base_date — дата дня в Eastern.
    Возвращает datetime в Eastern или None.
    """
    text = (text or "").strip()
    if not text or "all day" in text.lower():
        return base_date.replace(hour=0, minute=0, second=0, microsecond=0)
    # 8:30am, 10:00pm
    m = re.match(r"(\d{1,2}):(\d{2})\s*(am|pm)?", text, re.I)
    if not m:
        return None
    hour = int(m.group(1))
    minute = int(m.group(2))
    ampm = (m.group(3) or "").lower()
    if ampm == "pm" and hour != 12:
        hour += 12
    elif ampm == "am" and hour == 12:
        hour = 0
    try:
        return base_date.replace(hour=hour, minute=minute, second=0, microsecond=0)
    except ValueError:
        return None


def fetch_forex_factory_events(
    from_date: datetime,
    to_date: datetime,
) -> List[Dict]:
    """
    Загружает события календаря Forex Factory за диапазон дат.
    Только High и Medium impact, все валюты (фильтр USD делается в news_service).
    Время на сайте — US Eastern, конвертируется в UTC.

    Args:
        from_date: начало периода (datetime, любой timezone или naive)
        to_date: конец периода

    Returns:
        Список событий в формате, совместимом с news_service:
        title, currency, date, time, impact, timestamp (UTC), datetime (ISO),
        country, forecast, previous, actual.
    """
    events: List[Dict] = []
    # Нормализуем в date для итерации по дням
    from_d = from_date.date() if hasattr(from_date, "date") else from_date
    to_d = to_date.date() if hasattr(to_date, "date") else to_date
    if from_d > to_d:
        return events

    # curl_cffi с impersonate=chrome обходит 403 (TLS fingerprint); иначе — обычный requests
    if CURL_CFFI_AVAILABLE and curl_requests:
        try:
            session = curl_requests.Session(impersonate="chrome")
            session.headers.update(REQUEST_HEADERS)
            logger.debug("Forex Factory: using curl_cffi (Chrome TLS)")
        except Exception as e:
            logger.warning("curl_cffi Session failed, using requests: %s", e)
            session = requests.Session()
            session.headers.update(REQUEST_HEADERS)
    else:
        session = requests.Session()
        session.headers.update(REQUEST_HEADERS)
        if not CURL_CFFI_AVAILABLE:
            logger.debug("Forex Factory: using requests (install curl_cffi for better 403 bypass)")

    current = from_d
    while current <= to_d:
        day_param = _day_param(datetime(current.year, current.month, current.day))
        url = f"{BASE_URL}?day={day_param}"
        try:
            resp = session.get(url, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
        except Exception as e:
            logger.warning("Forex Factory request failed for %s: %s", day_param, e)
            current += timedelta(days=1)
            continue

        soup = BeautifulSoup(resp.text, "html.parser")
        base_date_eastern = datetime(current.year, current.month, current.day, tzinfo=EASTERN)

        # Варианты разметки: таблица с строками calendar__row или подобное
        rows = soup.select("tr.calendar__row")
        if not rows:
            rows = soup.select("table.calendar__table tr")
        if not rows:
            rows = soup.select(".calendar__table tr")
        if not rows:
            rows = soup.select("table.calendar tr")
        if not rows:
            # Любая таблица с классом, содержащим calendar
            for t in soup.find_all("table", class_=re.compile(r"calendar", re.I)):
                rows = t.find_all("tr")
                break

        for row in rows or []:
            try:
                cells = row.find_all("td")
                if len(cells) < 3:
                    continue
                # Порядок колонок FF: time, currency, impact (иконка), event, ...
                time_cell = cells[0].get_text(strip=True) if cells else ""
                currency_cell = (cells[1].get_text(strip=True) or "").strip().upper()
                if len(currency_cell) != 3:
                    currency_cell = ""
                impact_el = row.select_one("[class*='impact']")
                impact_class_list = impact_el.get("class", []) if impact_el else []
                impact = _parse_impact_class(impact_class_list)
                event_el = row.select_one("[class*='event']")
                title = (event_el.get_text(strip=True) if event_el else "").strip()
                if not title and len(cells) > 3:
                    title = cells[3].get_text(strip=True) or ""
                if not title:
                    continue

                dt_eastern = _parse_time_cell(time_cell, base_date_eastern)
                if not dt_eastern:
                    continue
                dt_utc = dt_eastern.astimezone(UTC)
                ts = int(dt_utc.timestamp())

                event = {
                    "title": title[:500],
                    "country": "",
                    "currency": currency_cell if currency_cell else "USD",
                    "date": current.strftime("%d/%m/%Y"),
                    "time": dt_eastern.strftime("%H:%M"),
                    "impact": impact,
                    "forecast": "",
                    "previous": "",
                    "actual": "",
                    "timestamp": ts,
                    "datetime": dt_utc.isoformat(),
                    "source": "forexfactory",
                }
                events.append(event)
            except Exception as e:
                logger.debug("Skip row Forex Factory: %s", e)
                continue

        current += timedelta(days=1)

    events.sort(key=lambda x: x.get("timestamp") or 0)
    logger.info("Forex Factory: fetched %s events from %s to %s", len(events), from_d, to_d)
    return events
