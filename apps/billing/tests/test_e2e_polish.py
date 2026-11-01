"""Small fixes from the second E2E browser run (issue #224): the credits history on a phone, the
ledger line and notice of the welcome credits, the Billing admin pages, the redeem link's sign-in
notice and the E2E seed."""

import importlib.util
import re
from pathlib import Path

import pytest
from django.urls import reverse

from apps.accounts.models import User
from apps.billing.models import (
    BillingSettings,
    CreditTransaction,
    CreditTransactionType,
    WelcomeCredit,
    WelcomeCreditStatus,
)
from apps.billing.services import add_credits, get_balance, welcome
from apps.core.models import Notification

pytestmark = [pytest.mark.django_db, pytest.mark.unit]

HISTORY = reverse('billing:credits-history')


def test_credits_history_has_no_clipped_table_on_a_phone(client, user):
    """Below `sm` each row is a card: the table is not a scrolling box and the description wraps."""
    add_credits(user, 25, description='Promo code E2E-PROMO (qa)', source='billing')
    client.force_login(user)

    html = client.get(HISTORY).content.decode()

    assert 'overflow-x-auto' not in html
    assert 'class="block sm:table min-w-full' in html
    assert 'Promo code E2E-PROMO (qa)' in html
    assert 'break-words' in html


def test_ledger_row_does_not_repeat_the_type_as_description(client, user):
    # An older welcome row: the description is the very label shown in the pill beside it.
    CreditTransaction.objects.create(
        user=user, amount=1000, type=CreditTransactionType.WELCOME, description='Welcome credits'
    )
    client.force_login(user)

    html = client.get(HISTORY).content.decode()

    assert html.count('Welcome credits') == 1  # the pill only
    assert '(no description)' not in html


@pytest.mark.welcome_credits
def test_new_welcome_credits_describe_themselves_without_repeating_the_type(user):
    user.email_confirmed = True
    user.save()
    WelcomeCredit.objects.get_or_create(user=user)

    assert welcome.settle(str(user.pk)) == WelcomeCreditStatus.GRANTED

    tx = CreditTransaction.objects.get(user=user, type=CreditTransactionType.WELCOME)
    assert tx.description != tx.get_type_display()
    notice = Notification.objects.get(user=user, key='welcome_credits')
    assert 'welcome credits' not in notice.body.lower()


def test_promo_error_slot_is_announced_to_screen_readers(client, user):
    client.force_login(user)
    response = client.post(reverse('billing:redeem'), {'code': 'NOPE-NOPE'}, HTTP_HX_REQUEST='true')

    assert response.status_code == 422
    html = response.content.decode()
    assert 'id="promo-msg" role="alert"' in html
    assert 'Invalid code.' in html


def test_redeem_link_signed_out_explains_the_code_on_the_sign_in_page(client):
    response = client.get(reverse('billing:redeem-code', args=['SECRET-CODE']), follow=True)

    html = response.content.decode()
    assert response.redirect_chain
    assert 'Sign in to redeem your promo code' in html
    # Nothing is said about the code itself, so it cannot be probed through the notice.
    notice = html.split('Sign in to redeem')[1].split('</p>')[0]
    assert 'SECRET-CODE' not in notice


def test_plain_sign_in_page_has_no_promo_notice(client):
    html = client.get(reverse('accounts:login')).content.decode()

    assert 'redeem your promo code' not in html


# --- admin -----------------------------------------------------------------------------------


def test_billing_settings_form_bounds_and_matching_inputs(client, admin_user):
    client.force_login(admin_user)
    row = BillingSettings.load()

    html = client.get(
        reverse('custom_admin:billing_billingsettings_change', args=[row.pk])
    ).content.decode()

    assert 'issue #160' not in html
    inputs = {
        name: tag
        for tag in re.findall(r'<input[^>]*>', html)
        for name in re.findall(r'name="(welcome_credits|storage_price_per_gb_per_day)"', tag)
    }
    assert 'max="1000000"' in inputs['welcome_credits']
    for tag in inputs.values():
        assert 'class="vIntegerField"' in tag  # the two inputs are styled alike


def test_billing_settings_success_message_has_no_doubled_name(client, admin_user):
    client.force_login(admin_user)
    row = BillingSettings.load()
    url = reverse('custom_admin:billing_billingsettings_change', args=[row.pk])

    response = client.post(
        url,
        {'storage_price_per_gb_per_day': '0.2', 'welcome_credits': '500', '_save': 'Save'},
        follow=True,
    )

    assert response.status_code == 200
    html = response.content.decode()
    assert 'Billing settings saved.' in html
    assert '“Billing settings”' not in html
    row.refresh_from_db()
    assert row.welcome_credits == 500


@pytest.mark.welcome_credits
def test_welcome_credit_admin_shows_the_email_and_a_readable_ledger_line(client, admin_user, user):
    user.email_confirmed = True
    user.save()
    grant, _ = WelcomeCredit.objects.get_or_create(user=user)
    welcome.settle(str(user.pk))
    grant.refresh_from_db()
    assert grant.status == WelcomeCreditStatus.GRANTED
    assert str(grant) == f'{user.email}: granted'
    client.force_login(admin_user)

    html = client.get(
        reverse('custom_admin:billing_welcomecredit_change', args=[grant.pk])
    ).content.decode()

    assert user.email in html
    assert '+1000 Welcome credits, ' in html
    assert ' UTC' in html
    assert '+00:00' not in html


def test_welcome_credit_admin_without_a_ledger_line(client, admin_user, user):
    grant, _ = WelcomeCredit.objects.get_or_create(user=user)
    client.force_login(admin_user)

    response = client.get(reverse('custom_admin:billing_welcomecredit_change', args=[grant.pk]))

    assert response.status_code == 200


# --- the E2E seed ----------------------------------------------------------------------------


@pytest.mark.welcome_credits
def test_e2e_seed_leaves_no_pending_welcome_rows_and_keeps_the_balances():
    path = Path(__file__).resolve().parents[3] / 'tests_e2e' / 'ai' / 'serve.py'
    spec = importlib.util.spec_from_file_location('e2e_ai_serve', path)
    assert spec and spec.loader
    serve = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(serve)
    assert BillingSettings.load().welcome_credits == 1000

    exec(serve.SEED, {})

    assert not WelcomeCredit.objects.exists()
    assert BillingSettings.load().welcome_credits == 1000  # put back for people who register
    balances = {u.email: get_balance(u) for u in User.objects.all()}
    assert balances['qa.user@example.com'] == 100
    assert balances['qa.broke@example.com'] == 0
