"""The committed API spec, `docs/openapi.yml`, is the backend's (issue #254).

A change to the API -- an endpoint, a schema, a status code, a router -- fails this test until the
file is regenerated with `inv api generate` (which also raises the version) and committed.
"""

import pytest

from admin import api
from config.api_version import API_VERSION

FIX = 'docs/openapi.yml is out of date: run `inv api generate` and commit the result.'


@pytest.fixture(scope='module')
def committed():
    spec = api.read_spec(api.SPEC_PATH)
    assert spec is not None, FIX
    return spec


def test_committed_spec_matches_the_backend(committed):
    assert api.strip_version(api.build_spec()) == api.strip_version(committed), FIX


def test_committed_version_is_the_api_version(committed):
    assert committed['info']['version'] == API_VERSION, FIX


def test_the_spec_is_openapi_3_1(committed):
    assert committed['openapi'].startswith('3.1.')


def test_every_operation_tag_is_listed_once_with_a_description(committed):
    """A new router's tag goes in `config.api.TAGS`, or its group has no place and no line."""
    listed = [tag['name'] for tag in committed['tags']]
    used = {
        tag
        for operations in committed['paths'].values()
        for operation in operations.values()
        for tag in operation.get('tags', [])
    }

    assert len(listed) == len(set(listed))
    assert used <= set(listed), f'Add to TAGS in config/api.py: {sorted(used - set(listed))}'
    assert all(tag.get('description') for tag in committed['tags'])


def test_the_live_api_reports_the_same_version():
    from config.api import api as ninja_api

    assert ninja_api.version == API_VERSION
