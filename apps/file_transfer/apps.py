from datetime import timedelta

from django.apps import AppConfig


class FileTransferConfig(AppConfig):
    name = 'apps.file_transfer'
    label = 'file_transfer'

    def ready(self):
        from apps.core.products import ProductApp, register

        register(
            ProductApp(
                label=self.label,
                name='File Transfer',
                description='Send large files to anyone with a link that expires.',
                url_name='file_transfer:dashboard',
                icon='fas fa-paper-plane',
            )
        )

        self._register_jobs()
        self._connect_signals()

    def _register_jobs(self):
        from apps.core.scheduler import JobSpec
        from apps.core.scheduler import register as register_job

        from . import jobs

        # `meter_transfers`'s spec default is a fixed daily time (03:00 UTC); the scheduler only
        # supports intervals, so it runs every 24h from whenever it first runs instead (documented
        # in CLAUDE.md). The other three intervals match the spec directly.
        register_job(
            JobSpec(
                name='file_transfer.meter_transfers',
                func=jobs.meter_transfers,
                interval=timedelta(hours=24),
                lease=timedelta(hours=1),
            )
        )
        register_job(
            JobSpec(
                name='file_transfer.expire_transfers',
                func=jobs.expire_transfers,
                interval=timedelta(minutes=5),
                lease=timedelta(minutes=10),
            )
        )
        register_job(
            JobSpec(
                name='file_transfer.cleanup_drafts',
                func=jobs.cleanup_drafts,
                interval=timedelta(hours=1),
                lease=timedelta(minutes=30),
            )
        )
        register_job(
            JobSpec(
                name='file_transfer.purge_download_ips',
                func=jobs.purge_download_ips,
                interval=timedelta(hours=24),
                lease=timedelta(hours=1),
            )
        )

    def _connect_signals(self):
        from django.dispatch import receiver

        from apps.billing.signals import credits_added

        from .services.metering import reenable_suspended_transfers_for_user

        @receiver(credits_added, dispatch_uid='file_transfer.reenable_on_credits_added')
        def _reenable_on_credits_added(sender, user, amount, **kwargs):
            reenable_suspended_transfers_for_user(user)
