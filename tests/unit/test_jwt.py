import pytest
from ninja_jwt.exceptions import TokenError

from src.user.models import (
    Permission,
    RolePermission,
    User,
    UserGlobalPermission,
    UserGlobalRole,
    UserServiceAssignment,
    UserServicePermission,
    UserServiceRole,
)
from src.user.tokens import CustomAccessToken, CustomRefreshToken

pytestmark = [pytest.mark.django_db, pytest.mark.unit]


def test_custom_access_token_includes_global_and_service_claims(
    regular_user: User,
    service,
    global_permission: Permission,
    global_role,
    service_permission: Permission,
    service_role,
):
    UserGlobalPermission.objects.create(user=regular_user, permission=global_permission)

    UserGlobalRole.objects.create(user=regular_user, role=global_role)
    RolePermission.objects.create(role=global_role, permission=global_permission)

    UserServiceAssignment.objects.create(user=regular_user, service=service, created_by=None)
    UserServicePermission.objects.create(
        user=regular_user, service=service, permission=service_permission
    )

    UserServiceRole.objects.create(user=regular_user, service=service, role=service_role)
    RolePermission.objects.create(role=service_role, permission=service_permission)

    token = CustomAccessToken.for_user(regular_user)

    assert isinstance(str(token), str)
    assert token['sub'] == str(regular_user.id)
    assert token['email'] == regular_user.email
    assert set(token['global_permissions']) == {'admin'}
    assert token['global_roles'] == ['super_admin']

    service_id = str(service.id)
    assert service_id in token['services']
    assert set(token['services'][service_id]['permissions']) == {'read'}
    assert token['services'][service_id]['roles'] == ['editor']


def test_custom_refresh_token_creates_access_token(regular_user: User):
    refresh = CustomRefreshToken.for_user(regular_user)
    access = refresh.access_token

    assert isinstance(access, CustomAccessToken)
    assert access['sub'] == str(regular_user.id)
    assert access['email'] == regular_user.email


def test_token_rejects_invalid_signature(settings, regular_user: User):
    token = CustomAccessToken.for_user(regular_user)
    token_str = str(token)

    # Tamper with the token
    tampered_token = token_str[:-5] + 'XXXXX'

    with pytest.raises(TokenError):
        CustomAccessToken(tampered_token)
