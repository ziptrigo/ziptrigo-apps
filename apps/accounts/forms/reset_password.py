from django import forms

from ..validators import PasswordValidator


class ResetPasswordForm(forms.Form):
    """Set a new password from a reset-password link (issue #52). Same rules as
    `PasswordResetConfirm` (`POST /api/auth/reset-password`) -- the token itself comes from the
    URL (`apps.accounts.views.reset_password.reset_password_page`), not a form field.
    """

    password = forms.CharField(strip=False, widget=forms.PasswordInput)
    password_confirm = forms.CharField(strip=False, widget=forms.PasswordInput)

    def clean_password(self) -> str:
        password = self.cleaned_data.get('password', '')
        PasswordValidator().validate(password)
        return password

    def clean(self):
        cleaned = super().clean()
        password = cleaned.get('password')
        password_confirm = cleaned.get('password_confirm')
        if password and password_confirm and password != password_confirm:
            self.add_error('password_confirm', 'Passwords do not match.')
        return cleaned
