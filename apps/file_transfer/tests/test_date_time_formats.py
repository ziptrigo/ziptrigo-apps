"""File transfer and the date and time format (issue #245): the owner's pages follow the account's
format, the public pages and every email are the fixed public wording."""

import re
from datetime import UTC, datetime
from unittest import mock

import pytest
from django.urls import reverse

from apps.accounts.services.display_formats import set_display_formats

from ..models import Transfer, TransferFile, TransferStatus
from ..services import emails

pytestmark = [pytest.mark.django_db, pytest.mark.unit]

EXPIRES = datetime(2030, 11, 5, 21, 5, tzinfo=UTC)


@pytest.fixture
def transfer(funded_user) -> Transfer:
    transfer = Transfer.objects.create(
        owner=funded_user,
        status=TransferStatus.ACTIVE,
        expires_at=EXPIRES,
        completed_at=datetime(2030, 11, 1, 8, 30, tzinfo=UTC),
    )
    TransferFile.objects.create(
        transfer=transfer, name='a.txt', size=10, storage_key='k/a.txt', uploaded=True
    )
    return transfer


def flat(response) -> str:
    assert response.status_code == 200
    return re.sub(r'\s+', ' ', response.content.decode())


@pytest.mark.parametrize(
    ('date_format', 'time_format', 'expires', 'sent'),
    [
        ('mdy', '12h', '11/05/2030, 9:05 PM UTC', '2030-11-01 8:30 AM UTC'),
        ('dmy', '24h', '05/11/2030, 21:05 UTC', '2030-11-01 08:30 UTC'),
    ],
)
def test_the_dashboard_row_follows_the_users_format(
    client, funded_user, transfer, date_format, time_format, expires, sent
):
    set_display_formats(funded_user, date_format=date_format, time_format=time_format)
    client.force_login(funded_user)

    body = flat(client.get(reverse('file_transfer:dashboard')))

    # The server renders the UTC fallback in the same wording the script then localises.
    assert f'>{expires}</time>' in body
    assert f'>{sent}</time>' in body
    assert f'data-date-format="{date_format}" data-time-format="{time_format}"' in body


@pytest.mark.parametrize(('date_format', 'text'), [('mdy', '11/05/2030'), ('dmy', '05/11/2030')])
def test_the_manage_expiry_is_a_date_field_with_an_iso_value(
    client, funded_user, transfer, date_format, text
):
    set_display_formats(funded_user, date_format=date_format)
    client.force_login(funded_user)

    body = flat(client.get(reverse('file_transfer:dashboard')))

    assert re.search(r'name="expiry_date" value="2030-11-05"', body)
    assert f'value="{text}"' in body
    assert 'type="date"' not in body


def test_the_public_page_is_the_same_for_a_signed_in_owner_and_a_visitor(
    client, funded_user, transfer
):
    set_display_formats(funded_user, date_format='dmy', time_format='24h')

    visitor = flat(client.get(reverse('t:download', args=[transfer.slug])))
    client.force_login(funded_user)
    owner = flat(client.get(reverse('t:download', args=[transfer.slug])))

    for body in (visitor, owner):
        assert 'Available until <time' in body
        assert 'Nov 5, 2030, 9:05 PM UTC</time>' in body
        assert 'data-date-format="public" data-time-format="12h"' in body
        assert '05/11/2030' not in body


def test_the_recipient_email_is_public_and_utc_whatever_the_owners_format(funded_user, transfer):
    set_display_formats(funded_user, date_format='dmy', time_format='24h')

    with mock.patch('apps.file_transfer.services.emails.send_email') as send:
        emails.send_transfer_notification.call(str(transfer.id), 'to@example.com')

    text = send.call_args.kwargs['text_body']
    assert 'This link expires on Nov 5, 2030, 9:05 PM UTC.' in text
    assert '05/11/2030' not in text and '21:05' not in text


def test_the_expires_tomorrow_email_is_public_and_utc(funded_user, transfer):
    set_display_formats(funded_user, date_format='dmy', time_format='24h')

    with mock.patch('apps.file_transfer.services.emails.send_email') as send:
        emails.send_expires_soon_notification.call(str(transfer.id))

    assert 'expires tomorrow (Nov 5, 2030, 9:05 PM UTC).' in send.call_args.kwargs['text_body']
