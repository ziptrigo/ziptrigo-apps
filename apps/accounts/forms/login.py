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
        super().__init__(*args, **kwargs)

    def clean(self):
        cleaned = super().clean()
        email = cleaned.get('email')
        password = cleaned.get('password')
        if not email or not password:
            return cleaned

        user: User | None = authenticate(self.request, email=email, password=password)
        if user is None:
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
