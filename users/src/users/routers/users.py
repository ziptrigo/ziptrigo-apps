from uuid import UUID

from ninja import Router
from ninja.errors import HttpError

from ..auth import AdminAuth
from ..models import (
    Permission,
    Role,
    Service,
    User,
    UserServiceAssignment,
    UserServicePermission,
    UserServiceRole,
)
from ..schemas import (
    UserCreateRequest,
    UserDeactivateRequest,
    UserResponse,
    UserServiceAssignmentUpdate,
    UserServiceInfo,
    UserServicesListResponse,
    UserUpdateRequest,
)

router = Router()
service_users_router = Router()
admin_auth = AdminAuth()


@service_users_router.post('/{service_id}/users', response=UserResponse, auth=admin_auth)
def create_service_user(request, service_id: UUID, payload: UserCreateRequest):
    """Create or assign a user to a service."""
    # Get or create user
    user, created = User.objects.get_or_create(email=payload.email, defaults={'name': payload.name})

    if created and payload.password:
        user.set_password(payload.password)
        user.save()

    # Get service
    try:
        service = Service.objects.get(id=service_id)
    except Service.DoesNotExist:
        raise HttpError(404, 'Service not found')

    # Create service assignment
    UserServiceAssignment.objects.get_or_create(
        user=user, service=service, defaults={'created_by': request.auth}
    )

    # Assign roles
    for role_name in payload.roles:
        try:
            role = Role.objects.get(service=service, name=role_name)
            UserServiceRole.objects.get_or_create(user=user, service=service, role=role)
        except Role.DoesNotExist:
            pass

    # Assign permissions
    for perm_code in payload.permissions:
        try:
            permission = Permission.objects.get(service=service, code=perm_code)
            UserServicePermission.objects.get_or_create(
                user=user, service=service, permission=permission
            )
        except Permission.DoesNotExist:
            pass

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


@router.get('/{user_id}/services', response=UserServicesListResponse, auth=admin_auth)
def list_user_services(request, user_id: UUID):
    """List all service assignments for a user."""
    try:
        user = User.objects.get(id=user_id)
    except User.DoesNotExist:
        raise HttpError(404, 'User not found')

    assignments = UserServiceAssignment.objects.filter(user=user).select_related('service')

    services_data = []
    for assignment in assignments:
        service = assignment.service

        # Get roles
        roles = UserServiceRole.objects.filter(user=user, service=service).select_related('role')
        role_names = [usr.role.name for usr in roles]

        # Get permissions
        perms = UserServicePermission.objects.filter(user=user, service=service).select_related(
            'permission'
        )
        perm_codes = [usp.permission.code for usp in perms]

        services_data.append(
            UserServiceInfo(
                service_id=str(service.id),
                service_name=service.name,
                roles=role_names,
                permissions=perm_codes,
            )
        )

    return UserServicesListResponse(services=services_data)


@router.patch('/{user_id}/services/{service_id}', auth=admin_auth)
def update_user_service_assignment(
    request, user_id: UUID, service_id: UUID, payload: UserServiceAssignmentUpdate
):
    """Update user's roles and permissions for a service."""
    try:
        user = User.objects.get(id=user_id)
        service = Service.objects.get(id=service_id)
    except (User.DoesNotExist, Service.DoesNotExist):
        raise HttpError(404, 'User or service not found')

    # Clear existing roles
    UserServiceRole.objects.filter(user=user, service=service).delete()

    # Assign new roles
    for role_name in payload.roles:
        try:
            role = Role.objects.get(service=service, name=role_name)
            UserServiceRole.objects.create(user=user, service=service, role=role)
        except Role.DoesNotExist:
            pass

    # Clear existing permissions
    UserServicePermission.objects.filter(user=user, service=service).delete()

    # Assign new permissions
    for perm_code in payload.permissions:
        try:
            permission = Permission.objects.get(service=service, code=perm_code)
            UserServicePermission.objects.create(user=user, service=service, permission=permission)
        except Permission.DoesNotExist:
            pass

    return {'detail': 'Updated successfully'}


@router.delete('/{user_id}/services/{service_id}', auth=admin_auth)
def delete_user_service_assignment(request, user_id: UUID, service_id: UUID):
    """Remove user's assignment to a service."""
    try:
        user = User.objects.get(id=user_id)
        service = Service.objects.get(id=service_id)
    except (User.DoesNotExist, Service.DoesNotExist):
        raise HttpError(404, 'User or service not found')

    # Delete assignment and related data
    UserServiceAssignment.objects.filter(user=user, service=service).delete()
    UserServiceRole.objects.filter(user=user, service=service).delete()
    UserServicePermission.objects.filter(user=user, service=service).delete()

    return {'detail': 'Service assignment removed successfully'}
