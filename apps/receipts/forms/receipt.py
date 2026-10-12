from django import forms

from apps.accounts.models import User

from ..models import MAX_NOTES_LENGTH
from ..services import clean_changes


class ReceiptForm(forms.Form):
    """The detail page's form. Every rule lives in `services.actions.clean_changes` (also what
    `update_receipt` and the API apply); this only gathers the fields and turns that
    `ValidationError` into per-field form errors, so the page shows the same messages the API
    would. Values are kept as the text the user typed; the service normalizes them."""

    vendor = forms.CharField(label='Vendor', required=False, strip=False)
    # ISO only (what a date input sends): `09/10/2026` would be read as September, silently.
    receipt_date = forms.DateField(label='Receipt date', required=False, input_formats=['%Y-%m-%d'])
    amount = forms.CharField(label='Amount', required=False)
    currency = forms.CharField(label='Currency', required=False)
    document_type = forms.CharField(label='Type', required=False)
    document_number = forms.CharField(label='Number', required=False, strip=False)
    payment_method = forms.CharField(label='Payment method', required=False, strip=False)
    category = forms.CharField(label='Category', required=False)
    notes = forms.CharField(
        label='Notes',
        required=False,
        strip=False,
        widget=forms.Textarea(attrs={'maxlength': MAX_NOTES_LENGTH}),
    )

    def __init__(self, *args, user: User, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.user = user

    def clean(self):
        cleaned = super().clean()
        # Check the fields that parsed, so every problem is reported in one go.
        valid = {name: value for name, value in cleaned.items() if name not in self.errors}
        try:
            clean_changes(self.user, valid)
        except forms.ValidationError as exc:
            self.add_error(None, exc)
        return cleaned
