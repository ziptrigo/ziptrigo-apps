from django import forms
from django.core.validators import URLValidator

from ..models import QRCode, QRCodeFormat, QRCodeType
from ..services.management import SHORT_CODE_PATTERN


class QRCodeCreateForm(forms.Form):
    """The QR code editor in create mode (also used for previews)."""

    name = forms.CharField(max_length=255)
    qr_type = forms.ChoiceField(label='Type', choices=QRCodeType.choices)
    qr_format = forms.ChoiceField(label='Format', choices=QRCodeFormat.choices)
    # The editor's single "Text / URL" field.
    url = forms.CharField(
        label='Text / URL',
        max_length=1000,
        strip=True,
        error_messages={'required': 'Please provide the text or URL to encode.'},
    )
    use_url_shortening = forms.BooleanField(required=False)
    # Generated client-side so the editor can show the short URL before saving.
    short_code = forms.RegexField(label='Short code', regex=SHORT_CODE_PATTERN, required=False)

    def clean(self):
        cleaned = super().clean()
        is_url = cleaned.get('qr_type') == QRCodeType.URL

        if is_url and cleaned.get('url'):
            try:
                URLValidator()(cleaned['url'])
            except forms.ValidationError:
                self.add_error('url', 'Please enter a valid URL, e.g. https://example.com.')

        # Short links only make sense for URLs.
        if not is_url:
            cleaned['use_url_shortening'] = False

        return cleaned

    def qrcode_fields(self) -> dict:
        """Keyword arguments for `services.create_qrcode` / `services.render_preview`."""
        data = self.cleaned_data
        is_url = data['qr_type'] == QRCodeType.URL
        return {
            'name': data['name'],
            'qr_type': data['qr_type'],
            'qr_format': data['qr_format'],
            'content': data['url'],
            'original_url': data['url'] if is_url else None,
            'use_url_shortening': data['use_url_shortening'],
            'short_code': data.get('short_code') or None,
        }


class QRCodeRenameForm(forms.ModelForm):
    """The QR code editor in edit mode: only the name can change."""

    class Meta:
        model = QRCode
        fields = ['name']
