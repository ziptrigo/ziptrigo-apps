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

        self._register_jobs()

    def _register_jobs(self):
        from datetime import timedelta

        from apps.core.scheduler import JobSpec
        from apps.core.scheduler import register as register_job

        from . import jobs

        register_job(
            JobSpec(
                name='core.purge_old_email_verifications',
                func=jobs.purge_old_email_verifications,
                interval=timedelta(hours=24),
                lease=timedelta(hours=1),
            )
        )
