import secrets
from uuid import UUID

from ninja import Router
from ninja.errors import HttpError

from ..auth import AdminAuth
from ..models import Service
from ..schemas import ServiceCreate, ServiceListResponse, ServiceResponse, ServiceUpdate

router = Router()
admin_auth = AdminAuth()


@router.get('', response=ServiceListResponse, auth=admin_auth)
def list_services(request):
    """List all services."""
    services = Service.objects.all()
    return ServiceListResponse(services=[ServiceResponse.model_validate(s) for s in services])


@router.post('', response=ServiceResponse, auth=admin_auth)
def create_service(request, payload: ServiceCreate):
    """Create a new service with generated client_id and client_secret."""
    client_id = secrets.token_urlsafe(32)
    client_secret = secrets.token_urlsafe(64)

    service = Service.objects.create(
        name=payload.name,
        description=payload.description,
        status=payload.status,
        client_id=client_id,
        client_secret=client_secret,
    )

    return ServiceResponse.model_validate(service)


@router.get('/{service_id}', response=ServiceResponse, auth=admin_auth)
def get_service(request, service_id: UUID):
    """Get service details."""
    try:
        service = Service.objects.get(id=service_id)
    except Service.DoesNotExist:
        raise HttpError(404, 'Service not found')

    return ServiceResponse.model_validate(service)


@router.patch('/{service_id}', response=ServiceResponse, auth=admin_auth)
def update_service(request, service_id: UUID, payload: ServiceUpdate):
    """Update service details."""
    try:
        service = Service.objects.get(id=service_id)
    except Service.DoesNotExist:
        raise HttpError(404, 'Service not found')

    if payload.name is not None:
        service.name = payload.name
    if payload.description is not None:
        service.description = payload.description
    if payload.status is not None:
        service.status = payload.status

    service.save()

    return ServiceResponse.model_validate(service)
