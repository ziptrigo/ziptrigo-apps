"""The send page's options form: recipients, message, expiry, max downloads, password,
notify-on-download (spec section 2). The files themselves are added/removed through separate
JSON endpoints (`apps/file_transfer/views/uploads.py`) driven by the browser's direct-to-S3
uploads, so they aren't part of this form.
"""

from django import forms

from apps.core.forms import DatePickerWidget

from ..services.expiry_choices import (
    CUSTOM_CHOICE,
    EXPIRY_CHOICES,
    NO_EXPIRATION_CHOICE,
    end_of_day,
)
from ..services.limits import MAX_MESSAGE_LENGTH
from ..services.send import SendOptions
from ._emails import parse_email_list
from ._styling import StyledFormMixin


class SendOptionsForm(StyledFormMixin, forms.Form):
    recipients = forms.CharField(
        widget=forms.Textarea(attrs={'rows': 3}),
        help_text='One email address per line (or separated by commas).',
    )
    message = forms.CharField(
        required=False,
        max_length=MAX_MESSAGE_LENGTH,
        widget=forms.Textarea(attrs={'rows': 4}),
    )
    expiry_choice = forms.ChoiceField(choices=EXPIRY_CHOICES, initial=NO_EXPIRATION_CHOICE)
    expiry_date = forms.DateField(required=False, widget=DatePickerWidget(min_today=True))
    max_downloads = forms.IntegerField(required=False, min_value=1)
    password = forms.CharField(required=False, widget=forms.PasswordInput(render_value=True))
    notify_on_download = forms.BooleanField(required=False, initial=True)

    def clean_recipients(self) -> list[str]:
        return parse_email_list(self.cleaned_data['recipients'])

    def clean_expiry_date(self):
        day = self.cleaned_data['expiry_date']
        return end_of_day(day) if day else None

    def clean(self):
        cleaned = super().clean()
        if cleaned.get('expiry_choice') == CUSTOM_CHOICE and not cleaned.get('expiry_date'):
            self.add_error('expiry_date', 'Provide a date for a custom expiry.')
        return cleaned

    def to_send_options(self) -> SendOptions:
        data = self.cleaned_data
        return SendOptions(
            recipients=data['recipients'],
            message=data.get('message', ''),
            expiry_choice=data['expiry_choice'],
            expiry_date=data.get('expiry_date'),
            max_downloads=data.get('max_downloads'),
            password=data.get('password', ''),
            notify_on_download=data.get('notify_on_download', True),
        )
