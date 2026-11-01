"""Welcome credits (issue #160, `apps.billing.services.welcome`): 1,000 credits for a genuinely
new account once its email is confirmed, with no code typed."""

from datetime import UTC, datetime
from typing import cast

import pytest
from django.contrib.auth import get_user_model
from django.test import override_settings
from django.urls import reverse

from apps.accounts.models import KnownEmail, User
from apps.accounts.services import google_oauth, known_emails, microsoft_oauth
from apps.accounts.services.email_confirmation import confirm_user, send_email_confirmed
from apps.accounts.services.password_reset import PasswordResetService
from apps.accounts.services.signup import create_account
from apps.accounts.tests.factories import UserFactory
from apps.accounts.tokens import PasswordResetToken
from apps.billing.jobs import settle_welcome_credits
from apps.billing.models import (
    BillingSettings,
    CreditTransaction,
    CreditTransactionType,
    WelcomeCredit,
    WelcomeCreditStatus,
    WelcomeRefusal,
)
from apps.billing.services import get_balance, welcome
from apps.core.models import Notification

pytestmark = [
    pytest.mark.django_db,
    pytest.mark.unit,
    pytest.mark.welcome_credits,
]


@pytest.fixture
def run(django_capture_on_commit_callbacks):
    """Run what a block queued for commit (the tasks run inline under pytest)."""

    def _run():
        return django_capture_on_commit_callbacks(execute=True)

    return _run


@pytest.fixture
def sent(monkeypatch) -> list[dict]:
    messages: list[dict] = []

    def fake_send_email(**kwargs):
        messages.append(kwargs)
        return 1, 0

    monkeypatch.setattr('apps.billing.services.welcome.send_email', fake_send_email)
    return messages


def signup(run, email='new@example.com', name='New Person') -> User:
    with run():
        return create_account(name=name, email=email, password='pw-12345678')


def confirm(run, user: User) -> None:
    with run():
        confirm_user(user)


def new_confirmed(run, email: str) -> User:
    """A new account that confirms its address, as a person would."""
    user = signup(run, email)
    confirm(run, user)
    return User.objects.get(pk=user.pk)


def status_of(user: User) -> str:
    return WelcomeCredit.objects.get(user=user).status


# -- the grant ---------------------------------------------------------------------------------


def test_a_new_account_gets_1000_credits_when_it_confirms_its_email(run):
    user = signup(run)

    grant = WelcomeCredit.objects.get(user=user)
    assert grant.status == WelcomeCreditStatus.PENDING
    assert get_balance(user) == 0

    confirm(run, user)

    grant.refresh_from_db()
    assert grant.status == WelcomeCreditStatus.GRANTED
    assert grant.credits == 1000
    assert grant.decided_at is not None
    assert get_balance(user) == 1000
    tx = CreditTransaction.objects.get(user=user)
    assert (tx.amount, tx.type, tx.source) == (1000, CreditTransactionType.WELCOME, 'billing')
    assert tx.description == 'A gift for your new account'
    assert grant.transaction_id == tx.pk


def test_the_amount_is_the_billing_setting(run):
    BillingSettings.objects.update_or_create(pk=1, defaults={'welcome_credits': 250})
    user = new_confirmed(run, 'new@example.com')
    assert get_balance(user) == 250


def test_the_user_is_told_in_the_app(run):
    user = new_confirmed(run, 'new@example.com')

    notice = Notification.objects.get(user=user)
    assert notice.source == 'billing'
    assert '1000' in notice.title
    assert notice.key == 'welcome_credits'
    assert notice.url == reverse('billing:credits-history')


def test_nothing_is_granted_before_the_email_is_confirmed(run):
    user = signup(run)
    with run():
        welcome.settle(str(user.pk))
    assert get_balance(user) == 0
    assert status_of(user) == WelcomeCreditStatus.PENDING


