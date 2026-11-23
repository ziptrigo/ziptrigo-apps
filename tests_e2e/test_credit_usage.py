"""The credit usage report on the Credits page (issue #347), end to end in a real browser.

A fresh account with a made-up ledger (so what other tests spend doesn't change the numbers):
the Usage tab opens first, the period, the grouping and the app chips refresh the report in place
and keep the address bar on the page's own URL, and the History tab has the ledger and the promo
code form.
"""

import re
import secrets
import uuid

from playwright.sync_api import Page, expect

from tests_e2e.conftest import ReceiptsServer

SEED = """
import os
from datetime import timedelta
from django.utils import timezone
from apps.accounts.models import User
from apps.billing.models import CreditTransaction
from apps.core.legal import TERMS_VERSION

user = User.objects.create_user(email=os.environ['E2E_USAGE_EMAIL'],
                                password=os.environ['E2E_USAGE_PASSWORD'], name='Usage')
user.email_confirmed = True
user.terms_version = TERMS_VERSION
user.save()
noon = timezone.now().replace(hour=12, minute=0, second=0, microsecond=0)
for days_ago, amount, kind, source in [
    (1, -4, 'spend', 'file_transfer'),
    (2, -6, 'spend', 'receipts'),
    (3, 500, 'promo', 'billing'),
    (40, -20, 'spend', 'todo'),
]:
    row = CreditTransaction.objects.create(
        user=user, amount=amount, type=kind, source=source, description='e2e usage')
    CreditTransaction.objects.filter(pk=row.pk).update(created_at=noon - timedelta(days=days_ago))
"""


def _seed(server: ReceiptsServer) -> tuple[str, str]:
    email = f'usage_{uuid.uuid4().hex[:8]}@example.com'
    password = secrets.token_urlsafe(16)
    server._env['E2E_USAGE_EMAIL'] = email
    server._env['E2E_USAGE_PASSWORD'] = password
    server.shell(SEED)
    return email, password


def _sign_in(page: Page, server: ReceiptsServer, email: str, password: str) -> None:
    page.goto(f'{server.url}/account/login/')
    page.fill('input[name="email"]', email)
    page.fill('input[name="password"]', password)
    page.click('button[type="submit"]')
    page.wait_for_url(lambda url: '/account/login' not in url)


def _choose_period(page: Page, label: str) -> None:
    page.locator('#usage-range').click()
    page.get_by_role('option', name=label).click()


