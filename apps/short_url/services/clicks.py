"""Counting a click on a link, and recording the event behind it.

`record` is the redirect view's one call. In one transaction it bumps the link's counters with a
single conditional `UPDATE` (so the click cap can't be overshot by racing clicks) and inserts the
`ClickEvent` (device, browser, OS, referrer host, country, bot flag, a hashed visitor id).

**How the two counters relate.** `ShortLink.click_count` is the headline number and what
`max_clicks` caps; `bot_click_count` counts the clicks flagged as bots (crawlers, link-preview
fetchers, HTTP libraries; see `useragents`).

- `filter_bots` on (the default): a bot click only adds to `bot_click_count`. The headline count
  and the cap are people's alone, so a chat app unfurling the link neither inflates the numbers
  nor uses up a click limit. The bot is still redirected and still gets its event.
- `filter_bots` off: every click adds to `click_count`, and a bot's also adds to
  `bot_click_count`, which then reads "of these, this many were bots". Bots use up the cap.

The counters record the choice in force at the time of the click; switching it later doesn't
rewrite history (the stats page re-reads the events with the current choice, see `stats`).

Only a `GET` is a click, and the caller skips the write over its rate limit (see the redirect
view); `with_event=False` is for the one case where the counter must still move (a link with a
click cap) but the extra insert is what the limit is there to shed.
"""

import hashlib
import hmac
import logging
from datetime import date, datetime, timezone
from urllib.parse import urlsplit

from django.conf import settings
from django.db import transaction
from django.db.models import F, Q
from django.http import HttpRequest
from django.utils import timezone as dj_timezone

from apps.core.ratelimit import normalize_ip_for_key
from apps.core.services.client_ip import client_ip

from ..models import ClickEvent, LinkStatus, ShortLink
from . import geoip
from .useragents import parse_user_agent

logger = logging.getLogger(__name__)

MAX_REFERRER_LENGTH = 255
_HOST_CHARS = frozenset('abcdefghijklmnopqrstuvwxyz0123456789.-_:[]')


def utc_day(when: datetime) -> date:
    """The UTC calendar day of `when`: the day every rollup, hash and chart uses."""
    return when.astimezone(timezone.utc).date()


def visitor_hash(ip: str | None, day: date) -> str:
    """The keyed hash that stands for one visitor on one day: HMAC-SHA256 over the UTC day and
    the normalized address (an IPv6 address as its /64, an IPv4-mapped one as IPv4: the same
    grouping as the rate limits), keyed with `SECRET_KEY`. The day is part of the message, so the
    same address hashes differently tomorrow: uniques can be counted per day, and nothing links a
    visitor across days or lets anyone without the key recover or look up an address."""
    key = b'short_url.visitor:' + settings.SECRET_KEY.encode()
    message = f'{day.isoformat()}|{normalize_ip_for_key(ip) if ip else ""}'.encode()
    return hmac.new(key, message, hashlib.sha256).hexdigest()


def _own_hosts(request: HttpRequest) -> set[str]:
    hosts = {(urlsplit(f'//{request.get_host()}').hostname or '').lower()}
    base = urlsplit(settings.BASE_URL).hostname
    if base:
        hosts.add(base.lower())
    return hosts


def referrer_host(request: HttpRequest) -> str:
    """The host of the `Referer` header, lower-cased and without `www.` or a port, or `''` for
    none, one that isn't http(s), one that is this site itself (a visit through the password page
    or the preview page), or anything that doesn't look like a host. Never the path or query."""
    raw = request.META.get('HTTP_REFERER', '')
    if not raw or len(raw) > 2048:
        return ''
    try:
        parts = urlsplit(raw)
        host = (parts.hostname or '').lower().rstrip('.')
    except ValueError:
        return ''
    if parts.scheme not in ('http', 'https') or not host:
        return ''
    if host in _own_hosts(request):
        return ''
    host = host.removeprefix('www.')
    if not host or len(host) > MAX_REFERRER_LENGTH or not set(host) <= _HOST_CHARS:
        return ''
    return host


def record(link: ShortLink, request: HttpRequest, *, with_event: bool = True) -> bool:
    """Count one click on `link`, atomically, and (unless `with_event` is False) record its
    event. Returns False when the link stopped being available between the caller's read and
    now: it was disabled, or the cap was reached by clicks racing this one (the cap is part of
    the same `UPDATE`). A failure to write the event is logged and swallowed: the visitor is
    redirected and the counters are right, only that one analytics row is lost."""
    agent = parse_user_agent(request.META.get('HTTP_USER_AGENT'))
    # A bot's click is the headline's only when the owner chose to count bots.
    counts_for_headline = not (agent.is_bot and link.filter_bots)
    now = dj_timezone.now()

    changes: dict[str, object] = {}
    if counts_for_headline:
        changes['click_count'] = F('click_count') + 1
        changes['last_clicked_at'] = now
    if agent.is_bot:
        changes['bot_click_count'] = F('bot_click_count') + 1

    event = None
    if with_event:
        ip = client_ip(request)
        event = ClickEvent(
            link_id=link.pk,
            at=now,
            ip_hash=visitor_hash(ip, utc_day(now)),
            referrer_host=referrer_host(request),
            country=geoip.country_code(ip),
            device=agent.device,
            browser=agent.browser,
            os=agent.os,
            is_bot=agent.is_bot,
        )

    under_cap = Q(max_clicks__isnull=True) | Q(click_count__lt=F('max_clicks'))
    with transaction.atomic():
        updated = ShortLink.objects.filter(under_cap, pk=link.pk, status=LinkStatus.ACTIVE).update(
            **changes
        )
        if not updated:
            return False
        if event is not None:
            try:
                with transaction.atomic():
                    event.save(force_insert=True)
            except Exception:
                logger.exception('Could not record the click event for link %s', link.pk)
    return True
