from django import forms

from ..models import ReminderChannel
from ..services.reminders import UNIT_MINUTES

#: The presets of the "Add reminder" row: minutes before the due time (`custom` uses the amount).
PRESETS = (
    ('0', 'At time of task'),
    ('5', '5 minutes before'),
    ('15', '15 minutes before'),
    ('60', '1 hour before'),
    ('1440', '1 day before'),
    ('10080', '1 week before'),
    ('custom', 'Custom...'),
)
UNITS = (('minutes', 'minutes'), ('hours', 'hours'), ('days', 'days'))


def to_minutes(amount: int, unit: str) -> int:
    return amount * UNIT_MINUTES[unit]


class ReminderForm(forms.Form):
    """Adding or editing a reminder: a time (a preset, or an amount and a unit) and a channel. It
    gathers and parses; the range, duplicates and cap are `services.reminders`'. `minutes_before`
    (set by `clean`) is what the service takes."""

    preset = forms.ChoiceField(
        label='Reminder',
        required=False,
        choices=PRESETS,
        error_messages={'invalid_choice': 'Choose when to remind you.'},
    )
    amount = forms.IntegerField(
        label='Amount',
        required=False,
        min_value=0,
        max_value=1_000_000,
        error_messages={'invalid': 'Enter a whole number.', 'min_value': 'Enter 0 or more.'},
    )
    unit = forms.ChoiceField(
        label='Unit',
        required=False,
        choices=UNITS,
        error_messages={'invalid_choice': 'Choose minutes, hours or days.'},
    )
    channel = forms.ChoiceField(
        label='Channel',
        required=False,
        choices=ReminderChannel.choices,
        error_messages={'invalid_choice': 'Choose in-app, email or both.'},
    )

    minutes_before: int | None = None

    def clean(self) -> dict:
        cleaned = super().clean() or {}
        if self.errors:
            return cleaned
        preset = cleaned.get('preset') or ''
        if preset and preset != 'custom':
            self.minutes_before = int(preset)
        elif cleaned.get('amount') is None:
            self.add_error('amount', 'Enter an amount.')
        else:
            self.minutes_before = to_minutes(cleaned['amount'], cleaned.get('unit') or 'minutes')
        return cleaned
