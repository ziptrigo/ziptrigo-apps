"""How the Notes API is wired into the site: mounted, tagged, behind the JWT login on every route,
and its schema names do not clash with another app's (the OpenAPI document keys them by class
name alone, so two with one name would silently replace each other)."""

import re
import uuid
from collections import defaultdict

import pytest
from ninja import Schema

from config.api import TAGS, api

pytestmark = [pytest.mark.django_db, pytest.mark.unit]


def notes_operations() -> list[tuple[str, str]]:
    schema = api.get_openapi_schema()
    return [
        (method.upper(), path)
        for path, methods in schema['paths'].items()
        if path.startswith('/api/notes/')
        for method in methods
    ]


def test_the_notes_tag_is_documented_first_class():
    names = [tag['name'] for tag in TAGS]
    assert 'Notes' in names
    description = next(tag['description'] for tag in TAGS if tag['name'] == 'Notes')
    assert description == 'Markdown notes, their versions, tags, share links and saved filters.'


def test_every_operation_is_tagged_notes():
    schema = api.get_openapi_schema()
    for path, methods in schema['paths'].items():
        if path.startswith('/api/notes/'):
            for operation in methods.values():
                assert operation['tags'] == ['Notes'], path


def test_every_route_needs_a_token(client):
    operations = notes_operations()
    assert len(operations) == 32  # a new endpoint is a deliberate change here
    for method, path in operations:
        url = re.sub(r'\{[a-z_]+\}', str(uuid.uuid4()), path)
        response = client.generic(method, url, data='{}', content_type='application/json')
        assert response.status_code == 401, (method, path)


def test_a_malformed_token_is_a_401(client):
    response = client.get('/api/notes/notes', HTTP_AUTHORIZATION='Bearer nonsense')

    assert response.status_code == 401


def test_a_deactivated_account_is_a_401(client, funded_user):
    from apps.accounts.tokens import CustomAccessToken

    headers = {'HTTP_AUTHORIZATION': f'Bearer {CustomAccessToken.for_user(funded_user)}'}
    funded_user.status = type(funded_user).STATUS_INACTIVE
    funded_user.save()

    assert client.get('/api/notes/notes', **headers).status_code == 401


def test_the_schema_names_are_unique_across_the_apis():
    seen: dict[str, set[str]] = defaultdict(set)

    def walk(parent: type) -> None:
        for child in parent.__subclasses__():
            if child.__module__.startswith('apps.'):
                seen[child.__name__].add(f'{child.__module__}.{child.__qualname__}')
            walk(child)

    walk(Schema)
    clashes = {
        name: sorted(where)
        for name, where in seen.items()
        if len(where) > 1 and any('.notes.' in item for item in where)
    }
    assert not clashes, clashes
