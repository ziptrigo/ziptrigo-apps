"""Reading a visitor's `User-Agent`: device class, browser and OS family, and whether it is a bot.

Parsing is `ua-parser`'s (its regex database). What it does not decide for us, this module does:

- **Bots.** `ua-parser` calls crawlers and most link-preview fetchers "Spider" devices; on top of
  that `BOT_PATTERN` lists the fetchers it misses (the chat apps, security scanners and plain HTTP
  libraries that open a link the moment it is pasted, which would otherwise count as a visit) and
  the generic words crawlers put in their names. A request with no `User-Agent` at all is treated
  as a bot as well: browsers always send one. The list errs on the side of letting a doubtful
  agent count as a person; it is a filter for statistics, not a security control (a `User-Agent`
  is whatever the client says it is).
- **Device class.** `desktop`, `mobile`, `tablet` or `bot`, from the platform tokens browsers
  send (an Android UA without `Mobile` is a tablet, as Chrome documents it).
"""

import re
from dataclasses import dataclass

from ua_parser import parse

from ..models import Device

#: A header longer than this is cut before parsing: real agents are 100-300 characters, and the
#: regex database is not worth running over kilobytes an attacker chose.
MAX_UA_LENGTH = 512

#: Link-preview fetchers, security scanners, monitors and HTTP libraries, plus the generic words
#: crawlers use. Matched case-insensitively anywhere in the header.
BOT_PATTERN = re.compile(
    r'(?:'
    # Generic: "...bot/1.0", "bot;", "crawler", "spider" ("Cubot" is a phone brand, not a crawler).
    r'(?<!cu)bot(?:\b|[/ ;)_-])|crawl|spider|slurp|scrap(?:er|y)|fetcher|'
    # Link previews: what a chat or social app fetches when a link is pasted.
    # Only crawler tokens, never a bare brand: the DuckDuckGo, Pinterest, Flipboard, Tumblr and
    # Yandex apps all have in-app browsers whose agents carry the brand name. WhatsApp's and
    # Tumblr's fetchers send just `WhatsApp/x.y` / `Tumblr/x.y` as the whole header, so those two
    # are matched at the start only (`DuckDuckBot` and `Pinterestbot` are caught by "bot" above).
    r'slackbot|slack-imgproxy|twitterbot|facebookexternalhit|facebot|^whatsapp/|discordbot|'
    r'telegrambot|linkedinbot|skypeuripreview|embedly|quora link preview|redditbot|'
    r'vkshare|flipboardproxy|flipboardrss|^tumblr/|google-safety|google-inspectiontool|'
    r'googleother|apis-google|mediapartners|bingpreview|'
    # Search and SEO engines that do not say "bot" (YandexBot and the rest of its "bots" are
    # caught above; the Yandex browser and search app are not crawlers).
    r'yandex(?:images|direct|video|favicons|metrika|market|news|blogs|pagechecker)|'
    r'baiduspider|applebot|petalbot|semrush|ahrefs|'
    # Monitors, headless browsers, scanners and HTTP libraries.
    r'headlesschrome|phantomjs|lighthouse|pingdom|uptimerobot|statuscake|datadog|'
    r'python-requests|python-urllib|aiohttp|httpx|go-http-client|java/|apache-httpclient|'
    r'libwww|node-fetch|axios|curl/|wget/|postmanruntime|'
    r'w3c_validator|link.?check|virustotal|urlscan|safelinks|mimecast|proofpoint|barracuda'
    r')',
    re.IGNORECASE,
)

_TABLET = re.compile(r'iPad|Tablet|PlayBook|Silk/|Kindle|Nexus 7|Nexus 9|SM-T\d', re.IGNORECASE)
_MOBILE = re.compile(r'Mobi|iPhone|iPod|Windows Phone|BlackBerry|Opera Mini', re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class ParsedAgent:
    device: str
    browser: str
    os: str
    is_bot: bool


def _family(value: str | None) -> str:
    """A browser or OS family as stored: `Mobile Safari` and `Chrome Mobile` are `Safari` and
    `Chrome` (the device class already says "mobile"), nothing known is `Other`."""
    value = (value or '').strip()
    if value.startswith('Mobile '):
        value = value[len('Mobile ') :]
    elif value.endswith(' Mobile'):
        value = value[: -len(' Mobile')]
    return (value or 'Other')[:64]


def _device_class(ua: str) -> str:
    if _TABLET.search(ua):
        return Device.TABLET
    if 'Android' in ua and 'Mobile' not in ua:
        return Device.TABLET
    if _MOBILE.search(ua) or 'Android' in ua:
        return Device.MOBILE
    return Device.DESKTOP


def parse_user_agent(header: str | None) -> ParsedAgent:
    """Classify a `User-Agent` header (None or empty is a bot)."""
    ua = (header or '').strip()[:MAX_UA_LENGTH]
    if not ua:
        return ParsedAgent(Device.BOT, 'Other', 'Other', True)
    result = parse(ua)
    device = result.device
    is_bot = bool(BOT_PATTERN.search(ua)) or (device is not None and device.family == 'Spider')
    browser = _family(result.user_agent.family if result.user_agent else None)
    os = _family(result.os.family if result.os else None)
    if is_bot:
        return ParsedAgent(Device.BOT, browser, os, True)
    return ParsedAgent(_device_class(ua), browser, os, False)
