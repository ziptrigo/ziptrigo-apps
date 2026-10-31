"""Template helpers for the Short URLs pages: the status pill and the short link's address.

Load with `{% load short_url_ui %}`.
"""

from django import template
from django.conf import settings

from apps.core.templatetags.core_ui import pill_html

from ..models import ShortLink
from ..services import display_state, short_url

register = template.Library()

#: `display_state` -> label, Font Awesome icon, tone.
_PILLS = {
    'active': ('Active', 'fas fa-check', 'green'),
    'disabled': ('Disabled', 'fas fa-pause', 'gray'),
    'expired': ('Expired', 'fas fa-clock', 'amber'),
    'capped': ('Click limit reached', 'fas fa-hand', 'amber'),
    'held': ('Under review', 'fas fa-flag', 'amber'),
    'blocked': ('Not working', 'fas fa-ban', 'amber'),
    'taken_down': ('Taken down', 'fas fa-ban', 'red'),
    'deleted': ('Deleted', 'fas fa-trash', 'gray'),
}


@register.simple_tag
def link_status_pill(link: ShortLink, state: str | None = None) -> str:
    """The coloured pill for what the link is doing right now (active, disabled, expired, ...).
    `state` is a `display_state` the caller already worked out (the link's own page also knows
    whether its destination is blocked); without it the pill works it out."""
    label, icon, tone = _PILLS[state or display_state(link)]
    return pill_html(tone, label, icon=icon)


@register.simple_tag
def short_link_url(link: ShortLink) -> str:
    """The public address of the link, `<BASE_URL>/s/<code>`."""
    return short_url(link.code)


@register.simple_tag
def short_link_path(link: ShortLink) -> str:
    """The link's path on the site, `/s/<code>`: what a row shows (the host is the site's own)."""
    return f'{settings.SHORT_URL_PATH}{link.code}'
