"""The one pill: a small rounded label with an optional icon, tinted by tone (issue #214).

Every status, chip and badge in the product pages (receipt status, vendor chip, link state, due date,
priority, repeat) is this shape, so they read as one family. Tinted fill, a darker shade of the same
colour as the text, a leading icon when the state needs more than colour to be understood. Contrast
(WCAG 2.x): the `-700` text on the `-50` tint is at least 4.5:1 in the light theme; the `-300` text on a
10% tint over `gray-800` is 7:1 or better in the dark one (docs/ui-design/README.md, "Status pills").

From a template: `{% load core_ui %}{% pill 'green' 'Processed' icon='fas fa-check' %}`. From a Python
helper that already builds its own HTML (the products' `*_ui` tags): `pill_html(...)`.
"""

from django import template
from django.utils.html import format_html
from django.utils.safestring import SafeString

register = template.Library()

#: tone -> the fill and text classes, each with its dark twin.
TONES = {
    'green': 'bg-green-50 text-green-700 dark:bg-green-400/10 dark:text-green-300',
    'amber': 'bg-amber-50 text-amber-700 dark:bg-amber-400/10 dark:text-amber-300',
    'blue': 'bg-blue-50 text-blue-700 dark:bg-blue-400/10 dark:text-blue-300',
    'red': 'bg-red-50 text-red-700 dark:bg-red-400/10 dark:text-red-300',
    'sky': 'bg-sky-50 text-sky-700 dark:bg-sky-400/10 dark:text-sky-300',
    'gray': 'bg-gray-100 text-gray-600 dark:bg-gray-700 dark:text-gray-300',
}

#: The shape every pill shares.
BASE = 'inline-flex items-center whitespace-nowrap rounded-full px-2 py-0.5 text-xs font-medium'


def pill_classes(tone: str, extra: str = '') -> str:
    """The classes of a pill, for an element a template builds itself (a tab count, a tag)."""
    classes = f'{BASE} {TONES[tone]}'
    return f'{classes} {extra}'.strip()


def pill_html(
    tone: str,
    label: str,
    icon: str = '',
    title: str = '',
    sr_only: str = '',
    extra: str = '',
) -> SafeString:
    """A pill as HTML. `icon` is Font Awesome classes (`fas fa-check`; a state icon is solid);
    `title` the native tooltip; `sr_only` text only a screen reader gets (" (overdue)"); `extra`
    more classes (`tabular-nums`)."""
    gap = 'gap-1.5' if icon else ''
    classes = pill_classes(tone, f'{gap} {extra}'.strip())
    icon_html = (
        format_html('<i class="{} text-[10px]" aria-hidden="true"></i>', icon) if icon else ''
    )
    sr_html = format_html('<span class="sr-only">{}</span>', sr_only) if sr_only else ''
    title_attr = format_html(' title="{}"', title) if title else ''
    return format_html(
        '<span class="{}"{}>{}{}{}</span>', classes, title_attr, icon_html, label, sr_html
    )


@register.simple_tag
def pill(tone: str, label: str, icon: str = '', title: str = '', extra: str = '') -> SafeString:
    return pill_html(tone, label, icon=icon, title=title, extra=extra)


@register.simple_tag
def pill_class(tone: str, extra: str = '') -> str:
    """`class="{% pill_class 'amber' %}"` for markup a template writes itself."""
    return pill_classes(tone, extra)
