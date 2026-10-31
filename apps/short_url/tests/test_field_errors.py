"""A refused link form carries each field's message to the page (issue #219): the slot above the
form says it as before, and the same message is also hidden data for the field's own line."""

import re

import pytest
from django.core.exceptions import ValidationError
from django.urls import reverse

from apps.short_url.views.common import validation_lines

from .factories import make_user

pytestmark = pytest.mark.django_db

HX = {'HTTP_HX_REQUEST': 'true'}


def _field_messages(html: str) -> dict[str, list[str]]:
    found: dict[str, list[str]] = {}
    for name, message in re.findall(r'<li data-field-error="([^"]+)">(.*?)</li>', html):
        found.setdefault(name, []).append(message)
    return found


def test_a_service_refusal_names_the_field_that_failed(web):
    response = web.post(
        reverse('short_url:create'),
        {'destination': 'https://example.com/x', 'expires_at': '2001-01-01T00:00:00Z'},
        **HX,
    )
    html = response.content.decode()

    assert response.status_code == 422
    assert response['HX-Retarget'] == '#link-form-msg'
    # The slot is unchanged: `Label: message`.
    assert 'Expires: ' in html
    # And the field has its message too, under the input that carries the date.
    messages = _field_messages(html)
    assert list(messages) == ['expires_at']
    assert 'future' in messages['expires_at'][0]


def test_a_form_refusal_names_the_field_that_failed(web):
    response = web.post(reverse('short_url:create'), {'destination': ''}, **HX)
    html = response.content.decode()

    assert response.status_code == 422
    assert 'Destination: ' in html
    assert list(_field_messages(html)) == ['destination']


def test_a_refusal_that_belongs_to_no_field_is_only_in_the_slot(client):
    broke = make_user()  # no credits, and an alias costs one
    client.force_login(broke)

    response = client.post(
        reverse('short_url:create'),
        {'destination': 'https://example.com/c', 'alias': 'costs-one', 'confirm_cost': '1'},
        **HX,
    )
    html = response.content.decode()

    assert response.status_code == 422
    assert 'have enough credits for this link.' in html
    assert _field_messages(html) == {}
    assert 'data-field-errors' not in html


def test_a_line_for_no_field_is_plain_and_comes_before_the_labelled_ones():
    """A service can report a field and something general in one error: the general line is
    printed as it is (not "All: ..."), first, and only the field's message is field data."""
    exc = ValidationError({'expires_at': ['Must be in the future.'], '__all__': ['Nope.']})

    lines = validation_lines(exc)

    assert list(lines) == ['Nope.', 'Expires: Must be in the future.']
    assert lines.field_errors == {'expires_at': ['Must be in the future.']}
