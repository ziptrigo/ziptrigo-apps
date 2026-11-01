"""Findings of the E2E browser run (issue #152): emails, the dashboard row, the manage page."""

from unittest import mock

import pytest
from django.urls import reverse
from django.utils import timezone

from .. import services
from ..models import Transfer, TransferFile, TransferRecipient, TransferStatus
from ..services import emails

pytestmark = [pytest.mark.django_db, pytest.mark.unit]


def _file(transfer: Transfer, name: str, *, uploaded: bool = True) -> TransferFile:
    return TransferFile.objects.create(
        transfer=transfer, name=name, size=10, storage_key=f'k/{name}', uploaded=uploaded
    )


def _sent(transfer: Transfer) -> mock.MagicMock:
    with mock.patch('apps.file_transfer.services.emails.send_email') as send:
        emails.send_transfer_notification.call(str(transfer.id), 'to@example.com')
    send.assert_called_once()
    return send


class TestRecipientEmail:
    def test_names_every_uploaded_file(self, user):
        transfer = Transfer.objects.create(owner=user, status=TransferStatus.ACTIVE)
        for name in ('a.txt', 'b.txt', 'c.txt'):
            _file(transfer, name)
        _file(transfer, 'paused.bin', uploaded=False)

        kwargs = _sent(transfer).call_args.kwargs

        assert kwargs['text_body'].startswith('You have received 3 files:\n- a.txt\n- b.txt\n- c.txt\n')
        assert 'paused.bin' not in kwargs['text_body']
        assert kwargs['subject'] == 'a.txt and 2 more files were sent to you via ZipTrigo'

    def test_one_file(self, user):
        transfer = Transfer.objects.create(owner=user, status=TransferStatus.ACTIVE)
        _file(transfer, 'only.pdf')

        kwargs = _sent(transfer).call_args.kwargs

        assert kwargs['text_body'].startswith('You have received a file: only.pdf.')
        assert kwargs['subject'] == 'only.pdf was sent to you via ZipTrigo'

    def test_long_lists_are_cut(self, user):
        transfer = Transfer.objects.create(owner=user, status=TransferStatus.ACTIVE)
        for i in range(emails.MAX_LISTED_FILES + 3):
            _file(transfer, f'f{i:02}.txt')

        body = _sent(transfer).call_args.kwargs['text_body']

        assert '- and 3 more' in body
        assert f'f{emails.MAX_LISTED_FILES:02}.txt' not in body


class TestDashboardRow:
    def test_ended_row_shows_when_it_ended_and_no_copy_link(self, client, user):
        transfer = Transfer.objects.create(
            owner=user,
            status=TransferStatus.EXPIRED,
            completed_at=timezone.now(),
            ended_at=timezone.now(),
            expires_at=timezone.now() + timezone.timedelta(days=3),
        )
        _file(transfer, 'old.txt')
        client.force_login(user)

        html = client.get(reverse('file_transfer:dashboard'), {'filter': 'ended'}).content.decode()

        assert 'Ended:' in html
        assert 'Expires:' not in html
        assert 'data-copy-url' not in html

    def test_resend_says_it_sent_the_email(self, client, funded_user, monkeypatch):
        transfer = Transfer.objects.create(
            owner=funded_user, status=TransferStatus.ACTIVE, completed_at=timezone.now()
        )
        _file(transfer, 'r.txt')
        recipient = TransferRecipient.objects.create(transfer=transfer, email='r@example.com')
        monkeypatch.setattr(services, 'resend_recipient_email', lambda *a, **k: None)
        client.force_login(funded_user)

        response = client.post(
            reverse('file_transfer:resend-recipient', args=[transfer.id, recipient.id]),
            HTTP_HX_REQUEST='true',
        )

        assert response.status_code == 200
        assert 'Sent the email to r@example.com again.' in response.content.decode()


class TestManagePage:
    def test_disable_asks_first_and_count_has_no_stray_space(self, client):
        transfer = Transfer.objects.create(
            owner=None, sender_email='s@example.com', status=TransferStatus.ACTIVE
        )

        html = client.get(f'/t/{transfer.slug}/manage/{transfer.manage_token}/').content.decode()

        assert 'Downloaded 0 times.' in html
        assert 'x-data="confirmModal"' in html
        assert 'data-title="Disable this transfer?"' in html
