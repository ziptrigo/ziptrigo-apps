"""`GET /api/receipts/reports` (`api/reports.py`): the same report as the page, strictly parsed."""

from datetime import date

import pytest
from django.utils import timezone

from ..models import ReceiptStatus
from .api_support import auth, get, limit_rule, other_auth  # noqa: F401 -- fixtures by name
from .factories import create_receipt, create_vendor

pytestmark = [pytest.mark.django_db, pytest.mark.unit, pytest.mark.usefixtures('ratelimit_clock')]

IN_YEAR = date(timezone.localdate().year, 2, 3)


@pytest.fixture
def make(funded_user, fake_storage):
    def _make(**fields):
        fields.setdefault('status', ReceiptStatus.PROCESSED)
        fields.setdefault('currency', 'USD')
        fields.setdefault('receipt_date', IN_YEAR)
        return create_receipt(funded_user, fake_storage, **fields)

    return _make


class TestReport:
    def test_it_needs_a_token(self, client):
        assert client.get('/api/receipts/reports').status_code == 401

    def test_it_returns_rows_and_a_total_per_currency(self, client, auth, funded_user, make):  # noqa: F811
        acme = create_vendor(funded_user, 'Acme', vetted=True)
        make(vendor_ref=acme, vendor='Acme', amount='30.00')
        make(vendor_ref=acme, vendor='Acme', amount='10.00')
        make(vendor_ref=acme, vendor='Acme', amount='5.00', currency='EUR')

        response = get(client, '/reports', auth)
        body = response.json()

        assert response.status_code == 200
        assert body['group'] == 'vendor' and body['processed_only'] is True
        assert body['date_from'] == f'{IN_YEAR.year}-01-01'
        assert body['receipt_count'] == 3
        usd = next(r for r in body['rows'] if r['currency'] == 'USD')
        assert usd == {
            'id': acme.pk,
            'name': 'Acme',
            'vetted': True,
            'currency': 'USD',
            'receipt_count': 2,
            'total': '40.00',
            'share': '100.0',
            'average': '20.00',
            'last_receipt_date': IN_YEAR.isoformat(),
        }
        assert [(t['currency'], t['total']) for t in body['totals']] == [
            ('EUR', '5.00'),
            ('USD', '40.00'),
        ]
        assert body['description'].startswith('Receipts dated ')

    def test_the_filters(self, client, auth, funded_user, make):  # noqa: F811
        new = create_vendor(funded_user, 'New')
        make(vendor_ref=new, vendor='New', amount='1.00')
        make(vendor_ref=new, vendor='New', amount='2.00', status=ReceiptStatus.NEEDS_REVIEW)
        make(amount='4.00', receipt_date=date(2001, 1, 1))

        assert get(client, '/reports', auth).json()['receipt_count'] == 1
        loose = get(client, '/reports', auth, processed_only='false').json()
        assert loose['receipt_count'] == 2
        assert get(client, '/reports', auth, vetted_only='true').json()['rows'] == []
        old = get(client, '/reports', auth, date_range='any').json()
        assert old['receipt_count'] == 2 and old['date_from'] is None
        custom = get(client, '/reports', auth, date_from='2001-01-01', date_to='2001-12-31').json()
        assert custom['receipt_count'] == 1
        categories = get(client, '/reports', auth, group='category').json()
        assert categories['rows'][0]['name'] == 'Uncategorized'
        assert categories['rows'][0]['vetted'] is None

    @pytest.mark.parametrize(
        'query',
        [
            {'group': 'color'},
            {'date_range': 'next_week'},
            {'date_from': 'yesterday'},
            {'date_range': 'this_year', 'date_from': '2026-01-01'},
            {'date_from': '2026-03-01', 'date_to': '2026-01-01'},
        ],
    )
    def test_bad_parameters_are_a_400_that_spends_no_budget(
        self,
        client,
        auth,
        settings,
        query,  # noqa: F811
    ):
        limit_rule(settings, 'RECEIPTS_REPORT_USER', 1)

        assert get(client, '/reports', auth, **query).status_code == 400
        assert get(client, '/reports', auth).status_code == 200

    def test_it_is_rate_limited(self, client, auth, settings):  # noqa: F811
        limit_rule(settings, 'RECEIPTS_REPORT_USER', 1)

        assert get(client, '/reports', auth).status_code == 200
        limited = get(client, '/reports', auth)

        assert limited.status_code == 429 and limited['Retry-After']

    def test_another_users_receipts_are_not_in_it(
        self,
        client,
        other_auth,
        make,  # noqa: F811
    ):
        make(amount='99.00')

        assert get(client, '/reports', other_auth).json()['receipt_count'] == 0
