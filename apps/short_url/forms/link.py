"""The create/edit form of a link.

It only gathers and checks the fields; the rules themselves live in `services` (the form calls
the same validators the service does, to show friendly errors per field). Credits are not part of
it: the views quote the cost of the extras and ask for confirmation before charging.
"""

from django import forms

from .. import services
from ..models import ShortLink
from ..services.destinations import UTM_PARAMS
from ..services.links import MAX_CLICKS_LIMIT


def _validated(field: str, check, value):
    """Run a service validator, turning its `ValidationError` into this form's field error."""
    try:
        return check(value)
    except forms.ValidationError as exc:
        raise forms.ValidationError(exc.message_dict.get(field) or exc.messages) from None


class LinkForm(forms.Form):
    destination = forms.CharField(label='Destination', max_length=2048 * 2)
    alias = forms.CharField(label='Alias', required=False, max_length=64)
    title = forms.CharField(label='Title', required=False, max_length=1000)
    tags = forms.CharField(label='Tags', required=False, max_length=2000)
    utm_source = forms.CharField(label='UTM source', required=False, max_length=200)
    utm_medium = forms.CharField(label='UTM medium', required=False, max_length=200)
    utm_campaign = forms.CharField(label='UTM campaign', required=False, max_length=200)
    utm_term = forms.CharField(label='UTM term', required=False, max_length=200)
    utm_content = forms.CharField(label='UTM content', required=False, max_length=200)
    expires_at = forms.DateTimeField(label='Expires', required=False)
    max_clicks = forms.IntegerField(
        label='Click limit', required=False, min_value=1, max_value=MAX_CLICKS_LIMIT
    )

    def __init__(self, *args, link: ShortLink | None = None, **kwargs):
        """`link`: the link being edited. Its alias can't be changed (that would free the old
        code), so the field is left out, and an expiry that has passed is accepted as long as it
        is the one the link already has."""
        super().__init__(*args, **kwargs)
        self.link = link
        if link is not None:
            del self.fields['alias']

    def clean_destination(self) -> str:
        # Checked on its own (UTM comes after), so the message points at the address itself.
        return _validated(
            'destination', services.validate_destination, self.cleaned_data['destination']
        )

    def clean_alias(self) -> str:
        alias = self.cleaned_data['alias'].strip()
        return _validated('alias', services.validate_alias, alias) if alias else ''

    def clean_title(self) -> str:
        return _validated('title', services.validate_title, self.cleaned_data['title'])

    def clean_tags(self) -> list[str]:
        return _validated('tags', services.normalize_tags, self.cleaned_data['tags'])

    def clean_expires_at(self):
        value = self.cleaned_data['expires_at']
        if not value:
            return None
        current = self.link.expires_at if self.link else None
        # The form shows the expiry to the minute, so "unchanged" means the same minute.
        if current is not None and value == current.replace(second=0, microsecond=0):
            return current
        return _validated('expires_at', services.validate_expiry, value)

    @property
    def utm(self) -> dict[str, str]:
        return {name: self.cleaned_data.get(name) or '' for name in UTM_PARAMS}
