from datetime import timedelta

import pytest
from tests.factories import UserFactory


@pytest.fixture(autouse=True)
def _jwt_settings(settings):
    """Make JWT behavior deterministic for tests."""
    settings.JWT_SECRET = 'test-secret'
    settings.JWT_ALGORITHM = 'HS256'
    settings.JWT_EXP_DELTA_SECONDS = 3600

    # Configure NINJA_JWT settings
    settings.NINJA_JWT = {
        'ACCESS_TOKEN_LIFETIME': timedelta(seconds=3600),
        'REFRESH_TOKEN_LIFETIME': timedelta(days=30),
        'ROTATE_REFRESH_TOKENS': False,
        'BLACKLIST_AFTER_ROTATION': False,
        'UPDATE_LAST_LOGIN': False,
        'ALGORITHM': 'HS256',
        'SIGNING_KEY': 'test-secret',
        'VERIFYING_KEY': None,
        'AUDIENCE': None,
        'ISSUER': None,
        'AUTH_HEADER_TYPES': ('Bearer',),
        'AUTH_HEADER_NAME': 'HTTP_AUTHORIZATION',
        'USER_ID_FIELD': 'id',
        'USER_ID_CLAIM': 'sub',
        'USER_AUTHENTICATION_RULE': 'ninja_jwt.authentication.default_user_authentication_rule',
        'AUTH_TOKEN_CLASSES': ('users.users.tokens.CustomAccessToken',),
        'TOKEN_TYPE_CLAIM': 'token_type',
        'JTI_CLAIM': 'jti',
        'SLIDING_TOKEN_REFRESH_EXP_CLAIM': 'refresh_exp',
        'SLIDING_TOKEN_LIFETIME': timedelta(minutes=5),
        'SLIDING_TOKEN_REFRESH_LIFETIME': timedelta(days=1),
    }


@pytest.fixture()
def api_client():
    from ninja.testing import TestClient
    from users.users.api import api

    return TestClient(api)


@pytest.fixture()
def admin_user():
    return UserFactory(is_staff=True, is_superuser=True)


@pytest.fixture()
def regular_user():
    return UserFactory()
