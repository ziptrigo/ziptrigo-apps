from django import forms

from ..models import User


class ThemeForm(forms.Form):
    """The "Appearance" section of the account page: system, light or dark."""

    theme = forms.ChoiceField(
        choices=User.THEME_CHOICES,
        error_messages={
            'required': 'Please choose a theme.',
            'invalid_choice': 'Please choose one of system, light or dark.',
        },
    )
