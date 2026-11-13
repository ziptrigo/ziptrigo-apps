"""The Costs page (`billing:costs`): every app's prices, read live from the settings singletons."""

import dataclasses
import re
from datetime import date, timedelta
from decimal import Decimal

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.accounts.services.timezones import user_today
from apps.billing.models import BillingSettings
from apps.billing.views.costs import storage_example
from apps.core import products as product_registry
from apps.core.costs import charge, credits_text, daily_rate_text, plural, storage_usage
from apps.core.datetime_format import format_date, formats_for
from apps.core.products import get_products
from apps.file_transfer.models import Transfer, TransferStatus
from apps.notes.models import Note, NotesSettings
from apps.receipts.models import ReceiptsAccount, ReceiptsSettings
from apps.short_url.models import ShortUrlSettings
from apps.todo.models import TodoAccount, TodoSettings
from apps.todo.services.accounts import get_account as get_todo_account
from apps.todo.services.billing import month_key

pytestmark = [pytest.mark.django_db, pytest.mark.unit]

COSTS = reverse('billing:costs')

#: Measured at 18 (session and user, six settings rows, the balance, and each product's usage
#: counts); a little headroom, but a provider that starts querying per row breaks it.
MAX_QUERIES = 20


def _text(response) -> str:
    """The page's text with tags dropped and whitespace collapsed."""
    html = response.content.decode()
    return ' '.join(re.sub(r'<[^>]+>', ' ', html).split())


@pytest.fixture
def page(client, user):
    client.force_login(user)

    def get():
        response = client.get(COSTS)
        assert response.status_code == 200
        return response

    return get


class TestHelpers:
    def test_credits_text(self):
        assert credits_text(1) == '1 credit'
        assert credits_text(10) == '10 credits'
        assert credits_text(1000) == '1,000 credits'
        assert credits_text(Decimal('0.1000')) == '0.1 credits'
        assert credits_text(Decimal('6.00')) == '6 credits'
        assert credits_text(Decimal('1.0')) == '1 credit'
        assert credits_text(Decimal('1234.5')) == '1,234.5 credits'
        assert credits_text(Decimal('1E+2')) == '100 credits'

    def test_a_price_of_zero_is_free(self):
        line = charge('Password', 0, 'once per link')
        assert line.free and line.cost == 'Free' and line.unit == ''
        assert charge('Password', 2, 'once per link').cost == '2 credits'

    def test_daily_rate_and_storage_usage(self):
        assert daily_rate_text(Decimal(0)) == '0 credits a day'
        assert daily_rate_text(Decimal('0.004')) == 'less than 0.01 credits a day'
        assert daily_rate_text(Decimal('2.5')) == 'about 2.5 credits a day'
        assert storage_usage(10 * 1024**3, Decimal('0.1')) == (
            'You store 10.0\xa0GB: about 1 credit a day.'
        )

    def test_storage_example(self):
        assert storage_example(Decimal('0.1')) == '2 GB kept for 30 days costs 6 credits.'
        assert storage_example(Decimal('0.0123')) == '2 GB kept for 30 days costs 0.74 credits.'
        # A price that rounds to 0 over the example is not shown as costing nothing.
        assert storage_example(Decimal('0.00005')) == (
            '2 GB kept for 30 days costs less than 0.01 credits.'
        )

    def test_plural(self):
        assert plural(1, 'note') == '1 note'
        assert plural(2, 'note') == '2 notes'


def test_requires_login(client):
    response = client.get(COSTS)
    assert response.status_code == 302
    assert reverse('accounts:login') in response['Location']


def test_account_page_links_to_it(client, user):
    client.force_login(user)
    html = client.get(reverse('accounts:account')).content.decode()
    assert f'href="{COSTS}"' in html
    assert '>Costs' in html.replace('</i>', '>')


def test_every_product_has_a_cost_section():
    missing = [product.label for product in get_products() if product.costs is None]
    assert missing == []


def test_sections_follow_the_users_order_and_include_hidden_apps(page, user):
    labels = [product.label for product in get_products()]
    user.app_order = list(reversed(labels))
    user.hidden_apps = [labels[0]]
    user.save()
    html = page().content.decode()
    found = re.findall(r'<section id="costs-([a-z_]+)"', html)
    assert found == ['shared', *reversed(labels)]


def test_shared_storage_price_and_example(page):
    text = _text(page())
    assert '0.1 credits per GB per day' in text
    assert 'Example: 2 GB kept for 30 days costs 6 credits.' in text
    assert 'New accounts 1,000 credits once the email address is confirmed.' in text

    config = BillingSettings.load()
    config.storage_price_per_gb_per_day = Decimal('0.25')
    config.welcome_credits = 0
    config.save()
    text = _text(page())
    assert '0.25 credits per GB per day' in text
    assert 'costs 15 credits.' in text
    assert 'New accounts' not in text


def test_prices_are_read_live(page):
    receipts = ReceiptsSettings.load()
    receipts.price_per_file = 3
    receipts.save()
    short = ShortUrlSettings.load()
    short.password_price = 0
    short.free_active_links = 40
    short.save()
    text = _text(page())
    assert 'Read a receipt with AI' in text
    assert '3 credits per file' in text
    assert 'Password Free' in text
    assert 'Each active link beyond 40' in text
    assert '0 of your 40 free active links are in use.' in text


def test_todo_usage_says_when_the_allowance_starts_again(page, user):
    account = get_todo_account(user)
    account.month_key = month_key(user)
    account.month_task_count = 4
    account.save()
    today = user_today(user)
    first = (
        date(today.year + 1, 1, 1) if today.month == 12 else date(today.year, today.month + 1, 1)
    )
    text = _text(page())
    config = TodoSettings.load()
    assert f'This month: 4 of {config.free_tasks_per_month} free tasks used.' in text
    assert f'The allowance starts again on {format_date(first, "full", formats_for(user))}.' in text


