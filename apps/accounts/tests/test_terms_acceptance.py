"""Accepting the Terms of Use and Privacy Policy (issue #96): the sign-up checkbox, the record on
the user, and the gate that sends a signed-in user who has not accepted to the accept page."""

from datetime import UTC, datetime

import pytest
from django.contrib.auth import SESSION_KEY
from django.core.exceptions import ValidationError
from django.urls import reverse

from apps.accounts.models import User
from apps.accounts.services import google_oauth, terms
from apps.accounts.services.signup import create_account
from apps.core import legal

pytestmark = [pytest.mark.django_db, pytest.mark.unit, pytest.mark.terms_gate]

HTMX = {'HTTP_HX_REQUEST': 'true'}
ACCEPT_URL = '/account/terms/'


def _register(client, **overrides):
    payload = {
        'name': 'New User',
        'email': 'newuser@example.com',
        'password': 'password123',
        'accept_terms': 'on',
        **overrides,
    }
    return client.post(reverse('accounts:register'), payload, **HTMX)


@pytest.fixture
def pending(db) -> User:
    """An account that never accepted: one made by "Sign up with Google", or before the feature."""
    return User.objects.create_user(
        email='pending@example.com', password='password123', email_confirmed=True
    )


@pytest.fixture
def accepted(pending) -> User:
    terms.accept(pending)
    return pending


# --- sign-up -------------------------------------------------------------------------------


def test_register_page_has_one_unticked_required_checkbox_linking_both_documents(client):
    html = client.get(reverse('accounts:register')).content.decode()

    assert html.count('name="accept_terms"') == 1
    checkbox = html.split('name="accept_terms"', 1)[1].split('>', 1)[0]
    assert 'required' in checkbox and 'checked' not in checkbox
    assert f'href="{reverse("core:terms")}" target="_blank"' in html
    assert f'href="{reverse("core:privacy")}" target="_blank"' in html


def test_signup_without_the_checkbox_is_refused(client):
    response = _register(client, accept_terms='')

    assert response.status_code == 422
    assert terms.TERMS_REQUIRED_MESSAGE in response.content.decode()
    assert 'data-field-error="accept_terms"' in response.content.decode()
    assert not User.objects.filter(email='newuser@example.com').exists()


def test_signup_with_the_checkbox_records_the_acceptance(client):
    before = datetime.now(UTC)

    response = _register(client)

    assert response.status_code == 200
    assert response['HX-Redirect'] == reverse('accounts:created')
    user = User.objects.get(email='newuser@example.com')
    assert user.terms_version == legal.TERMS_VERSION
    assert user.terms_accepted_at is not None and user.terms_accepted_at >= before
    assert not terms.needs_acceptance(user)


def test_a_refused_form_keeps_the_tick(client):
    html = _register(client, password='short').content.decode()

    assert 'checked' in html.split('name="accept_terms"', 1)[1].split('>', 1)[0]


def test_create_account_refuses_without_acceptance():
    with pytest.raises(ValidationError) as caught:
        create_account(name='A', email='a@example.com', password='password123', accept_terms=False)

    assert caught.value.message_dict == {'accept_terms': [terms.TERMS_REQUIRED_MESSAGE]}
    assert not User.objects.filter(email='a@example.com').exists()


# --- the service ---------------------------------------------------------------------------


def test_accept_records_the_current_version_once(pending):
    assert terms.needs_acceptance(pending)

    assert terms.accept(pending) is True
    pending.refresh_from_db()
    first = pending.terms_accepted_at
    assert pending.terms_version == legal.TERMS_VERSION and first is not None

    assert terms.accept(pending) is False
    pending.refresh_from_db()
    assert pending.terms_accepted_at == first


def test_a_new_version_asks_again(accepted, monkeypatch):
    monkeypatch.setattr(legal, 'TERMS_VERSION', '2099-01-01')

    assert terms.needs_acceptance(accepted)
    assert terms.accept(accepted) is True
    assert accepted.terms_version == '2099-01-01'


def test_an_account_made_by_google_sign_up_has_not_accepted(settings):
    settings.GOOGLE_CLIENT_ID = 'client-id'
    identity = google_oauth.identity_from_claims(
        {'sub': '123', 'email': 'someone@gmail.com', 'email_verified': True, 'name': 'Someone'}
    )

    user = google_oauth.resolve_user(identity)

    assert user.terms_version == '' and user.terms_accepted_at is None
    assert terms.needs_acceptance(user)


# --- the gate ------------------------------------------------------------------------------


def test_a_pending_user_is_sent_to_the_accept_page_with_next(client, pending):
    client.force_login(pending)

    response = client.get('/links/?status=active')

    assert response.status_code == 302
    assert response['Location'] == f'{ACCEPT_URL}?next=%2Flinks%2F%3Fstatus%3Dactive'


