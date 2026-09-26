from django import forms

from ..models import QRCode, QRCodeFormat, QRCodeType
from ..services import validate_content
from ..services.management import MAX_CONTENT_LENGTH


class QRCodeCreateForm(forms.Form):
    """The QR code editor in create mode (also used for previews).

    The content rules come from `services.validate_content`, so the API enforces the same ones.
    """

    name = forms.CharField(max_length=255)
    qr_type = forms.ChoiceField(label='Type', choices=QRCodeType.choices)
    qr_format = forms.ChoiceField(label='Format', choices=QRCodeFormat.choices)
    # The editor's single "Text / URL" field.
    url = forms.CharField(
        label='Text / URL',
        max_length=MAX_CONTENT_LENGTH,
        strip=True,
        error_messages={'required': 'Please provide the text or URL to encode.'},
    )
    use_url_shortening = forms.BooleanField(required=False)

    def clean(self):
        cleaned = super().clean()
        if cleaned.get('qr_type') and cleaned.get('url'):
            try:
                validate_content(cleaned['qr_type'], cleaned['url'])
            except forms.ValidationError as e:
                self.add_error('url', e)
        return cleaned

    def qrcode_fields(self) -> dict:
        """Keyword arguments for `services.create_qrcode` / `services.render_preview`."""
        data = self.cleaned_data
        return {
            'name': data['name'],
            'qr_type': data['qr_type'],
            'qr_format': data['qr_format'],
            'content': data['url'],
            'use_url_shortening': data['use_url_shortening'],
        }


class QRCodeRenameForm(forms.ModelForm):
    """The QR code editor in edit mode: only the name can change."""

    class Meta:
        model = QRCode
        fields = ['name']
