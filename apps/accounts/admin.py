"""Admin registrations for the accounts app."""

from django.contrib import admin

from apps.core.admin_site import custom_admin_site

from .models import User


class UserAdmin(admin.ModelAdmin):
    """Admin interface for the custom User model."""

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


custom_admin_site.register(User, UserAdmin)
