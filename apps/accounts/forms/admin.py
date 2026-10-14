"""Admin forms for the email-based `User`, built on Django's password-aware user forms."""

from django import forms
from django.contrib.auth.forms import AdminUserCreationForm, UserChangeForm

from ..models import User


def _clean_email_ignoring_case(form: forms.ModelForm) -> str:
    """`clean_email` for the admin forms: normalized the way accounts are stored (domain
    lower-cased) and refused when another account has the address in any letter case, like
    signup and `PUT /api/account` do (audit #15). The model's own unique check is case-sensitive,
    so without this staff could create the `Alice@x.com` next to `alice@x.com` that Google
    sign-in would then refuse as a conflict forever. Staff only, so saying that the address is
    taken reveals nothing an outsider can reach. An address left as it was on the change form is
    not re-checked, so staff can still edit (or deactivate) one of two legacy rows that differ
    only by case.

    A function rather than a mixin: a mixin in front of Django's `AdminUserCreationForm` makes
    `ty` re-report Django's own incompatible `set_password_and_save` overrides on our class.
    """
    cleaned = form.cleaned_data['email']
    if 'email' not in form.changed_data:
        return cleaned
    email = User.objects.normalize_email(cleaned)
    if User.objects.filter(email__iexact=email).exclude(pk=form.instance.pk).exists():
        raise forms.ValidationError('A user with this email already exists.', code='unique')
    return email


class UserAdminCreationForm(AdminUserCreationForm):
    class Meta:
        model = User
        fields = ('email', 'name')

    def clean_email(self) -> str:
        return _clean_email_ignoring_case(self)


class UserAdminChangeForm(UserChangeForm):
    class Meta:
        model = User
        fields = '__all__'

    def clean_email(self) -> str:
        return _clean_email_ignoring_case(self)
