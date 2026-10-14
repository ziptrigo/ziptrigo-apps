from uuid import UUID

from ninja import Router
from ninja.errors import HttpError

from apps.accounts.auth import AdminAuth
from apps.accounts.models import User

from .models import CreditTransactionType
from .schemas import (
    CreditTransactionRequest,
    CreditTransactionResponse,
    UserCreditsResponse,
)
from .services import InsufficientCreditsError, apply_credits, get_balance

router = Router(tags=['Credits'])
admin_auth = AdminAuth()


@router.post(
    '/users/{user_id}/credits',
    response={201: CreditTransactionResponse},
    auth=admin_auth,
)
def create_credit_transaction(request, user_id: UUID, payload: CreditTransactionRequest):
    """Add or remove credits to/from a user account.

    Creates a credit transaction and updates the user's credit balance atomically. Staff must
    hold `billing.add_credittransaction`, the same permission the admin's adjustment tool needs
    (superusers hold every permission); being staff alone is not enough.
    """
    # `AdminAuth` only checks `is_staff`; `request.auth` is the authenticated user. Checked before
    # the user lookup so a refusal says nothing about which ids exist.
    if not request.auth.has_perm('billing.add_credittransaction'):
        raise HttpError(403, 'You do not have permission to adjust credits.')

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

    try:
        credit_transaction = apply_credits(
            user,
            payload.amount,
            tx_type=payload.transaction_type,
            description=payload.description,
        )
    except InsufficientCreditsError:
        raise HttpError(400, 'Insufficient credits')
    except ValueError as e:
        raise HttpError(400, str(e))

    return 201, CreditTransactionResponse.model_validate(credit_transaction)


@router.get('/users/{user_id}/credits', response=UserCreditsResponse, auth=admin_auth)
def get_user_credits(request, user_id: UUID):
    """Get the current credit balance for a user."""
    try:
        user = User.objects.get(id=user_id)
    except User.DoesNotExist:
        raise HttpError(404, 'User not found')

    return UserCreditsResponse(user_id=user.id, credits=get_balance(user))