def test_settling_and_confirming_again_never_grants_twice(run):
    user = new_confirmed(run, 'new@example.com')

    with run():
        welcome.settle(str(user.pk))
        welcome.settle(str(user.pk))
        send_email_confirmed(user)
        send_email_confirmed(user)

    assert get_balance(user) == 1000
    assert CreditTransaction.objects.filter(user=user).count() == 1
    assert Notification.objects.filter(user=user).count() == 1


def test_confirming_a_changed_address_does_not_grant_again(run):
    user = new_confirmed(run, 'old@example.com')
    user.email = 'fresh@example.com'
    user.email_confirmed = False
    user.save()

    confirm(run, user)

    assert get_balance(user) == 1000
    assert WelcomeCredit.objects.filter(user=user).count() == 1


# -- who is not new ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    'variant',
    [
        'person+shop@example.com',
        'person+a+b@example.com',
        'Person+Promo@Example.com',
    ],
)
def test_a_plus_variant_of_a_confirmed_address_is_not_new(run, sent, variant):
    first = new_confirmed(run, 'person@example.com')
    second = new_confirmed(run, variant)

    assert get_balance(first) == 1000
    assert get_balance(second) == 0
    grant = WelcomeCredit.objects.get(user=second)
    assert grant.status == WelcomeCreditStatus.REFUSED
    assert grant.reason == WelcomeRefusal.ADDRESS_SEEN
    assert len(sent) == 1


@pytest.mark.parametrize(
    ('first', 'second'),
    [
        ('first.last@gmail.com', 'firstlast@gmail.com'),
        ('firstlast@gmail.com', 'f.i.r.s.t.l.a.s.t+x@gmail.com'),
        ('first.last@gmail.com', 'first.last@googlemail.com'),
    ],
)
def test_gmail_dots_and_googlemail_are_the_same_address(run, sent, first, second):
    new_confirmed(run, first)
    other = new_confirmed(run, second)

    assert get_balance(other) == 0
    assert status_of(other) == WelcomeCreditStatus.REFUSED


def test_dots_mean_nothing_special_outside_gmail(run):
    new_confirmed(run, 'first.last@example.com')
    other = new_confirmed(run, 'firstlast@example.com')
    assert get_balance(other) == 1000


def test_an_address_a_previous_account_moved_away_from_is_not_new(run, sent):
    """Account A signs up with a@, then switches to b@. A new account with a@ is not new."""
    previous = new_confirmed(run, 'a@example.com')
    previous.email = 'b@example.com'
    previous.email_confirmed = False
    previous.save()
    confirm(run, previous)

    newcomer = new_confirmed(run, 'a@example.com')

    assert get_balance(newcomer) == 0
    assert status_of(newcomer) == WelcomeCreditStatus.REFUSED
    assert len(sent) == 1


def test_a_deleted_account_does_not_make_its_address_new_again(run, sent):
    gone = new_confirmed(run, 'a@example.com')
    gone.delete()

    newcomer = new_confirmed(run, 'a@example.com')

    assert get_balance(newcomer) == 0
    assert status_of(newcomer) == WelcomeCreditStatus.REFUSED
    assert not WelcomeCredit.objects.filter(user_id=gone.pk).exists()
    assert KnownEmail.objects.count() == 1


def test_two_unconfirmed_variants_the_first_to_confirm_gets_the_credits(run, sent):
    """Registering is not claiming: the address is only taken once somebody confirms it."""
    squatter = signup(run, 'a@example.com')
    owner = signup(run, 'a+shop@example.com')

    confirm(run, owner)
    confirm(run, squatter)

    assert get_balance(owner) == 1000
    assert get_balance(squatter) == 0
    assert status_of(squatter) == WelcomeCreditStatus.REFUSED


def test_a_refusal_is_final_even_if_the_user_moves_to_a_fresh_address(run, sent):
    new_confirmed(run, 'a@example.com')
    refused = new_confirmed(run, 'a+2@example.com')
    refused.email = 'fresh@example.com'
    refused.email_confirmed = False
    refused.save()

    confirm(run, refused)

    assert get_balance(refused) == 0
    assert len(sent) == 1


# -- who is never offered them -----------------------------------------------------------------


