"""Helpers for the To-do API tests (`test_api_*.py`): JWT headers and JSON request shortcuts over
Django's test client, which exercises the real `/api/todo/...` URLs."""

import json

import pytest

from apps.accounts.tokens import CustomAccessToken

BASE = '/api/todo'


def bearer(user) -> dict[str, str]:
    return {'HTTP_AUTHORIZATION': f'Bearer {CustomAccessToken.for_user(user)}'}


@pytest.fixture
def auth(funded_user) -> dict[str, str]:
    return bearer(funded_user)


@pytest.fixture
def other_auth(other_user) -> dict[str, str]:
    return bearer(other_user)


def get(client, path, auth, **query):
    return client.get(f'{BASE}{path}', query, **auth)


def post(client, path, auth, payload=None):
    return client.post(
        f'{BASE}{path}',
        data=json.dumps({} if payload is None else payload),
        content_type='application/json',
        **auth,
    )


def patch(client, path, auth, payload):
    return client.patch(
        f'{BASE}{path}', data=json.dumps(payload), content_type='application/json', **auth
    )


def delete(client, path, auth):
    return client.delete(f'{BASE}{path}', **auth)


def limit_rule(settings, rule: str, limit: int = 1, window: int = 3600) -> None:
    """Turn rate limiting on with a tiny budget for one rule."""
    settings.RATELIMIT_ENABLE = True
    settings.RATELIMIT_RULES = {**settings.RATELIMIT_RULES, rule: (limit, window)}
