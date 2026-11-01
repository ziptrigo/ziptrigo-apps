"""Template helpers for the To-do pages: the rendered description, the due badge, the priority
flag and the label chip.

Load with `{% load todo_ui %}`.
"""

import colorsys
import logging
import re
from collections.abc import Sequence
from datetime import date, timedelta

from django import template
from django.template.defaultfilters import linebreaksbr
from django.utils.html import format_html
from django.utils.safestring import SafeString

from apps.accounts.services.timezones import user_today
from apps.core.markdown import render
from apps.core.templatetags.core_ui import pill_html

from ..models import Label, Task
from ..services.recurrence import MONTH_NAMES, WEEKDAY_NAMES, describe, mode_label
from ..services.references import TASK_URL_PATH, resolver_for_request

logger = logging.getLogger(__name__)

register = template.Library()

#: Priority -> pill tone (red, amber, sky).
_FLAG_TONES = {1: 'red', 2: 'amber', 3: 'sky'}


@register.simple_tag(takes_context=True)
def render_description(context, task: Task) -> SafeString:
    """The task's description as sanitised HTML; `#1234` and links to the user's own task pages
    become links to them. A description over the Markdown size guard (the services cap far below
    it) is shown unrendered rather than failing the page."""
    resolver = resolver_for_request(context['request'])
    try:
        return render(task.description, reference_resolver=resolver, task_url_path=TASK_URL_PATH)
    except ValueError:
        logger.warning('Task %s: description shown unrendered', task.pk)
        return format_html('<p>{}</p>', linebreaksbr(task.description, autoescape=True))


def due_text(due: date, today: date) -> str:
    """`Today`, `Tomorrow`, the weekday for the next 6 days, else `5 Nov` (the year added when it
    isn't this year's)."""
    delta = (due - today).days  # negative: overdue, shown as a date
    if delta == 0:
        return 'Today'
    if delta == 1:
        return 'Tomorrow'
    if 2 <= delta <= 6:
        return due.strftime('%A')
    text = f'{due.day} {due.strftime("%b")}'
    return text if due.year == today.year else f'{text} {due.year}'


@register.simple_tag(takes_context=True)
def due_badge(context, task: Task) -> SafeString:
    """The due date as a badge: red when overdue, with the time when the task has one."""
    if task.due_date is None:
        return SafeString('')
    today = context.get('today') or user_today(context['request'].user)
    overdue = task.is_overdue(today)
    text = due_text(task.due_date, today)
    tone = 'red' if overdue else 'green' if text == 'Today' else 'gray'
    time = f' {task.due_time:%H:%M}' if task.due_time is not None else ''
    return pill_html(
        tone,
        f'{text}{time}',
        icon='far fa-calendar',
        title=f'Due {task.due_date.isoformat()}{time}',
        sr_only=' (overdue)' if overdue else '',
    )


@register.simple_tag
def priority_flag(task: Task) -> SafeString:
    """A pill for priority 1, 2 or 3 (`P1`, with a flag); nothing for none."""
    tone = _FLAG_TONES.get(task.priority)
    if tone is None:
        return SafeString('')
    return pill_html(
        tone,
        f'P{task.priority}',
        icon='fas fa-flag',
        title=f'Priority {task.priority}',
        sr_only=f' (priority {task.priority})',
    )


#: The card the chips sit on in dark mode (Tailwind `gray-800`), and the contrast WCAG asks of text.
_DARK_CARD = (0x28, 0x2E, 0x2C)
_MIN_CONTRAST = 4.5
_HEX_COLOR = re.compile(r'#[0-9a-fA-F]{6}')


def _channel(value: float) -> float:
    value /= 255
    return value / 12.92 if value <= 0.03928 else ((value + 0.055) / 1.055) ** 2.4


def _luminance(rgb: Sequence[float]) -> float:
    red, green, blue = (_channel(part) for part in rgb)
    return 0.2126 * red + 0.7152 * green + 0.0722 * blue


def _contrast(first: Sequence[float], second: Sequence[float]) -> float:
    lighter, darker = sorted((_luminance(first), _luminance(second)), reverse=True)
    return (lighter + 0.05) / (darker + 0.05)


def dark_mode_color(color: str) -> str:
    """`color` (`#rrggbb`) lightened, keeping its hue, until it reads on the dark card with the chip's
    own 20% tint on it (contrast of at least 4.5:1); a colour that already does is returned as it is."""
    if not _HEX_COLOR.fullmatch(color):  # the model validates it; a stray value is left alone
        return color
    red, green, blue = (int(color[index : index + 2], 16) for index in (1, 3, 5))
    background = tuple(
        0.8 * card + 0.2 * part for card, part in zip(_DARK_CARD, (red, green, blue))
    )
    hue, lightness, saturation = colorsys.rgb_to_hls(red / 255, green / 255, blue / 255)
    candidate = (red, green, blue)
    while _contrast(candidate, background) < _MIN_CONTRAST and lightness < 1:
        lightness = min(lightness + 0.02, 1)
        candidate = tuple(
            round(part * 255) for part in colorsys.hls_to_rgb(hue, lightness, saturation)
        )
    return '#{:02x}{:02x}{:02x}'.format(*candidate)


@register.simple_tag
def label_chip(label: Label) -> SafeString:
    """The label as a chip: its colour at 20% as the background and as the text. In dark mode the text
    is lightened where the raw colour would be unreadable on the dark card (`#0000ff` is about 1.7:1):
    `--chip-dark`, which the layout's CSS applies under `.dark`. The description is the native tooltip
    (`title`) and nothing else."""
    return format_html(
        '<span class="label-chip inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium"'
        ' style="background-color: {}33; color: {}; --chip-dark: {}"{}>{}</span>',
        label.color,
        label.color,
        dark_mode_color(label.color),
        format_html(' title="{}"', label.description) if label.description else '',
        label.name,
    )


@register.simple_tag
def repeat_choices() -> dict[str, list[tuple]]:
    """The weekdays (RRULE code, name) and months (number, name) the Repeat control lists."""
    return {
        'weekdays': list(WEEKDAY_NAMES.items()),
        'months': list(enumerate(MONTH_NAMES, start=1)),
    }


@register.simple_tag
def repeat_chip(task: Task, detail: bool = False) -> SafeString:
    """The repeat rule of a task that is still repeating as a chip: the sentence (`Every month on
    the 1st`) and the mode, whose meaning is the tooltip. A row shows the mode only for "from
    completion"; `detail` (the task page) always does. Nothing for a task that does not repeat."""
    recurrence = task.active_recurrence
    if recurrence is None:
        return SafeString('')
    label = mode_label(recurrence.mode)
    show_mode = detail or recurrence.mode == 'from_completion'
    text = format_html(
        '{}{}',
        describe(recurrence.rrule),
        format_html(' <span class="font-normal opacity-80">({})</span>', label.lower())
        if show_mode
        else '',
    )
    return pill_html('gray', text, icon='fas fa-repeat', title=label)


@register.simple_tag
def day_heading(day: date, today: date) -> str:
    """`Tomorrow`, `Wednesday 4 Nov`, ... for the Upcoming page's day headings."""
    if day == today + timedelta(days=1):
        return f'Tomorrow, {day.day} {day.strftime("%b")}'
    return f'{day.strftime("%A")} {day.day} {day.strftime("%b")}'
