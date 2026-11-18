"""The owner's share panel: creating and editing a link. Only reads the fields; the rules (the
label length, the expiry not in the past, the cap) are the service's (`services.share`)."""

from datetime import date

from django import forms


class ShareLinkForm(forms.Form):
    label = forms.CharField(label='Label', required=False, strip=False)
    #: A date (`YYYY-MM-DD`, what the date field posts); empty means "no expiry".
    expires_on = forms.DateField(
        label='Expires on',
        required=False,
        input_formats=['%Y-%m-%d'],
        error_messages={'invalid': 'Enter a date.'},
    )

    def expiry(self) -> date | None:
        return self.cleaned_data.get('expires_on')
