"""Template helpers for the To-do pages: the rendered description, the due badge, the priority
flag, the state and resolution pills and the label chip.

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
from apps.core import datetime_format
from apps.core.datetime_format import DisplayFormats, format_date, format_time
from apps.core.markdown import render
from apps.core.templatetags.core_ui import pill_html

from ..models import SELECTABLE_STATES, Label, Resolution, Task, TaskState
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


def due_text(due: date, today: date, formats: DisplayFormats | None = None) -> str:
    """`Today`, `Tomorrow`, the weekday for the next 6 days, else the date in the viewer's format
    (`11/05`, the year added when it isn't this year's; `Nov 5` on a public page)."""
    delta = (due - today).days  # negative: overdue, shown as a date
    if delta == 0:
        return 'Today'
    if delta == 1:
        return 'Tomorrow'
    if 2 <= delta <= 6:
        return datetime_format.WEEKDAY_NAMES[due.weekday()]
    return format_date(due, 'compact', formats, today=today)


def is_word_label(due: date, today: date) -> bool:
    """Whether `due_text` wrote a word (Today, Tomorrow, a weekday) rather than a date."""
    return 0 <= (due - today).days <= 6


@register.simple_tag(takes_context=True)
def due_badge(context, task: Task) -> SafeString:
    """The due date as a badge: red when overdue, with the time when the task has one."""
    if task.due_date is None:
        return SafeString('')
    today = context.get('today') or user_today(context['request'].user)
    overdue = task.is_overdue(today)
    text = due_text(task.due_date, today)
    tone = 'red' if overdue else 'green' if text == 'Today' else 'gray'
    due_time = format_time(task.due_time) if task.due_time is not None else ''
    # A word and a time read as one phrase ("Today 9:05 PM"); a date is set off with a comma.
    joiner = ' ' if is_word_label(task.due_date, today) else ', '
    title = format_date(task.due_date)
    if due_time:
        title = f'{title}, {due_time}'
    return pill_html(
        tone,
        f'{text}{joiner}{due_time}' if due_time else text,
        icon='far fa-calendar',
        title=f'Due {title}',
        sr_only=' (overdue)' if overdue else '',
    )


#: State -> (pill tone, icon) of the state chip: To do is the plain one, In progress stands out,
#: On hold and Blocked (muted) are grey like the dimmed row.
_STATE_STYLE = {
    TaskState.TODO: ('gray', 'far fa-circle'),
    TaskState.IN_PROGRESS: ('sky', 'fas fa-circle-half-stroke'),
    TaskState.BLOCKED: ('gray', 'fas fa-ban'),
    TaskState.ON_HOLD: ('gray', 'fas fa-pause'),
}
#: How many open blockers a Blocked chip's tooltip names before "and N more".
_TOOLTIP_BLOCKERS = 5


def blockers_text(task: Task) -> str:
    """`#1200 Pay rent, #1201 Call` for the task's open blockers (the first few, then `and N more`):
    what a Blocked chip's tooltip and the close question say it waits for."""
    blockers = task.open_blockers
    names = [f'#{blocker.number} {blocker.title}' for blocker in blockers[:_TOOLTIP_BLOCKERS]]
    text = ', '.join(names)
    if len(blockers) > _TOOLTIP_BLOCKERS:
        text += f' and {len(blockers) - _TOOLTIP_BLOCKERS} more'
    return text


@register.simple_tag
def state_pill(task: Task) -> SafeString:
    """The state of an open task as a pill (`To do`, `In progress`, `Blocked`, `On hold`). A Blocked
    one says in its tooltip which open tasks it waits for."""
    tone, icon = _STATE_STYLE.get(task.state, _STATE_STYLE[TaskState.TODO])
    label = TaskState(task.state).label if task.state in TaskState.values else 'To do'
    title = 'State'
    if task.state == TaskState.BLOCKED and (names := blockers_text(task)):
        title = f'Blocked by {names}'
    return pill_html(tone, label, icon=icon, title=title)


@register.simple_tag
def state_choices() -> list[dict]:
    """The states a person can pick, with the icon each has in its pill: what the state menus
    list. Blocked is not one: only a blocker sets it."""
    return [
        {'value': state.value, 'label': state.label, 'icon': _STATE_STYLE[state][1]}
        for state in SELECTABLE_STATES
    ]


@register.simple_tag
def blocker_marker(task: Task) -> SafeString:
    """What a task in a "Blocked by" / "Blocks" list is, when it is not simply open: `Done`, `Won't do`
    or `In the trash`. Nothing for an open task."""
    if task.is_trashed:
        return pill_html('red', 'In the trash', icon='fas fa-trash')
    if task.is_wont_do:
        return pill_html('gray', "Won't do", icon='fas fa-circle-xmark')
    if task.is_completed:
        return pill_html('green', 'Done', icon='fas fa-check')
    return SafeString('')


@register.simple_tag
def resolution_pill(task: Task, always: bool = False) -> SafeString:
    """How a closed task was closed: a `Won't do` pill, and a `Done` one when `always` (a Done row
    already has its tick). Nothing for an open task."""
    if task.completed_at is None:
        return SafeString('')
    if task.resolution == Resolution.WONT_DO:
        return pill_html('gray', "Won't do", icon='fas fa-circle-xmark', title="Closed as won't do")
    if always:
        return pill_html('green', 'Done', icon='fas fa-check', title='Closed as done')
    return SafeString('')


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
    """`Tomorrow, 11/05`, `Wednesday, 11/04`, ... for the Upcoming page's day headings."""
    if day == today + timedelta(days=1):
        return f'Tomorrow, {format_date(day, "compact", today=today)}'
    return format_date(day, 'heading', today=today)
