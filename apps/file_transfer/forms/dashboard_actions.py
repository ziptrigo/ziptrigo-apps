"""Forms for the dashboard's per-transfer actions (spec section 6): extend/shorten expiry,
change max downloads, change/remove password, add recipients.
"""

from django import forms

from apps.core.forms import DatePickerWidget

from ..services.expiry_choices import (
    CUSTOM_CHOICE,
    EXPIRY_CHOICES,
    NO_EXPIRATION_CHOICE,
    end_of_day,
)
from ._emails import parse_email_list
from ._styling import StyledFormMixin


class ExpiryActionForm(StyledFormMixin, forms.Form):
    expiry_choice = forms.ChoiceField(choices=EXPIRY_CHOICES, initial=NO_EXPIRATION_CHOICE)
    expiry_date = forms.DateField(required=False, widget=DatePickerWidget(min_today=True))

    def clean_expiry_date(self):
        day = self.cleaned_data['expiry_date']
        return end_of_day(day) if day else None

    def clean(self):
        cleaned = super().clean()
        if cleaned.get('expiry_choice') == CUSTOM_CHOICE and not cleaned.get('expiry_date'):
            self.add_error('expiry_date', 'Provide a date for a custom expiry.')
        return cleaned


class MaxDownloadsActionForm(StyledFormMixin, forms.Form):
    max_downloads = forms.IntegerField(required=False, min_value=1)


class PasswordActionForm(StyledFormMixin, forms.Form):
    """A blank `password` means "leave it unchanged" -- the current hash can't be pre-filled into
    the input, so there'd be no way to tell "the owner wants it gone" from "the owner didn't
    touch this field" without the explicit `remove_password` checkbox, which the view (not this
    form) treats as taking priority over a non-blank `password`."""

    password = forms.CharField(required=False, widget=forms.PasswordInput(render_value=True))
    remove_password = forms.BooleanField(required=False)


class TransferSettingsActionForm(ExpiryActionForm, MaxDownloadsActionForm, PasswordActionForm):
    """The dashboard row's combined "settings" action: expiry, max downloads and password
    together, one submit. A blank password removes it (see `PasswordActionForm`)."""


class AddRecipientsForm(StyledFormMixin, forms.Form):
    recipients = forms.CharField(
        widget=forms.Textarea(attrs={'rows': 3}),
        help_text='One email address per line (or separated by commas).',
    )

    def clean_recipients(self) -> list[str]:
        return parse_email_list(self.cleaned_data['recipients'])
