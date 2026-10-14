from django import forms

from ..validators import PasswordValidator
from .email import NormalizedEmailMixin


class RegisterForm(NormalizedEmailMixin, forms.Form):
    """Sign-up form for the web UI (issue #52), the only way to create an account (issue #117).
    Account creation itself is delegated to `apps.accounts.services.signup.create_account`. The
    captcha is checked by the view (`apps.accounts.views.register`), not here, so that an invalid
    form never spends a single-use token or a call to Cloudflare.
    """

    name = forms.CharField(max_length=255)
    # Normalized by `NormalizedEmailMixin` the same way `create_account` normalizes it. Whether
    # the address is already registered is deliberately *not* checked here: the form runs before
    # the captcha and every rate limit, so a "this email already exists" answer from it would let
    # a script enumerate accounts for free. `create_account` reports it instead, once the captcha
    # and `SIGNUP_IP` have passed (`apps.accounts.views.register`).
    email = forms.EmailField()
    password = forms.CharField(strip=False, widget=forms.PasswordInput)

    def clean_password(self) -> str:
        password = self.cleaned_data.get('password', '')
        PasswordValidator().validate(password)
        return password