def test_accepting_lands_on_next_and_ends_the_gate(client, pending):
    client.force_login(pending)

    page = client.get(f'{ACCEPT_URL}?next=/account/')
    assert page.status_code == 200
    html = page.content.decode()
    assert 'One more step' in html
    assert 'name="next" value="/account/"' in html

    response = client.post(ACCEPT_URL, {'accept_terms': 'on', 'next': '/account/'})

    assert response.status_code == 302 and response['Location'] == '/account/'
    pending.refresh_from_db()
    assert pending.terms_version == legal.TERMS_VERSION
    assert client.get('/account/').status_code == 200


def test_accepting_without_the_tick_is_refused(client, pending):
    client.force_login(pending)

    response = client.post(ACCEPT_URL, {'next': '/account/'})

    assert response.status_code == 422
    assert terms.TERMS_REQUIRED_MESSAGE in response.content.decode()
    pending.refresh_from_db()
    assert pending.terms_version == ''


def test_next_must_be_on_this_site(client, pending):
    client.force_login(pending)

    response = client.post(ACCEPT_URL, {'accept_terms': 'on', 'next': 'https://evil.example/'})

    assert response['Location'] == reverse('core:home')


def test_a_post_elsewhere_is_redirected_without_being_handled(client, pending):
    client.force_login(pending)

    response = client.post(reverse('accounts:theme-update'), {'theme': 'dark'})

    assert response.status_code == 302 and response['Location'] == ACCEPT_URL
    pending.refresh_from_db()
    assert pending.theme == 'system'


def test_an_htmx_request_navigates_instead_of_swapping(client, pending):
    client.force_login(pending)

    response = client.get('/links/list/', **HTMX)

    assert response.status_code == 204 and response.content == b''
    assert response['HX-Redirect'] == ACCEPT_URL


def test_an_htmx_request_from_the_accept_page_does_not_reload_it(client, pending):
    client.force_login(pending)

    response = client.get(
        reverse('core:notification-bell'),
        HTTP_HX_CURRENT_URL=f'http://testserver{ACCEPT_URL}?next=/',
        **HTMX,
    )

    assert response.status_code == 204
    assert 'HX-Redirect' not in response


@pytest.mark.parametrize('path', ['/privacy/', '/terms/', '/static/core/css/site.css', '/sw.js'])
def test_public_pages_stay_reachable_while_pending(client, pending, path):
    client.force_login(pending)

    assert client.get(path).status_code in (200, 404)  # never the gate's redirect


def test_logout_stays_reachable_while_pending(client, pending):
    client.force_login(pending)

    response = client.post(reverse('accounts:logout'))

    assert response.status_code == 302 and ACCEPT_URL not in response['Location']
    assert SESSION_KEY not in client.session


def test_the_api_is_not_gated(client, pending):
    from apps.accounts.tokens import CustomAccessToken

    token = CustomAccessToken.for_user(pending)

    response = client.get('/api/account', HTTP_AUTHORIZATION=f'Bearer {token}')

    assert response.status_code == 200


def test_the_admin_is_not_gated(client):
    admin = User.objects.create_superuser(email='boss@example.com', password='password123')
    client.force_login(admin)

    assert client.get('/admin/').status_code == 200
    assert client.get('/').status_code == 302  # the site itself still asks


def test_an_accepted_user_and_a_visitor_are_left_alone(client, accepted):
    assert client.get('/').status_code == 200  # anonymous
    assert client.get(ACCEPT_URL).status_code == 302  # login required

    client.force_login(accepted)

    assert client.get('/account/').status_code == 200
    # Nothing to accept: the page sends them on.
    response = client.get(f'{ACCEPT_URL}?next=/account/')
    assert response.status_code == 302 and response['Location'] == '/account/'


def test_a_user_on_an_older_version_sees_the_page_once(client, accepted, monkeypatch):
    monkeypatch.setattr(legal, 'TERMS_VERSION', '2099-01-01')
    client.force_login(accepted)

    assert client.get('/account/')['Location'].startswith(ACCEPT_URL)
    assert 'Our terms have changed' in client.get(ACCEPT_URL).content.decode()

    client.post(ACCEPT_URL, {'accept_terms': 'on'})

    assert client.get('/account/').status_code == 200


def test_the_user_admin_shows_the_acceptance_read_only(client, accepted):
    admin = User.objects.create_superuser(email='boss@example.com', password='password123')
    client.force_login(admin)

    html = client.get(
        reverse('custom_admin:accounts_user_change', args=[accepted.pk])
    ).content.decode()

    assert legal.TERMS_VERSION in html
    assert 'name="terms_version"' not in html and 'name="terms_accepted_at' not in html
