from datetime import timedelta

from django.apps import AppConfig


class NotesConfig(AppConfig):
    name = 'apps.notes'
    label = 'notes'
    verbose_name = 'Notes'

    def ready(self):
        from apps.core.products import ProductApp, register

        register(
            ProductApp(
                label=self.label,
                name='Notes',
                description='Markdown notes with code blocks, tags and share links.',
                url_name='notes:list',
                icon='fas fa-note-sticky',
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
                name='notes.purge_trash',
                func=jobs.purge_trash,
                description=(
                    'Permanently deletes notes that have been in the Trash longer '
                    'than the Trash period in the Notes settings.'
                ),
                interval=timedelta(hours=24),
                lease=timedelta(hours=1),
            )
        )
        register_job(
            JobSpec(
                name='notes.renew',
                func=jobs.renew,
                description=(
                    'Charges the renewal of paid notes that fall due, locking a note whose '
                    "owner's balance cannot cover it; unlocks notes locked earlier once the "
                    'owner can pay; and warns users whose balance will not cover the renewals '
                    'coming up.'
                ),
                interval=timedelta(hours=24),
                lease=timedelta(hours=1),
            )
        )

    def _connect_signals(self):
        import logging

        from django.dispatch import receiver

        from apps.billing.signals import credits_added

        from .services import billing

        logger = logging.getLogger(__name__)

        # `weak=False`, as for the other products' receivers: a local closure with nothing else
        # holding it would be garbage-collected as soon as `ready()` returns. `credits_added` is
        # sent with `send`, not `send_robust`, so an exception here would stop the remaining
        # receivers (file transfer's re-enable, receipts' and To-do's resume): never let one
        # escape. It only queues `resume_notes`, which is idempotent.
        @receiver(credits_added, dispatch_uid='notes.resume_on_credits_added', weak=False)
        def _resume_on_credits_added(sender, user, amount, **kwargs):
            try:
                billing.on_credits_added(user)
            except Exception:
                logger.exception('Could not queue the Notes resume for user %s', user.pk)
