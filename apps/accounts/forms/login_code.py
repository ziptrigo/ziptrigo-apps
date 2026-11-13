from django import forms

from ..services.login_otp import CODE_MAX_LENGTH, clean_code
from .email import NormalizedEmailMixin


class LoginCodeRequestForm(NormalizedEmailMixin, forms.Form):
    """Ask for a sign-in code by email (issue #295). Just an email format check: whether the
    account exists is never revealed (CLAUDE.md), so there is nothing further to validate."""

    email = forms.EmailField()


class LoginCodeForm(NormalizedEmailMixin, forms.Form):
    """The address and the code from the email. The code is only checked for shape here (digits,
    spaces and hyphens allowed as typed); whether it is right is decided, and answered
    generically, by `services.login_otp.user_for_code`."""

    email = forms.EmailField()
    code = forms.CharField(max_length=CODE_MAX_LENGTH * 2, strip=True)

    def clean_code(self) -> str:
        code = clean_code(self.cleaned_data['code'])
        if not code.isascii() or not code.isdigit() or len(code) > CODE_MAX_LENGTH:
            raise forms.ValidationError('Enter the code from the email: digits only.')
        return code
