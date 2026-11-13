"""The Costs page (`billing:costs`): every app's prices, read live from the settings singletons."""

import re
from datetime import date, timedelta
from decimal import Decimal

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.accounts.services.timezones import user_today
from apps.billing.models import BillingSettings
from apps.core.costs import charge, credits_text, daily_rate_text, plural, storage_usage
from apps.core.datetime_format import format_date, formats_for
from apps.core.products import get_products
from apps.notes.models import Note, NotesSettings
from apps.receipts.models import ReceiptsAccount, ReceiptsSettings
from apps.short_url.models import ShortUrlSettings
from apps.todo.models import TodoAccount, TodoSettings
from apps.todo.services.accounts import get_account as get_todo_account
from apps.todo.services.billing import month_key

pytestmark = [pytest.mark.django_db, pytest.mark.unit]

COSTS = reverse('billing:costs')


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


def test_storage_lines_link_to_the_shared_price(page):
    html = page().content.decode()
    assert (
        html.count('href="#costs-shared"') >= 4
    )  # the jump link, and File Transfer, Receipts, To-do
