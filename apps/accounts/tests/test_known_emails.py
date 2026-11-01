"""`apps.accounts.services.known_emails` and `KnownEmail` (issue #160): the memory of which
addresses were ever confirmed on an account, which the welcome credits rely on."""

import importlib
from datetime import UTC, datetime
from typing import cast

import pytest
from django.apps import apps as django_apps
from django.test import override_settings

from apps.accounts.models import KnownEmail, User
from apps.accounts.services import known_emails
from apps.accounts.services.email_confirmation import confirm_user
from apps.accounts.tests.factories import UserFactory

pytestmark = [pytest.mark.django_db, pytest.mark.unit]


def _user(email: str, *, confirmed: bool = True, **fields) -> User:
    user = cast(User, UserFactory(email=email, **fields))
    if confirmed:
        user.email_confirmed = True
        user.email_confirmed_at = datetime.now(UTC)
        user.save(update_fields=['email_confirmed', 'email_confirmed_at'])
    return user


@pytest.mark.parametrize(
    ('email', 'expected'),
    [
        ('Alice@Example.COM', 'alice@example.com'),
        ('alice+news@example.com', 'alice@example.com'),
        ('alice+news+more@example.com', 'alice@example.com'),
        ('  alice@example.com ', 'alice@example.com'),
        # Dots only mean nothing at Gmail.
        ('first.last@example.com', 'first.last@example.com'),
        ('first.last@gmail.com', 'firstlast@gmail.com'),
        ('f.i.r.s.t.l.a.s.t+x@gmail.com', 'firstlast@gmail.com'),
        ('first.last@googlemail.com', 'firstlast@gmail.com'),
        # Nothing before the plus: keep what is there rather than an empty local part.
        ('+tag@example.com', '+tag@example.com'),
        ('no-at-sign', 'no-at-sign'),
    ],
)
def test_canonical_email(email, expected):
    assert known_emails.canonical_email(email) == expected


def test_variants_share_a_fingerprint_and_different_people_do_not():
    same = {'a@x.com', 'A@X.com', 'a+1@x.com', 'a+b+c@x.com'}
    assert len({known_emails.fingerprint(e) for e in same}) == 1
    assert known_emails.fingerprint('a@x.com') != known_emails.fingerprint('b@x.com')
    assert known_emails.fingerprint('a@x.com') != known_emails.fingerprint('a@y.com')


def test_the_fingerprint_is_keyed_and_hides_the_address():
    plain = known_emails.fingerprint('a@x.com')
    assert len(plain) == 64
    assert 'a@x.com' not in plain
    with override_settings(SECRET_KEY='another-secret-key'):
        assert known_emails.fingerprint('a@x.com') != plain


def test_remember_makes_the_first_account_the_holder_and_nobody_else():
    first = _user('a@x.com', confirmed=False)
    second = _user('a+2@x.com', confirmed=False)

    assert known_emails.remember(first) is True
    assert known_emails.remember(first) is True  # again: still the holder
    assert known_emails.remember(second) is False
    assert KnownEmail.objects.count() == 1


def test_confirming_by_link_remembers_the_address():
    user = _user('a@x.com', confirmed=False)
    assert not KnownEmail.objects.exists()

    confirm_user(user)

    row = KnownEmail.objects.get()
    assert row.fingerprint == known_emails.fingerprint('a@x.com')
    assert row.first_user_id == user.pk


def test_an_account_created_already_confirmed_remembers_its_address():
    user = User.objects.create_user(email='g@x.com', password=None, name='G', email_confirmed=True)
    assert KnownEmail.objects.get().first_user_id == user.pk


def test_an_unconfirmed_signup_claims_nothing():
    """Otherwise registering a victim's address would get its owner refused later."""
    User.objects.create_user(email='victim@x.com', password='pw-12345678', name='Mallory')
    assert not KnownEmail.objects.exists()

    owner = _user('victim+shop@x.com', confirmed=False)
    confirm_user(owner)
    assert KnownEmail.objects.get().first_user_id == owner.pk


def test_the_memory_outlives_the_user():
    user = _user('a@x.com')
    known_emails.remember(user)
    user_id = user.pk

    user.delete()

    assert KnownEmail.objects.get().first_user_id == user_id
    newcomer = _user('a@x.com', confirmed=False)
    assert known_emails.remember(newcomer) is False


def test_an_address_the_account_moved_away_from_stays_known():
    user = _user('old@x.com')
    known_emails.remember(user)
    user.email = 'new@x.com'
    user.email_confirmed = False
    user.save()
    confirm_user(user)

    assert KnownEmail.objects.count() == 2
    assert known_emails.remember(_user('old@x.com', confirmed=False)) is False
    assert known_emails.remember(_user('new+1@x.com', confirmed=False)) is False


def test_the_backfill_keeps_the_oldest_confirmed_account_as_holder():
    oldest = _user('a@x.com')
    younger = _user('a+1@x.com')
    unconfirmed = _user('z@x.com', confirmed=False)
    User.objects.filter(pk=oldest.pk).update(created_at=datetime(2020, 1, 1, tzinfo=UTC))
    User.objects.filter(pk=younger.pk).update(created_at=datetime(2021, 1, 1, tzinfo=UTC))
    KnownEmail.objects.all().delete()
    migration = importlib.import_module('apps.accounts.migrations.0006_known_emails')

    migration.backfill(django_apps, None)

    rows = {row.fingerprint: row.first_user_id for row in KnownEmail.objects.all()}
    assert rows == {known_emails.fingerprint('a@x.com'): oldest.pk}
    assert known_emails.fingerprint(unconfirmed.email) not in rows
    # The migration's frozen copy of the hash agrees with the live one.
    assert migration.fingerprint('A+x@x.com') == known_emails.fingerprint('a@x.com')
