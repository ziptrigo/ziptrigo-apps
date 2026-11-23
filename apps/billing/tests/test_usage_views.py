"""The Credits page: its tabs and the Usage tab (issue #347)."""

import re
from datetime import UTC, datetime
from unittest import mock

import pytest
from django.urls import reverse

from apps.accounts.services.display_formats import set_display_formats
from apps.billing.models import CreditTransactionType
from apps.billing.services import credits_url, usage

from .usage_support import ledger, make_user, refund, spend, utc

pytestmark = [pytest.mark.django_db, pytest.mark.integration]

NOW = datetime(2026, 11, 23, 12, 0, tzinfo=UTC)  # a Monday
PAGE = reverse('billing:credits-history')
FRAGMENT = reverse('billing:credits-usage')
HTMX = {'HTTP_HX_REQUEST': 'true'}


@pytest.fixture(autouse=True)
def _today(monkeypatch):
    monkeypatch.setattr('django.utils.timezone.now', lambda: NOW)


@pytest.fixture
def signed_in(client, user):
    client.force_login(user)
    return client


def _text(response) -> str:
    return re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', ' ', response.content.decode()))


# -- login --


def test_both_addresses_need_a_session(client):
    assert client.get(PAGE).status_code == 302
    assert 'login' in client.get(FRAGMENT, **HTMX)['HX-Redirect']  # htmx is sent to sign in


# -- tabs --


def test_the_page_opens_on_usage_with_two_tabs(signed_in):
    body = signed_in.get(PAGE).content.decode()

    assert '<h1 class="text-2xl font-semibold">Credits</h1>' in body
    assert re.search(r'href="/billing/credits/"\s+aria-current="page"[^>]*>Usage</a>', body)
    assert re.search(r'href="/billing/credits/\?tab=history"\s*class="[^"]*">History</a>', body)
    assert 'Credits used' in body
    assert 'id="promo-form"' not in body  # the promo form is on History


def test_the_history_tab_has_the_ledger_and_the_promo_form(signed_in, user):
    spend(user, 3, 'receipts', NOW)

    body = signed_in.get(PAGE, {'tab': 'history'}).content.decode()

    assert re.search(r'href="/billing/credits/\?tab=history"\s+aria-current="page"', body)
    assert 'id="promo-form"' in body
    assert 'Have a promo code?' in body
    assert 'Credits used' not in body


def test_an_unknown_tab_is_usage(signed_in):
    body = signed_in.get(PAGE, {'tab': 'nope'}).content.decode()

    assert 'Credits used' in body
    assert 'id="promo-form"' not in body


def test_the_history_pager_keeps_the_tab(signed_in, user):
    for index in range(30):
        ledger(user, 1, description=f'row {index}')

    body = signed_in.get(PAGE, {'tab': 'history'}).content.decode()

    assert 'href="?tab=history&amp;page=2"' in body
    page_two = signed_in.get(PAGE, {'tab': 'history', 'page': 2}).content.decode()
    assert 'href="?tab=history&amp;page=1"' in page_two


def test_the_history_has_an_app_column(signed_in, user):
    spend(user, 3, 'receipts', NOW)
    ledger(user, 500, kind=CreditTransactionType.PROMO, source='billing', description='Promo X')
    ledger(user, 7, description='Plain top up')

    body = signed_in.get(PAGE, {'tab': 'history'}).content.decode()

    assert '<th class="py-2 pr-4">App</th>' in body
    assert 'Receipts' in body
    # Ledger rows with no registered app show a dash, never the raw source label.
    assert body.count('aria-label="No app"') == 2
    assert '>billing<' not in body


def test_the_costs_page_and_the_account_page_link_to_the_usage_tab(signed_in):
    assert 'href="/billing/credits/"' in signed_in.get(reverse('billing:costs')).content.decode()
    account = signed_in.get(reverse('accounts:account')).content.decode()
    assert re.search(r'href="/billing/credits/"[^>]*>\s*Credits\s*</a>', account)
    assert 'Usage History' not in account


