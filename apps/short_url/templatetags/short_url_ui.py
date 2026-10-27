"""Template helpers for the Short URLs pages: the status pill and the short link's address.

Load with `{% load short_url_ui %}`.
"""

from django import template
from django.conf import settings
from django.utils.html import format_html

from ..models import ShortLink
from ..services import display_state, short_url

register = template.Library()

# text classes, border classes, icon classes: the design system's status tones
# (docs/ui-design/README.md, "Status pills"); label text is -700 on light for green and amber
# because -600 on white is below 4.5:1.
_TONES = {
    'green': (
        'text-green-700 dark:text-green-400',
        'border-green-600/40 dark:border-green-400/40',
        'text-green-600 dark:text-green-400',
    ),
    'amber': (
        'text-amber-700 dark:text-amber-400',
        'border-amber-600/40 dark:border-amber-400/40',
        'text-amber-600 dark:text-amber-400',
    ),
    'red': (
        'text-red-600 dark:text-red-400',
        'border-red-600/40 dark:border-red-400/40',
        'text-red-600 dark:text-red-400',
    ),
    'gray': (
        'text-gray-600 dark:text-gray-400',
        'border-gray-400/60 dark:border-gray-500/60',
        'text-gray-500 dark:text-gray-400',
    ),
}

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
    text, border, icon_color = _TONES[tone]
    return format_html(
        '<span class="inline-flex items-center gap-1.5 whitespace-nowrap rounded-full border '
        '{} {} px-2 py-0.5 text-xs font-medium">'
        '<i class="{} {}" aria-hidden="true"></i>{}</span>',
        text,
        border,
        icon,
        icon_color,
        label,
    )


@register.simple_tag
def short_link_url(link: ShortLink) -> str:
    """The public address of the link, `<BASE_URL>/s/<code>`."""
    return short_url(link.code)


@register.simple_tag
def short_link_path(link: ShortLink) -> str:
    """The link's path on the site, `/s/<code>`: what a row shows (the host is the site's own)."""
    return f'{settings.SHORT_URL_PATH}{link.code}'
