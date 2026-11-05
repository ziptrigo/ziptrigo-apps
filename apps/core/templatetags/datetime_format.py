"""Template filters for the site's date and time wording (issue #245, `apps.core.datetime_format`).

    {% load datetime_format %}
    {{ task.due_date|fdate }}                 11/05/2026  (or 05/11/2026, or Nov 5, 2026 on a public page)
    {{ task.due_date|fdate:'compact' }}       11/05       (the year only when it isn't this year's)
    {{ task.due_time|ftime }}                 9:05 PM     (or 21:05)
    {{ receipt.created_at|fdatetime }}        11/05/2026, 9:05 PM

They follow the formats active for the request, so a page renders in the signed-in user's choice
and a public page in the fixed public wording. `{% format_data_attrs %}` writes the
`data-date-format` / `data-time-format` attributes the browser scripts read.
"""

from datetime import date, datetime, time

from django import template
from django.utils import timezone
from django.utils.html import format_html
from django.utils.safestring import SafeString

from apps.core import datetime_format

register = template.Library()


def _check_style(style: str) -> str:
    if style not in datetime_format.STYLES:
        raise template.TemplateSyntaxError(
            f'Unknown date style {style!r}; use one of {", ".join(datetime_format.STYLES)}'
        )
    return style


@register.filter
def fdate(value: date | datetime | None, style: str = 'full') -> str:
    return datetime_format.format_date(value, _check_style(style))


@register.filter
def ftime(value: time | datetime | None) -> str:
    return datetime_format.format_time(value)


@register.filter
def fdatetime(value: datetime | None, style: str = 'full') -> str:
    return datetime_format.format_datetime(value, _check_style(style))


@register.simple_tag
def format_data_attrs() -> SafeString:
    """`data-date-format="mdy|dmy|public" data-time-format="12h|24h"` for the active formats."""
    active = datetime_format.get_active()
    return format_html(
        'data-date-format="{}" data-time-format="{}"', active.date_order, active.clock
    )


@register.simple_tag
def sample_date(order: str) -> str:
    """Today's date written in `order` (`mdy` or `dmy`), for the examples on the account page."""
    if order not in datetime_format.DATE_ORDERS:
        raise template.TemplateSyntaxError(f'Unknown date order {order!r}')
    return datetime_format.format_date(
        timezone.localdate(),
        'full',
        datetime_format.DisplayFormats(order, datetime_format.CLOCK_12H),
    )


@register.simple_tag
def sample_time(clock: str) -> str:
    """9:05 PM written on `clock` (`12h` or `24h`), for the examples on the account page."""
    if clock not in datetime_format.CLOCKS:
        raise template.TemplateSyntaxError(f'Unknown clock {clock!r}')
    return datetime_format.format_time(
        time(21, 5), datetime_format.DisplayFormats(datetime_format.MDY, clock)
    )


# -- The date and time fields (core/components/date_picker.html, time_field.html) ------------------


def _numeric_formats() -> datetime_format.DisplayFormats:
    """The active formats with a numeric date order: a field on a public page has no account to
    take the order from, so it is typed in `mdy`."""
    active = datetime_format.get_active()
    return datetime_format.DisplayFormats(active.numeric_order, active.clock)


@register.simple_tag
def field_date_text(value: object) -> str:
    """An ISO `YYYY-MM-DD` value (or a date) as the date field's text box shows it."""
    if isinstance(value, str):
        try:
            value = date.fromisoformat(value)
        except ValueError:
            return ''
    if not isinstance(value, date):
        return ''
    return datetime_format.format_date(value, 'full', _numeric_formats())


@register.simple_tag
def field_date_placeholder() -> str:
    return 'dd/mm/yyyy' if _numeric_formats().date_order == datetime_format.DMY else 'mm/dd/yyyy'


@register.simple_tag
def field_time_text(value: object) -> str:
    """An ISO `HH:MM` value (or a time) as the time field's text box shows it."""
    if isinstance(value, str):
        try:
            value = time.fromisoformat(value)
        except ValueError:
            return ''
    if not isinstance(value, time):
        return ''
    return datetime_format.format_time(value, _numeric_formats())


@register.simple_tag
def field_time_placeholder() -> str:
    return '21:00' if _numeric_formats().clock == datetime_format.CLOCK_24H else '9:00 PM'