# -- the report --


def test_the_default_is_the_last_30_days_by_day(signed_in, user):
    spend(user, 4, 'file_transfer', utc(2026, 11, 20))
    spend(user, 6, 'receipts', utc(2026, 11, 20))
    refund(user, 3, 'receipts', utc(2026, 11, 21))
    ledger(user, 500, kind=CreditTransactionType.PROMO, source='billing', at=utc(2026, 11, 3))
    spend(user, 99, 'receipts', utc(2026, 9, 1))  # outside

    response = signed_in.get(PAGE)
    text = _text(response)

    assert 'From 10/25/2026 to 11/23/2026' in text
    assert 'Credits used 7' in text
    assert 'Most: Receipts (3) · 3 refunded, already subtracted' not in text  # file transfer is top
    assert 'Most: File Transfer (4) · 3 refunded, already subtracted' in text
    assert 'Credits added +500 Promo code 500' in text
    assert 'Average per day' in text
    assert 'over 30 days' in text
    assert 'Credits used per day' in text
    assert response.context['group'] == 'day'


def test_the_chart_is_an_svg_with_a_sentence_and_a_title_per_segment(signed_in, user):
    spend(user, 4, 'file_transfer', utc(2026, 11, 20))
    spend(user, 6, 'receipts', utc(2026, 11, 20))

    body = signed_in.get(PAGE, {'group': 'day'}).content.decode()

    assert '<svg viewBox="0 0 600 192"' in body
    assert 'role="img"' in body
    assert (
        'aria-label="Credits used per day from 10/25/2026 to 11/23/2026: 10 in total, '
        'at most 10 in a day. The table below has every number."'
    ) in body
    assert '<title>11/20/2026 · File Transfer: 4</title>' in body
    assert '<title>11/20/2026 · Receipts: 6</title>' in body
    slot = usage.registry()['file_transfer'][2]
    assert f'style="--chart: var(--c-chart-{slot})"' in body
    assert 'stroke-gray-200' in body  # the gridlines


def test_a_refund_makes_a_negative_period_that_the_chart_draws_as_nothing(signed_in, user):
    spend(user, 5, 'receipts', utc(2026, 11, 20))
    refund(user, 9, 'receipts', utc(2026, 11, 22))

    response = signed_in.get(PAGE, {'group': 'day'})
    body = response.content.decode()

    assert re.search(r'11/22/2026</th>\s*<td[^>]*text-green-700[^>]*>-9</td>', body)
    assert '<title>11/22/2026' not in body
    assert 'Credits used -4' in _text(response)


def test_the_table_is_newest_first_with_a_total_row_and_dashes(signed_in, user):
    spend(user, 4, 'file_transfer', utc(2026, 11, 20))
    spend(user, 6, 'receipts', utc(2026, 11, 21))

    body = signed_in.get(PAGE, {'group': 'day'}).content.decode()

    assert body.index('11/21/2026</th>') < body.index('11/20/2026</th>')
    assert re.search(r'11/21/2026</th>\s*<td[^>]*>\s*–\s*</td>\s*<td[^>]*>6</td>', body)
    assert '<th scope="row" class="py-2 pr-3 text-left">Total</th>' in body
    assert 'Credits used per day and app' in body  # the caption


def test_thousands_are_grouped(signed_in, user):
    spend(user, 1284, 'receipts', utc(2026, 11, 20))

    assert 'Credits used 1,284' in _text(signed_in.get(PAGE))


