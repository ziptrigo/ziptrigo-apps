from datetime import timedelta

from django.apps import AppConfig


class TodoConfig(AppConfig):
    name = 'apps.todo'
    label = 'todo'
    verbose_name = 'To-do'

    def ready(self):
        # Registers the system checks (`@register()` runs at import).
        from apps.core.products import ProductApp, register

        from . import checks  # noqa: F401

        register(
            ProductApp(
                label=self.label,
                name='To-do',
                description='Tasks with due dates, labels and reminders, repeating when you need them.',
                url_name='todo:today',
                icon='fas fa-check-square',
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
                name='todo.purge_trash',
                func=jobs.purge_trash,
                interval=timedelta(hours=24),
                lease=timedelta(hours=1),
            )
        )
        register_job(
            JobSpec(
                name='todo.meter_storage',
                func=jobs.meter_storage,
                interval=timedelta(hours=24),
                lease=timedelta(hours=1),
            )
        )
        register_job(
            JobSpec(
                name='todo.cleanup_uploads',
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
        from apps.core.services.queue import enqueue_after_commit

        from .services import billing, lifecycle

        logger = logging.getLogger(__name__)

        # `weak=False`, as for receipts' and file_transfer's receivers: a local closure with nothing
        # else holding it would be garbage-collected as soon as `ready()` returns.
        #
        # `credits_added` is sent with `send`, not `send_robust`, so an exception here would stop
        # the remaining receivers (file transfer's re-enable, receipts' resume) from running:
        # never let one escape. It only queues work (`settle_unpaid_tasks`, and `resume_account`
        # for frozen attachment storage), and both are idempotent.
        @receiver(credits_added, dispatch_uid='todo.settle_on_credits_added', weak=False)
        def _settle_on_credits_added(sender, user, amount, **kwargs):
            try:
                billing.on_credits_added(user)
            except Exception:
                logger.exception('Could not queue the To-do work for user %s', user.pk)

        # Deleting a user deletes their To-do rows by cascade; their S3 objects (all under one
        # prefix) are deleted by a queued task, once the deletion has actually committed. The task
        # takes the plain id: the user row is gone by then.
        @receiver(
            pre_delete,
            sender=get_user_model(),
            dispatch_uid='todo.delete_storage_on_user_delete',
            weak=False,
        )
        def _delete_storage_on_user_delete(sender, instance, **kwargs):
            try:
                enqueue_after_commit(lifecycle.delete_user_storage, str(instance.pk))
            except Exception:
                logger.exception('Could not queue deletion of to-do storage for %s', instance.pk)
