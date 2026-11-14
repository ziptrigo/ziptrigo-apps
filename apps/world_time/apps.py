from datetime import timedelta

from django.apps import AppConfig


class WorldTimeConfig(AppConfig):
    name = 'apps.world_time'
    label = 'world_time'
    verbose_name = 'World Time'

    def ready(self):
        from apps.core.products import ProductApp, register

        from .services.costs import cost_section

        register(
            ProductApp(
                label=self.label,
                name='World Time',
                description='A world clock: compare time zones and find a time that works.',
                url_name='world_time:home',
                icon='fas fa-earth-americas',
                costs=cost_section,
            )
        )
        self._register_jobs()

    def _register_jobs(self):
        from apps.core.scheduler import JobSpec
        from apps.core.scheduler import register as register_job

        from . import jobs

        register_job(
            JobSpec(
                name='world_time.purge_events',
                func=jobs.purge_events,
                description=(
                    'Deletes shared World Time events that ended longer ago than the purge '
                    'period in the World Time settings; their links stop working.'
                ),
                interval=timedelta(hours=24),
                lease=timedelta(hours=1),
            )
        )
