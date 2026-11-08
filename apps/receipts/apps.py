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
                description=(
                    'Charges each Receipts account one day of storage for its files, '
                    "pauses accounts that can't pay, and resumes paused accounts "
                    'whose owner has topped up.'
                ),
                interval=timedelta(hours=24),
                lease=timedelta(hours=1),
            )
        )
        register_job(
            JobSpec(
                name='receipts.purge_trash',
                func=jobs.purge_trash,
                description=(
                    'Permanently deletes receipts (rows and files) that have been in '
                    'the Trash longer than the Trash period in the Receipts settings.'
                ),
                interval=timedelta(hours=24),
                lease=timedelta(hours=1),
            )
        )
        register_job(
            JobSpec(
                name='receipts.retry_due',
                func=jobs.retry_due,
                description=(
                    'Queues receipts whose AI reading is due for a retry, recovers '
                    'receipts stuck in processing for over 30 minutes, and queues '
                    'ones nobody picked up.'
                ),
                interval=timedelta(minutes=5),
                lease=timedelta(minutes=10),
            )
        )
        register_job(
            JobSpec(
                name='receipts.poll_inbound',
                func=jobs.poll_inbound,
                description=(
                    'Checks the S3 inbox for receipts emailed in and queues each new '
                    'message to be read.'
                ),
                interval=timedelta(minutes=1),
                lease=timedelta(minutes=2),
            )
        )
        register_job(
            JobSpec(
                name='receipts.purge_inbound_messages',
                func=jobs.purge_inbound_messages,
                description=(
                    'Deletes the records (and leftover files) of processed inbound '
                    'emails older than 30 days that matched no inbox address, such as '
                    'junk mail.'
                ),
                interval=timedelta(hours=24),
                lease=timedelta(hours=1),
            )
        )
        register_job(
            JobSpec(
                name='receipts.link_vendors',
                func=jobs.link_vendors,
                description=(
                    'Links receipts that have a vendor name but no vendor record to '
                    "the user's vendor of that name, creating one when needed."
                ),
                interval=timedelta(hours=1),
                lease=timedelta(minutes=30),
            )
        )
        register_job(
            JobSpec(
                name='receipts.cleanup_uploads',
                func=jobs.cleanup_uploads,
                description=(
                    'Removes receipt uploads that were started but not completed within 24 hours.'
                ),
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
        from apps.core.services.queue import enqueue_after_commit

        from .models import ReceiptsAccount
        from .services import lifecycle, metering

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
