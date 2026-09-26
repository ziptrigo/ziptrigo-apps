"""Admin forms for the email-based `User`, built on Django's password-aware user forms."""

from django.contrib.auth.forms import AdminUserCreationForm, UserChangeForm

from ..models import User


class UserAdminCreationForm(AdminUserCreationForm):
    class Meta:
        model = User
        fields = ('email', 'name')


class UserAdminChangeForm(UserChangeForm):
    class Meta:
        model = User
        fields = '__all__'
