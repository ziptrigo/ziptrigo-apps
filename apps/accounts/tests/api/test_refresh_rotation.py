"""Rotating refresh tokens (issue #252): `POST /api/auth/refresh` replaces the refresh token it is
given; a replay after the grace window revokes the whole family."""

import uuid
from datetime import timedelta

import pytest
from django.contrib.auth.models import Permission
from django.db.models import QuerySet
from django.urls import reverse
from django.utils import timezone

from apps.accounts import jobs
from apps.accounts.models import RefreshToken, User
from apps.accounts.services import refresh_tokens
from apps.accounts.tests.factories import UserFactory
from apps.accounts.tokens import CustomRefreshToken
from apps.core.scheduler import get_jobs

pytestmark = [pytest.mark.django_db, pytest.mark.integration]


def _login(client, user, password='testpass123'):
    response = client.post(
        '/api/auth/login',
        {'email': user.email, 'password': password},
        content_type='application/json',
    )
    assert response.status_code == 200
    return response.json()


def _refresh(client, token):
    return client.post(
        '/api/auth/refresh', {'refresh_token': token}, content_type='application/json'
    )


def _row(token: str) -> RefreshToken:
    return RefreshToken.objects.get(jti=CustomRefreshToken(token)['jti'])


def _age_rotation(token: str, seconds: int) -> None:
    """Pretend `token` was replaced `seconds` ago."""
    RefreshToken.objects.filter(jti=CustomRefreshToken(token)['jti']).update(
        rotated_at=timezone.now() - timedelta(seconds=seconds)
    )


def test_login_starts_a_family(client, user):
    tokens = _login(client, user)

    row = _row(tokens['refresh_token'])
    assert row.user_id == user.pk
    assert row.rotated_at is None and row.revoked_at is None
    assert row.expires_at > timezone.now() + timedelta(days=29)


def test_refresh_returns_a_new_refresh_token_that_works(client, user):
    first = _login(client, user)

    response = _refresh(client, first['refresh_token'])

    assert response.status_code == 200
    body = response.json()
    assert set(body) == {'access_token', 'refresh_token', 'token_type', 'expires_in'}
    assert body['token_type'] == 'Bearer'
    assert body['refresh_token'] != first['refresh_token']
    assert _row(first['refresh_token']).rotated_at is not None
    assert _row(body['refresh_token']).family == _row(first['refresh_token']).family
    # The new access token authenticates; the new refresh token refreshes again.
    me = client.get('/api/account', HTTP_AUTHORIZATION=f'Bearer {body["access_token"]}')
    assert me.status_code == 200
    assert _refresh(client, body['refresh_token']).status_code == 200


def test_the_new_token_lives_a_full_lifetime_from_now(client, user):
    first = _login(client, user)
    RefreshToken.objects.update(expires_at=timezone.now() + timedelta(days=1))

    body = _refresh(client, first['refresh_token']).json()

    # Sliding: the successor is a fresh 30 days, whatever the old one had left.
    assert _row(body['refresh_token']).expires_at > timezone.now() + timedelta(days=29)


def test_old_token_may_be_used_again_within_the_grace_window(client, user):
    first = _login(client, user)
    second = _refresh(client, first['refresh_token']).json()

    again = _refresh(client, first['refresh_token'])

    assert again.status_code == 200
    third = again.json()
    assert third['refresh_token'] not in (first['refresh_token'], second['refresh_token'])
    # Both successors are live siblings of one family.
    assert _row(third['refresh_token']).family == _row(second['refresh_token']).family
    assert _refresh(client, second['refresh_token']).status_code == 200
    assert _refresh(client, third['refresh_token']).status_code == 200


def test_old_token_after_the_grace_window_is_refused_and_revokes_the_family(client, user):
    first = _login(client, user)
    second = _refresh(client, first['refresh_token']).json()
    third = _refresh(client, second['refresh_token']).json()
    _age_rotation(first['refresh_token'], 61)

    replay = _refresh(client, first['refresh_token'])

    assert replay.status_code == 401
    assert replay.json() == {'detail': 'Invalid or expired refresh token'}
    # Every token of the family is dead, the newest one included.
    assert _refresh(client, third['refresh_token']).status_code == 401
    assert _refresh(client, second['refresh_token']).status_code == 401
    family = _row(first['refresh_token']).family
    assert not RefreshToken.objects.filter(family=family, revoked_at__isnull=True).exists()


def test_grace_window_is_a_setting(client, user, settings):
    settings.JWT_REFRESH_GRACE_SECONDS = 5
    first = _login(client, user)
    _refresh(client, first['refresh_token'])

    _age_rotation(first['refresh_token'], 4)
    assert _refresh(client, first['refresh_token']).status_code == 200
    _age_rotation(first['refresh_token'], 6)
    assert _refresh(client, first['refresh_token']).status_code == 401


def test_a_replay_leaves_other_families_alone(client, user):
    laptop = _login(client, user)
    phone = _login(client, user)
    _refresh(client, laptop['refresh_token'])
    _age_rotation(laptop['refresh_token'], 120)

    assert _refresh(client, laptop['refresh_token']).status_code == 401

    assert _row(laptop['refresh_token']).family != _row(phone['refresh_token']).family
    assert _refresh(client, phone['refresh_token']).status_code == 200


