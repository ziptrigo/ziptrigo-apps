"""Claim on login (spec section 6): a confirmed anonymous transfer becomes a user's own the
moment they log in with a matching, confirmed email address.
"""

from datetime import UTC, datetime

import pytest
from django.utils import timezone

from apps.accounts.models import User

from ..models import Transfer, TransferStatus
from ..services.claim import claim_transfers_for_user

pytestmark = [pytest.mark.django_db, pytest.mark.unit]


def _confirmed_anon_transfer(email: str, status=TransferStatus.ACTIVE, completed=True) -> Transfer:
    transfer = Transfer.objects.create(owner=None, sender_email=email, status=status)
    if completed:
        Transfer.objects.filter(pk=transfer.pk).update(completed_at=timezone.now())
        transfer.refresh_from_db()
    return transfer


def _confirmed_user(email: str, email_confirmed: bool = True) -> User:
    user = User.objects.create_user(email=email, password='testpass123')
    user.email_confirmed = email_confirmed
    if email_confirmed:
        user.email_confirmed_at = datetime.now(UTC)
    user.save()
    return user


def test_claims_a_matching_confirmed_transfer():
    transfer = _confirmed_anon_transfer('sender@example.com')
    user = _confirmed_user('sender@example.com')

    claimed = claim_transfers_for_user(user)

    assert claimed == 1
    transfer.refresh_from_db()
    assert transfer.owner_id == user.id
    assert transfer.last_billed_at is not None


def test_claim_is_case_insensitive():
    _confirmed_anon_transfer('Sender@Example.com')
    user = _confirmed_user('sender@example.com')

    assert claim_transfers_for_user(user) == 1


def test_does_not_claim_for_unconfirmed_user_email():
    transfer = _confirmed_anon_transfer('sender@example.com')
    user = _confirmed_user('sender@example.com', email_confirmed=False)

    assert claim_transfers_for_user(user) == 0
    transfer.refresh_from_db()
    assert transfer.owner_id is None


def test_does_not_claim_unconfirmed_anonymous_transfer():
    """A transfer still `PENDING_CONFIRMATION` (never `completed_at`) was never actually sent."""
    transfer = _confirmed_anon_transfer(
        'sender@example.com', status=TransferStatus.PENDING_CONFIRMATION, completed=False
    )
    user = _confirmed_user('sender@example.com')

    assert claim_transfers_for_user(user) == 0
    transfer.refresh_from_db()
    assert transfer.owner_id is None


def test_does_not_claim_an_ended_transfer():
    transfer = _confirmed_anon_transfer('sender@example.com', status=TransferStatus.EXPIRED)
    user = _confirmed_user('sender@example.com')

    assert claim_transfers_for_user(user) == 0
    transfer.refresh_from_db()
    assert transfer.owner_id is None


def test_does_not_reclaim_an_already_owned_transfer():
    other_user = _confirmed_user('other@example.com')
    transfer = _confirmed_anon_transfer('sender@example.com')
    Transfer.objects.filter(pk=transfer.pk).update(owner=other_user)

    user = _confirmed_user('sender@example.com')
    assert claim_transfers_for_user(user) == 0

    transfer.refresh_from_db()
    assert transfer.owner_id == other_user.id


def test_does_not_claim_when_email_does_not_match():
    transfer = _confirmed_anon_transfer('sender@example.com')
    user = _confirmed_user('someone-else@example.com')

    assert claim_transfers_for_user(user) == 0
    transfer.refresh_from_db()
    assert transfer.owner_id is None


def test_claims_a_disabled_transfer_too():
    transfer = _confirmed_anon_transfer('sender@example.com', status=TransferStatus.DISABLED)
    user = _confirmed_user('sender@example.com')

    assert claim_transfers_for_user(user) == 1
    transfer.refresh_from_db()
    assert transfer.owner_id == user.id


def test_no_email_no_op():
    user = User(email='', email_confirmed=True)
    assert claim_transfers_for_user(user) == 0


class TestClaimOnLoginSignal:
    """Integration: the actual session login view fires `user_logged_in`, which
    `apps.file_transfer.apps` wires to `claim_transfers_for_user`."""

    def test_logging_in_claims_a_matching_transfer(self, client):
        transfer = _confirmed_anon_transfer('sender@example.com')
        user = _confirmed_user('sender@example.com')

        response = client.post(
            '/account/login/', {'email': 'sender@example.com', 'password': 'testpass123'}
        )
        assert response.status_code in (302, 200)

        transfer.refresh_from_db()
        assert transfer.owner_id == user.id
