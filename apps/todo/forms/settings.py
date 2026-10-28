from django import forms

from ..models import ReminderChannel
from .reminder import UNITS, to_minutes


class SettingsForm(forms.Form):
    default_due_time = forms.TimeField(
        label='Default due time',
        error_messages={'required': 'Enter a time.', 'invalid': 'Enter a valid time.'},
    )
    # The default reminder. A request without `default_reminder_mode` leaves it alone (a form that
    # only sets the due time), so `reminder_defaults` is then None.
    default_reminder_mode = forms.ChoiceField(
        label='Default reminder',
        required=False,
        choices=(('none', 'No reminder'), ('set', 'Remind me')),
        error_messages={'invalid_choice': 'Choose whether new tasks get a reminder.'},
    )
    default_reminder_amount = forms.IntegerField(
        label='Default reminder',
        required=False,
        min_value=0,
        max_value=1_000_000,
        error_messages={'invalid': 'Enter a whole number.', 'min_value': 'Enter 0 or more.'},
    )
    default_reminder_unit = forms.ChoiceField(
        label='Default reminder unit',
        required=False,
        choices=UNITS,
        error_messages={'invalid_choice': 'Choose minutes, hours or days.'},
    )
    default_reminder_channel = forms.ChoiceField(
        label='Default channel',
        required=False,
        choices=ReminderChannel.choices,
        error_messages={'invalid_choice': 'Choose in-app, email or both.'},
    )

    #: `(minutes or None, channel)` to save, or None to leave the default reminder as it is.
    reminder_defaults: tuple[int | None, str] | None = None

    def clean(self) -> dict:
        cleaned = super().clean() or {}
        if self.errors or not cleaned.get('default_reminder_mode'):
            return cleaned
        minutes: int | None = None
        if cleaned['default_reminder_mode'] == 'set':
            if cleaned.get('default_reminder_amount') is None:
                self.add_error('default_reminder_amount', 'Enter an amount.')
                return cleaned
            minutes = to_minutes(
                cleaned['default_reminder_amount'],
                cleaned.get('default_reminder_unit') or 'minutes',
            )
        self.reminder_defaults = (
            minutes,
            cleaned.get('default_reminder_channel') or ReminderChannel.IN_APP,
        )
        return cleaned