def test_the_period_select_and_dates(signed_in, user):
    spend(user, 2, 'todo', utc(2026, 9, 5))
    spend(user, 3, 'todo', utc(2026, 9, 25))

    response = signed_in.get(
        PAGE, {'range': 'custom', 'date_from': '2026-09-01', 'date_to': '2026-09-30'}
    )
    body = response.content.decode()

    assert 'From 09/01/2026 to 09/30/2026' in _text(response)
    assert 'Credits used 5' in _text(response)
    assert 'name="range" value="custom"' in body
    assert 'name="date_from" value="2026-09-01"' in body
    assert 'name="date_to" value="2026-09-30"' in body


def test_other_periods(signed_in, user):
    spend(user, 2, 'todo', utc(2026, 10, 15))

    last_month = signed_in.get(PAGE, {'range': 'last_month'})
    assert 'From 10/01/2026 to 10/31/2026' in _text(last_month)
    assert 'Credits used 2' in _text(last_month)
    this_year = signed_in.get(PAGE, {'range': 'this_year'})
    assert 'From 01/01/2026 to 11/23/2026' in _text(this_year)
    assert this_year.context['group'] == 'month'
    assert signed_in.get(PAGE, {'range': 'all_time'}).context['first_text'] == '10/15/2026'


def test_group_by_marks_the_chosen_option(signed_in):
    body = signed_in.get(PAGE, {'group': 'month'}).content.decode()

    assert re.search(
        r'aria-current="true">\s*<input type="radio" name="group" value="month"[^>]* checked>Month',
        body,
    )
    assert body.count('aria-current="true"') == 1


def test_parsing_is_lenient(signed_in, user):
    spend(user, 2, 'todo', utc(2026, 11, 15))

    response = signed_in.get(
        PAGE,
        {
            'range': 'bogus',
            'group': 'decade',
            'date_from': 'x',
            'date_to': '20261101',
            'app': ['??'],
        },
    )

    assert response.status_code == 200
    assert 'From 10/25/2026 to 11/23/2026' in _text(response)
    assert 'Credits used 2' in _text(response)
    assert signed_in.get(PAGE, {'range': 'custom', 'date_from': 'garbage'}).status_code == 200


def test_a_grouping_over_120_periods_shows_a_line_and_uses_the_suggested_one(signed_in, user):
    spend(user, 2, 'todo', utc(2026, 11, 15))

    response = signed_in.get(PAGE, {'range': 'this_year', 'group': 'day'})
    body = response.content.decode()

    assert response.status_code == 200
    assert re.search(r'<p role="alert"[^>]*>That is 327 days, too many to show\.', body)
    assert response.context['group'] == 'month'
    assert 'Credits used per month' in body


def test_a_valid_choice_shows_no_refusal(signed_in):
    assert 'role="alert"' not in signed_in.get(PAGE, {'group': 'day'}).content.decode()


def test_nothing_spent_shows_the_empty_state_with_the_tiles(signed_in, user):
    ledger(user, 1000, kind=CreditTransactionType.WELCOME, source='billing', at=utc(2026, 11, 20))

    response = signed_in.get(PAGE, {'range': 'last_7_days'})
    text = _text(response)

    assert 'No credits used from 11/17/2026 to 11/23/2026' in text
    assert 'Credits added +1,000 Welcome credits 1,000' in text
    assert 'href="/billing/costs/"' in response.content.decode()
    assert '<svg' not in response.content.decode()


def test_the_app_filter_applies_to_tiles_chart_and_table(signed_in, user):
    spend(user, 4, 'file_transfer', utc(2026, 11, 20))
    spend(user, 6, 'receipts', utc(2026, 11, 20))

    response = signed_in.get(PAGE, {'group': 'day', 'app': 'receipts'})
    body = response.content.decode()
    text = _text(response)

    assert 'showing 1 of 2 apps' in text
    assert 'Credits used 6' in text
    assert '<title>11/20/2026 · Receipts: 6</title>' in body
    assert 'File Transfer: 4' not in body
    assert re.search(r'name="app" value="receipts" class="peer sr-only" checked', body)
    assert re.search(r'name="app" value="file_transfer" class="peer sr-only" >', body)
    # The legend still lists the app that is left out, so it can be turned on again.
    assert 'File Transfer' in body


