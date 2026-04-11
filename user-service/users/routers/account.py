from django.http import HttpRequest
from ninja import Router
from ninja.errors import HttpError

from ..auth import JWTAuth
from ..models import User
from ..schemas import AccountUpdateRequest, AccountUpdateResponse

router = Router()
jwt_auth = JWTAuth()


@router.put('/account', response=AccountUpdateResponse, auth=jwt_auth)
def update_account(request: HttpRequest, payload: AccountUpdateRequest):
    """Update current user's account information (name and/or email).

    Requires JWT authentication.
    """
    user: User = request.auth  # type: ignore

    # Update name if provided
    if payload.name is not None:
        user.name = payload.name

    # Update email if provided and different
    if payload.email and payload.email != user.email:
        # Check if email already exists
        if User.objects.filter(email=payload.email).exclude(id=user.id).exists():
            raise HttpError(400, 'Email already in use')
        user.email = payload.email

    user.save()

    return AccountUpdateResponse(
        message='Profile updated successfully',
        user={
            'id': str(user.id),
            'name': user.name,
            'email': user.email,
            'credits': user.credits,
        },
    )
