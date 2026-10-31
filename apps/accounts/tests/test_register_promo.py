"""The register page's promo code field (issue #70): validated through the `signup_codes` hook,
reserved after the account is created, granted when the email is confirmed."""

import re

import pytest
from django.core.exceptions import ValidationError
from django.urls import reverse

from apps.accounts import signup_codes
from apps.accounts.models import User
from apps.accounts.services.email_confirmation import get_email_confirmation_service
from apps.accounts.services.password_reset import get_password_reset_service
from apps.accounts.signals import email_confirmed
from apps.billing.models import PromoCode, PromoRedemption, PromoRedemptionStatus
from apps.billing.services import get_balance

pytestmark = [pytest.mark.django_db, pytest.mark.unit]

HTMX = {'HTTP_HX_REQUEST': 'true'}


def _register(client, **overrides):
    payload = {
        'name': 'New User',
        'email': 'newuser@example.com',
        'password': 'password123',
        **overrides,
    }
    return client.post(reverse('accounts:register'), payload, **HTMX)


@pytest.fixture
def code():
    return PromoCode.objects.create(code='WELCOME1', credits=300, max_redemptions=1)


@pytest.fixture
def sent_emails(monkeypatch):
    """Every email `core.services.email_verification` sends, so a test can read the confirmation
    link out of the signup email (a resend within the cooldown would send nothing)."""
    sent = []
    monkeypatch.setattr(
        'apps.core.services.email_verification.send_email',
        lambda **kwargs: sent.append(kwargs) or (1, 0),
    )
    return sent


def _token(sent) -> str:
    match = re.search(r'confirm-email/([^/\s]+)/', sent[-1]['text_body'])
    assert match is not None
    return match.group(1)


def _confirm(client, token):
    """Click the emailed link."""
    return client.post(reverse('accounts:confirm-email', args=[token]))


def test_signup_with_code_reserves_then_grants_on_confirmation(
    client, code, sent_emails, django_capture_on_commit_callbacks
):
    with django_capture_on_commit_callbacks(execute=True):
        response = _register(client, promo_code='welcome1')

    assert response['HX-Redirect'] == reverse('accounts:created')
    user = User.objects.get(email='newuser@example.com')
    reservation = PromoRedemption.objects.get(user=user)
    assert reservation.status == PromoRedemptionStatus.PENDING
    assert get_balance(user) == 0
    code.refresh_from_db()
    assert code.claimed_count == 1

    with django_capture_on_commit_callbacks(execute=True):
        response = _confirm(client, _token(sent_emails))

    assert response.status_code == 302
    assert response['Location'] == reverse('accounts:email-confirmed')
    reservation.refresh_from_db()
    assert reservation.status == PromoRedemptionStatus.GRANTED
    assert get_balance(user) == 300


def test_signup_without_code_is_unchanged(client, code):
    response = _register(client)
    assert response['HX-Redirect'] == reverse('accounts:created')
    assert not PromoRedemption.objects.exists()


def test_invalid_code_is_a_form_error_before_the_account_is_created(client):
    response = _register(client, promo_code='NOPE1234')

    assert response.status_code == 422
    assert 'Promo code: Invalid code.' in response.content.decode()
    assert not User.objects.filter(email='newuser@example.com').exists()


def test_email_bound_code_for_someone_else_is_invalid(client):
    PromoCode.objects.create(code='VIPONLY1', credits=1, allowed_emails=['vip@example.com'])
    response = _register(client, promo_code='VIPONLY1')
    assert response.status_code == 422
    assert 'Invalid code.' in response.content.decode()
    response = _register(client, promo_code='VIPONLY1', email='vip@example.com')
    assert response.status_code == 200


def test_code_check_is_rate_limited_per_ip(client, code, settings, ratelimit_clock):
    settings.RATELIMIT_ENABLE = True
    settings.RATELIMIT_RULES = {**settings.RATELIMIT_RULES, 'PROMO_REDEEM_IP': (1, 3600)}
    assert _register(client, promo_code='NOPE1234').status_code == 422

    response = _register(client, promo_code='NOPE1234')

    assert response.status_code == 429
    assert response['HX-Retarget'] == '#register-msg'
    assert not User.objects.filter(email='newuser@example.com').exists()


def test_code_check_does_not_spend_the_signup_budget(client, code, settings, ratelimit_clock):
    settings.RATELIMIT_ENABLE = True
    settings.RATELIMIT_RULES = {**settings.RATELIMIT_RULES, 'SIGNUP_IP': (1, 3600)}
    for _ in range(3):
        assert _register(client, promo_code='NOPE1234').status_code == 422
    assert _register(client, promo_code='WELCOME1').status_code == 200


def test_existing_address_with_a_code_answers_the_same_and_reserves_nothing(client, user, code):
    response = _register(client, email=user.email, promo_code='WELCOME1')

    assert response.status_code == 200
    assert response['HX-Redirect'] == reverse('accounts:created')
    assert not PromoRedemption.objects.exists()
    code.refresh_from_db()
    assert code.claimed_count == 0


def test_without_a_handler_a_code_is_invalid(client, code, monkeypatch):
    monkeypatch.setattr(signup_codes, '_handler', None)
    response = _register(client, promo_code='WELCOME1')
    assert response.status_code == 422
    assert 'Invalid code.' in response.content.decode()
    assert not User.objects.filter(email='newuser@example.com').exists()


def test_handler_error_message_is_shown(client, monkeypatch):
    class Handler:
        def validate(self, code, email):
            raise ValidationError('Custom refusal.')

        def attach(self, user, code):
            raise AssertionError('not reached')

    monkeypatch.setattr(signup_codes, '_handler', Handler())
    response = _register(client, promo_code='ANY12345')
    assert response.status_code == 422
    assert 'Custom refusal.' in response.content.decode()


def test_register_page_prefills_from_the_query_string(client):
    html = client.get(reverse('accounts:register') + '?promo=abc123').content.decode()
    assert 'value="abc123"' in html


def test_email_confirmed_signal_fires_on_confirmation_and_reset(
    client, sent_emails, django_capture_on_commit_callbacks
):
    received = []
    email_confirmed.connect(
        lambda sender, user, **kw: received.append(user.pk), dispatch_uid='t', weak=False
    )
    try:
        unconfirmed = User.objects.create_user(email='a@example.com', password='password123')
        get_email_confirmation_service().send_confirmation_email(unconfirmed)
        token = _token(sent_emails)
        with django_capture_on_commit_callbacks(execute=True):
            assert _confirm(client, token).status_code == 302
        assert received == [unconfirmed.pk]

        # The same link again (idempotent for its own row) confirms nothing new: no signal.
        received.clear()
        with django_capture_on_commit_callbacks(execute=True):
            assert _confirm(client, token).status_code == 302
        assert received == []

        other = User.objects.create_user(email='b@example.com', password='password123')
        service = get_password_reset_service()
        token = service.reset_url_for(other).rsplit('/', 2)[-2]
        with django_capture_on_commit_callbacks(execute=True):
            service.reset_password(other, 'newpassword9', token=token)
        assert received == [other.pk]
    finally:
        email_confirmed.disconnect(dispatch_uid='t')
