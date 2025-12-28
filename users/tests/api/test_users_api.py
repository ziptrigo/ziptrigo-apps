import pytest

from src.users.models import (
    Permission,
    Role,
    User,
    UserServiceAssignment,
    UserServicePermission,
    UserServiceRole,
)

pytestmark = [pytest.mark.django_db, pytest.mark.integration]


def test_admin_can_create_service_user_and_assign_roles_permissions(
    api_client, admin_user, service
):
    api_client.force_authenticate(user=admin_user)

    role = Role.objects.create(service=service, name='editor', description='Editor')
    permission = Permission.objects.create(
        type=Permission.TYPE_SERVICE,
        service=service,
        code='read',
        description='Read',
    )

    response = api_client.post(
        f'/api/services/{service.id}/users',
        {
            'email': 'new.user@example.com',
            'name': 'New User',
            'password': 'pass-123',
            'roles': [role.name],
            'permissions': [permission.code],
        },
        format='json',
    )

    assert response.status_code == 201
    user_id = response.data['id']

    user = User.objects.get(id=user_id)
    assert user.email == 'new.user@example.com'
    assert user.check_password('pass-123')

    assert UserServiceAssignment.objects.filter(user=user, service=service).exists()
    assert UserServiceRole.objects.filter(user=user, service=service, role=role).exists()
    assert UserServicePermission.objects.filter(
        user=user, service=service, permission=permission
    ).exists()


def test_admin_can_deactivate_and_reactivate_user(api_client, admin_user, regular_user: User):
    api_client.force_authenticate(user=admin_user)

    deactivate = api_client.post(
        f'/api/users/{regular_user.id}/deactivate',
        {'reason': 'manual'},
        format='json',
    )

    assert deactivate.status_code == 200
    regular_user.refresh_from_db()
    assert regular_user.status == User.STATUS_INACTIVE
    assert regular_user.inactive_reason == 'manual'

    reactivate = api_client.post(f'/api/users/{regular_user.id}/reactivate', format='json')

    assert reactivate.status_code == 200
    regular_user.refresh_from_db()
    assert regular_user.status == User.STATUS_ACTIVE
    assert regular_user.inactive_reason == ''


def test_user_services_list_includes_roles_and_permissions(api_client, admin_user, service):
    api_client.force_authenticate(user=admin_user)

    user = User.objects.create_user(email='svc.user@example.com', password='password123', name='')
    role = Role.objects.create(service=service, name='editor', description='Editor')
    permission = Permission.objects.create(
        type=Permission.TYPE_SERVICE,
        service=service,
        code='read',
        description='Read',
    )

    UserServiceAssignment.objects.create(user=user, service=service, created_by=admin_user)
    UserServiceRole.objects.create(user=user, service=service, role=role)
    UserServicePermission.objects.create(user=user, service=service, permission=permission)

    response = api_client.get(f'/api/users/{user.id}/services')

    assert response.status_code == 200
    assert response.data == [
        {
            'service_id': str(service.id),
            'service_name': service.name,
            'roles': ['editor'],
            'permissions': ['read'],
        }
    ]


def test_admin_can_update_user_service_assignment_and_remove_it(api_client, admin_user, service):
    api_client.force_authenticate(user=admin_user)

    user = User.objects.create_user(
        email='assign.user@example.com', password='password123', name=''
    )

    role_editor = Role.objects.create(service=service, name='editor', description='Editor')
    role_viewer = Role.objects.create(service=service, name='viewer', description='Viewer')

    perm_read = Permission.objects.create(
        type=Permission.TYPE_SERVICE,
        service=service,
        code='read',
        description='Read',
    )
    perm_write = Permission.objects.create(
        type=Permission.TYPE_SERVICE,
        service=service,
        code='write',
        description='Write',
    )

    UserServiceAssignment.objects.create(user=user, service=service, created_by=admin_user)
    UserServiceRole.objects.create(user=user, service=service, role=role_editor)
    UserServicePermission.objects.create(user=user, service=service, permission=perm_read)

    patch = api_client.patch(
        f'/api/users/{user.id}/services/{service.id}',
        {'roles': ['viewer'], 'permissions': ['write']},
        format='json',
    )

    assert patch.status_code == 200
    assert UserServiceRole.objects.filter(user=user, service=service).count() == 1
    assert UserServiceRole.objects.filter(user=user, service=service, role=role_viewer).exists()
    assert UserServicePermission.objects.filter(user=user, service=service).count() == 1
    assert UserServicePermission.objects.filter(
        user=user, service=service, permission=perm_write
    ).exists()

    delete = api_client.delete(f'/api/users/{user.id}/services/{service.id}')

    assert delete.status_code == 204
    assert not UserServiceAssignment.objects.filter(user=user, service=service).exists()
    assert not UserServiceRole.objects.filter(user=user, service=service).exists()
    assert not UserServicePermission.objects.filter(user=user, service=service).exists()
