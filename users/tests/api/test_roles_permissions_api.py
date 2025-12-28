import pytest

from src.users.models import Permission, RolePermission

pytestmark = [pytest.mark.django_db, pytest.mark.integration]


def test_admin_can_create_permission_for_service(api_client, admin_user, service):
    api_client.force_authenticate(user=admin_user)

    response = api_client.post(
        f'/api/services/{service.id}/permissions',
        {
            'type': Permission.TYPE_SERVICE,
            'service': str(service.id),
            'code': 'write',
            'description': 'Write permission',
        },
        format='json',
    )

    assert response.status_code == 201
    assert response.data['code'] == 'write'
    assert response.data['type'] == Permission.TYPE_SERVICE
    assert str(response.data['service']) == str(service.id)


def test_admin_can_create_role_and_map_permission_codes(api_client, admin_user, service):
    api_client.force_authenticate(user=admin_user)

    Permission.objects.create(
        type=Permission.TYPE_SERVICE,
        service=service,
        code='read',
        description='Read',
    )
    Permission.objects.create(
        type=Permission.TYPE_SERVICE,
        service=service,
        code='write',
        description='Write',
    )

    response = api_client.post(
        f'/api/services/{service.id}/roles',
        {'name': 'editor', 'description': 'Editor', 'permissions': ['read', 'missing', 'write']},
        format='json',
    )

    assert response.status_code == 201
    role_id = response.data['id']

    mapped_codes = set(
        RolePermission.objects.filter(role_id=role_id).values_list('permission__code', flat=True)
    )
    assert mapped_codes == {'read', 'write'}
