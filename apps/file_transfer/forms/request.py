"""The file request forms (issue #306): the page that creates one (`FileRequestForm`) and the
settings form of a request on the dashboard (`RequestSettingsForm`). Both only gather and shape the
fields; every rule (title, audience, limits, invitees, expiry) lives in
`services.requests`, which the form calls so the web and the API refuse the same things.
"""

from decimal import Decimal

from django import forms
from django.core.exceptions import ValidationError

from apps.core.forms import DatePickerWidget

from ..models import UploadAudience
from ..services import requests as request_service
from ..services.expiry_choices import (
    CUSTOM_CHOICE,
    EXPIRY_CHOICES,
    NO_EXPIRATION_CHOICE,
    end_of_day,
)
from ._emails import parse_email_list
from ._styling import StyledFormMixin

#: The service's field names that the form calls something else.
_FORM_FIELD = {'max_total_size_bytes': 'max_total_gb'}


def add_service_errors(form: forms.Form, exc: ValidationError) -> None:
    """Show a service `ValidationError` on the form: under its field when it names one, as a form
    error otherwise."""
    if hasattr(exc, 'error_dict'):
        for field, errors in exc.error_dict.items():
            target = _FORM_FIELD.get(field, field)
            for error in errors:
                form.add_error(target if target in form.fields else None, error.messages[0])
    else:
        form.add_error(None, exc.messages[0])


class _RequestFieldsForm(StyledFormMixin, forms.Form):
    title = forms.CharField(
        label='Title',
        required=False,
        widget=forms.TextInput(attrs={'maxlength': '100'}),
        help_text='Optional. Shown to the people you ask, on the upload page.',
    )
    message = forms.CharField(
        label='Message',
        required=False,
        widget=forms.Textarea(attrs={'rows': 4}),
        help_text='Shown to the people you ask, on the upload page.',
    )
    upload_audience = forms.ChoiceField(
        label='Who can upload',
        choices=UploadAudience.choices,
        initial=UploadAudience.ANYONE,
        widget=forms.RadioSelect,
    )
    emails = forms.CharField(
        label='Their email addresses',
        required=False,
        widget=forms.Textarea(attrs={'rows': 3}),
        help_text='One email address per line (or separated by commas). They are emailed the '
        'link, and need an account with that address to upload.',
    )
    max_files = forms.IntegerField(
        label='Max files', required=False, min_value=1, help_text='Blank: the site maximum.'
    )
    max_total_gb = forms.DecimalField(
        label='Max total size (GB)',
        required=False,
        min_value=Decimal('0.01'),
        decimal_places=2,
        help_text='Blank: the site maximum.',
    )
    expiry_choice = forms.ChoiceField(choices=EXPIRY_CHOICES, initial=NO_EXPIRATION_CHOICE)
    expiry_date = forms.DateField(required=False, widget=DatePickerWidget(min_today=True))
    password = forms.CharField(
        label='Password (optional)',
        required=False,
        widget=forms.PasswordInput(render_value=True),
    )

    def clean_emails(self) -> list[str]:
        return parse_email_list(self.cleaned_data.get('emails', ''))

    def clean_expiry_date(self):
        day = self.cleaned_data['expiry_date']
        return end_of_day(day) if day else None

    def clean(self):
        cleaned = super().clean()
        if cleaned.get('expiry_choice') == CUSTOM_CHOICE and not cleaned.get('expiry_date'):
            self.add_error('expiry_date', 'Provide a date for a custom expiry.')
        return cleaned

    def max_total_size_bytes(self) -> int | None:
        return request_service.gb_to_bytes(self.cleaned_data.get('max_total_gb'))


class FileRequestForm(_RequestFieldsForm):
    """The create page. Invalid values the service refuses (a title that is too long, no invitee
    for "Only these people", a limit above the site's) are added to the form here, so they show
    under their field like any other."""

    def clean(self):
        cleaned = super().clean()
        if self.errors:
            return cleaned
        try:
            request_service.validate_request_options(self.to_options())
        except ValidationError as exc:
            add_service_errors(self, exc)
        return cleaned

    def to_options(self) -> request_service.RequestOptions:
        data = self.cleaned_data
        return request_service.RequestOptions(
            title=data.get('title', ''),
            message=data.get('message', ''),
            upload_audience=data['upload_audience'],
            emails=data.get('emails', []),
            max_files=data.get('max_files'),
            max_total_size_bytes=self.max_total_size_bytes(),
            expiry_choice=data['expiry_choice'],
            expiry_date=data.get('expiry_date'),
            password=data.get('password', ''),
        )


class RequestSettingsForm(_RequestFieldsForm):
    """A request's settings on the dashboard. A blank password leaves it unchanged (the hash can't
    be shown), `remove_password` clears it; blank limits mean the site's maximum."""

    remove_password = forms.BooleanField(required=False)

    def to_changes(self) -> dict:
        """The `services.requests.update_request` keys this submit sets: all of them."""
        data = self.cleaned_data
        changes: dict = {
            'title': data.get('title', ''),
            'message': data.get('message', ''),
            'upload_audience': data['upload_audience'],
            'max_files': data.get('max_files'),
            'max_total_size_bytes': self.max_total_size_bytes(),
            'expiry_choice': data['expiry_choice'],
            'expiry_date': data.get('expiry_date'),
            'remove_password': bool(data.get('remove_password')),
            'password': data.get('password', ''),
        }
        if data['upload_audience'] == UploadAudience.SELECTED:
            changes['emails'] = data.get('emails', [])
        return changes
