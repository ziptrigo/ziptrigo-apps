"""Welcome credits (issue #160): credits for a genuinely new account, with no code to type.

**Who gets them.** An account created while `BillingSettings.welcome_credits` is above 0 (staff
and superusers excluded) gets a `WelcomeCredit` row, `pending`, from the `accounts.account_created`
signal (`on_account_created`). Accounts that existed before, and accounts created while the setting
was 0, have no row and are never offered anything. The row is the once-per-user guard (one per
user, a unique constraint), so a signal that fires twice, a retried task or a user who changes
email and confirms again cannot grant twice.

**When.** `settle` runs once the email is confirmed: queued on commit by `on_account_created` for an
account created already confirmed ("Sign in with Google/Microsoft", `inv users create --confirm`)
and by `on_email_confirmed` for the emailed link and a password reset, and swept by the daily
`billing.settle_welcome_credits` job in case a task was lost. It is a no-op unless the row is
`pending`, the user is confirmed and active, and the setting is still above 0 (switched off, the row
waits).

**Genuinely new** is `accounts.services.known_emails.remember(user)`: the user must be the first
account to have confirmed the address *or any `+tag` / Gmail-dots variant of it*, ever, even if
that account was deleted or moved to another address since. Otherwise the row is `refused`
(`address_seen`) and one email says so (`send_welcome_refused_email`; the guard for once is
`refusal_emailed_at`, set by a conditional `UPDATE`). A refusal is final: changing to a fresh
address afterwards does not reopen it.

A grant is `add_credits(type=WELCOME)`, the row update and an in-app notification in one
transaction; the notification runs in its own savepoint and cannot undo the grant.
"""

import logging
from datetime import UTC, datetime

from django.db import transaction
from django.tasks import task
from django.template.loader import render_to_string
from django.urls import reverse

from apps.accounts.models import User
from apps.accounts.services import known_emails
from apps.core.services import notifications
from apps.core.services.email import send_email
from apps.core.services.queue import enqueue_after_commit

from ..models import (
    BillingSettings,
    CreditTransactionType,
    WelcomeCredit,
    WelcomeCreditStatus,
    WelcomeRefusal,
)
from .credits import add_credits

logger = logging.getLogger(__name__)

SOURCE = 'billing'
LEDGER_DESCRIPTION = 'A gift for your new account'
REFUSED_SUBJECT = 'About your ZipTrigo welcome credits'


def configured_credits() -> int:
    """The welcome credits the admin has set; 0 means off."""
    return BillingSettings.load().welcome_credits


def on_account_created(user: User) -> None:
    """`accounts.signals.account_created`: open the welcome credits of a new account. Runs in the
    creating transaction, in its own savepoint (a failure is logged and never loses the account)."""
    if user.is_staff or user.is_superuser:
        return
    try:
        with transaction.atomic():
            if not configured_credits():
                return
            WelcomeCredit.objects.get_or_create(user=user)
    except Exception:
        logger.exception('Could not open welcome credits for user %s', user.pk)
        return
    if user.email_confirmed:
        enqueue_after_commit(settle_welcome_credit, str(user.pk))


def on_email_confirmed(user: User) -> None:
    """`accounts.signals.email_confirmed` (already on commit): settle, if there is anything to."""
    enqueue_after_commit(settle_welcome_credit, str(user.pk))


def settle(user_id: str) -> str:
    """Grant or refuse the welcome credits of `user_id` if they are due. Returns the row's status
    after the call (`''` when there is no row). Idempotent."""
    with transaction.atomic():
        grant = (
            WelcomeCredit.objects.select_for_update(of=('self',))
            .select_related('user')
            .filter(user_id=user_id)
            .first()
        )
        if grant is None:
            return ''
        if grant.status != WelcomeCreditStatus.PENDING:
            return grant.status
        user = grant.user
        if not user.email_confirmed or not user.is_active or user.status != User.STATUS_ACTIVE:
            return grant.status
        if user.is_staff or user.is_superuser:
            # Made staff after signing up (the admin's add form does this): not offered either.
            return grant.status
        amount = configured_credits()
        if amount <= 0:
            return grant.status
        if not known_emails.remember(user):
            _refuse(grant)
            return grant.status
        tx = add_credits(
            user,
            amount,
            tx_type=CreditTransactionType.WELCOME,
            description=LEDGER_DESCRIPTION,
            source=SOURCE,
        )
        grant.status = WelcomeCreditStatus.GRANTED
        grant.credits = amount
        grant.transaction_id = tx.pk
        grant.decided_at = datetime.now(UTC)
        grant.save(update_fields=['status', 'credits', 'transaction', 'decided_at'])
        _notify_granted(user, amount)
        return grant.status


def _refuse(grant: WelcomeCredit) -> None:
    grant.status = WelcomeCreditStatus.REFUSED
    grant.reason = WelcomeRefusal.ADDRESS_SEEN
    grant.decided_at = datetime.now(UTC)
    grant.save(update_fields=['status', 'reason', 'decided_at'])
    enqueue_after_commit(send_welcome_refused_email, str(grant.user_id))


