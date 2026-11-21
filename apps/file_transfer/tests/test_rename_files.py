"""Renaming a finished file on the send pages before sending (the logged-in and the anonymous
draft): the name is edited without its extension, which is kept."""

import json

import pytest
from django.core.exceptions import ValidationError
from django.test import Client
from django.urls import reverse

from apps.accounts.tests.factories import UserFactory

from .. import services
from ..models import Transfer, TransferFile, TransferStatus

pytestmark = [pytest.mark.django_db, pytest.mark.unit]


def _post_json(client, url, payload):
    return client.post(url, data=json.dumps(payload), content_type='application/json')


def _rename_url(file):
    return reverse('file_transfer:send-rename-file', args=[file.transfer_id, file.id])


class TestService:
    def test_keeps_the_extension(self, uploaded_file):
        services.rename_file(uploaded_file, 'Q3 report')

        uploaded_file.refresh_from_db()
        assert uploaded_file.name == 'Q3 report.pdf'

    def test_an_empty_name_is_refused(self, uploaded_file):
        with pytest.raises(ValidationError, match='Give the file a name'):
            services.rename_file(uploaded_file, '  ')

    def test_a_paused_upload_keeps_its_name(self, draft_transfer, fake_storage):
        pending = services.add_file(draft_transfer, 'big.iso', 1024, storage=fake_storage)

        with pytest.raises(ValidationError, match='finish uploading'):
            services.rename_file(pending, 'other')

    def test_a_sent_transfer_is_refused(self, uploaded_file):
        Transfer.objects.filter(pk=uploaded_file.transfer_id).update(status=TransferStatus.ACTIVE)

        with pytest.raises(ValidationError, match='already been sent'):
            services.rename_file(uploaded_file, 'other')


class TestLoggedInView:
    def test_renames(self, client, uploaded_file):
        client.force_login(uploaded_file.transfer.owner)

        response = _post_json(client, _rename_url(uploaded_file), {'name': 'Q3 report'})

        assert response.status_code == 200
        assert response.json() == {'ok': True, 'name': 'Q3 report.pdf'}

    def test_an_empty_name_is_a_422_that_spends_nothing(self, client, uploaded_file, settings):
        settings.RATELIMIT_ENABLE = True
        settings.RATELIMIT_RULES = {**settings.RATELIMIT_RULES, 'FT_UPLOAD_USER': (1, 3600)}
        client.force_login(uploaded_file.transfer.owner)

        refused = _post_json(client, _rename_url(uploaded_file), {'name': ' '})
        assert refused.status_code == 422
        assert refused.json()['error'] == 'Give the file a name.'
        assert _post_json(client, _rename_url(uploaded_file), {'name': 'a'}).status_code == 200
        assert _post_json(client, _rename_url(uploaded_file), {'name': 'b'}).status_code == 429

    def test_another_users_draft_is_a_404(self, client, uploaded_file):
        client.force_login(UserFactory())

        assert _post_json(client, _rename_url(uploaded_file), {'name': 'x'}).status_code == 404

    def test_the_send_page_turns_renaming_on(self, client, funded_user):
        client.force_login(funded_user)

        assert 'canRename: true' in client.get(reverse('file_transfer:send')).content.decode()


class TestAnonymousView:
    def _uploaded(self, client, fake_storage):
        client.get(reverse('file_transfer:anon-send'))
        transfer = Transfer.objects.get(owner__isnull=True, status=TransferStatus.DRAFT)
        add_url = reverse('file_transfer:anon-send-add-file', args=[transfer.id])
        file_id = _post_json(client, add_url, {'name': 'scan.png', 'size': 10}).json()['file_id']
        file = TransferFile.objects.get(id=file_id)
        fake_storage.seed_object(file.storage_key, 10)
        _post_json(
            client,
            reverse('file_transfer:anon-send-complete-file', args=[transfer.id, file_id]),
            {'parts': [{'PartNumber': 1, 'ETag': 'e1'}]},
        )
        return file

    def _url(self, file):
        return reverse('file_transfer:anon-send-rename-file', args=[file.transfer_id, file.id])

    def test_renames(self, client, anon_enabled, fake_storage):
        file = self._uploaded(client, fake_storage)

        response = _post_json(client, self._url(file), {'name': 'Receipt'})

        assert response.json() == {'ok': True, 'name': 'Receipt.png'}

    def test_another_session_is_a_404(self, client, anon_enabled, fake_storage):
        file = self._uploaded(client, fake_storage)
        other = Client()
        other.get(reverse('file_transfer:anon-send'))

        assert _post_json(other, self._url(file), {'name': 'x'}).status_code == 404
