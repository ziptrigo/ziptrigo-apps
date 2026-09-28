"""Rate limiting on file_transfer endpoints (issue #53): anonymous upload/confirm, logged-in
uploads (generous, per user), the public download page and password attempts (per IP and per
transfer), and the anonymous manage link.
"""

import json

import pytest
from django.urls import reverse

from ..models import Transfer, TransferStatus
from ..services import password as password_service

pytestmark = [pytest.mark.django_db, pytest.mark.unit]


def _post_json(client, url, payload):
    return client.post(url, data=json.dumps(payload), content_type='application/json')


def _enable(settings, rule: str, limit: int = 1, window: int = 60):
    settings.RATELIMIT_ENABLE = True
    settings.RATELIMIT_RULES = {**settings.RATELIMIT_RULES, rule: (limit, window)}


def _active(transfer, **overrides):
    transfer.status = TransferStatus.ACTIVE
    for key, value in overrides.items():
        setattr(transfer, key, value)
    transfer.save()
    return transfer


class TestAnonymousUploadRateLimit:
    def test_add_file_429_after_ip_limit(self, client, anon_enabled, fake_storage, settings):
        _enable(settings, 'FT_ANON_UPLOAD_IP', limit=1)
        client.get(reverse('file_transfer:anon-send'))
        transfer = Transfer.objects.get(owner__isnull=True, status=TransferStatus.DRAFT)
        url = reverse('file_transfer:anon-send-add-file', args=[transfer.id])

        _post_json(client, url, {'name': 'a.bin', 'size': 10})
        response = _post_json(client, url, {'name': 'b.bin', 'size': 10})

        assert response.status_code == 429
        assert response.json()['error']
        assert 'Retry-After' in response

    def test_part_urls_429_after_ip_limit(self, client, anon_enabled, fake_storage, settings):
        client.get(reverse('file_transfer:anon-send'))
        transfer = Transfer.objects.get(owner__isnull=True, status=TransferStatus.DRAFT)
        add_url = reverse('file_transfer:anon-send-add-file', args=[transfer.id])
        added = _post_json(client, add_url, {'name': 'a.bin', 'size': 10}).json()

        # Enabled *after* creating the draft/file (which themselves go through the same shared
        # bucket) so the limit budget is spent entirely on the `part_urls` calls below.
        _enable(settings, 'FT_ANON_UPLOAD_IP', limit=1)
        parts_url = reverse(
            'file_transfer:anon-send-part-urls', args=[transfer.id, added['file_id']]
        )
        payload = {'parts': [{'part_number': 1, 'checksum_sha256': 'x'}]}

        _post_json(client, parts_url, payload)
        response = _post_json(client, parts_url, payload)

        assert response.status_code == 429


class TestAnonymousConfirmRateLimit:
    def _drafted_transfer_with_uploaded_file(self, client, fake_storage):
        client.get(reverse('file_transfer:anon-send'))
        transfer = Transfer.objects.get(owner__isnull=True, status=TransferStatus.DRAFT)
        from ..models import TransferFile

        add_url = reverse('file_transfer:anon-send-add-file', args=[transfer.id])
        added = _post_json(client, add_url, {'name': 'report.pdf', 'size': 10}).json()
        file = TransferFile.objects.get(id=added['file_id'])
        fake_storage.put_object(file.storage_key, 10)
        _post_json(
            client,
            reverse('file_transfer:anon-send-complete-file', args=[transfer.id, file.id]),
            {'parts': [{'PartNumber': 1, 'ETag': 'e1'}]},
        )
        return transfer

    def test_start_confirmation_429_after_ip_limit(
        self, client, anon_enabled, fake_storage, settings, monkeypatch
    ):
        sent = []
        monkeypatch.setattr(
            'apps.core.services.email_verification.send_email',
            lambda **kwargs: sent.append(kwargs) or (1, 0),
        )
        transfer = self._drafted_transfer_with_uploaded_file(client, fake_storage)
        _enable(settings, 'FT_ANON_CONFIRM_START_IP', limit=0)

        response = client.post(
            reverse('file_transfer:anon-send-confirm', args=[transfer.id]),
            data={
                'sender_email': 'sender@example.com',
                'recipients': 'recipient@example.com',
                'expiry_choice': '1',
            },
        )

        assert response.status_code == 429
        transfer.refresh_from_db()
        assert transfer.status == TransferStatus.DRAFT  # never started
        assert not sent

    def test_resend_confirmation_429_after_ip_limit(
        self, client, anon_enabled, fake_storage, settings, monkeypatch
    ):
        monkeypatch.setattr(
            'apps.core.services.email_verification.send_email',
            lambda **kwargs: (1, 0),
        )
        transfer = self._drafted_transfer_with_uploaded_file(client, fake_storage)
        client.post(
            reverse('file_transfer:anon-send-confirm', args=[transfer.id]),
            data={
                'sender_email': 'sender@example.com',
                'recipients': 'recipient@example.com',
                'expiry_choice': '1',
            },
        )
        _enable(settings, 'FT_ANON_CONFIRM_RESEND_IP', limit=0)

        response = client.post(
            reverse('file_transfer:anon-send-confirm-resend', args=[transfer.id])
        )

        assert response.status_code == 429
        assert b'Too many requests' in response.content

    def test_confirm_code_429_after_ip_limit(
        self, client, anon_enabled, fake_storage, settings, monkeypatch
    ):
        monkeypatch.setattr(
            'apps.core.services.email_verification.send_email',
            lambda **kwargs: (1, 0),
        )
        transfer = self._drafted_transfer_with_uploaded_file(client, fake_storage)
        client.post(
            reverse('file_transfer:anon-send-confirm', args=[transfer.id]),
            data={
                'sender_email': 'sender@example.com',
                'recipients': 'recipient@example.com',
                'expiry_choice': '1',
            },
        )
        _enable(settings, 'FT_ANON_CONFIRM_CODE_IP', limit=0)

        response = client.post(
            reverse('file_transfer:anon-send-confirm-code', args=[transfer.id]),
            data={'code': '000000'},
        )

        assert response.status_code == 429
        assert b'Too many attempts' in response.content
        transfer.refresh_from_db()
        assert transfer.status == TransferStatus.PENDING_CONFIRMATION  # code never even checked

    def test_confirm_link_429_after_ip_limit(
        self, client, anon_enabled, fake_storage, settings, monkeypatch
    ):
        import re

        sent = []
        monkeypatch.setattr(
            'apps.core.services.email_verification.send_email',
            lambda **kwargs: sent.append(kwargs) or (1, 0),
        )
        transfer = self._drafted_transfer_with_uploaded_file(client, fake_storage)
        client.post(
            reverse('file_transfer:anon-send-confirm', args=[transfer.id]),
            data={
                'sender_email': 'sender@example.com',
                'recipients': 'recipient@example.com',
                'expiry_choice': '1',
            },
        )
        match = re.search(r'(/transfer/send/anon/\S+/confirm/link/\S+/)', sent[-1]['text_body'])
        assert match
        link_url = match.group(1)

        _enable(settings, 'FT_ANON_CONFIRM_LINK_IP', limit=0)
        response = client.get(link_url)

        assert response.status_code == 429
        transfer.refresh_from_db()
        assert transfer.status == TransferStatus.PENDING_CONFIRMATION


