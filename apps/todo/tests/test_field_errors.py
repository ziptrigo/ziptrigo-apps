"""A refused To-do form carries each field's message to the page (issue #219): the slot has its
`Label: message` lines as before, and the same messages are hidden data for the fields."""

import re
from html import unescape

import pytest
from django.urls import reverse

from ..models import Task

pytestmark = pytest.mark.django_db

HTMX = {'HTTP_HX_REQUEST': 'true'}


def _field_messages(response) -> dict[str, list[str]]:
    found: dict[str, list[str]] = {}
    html = response.content.decode()
    for name, message in re.findall(r'<li data-field-error="([^"]+)">(.*?)</li>', html):
        found.setdefault(name, []).append(unescape(message))
    return found


def test_a_service_refusal_names_the_fields_that_failed(web):
    response = web.post(
        reverse('todo:create'), {'title': '', 'due_time': '09:00', 'view': 'today'}, **HTMX
    )

    assert response.status_code == 422
    assert response['HX-Retarget'] == '#quick-add-msg'
    assert 'Title: Title is required.' in unescape(response.content.decode())
    assert _field_messages(response) == {
        'title': ['Title is required.'],
        'due_time': ['A time needs a date.'],
    }
    assert not Task.objects.exists()


def test_a_refusal_that_belongs_to_no_field_is_only_in_the_slot(user, config):
    from django.test import Client

    from ..services import create_task

    create_task(user, title='One')
    create_task(user, title='Two')
    client = Client()
    client.force_login(user)

    response = client.post(reverse('todo:create'), {'title': 'Three'}, **HTMX)

    assert response.status_code == 422
    assert 'Top up your credits' in response.content.decode()
    assert _field_messages(response) == {}
    assert 'data-field-errors' not in response.content.decode()


def test_a_form_refusal_names_the_field(web, funded_user):
    from ..services import create_task

    task = create_task(funded_user, title='Plan')

    response = web.post(reverse('todo:comment-add', args=[task.number]), {'body': '   '}, **HTMX)

    assert response.status_code == 422
    assert list(_field_messages(response)) == ['body']
