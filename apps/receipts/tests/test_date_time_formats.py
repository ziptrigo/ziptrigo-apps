"""Receipts and the date and time format (issue #245): the pages in both formats, the site's date
field in place of the native input, and the attention email in the public wording."""

import re
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from django.urls import reverse

from apps.accounts.services.display_formats import set_display_formats

from ..models import ReceiptStatus
from ..services import emails
from .factories import create_inbound_email, create_receipt, create_vendor

pytestmark = [pytest.mark.django_db, pytest.mark.unit]

BOTH = pytest.mark.parametrize(
    ('date_format', 'time_format', 'dated'),
    [('mdy', '12h', '10/25/2026'), ('dmy', '24h', '25/10/2026')],
    ids=['mdy-12h', 'dmy-24h'],
)


@pytest.fixture
def receipt(funded_user, fake_storage):
    return create_receipt(
        funded_user,
        fake_storage,
        status=ReceiptStatus.PROCESSED,
        vendor='Staples',
        amount=Decimal('42.10'),
        currency='USD',
        receipt_date=date(2026, 10, 25),
    )


def body(response) -> str:
    assert response.status_code == 200
    return re.sub(r'\s+', ' ', response.content.decode())


@BOTH
def test_the_dashboard_row_and_detail_follow_the_format(
    web, funded_user, receipt, date_format, time_format, dated
):
    set_display_formats(funded_user, date_format=date_format, time_format=time_format)

    rows = body(web.get(reverse('receipts:dashboard')))
    detail = body(web.get(reverse('receipts:detail', args=[receipt.pk])))

    assert dated in rows
    assert f'Staples</span> &middot; {dated}' not in rows  # the list has no heading
    assert f'&middot; {dated}' in detail  # the page heading
    assert f'(receipt dated {dated[:5]})' in detail


@BOTH
def test_the_receipt_date_field_shows_the_users_order_and_submits_iso(
    web, funded_user, receipt, date_format, time_format, dated
):
    set_display_formats(funded_user, date_format=date_format, time_format=time_format)

    detail = body(web.get(reverse('receipts:detail', args=[receipt.pk])))

    assert re.search(r'name="receipt_date" value="2026-10-25"', detail)
    assert 'id="id_receipt_date"' in detail and f'value="{dated}"' in detail
    assert 'type="date"' not in detail


@BOTH
def test_the_trash_vendors_and_duplicate_banner_follow_the_format(
    web, funded_user, fake_storage, receipt, date_format, time_format, dated
):
    set_display_formats(funded_user, date_format=date_format, time_format=time_format)
    vendor = create_vendor(funded_user, 'ACME', vetted=True)
    for deleted_at in (None, datetime(2026, 10, 26, tzinfo=UTC)):
        create_receipt(
            funded_user,
            fake_storage,
            status=ReceiptStatus.PROCESSED,
            vendor='ACME',
            vendor_ref=vendor,
            receipt_date=date(2026, 10, 25),
            deleted_at=deleted_at,
        )
    original = create_receipt(
        funded_user,
        fake_storage,
        status=ReceiptStatus.PROCESSED,
        vendor='Staples',
        receipt_date=date(2026, 10, 25),
        amount=Decimal('42.10'),
        currency='USD',
    )
    duplicate = create_receipt(
        funded_user,
        fake_storage,
        status=ReceiptStatus.PROCESSED,
        vendor='Staples',
        receipt_date=date(2026, 10, 25),
        possible_duplicate_of=original,
    )

    trash = body(web.get(reverse('receipts:trash')))
    vendors = body(web.get(reverse('receipts:vendors')))
    banner = body(web.get(reverse('receipts:detail', args=[duplicate.pk])))

    assert dated in trash
    assert dated in vendors
    assert f'Staples, {dated}' in banner


@BOTH
def test_the_dashboard_filter_dates_are_fields_that_submit_iso(
    web, funded_user, receipt, date_format, time_format, dated
):
    set_display_formats(funded_user, date_format=date_format, time_format=time_format)

    page = web.get(reverse('receipts:dashboard'), {'date_from': '2026-10-25'}).content.decode()

    assert re.search(
        r'<input type="hidden" id="date_from_value" name="date_from" value="2026-10-25"', page
    )
    assert 'id="date_from"' in page and f'value="{dated}"' in page
    assert 'type="date"' not in page
    assert 'change from:#date_from_value' in page


def test_the_attention_email_is_public_and_utc_whatever_the_owners_format(
    funded_user, fake_storage, sent_emails
):
    set_display_formats(funded_user, date_format='dmy', time_format='24h')
    email = create_inbound_email(
        funded_user, subject='Fwd: invoices', received_at=datetime(2026, 11, 5, 21, 5, tzinfo=UTC)
    )
    create_receipt(
        funded_user,
        fake_storage,
        inbound_email=email,
        source='email',
        status=ReceiptStatus.NEEDS_REVIEW,
        original_filename='a.pdf',
    )

    emails.send_attention_email.call(str(email.pk))

    [mail] = sent_emails
    assert '(received Nov 5, 2026, 9:05 PM UTC)' in mail['text_body']
    assert '(received Nov 5, 2026, 9:05 PM UTC)' in mail['html_body']
    assert '05/11' not in mail['text_body'] and '21:05' not in mail['text_body']