def test_a_legacy_token_without_a_row_is_accepted_once_and_migrated(client, user):
    legacy = str(CustomRefreshToken.for_user(user))
    assert not RefreshToken.objects.exists()

    response = _refresh(client, legacy)

    assert response.status_code == 200
    body = response.json()
    assert body['refresh_token'] != legacy
    assert _row(legacy).rotated_at is not None
    assert _row(body['refresh_token']).family == _row(legacy).family
    assert _refresh(client, body['refresh_token']).status_code == 200


def test_a_legacy_token_is_not_good_forever(client, user):
    legacy = str(CustomRefreshToken.for_user(user))
    body = _refresh(client, legacy).json()

    # Within the window it may be used again; after it, it is a replay like any other.
    assert _refresh(client, legacy).status_code == 200
    _age_rotation(legacy, 61)
    assert _refresh(client, legacy).status_code == 401
    assert _refresh(client, body['refresh_token']).status_code == 401


def test_two_first_uses_of_a_legacy_token_share_one_row(user):
    """Concurrent first uses both reach `get_or_create`; the second finds the first's row."""
    legacy = CustomRefreshToken.for_user(user)

    a = refresh_tokens.rotate(legacy, user)
    b = refresh_tokens.rotate(legacy, user)

    assert RefreshToken.objects.filter(jti=legacy['jti']).count() == 1
    assert _row(str(a)).family == _row(str(b)).family == _row(str(legacy)).family


def test_two_first_uses_race_to_create_the_legacy_row(user, monkeypatch):
    """The other caller inserts the row between our lookup and our insert: `get_or_create` hits
    the unique `jti` (IntegrityError) and re-reads the winner's row, so both callers end up on one
    row and one family. SQLite can't interleave two real transactions, so the race is staged: the
    first lookup misses although the row already exists."""
    legacy = CustomRefreshToken.for_user(user)
    winner = RefreshToken.objects.create(
        jti=str(legacy['jti']),
        user=user,
        family=uuid.uuid4(),
        expires_at=timezone.now() + timedelta(days=30),
    )
    real_get = QuerySet.get
    misses = []

    def get_missing_once(self, *args, **kwargs):
        if self.model is RefreshToken and not misses:
            misses.append(True)
            raise RefreshToken.DoesNotExist
        return real_get(self, *args, **kwargs)

    monkeypatch.setattr(QuerySet, 'get', get_missing_once)

    a = refresh_tokens.rotate(legacy, user)
    b = refresh_tokens.rotate(legacy, user)

    assert misses == [True]
    assert RefreshToken.objects.filter(jti=legacy['jti']).count() == 1
    assert _row(str(a)).family == _row(str(b)).family == winner.family


def test_a_token_has_at_most_max_successors_within_the_window(client, user):
    first = _login(client, user)
    siblings = [
        _refresh(client, first['refresh_token']) for _ in range(refresh_tokens.MAX_SUCCESSORS)
    ]
    assert [r.status_code for r in siblings] == [200] * refresh_tokens.MAX_SUCCESSORS
    assert _row(first['refresh_token']).successor_count == refresh_tokens.MAX_SUCCESSORS

    over = _refresh(client, first['refresh_token'])

    # Inside the window, but past the cap: a replay, so the whole family dies.
    assert over.status_code == 401
    assert over.json() == {'detail': 'Invalid or expired refresh token'}
    for response in siblings:
        assert _refresh(client, response.json()['refresh_token']).status_code == 401


def test_refresh_is_rate_limited_per_user(client, user, settings):
    settings.RATELIMIT_ENABLE = True
    settings.RATELIMIT_RULES = {**settings.RATELIMIT_RULES, 'REFRESH_USER': (2, 3600)}
    token = _login(client, user)['refresh_token']
    for _ in range(2):
        token = _refresh(client, token).json()['refresh_token']

    response = _refresh(client, token)

    assert response.status_code == 429
    assert 'Retry-After' in response.headers
    # The refused request rotated nothing.
    assert _row(token).rotated_at is None


def test_garbage_does_not_spend_the_refresh_budget(client, user, settings):
    settings.RATELIMIT_ENABLE = True
    settings.RATELIMIT_RULES = {**settings.RATELIMIT_RULES, 'REFRESH_USER': (1, 3600)}
    token = _login(client, user)['refresh_token']

    assert _refresh(client, 'not-a-token').status_code == 401
    assert _refresh(client, 'x.y.z').status_code == 401

    assert _refresh(client, token).status_code == 200


def test_the_grace_setting_check(settings):
    from apps.accounts.checks import check_refresh_grace

    for good in (0, 60, 3600):
        settings.JWT_REFRESH_GRACE_SECONDS = good
        assert check_refresh_grace() == []
    for bad in (-1, 3601):
        settings.JWT_REFRESH_GRACE_SECONDS = bad
        (warning,) = check_refresh_grace()
        assert warning.id == 'accounts.W005' and warning.level == 30


