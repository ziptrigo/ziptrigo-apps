from django import forms

from ..models import User
from ..validators import PasswordValidator


class ChangePasswordForm(forms.Form):
    """The "Change Password" section of the account page. The current password must be right, the
    new one follows the same rules as signup and reset (`PasswordValidator`)."""

    current_password = forms.CharField(strip=False, widget=forms.PasswordInput)
    new_password = forms.CharField(strip=False, widget=forms.PasswordInput)
    new_password_confirm = forms.CharField(strip=False, widget=forms.PasswordInput)

    def __init__(self, user: User, *args, **kwargs):
        self.user = user
        #: Set `True` only when the current password was checked and was wrong. Read by
        #: `apps.accounts.views.password.password_change` to count the attempt against the
        #: failed-attempts-only `CHANGE_PASSWORD_USER` rate limit.
        self.current_password_invalid = False
        super().__init__(*args, **kwargs)

    def clean_current_password(self) -> str:
        password = self.cleaned_data.get('current_password', '')
        if not self.user.check_password(password):
            self.current_password_invalid = True
            raise forms.ValidationError('Your current password is incorrect.')
        return password

    def clean_new_password(self) -> str:
        password = self.cleaned_data.get('new_password', '')
        PasswordValidator().validate(password)
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
