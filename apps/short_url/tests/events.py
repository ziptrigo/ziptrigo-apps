"""Helpers that put click events in the database at chosen times."""

from datetime import date, datetime, timedelta, timezone

from ..models import ClickEvent, ShortLink

NOW = datetime(2026, 10, 20, 12, 0, tzinfo=timezone.utc)
TODAY = NOW.date()


def at_day(day: date, hour: int = 10) -> datetime:
    return datetime(day.year, day.month, day.day, hour, tzinfo=timezone.utc)


def days_ago(n: int) -> date:
    return TODAY - timedelta(days=n)


def add_event(
    link: ShortLink,
    when: datetime,
    *,
    visitor: str = 'v1',
    referrer: str = '',
    country: str = '',
    device: str = 'desktop',
    browser: str = 'Chrome',
    os: str = 'Windows',
    bot: bool = False,
) -> ClickEvent:
    return ClickEvent.objects.create(
        link=link,
        at=when,
        ip_hash=visitor,
        referrer_host=referrer,
        country=country,
        device='bot' if bot else device,
        browser=browser,
        os=os,
        is_bot=bot,
    )
