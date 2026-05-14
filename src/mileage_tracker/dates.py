from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from dateutil import parser as dateparser

TZ = ZoneInfo("America/New_York")


def today() -> date:
    return datetime.now(TZ).date()


def parse_date(value: str | None) -> date:
    """Accept 'today', 'yesterday', '2 days ago', or any ISO-ish date."""
    if value is None:
        return today()
    s = value.strip().lower()
    if s in ("today", "now"):
        return today()
    if s == "yesterday":
        return today() - timedelta(days=1)
    if s.endswith(" days ago"):
        n = int(s.split()[0])
        return today() - timedelta(days=n)
    return dateparser.parse(value).date()


def year_bounds(year: int) -> tuple[date, date]:
    return date(year, 1, 1), date(year, 12, 31)
