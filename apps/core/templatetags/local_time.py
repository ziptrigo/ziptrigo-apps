"""`{% local_time %}`: a time shown in the visitor's own time zone.

The site keeps every time in UTC. A signed-in user's saved time zone (`User.timezone`, issue #177)
is what `|date` and `localtime()` follow on the server; this tag follows the *browser's* zone
instead, and doesn't depend on the zone that is active: it always renders a `<time>` element with
the UTC value marked "UTC" (what a visitor without JavaScript sees) that `core/js/local_time.js`,
loaded on every page by `core/base.html`, rewrites in the browser's zone. So it suits a visitor
with no saved zone too (an anonymous download page, say).

    {% load local_time %}
    {% local_time transfer.expires_at %}            Oct 27, 2026, 8:51 AM
    {% local_time receipt.created_at 'short' %}     Oct 27, 8:51 AM
    {% local_time tx.created_at 'iso' %}            2026-10-27 08:51
    {% local_time n.created_at 'iso' title=True %}  only the tooltip; the element's text is kept

With `title=True` it renders just the attribute pair to put on an element of your own, whose text
stays as it is (for a relative "3 hours ago" with the exact time as its tooltip).
"""

from datetime import UTC, datetime

from django import template
from django.utils import dateformat, timezone
from django.utils.html import format_html
from django.utils.safestring import SafeString

register = template.Library()

#: The server-side (UTC) fallback for each style; the browser's own wording replaces it.
FALLBACK_FORMATS = {'long': 'M j, Y H:i', 'short': 'M j, H:i', 'iso': 'Y-m-d H:i'}


@register.simple_tag
def local_time(value: datetime | None, style: str = 'long', title: bool = False) -> SafeString:
    if value is None:
        return SafeString('')
    if style not in FALLBACK_FORMATS:
        raise template.TemplateSyntaxError(
            f'local_time: unknown style {style!r}; use one of {", ".join(FALLBACK_FORMATS)}'
        )
    # A naive value is taken to be UTC already, like everything the site stores.
    utc = value.astimezone(UTC) if timezone.is_aware(value) else value.replace(tzinfo=UTC)
    fallback = f'{dateformat.format(utc, FALLBACK_FORMATS[style])} UTC'
    iso = utc.isoformat()
    if title:
        return format_html(
            'datetime="{}" data-local-time="{}" data-local-time-title title="{}"',
            iso,
            style,
            fallback,
        )
    return format_html('<time datetime="{}" data-local-time="{}">{}</time>', iso, style, fallback)
