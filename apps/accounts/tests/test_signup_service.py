"""`apps.accounts.services.signup.create_account` (issue #52 code review): the pre-check alone
(`User.objects.filter(email=email).exists()`) doesn't close the race between two concurrent
signups for the same address -- only the `transaction.atomic()` + `IntegrityError` catch around
the actual `create_user` call does. These tests exercise that catch directly (by bypassing the
pre-check, since reproducing the real race in a synchronous test isn't practical) and the
domain-case-normalization fix that closes the *non-racy* way this used to be reachable -- see
`apps.accounts.services.signup`'s module docstring.
"""

import pytest
from django.db.models import QuerySet

from apps.accounts.models import User
from apps.accounts.services.signup import EmailAlreadyRegistered, create_account

pytestmark = [pytest.mark.django_db, pytest.mark.unit]


def test_create_account_rejects_email_differing_only_by_domain_case(user):
    """`user.email` is already normalized (`UserManager.create_user` lower-cases the domain), so
    a signup for the same address with an upper-case domain must be rejected as a duplicate."""
    mixed_case_domain = f'{user.email.split("@")[0]}@{user.email.split("@")[1].upper()}'
    assert mixed_case_domain != user.email
    assert mixed_case_domain.lower() == user.email

    with pytest.raises(EmailAlreadyRegistered):
        create_account(name='Someone Else', email=mixed_case_domain, password='password123')

    assert User.objects.filter(email=user.email).count() == 1


def test_create_account_converts_integrity_error_to_email_already_registered(user, monkeypatch):
    """Simulates the genuine race the pre-check alone can't close: something else has already
    created the row by the time `create_user` runs, even though the pre-check (patched here to
    always report "no match", standing in for a concurrent request winning the race between this
    call's own check and its `create_user`) didn't see it. The unique constraint must still turn
    into `EmailAlreadyRegistered`, not an unhandled `IntegrityError`."""
    monkeypatch.setattr(QuerySet, 'exists', lambda self: False)

    with pytest.raises(EmailAlreadyRegistered):
        create_account(name='Racer', email=user.email, password='password123')

    assert User.objects.filter(email=user.email).count() == 1
