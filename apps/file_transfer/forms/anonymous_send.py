"""The anonymous send page's options form (spec section 2 phase 2): like `SendOptionsForm`, plus
the sender's email (needed to start confirmation) and a fixed expiry dropdown instead of the
logged-in one -- built dynamically from `FileTransferSettings.anonymous_allowed_expiry_days`, since
an admin can change that list at any time.
"""

from django import forms

from ..models import FileTransferSettings
from ..services.anonymous import AnonymousSendOptions
from ..services.expiry_choices import anonymous_expiry_choices
from ..services.limits import MAX_MESSAGE_LENGTH
from ._emails import parse_email_list
from ._styling import StyledFormMixin


class AnonymousSendOptionsForm(StyledFormMixin, forms.Form):
    sender_email = forms.EmailField(label='Your email')
    recipients = forms.CharField(
        widget=forms.Textarea(attrs={'rows': 3}),
        help_text='One email address per line (or separated by commas).',
    )
    message = forms.CharField(
        required=False,
        max_length=MAX_MESSAGE_LENGTH,
        widget=forms.Textarea(attrs={'rows': 4}),
    )
    expiry_choice = forms.ChoiceField(choices=())
    max_downloads = forms.IntegerField(required=False, min_value=1)
    password = forms.CharField(required=False, widget=forms.PasswordInput(render_value=True))
    notify_on_download = forms.BooleanField(required=False, initial=True)

    def __init__(self, *args, settings_row: FileTransferSettings | None = None, **kwargs):
        super().__init__(*args, **kwargs)
        settings_row = settings_row or FileTransferSettings.load()
        self.fields['expiry_choice'].choices = anonymous_expiry_choices(settings_row)

    def clean_recipients(self) -> list[str]:
        return parse_email_list(self.cleaned_data['recipients'])

    def to_anonymous_send_options(self) -> AnonymousSendOptions:
        data = self.cleaned_data
        return AnonymousSendOptions(
            sender_email=data['sender_email'],
            recipients=data['recipients'],
            message=data.get('message', ''),
            expiry_choice=data['expiry_choice'],
            max_downloads=data.get('max_downloads'),
            password=data.get('password', ''),
            notify_on_download=data.get('notify_on_download', True),
        )
