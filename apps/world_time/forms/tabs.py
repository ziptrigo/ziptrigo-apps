"""The World Time forms. They only gather the fields; the rules (lengths, uniqueness, caps) are the
services', which the views call next and whose `ValidationError`s they show the same way."""

from django import forms

from ..limits import LOCATION_LABEL_MAX, TAB_NAME_MAX


class TabForm(forms.Form):
    name = forms.CharField(
        max_length=TAB_NAME_MAX,
        error_messages={
            'required': 'Give the tab a name.',
            'max_length': f'Keep the name to {TAB_NAME_MAX} characters or fewer.',
        },
    )


class LabelForm(forms.Form):
    label = forms.CharField(
        required=False,
        max_length=LOCATION_LABEL_MAX,
        error_messages={
            'max_length': f'Keep the label to {LOCATION_LABEL_MAX} characters or fewer.'
        },
    )


class LocationForm(forms.Form):
    """What a suggestion posts: `city` (a GeoNames id) or `zone` (an IANA name), one of them."""

    city = forms.IntegerField(required=False, min_value=1)
    zone = forms.CharField(required=False, max_length=64)

    def clean(self):
        cleaned = super().clean() or {}
        if not self.errors and not cleaned.get('city') and not cleaned.get('zone'):
            raise forms.ValidationError('Choose a place from the suggestions.')
        return cleaned