def test_the_setting_at_zero_turns_it_off_and_leaves_no_trace(run, sent):
    BillingSettings.objects.update_or_create(pk=1, defaults={'welcome_credits': 0})

    user = new_confirmed(run, 'new@example.com')

    assert not WelcomeCredit.objects.exists()
    assert get_balance(user) == 0
    assert not sent
    # The address is still remembered, so turning it on later does not make it new.
    assert KnownEmail.objects.count() == 1


def test_switching_it_off_while_a_row_waits_leaves_the_row_pending(run):
    user = signup(run)
    BillingSettings.objects.update_or_create(pk=1, defaults={'welcome_credits': 0})

    confirm(run, user)

    assert status_of(user) == WelcomeCreditStatus.PENDING
    assert get_balance(user) == 0


def test_accounts_that_existed_before_get_nothing(run):
    BillingSettings.objects.update_or_create(pk=1, defaults={'welcome_credits': 0})
    veteran = signup(run, 'old@example.com')
    BillingSettings.objects.update_or_create(pk=1, defaults={'welcome_credits': 1000})

    confirm(run, veteran)

    assert not WelcomeCredit.objects.exists()
    assert get_balance(veteran) == 0


def test_staff_accounts_are_not_offered_them(run):
    with run():
        staff = User.objects.create_user(
            email='staff@example.com', password='pw-12345678', name='S', is_staff=True
        )
        confirm_user(staff)
    assert not WelcomeCredit.objects.exists()


def test_an_inactive_account_gets_nothing_until_it_is_active(run):
    user = signup(run)
    user.status = User.STATUS_INACTIVE
    user.save()

    confirm(run, user)

    assert status_of(user) == WelcomeCreditStatus.PENDING
    assert get_balance(user) == 0


# -- every way an account is created ------------------------------------------------------------


def test_a_google_signup_gets_them_without_an_email_confirmation(run):
    identity = google_oauth.GoogleIdentity(
        sub='sub-1', email='g.person@gmail.com', email_verified=True, name='G Person'
    )
    with run():
        user = google_oauth.resolve_user(identity)

    assert user.email_confirmed
    assert get_balance(user) == 1000
    assert status_of(user) == WelcomeCreditStatus.GRANTED


def test_a_second_google_signup_for_the_same_gmail_is_refused(run, sent):
    google_oauth.resolve_user(
        google_oauth.GoogleIdentity(
            sub='sub-1', email='g.person@gmail.com', email_verified=True, name='G'
        )
    )
    with run():
        other = google_oauth.resolve_user(
            google_oauth.GoogleIdentity(
                sub='sub-2', email='gperson+x@gmail.com', email_verified=True, name='G2'
            )
        )

    assert get_balance(other) == 0
    assert len(sent) == 1


def test_a_microsoft_signup_gets_them_without_an_email_confirmation(run):
    identity = microsoft_oauth.MicrosoftIdentity(
        tenant_id=microsoft_oauth.PERSONAL_ACCOUNT_TENANT_ID,
        object_id='oid-1',
        email='m.person@outlook.com',
        email_domain_verified=False,
        name='M Person',
    )
    with run():
        user = microsoft_oauth.resolve_user(identity)

    assert get_balance(user) == 1000


def test_an_account_created_by_the_command_with_confirm_gets_them(run):
    with run():
        user = get_user_model().objects.create_user(
            email='cli@example.com',
            password='pw-12345678',
            name='Cli',
            email_confirmed=True,
            email_confirmed_at=datetime.now(UTC),
        )
    assert get_balance(user) == 1000


# -- the email when nothing is credited ---------------------------------------------------------


def test_the_refusal_email_goes_once_to_the_confirmed_address(run, sent):
    new_confirmed(run, 'person@example.com')
    refused = new_confirmed(run, 'person+2@example.com')

    (message,) = sent
    assert message['to'] == 'person+2@example.com'
    assert message['subject'] == welcome.REFUSED_SUBJECT
    assert 'welcome credits' in message['text_body']
    assert '/billing/' in message['html_body']
    assert 'person@example.com' not in message['text_body']  # nothing about the other account
    grant = WelcomeCredit.objects.get(user=refused)
    assert grant.refusal_emailed_at is not None

    # Running the task again, or settling again, sends nothing more.
    welcome.send_welcome_refused_email.call(str(refused.pk))
    welcome.settle(str(refused.pk))
    assert len(sent) == 1


