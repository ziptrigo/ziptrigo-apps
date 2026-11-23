"""`GET /api/billing/usage` (issue #347)."""

from datetime import UTC, datetime

import pytest

from apps.accounts.tokens import CustomAccessToken
from apps.billing.models import CreditTransactionType
from apps.billing.services import add_credits

from .usage_support import ledger, make_user, refund, spend, utc

pytestmark = [pytest.mark.django_db, pytest.mark.integration]

NOW = datetime(2026, 11, 23, 12, 0, tzinfo=UTC)
URL = '/api/billing/usage'


@pytest.fixture(autouse=True)
def _today(monkeypatch):
    monkeypatch.setattr('django.utils.timezone.now', lambda: NOW)


def _get(client, user, **params):
    headers = {'HTTP_AUTHORIZATION': f'Bearer {CustomAccessToken.for_user(user)}'}
    return client.get(URL, params, **headers)


def test_a_token_is_required(client):
    assert client.get(URL).status_code == 401


def test_the_shape_and_the_numbers(client):
    user = make_user()
    spend(user, 4, 'file_transfer', utc(2026, 11, 20))
    spend(user, 6, 'receipts', utc(2026, 11, 20))
    refund(user, 3, 'receipts', utc(2026, 11, 21))
    ledger(user, 500, kind=CreditTransactionType.PROMO, source='billing', at=utc(2026, 11, 3))
    add_credits(user, 10)

    response = _get(client, user, range='last_7_days')

    assert response.status_code == 200
    body = response.json()
    assert body['date_from'] == '2026-11-17' and body['date_to'] == '2026-11-23'
    assert body['group'] == 'day'
    assert body['timezone'] == 'UTC'
    assert body['balance'] == 10
    assert body['used'] == 7 and body['refunded'] == 3
    assert body['added'] == [{'type': 'purchase', 'credits': 10}]  # the promo is outside the 7 days
    assert {a['app']: a['used'] for a in body['apps']} == {'file_transfer': 4, 'receipts': 3}
    assert body['apps'][0]['name'] in {'File Transfer', 'Receipts'}
    assert len(body['periods']) == 7
    twentieth = next(p for p in body['periods'] if p['start'] == '2026-11-20')
    assert twentieth == {
        'start': '2026-11-20',
        'label': 'Nov 20, 2026',
        'total': 10,
        'apps': {'file_transfer': 4, 'receipts': 6},
    }
    assert next(p for p in body['periods'] if p['start'] == '2026-11-21')['total'] == -3


def test_added_by_type_and_the_public_wording_whatever_the_accounts_format(client):
    from apps.accounts.services.display_formats import set_display_formats

    user = make_user()
    set_display_formats(user, date_format='dmy')
    ledger(user, 500, kind=CreditTransactionType.PROMO, source='billing', at=utc(2026, 11, 3))

    body = _get(client, user, range='this_month', group='week').json()

    assert body['added'] == [{'type': 'promo', 'credits': 500}]
    assert body['periods'][0]['label'] == 'Week of Oct 26, 2026'


def test_groupings_and_the_app_filter(client):
    user = make_user()
    spend(user, 4, 'file_transfer', utc(2026, 11, 20))
    spend(user, 6, 'receipts', utc(2026, 11, 20))
    spend(user, 1, '', utc(2026, 11, 20))

    body = _get(client, user, range='this_year', group='month', app='receipts,other').json()

    assert body['group'] == 'month'
    assert body['used'] == 7
    assert [a['app'] for a in body['apps']] == ['receipts', 'other']
    assert body['periods'][-1] == {
        'start': '2026-11-01',
        'label': 'Nov 2026',
        'total': 7,
        'apps': {'receipts': 6, 'other': 1},
    }


def test_an_app_without_usage_gives_an_empty_report(client):
    user = make_user()
    spend(user, 4, 'file_transfer', utc(2026, 11, 20))

    body = _get(client, user, app='todo').json()

    assert body['used'] == 0 and body['apps'] == []


def test_a_custom_range_and_the_zone(client):
    user = make_user(timezone='America/Los_Angeles')
    spend(user, 3, 'todo', utc(2026, 11, 10, 7, 30))  # the 9th there

    body = _get(client, user, range='custom', date_from='2026-11-09', date_to='2026-11-09').json()

    assert body['timezone'] == 'America/Los_Angeles'
    assert body['used'] == 3 and len(body['periods']) == 1


def test_only_the_callers_usage(client):
    user = make_user()
    spend(make_user(), 50, 'receipts', utc(2026, 11, 20))

    assert _get(client, user).json()['used'] == 0


@pytest.mark.parametrize(
    ('params', 'message'),
    [
        ({'range': 'forever'}, 'range must be one of'),
        ({'group': 'decade'}, 'group must be one of'),
        ({'app': 'receipts,nope'}, 'Unknown app: nope.'),
        (
            {'range': 'custom', 'date_from': '11/20/2026'},
            'date_from must be a date like 2026-11-23.',
        ),
        ({'range': 'custom', 'date_to': '20261120'}, 'date_to must be a date like 2026-11-23.'),
        ({'range': 'last_7_days', 'date_from': '2026-11-20'}, 'only be used with range=custom'),
        ({'range': 'this_year', 'group': 'day'}, 'too many to show'),
    ],
)
def test_every_refusal_is_a_400_with_a_sentence(client, params, message):
    response = _get(client, make_user(), **params)

    assert response.status_code == 400
    assert message in response.json()['detail']


def test_a_refusal_spends_no_budget(client, settings, ratelimit_clock):
    settings.RATELIMIT_ENABLE = True
    settings.RATELIMIT_RULES = {**settings.RATELIMIT_RULES, 'BILLING_USAGE_USER': (1, 60)}
    user = make_user()
    for _ in range(3):
        assert _get(client, user, range='forever').status_code == 400

    assert _get(client, user).status_code == 200


def test_the_rate_limit(client, settings, ratelimit_clock):
    settings.RATELIMIT_ENABLE = True
    settings.RATELIMIT_RULES = {**settings.RATELIMIT_RULES, 'BILLING_USAGE_USER': (2, 60)}
    user = make_user()
    assert _get(client, user).status_code == 200
    assert _get(client, user).status_code == 200

    response = _get(client, user)

    assert response.status_code == 429
    assert 'Retry-After' in response
