"""The redeem form, the redeem link and the credits history line (issue #70)."""

from typing import cast

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.accounts.tests.factories import UserFactory
from apps.billing.models import PromoCode
from apps.billing.services import get_balance

pytestmark = [pytest.mark.django_db, pytest.mark.unit]

HTMX = {'HTTP_HX_REQUEST': 'true'}
REDEEM = reverse('billing:redeem')
HISTORY = reverse('billing:credits-history')


@pytest.fixture
def code():
    return PromoCode.objects.create(code='LAUNCH2026', credits=250, campaign='Launch')


def _post(client, code_text, *, next_url='', htmx=True):
    data = {'code': code_text, 'next': next_url}
    return client.post(REDEEM, data, **(HTMX if htmx else {}))


def test_redeem_form_is_on_the_account_and_credits_pages(client, user):
    client.force_login(user)
    for url in (reverse('accounts:account'), HISTORY):
        html = client.get(url).content.decode()
        assert f'hx-post="{REDEEM}"' in html
        assert 'Have a promo code?' in html


def test_redeem_requires_login(client):
    response = client.post(REDEEM, {'code': 'LAUNCH2026'})
    assert response.status_code == 302
    assert response['Location'].startswith(reverse('accounts:login'))


def test_redeem_success_redirects_with_a_flash(client, user, code):
    client.force_login(user)

    response = _post(client, 'launch2026', next_url=reverse('accounts:account'))

    assert response.status_code == 200
    assert response['HX-Redirect'] == reverse('accounts:account')
    assert get_balance(user) == 250
    page = client.get(reverse('accounts:account')).content.decode()
    assert 'Added 250 credits with promo code LAUNCH2026.' in page


def test_redeem_without_htmx_redirects_and_flashes(client, user, code):
    client.force_login(user)
    response = _post(client, 'LAUNCH2026', htmx=False)
    assert response.status_code == 302
    assert response['Location'] == HISTORY
    assert get_balance(user) == 250


def test_history_line_names_the_code(client, user, code):
    client.force_login(user)
    _post(client, 'LAUNCH2026')
    html = client.get(HISTORY).content.decode()
    assert 'Promo code LAUNCH2026 (Launch)' in html
    assert '+250' in html
    assert '>Promo code</span>' in html


def test_refusal_is_a_422_partial_with_the_message(client, user, code):
    client.force_login(user)
    _post(client, 'LAUNCH2026')

    response = _post(client, 'LAUNCH2026')

    assert response.status_code == 422
    html = response.content.decode()
    assert 'You already redeemed this code.' in html
    assert 'id="promo-form"' in html
    assert 'value="LAUNCH2026"' in html  # what was typed stays
    assert get_balance(user) == 250


def test_unknown_code_is_invalid(client, user):
    client.force_login(user)
    response = _post(client, 'NOPE1234')
    assert response.status_code == 422
    assert 'Invalid code.' in response.content.decode()


def test_empty_code_is_refused_without_a_lookup(client, user):
    client.force_login(user)
    response = _post(client, '   ')
    assert response.status_code == 422
    assert 'Enter a code.' in response.content.decode()


def test_refusal_without_htmx_flashes_an_error(client, user):
    client.force_login(user)
    response = _post(client, 'NOPE1234', htmx=False)
    assert response.status_code == 302
    assert response['Location'] == HISTORY
    assert 'Invalid code.' in client.get(HISTORY).content.decode()


def test_unconfirmed_user_is_told_to_confirm(client, code):
    user = cast(User, UserFactory())
    client.force_login(user)
    response = _post(client, 'LAUNCH2026')
    assert response.status_code == 422
    assert 'Confirm your email address first.' in response.content.decode()
    assert get_balance(user) == 0


def test_next_must_be_same_site(client, user, code):
    client.force_login(user)
    response = _post(client, 'LAUNCH2026', next_url='https://evil.example/x')
    assert response['HX-Redirect'] == HISTORY