def test_the_refusal_email_escapes_the_name_and_keeps_it_out_of_the_subject(run, sent):
    new_confirmed(run, 'person@example.com')
    with run():
        user = create_account(
            name='<script>alert(1)</script>', email='person+2@example.com', password='pw-12345678'
        )
    confirm(run, user)

    (message,) = sent
    assert '<script>' not in message['html_body']
    assert '&lt;script&gt;' in message['html_body']
    assert 'script' not in message['subject']


def test_no_refusal_email_for_an_unconfirmed_address(run, sent):
    new_confirmed(run, 'person@example.com')
    user = signup(run, 'person+2@example.com')
    WelcomeCredit.objects.filter(user=user).update(status=WelcomeCreditStatus.REFUSED)

    welcome.send_welcome_refused_email.call(str(user.pk))

    assert not sent


def test_the_refusal_email_is_not_sent_to_a_grant_that_was_given(run, sent):
    user = new_confirmed(run, 'person@example.com')
    welcome.send_welcome_refused_email.call(str(user.pk))
    assert not sent


# -- the daily safety net -----------------------------------------------------------------------


def test_the_job_settles_a_confirmed_user_whose_task_was_lost(run):
    user = signup(run)
    # Confirmed without any signal, as if the on-commit task never ran.
    User.objects.filter(pk=user.pk).update(email_confirmed=True)

    settle_welcome_credits()

    assert get_balance(user) == 1000


def test_the_job_sends_a_refusal_email_whose_task_was_lost(run, sent):
    new_confirmed(run, 'person@example.com')
    user = signup(run, 'person+2@example.com')
    User.objects.filter(pk=user.pk).update(email_confirmed=True)
    WelcomeCredit.objects.filter(user=user).update(status=WelcomeCreditStatus.REFUSED)

    settle_welcome_credits()
    settle_welcome_credits()

    assert len(sent) == 1


def test_the_job_leaves_unconfirmed_users_alone(run):
    user = signup(run)
    settle_welcome_credits()
    assert status_of(user) == WelcomeCreditStatus.PENDING


# -- the memory is shared with the rest ---------------------------------------------------------


def test_the_user_factory_accounts_behave_like_any_other(run):
    """A confirmed address written by hand (tests, the shell) is still found by `remember`."""
    existing = cast(User, UserFactory(email='x@example.com'))
    known_emails.remember(existing)
    newcomer = new_confirmed(run, 'x+1@example.com')
    assert status_of(newcomer) == WelcomeCreditStatus.REFUSED


def test_rotating_the_secret_key_makes_known_addresses_look_new_again(run):
    """The documented caveat of `known_emails`: fingerprints are keyed with `SECRET_KEY`."""
    new_confirmed(run, 'a@example.com')
    with override_settings(SECRET_KEY='a-different-secret-key-for-the-test'):
        newcomer = new_confirmed(run, 'a+2@example.com')
    assert get_balance(newcomer) == 1000


# -- review follow-ups ------------------------------------------------------------------------


def test_a_failure_remembering_the_address_never_fails_the_confirmation(run, monkeypatch, caplog):
    user = signup(run)

    def boom(user):
        raise RuntimeError('database exploded')

    monkeypatch.setattr(known_emails, 'remember', boom)
    with run():
        assert confirm_user(user) is True

    assert User.objects.get(pk=user.pk).email_confirmed
    assert 'Could not remember the confirmed address' in caplog.text


def test_a_failure_remembering_the_address_never_loses_a_new_account(monkeypatch):
    monkeypatch.setattr(known_emails, 'remember', lambda user: 1 / 0)
    user = User.objects.create_user(
        email='g@example.com', password=None, name='G', email_confirmed=True
    )
    assert User.objects.filter(pk=user.pk).exists()


