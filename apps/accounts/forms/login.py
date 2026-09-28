from django import forms
from django.contrib.auth import authenticate
from django.http import HttpRequest

from ..models import User


class LoginForm(forms.Form):
    """Email/password login for the web UI. Same rules as `POST /api/auth/login`."""

    email = forms.EmailField()
    password = forms.CharField(strip=False, widget=forms.PasswordInput)

    def __init__(self, request: HttpRequest | None = None, *args, **kwargs):
        self.request = request
        self.user_cache: User | None = None
        #: Set `True` only when `authenticate()` itself returned `None` (unknown email or wrong
        #: password) -- as opposed to a known user blocked by `status`/`email_confirmed`, or the
        #: form never reaching `authenticate()` at all (a missing/malformed field). Read by
        #: `apps.accounts.views.login.login_page` to decide whether this submission counts as a
        #: *failed* login attempt against the looser `LOGIN_ACCOUNT` rate limit (issue #53 code
        #: review: that rule counts only failed attempts, never a status/confirmation block that
        #: had nothing to do with the password) -- see `apps.accounts.services.login_throttle`.
        self.credentials_invalid = False
        super().__init__(*args, **kwargs)

    def clean(self):
        cleaned = super().clean()
        email = cleaned.get('email')
        password = cleaned.get('password')
        if not email or not password:
            return cleaned

        user: User | None = authenticate(self.request, email=email, password=password)
        if user is None:
            self.credentials_invalid = True
            raise forms.ValidationError('Invalid credentials')
        if user.status != User.STATUS_ACTIVE:
            raise forms.ValidationError('User not active')
        if not user.email_confirmed:
            raise forms.ValidationError(
                'Please confirm your email address before logging in. '
                'Check your inbox for the confirmation link.'
            )

        self.user_cache = user
        return cleaned

    def get_user(self) -> User:
        """The authenticated user. Only valid after `is_valid()` returned `True`."""
        assert self.user_cache is not None
        return self.user_cache
