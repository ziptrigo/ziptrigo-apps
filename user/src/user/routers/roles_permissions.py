from uuid import UUID

from ninja import Router
from ninja.errors import HttpError

from ..auth import AdminAuth
from ..models import Permission, Role, RolePermission, Service
from ..schemas import (
    PermissionCreate,
    PermissionListResponse,
    PermissionResponse,
    RoleCreate,
    RoleListResponse,
    RoleResponse,
)

router = Router()
admin_auth = AdminAuth()


@router.get('/{service_id}/permissions', response=PermissionListResponse, auth=admin_auth)
def list_service_permissions(request, service_id: UUID):
    """List all permissions for a service."""
    permissions = Permission.objects.filter(service_id=service_id)
    return PermissionListResponse(
        permissions=[PermissionResponse.model_validate(p) for p in permissions]
    )


@router.post('/{service_id}/permissions', response=PermissionResponse, auth=admin_auth)
def create_service_permission(request, service_id: UUID, payload: PermissionCreate):
    """Create a new permission for a service."""
    try:
        service = Service.objects.get(id=service_id)
    except Service.DoesNotExist:
        raise HttpError(404, 'Service not found')

    permission = Permission.objects.create(
        service=service,
        type=Permission.TYPE_SERVICE,
        code=payload.code,
        description=payload.description,
    )

    return PermissionResponse.model_validate(permission)


@router.get('/{service_id}/roles', response=RoleListResponse, auth=admin_auth)
def list_service_roles(request, service_id: UUID):
    """List all roles for a service."""
    roles = Role.objects.filter(service_id=service_id)
    return RoleListResponse(roles=[RoleResponse.model_validate(r) for r in roles])


@router.post('/{service_id}/roles', response=RoleResponse, auth=admin_auth)
def create_service_role(request, service_id: UUID, payload: RoleCreate):
    """Create a new role for a service."""
    try:
        service = Service.objects.get(id=service_id)
    except Service.DoesNotExist:
        raise HttpError(404, 'Service not found')

    role = Role.objects.create(service=service, name=payload.name, description=payload.description)

    # Add permissions to role if provided
    if payload.permissions:
        permissions = Permission.objects.filter(service=service, code__in=payload.permissions)
        for permission in permissions:
            RolePermission.objects.create(role=role, permission=permission)

    return RoleResponse.model_validate(role)
