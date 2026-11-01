"""Base `ModelAdmin` for the singleton settings pages (`CoreSettings`, `BillingSettings`,
`FileTransferSettings`): superusers only, one row (the `pk=1` row the model's `load()` creates),
never deleted, and a changelist that goes straight to that row's change form.
"""

from django.contrib import admin
from django.http import HttpResponse
from django.shortcuts import redirect

from .admin_site import AuthenticatedHttpRequest
from .models.singleton import load_singleton


class SingletonSettingsAdmin(admin.ModelAdmin):
    """Subclasses only set `fieldsets`/`exclude`/... as for any `ModelAdmin`; the change-form URL
    is derived from the registered model and the admin site's name."""

    def _is_superuser(self, request: AuthenticatedHttpRequest) -> bool:
        # `request.user` is typed `PermissionsMixin` (see `AuthenticatedHttpRequest`'s docstring):
        # `core` can't import `accounts`, so `is_active` is read defensively. The admin site
        # itself already requires an active, staff-authenticated session to reach any view.
        user = request.user
        return bool(getattr(user, 'is_active', True) and user.is_superuser)

    def has_module_permission(self, request: AuthenticatedHttpRequest) -> bool:
        return self._is_superuser(request)

    def has_view_permission(self, request: AuthenticatedHttpRequest, obj=None) -> bool:
        return self._is_superuser(request)

    def has_add_permission(self, request: AuthenticatedHttpRequest) -> bool:
        # Never "Add": the changelist creates the one row on first visit (`load_singleton`), so
        # the dashboard and the list don't offer an Add link for a row that only has to be opened.
        return False

    def has_change_permission(self, request: AuthenticatedHttpRequest, obj=None) -> bool:
        return self._is_superuser(request)

    def has_delete_permission(self, request: AuthenticatedHttpRequest, obj=None) -> bool:
        return False

    def changelist_view(
        self, request: AuthenticatedHttpRequest, extra_context=None
    ) -> HttpResponse:
        """Skip the changelist entirely -- there's only ever one row."""
        row = load_singleton(self.model)
        opts = self.model._meta
        return redirect(f'{self.admin_site.name}:{opts.app_label}_{opts.model_name}_change', row.pk)
