import pytest
from src.users.models import CreditTransaction, CreditTransactionType, User

pytestmark = [pytest.mark.django_db, pytest.mark.integration]


def test_admin_can_add_credits_to_user(api_client, admin_user, regular_user: User):
    """Test that admin can add credits to a user account."""
    api_client.force_authenticate(user=admin_user)

    assert regular_user.credits == 0

    response = api_client.post(
        f'/api/users/{regular_user.id}/credits',
        {
            'transaction_type': 'purchase',
            'amount': 100,
            'description': 'Initial purchase',
        },
        format='json',
    )

    assert response.status_code == 201
    assert response.data['amount'] == 100
    assert response.data['type'] == 'purchase'
    assert response.data['description'] == 'Initial purchase'
    assert response.data['user_id'] == str(regular_user.id)

    regular_user.refresh_from_db()
    assert regular_user.credits == 100


def test_admin_can_remove_credits_from_user(api_client, admin_user, regular_user: User):
    """Test that admin can remove credits from a user account."""
    api_client.force_authenticate(user=admin_user)

    # Set initial credits
    regular_user.credits = 100
    regular_user.save()

    response = api_client.post(
        f'/api/users/{regular_user.id}/credits',
        {
            'transaction_type': 'spend',
            'amount': -30,
            'description': 'Spent credits',
        },
        format='json',
    )

    assert response.status_code == 201
    assert response.data['amount'] == -30
    assert response.data['type'] == 'spend'

    regular_user.refresh_from_db()
    assert regular_user.credits == 70


def test_admin_can_adjust_credits(api_client, admin_user, regular_user: User):
    """Test that admin can adjust credits with adjustment type."""
    api_client.force_authenticate(user=admin_user)

    response = api_client.post(
        f'/api/users/{regular_user.id}/credits',
        {
            'transaction_type': 'adjustment',
            'amount': 50,
            'description': 'Manual adjustment',
        },
        format='json',
    )

    assert response.status_code == 201
    assert response.data['type'] == 'adjustment'

    regular_user.refresh_from_db()
    assert regular_user.credits == 50


def test_admin_can_refund_credits(api_client, admin_user, regular_user: User):
    """Test that admin can refund credits."""
    api_client.force_authenticate(user=admin_user)

    response = api_client.post(
        f'/api/users/{regular_user.id}/credits',
        {
            'transaction_type': 'refund',
            'amount': 25,
            'description': 'Refund for cancellation',
        },
        format='json',
    )

    assert response.status_code == 201
    assert response.data['type'] == 'refund'

    regular_user.refresh_from_db()
    assert regular_user.credits == 25


def test_credit_transaction_creates_audit_record(api_client, admin_user, regular_user: User):
    """Test that credit transactions create audit records."""
    api_client.force_authenticate(user=admin_user)

    response = api_client.post(
        f'/api/users/{regular_user.id}/credits',
        {
            'transaction_type': 'purchase',
            'amount': 100,
            'description': 'Test purchase',
        },
        format='json',
    )

    assert response.status_code == 201

    transactions = CreditTransaction.objects.filter(user=regular_user)
    assert transactions.count() == 1

    transaction = transactions.first()
    assert transaction.amount == 100
    assert transaction.type == CreditTransactionType.PURCHASE
    assert transaction.description == 'Test purchase'


def test_invalid_transaction_type_returns_400(api_client, admin_user, regular_user: User):
    """Test that invalid transaction type returns 400 error."""
    api_client.force_authenticate(user=admin_user)

    response = api_client.post(
        f'/api/users/{regular_user.id}/credits',
        {
            'transaction_type': 'invalid_type',
            'amount': 100,
            'description': 'Invalid transaction',
        },
        format='json',
    )

    assert response.status_code == 400
    assert 'Invalid transaction type' in response.data['detail']


def test_nonexistent_user_returns_404(api_client, admin_user):
    """Test that credits endpoint returns 404 for nonexistent user."""
    api_client.force_authenticate(user=admin_user)

    fake_uuid = '00000000-0000-0000-0000-000000000000'
    response = api_client.post(
        f'/api/users/{fake_uuid}/credits',
        {
            'transaction_type': 'purchase',
            'amount': 100,
            'description': 'Test',
        },
        format='json',
    )

    assert response.status_code == 404


def test_admin_can_get_user_credits_balance(api_client, admin_user, regular_user: User):
    """Test that admin can retrieve user's credit balance."""
    api_client.force_authenticate(user=admin_user)

    regular_user.credits = 250
    regular_user.save()

    response = api_client.get(f'/api/users/{regular_user.id}/credits')

    assert response.status_code == 200
    assert response.data['user_id'] == str(regular_user.id)
    assert response.data['credits'] == 250


def test_get_credits_nonexistent_user_returns_404(api_client, admin_user):
    """Test that getting credits for nonexistent user returns 404."""
    api_client.force_authenticate(user=admin_user)

    fake_uuid = '00000000-0000-0000-0000-000000000000'
    response = api_client.get(f'/api/users/{fake_uuid}/credits')

    assert response.status_code == 404


def test_non_admin_cannot_add_credits(api_client, regular_user):
    """Test that non-admin users cannot add credits."""
    api_client.force_authenticate(user=regular_user)

    other_user = User.objects.create_user(email='other@example.com', password='password')

    response = api_client.post(
        f'/api/users/{other_user.id}/credits',
        {
            'transaction_type': 'purchase',
            'amount': 100,
            'description': 'Unauthorized attempt',
        },
        format='json',
    )

    assert response.status_code == 401


def test_multiple_transactions_update_balance_correctly(api_client, admin_user, regular_user: User):
    """Test that multiple transactions correctly update the balance."""
    api_client.force_authenticate(user=admin_user)

    # Add 100 credits
    api_client.post(
        f'/api/users/{regular_user.id}/credits',
        {'transaction_type': 'purchase', 'amount': 100, 'description': 'Purchase 1'},
        format='json',
    )

    # Add 50 more credits
    api_client.post(
        f'/api/users/{regular_user.id}/credits',
        {'transaction_type': 'purchase', 'amount': 50, 'description': 'Purchase 2'},
        format='json',
    )

    # Spend 30 credits
    api_client.post(
        f'/api/users/{regular_user.id}/credits',
        {'transaction_type': 'spend', 'amount': -30, 'description': 'Spend 1'},
        format='json',
    )

    regular_user.refresh_from_db()
    assert regular_user.credits == 120

    # Verify all transactions are recorded
    assert CreditTransaction.objects.filter(user=regular_user).count() == 3
