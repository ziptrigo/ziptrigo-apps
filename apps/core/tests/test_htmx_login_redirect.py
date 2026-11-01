"""`HtmxLoginRedirectMiddleware`: an htmx request without a session navigates to the login page."""

import pytest
from django.urls import reverse

pytestmark = [pytest.mark.django_db, pytest.mark.unit]

HTMX = {'HTTP_HX_REQUEST': 'true'}


def test_htmx_post_without_session_gets_hx_redirect_to_login_with_current_page(client):
    response = client.post(
        reverse('feedback:submit'),
        {'description': 'hello'},
        HTTP_HX_CURRENT_URL='http://testserver/feedback/?x=1',
        **HTMX,
    )

    assert response.status_code == 200
    assert response.content == b''
    assert response['HX-Redirect'] == '/account/login/?next=/feedback/%3Fx%3D1'


def test_next_is_the_site_root_when_the_current_url_is_another_site(client):
    response = client.post(
        reverse('feedback:submit'), HTTP_HX_CURRENT_URL='https://evil.example.com/x', **HTMX
    )

    assert response['HX-Redirect'] == '/account/login/?next=/'


def test_plain_request_still_gets_the_302(client):
    response = client.post(reverse('feedback:submit'))

    assert response.status_code == 302
    assert response['Location'].startswith('/account/login/')
    assert 'HX-Redirect' not in response


def test_other_redirects_of_htmx_requests_are_left_alone(client, user):
    client.force_login(user)

    # A plain logout redirect (not to the login page) is not rewritten.
    response = client.post(reverse('accounts:logout'), **HTMX)

    assert response.status_code == 302
    assert 'HX-Redirect' not in response


def test_notification_bell_keeps_its_204(client):
    response = client.get(reverse('core:notification-bell'), **HTMX)

    assert response.status_code == 204
