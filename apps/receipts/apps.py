from datetime import timedelta

from django.apps import AppConfig


class ReceiptsConfig(AppConfig):
    name = 'apps.receipts'
    label = 'receipts'

    def ready(self):
        # Registers the system checks (`@register()` runs at import).
        from apps.core.products import ProductApp, register

        from . import checks  # noqa: F401

        register(
            ProductApp(
                label=self.label,
                name='Receipts',
                description='Email or snap your receipts and invoices; we file and categorize them.',
                url_name='receipts:dashboard',
                icon='fas fa-receipt',
            )
        )

        self._register_jobs()
        self._connect_signals()

    def _register_jobs(self):
        from apps.core.scheduler import JobSpec
        from apps.core.scheduler import register as register_job

        from . import jobs

        register_job(
            JobSpec(
                name='receipts.meter_storage',
                func=jobs.meter_storage,
                interval=timedelta(hours=24),
                lease=timedelta(hours=1),
            )
        )
        register_job(
            JobSpec(
                name='receipts.purge_trash',
                func=jobs.purge_trash,
                interval=timedelta(hours=24),
                lease=timedelta(hours=1),
            )
        )
        register_job(
            JobSpec(
                name='receipts.retry_due',
                func=jobs.retry_due,
                interval=timedelta(minutes=5),
                lease=timedelta(minutes=10),
            )
        )
        register_job(
            JobSpec(
                name='receipts.poll_inbound',
                func=jobs.poll_inbound,
                interval=timedelta(minutes=1),
                lease=timedelta(minutes=2),
            )
        )
        register_job(
            JobSpec(
                name='receipts.cleanup_uploads',
                func=jobs.cleanup_uploads,
                interval=timedelta(hours=1),
                lease=timedelta(minutes=30),
            )
        )

    def _connect_signals(self):
        import logging

        from django.contrib.auth import get_user_model
        from django.db.models.signals import pre_delete
        from django.dispatch import receiver

        from apps.billing.signals import credits_added

        from .models import ReceiptsAccount
        from .services import lifecycle, metering
        from .services.queue import enqueue_after_commit

        logger = logging.getLogger(__name__)

        # `weak=False`, as for file_transfer's receiver: a local closure with nothing else holding
        # it would otherwise be garbage-collected as soon as `ready()` returns, silently turning
        # this into a dead entry that never fires.
        #
        # `credits_added` is sent with `send`, not `send_robust`, so an exception here would stop
        # the remaining receivers (file_transfer's re-enable) from running: never let one escape.
        # It only queues work; `resume_account` is idempotent, and a refund this app issues itself
        # fires the signal too, which is fine for that reason.
        @receiver(credits_added, dispatch_uid='receipts.resume_on_credits_added', weak=False)
        def _resume_on_credits_added(sender, user, amount, **kwargs):
            try:
                if ReceiptsAccount.objects.filter(user=user).exists():
                    metering.resume_account.enqueue(str(user.pk))
            except Exception:
                logger.exception('Could not queue resume_account for user %s', user.pk)

        # Deleting a user deletes their receipts rows by cascade; their S3 objects (all under one
        # prefix) are deleted by a queued task, once the deletion has actually committed. The task
        # takes the plain id: the user row is gone by then.
        @receiver(
            pre_delete,
            sender=get_user_model(),
            dispatch_uid='receipts.delete_storage_on_user_delete',
            weak=False,
        )
        def _delete_storage_on_user_delete(sender, instance, **kwargs):
            try:
                enqueue_after_commit(lifecycle.delete_user_storage, str(instance.pk))
            except Exception:
                logger.exception('Could not queue deletion of receipts storage for %s', instance.pk)
