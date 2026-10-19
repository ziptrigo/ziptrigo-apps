from datetime import timedelta

import pytest


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
        'AUTH_TOKEN_CLASSES': ('apps.accounts.tokens.CustomAccessToken',),
        'TOKEN_TYPE_CLAIM': 'token_type',
        'JTI_CLAIM': 'jti',
        'SLIDING_TOKEN_REFRESH_EXP_CLAIM': 'refresh_exp',
        'SLIDING_TOKEN_LIFETIME': timedelta(minutes=5),
        'SLIDING_TOKEN_REFRESH_LIFETIME': timedelta(days=1),
    }


@pytest.fixture(autouse=True)
def _turnstile_off(settings):
    """The signup captcha (issue #117) is off unless a test turns it on: pytest loads `.env.local`
    when it exists, and a developer's own Turnstile keys there mustn't change what these tests see.
    """
    settings.TURNSTILE_SITE_KEY = ''
    settings.TURNSTILE_SECRET_KEY = ''


@pytest.fixture(autouse=True)
def _microsoft_off(settings):
    """Sign in with Microsoft (issue #156) is off unless a test turns it on: pytest loads
    `.env.local` when it exists, and a developer's own Microsoft app there mustn't add a button to
    the pages these tests render."""
    settings.MICROSOFT_CLIENT_ID = ''
    settings.MICROSOFT_CLIENT_SECRET = ''
    settings.MICROSOFT_REDIRECT_URI = ''
