"""Reusable form widgets, rendered with the site's own look (sage palette, light and dark)."""

from datetime import date, datetime, time
from typing import Any

from django import forms
from django.template.loader import render_to_string
from django.utils import timezone
from django.utils.safestring import SafeString


class DatePickerWidget(forms.Widget):
    """A date field: a text box you can type into, in your own date order (`11/05/2026` or
    `05/11/2026`; ISO is accepted too), and a calendar button that opens a picker.

    Submits one `YYYY-MM-DD` value under the field's name, so pair it with a `forms.DateField`.
    `min_today=True` greys out past days (or pass `attrs={'min': 'YYYY-MM-DD'}`); `attrs={'max':
    ...}` works the same way. The markup and behaviour are `core/components/date_picker.html` and
    `core/js/date_picker.js`, which templates can also use directly with `{% include %}`.
    """

    # Tells `StyledFormMixin` the widget brings its own styling.
    self_styled = True

    def __init__(self, attrs: dict[str, Any] | None = None, min_today: bool = False):
        super().__init__(attrs)
        self.min_today = min_today

    def render(self, name, value, attrs=None, renderer=None) -> SafeString:
        attrs = self.build_attrs(self.attrs, attrs)
        if isinstance(value, datetime):
            value = timezone.localtime(value).date() if timezone.is_aware(value) else value.date()
        min_value = attrs.get('min') or (timezone.localdate().isoformat() if self.min_today else '')
        return SafeString(
            render_to_string(
                'core/components/date_picker.html',
                {
                    'name': name,
                    'value': value.isoformat() if isinstance(value, date) else (value or ''),
                    'min': min_value,
                    'max': attrs.get('max', ''),
                    'id': attrs.get('id', ''),
                    'aria_label': attrs.get('aria-label', ''),
                    'disabled': bool(attrs.get('disabled')),
                },
            )
        )


class TimeFieldWidget(forms.Widget):
    """A time-of-day field: a text box that accepts `10pm`, `22:00`, `2200`, `10:30 p` or `9`, shows
    the time on the user's clock (`10:00 PM` or `22:00`) and offers half-hour suggestions.

    Submits one `HH:MM` value under the field's name, so pair it with a `forms.TimeField`. The
    markup and behaviour are `core/components/time_field.html` and `core/js/time_field.js`, which
    templates can also use directly with `{% include %}`.
    """

    self_styled = True

    def render(self, name, value, attrs=None, renderer=None) -> SafeString:
        attrs = self.build_attrs(self.attrs, attrs)
        if isinstance(value, datetime):
            value = timezone.localtime(value).time() if timezone.is_aware(value) else value.time()
        return SafeString(
            render_to_string(
                'core/components/time_field.html',
                {
                    'name': name,
                    'value': value.strftime('%H:%M')
                    if isinstance(value, time)
                    else (str(value)[:5] if value else ''),
                    'id': attrs.get('id', ''),
                    'aria_label': attrs.get('aria-label', ''),
                    'required': bool(attrs.get('required')),
                    'disabled': bool(attrs.get('disabled')),
                },
            )
        )
