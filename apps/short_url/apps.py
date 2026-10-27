from datetime import timedelta

from django.apps import AppConfig


class ShortUrlConfig(AppConfig):
    name = 'apps.short_url'
    label = 'short_url'
    verbose_name = 'Short URLs'

    def ready(self):
        # Registers the system checks (`@register()` runs at import).
        from django.contrib.auth import get_user_model
        from django.db.models.signals import pre_delete
        from django.dispatch import receiver

        from apps.core.products import ProductApp, register

        from . import checks  # noqa: F401

        # `weak=False`, as for receipts' receiver: a local closure would otherwise be garbage
        # collected as soon as `ready()` returns.
        @receiver(
            pre_delete,
            sender=get_user_model(),
            dispatch_uid='short_url.retire_codes_on_user_delete',
            weak=False,
        )
        def _retire_codes_on_user_delete(sender, instance, **kwargs):
            from .services.codes import retire_codes_of

            retire_codes_of(instance)

        register(
            ProductApp(
                label=self.label,
                name='Short URLs',
                description='Shorten a link, track it, and share it as a QR code.',
                url_name='short_url:dashboard',
                icon='fas fa-link',
            )
        )

        self._register_jobs()

    def _register_jobs(self):
        from apps.core.scheduler import JobSpec
        from apps.core.scheduler import register as register_job

        from . import jobs

        register_job(
            JobSpec(
                name='short_url.rollup_clicks',
                func=jobs.rollup_clicks,
                interval=timedelta(hours=1),
                lease=timedelta(minutes=30),
            )
        )
        register_job(
            JobSpec(
                name='short_url.purge_click_events',
                func=jobs.purge_click_events,
                interval=timedelta(hours=24),
                lease=timedelta(hours=1),
            )
        )
