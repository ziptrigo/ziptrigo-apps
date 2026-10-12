from django import forms
from django.contrib.auth import password_validation

from ..models import User
from ..validators import PasswordValidator


class ChangePasswordForm(forms.Form):
    """The "Change Password" section of the account page. The current password must be right; the
    new one follows the same rules as signup and reset (`PasswordValidator`) plus Django's
    `AUTH_PASSWORD_VALIDATORS` (too common, all digits, too similar to the email or name), which
    need the user and so start here."""

    current_password = forms.CharField(strip=False, widget=forms.PasswordInput)
    new_password = forms.CharField(strip=False, widget=forms.PasswordInput)
    new_password_confirm = forms.CharField(strip=False, widget=forms.PasswordInput)

    def __init__(self, user: User, *args, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)

    def clean_current_password(self) -> str:
        password = self.cleaned_data.get('current_password', '')
        if not self.user.check_password(password):
            raise forms.ValidationError('Your current password is incorrect.')
        return password

    def clean_new_password(self) -> str:
        password = self.cleaned_data.get('new_password', '')
        PasswordValidator().validate(password)
        password_validation.validate_password(password, self.user)
        return password

    def clean(self):
        cleaned = super().clean()
        current = cleaned.get('current_password')
        new = cleaned.get('new_password')
        confirm = cleaned.get('new_password_confirm')
        if new and confirm and new != confirm:
            self.add_error('new_password_confirm', 'Passwords do not match.')
        elif current and new and current == new:
            self.add_error(
                'new_password', 'The new password must be different from the current one.'
            )
        return cleaned
