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


def test_the_live_api_reports_the_same_version():
    from config.api import api as ninja_api

    assert ninja_api.version == API_VERSION
