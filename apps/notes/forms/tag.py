from django import forms
from django.core.validators import RegexValidator

from ..limits import TAG_DESCRIPTION_MAX, TAG_NAME_MAX
from ..models import DEFAULT_TAG_COLOR


class TagForm(forms.Form):
    """Adding or editing a tag. A rename posts only the name, so colour and description are
    optional here (the view applies only what was posted)."""

    name = forms.CharField(
        label='Name', max_length=TAG_NAME_MAX, error_messages={'required': 'Name is required.'}
    )
    color = forms.CharField(
        label='Colour',
        required=False,
        max_length=7,
        validators=[RegexValidator(r'^#[0-9a-fA-F]{6}$', 'Use a colour like #8fa89e.')],
        initial=DEFAULT_TAG_COLOR,
    )
    description = forms.CharField(
        label='Description', required=False, max_length=TAG_DESCRIPTION_MAX
    )
