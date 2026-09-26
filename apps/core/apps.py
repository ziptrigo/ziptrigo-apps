import os

from django.apps import AppConfig


class CoreConfig(AppConfig):
    name = 'apps.core'
    label = 'core'

    def ready(self):
        # Only run in the reloader process, not the main watcher
        if os.environ.get('RUN_MAIN') == 'true':
            # Imports and register checks
            from . import checks  # noqa: F401

            environment = os.getenv('ENVIRONMENT')
            if environment:
                print(f'Environment: {environment}')
