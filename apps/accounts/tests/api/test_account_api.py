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


def _put(client, user, payload, password='testpass123'):
    """`PUT /api/account` as `user` (the `user` fixture's password by default, which a change of
    email needs); `password=None` sends none."""
    if password is not None:
        payload = {**payload, 'current_password': password}
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


def test_email_change_requires_the_current_password(client, user, sent_confirmations):
    """A stolen bearer token must not be enough to move the account to another address."""
    missing = _put(client, user, {'email': 'new@example.com'}, password=None)
    blank = _put(client, user, {'email': 'new@example.com'}, password='')
    wrong = _put(client, user, {'email': 'new@example.com'}, password='not-the-password')

    assert missing.status_code == blank.status_code == wrong.status_code == 400
    assert 'required' in missing.json()['detail']
    assert 'incorrect' in wrong.json()['detail']
    user.refresh_from_db()
    assert user.email == 'testuser@example.com'
    assert user.email_confirmed
    assert sent_confirmations == []


def test_checking_the_password_does_not_rehash_it(client, user, settings, sent_confirmations):
    """A rehash on success would change the `pwd` claim and so revoke the caller's own tokens."""
    from django.contrib.auth.hashers import make_password

    settings.PASSWORD_HASHERS = [
        'django.contrib.auth.hashers.PBKDF2PasswordHasher',
        'django.contrib.auth.hashers.MD5PasswordHasher',  # an older hasher than the preferred
    ]
    user.password = make_password('testpass123', hasher='md5')
    user.save(update_fields=['password'])
    old_hash = user.password
    token = CustomAccessToken.for_user(user)

    response = client.put(
        '/api/account',
        {'email': 'new@example.com', 'current_password': 'testpass123'},
        content_type='application/json',
        HTTP_AUTHORIZATION=f'Bearer {token}',
    )

    assert response.status_code == 200
    user.refresh_from_db()
    assert user.password == old_hash
    again = client.put(
        '/api/account',
        {'name': 'Still In'},
        content_type='application/json',
        HTTP_AUTHORIZATION=f'Bearer {token}',
    )
    assert again.status_code == 200


def test_wrong_password_attempts_count_against_the_email_change_budget(
    client, user, settings, sent_confirmations
):
    settings.RATELIMIT_ENABLE = True
    settings.RATELIMIT_RULES = {**settings.RATELIMIT_RULES, 'ACCOUNT_EMAIL_CHANGE_USER': (2, 3600)}

    assert _put(client, user, {'email': 'a@example.com'}, password='wrong1').status_code == 400
    assert _put(client, user, {'email': 'a@example.com'}, password='wrong2').status_code == 400
    # The budget is spent, so even the right password is refused for now.
    assert _put(client, user, {'email': 'a@example.com'}).status_code == 429
    user.refresh_from_db()
    assert user.email == 'testuser@example.com'


def test_a_wrong_password_does_not_reveal_whether_an_address_is_taken(
    client, user, sent_confirmations
):
    User.objects.create_user(email='taken@example.com', password='password123')

    response = _put(client, user, {'email': 'taken@example.com'}, password='wrong')

    assert response.status_code == 400
    assert response.json()['detail'] == 'Your current password is incorrect.'


def test_an_account_without_a_usable_password_cannot_change_its_email_here(
    client, user, sent_confirmations
):
    """A Google-only account has no password to re-authenticate with, so the change is refused
    instead of waved through. (Making the password unusable also revokes the tokens issued before
    it, so the handler is reached with the user fetched directly, not through a token.)"""
    from types import SimpleNamespace

    from ninja.errors import HttpError

    from apps.accounts.routers.account import update_account
    from apps.accounts.schemas import AccountUpdateRequest

    user.set_unusable_password()
    user.save()
    payload = AccountUpdateRequest(email='new@example.com', current_password='anything')

    with pytest.raises(HttpError) as excinfo:
        update_account(SimpleNamespace(auth=user), payload)

    assert excinfo.value.status_code == 400
    assert 'Set a password' in excinfo.value.message
    user.refresh_from_db()
    assert user.email == 'testuser@example.com'


def test_name_only_change_needs_no_password(client, user, sent_confirmations):
    response = _put(client, user, {'name': 'Renamed'}, password=None)

    assert response.status_code == 200
    user.refresh_from_db()
    assert user.name == 'Renamed'


def test_name_longer_than_the_model_field_is_a_422_not_a_500(client, user, sent_confirmations):
    from apps.accounts.schemas.account import NAME_MAX_LENGTH

    assert NAME_MAX_LENGTH == User._meta.get_field('name').max_length  # ty: ignore[unresolved-attribute]

    ok = _put(client, user, {'name': 'x' * NAME_MAX_LENGTH}, password=None)
    too_long = _put(client, user, {'name': 'x' * (NAME_MAX_LENGTH + 1)}, password=None)

    assert ok.status_code == 200
    assert too_long.status_code == 422


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


def test_email_change_429_drops_a_name_sent_with_it(client, user, settings, sent_confirmations):
    """A 429 refuses the whole update on purpose: nothing was written yet, so the name sent along
    with the email isn't saved either."""
    settings.RATELIMIT_ENABLE = True
    settings.RATELIMIT_RULES = {**settings.RATELIMIT_RULES, 'ACCOUNT_EMAIL_CHANGE_USER': (1, 3600)}
    assert _put(client, user, {'email': 'first@example.com'}).status_code == 200

    response = _put(client, user, {'name': 'Renamed', 'email': 'second@example.com'})

    assert response.status_code == 429
    after = User.objects.get(pk=user.pk)
    assert after.name == 'Test User'
    assert after.email == 'first@example.com'


def test_email_change_rate_limit_is_per_user(client, user, settings, sent_confirmations):
    settings.RATELIMIT_ENABLE = True
    settings.RATELIMIT_RULES = {**settings.RATELIMIT_RULES, 'ACCOUNT_EMAIL_CHANGE_USER': (1, 3600)}
    other = User.objects.create_user(email='other@example.com', password='password123')

    assert _put(client, user, {'email': 'taken@example.com'}).status_code == 200
    assert _put(client, user, {'email': 'again@example.com'}).status_code == 429

    assert (
        _put(client, other, {'email': 'other2@example.com'}, password='password123').status_code
        == 200
    )


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