def test_a_password_reset_that_confirms_the_address_settles_the_credits(run):
    user = signup(run)
    token = str(PasswordResetToken.for_user(user))
    with run():
        PasswordResetService.reset_password(user, 'newpass123', token=token)

    assert get_balance(user) == 1000
    assert status_of(user) == WelcomeCreditStatus.GRANTED


def test_a_password_reset_for_a_variant_of_a_known_address_is_refused(run, sent):
    new_confirmed(run, 'a@example.com')
    user = signup(run, 'a+2@example.com')
    token = str(PasswordResetToken.for_user(user))
    with run():
        PasswordResetService.reset_password(user, 'newpass123', token=token)

    assert get_balance(user) == 0
    assert status_of(user) == WelcomeCreditStatus.REFUSED
    assert len(sent) == 1


def test_staff_confirming_an_address_in_the_admin_settles_the_credits(run, client, admin_user):
    user = signup(run)
    client.force_login(admin_user)

    with run():
        response = client.post(
            reverse('custom_admin:accounts_user_change', args=[user.pk]),
            {
                'email': user.email,
                'name': user.name,
                'status': User.STATUS_ACTIVE,
                'is_active': 'on',
                'email_confirmed': 'on',
            },
        )

    assert response.status_code == 302
    assert get_balance(user) == 1000
    assert known_emails.remember(User.objects.get(pk=user.pk)) is True
    assert KnownEmail.objects.count() == 1


def test_a_user_made_staff_after_signing_up_is_not_offered_the_credits(run):
    user = signup(run)
    User.objects.filter(pk=user.pk).update(is_staff=True)

    confirm(run, user)
    settle_welcome_credits()

    assert status_of(user) == WelcomeCreditStatus.PENDING
    assert get_balance(user) == 0


def test_a_refusal_email_that_could_not_be_sent_is_tried_again(run, monkeypatch):
    new_confirmed(run, 'a@example.com')
    outcomes = [(0, 1), (1, 0)]
    messages: list[dict] = []

    def flaky(**kwargs):
        result = outcomes.pop(0)
        if result[0]:
            messages.append(kwargs)
        return result

    monkeypatch.setattr('apps.billing.services.welcome.send_email', flaky)
    refused = new_confirmed(run, 'a+2@example.com')
    grant = WelcomeCredit.objects.get(user=refused)
    assert grant.refusal_emailed_at is None  # the first send failed and released the claim
    assert not messages

    settle_welcome_credits()  # the daily job sends it

    grant.refresh_from_db()
    assert grant.refusal_emailed_at is not None
    assert len(messages) == 1


def test_the_job_is_not_starved_by_rows_it_can_never_settle(run):
    """Inactive users stay pending whatever happens; they must not hide the rows behind them."""
    stuck = [signup(run, f'stuck{i}@example.com') for i in range(3)]
    for user in stuck:
        User.objects.filter(pk=user.pk).update(email_confirmed=True, status=User.STATUS_INACTIVE)
    ok = signup(run, 'fine@example.com')
    User.objects.filter(pk=ok.pk).update(email_confirmed=True)

    assert welcome.settle_pending(batch=2) == 1

    assert get_balance(ok) == 1000
    assert all(status_of(user) == WelcomeCreditStatus.PENDING for user in stuck)


def test_the_job_pages_through_more_rows_than_one_batch(run):
    users = [signup(run, f'user{i}@example.com') for i in range(5)]
    User.objects.filter(pk__in=[u.pk for u in users]).update(email_confirmed=True)

    assert welcome.settle_pending(batch=2) == 5

    assert all(get_balance(user) == 1000 for user in users)


def test_the_job_does_nothing_while_the_setting_is_zero(run):
    user = signup(run)
    User.objects.filter(pk=user.pk).update(email_confirmed=True)
    BillingSettings.objects.update_or_create(pk=1, defaults={'welcome_credits': 0})

    assert welcome.settle_pending() == 0
    assert status_of(user) == WelcomeCreditStatus.PENDING
