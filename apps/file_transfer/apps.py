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
        import logging

        from django.contrib.auth.signals import user_logged_in
        from django.dispatch import receiver

        from apps.billing.signals import credits_added

        from .services.claim import claim_transfers_for_user
        from .services.metering import reenable_suspended_transfers_for_user

        logger = logging.getLogger(__name__)

        # `weak=False`: `Signal.connect()` defaults to a *weak* reference, and this receiver is a
        # local closure with nothing else keeping it alive once `_connect_signals` returns -- so
        # with the default, CPython's refcounting collects it immediately after `ready()`
        # finishes, silently turning it into a dead entry in `credits_added.receivers` that never
        # fires again. `dispatch_uid` alone doesn't prevent that; it only dedupes repeat
        # `connect()` calls. The daily `meter_transfers` job's fallback re-enable check meant this
        # was masked rather than fatal (topped-up transfers still got re-enabled, just up to a day
        # later instead of immediately), but the whole point of this signal is the fast path.
        @receiver(credits_added, dispatch_uid='file_transfer.reenable_on_credits_added', weak=False)
        def _reenable_on_credits_added(sender, user, amount, **kwargs):
            reenable_suspended_transfers_for_user(user)

        # Same `weak=False` reasoning as above. Claim-on-login (spec section 6): a confirmed
        # anonymous transfer with no owner becomes this user's the moment they log in with a
        # matching, confirmed email. Only the session login page (`apps.accounts.views.login`)
        # ever fires `user_logged_in` -- signup is API-only and never starts a session (see
        # CLAUDE.md's Auth section) -- so this is the one place claiming can happen; an account
        # created with a matching but not-yet-confirmed email claims on whatever its first login
        # is *after* that email gets confirmed. Never let a bug here break someone's login.
        @receiver(user_logged_in, dispatch_uid='file_transfer.claim_on_login', weak=False)
        def _claim_on_login(sender, request, user, **kwargs):
            try:
                claim_transfers_for_user(user)
            except Exception:
                logger.exception('claim_transfers_for_user failed for user %s', user.pk)