def test_a_filter_for_an_app_with_no_usage_is_ignored(signed_in, user):
    spend(user, 4, 'file_transfer', utc(2026, 11, 20))

    response = signed_in.get(PAGE, {'app': 'receipts'})

    assert 'Credits used 4' in _text(response)


def test_the_filters_are_a_get_form_that_refreshes_the_report(signed_in):
    body = signed_in.get(PAGE).content.decode()

    assert 'id="usage-filters"' in body
    assert 'method="get"' in body
    assert f'hx-get="{FRAGMENT}"' in body
    assert 'hx-target="#usage-report"' in body
    assert '<noscript><button type="submit"' in body
    assert 'id="usage-report"' in body


def test_dates_follow_the_accounts_format(signed_in, user):
    set_display_formats(user, date_format='dmy')
    spend(user, 4, 'file_transfer', utc(2026, 11, 20))

    response = signed_in.get(PAGE, {'group': 'day'})
    text = _text(response)

    assert 'From 25/10/2026 to 23/11/2026' in text
    assert re.search(r'20/11/2026</th>', response.content.decode())
    assert 'Credits used per day' in text


def test_days_are_the_accounts_own_and_the_footnote_says_so(signed_in, user):
    user.timezone = 'America/Los_Angeles'
    user.save(update_fields=['timezone'])
    spend(user, 3, 'todo', utc(2026, 11, 21, 7, 30))  # 23:30 on the 20th there

    response = signed_in.get(PAGE, {'group': 'day'})
    body = response.content.decode()

    assert re.search(r'11/20/2026</th>\s*<td[^>]*>3</td>', body)
    assert 'Days are in your time zone (America/Los_Angeles). Weeks start on Monday.' in _text(
        response
    )


def test_an_unset_zone_reads_utc(signed_in):
    assert 'Days are in your time zone (UTC).' in _text(signed_in.get(PAGE))


def test_another_users_usage_is_never_shown(signed_in, user):

    spend(make_user(), 50, 'receipts', utc(2026, 11, 20))

    assert 'No credits used' in _text(signed_in.get(PAGE))


def test_the_page_makes_a_bounded_number_of_queries(signed_in, user, django_assert_max_num_queries):
    for index in range(1, 20):
        spend(user, index, 'receipts', utc(2026, 11, index))
    with django_assert_max_num_queries(14):
        assert signed_in.get(PAGE, {'group': 'day'}).status_code == 200


# -- the htmx fragment --


def test_the_fragment_answers_the_report_and_pushes_the_pages_address(signed_in, user):
    spend(user, 4, 'file_transfer', utc(2026, 11, 20))

    response = signed_in.get(
        FRAGMENT, {'range': 'last_90_days', 'group': 'month', 'app': 'file_transfer'}, **HTMX
    )
    body = response.content.decode()

    assert response.status_code == 200
    assert '<html' not in body  # only the fragment
    assert 'Credits used' in body
    assert response['HX-Push-Url'] == '/billing/credits/?range=last_90_days&group=month'
    # The Group by control comes along out of band (the period decides which one is suggested).
    assert 'id="usage-group" hx-swap-oob="true"' in body


def test_the_pushed_address_keeps_a_partial_app_filter_and_drops_what_is_default(signed_in, user):
    spend(user, 4, 'file_transfer', utc(2026, 11, 20))
    spend(user, 6, 'receipts', utc(2026, 11, 20))

    response = signed_in.get(
        FRAGMENT, {'range': 'last_30_days', 'group': 'day', 'app': 'receipts'}, **HTMX
    )

    assert response['HX-Push-Url'] == '/billing/credits/?app=receipts'


