"""The manual credit adjustment page on the CreditTransaction admin."""

import pytest
from django.urls import reverse

from apps.billing.models import CreditTransaction, CreditTransactionType
from apps.billing.services import add_credits, get_balance

pytestmark = [pytest.mark.django_db, pytest.mark.unit]

URL = 'custom_admin:billing_credittransaction_adjust'


def _adjust(client, user, direction, amount, description=''):
    return client.post(
        reverse(URL),
        {
            'user_email': user.email,
            'direction': direction,
            'amount': amount,
            'description': description,
        },
        follow=True,
    )


def test_add_credits(client, admin_user, regular_user):
    client.force_login(admin_user)

    response = _adjust(client, regular_user, 'add', 10, 'Goodwill')

    assert response.status_code == 200
    assert get_balance(regular_user) == 10
    tx = CreditTransaction.objects.get(user=regular_user)
    assert tx.type == CreditTransactionType.ADJUSTMENT
    assert tx.amount == 10
    assert tx.description == 'Goodwill'
    assert 'New balance: 10' in response.content.decode()


def test_spend_credits_defaults_description(client, admin_user, regular_user):
    add_credits(regular_user, 5)
    client.force_login(admin_user)

    _adjust(client, regular_user, 'spend', 3)

    assert get_balance(regular_user) == 2
    tx = CreditTransaction.objects.filter(user=regular_user).latest('created_at')
    assert tx.amount == -3
    assert tx.description == 'Admin adjustment'


def test_spend_more_than_balance_is_refused(client, admin_user, regular_user):
    client.force_login(admin_user)

    response = _adjust(client, regular_user, 'spend', 1)

    assert 'Insufficient credits' in response.content.decode()
    assert get_balance(regular_user) == 0
    assert not CreditTransaction.objects.filter(user=regular_user).exists()


def test_unknown_user_is_reported(client, admin_user):
    client.force_login(admin_user)

    response = client.post(
        reverse(URL),
        {'user_email': 'ghost@example.com', 'direction': 'add', 'amount': 1},
        follow=True,
    )

    assert 'No user found with email: ghost@example.com' in response.content.decode()
    assert not CreditTransaction.objects.exists()


def test_staff_without_permission_is_forbidden(client, regular_user):
    regular_user.is_staff = True
    regular_user.save(update_fields=['is_staff'])
    client.force_login(regular_user)

    response = client.get(reverse(URL))

    assert response.status_code == 403


def test_changelist_links_to_adjustment_page(client, admin_user):
    client.force_login(admin_user)

    response = client.get(reverse('custom_admin:billing_credittransaction_changelist'))

    assert reverse(URL) in response.content.decode()
