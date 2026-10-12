import pytest

from apps.accounts.models import User
from apps.accounts.tokens import CustomAccessToken

pytestmark = [pytest.mark.django_db, pytest.mark.integration]


@pytest.fixture
def sent_confirmations(monkeypatch) -> list[str]:
    sent: list[str] = []
    monkeypatch.setattr(
        'apps.accounts.services.email_confirmation.EmailConfirmationService.send_confirmation_email',
        lambda self, user: sent.append(user.email),
    )
    return sent


def _put(client, user, payload):
    return client.put(
        '/api/account',
        payload,
        content_type='application/json',
        HTTP_AUTHORIZATION=f'Bearer {CustomAccessToken.for_user(user)}',
    )


def test_email_change_requires_reconfirmation(client, user, sent_confirmations):
    response = _put(client, user, {'email': 'new@example.com'})

    assert response.status_code == 200
    user.refresh_from_db()
    assert user.email == 'new@example.com'
    assert not user.email_confirmed
    assert user.email_confirmed_at is None
    assert sent_confirmations == ['new@example.com']
    assert 'confirm your new email' in response.json()['message']


def test_name_only_change_keeps_confirmation(client, user, sent_confirmations):
    response = _put(client, user, {'name': 'Renamed'})

    assert response.status_code == 200
    user.refresh_from_db()
    assert user.name == 'Renamed'
    assert user.email_confirmed
    assert sent_confirmations == []


def test_email_in_use_is_rejected(client, user, sent_confirmations):
    User.objects.create_user(email='taken@example.com', password='password123')

    response = _put(client, user, {'email': 'taken@example.com'})

    assert response.status_code == 400
    user.refresh_from_db()
    assert user.email == 'testuser@example.com'
    assert user.email_confirmed


def test_email_in_use_keeps_its_message(client, user, sent_confirmations):
    """The 400 is kept as is (issue #125 decided to rate limit it, not hide it)."""
    User.objects.create_user(email='taken@example.com', password='password123')

    response = _put(client, user, {'email': 'taken@example.com'})

    assert response.status_code == 400
    assert response.json()['detail'] == 'Email already in use'


def test_email_change_attempts_are_rate_limited_per_user(
    client, user, settings, sent_confirmations
):
    """Issue #125: the 400 above says whether an address has an account, so every attempted
    change counts -- successful or not -- and is charged before that check."""
    settings.RATELIMIT_ENABLE = True
    settings.RATELIMIT_RULES = {**settings.RATELIMIT_RULES, 'ACCOUNT_EMAIL_CHANGE_USER': (2, 3600)}
    User.objects.create_user(email='taken@example.com', password='password123')

    assert _put(client, user, {'email': 'taken@example.com'}).status_code == 400
    assert _put(client, user, {'email': 'taken@example.com'}).status_code == 400
    blocked_taken = _put(client, user, {'email': 'taken@example.com'})
    blocked_free = _put(client, user, {'email': 'free@example.com'})

    # Over the limit the answer is the same for a taken and a free address.
    assert blocked_taken.status_code == blocked_free.status_code == 429
    assert 'Retry-After' in blocked_taken
    user.refresh_from_db()
    assert user.email == 'testuser@example.com'
    assert user.email_confirmed


def test_email_change_rate_limit_is_per_user(client, user, settings, sent_confirmations):
    settings.RATELIMIT_ENABLE = True
    settings.RATELIMIT_RULES = {**settings.RATELIMIT_RULES, 'ACCOUNT_EMAIL_CHANGE_USER': (1, 3600)}
    other = User.objects.create_user(email='other@example.com', password='password123')

    assert _put(client, user, {'email': 'taken@example.com'}).status_code == 200
    assert _put(client, user, {'email': 'again@example.com'}).status_code == 429

    assert _put(client, other, {'email': 'other2@example.com'}).status_code == 200


def test_updates_that_do_not_change_the_email_cost_no_budget(
    client, user, settings, sent_confirmations
):
    settings.RATELIMIT_ENABLE = True
    settings.RATELIMIT_RULES = {**settings.RATELIMIT_RULES, 'ACCOUNT_EMAIL_CHANGE_USER': (1, 3600)}

    for _ in range(3):
        assert _put(client, user, {'name': 'Renamed'}).status_code == 200
        assert _put(client, user, {'name': 'Renamed', 'email': user.email}).status_code == 200

    assert _put(client, user, {'email': 'new@example.com'}).status_code == 200


def test_email_change_invalidates_a_still_pending_confirmation_for_the_old_address(
    client, user, sent_confirmations
):
    """Issue #58 follow-up: a confirmation link already sent to the address a user is leaving
    must stop working once they move away from it -- otherwise, if that address is later freed
    and claimed by someone else, the original owner's stale link could confirm the *new*
    account."""
    from apps.core.models import EmailVerification
    from apps.core.services.email_verification import EmailVerificationContext, start

    def build_email(context: EmailVerificationContext) -> tuple[str, str, str]:
        return 'subject', 'text', 'html'

    pending_id = start(user.email, 'accounts.email_confirmation', build_email=build_email)

    response = _put(client, user, {'email': 'new@example.com'})

    assert response.status_code == 200
    pending = EmailVerification.objects.get(pk=pending_id)
    assert pending.invalidated_at is not None


@pytest.fixture
def sent_notices(monkeypatch) -> list[dict]:
    """Capture what the "email changed" notice hands to `send_email`."""
    sent: list[dict] = []
    monkeypatch.setattr(
        'apps.accounts.services.email_change.send_email', lambda **kwargs: sent.append(kwargs)
    )
    return sent


def test_email_change_notifies_the_previous_confirmed_address(
    client, user, sent_confirmations, sent_notices, django_capture_on_commit_callbacks
):
    with django_capture_on_commit_callbacks(execute=True):
        response = _put(client, user, {'email': 'new@example.com'})

    assert response.status_code == 200
    assert len(sent_notices) == 1
    notice = sent_notices[0]
    assert notice['to'] == 'testuser@example.com'
    assert notice['subject'] == 'Your ZipTrigo email address was changed'
    assert 'from testuser@example.com to new@example.com' in notice['text_body']


def test_email_change_from_an_unconfirmed_address_sends_no_notice(
    client, user, sent_confirmations, sent_notices, django_capture_on_commit_callbacks
):
    user.email_confirmed = False
    user.save()

    with django_capture_on_commit_callbacks(execute=True):
        _put(client, user, {'email': 'new@example.com'})

    assert sent_notices == []


def test_name_only_change_sends_no_notice(
    client, user, sent_confirmations, sent_notices, django_capture_on_commit_callbacks
):
    with django_capture_on_commit_callbacks(execute=True):
        _put(client, user, {'name': 'Renamed'})

    assert sent_notices == []


def test_email_changed_notice_escapes_the_name(user):
    from datetime import UTC, datetime

    from apps.accounts.services.email_change import render_email_changed_notice

    user.name = '<b>Mallory</b>'
    _, text_body, html_body = render_email_changed_notice(
        user=user,
        previous_email='old@example.com',
        new_email='new@example.com',
        changed_at=datetime(2026, 10, 12, 14, 5, tzinfo=UTC),
    )

    assert '2026-10-12 at 14:05 UTC' in text_body
    assert '&lt;b&gt;Mallory&lt;/b&gt;' in html_body
    assert '<b>Mallory</b>' not in html_body
    assert 'old@example.com' in html_body
    assert 'new@example.com' in html_body