def test_a_custom_range_is_in_the_pushed_address(signed_in):
    response = signed_in.get(
        FRAGMENT, {'range': 'custom', 'date_from': '2026-09-01', 'date_to': '2026-09-30'}, **HTMX
    )

    assert (
        response['HX-Push-Url']
        == '/billing/credits/?range=custom&date_from=2026-09-01&date_to=2026-09-30'
    )


def test_a_refused_grouping_is_not_in_the_pushed_address(signed_in):
    response = signed_in.get(FRAGMENT, {'range': 'this_year', 'group': 'day'}, **HTMX)

    assert 'too many to show' in response.content.decode()
    assert response['HX-Push-Url'] == '/billing/credits/?range=this_year'


def test_opened_directly_the_fragment_redirects_to_the_page(signed_in):
    response = signed_in.get(FRAGMENT, {'range': 'last_7_days', 'app': 'todo'})

    assert response.status_code == 302
    assert response['Location'] == '/billing/credits/?range=last_7_days&app=todo'


def test_the_fragment_is_rate_limited_per_user(signed_in, settings, ratelimit_clock):
    settings.RATELIMIT_ENABLE = True
    settings.RATELIMIT_RULES = {**settings.RATELIMIT_RULES, 'BILLING_USAGE_USER': (2, 60)}
    for _ in range(2):
        assert signed_in.get(FRAGMENT, **HTMX).status_code == 200

    response = signed_in.get(FRAGMENT, **HTMX)

    assert response.status_code == 429
    assert response['HX-Retarget'] == '#usage-msg'
    assert 'Retry-After' in response


def test_a_redirected_direct_visit_spends_no_budget(signed_in, settings, ratelimit_clock):
    settings.RATELIMIT_ENABLE = True
    settings.RATELIMIT_RULES = {**settings.RATELIMIT_RULES, 'BILLING_USAGE_USER': (1, 60)}
    for _ in range(3):
        assert signed_in.get(FRAGMENT).status_code == 302

    assert signed_in.get(FRAGMENT, **HTMX).status_code == 200


# -- the "Top up" links --


def test_credits_url_builds_the_two_tabs():
    assert credits_url('usage') == '/billing/credits/'
    assert credits_url('history') == '/billing/credits/?tab=history'
    assert credits_url('history', page=2) == '/billing/credits/?tab=history&page=2'
    with mock.patch('django.conf.settings.BASE_URL', 'https://app.example.com/'):
        assert credits_url('history', absolute=True) == (
            'https://app.example.com/billing/credits/?tab=history'
        )
    with pytest.raises(ValueError):
        credits_url('elsewhere')


def test_the_template_tag_builds_the_same_address():
    from django.template import Context, Template

    html = Template(
        "{% load billing_ui %}{% credits_url 'history' %}|{% credits_url 'usage' %}"
    ).render(Context())

    assert html == '/billing/credits/?tab=history|/billing/credits/'


def test_a_top_up_link_lands_on_the_history_tab_with_the_promo_form(signed_in, user):
    from apps.receipts.services import get_account

    account = get_account(user)
    account.frozen_at = NOW
    account.save(update_fields=['frozen_at'])

    page = signed_in.get(reverse('receipts:dashboard')).content.decode()
    match = re.search(r'href="([^"]+)"[^>]*>\s*Top up\s*</a>', page)
    assert match
    href = match.group(1)

    assert href == '/billing/credits/?tab=history'
    assert 'id="promo-form"' in signed_in.get(href).content.decode()


def test_redeeming_returns_to_the_history_tab(signed_in, user):
    from apps.billing.models import PromoCode

    PromoCode.objects.create(code='USAGE347', credits=10)
    page = signed_in.get(credits_url('history')).content.decode()
    assert 'name="next" value="/billing/credits/?tab=history"' in page

    response = signed_in.post(
        reverse('billing:redeem'), {'code': 'USAGE347', 'next': '/billing/credits/?tab=history'}
    )

    assert response.status_code == 302
    assert response['Location'] == '/billing/credits/?tab=history'
