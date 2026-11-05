"""Template helpers for the account page's date and time format dropdowns (issue #245)."""

from django import template

from apps.core.templatetags.datetime_format import sample_date, sample_time

from ..models import User

register = template.Library()


@register.simple_tag
def date_format_options() -> list[dict[str, str]]:
    """The date orders for `core/components/select.html`: each with today's date in it as the hint."""
    return [
        {'value': value, 'label': label, 'hint': sample_date(value)}
        for value, label in User.DATE_FORMAT_CHOICES
    ]


@register.simple_tag
def time_format_options() -> list[dict[str, str]]:
    """The clocks for `core/components/select.html`: each with 9:05 PM as it reads in it as the hint."""
    return [
        {'value': value, 'label': label, 'hint': sample_time(value)}
        for value, label in User.TIME_FORMAT_CHOICES
    ]
