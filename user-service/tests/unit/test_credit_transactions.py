import pytest
from users.users.models import CreditTransaction, CreditTransactionType, User

pytestmark = [pytest.mark.django_db, pytest.mark.unit]


def test_user_has_default_credits_of_zero():
    """Test that newly created users have 0 credits by default."""
    user = User.objects.create_user(email='test@example.com', password='password')
    assert user.credits == 0


def test_credit_transaction_creates_record():
    """Test that creating a credit transaction saves it correctly."""
    user = User.objects.create_user(email='test@example.com', password='password')

    transaction = CreditTransaction.objects.create(
        user=user,
        amount=100,
        type=CreditTransactionType.PURCHASE,
        description='Initial purchase',
    )

    assert transaction.id is not None
    assert transaction.user == user
    assert transaction.amount == 100
    assert transaction.type == CreditTransactionType.PURCHASE
    assert transaction.description == 'Initial purchase'
    assert transaction.created_at is not None


def test_credit_transaction_supports_negative_amounts():
    """Test that credit transactions can have negative amounts for spending."""
    user = User.objects.create_user(email='test@example.com', password='password')

    transaction = CreditTransaction.objects.create(
        user=user,
        amount=-50,
        type=CreditTransactionType.SPEND,
        description='Used credits',
    )

    assert transaction.amount == -50


def test_credit_transaction_supports_all_types():
    """Test that all transaction types are valid."""
    user = User.objects.create_user(email='test@example.com', password='password')

    for transaction_type in CreditTransactionType.choices:
        transaction = CreditTransaction.objects.create(
            user=user,
            amount=10,
            type=transaction_type[0],
            description=f'Test {transaction_type[1]}',
        )
        assert transaction.type == transaction_type[0]


def test_credit_transactions_ordered_by_created_at_desc():
    """Test that credit transactions are returned in descending order by default."""
    user = User.objects.create_user(email='test@example.com', password='password')

    tx1 = CreditTransaction.objects.create(
        user=user, amount=10, type=CreditTransactionType.PURCHASE
    )
    tx2 = CreditTransaction.objects.create(
        user=user, amount=20, type=CreditTransactionType.PURCHASE
    )
    tx3 = CreditTransaction.objects.create(
        user=user, amount=30, type=CreditTransactionType.PURCHASE
    )

    transactions = list(CreditTransaction.objects.filter(user=user))
    assert transactions[0].id == tx3.id
    assert transactions[1].id == tx2.id
    assert transactions[2].id == tx1.id


def test_credit_transaction_related_name():
    """Test that credit transactions can be accessed via user.credit_transactions."""
    user = User.objects.create_user(email='test@example.com', password='password')

    CreditTransaction.objects.create(user=user, amount=10, type=CreditTransactionType.PURCHASE)
    CreditTransaction.objects.create(user=user, amount=-5, type=CreditTransactionType.SPEND)

    assert user.credit_transactions.count() == 2


def test_credit_transaction_cascade_delete():
    """Test that credit transactions are deleted when user is deleted."""
    user = User.objects.create_user(email='test@example.com', password='password')

    CreditTransaction.objects.create(user=user, amount=10, type=CreditTransactionType.PURCHASE)
    CreditTransaction.objects.create(user=user, amount=-5, type=CreditTransactionType.SPEND)

    user_id = user.id
    user.delete()

    assert CreditTransaction.objects.filter(user_id=user_id).count() == 0


def test_credit_transaction_str_representation():
    """Test the string representation of a credit transaction."""
    user = User.objects.create_user(email='test@example.com', password='password')

    transaction = CreditTransaction.objects.create(
        user=user, amount=100, type=CreditTransactionType.PURCHASE
    )

    str_repr = str(transaction)
    assert str(user.id) in str_repr
    assert 'purchase' in str_repr
    assert '100' in str_repr
