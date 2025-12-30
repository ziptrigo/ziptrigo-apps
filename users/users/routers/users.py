from uuid import UUID

from ninja import Router
from ninja.errors import HttpError

from ..auth import AdminAuth
from ..models import User
from ..schemas import (
    UserCreateRequest,
    UserDeactivateRequest,
    UserResponse,
    UserUpdateRequest,
)

router = Router()
admin_auth = AdminAuth()


@router.post('/', response=UserResponse, auth=admin_auth)
def create_user(request, payload: UserCreateRequest):
    """Create a new user."""
    user = User.objects.create_user(
        email=payload.email,
        password=payload.password,
        name=payload.name,
    )
    return UserResponse.model_validate(user)


@router.get('/{user_id}', response=UserResponse, auth=admin_auth)
def get_user(request, user_id: UUID):
    """Get user details."""
    try:
        user = User.objects.get(id=user_id)
    except User.DoesNotExist:
        raise HttpError(404, 'User not found')

    return UserResponse.model_validate(user)


@router.patch('/{user_id}', response=UserResponse, auth=admin_auth)
def update_user(request, user_id: UUID, payload: UserUpdateRequest):
    """Update user details."""
    try:
        user = User.objects.get(id=user_id)
    except User.DoesNotExist:
        raise HttpError(404, 'User not found')

    if payload.email is not None:
        user.email = payload.email
    if payload.name is not None:
        user.name = payload.name

    user.save()

    return UserResponse.model_validate(user)


@router.delete('/{user_id}', auth=admin_auth)
def delete_user(request, user_id: UUID):
    """Soft delete a user."""
    try:
        user = User.objects.get(id=user_id)
    except User.DoesNotExist:
        raise HttpError(404, 'User not found')

    user.mark_deleted()

    return {'detail': 'User deleted successfully'}


@router.post('/{user_id}/deactivate', response=UserResponse, auth=admin_auth)
def deactivate_user(request, user_id: UUID, payload: UserDeactivateRequest):
    """Deactivate a user."""
    try:
        user = User.objects.get(id=user_id)
    except User.DoesNotExist:
        raise HttpError(404, 'User not found')

    user.deactivate(payload.reason)

    return UserResponse.model_validate(user)


@router.post('/{user_id}/reactivate', response=UserResponse, auth=admin_auth)
def reactivate_user(request, user_id: UUID):
    """Reactivate a user."""
    try:
        user = User.objects.get(id=user_id)
    except User.DoesNotExist:
        raise HttpError(404, 'User not found')

    user.reactivate()

    return UserResponse.model_validate(user)


