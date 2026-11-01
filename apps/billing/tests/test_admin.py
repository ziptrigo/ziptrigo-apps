"""The manual credit adjustment page on the CreditTransaction admin."""

from typing import cast

import pytest
from django.contrib.auth.models import Permission
from django.urls import reverse

from apps.accounts.models import User
from apps.accounts.tests.factories import UserFactory
from apps.billing.models import CreditAccount, CreditTransaction, CreditTransactionType
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


def _staff_with_perms(user, *codenames):
    user.is_staff = True
    user.save(update_fields=['is_staff'])
    user.user_permissions.add(*Permission.objects.filter(codename__in=codenames))
    return user


def test_staff_with_add_permission_can_adjust(client, regular_user):
    staff = _staff_with_perms(UserFactory(), 'add_credittransaction')
    client.force_login(staff)

    _adjust(client, regular_user, 'add', 4)

    assert get_balance(regular_user) == 4


def test_staff_with_only_view_permission_cannot_adjust(client, regular_user):
    staff = _staff_with_perms(UserFactory(), 'view_credittransaction')
    client.force_login(staff)

    response = client.post(
        reverse(URL),
        {'user_email': regular_user.email, 'direction': 'add', 'amount': 5},
    )

    assert response.status_code == 403
    assert get_balance(regular_user) == 0
    assert not CreditTransaction.objects.exists()


def test_view_only_staff_changelist_hides_adjust_link(client):
    staff = _staff_with_perms(UserFactory(), 'view_credittransaction')
    client.force_login(staff)

    response = client.get(reverse('custom_admin:billing_credittransaction_changelist'))

    assert response.status_code == 200
    assert reverse(URL) not in response.content.decode()


def test_superuser_cannot_add_ledger_rows(client, admin_user, regular_user):
    client.force_login(admin_user)

    response = client.post(
        reverse('custom_admin:billing_credittransaction_add'),
        {'user': regular_user.pk, 'amount': 1000, 'type': 'adjustment', 'description': 'x'},
    )

    assert response.status_code == 403
    assert not CreditTransaction.objects.exists()


def test_superuser_cannot_change_or_delete_ledger_rows(client, admin_user, regular_user):
    add_credits(regular_user, 5)
    tx = CreditTransaction.objects.get(user=regular_user)
    client.force_login(admin_user)

    change_url = reverse('custom_admin:billing_credittransaction_change', args=[tx.pk])
    delete_url = reverse('custom_admin:billing_credittransaction_delete', args=[tx.pk])

    assert client.post(change_url, {'amount': 999}).status_code == 403
    assert client.post(delete_url, {'post': 'yes'}).status_code == 403
    tx.refresh_from_db()
    assert tx.amount == 5


def test_no_bulk_delete_action(client, admin_user, regular_user):
    add_credits(regular_user, 5)
    tx = CreditTransaction.objects.get(user=regular_user)
    client.force_login(admin_user)
    url = reverse('custom_admin:billing_credittransaction_changelist')

    assert 'delete_selected' not in client.get(url).content.decode()

    client.post(url, {'action': 'delete_selected', '_selected_action': [tx.pk], 'post': 'yes'})
    assert CreditTransaction.objects.filter(pk=tx.pk).exists()


def test_changelist_renders_for_superuser(client, admin_user):
    client.force_login(admin_user)

    response = client.get(reverse('custom_admin:billing_credittransaction_changelist'))

    assert response.status_code == 200


def test_superuser_cannot_delete_credit_account(client, admin_user, regular_user):
    add_credits(regular_user, 5)
    client.force_login(admin_user)
    url = reverse('custom_admin:billing_creditaccount_delete', args=[regular_user.pk])

    assert client.get(url).status_code == 403
    assert client.post(url, {'post': 'yes'}).status_code == 403
    assert CreditAccount.objects.filter(pk=regular_user.pk).exists()
    assert get_balance(regular_user) == 5


def test_superuser_can_delete_user_with_credit_history(client, admin_user, regular_user):
    """The read-only billing admins must not block deleting the user: the cascade removes the
    ledger and the balance together."""
    add_credits(regular_user, 5)
    add_credits(regular_user, 3)
    user_id = regular_user.pk
    client.force_login(admin_user)
    url = reverse('custom_admin:accounts_user_delete', args=[user_id])

    page = client.get(url)
    assert page.status_code == 200
    assert 'credit transaction' in page.content.decode().lower()

    response = client.post(url, {'post': 'yes'})

    assert response.status_code == 302
    assert not User.objects.filter(pk=user_id).exists()
    assert not CreditAccount.objects.filter(pk=user_id).exists()
    assert not CreditTransaction.objects.filter(user_id=user_id).exists()


def test_staff_needs_billing_delete_permission_to_delete_user_with_credit_history(client):
    """Same as before the ledger was made read-only: the user's cascaded rows still need the
    caller to hold their delete permission (only the admin's own refusal is bypassed)."""
    target = cast(User, UserFactory())
    add_credits(target, 5)
    staff = _staff_with_perms(cast(User, UserFactory()), 'delete_user')
    client.force_login(staff)
    url = reverse('custom_admin:accounts_user_delete', args=[target.pk])

    assert client.post(url, {'post': 'yes'}).status_code == 403
    assert User.objects.filter(pk=target.pk).exists()

    staff.user_permissions.add(
        *Permission.objects.filter(
            codename__in=['delete_credittransaction', 'delete_creditaccount']
        )
    )

    assert client.post(url, {'post': 'yes'}).status_code == 302
    assert not User.objects.filter(pk=target.pk).exists()
    assert not CreditTransaction.objects.filter(user_id=target.pk).exists()


def test_adjustment_finds_the_user_whatever_the_case_of_the_email(client, admin_user, regular_user):
    client.force_login(admin_user)

    client.post(
        reverse(URL),
        {'user_email': regular_user.email.upper(), 'direction': 'add', 'amount': 3},
    )

    assert get_balance(regular_user) == 3


def test_balance_changes_move_updated_at(regular_user):
    add_credits(regular_user, 5)
    account = CreditAccount.objects.get(user=regular_user)
    before = account.updated_at
    CreditAccount.objects.filter(pk=account.pk).update(updated_at=before.replace(year=2000))

    add_credits(regular_user, 1)

    account.refresh_from_db()
    assert account.updated_at.year != 2000