def test_password_change_revokes_the_rotated_token(client, user):
    first = _login(client, user)
    second = _refresh(client, first['refresh_token']).json()

    user.set_password('changed789')
    user.save(update_fields=['password'])

    assert _refresh(client, second['refresh_token']).status_code == 401
    assert _refresh(client, first['refresh_token']).status_code == 401
    # A fresh login starts over.
    new = _login(client, user, 'changed789')
    assert _refresh(client, new['refresh_token']).status_code == 200


def test_an_inactive_user_is_refused_without_rotating(client, user):
    first = _login(client, user)
    user.status = User.STATUS_INACTIVE
    user.save(update_fields=['status'])

    assert _refresh(client, first['refresh_token']).status_code == 403

    assert _row(first['refresh_token']).rotated_at is None


def test_garbage_and_access_tokens_are_refused(client, user):
    tokens = _login(client, user)

    assert _refresh(client, 'not-a-token').status_code == 401
    assert _refresh(client, tokens['access_token']).status_code == 401


def test_a_failed_refresh_creates_no_token(client, user):
    first = _login(client, user)
    _refresh(client, first['refresh_token'])
    _age_rotation(first['refresh_token'], 120)
    before = RefreshToken.objects.count()

    _refresh(client, first['refresh_token'])

    assert RefreshToken.objects.count() == before


# -- purge job --


def test_purge_deletes_only_expired_rows():
    user = UserFactory()
    now = timezone.now()
    for i, delta in enumerate([-timedelta(days=1), -timedelta(seconds=1), timedelta(days=1)]):
        RefreshToken.objects.create(
            jti=f'jti-{i}', user=user, family=uuid.uuid4(), expires_at=now + delta
        )

    jobs.purge_refresh_tokens()

    assert list(RefreshToken.objects.values_list('jti', flat=True)) == ['jti-2']


def test_purge_works_in_batches(monkeypatch):
    monkeypatch.setattr(jobs, 'PURGE_BATCH', 2)
    user = UserFactory()
    for i in range(5):
        RefreshToken.objects.create(
            jti=f'old-{i}',
            user=user,
            family=uuid.uuid4(),
            expires_at=timezone.now() - timedelta(days=1),
        )

    jobs.purge_refresh_tokens()

    assert not RefreshToken.objects.exists()


def test_the_purge_job_is_registered():
    specs = {job.name: job for job in get_jobs()}

    spec = specs['accounts.purge_refresh_tokens']
    assert spec.func is jobs.purge_refresh_tokens
    assert spec.interval == timedelta(hours=24)


# -- admin --


@pytest.fixture
def admin_client(client, admin_user):
    client.force_login(admin_user)
    return client


def test_admin_filters_by_revoked_and_rotated(admin_client, user):
    live = refresh_tokens.issue(user)
    rotated = refresh_tokens.issue(user)
    refresh_tokens.rotate(rotated, user)
    RefreshToken.objects.filter(jti=live['jti']).update(revoked_at=timezone.now())
    url = reverse('custom_admin:accounts_refreshtoken_changelist')

    def ids(**params):
        response = admin_client.get(url, params)
        assert response.status_code == 200
        return {row.jti for row in response.context['cl'].result_list}

    assert ids(revoked='yes') == {live['jti']}
    assert live['jti'] not in ids(revoked='no')
    assert ids(rotated='yes') == {rotated['jti']}
    assert rotated['jti'] not in ids(rotated='no')


def test_admin_lists_tokens_read_only(admin_client, user):
    refresh_tokens.issue(user)

    listing = admin_client.get(reverse('custom_admin:accounts_refreshtoken_changelist'))
    assert listing.status_code == 200
    assert user.email in listing.content.decode()
    assert admin_client.get(reverse('custom_admin:accounts_refreshtoken_add')).status_code == 403


def test_deleting_a_user_deletes_their_refresh_tokens(admin_client, regular_user):
    refresh_tokens.issue(regular_user)

    response = admin_client.post(
        reverse('custom_admin:accounts_user_delete', args=[regular_user.pk]), {'post': 'yes'}
    )

    assert response.status_code == 302
    assert not RefreshToken.objects.exists()


def _grant(user, *codenames):
    """Make `user` staff holding exactly these permissions."""
    user.is_staff = True
    user.save(update_fields=['is_staff'])
    user.user_permissions.add(*Permission.objects.filter(codename__in=codenames))
    return User.objects.get(pk=user.pk)  # drop the cached permissions


def test_staff_needs_the_delete_permission_to_delete_a_user_with_tokens(client, regular_user):
    staff = _grant(UserFactory(), 'view_user', 'delete_user')
    refresh_tokens.issue(regular_user)
    client.force_login(staff)
    url = reverse('custom_admin:accounts_user_delete', args=[regular_user.pk])

    assert client.post(url, {'post': 'yes'}).status_code == 403

    client.force_login(_grant(staff, 'delete_refreshtoken'))
    assert client.post(url, {'post': 'yes'}).status_code == 302