def test_the_report_refreshes_in_place_and_the_address_follows(
    page: Page, receipts_server: ReceiptsServer
):
    url = receipts_server.url
    _sign_in(page, receipts_server, *_seed(receipts_server))

    page.goto(f'{url}/billing/credits/')
    expect(page.get_by_role('heading', name='Credits', exact=True)).to_be_visible()
    expect(page.locator('nav[aria-label="Credits"] [aria-current="page"]')).to_have_text('Usage')
    report = page.locator('#usage-report')
    # Last 30 days, by day: the two spends in range, not the one 40 days ago; the promo is "added".
    expect(report.locator('dl > div', has_text='Credits used')).to_contain_text('10')
    expect(report.locator('dl > div', has_text='Credits added')).to_contain_text('Promo code 500')
    expect(report.get_by_role('heading', name='Credits used per day')).to_be_visible()
    expect(report.locator('svg[role="img"]')).to_be_visible()
    expect(report.locator('label.chip-toggle')).to_have_count(2)
    expect(report.locator('tfoot')).to_contain_text('10')

    # A longer period brings in the old spend, and the suggested grouping changes to weeks.
    _choose_period(page, 'Last 90 days')
    expect(report.locator('dl > div', has_text='Credits used')).to_contain_text('30')
    expect(report.get_by_role('heading', name='Credits used per week')).to_be_visible()
    expect(report.locator('label.chip-toggle')).to_have_count(3)
    expect(page).to_have_url(re.compile(r'/billing/credits/\?range=last_90_days$'))

    # Picking a grouping is remembered in the address...
    page.locator('#usage-group label', has_text='Month').click()
    expect(report.get_by_role('heading', name='Credits used per month')).to_be_visible()
    expect(page).to_have_url(re.compile(r'range=last_90_days&group=month$'))
    expect(page.locator('#usage-group [aria-current="true"]')).to_have_text('Month')

    # ...until the period changes, which gets its own suggestion again.
    _choose_period(page, 'Last 7 days')
    expect(report.get_by_role('heading', name='Credits used per day')).to_be_visible()
    expect(page).to_have_url(re.compile(r'/billing/credits/\?range=last_7_days$'))
    expect(page.locator('#usage-group [aria-current="true"]')).to_have_text('Day')
    expect(report.locator('dl > div', has_text='Credits used')).to_contain_text('10')

    # The legend chips are the per-app filter: turning Receipts off leaves File Transfer's 4.
    page.locator('label.chip-toggle', has_text='Receipts').click()
    expect(report).to_contain_text('showing 1 of 2 apps')
    expect(report.locator('dl > div', has_text='Credits used')).to_contain_text('4')
    expect(page).to_have_url(re.compile(r'range=last_7_days&app=file_transfer$'))
    expect(
        report.locator('label.chip-toggle', has_text='Receipts').locator('input')
    ).not_to_be_checked()
    # The last ticked chip cannot be unticked (nothing ticked would mean every app).
    page.locator('label.chip-toggle', has_text='File Transfer').click()
    expect(
        report.locator('label.chip-toggle', has_text='File Transfer').locator('input')
    ).to_be_checked()
    expect(page.locator('#usage-hint')).to_have_text('At least one app stays shown.')
    page.locator('label.chip-toggle', has_text='Receipts').click()
    expect(report).not_to_contain_text('showing')
    expect(report.locator('dl > div', has_text='Credits used')).to_contain_text('10')


def test_a_custom_period_and_a_grouping_that_is_too_fine(
    page: Page, receipts_server: ReceiptsServer
):
    url = receipts_server.url
    _sign_in(page, receipts_server, *_seed(receipts_server))

    page.goto(f'{url}/billing/credits/?range=all_time')
    expect(page.locator('#usage-report')).to_contain_text('Credits used')

    # Asking for a grouping that makes too many periods says so and shows the suggested one.
    page.goto(f'{url}/billing/credits/?range=custom&date_from=2000-01-01&group=day')
    alert = page.locator('#usage-report [role="alert"]')
    expect(alert).to_contain_text('too many to show')
    expect(page.locator('#usage-group [aria-current="true"]')).to_have_text('Year')

    # Custom shows two date fields.
    _choose_period(page, 'Custom')
    expect(page.locator('#usage-from')).to_be_visible()
    expect(page.locator('#usage-to')).to_be_visible()
    _choose_period(page, 'Last 30 days')
    expect(page.locator('#usage-from')).to_be_hidden()


def test_the_tabs_the_ledger_and_the_promo_form(page: Page, receipts_server: ReceiptsServer):
    url = receipts_server.url
    _sign_in(page, receipts_server, *_seed(receipts_server))

    page.goto(f'{url}/billing/credits/')
    expect(page.locator('#promo-form')).to_have_count(0)

    page.get_by_role('link', name='History').click()
    expect(page).to_have_url(re.compile(r'/billing/credits/\?tab=history$'))
    expect(page.locator('#promo-form')).to_be_visible()
    table = page.locator('main table')
    expect(table.locator('th', has_text='App')).to_be_visible()
    expect(table.locator('tbody tr', has_text='Receipts')).to_contain_text('-6')
    expect(table.locator('tbody tr', has_text='Promo code')).to_contain_text('+500')

    page.get_by_role('link', name='Usage').click()
    expect(page).to_have_url(re.compile(r'/billing/credits/$'))
    expect(page.locator('#usage-report')).to_be_visible()