class TestLoggedInUploadRateLimit:
    def test_add_file_429_after_user_limit(self, client, draft_transfer, fake_storage, settings):
        _enable(settings, 'FT_UPLOAD_USER', limit=1)
        client.force_login(draft_transfer.owner)
        url = reverse('file_transfer:send-add-file', args=[draft_transfer.id])

        _post_json(client, url, {'name': 'a.bin', 'size': 10})
        response = _post_json(client, url, {'name': 'b.bin', 'size': 10})

        assert response.status_code == 429
        assert response.json()['error']

    def test_different_users_have_independent_budgets(
        self, client, draft_transfer, fake_storage, settings
    ):
        from apps.accounts.tests.factories import UserFactory

        from ..models import Transfer as TransferModel

        _enable(settings, 'FT_UPLOAD_USER', limit=1)
        other_user = UserFactory()
        other_transfer = TransferModel.objects.create(owner=other_user, status=TransferStatus.DRAFT)

        client.force_login(draft_transfer.owner)
        _post_json(
            client,
            reverse('file_transfer:send-add-file', args=[draft_transfer.id]),
            {'name': 'a.bin', 'size': 10},
        )
        client.logout()

        client.force_login(other_user)
        response = _post_json(
            client,
            reverse('file_transfer:send-add-file', args=[other_transfer.id]),
            {'name': 'b.bin', 'size': 10},
        )

        assert response.status_code == 201


class TestDownloadPageRateLimit:
    def test_429_after_ip_limit(self, client, draft_transfer, uploaded_file, settings):
        _active(draft_transfer)
        _enable(settings, 'FT_DOWNLOAD_IP', limit=1)

        client.get(reverse('t:download', args=[draft_transfer.slug]))
        response = client.get(reverse('t:download', args=[draft_transfer.slug]))

        assert response.status_code == 429
        assert 'Retry-After' in response


class TestUnlockRateLimit:
    def test_429_after_ip_limit(self, client, draft_transfer, settings):
        _active(draft_transfer, password_hash=password_service.hash_password('sekret'))
        _enable(settings, 'FT_UNLOCK_IP', limit=1)

        url = reverse('t:unlock', args=[draft_transfer.slug])
        client.post(url, {'password': 'wrong'})
        response = client.post(url, {'password': 'wrong'})

        assert response.status_code == 429
        assert b'Too many attempts' in response.content

    def test_429_after_per_transfer_limit_even_from_different_ips(
        self, client, draft_transfer, settings
    ):
        _active(draft_transfer, password_hash=password_service.hash_password('sekret'))
        _enable(settings, 'FT_UNLOCK_TRANSFER', limit=1)
        settings.RATELIMIT_RULES = {**settings.RATELIMIT_RULES, 'FT_UNLOCK_IP': (1000, 60)}

        url = reverse('t:unlock', args=[draft_transfer.slug])
        client.post(url, {'password': 'wrong'}, REMOTE_ADDR='10.0.0.1')
        response = client.post(url, {'password': 'wrong'}, REMOTE_ADDR='10.0.0.2')

        assert response.status_code == 429

    def test_correct_password_still_works_under_the_limit(self, client, draft_transfer, settings):
        _active(draft_transfer, password_hash=password_service.hash_password('sekret'))
        _enable(settings, 'FT_UNLOCK_IP', limit=5)

        response = client.post(
            reverse('t:unlock', args=[draft_transfer.slug]), {'password': 'sekret'}
        )

        assert response.status_code == 302


class TestManageLinkRateLimit:
    def test_manage_page_429_after_ip_limit(self, client, settings):
        transfer = Transfer.objects.create(
            owner=None, sender_email='sender@example.com', status=TransferStatus.ACTIVE
        )
        _enable(settings, 'FT_MANAGE_IP', limit=1)
        url = f'/t/{transfer.slug}/manage/{transfer.manage_token}/'

        client.get(url)
        response = client.get(url)

        assert response.status_code == 429