def _notify_granted(user: User, amount: int) -> None:
    try:
        with transaction.atomic():
            notifications.notify(
                user,
                source=SOURCE,
                title=f'Welcome! We added {amount} credits to your account',
                body='Use them in any ZipTrigo app. Your credits history shows where they go.',
                url=reverse('billing:credits-history'),
                key='welcome_credits',
            )
    except Exception:
        logger.exception('Could not notify user %s of their welcome credits', user.pk)


@task
def settle_welcome_credit(user_id: str) -> None:
    settle(user_id)


@task
def send_welcome_refused_email(user_id: str) -> None:
    """Tell a confirmed owner their account got no welcome credits, and why, once. The conditional
    `UPDATE` on `refusal_emailed_at` is the once-only guard; nothing user-controlled is in the
    subject, and the body is autoescaped."""
    claimed = WelcomeCredit.objects.filter(
        user_id=user_id,
        status=WelcomeCreditStatus.REFUSED,
        refusal_emailed_at__isnull=True,
        user__email_confirmed=True,
        user__is_active=True,
    ).update(refusal_emailed_at=datetime.now(UTC))
    if not claimed:
        return
    user = User.objects.get(pk=user_id)
    subject, text_body, html_body = render_welcome_refused_email(user)
    sent, _ = send_email(to=user.email, subject=subject, text_body=text_body, html_body=html_body)
    if not sent:
        # Every backend failed (`send_email` does not raise): release the claim so the daily job
        # tries again.
        WelcomeCredit.objects.filter(user_id=user_id).update(refusal_emailed_at=None)


def render_welcome_refused_email(user: User) -> tuple[str, str, str]:
    """The subject, text and HTML bodies of the "no welcome credits" email."""
    from django.conf import settings

    greeting = f'Hi {user.name},' if user.name else 'Hi there,'
    credits_url = settings.BASE_URL.rstrip('/') + reverse('billing:credits-history')
    text_body = (
        f'{greeting}\n\n'
        'Welcome to ZipTrigo! New accounts get free welcome credits, but yours did not get them: '
        'the email address on this account, or a variation of it (for example with a +tag or, '
        'for Gmail, different dots), was already used for a ZipTrigo account before, and the '
        'welcome credits are only for addresses that are new to us.\n\n'
        'Nothing else is affected. Your account works normally, and you can add credits or '
        f'redeem a promo code any time: {credits_url}\n\n'
        '--\n'
        'The ZipTrigo Team\n'
    )
    html_body = render_to_string(
        'billing/emails/welcome_refused.html',
        {'greeting': greeting, 'credits_url': credits_url},
    )
    return REFUSED_SUBJECT, text_body, html_body


def settle_pending(*, batch: int = 500) -> int:
    """The daily safety net (`billing.settle_welcome_credits`): settle every pending row whose
    user is confirmed and active, and send the email of a refusal whose task was lost. Pages
    through them oldest first by `created_at`, so rows `settle` leaves pending (a staff member, an
    inactive user) cannot starve the ones behind them. Does nothing while the setting is 0.
    Returns how many rows it settled or mailed. Idempotent."""
    if configured_credits() <= 0:
        return 0
    done = 0
    pending = WelcomeCredit.objects.filter(
        status=WelcomeCreditStatus.PENDING,
        user__email_confirmed=True,
        user__is_active=True,
        user__status=User.STATUS_ACTIVE,
        user__is_staff=False,
        user__is_superuser=False,
    )
    for user_id in _pages(pending, batch):
        try:
            if settle(str(user_id)) != WelcomeCreditStatus.PENDING:
                done += 1
        except Exception:
            logger.exception('Could not settle welcome credits for user %s', user_id)
    unsent = WelcomeCredit.objects.filter(
        status=WelcomeCreditStatus.REFUSED,
        refusal_emailed_at__isnull=True,
        user__email_confirmed=True,
        user__is_active=True,
    )
    for user_id in _pages(unsent, batch):
        try:
            send_welcome_refused_email.call(str(user_id))
            done += 1
        except Exception:
            logger.exception('Could not send the welcome credits email to user %s', user_id)
    return done


def _pages(queryset, batch: int):
    """The `user_id` of every row of `queryset`, oldest first, `batch` at a time (keyset paging on
    `created_at`, which the loop's own writes do not change)."""
    last = None
    while True:
        rows = queryset.order_by('created_at', 'pk')
        if last is not None:
            rows = rows.filter(created_at__gte=last[0]).exclude(created_at=last[0], pk__lte=last[1])
        page = list(rows.values_list('user_id', 'created_at', 'pk')[:batch])
        if not page:
            return
        for user_id, _, _ in page:
            yield user_id
        last = (page[-1][1], page[-1][2])
        if len(page) < batch:
            return
