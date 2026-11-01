import logging

from django.apps import AppConfig

logger = logging.getLogger(__name__)


class AccountsConfig(AppConfig):
    name = 'apps.accounts'
    label = 'accounts'

    def ready(self):
        # Registers the system checks (`@register()` runs at import).
        from django.contrib.auth import get_user_model
        from django.db.models.signals import post_save

        from . import checks  # noqa: F401

        post_save.connect(
            _announce_new_account,
            sender=get_user_model(),
            dispatch_uid='accounts.announce_new_account',
            weak=False,
        )


def _announce_new_account(sender, instance, created, raw=False, **kwargs):
    """A new `User` row: remember its address when it is already confirmed (an account created
    through "Sign in with Google/Microsoft" never gets an `email_confirmed` signal), then send
    `signals.account_created`. See `services.known_emails` and `signals.account_created`."""
    if not created or raw:
        return
    from .services import known_emails
    from .signals import account_created

    if instance.email_confirmed:
        known_emails.remember(instance)
    for _, result in account_created.send_robust(sender=sender, user=instance):
        if isinstance(result, Exception):
            logger.error(
                'account_created receiver failed for user %s', instance.pk, exc_info=result
            )
