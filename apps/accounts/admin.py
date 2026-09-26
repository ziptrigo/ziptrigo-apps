"""Admin registrations for the accounts app."""

from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin

from apps.core.admin_site import custom_admin_site

from .forms.admin import UserAdminChangeForm, UserAdminCreationForm
from .models import User


class UserAdmin(DjangoUserAdmin):
    """Admin interface for the custom User model.

    Built on Django's `UserAdmin`, so the password is shown as a read-only hash with a link to the
    change-password form, and new users get hashed passwords.
    """

    form = UserAdminChangeForm
    add_form = UserAdminCreationForm

    list_display = [
        'email',
        'name',
        'email_confirmed',
        'is_staff',
        'is_active',
        'status',
    ]
    list_filter = ['status', 'email_confirmed', 'is_staff', 'is_active']
    search_fields = ['email', 'name']
    ordering = ['email']
    readonly_fields = ['email_confirmed_at', 'created_at', 'updated_at']
    fieldsets = (
        (
            'Authentication',
            {'fields': ('email', 'password')},
        ),
        (
            'Personal info',
            {'fields': ('name',)},
        ),
        (
            'Status',
            {'fields': ('status', 'email_confirmed', 'email_confirmed_at')},
        ),
        (
            'Permissions',
            {'fields': ('is_staff', 'is_superuser', 'is_active', 'groups', 'user_permissions')},
        ),
        (
            'Important dates',
            {'fields': ('last_login', 'created_at', 'updated_at')},
        ),
    )
    add_fieldsets = (
        (
            None,
            {
                'classes': ('wide',),
                'fields': ('email', 'name', 'usable_password', 'password1', 'password2'),
            },
        ),
    )


custom_admin_site.register(User, UserAdmin)
