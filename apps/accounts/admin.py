"""Admin registrations for the accounts app."""

from django.apps import apps as django_apps
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin

from apps.core.admin_site import custom_admin_site

from .forms.admin import UserAdminChangeForm, UserAdminCreationForm
from .http import AuthenticatedHttpRequest
from .models import User

# Apps whose rows hang off a user with `on_delete=CASCADE` and are meant to go with it, although
# their own admins refuse delete (a ledger row must not be removed by hand, but deleting the
# account removes ledger and balance together, which keeps them consistent). Named by label because
# `accounts` can't import them.
CASCADE_WITH_USER_APP_LABELS = ('billing',)


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
    readonly_fields = [
        'google_sub',
        'microsoft_sub',
        'email_confirmed_at',
        'created_at',
        'updated_at',
    ]
    fieldsets = (
        (
            'Authentication',
            {'fields': ('email', 'password', 'google_sub', 'microsoft_sub')},
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

    def get_deleted_objects(self, objs, request: AuthenticatedHttpRequest):
        """Don't let a cascaded row's read-only admin block deleting its user.

        Django's `get_deleted_objects` asks each cascaded object's own admin for
        `has_delete_permission(request, obj)` and refuses the whole delete when any says no.
        Billing's admins say no to everyone (the ledger is read-only), so deleting a user with
        credit history would be refused even for a superuser. For the models of
        `CASCADE_WITH_USER_APP_LABELS` the question becomes whether the caller holds the model's
        `delete_<model>` permission, which is what the default `has_delete_permission` asked
        before they were made read-only: a superuser passes, staff still need it granted.
        """
        to_delete, model_count, perms_needed, protected = super().get_deleted_objects(objs, request)
        # `perms_needed` holds verbose names (see `django.contrib.admin.utils`), so map back.
        for model in django_apps.get_models():
            opts = model._meta
            if opts.app_label in CASCADE_WITH_USER_APP_LABELS and request.user.has_perm(
                f'{opts.app_label}.delete_{opts.model_name}'
            ):
                perms_needed.discard(opts.verbose_name)
        return to_delete, model_count, perms_needed, protected


custom_admin_site.register(User, UserAdmin)
