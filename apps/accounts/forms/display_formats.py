from django import forms

from ..models import User


class DisplayFormatsForm(forms.Form):
    """The "Appearance" section's date and time format: both fields optional, so a post with only
    one of them changes only that one; a post with neither is refused."""

    date_format = forms.ChoiceField(
        choices=User.DATE_FORMAT_CHOICES,
        required=False,
        error_messages={'invalid_choice': 'Please choose mm/dd/yyyy or dd/mm/yyyy.'},
    )
    time_format = forms.ChoiceField(
        choices=User.TIME_FORMAT_CHOICES,
        required=False,
        error_messages={'invalid_choice': 'Please choose a 12-hour or a 24-hour clock.'},
    )

    def clean(self):
        cleaned = super().clean()
        if cleaned is not None and not self.errors:
            if not cleaned.get('date_format') and not cleaned.get('time_format'):
                raise forms.ValidationError('Please choose a date or a time format.')
        return cleaned
