import os

from django.apps import AppConfig


class QrCodeConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'qr_code'
    label = 'qr_code'

    def ready(self):
        # Only run in the reloader process, not the main watcher
        if os.environ.get('RUN_MAIN') == 'true':
            # Imports and register checks
            from . import checks  # noqa: F401

            environment = os.getenv('ENVIRONMENT')
            if environment:
                print(f'Environment: {environment}')
