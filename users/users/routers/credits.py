from uuid import UUID

from django.db import transaction
from ninja import Router
from ninja.errors import HttpError

from ..auth import AdminAuth
from ..models import CreditTransaction, CreditTransactionType, User
from ..schemas import CreditTransactionRequest, CreditTransactionResponse, UserCreditsResponse

router = Router()
admin_auth = AdminAuth()


@router.post(
    '/{user_id}/credits',
    response=CreditTransactionResponse,
    auth=admin_auth,
)
def create_credit_transaction(request, user_id: UUID, payload: CreditTransactionRequest):
    """Add or remove credits to/from a user account.

    Creates a credit transaction and updates the user's credit balance atomically.
    """
    try:
        user = User.objects.get(id=user_id)
    except User.DoesNotExist:
        raise HttpError(404, 'User not found')

    # Validate transaction type
    if payload.transaction_type not in [choice[0] for choice in CreditTransactionType.choices]:
        raise HttpError(
            400,
            f'Invalid transaction type. Must be one of: {", ".join([choice[0] for choice in CreditTransactionType.choices])}',
        )

    # Create transaction and update user credits atomically
    with transaction.atomic():
        # Create the transaction record
        credit_transaction = CreditTransaction.objects.create(
            user=user,
            amount=payload.amount,
            type=payload.transaction_type,
            description=payload.description,
        )

        # Update user's credit balance
        user.credits += payload.amount
        user.save(update_fields=['credits'])

    return CreditTransactionResponse.model_validate(credit_transaction)


@router.get('/{user_id}/credits', response=UserCreditsResponse, auth=admin_auth)
def get_user_credits(request, user_id: UUID):
    """Get the current credit balance for a user."""
    try:
        user = User.objects.get(id=user_id)
    except User.DoesNotExist:
        raise HttpError(404, 'User not found')

    return UserCreditsResponse(user_id=user.id, credits=user.credits)