def test_todo_count_of_a_past_month_is_not_used(page, user):
    account = get_todo_account(user)
    account.month_key = '2000-01'
    account.month_task_count = 9
    account.save()
    assert 'This month: 0 of' in _text(page())


def test_notes_usage_lists_free_notes_renewals_and_locks(page, user):
    now = timezone.now()
    Note.objects.create(user=user, title='Free one', is_free=True)
    Note.objects.create(user=user, title='Paid', paid_through=now + timedelta(days=40))
    Note.objects.create(user=user, title='Later', paid_through=now + timedelta(days=200))
    Note.objects.create(user=user, title='Locked', paid_through=now, locked_at=now)
    Note.objects.create(user=user, title='Trashed', is_free=True, deleted_at=now)
    config = NotesSettings.load()
    text = _text(page())
    assert f'You have 1 of {config.free_notes} free notes.' in text
    assert (
        f'2 paid notes renew every {config.renewal_days} days; the next renewal is on '
        f'{format_date(now + timedelta(days=40), "full", formats_for(user))} for '
        f'{credits_text(config.renewal_price)}.'
    ) in text
    assert '1 note is locked until the renewal is paid.' in text


def test_storage_usage_lines(page, user):
    ReceiptsAccount.objects.create(user=user, stored_bytes=5 * 1024**3, frozen_at=timezone.now())
    text = _text(page())
    assert 'You store 5.0 GB: about 0.5 credits a day.' in text
    assert 'Storage is paused until you top up.' in text
    assert 'You have no active transfers, so nothing is charged for them.' in text


def test_opening_the_page_creates_no_product_account(page, user):
    page()
    assert not TodoAccount.objects.filter(user=user).exists()
    assert not ReceiptsAccount.objects.filter(user=user).exists()


def _section_html(html: str, label: str) -> str:
    start = html.index(f'<section id="costs-{label}"')
    return html[start : html.index('</section>', start)]


def test_storage_lines_link_to_the_shared_price(page):
    html = page().content.decode()
    for label in ('file_transfer', 'receipts', 'todo'):
        section = _section_html(html, label)
        assert 'href="#costs-shared"' in section, label
        assert 'Storage' in section
    for label in ('qr_code', 'short_url', 'notes'):
        assert 'href="#costs-shared"' not in _section_html(html, label), label


def test_file_transfer_shows_the_accrued_rate_not_the_one_credit_minimum(page, user):
    # 50 MB at 0.1 credits per GB per day accrues about 0.005 credits a day: the daily job spends
    # only whole credits, so it is not charged 1 credit a day.
    for status, size in (
        (TransferStatus.ACTIVE, 30 * 1024**2),
        (TransferStatus.ACTIVE, 20 * 1024**2),
        (TransferStatus.SUSPENDED, 10 * 1024**3),
        (TransferStatus.DISABLED, 10 * 1024**3),
    ):
        Transfer.objects.create(owner=user, status=status, size_bytes=size)
    text = _text(page())
    assert 'You have 2 active transfers (50.0 MB): less than 0.01 credits a day.' in text
    assert '1 transfer is suspended until you top up.' in text
    assert 'at least 1 credit a day' not in text
    assert 'once your balance covers a day of its storage (at least 1 credit)' in text


def test_file_transfer_rate_for_a_large_transfer(page, user):
    Transfer.objects.create(owner=user, status=TransferStatus.ACTIVE, size_bytes=25 * 1024**3)
    assert 'You have 1 active transfer (25.0 GB): about 2.5 credits a day.' in _text(page())


def test_each_charge_says_whether_it_is_confirmed_first(page):
    html = page().content.decode()
    text = _text(page())
    assert 'each one says below whether you confirm its cost first' in text
    assert 'Charged when the task is added.' in _section_html(html, 'todo')
    assert 'Charged when a file is uploaded or emailed in.' in _section_html(html, 'receipts')
    assert 'You confirm the cost first.' in _section_html(html, 'receipts')
    assert 'You confirm the cost first.' in _section_html(html, 'notes')
    assert 'Taken automatically on the renewal date.' in _section_html(html, 'notes')
    assert 'You confirm the cost of a paid extra first.' in _section_html(html, 'short_url')


def test_a_failing_section_does_not_take_the_page_down(page, monkeypatch, caplog):
    def broken(user):
        raise RuntimeError('boom')

    receipts = product_registry._registry['receipts']
    monkeypatch.setitem(
        product_registry._registry, 'receipts', dataclasses.replace(receipts, costs=broken)
    )
    response = page()
    html = response.content.decode()
    assert 'Prices for Receipts are not available right now.' in _section_html(html, 'receipts')
    assert 'Read a receipt with AI' not in html
    assert 'Each task beyond' in html  # the other sections still render
    assert 'Costs section of receipts failed' in caplog.text


def test_query_count_does_not_grow_with_the_users_data(page, user, django_assert_max_num_queries):
    page()  # warm the singletons' rows (created on first load)
    with django_assert_max_num_queries(MAX_QUERIES):
        page()
    now = timezone.now()
    for i in range(30):
        Note.objects.create(user=user, title=f'n{i}', paid_through=now + timedelta(days=i))
        Transfer.objects.create(owner=user, status=TransferStatus.ACTIVE, size_bytes=1024)
    with django_assert_max_num_queries(MAX_QUERIES):
        page()
