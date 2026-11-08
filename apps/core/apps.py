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

        # Web Push (issue #239): its system check, and signing out forgets this device's
        # subscription. Registered in every process, unlike `checks` above.
        from django.contrib.auth.signals import user_logged_out

        from . import push_checks  # noqa: F401

        user_logged_out.connect(
            _forget_push_subscription, weak=False, dispatch_uid='core.push.logout'
        )

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
                description='Deletes email verification codes and links older than 30 days.',
                interval=timedelta(hours=24),
                lease=timedelta(hours=1),
            )
        )
        register_job(
            JobSpec(
                name='core.purge_expired_rate_limit_counters',
                func=jobs.purge_expired_rate_limit_counters,
                description=(
                    'Deletes rate-limit counters whose time window has ended (only '
                    'needed when the counters are stored in the database, not Redis).'
                ),
                interval=timedelta(hours=1),
                lease=timedelta(minutes=30),
            )
        )
        register_job(
            JobSpec(
                name='core.purge_old_task_results',
                func=jobs.purge_old_task_results,
                description=(
                    'Deletes the results of finished background tasks older than 14 '
                    'days. Tasks that are waiting or running are never touched.'
                ),
                interval=timedelta(hours=24),
                lease=timedelta(hours=1),
            )
        )
        register_job(
            JobSpec(
                name='core.purge_old_notifications',
                func=jobs.purge_old_notifications,
                description=(
                    'Deletes in-app notifications (the header bell) older than 90 '
                    'days, read or not.'
                ),
                interval=timedelta(hours=24),
                lease=timedelta(hours=1),
            )
        )
        register_job(
            JobSpec(
                name='core.clear_expired_sessions',
                func=jobs.clear_expired_sessions,
                description=(
                    "Deletes expired login sessions from the database (what Django's "
                    'clearsessions command does).'
                ),
                interval=timedelta(hours=24),
                lease=timedelta(hours=1),
            )
        )


def _forget_push_subscription(sender, request=None, user=None, **kwargs):
    """`user_logged_out` receiver: delete the push subscription this session saved, so a shared
    device stops getting the signed-out user's notifications. Never raises: a sign-out must not
    fail over it."""
    import logging

    from .services import push

    try:
        push.forget_session_subscription(getattr(request, 'session', None), user)
    except Exception:
        logging.getLogger(__name__).exception('Could not delete the push subscription on sign-out')
