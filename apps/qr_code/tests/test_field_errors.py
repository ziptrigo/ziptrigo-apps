"""The QR code editor's refusals carry each field's message to the page (issue #219)."""

import re

import pytest
from django.urls import reverse

pytestmark = pytest.mark.django_db

HTMX = {'HTTP_HX_REQUEST': 'true'}


def _data(**overrides) -> dict:
    data = {'name': 'Menu', 'qr_type': 'url', 'qr_format': 'png', 'url': 'https://example.com'}
    data.update(overrides)
    return data


def _field_messages(html: str) -> dict[str, list[str]]:
    found: dict[str, list[str]] = {}
    for name, message in re.findall(r'<li data-field-error="([^"]+)">(.*?)</li>', html):
        found.setdefault(name, []).append(message)
    return found


def test_each_failed_field_is_named_and_the_slot_still_lists_every_line(client, user):
    client.force_login(user)

    response = client.post(
        reverse('qr_code:create-submit'), _data(name='', url='not a url'), **HTMX
    )
    html = response.content.decode()

    assert response.status_code == 422
    assert response['HX-Retarget'] == '#qrcode-msg'
    # The slot: `Label: message`, as before.
    assert 'Name: ' in html
    assert 'Text / URL: ' in html
    # The same messages, by the name of the input they belong to.
    messages = _field_messages(html)
    assert list(messages) == ['name', 'url']
    assert 'valid URL' in messages['url'][0]
    # Said once per place: the label-prefixed line is not in the field data.
    assert not any(m.startswith(('Name:', 'Text / URL:')) for ms in messages.values() for m in ms)


def test_the_edit_form_carries_them_too(client, user, qr_code):
    client.force_login(user)

    response = client.post(reverse('qr_code:edit-submit', args=[qr_code.pk]), {'name': ''}, **HTMX)

    assert response.status_code == 422
    assert list(_field_messages(response.content.decode())) == ['name']


def test_a_page_with_no_errors_carries_no_field_data(client, user):
    client.force_login(user)

    html = client.get(reverse('qr_code:create')).content.decode()

    assert 'data-field-errors' not in html
