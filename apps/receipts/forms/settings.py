from django import forms

from ..services.addresses import parse_allowlist
from ..services.currencies import normalize_currency

SENDER_MODES = (('any', 'Anyone'), ('only', 'Only these'))


class InboxSettingsForm(forms.Form):
    """Accepted senders and the default currency. Validation is the services' own
    (`addresses.parse_allowlist`, `currencies.normalize_currency`), so a typo shows the same
    friendly message here as it would through the API."""

    sender_mode = forms.ChoiceField(label='Accepted senders', choices=SENDER_MODES)
    sender_allowlist = forms.CharField(label='Accepted senders', required=False, strip=False)
    default_currency = forms.CharField(label='Default currency')

    def clean_sender_allowlist(self) -> str:
        return self.cleaned_data['sender_allowlist'].strip()

    def clean_default_currency(self) -> str:
        code = normalize_currency(self.cleaned_data['default_currency'])
        if not code:
            raise forms.ValidationError('Choose a currency.')
        return code

    def clean(self):
        cleaned = super().clean()
        if cleaned.get('sender_mode') != 'only':
            cleaned['sender_allowlist'] = ''
        elif 'sender_allowlist' in cleaned:
            try:
                entries = parse_allowlist(cleaned['sender_allowlist'])
            except forms.ValidationError as exc:
                self.add_error('sender_allowlist', exc)
            else:
                if not entries:
                    self.add_error(
                        'sender_allowlist',
                        'Enter at least one address, or choose Anyone to accept all senders.',
                    )
        return cleaned


class CategoryNameForm(forms.Form):
    name = forms.CharField(
        label='Category name',
        strip=False,
        error_messages={'required': 'Enter a category name.'},
    )
