from django import forms

from apps.core import color_schemes

from ..models import User


class ThemeForm(forms.Form):
    """The "Appearance" section of the account page: system, light or dark, the colour scheme
    and high contrast.

    `color_scheme` is optional, so a post with only `theme` (an older page) changes only the theme.
    """

    theme = forms.ChoiceField(
        choices=User.THEME_CHOICES,
        error_messages={
            'required': 'Please choose a theme.',
            'invalid_choice': 'Please choose one of system, light or dark.',
        },
    )
    color_scheme = forms.ChoiceField(
        choices=color_schemes.CHOICES,
        required=False,
        error_messages={'invalid_choice': 'Please choose one of the listed color schemes.'},
    )
    # An unticked checkbox posts nothing, so the page also posts `contrast_posted`: without it (an
    # older page, a post with only `theme`) high contrast is left as it is.
    high_contrast = forms.BooleanField(required=False)
    contrast_posted = forms.BooleanField(required=False)
