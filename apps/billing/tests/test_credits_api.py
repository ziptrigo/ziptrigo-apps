import pytest

from apps.accounts.models import User
from apps.accounts.tokens import CustomAccessToken
from apps.billing.models import CreditTransaction, CreditTransactionType
from apps.billing.services import add_credits, get_balance

pytestmark = [pytest.mark.django_db, pytest.mark.integration]


def _auth_headers(user: User) -> dict[str, str]:
    return {'HTTP_AUTHORIZATION': f'Bearer {CustomAccessToken.for_user(user)}'}


def _post_credits(client, requester: User, user_id, payload):
    return client.post(
        f'/api/billing/users/{user_id}/credits',
        payload,
        content_type='application/json',
        **_auth_headers(requester),
    )


def _get_credits(client, requester: User, user_id):
    return client.get(f'/api/billing/users/{user_id}/credits', **_auth_headers(requester))


def test_admin_can_add_credits_to_user(client, admin_user, regular_user: User):
    """Test that admin can add credits to a user account."""
    assert get_balance(regular_user) == 0

    response = _post_credits(
        client,
        admin_user,
        regular_user.id,
        {
            'transaction_type': 'purchase',
            'amount': 100,
            'description': 'Initial purchase',
        },
    )

    assert response.status_code == 201
    data = response.json()
    assert data['amount'] == 100
    assert data['type'] == 'purchase'
    assert data['description'] == 'Initial purchase'
    assert data['user_id'] == str(regular_user.id)

    assert get_balance(regular_user) == 100


def test_admin_can_remove_credits_from_user(client, admin_user, regular_user: User):
    """Test that admin can remove credits from a user account."""
    # Set initial credits
    add_credits(regular_user, 100)

    response = _post_credits(
        client,
        admin_user,
        regular_user.id,
        {
            'transaction_type': 'spend',
            'amount': -30,
            'description': 'Spent credits',
        },
    )

    assert response.status_code == 201
    data = response.json()
    assert data['amount'] == -30
    assert data['type'] == 'spend'

    assert get_balance(regular_user) == 70


def test_admin_can_adjust_credits(client, admin_user, regular_user: User):
    """Test that admin can adjust credits with adjustment type."""
    response = _post_credits(
        client,
        admin_user,
        regular_user.id,
        {
            'transaction_type': 'adjustment',
            'amount': 50,
            'description': 'Manual adjustment',
        },
    )

    assert response.status_code == 201
    assert response.json()['type'] == 'adjustment'

    assert get_balance(regular_user) == 50


def test_admin_can_refund_credits(client, admin_user, regular_user: User):
    """Test that admin can refund credits."""
    response = _post_credits(
        client,
        admin_user,
        regular_user.id,
        {
            'transaction_type': 'refund',
            'amount': 25,
            'description': 'Refund for cancellation',
        },
    )

    assert response.status_code == 201
    assert response.json()['type'] == 'refund'

    assert get_balance(regular_user) == 25


def test_credit_transaction_creates_audit_record(client, admin_user, regular_user: User):
    """Test that credit transactions create audit records."""
    response = _post_credits(
        client,
        admin_user,
        regular_user.id,
        {
            'transaction_type': 'purchase',
            'amount': 100,
            'description': 'Test purchase',
        },
    )

    assert response.status_code == 201

    transactions = CreditTransaction.objects.filter(user=regular_user)
    assert transactions.count() == 1

    transaction = transactions.first()
    assert transaction.amount == 100
    assert transaction.type == CreditTransactionType.PURCHASE
    assert transaction.description == 'Test purchase'


def test_invalid_transaction_type_returns_400(client, admin_user, regular_user: User):
    """Test that invalid transaction type returns 400 error."""
    response = _post_credits(
        client,
        admin_user,
        regular_user.id,
        {
            'transaction_type': 'invalid_type',
            'amount': 100,
            'description': 'Invalid transaction',
        },
    )

    assert response.status_code == 400
    assert 'Invalid transaction type' in response.json()['detail']


def test_nonexistent_user_returns_404(client, admin_user):
    fake_uuid = '00000000-0000-0000-0000-000000000000'
    response = _post_credits(
        client,
        admin_user,
        fake_uuid,
        {
            'transaction_type': 'purchase',
            'amount': 100,
            'description': 'Test',
        },
    )

    assert response.status_code == 404


def test_admin_can_get_user_credits_balance(client, admin_user, regular_user: User):
    """Test that admin can retrieve user's credit balance."""
    add_credits(regular_user, 250)

    response = _get_credits(client, admin_user, regular_user.id)

    assert response.status_code == 200
    data = response.json()
    assert data['user_id'] == str(regular_user.id)
    assert data['credits'] == 250


def test_get_credits_nonexistent_user_returns_404(client, admin_user):
    fake_uuid = '00000000-0000-0000-0000-000000000000'
    response = _get_credits(client, admin_user, fake_uuid)

    assert response.status_code == 404


def test_non_admin_cannot_add_credits(client, regular_user):
    """Test that non-admin users cannot add credits."""
    other_user = User.objects.create_user(email='other@example.com', password='password')

    response = _post_credits(
        client,
        regular_user,
        other_user.id,
        {
            'transaction_type': 'purchase',
            'amount': 100,
            'description': 'Unauthorized attempt',
        },
    )

    assert response.status_code == 401


def test_multiple_transactions_update_balance_correctly(client, admin_user, regular_user: User):
    """Test that multiple transactions correctly update the balance."""
    # Add 100 credits
    _post_credits(
        client,
        admin_user,
        regular_user.id,
        {'transaction_type': 'purchase', 'amount': 100, 'description': 'Purchase 1'},
    )

    # Add 50 more credits
    _post_credits(
        client,
        admin_user,
        regular_user.id,
        {'transaction_type': 'purchase', 'amount': 50, 'description': 'Purchase 2'},
    )

    # Spend 30 credits
    _post_credits(
        client,
        admin_user,
        regular_user.id,
        {'transaction_type': 'spend', 'amount': -30, 'description': 'Spend 1'},
    )

    assert get_balance(regular_user) == 120

    # Verify all transactions are recorded
    assert CreditTransaction.objects.filter(user=regular_user).count() == 3


def test_admin_cannot_take_balance_below_zero(client, admin_user, regular_user: User):
    """`CreditAccount.balance` is unsigned (see CLAUDE.md's "Known gaps"): spending more than a
    user's balance is rejected with 400 rather than driving the balance negative."""
    add_credits(regular_user, 20)

    response = _post_credits(
        client,
        admin_user,
        regular_user.id,
        {'transaction_type': 'spend', 'amount': -30, 'description': 'Overspend'},
    )

    assert response.status_code == 400
    assert get_balance(regular_user) == 20
