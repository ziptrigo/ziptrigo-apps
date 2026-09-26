from ninja import Router
from ninja.errors import HttpError

from ..auth import JWTAuth
from ..models import User
from ..schemas import AccountUpdateRequest, AccountUpdateResponse
from ..services.email_confirmation import get_email_confirmation_service

router = Router()
jwt_auth = JWTAuth()


@router.put('/account', response=AccountUpdateResponse, auth=jwt_auth)
def update_account(request, payload: AccountUpdateRequest):
    """Update current user's account information (name and/or email).

    Requires JWT authentication.
    """
    # `request` is left unannotated (as elsewhere in this package, e.g. `routers/users.py`)
    # rather than typed `HttpRequest`, which doesn't declare `.auth` -- `JWTAuth` (see `jwt_auth`
    # above) sets it to the authenticated `User` at runtime.
    user: User = request.auth

    # Update name if provided
    if payload.name is not None:
        user.name = payload.name

    # Update email if provided and different. The new address must be confirmed again, so a user
    # can't claim an address they don't own.
    email_changed = bool(payload.email and payload.email != user.email)
    if email_changed:
        # Check if email already exists
        if User.objects.filter(email=payload.email).exclude(id=user.id).exists():
            raise HttpError(400, 'Email already in use')
        user.email = payload.email
        user.email_confirmed = False
        user.email_confirmed_at = None

    user.save()

    if email_changed:
        get_email_confirmation_service().send_confirmation_email(user)

    return AccountUpdateResponse(
        message=(
            'Profile updated. Check your inbox to confirm your new email address.'
            if email_changed
            else 'Profile updated successfully'
        ),
        user={
            'id': str(user.id),
            'name': user.name,
            'email': user.email,
        },
    )
