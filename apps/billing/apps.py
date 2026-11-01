from datetime import timedelta

from django.apps import AppConfig


class BillingConfig(AppConfig):
    name = 'apps.billing'
    label = 'billing'

    def ready(self):
        from django.contrib.auth import get_user_model

        from apps.accounts import signup_codes
        from apps.accounts.signals import account_created, email_confirmed

        from .services import welcome
        from .services.promo import PromoSignupCodeHandler, grant_reservations

        # The register page's promo code field (issue #70): `accounts` can't import `billing`, so
        # billing hands it the handler that validates and reserves a code.
        signup_codes.register(PromoSignupCodeHandler())

        # `weak=False`: a local function would be garbage collected once `ready()` returns.
        def _grant_promo_reservations(sender, user, **kwargs):
            grant_reservations(user)

        email_confirmed.connect(
            _grant_promo_reservations,
            sender=get_user_model(),
            dispatch_uid='billing.grant_promo_reservations',
            weak=False,
        )

        # The welcome credits of a genuinely new account (issue #160). Two receivers: one opens the
        # row when the account is created (also settling an account created already confirmed,
        # which gets no `email_confirmed`), one settles when the address is confirmed later.
        def _open_welcome_credits(sender, user, **kwargs):
            welcome.on_account_created(user)

        def _settle_welcome_credits(sender, user, **kwargs):
            welcome.on_email_confirmed(user)

        account_created.connect(
            _open_welcome_credits,
            sender=get_user_model(),
            dispatch_uid='billing.open_welcome_credits',
            weak=False,
        )
        email_confirmed.connect(
            _settle_welcome_credits,
            sender=get_user_model(),
            dispatch_uid='billing.settle_welcome_credits',
            weak=False,
        )

        self._register_jobs()

    def _register_jobs(self):
        from apps.core.scheduler import JobSpec
        from apps.core.scheduler import register as register_job

        from . import jobs

        register_job(
            JobSpec(
                name='billing.release_promo_reservations',
                func=jobs.release_promo_reservations,
                interval=timedelta(hours=24),
                lease=timedelta(hours=1),
            )
        )
        register_job(
            JobSpec(
                name='billing.settle_welcome_credits',
                func=jobs.settle_welcome_credits,
                interval=timedelta(hours=24),
                lease=timedelta(hours=1),
            )
        )
