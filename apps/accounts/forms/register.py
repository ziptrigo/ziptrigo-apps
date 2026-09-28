from django import forms

from ..models import User
from ..validators import PasswordValidator


class RegisterForm(forms.Form):
    """Sign-up form for the web UI (issue #52). Same rules as `SignupRequest`
    (`POST /api/auth/signup`); account creation itself is delegated to
    `apps.accounts.services.signup.create_account` so both surfaces share it.
    """

    name = forms.CharField(max_length=255)
    email = forms.EmailField()
    password = forms.CharField(strip=False, widget=forms.PasswordInput)

    def clean_email(self) -> str:
        # Normalized the same way `apps.accounts.services.signup.create_account` (and
        # `UserManager.create_user`) normalize it, so this check can't be fooled by a domain-case
        # variant of an email that's already registered (e.g. `user@Example.COM` vs.
        # `user@example.com`) -- see that module's docstring for the full story.
        email = User.objects.normalize_email(self.cleaned_data['email'])
        if User.objects.filter(email=email).exists():
            raise forms.ValidationError('An account with this email already exists.')
        return email

    def clean_password(self) -> str:
        password = self.cleaned_data.get('password', '')
        PasswordValidator().validate(password)
        return password