def test_rate_limited_per_user(client, user, settings, ratelimit_clock):
    settings.RATELIMIT_ENABLE = True
    settings.RATELIMIT_RULES = {**settings.RATELIMIT_RULES, 'PROMO_REDEEM_USER': (2, 3600)}
    client.force_login(user)
    for _ in range(2):
        assert _post(client, 'NOPE1234').status_code == 422

    response = _post(client, 'NOPE1234')

    assert response.status_code == 429
    assert response['HX-Retarget'] == '#promo-msg'
    assert 'Retry-After' in response


def test_rate_limited_per_ip(client, user, settings, ratelimit_clock):
    settings.RATELIMIT_ENABLE = True
    settings.RATELIMIT_RULES = {**settings.RATELIMIT_RULES, 'PROMO_REDEEM_IP': (1, 3600)}
    client.force_login(user)
    assert _post(client, 'NOPE1234').status_code == 422
    assert _post(client, 'NOPE1234').status_code == 429


# -- the redeem link --


def test_redeem_link_shows_the_form_prefilled(client, user, code):
    client.force_login(user)
    response = client.get(reverse('billing:redeem-code', args=['launch2026']))
    assert response.status_code == 200
    html = response.content.decode()
    assert 'value="LAUNCH2026"' in html
    assert f'hx-post="{REDEEM}"' in html
    assert get_balance(user) == 0  # a GET never redeems


@pytest.mark.parametrize(('balance', 'text'), [(1, '1</span> credit\n'), (2, '2</span> credits\n')])
def test_redeem_page_pluralizes_the_balance(client, user, code, balance, text):
    from apps.billing.services import add_credits

    add_credits(user, balance, source='test')
    client.force_login(user)

    html = client.get(reverse('billing:redeem-code', args=['launch2026'])).content.decode()

    assert text in html


@pytest.mark.parametrize(('balance', 'text'), [(1, '1</span> credit\n'), (2, '2</span> credits\n')])
def test_credits_history_pluralizes_the_balance(client, user, balance, text):
    """`|pluralize` can't read the lazy `credits_balance` and always said "credit"."""
    from apps.billing.services import add_credits

    add_credits(user, balance, source='test')
    client.force_login(user)

    assert text in client.get(HISTORY).content.decode()


def test_redeem_link_without_session_goes_to_login_with_next_and_promo(client, code):
    url = reverse('billing:redeem-code', args=['LAUNCH2026'])
    response = client.get(url)
    assert response.status_code == 302
    location = response['Location']
    assert location.startswith(reverse('accounts:login') + '?')
    assert 'next=%2Fbilling%2Fredeem%2FLAUNCH2026%2F' in location
    assert 'promo=LAUNCH2026' in location
    # The login page forwards the code to the register link.
    login_html = client.get(location).content.decode()
    assert f'href="{reverse("accounts:register")}?promo=LAUNCH2026"' in login_html
    # And the register page prefills it.
    register_html = client.get(reverse('accounts:register') + '?promo=LAUNCH2026').content.decode()
    assert 'name="promo_code"' in register_html
    assert 'value="LAUNCH2026"' in register_html


def test_login_page_register_link_is_plain_without_promo(client):
    html = client.get(reverse('accounts:login')).content.decode()
    assert f'href="{reverse("accounts:register")}"' in html


def test_redeem_link_then_login_lands_on_the_form(user, code):
    client = Client()
    url = reverse('billing:redeem-code', args=['LAUNCH2026'])
    response = client.get(url)
    login_url = response['Location']
    response = client.post(
        login_url,
        {'email': user.email, 'password': 'testpass123', 'next': url},
    )
    assert response.status_code == 302
    assert response['Location'] == url


def test_expired_code_message_on_the_form(client, user):
    PromoCode.objects.create(
        code='OLD12345', credits=1, ends_at=timezone.now() - timezone.timedelta(days=1)
    )
    client.force_login(user)
    response = _post(client, 'OLD12345')
    assert 'This code has expired.' in response.content.decode()
