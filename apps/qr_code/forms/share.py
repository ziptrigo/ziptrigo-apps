"""The owner's share panel: creating and editing a link. Only reads the fields; the rules (the
label length, the expiry not in the past, the cap) are the service's (`services.share`)."""

import re
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


_ADDRESS_SPLIT = re.compile(r'[,;\s]+')


class ShareEmailForm(forms.Form):
    """Emailing a link: a box of addresses (separated by commas, semicolons, spaces or new lines)
    and an optional message. Only reads the fields; the address format, the cap, the message length
    and the cooldown are the service's (`services.share_emails`). Both boxes have a generous
    `max_length` so a pasted file is not split into thousands of pieces or walked by the cleaner."""

    emails = forms.CharField(
        label='Email addresses',
        required=False,
        max_length=5000,
        error_messages={'max_length': 'Too many addresses.'},
    )
    message = forms.CharField(label='Message', required=False, strip=False, max_length=5000)

    def addresses(self) -> list[str]:
        return [part for part in _ADDRESS_SPLIT.split(self.cleaned_data['emails']) if part]
