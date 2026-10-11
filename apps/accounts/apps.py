from django.apps import AppConfig


class AccountsConfig(AppConfig):
    name = 'apps.accounts'
    label = 'accounts'

    def ready(self):
        # Registers the system checks (`@register()` runs at import).
        from . import checks  # noqa: F401
