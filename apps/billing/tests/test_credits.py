from datetime import UTC, datetime, timedelta

import pytest
from django.test import Client

from apps.billing.models import CreditTransaction
from apps.billing.services import InsufficientCreditsError, add_credits, get_balance, spend_credits


@pytest.mark.django_db
def test_add_credits_increases_balance_and_creates_transaction(user):
    add_credits(user, 100, tx_type='purchase', description='Test purchase')

    assert get_balance(user) == 100

    tx = CreditTransaction.objects.get(user=user)
    assert tx.amount == 100
    assert tx.type == 'purchase'
    assert tx.description == 'Test purchase'


@pytest.mark.django_db
def test_spend_credits_decreases_balance_and_creates_transaction(user):
    add_credits(user, 100, tx_type='purchase', description='Seed')
    spend_credits(user, 25, tx_type='spend', description='Test spend')

    assert get_balance(user) == 75

    txs = list(CreditTransaction.objects.filter(user=user).order_by('created_at', 'id'))
    assert len(txs) == 2

    assert txs[0].amount == 100
    assert txs[1].amount == -25
    assert txs[1].type == 'spend'
    assert txs[1].description == 'Test spend'


@pytest.mark.django_db
def test_spend_credits_insufficient_raises_and_does_not_create_transaction(user):
    assert CreditTransaction.objects.filter(user=user).count() == 0

    with pytest.raises(InsufficientCreditsError):
        spend_credits(user, 1, tx_type='spend', description='Should fail')

    assert get_balance(user) == 0
    assert CreditTransaction.objects.filter(user=user).count() == 0


@pytest.mark.django_db
def test_credits_history_page_shows_only_current_user_and_orders_most_recent_first(db, user):
    from django.contrib.auth import get_user_model

    User = get_user_model()
    other = User.objects.create_user(
        email='other@example.com',
        password='testpass123',
        name='Other User',
    )
    other.email_confirmed = True
    other.email_confirmed_at = datetime.now(UTC)
    other.save(update_fields=['email_confirmed', 'email_confirmed_at'])

    base = datetime.now(UTC)

    older = CreditTransaction.objects.create(
        user=user,
        amount=10,
        type='purchase',
        description='Older tx',
    )
    CreditTransaction.objects.filter(pk=older.pk).update(created_at=base - timedelta(hours=1))

    newer = CreditTransaction.objects.create(
        user=user,
        amount=-3,
        type='spend',
        description='Newer tx',
    )
    CreditTransaction.objects.filter(pk=newer.pk).update(created_at=base)

    CreditTransaction.objects.create(
        user=other,
        amount=999,
        type='purchase',
        description='Other user tx',
    )

    client = Client()
    client.force_login(user)

    response = client.get('/billing/credits/')
    assert response.status_code == 200

    body = response.content.decode('utf-8')

    assert 'Older tx' in body
    assert 'Newer tx' in body
    assert 'Other user tx' not in body

    assert body.find('Newer tx') < body.find('Older tx')
