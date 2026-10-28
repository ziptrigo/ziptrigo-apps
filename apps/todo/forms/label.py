from django import forms
from django.core.validators import RegexValidator

from ..models import DEFAULT_LABEL_COLOR
from ..services.limits import LABEL_DESCRIPTION_MAX, LABEL_NAME_MAX


class LabelForm(forms.Form):
    """Adding or editing a label. A rename posts only the name, so colour and description are
    optional here (the view applies only what was posted)."""

    name = forms.CharField(
        label='Name', max_length=LABEL_NAME_MAX, error_messages={'required': 'Name is required.'}
    )
    color = forms.CharField(
        label='Colour',
        required=False,
        max_length=7,
        validators=[RegexValidator(r'^#[0-9a-fA-F]{6}$', 'Use a colour like #8fa89e.')],
        initial=DEFAULT_LABEL_COLOR,
    )
    description = forms.CharField(
        label='Description', required=False, max_length=LABEL_DESCRIPTION_MAX
    )
