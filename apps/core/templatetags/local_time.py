"""`{% local_time %}`: a time shown in the visitor's own time zone.

The site keeps every time in UTC. A signed-in user's saved time zone (`User.timezone`, issue #177)
is what `|date` and `localtime()` follow on the server; this tag follows the *browser's* zone
instead, and doesn't depend on the zone that is active: it always renders a `<time>` element with
the UTC value marked "UTC" (what a visitor without JavaScript sees) that `core/js/local_time.js`,
loaded on every page by `core/base.html`, rewrites in the browser's zone. So it suits a visitor
with no saved zone too (an anonymous download page, say).

The *wording* is the site's, not the browser's locale (issue #245): the active display formats
(`apps.core.datetime_format`) decide the date order and the clock, on the server for the fallback
and, through `data-date-format` / `data-time-format`, in the script: the signed-in user's own
order and clock, or `Nov 5, 2026, 9:05 PM` on a public page.

    {% load local_time %}
    {% local_time transfer.expires_at %}            11/05/2026, 9:05 PM  (Nov 5, 2026, 9:05 PM public)
    {% local_time receipt.created_at 'short' %}     11/05, 9:05 PM       (the year when it isn't this year's)
    {% local_time tx.created_at 'iso' %}            2026-11-05 9:05 PM
    {% local_time n.created_at 'iso' title=True %}  only the tooltip; the element's text is kept

With `title=True` it renders just the attribute pair to put on an element of your own, whose text
stays as it is (for a relative "3 hours ago" with the exact time as its tooltip).
"""

from datetime import UTC, datetime

from django import template
from django.utils import timezone
from django.utils.html import format_html
from django.utils.safestring import SafeString

from apps.core import datetime_format

register = template.Library()

#: The styles, and the `datetime_format` date style each one is written in (`iso` is its own).
STYLES = {'long': 'full', 'short': 'compact', 'iso': 'iso'}


def fallback_text(utc: datetime, style: str, formats: datetime_format.DisplayFormats) -> str:
    """The server-side (UTC) text for `style`; the browser's own zone replaces it."""
    if style == 'iso':
        text = f'{utc:%Y-%m-%d} {datetime_format.format_time(utc, formats)}'
    else:
        text = datetime_format.format_datetime(
            utc, STYLES[style], formats, today=timezone.now().astimezone(UTC).date()
        )
    return f'{text} UTC'


@register.simple_tag
def local_time(value: datetime | None, style: str = 'long', title: bool = False) -> SafeString:
    if value is None:
        return SafeString('')
    if style not in STYLES:
        raise template.TemplateSyntaxError(
            f'local_time: unknown style {style!r}; use one of {", ".join(STYLES)}'
        )
    # A naive value is taken to be UTC already, like everything the site stores.
    utc = value.astimezone(UTC) if timezone.is_aware(value) else value.replace(tzinfo=UTC)
    formats = datetime_format.get_active()
    # The fallback is rendered from the UTC value, with no zone conversion.
    fallback = fallback_text(utc.replace(tzinfo=None), style, formats)
    iso = utc.isoformat()
    attrs = (iso, style, formats.date_order, formats.clock)
    if title:
        return format_html(
            'datetime="{}" data-local-time="{}" data-date-format="{}" data-time-format="{}"'
            ' data-local-time-title title="{}"',
            *attrs,
            fallback,
        )
    return format_html(
        '<time datetime="{}" data-local-time="{}" data-date-format="{}" data-time-format="{}">{}</time>',
        *attrs,
        fallback,
    )
