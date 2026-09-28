import base64
import hashlib
import json

import pytest
from django.urls import reverse

from apps.accounts.tests.factories import UserFactory

pytestmark = [pytest.mark.django_db, pytest.mark.unit]

_CHECKSUM = base64.b64encode(b'\x00' * 32).decode()


def _part_checksum(data: bytes) -> str:
    """What `FakeS3Storage.list_parts` reports for a part recorded with these bytes -- see its
    docstring."""
    return base64.b64encode(hashlib.sha256(data).digest()).decode()


def _post_json(client, url, payload):
    return client.post(url, data=json.dumps(payload), content_type='application/json')


def test_add_file_requires_login(client, draft_transfer):
    url = reverse('file_transfer:send-add-file', args=[draft_transfer.id])
    response = _post_json(client, url, {'name': 'a.bin', 'size': 10})
    assert response.status_code == 302


def test_add_file_success(client, draft_transfer, fake_storage):
    client.force_login(draft_transfer.owner)
    url = reverse('file_transfer:send-add-file', args=[draft_transfer.id])

    response = _post_json(client, url, {'name': 'a.bin', 'size': 10 * 1024 * 1024})

    assert response.status_code == 201
    body = response.json()
    assert 'file_id' in body
    assert body['part_count'] >= 1


def test_add_file_rejects_bad_size(client, draft_transfer, fake_storage):
    client.force_login(draft_transfer.owner)
    url = reverse('file_transfer:send-add-file', args=[draft_transfer.id])

    response = _post_json(client, url, {'name': 'a.bin', 'size': 'not-a-number'})

    assert response.status_code == 422


def test_add_file_404s_for_other_users_draft(client, draft_transfer, fake_storage):
    other_user = UserFactory()
    client.force_login(other_user)
    url = reverse('file_transfer:send-add-file', args=[draft_transfer.id])

    response = _post_json(client, url, {'name': 'a.bin', 'size': 10})

    assert response.status_code == 404


def test_part_urls_and_complete_and_remove_flow(client, draft_transfer, fake_storage):
    client.force_login(draft_transfer.owner)
    add_url = reverse('file_transfer:send-add-file', args=[draft_transfer.id])
    added = _post_json(client, add_url, {'name': 'a.bin', 'size': 10}).json()
    file_id = added['file_id']

    parts_url = reverse('file_transfer:send-part-urls', args=[draft_transfer.id, file_id])
    parts_response = _post_json(
        client, parts_url, {'parts': [{'part_number': 1, 'checksum_sha256': _CHECKSUM}]}
    )
    assert parts_response.status_code == 200
    assert '1' in parts_response.json()['urls']

    from ..models import TransferFile

    file = TransferFile.objects.get(id=file_id)
    fake_storage.put_object(file.storage_key, 10)

    complete_url = reverse('file_transfer:send-complete-file', args=[draft_transfer.id, file_id])
    complete_response = _post_json(
        client, complete_url, {'parts': [{'PartNumber': 1, 'ETag': 'e1'}]}
    )
    assert complete_response.status_code == 200
    file.refresh_from_db()
    assert file.uploaded is True

    remove_url = reverse('file_transfer:send-remove-file', args=[draft_transfer.id, file_id])
    remove_response = client.post(remove_url)
    assert remove_response.status_code == 200
    assert not TransferFile.objects.filter(id=file_id).exists()


def test_part_urls_rejects_malformed_parts_payload(client, draft_transfer, fake_storage):
    client.force_login(draft_transfer.owner)
    add_url = reverse('file_transfer:send-add-file', args=[draft_transfer.id])
    added = _post_json(client, add_url, {'name': 'a.bin', 'size': 10}).json()

    parts_url = reverse('file_transfer:send-part-urls', args=[draft_transfer.id, added['file_id']])
    response = _post_json(client, parts_url, {'parts': [{'part_number': 'nope'}]})

    assert response.status_code == 422


def test_complete_file_rejects_size_mismatch(client, draft_transfer, fake_storage):
    client.force_login(draft_transfer.owner)
    add_url = reverse('file_transfer:send-add-file', args=[draft_transfer.id])
    added = _post_json(client, add_url, {'name': 'a.bin', 'size': 10}).json()
    file_id = added['file_id']

    complete_url = reverse('file_transfer:send-complete-file', args=[draft_transfer.id, file_id])
    response = _post_json(client, complete_url, {'parts': [{'PartNumber': 1, 'ETag': 'e1'}]})

    assert response.status_code == 422


def test_add_file_stores_client_last_modified(client, draft_transfer, fake_storage):
    from ..models import TransferFile

    client.force_login(draft_transfer.owner)
    url = reverse('file_transfer:send-add-file', args=[draft_transfer.id])

    added = _post_json(
        client, url, {'name': 'a.bin', 'size': 10, 'client_last_modified': 1700000000000}
    ).json()

    file = TransferFile.objects.get(id=added['file_id'])
    assert file.client_last_modified == 1700000000000


class TestResumeFile:
    def test_requires_login(self, client, draft_transfer, fake_storage):
        from ..services import uploads

        file = uploads.add_file(draft_transfer, 'a.bin', 300, storage=fake_storage)
        url = reverse('file_transfer:send-resume-file', args=[draft_transfer.id, file.id])

        response = client.post(url)

        assert response.status_code == 302

    def test_404s_for_other_users_draft(self, client, draft_transfer, fake_storage):
        from ..services import uploads

        file = uploads.add_file(draft_transfer, 'a.bin', 300, storage=fake_storage)
        client.force_login(UserFactory())
        url = reverse('file_transfer:send-resume-file', args=[draft_transfer.id, file.id])

        response = client.post(url)

        assert response.status_code == 404

    def test_reports_parts_already_uploaded(self, client, draft_transfer, fake_storage):
        client.force_login(draft_transfer.owner)
        add_url = reverse('file_transfer:send-add-file', args=[draft_transfer.id])
        added = _post_json(client, add_url, {'name': 'a.bin', 'size': 200 * 1024 * 1024}).json()
        from ..models import TransferFile

        file = TransferFile.objects.get(id=added['file_id'])
        # Simulate one part already having landed in S3 in an earlier page load.
        fake_storage.upload_part(file.storage_key, file.upload_id, 1, b'\0' * 10)

        url = reverse('file_transfer:send-resume-file', args=[draft_transfer.id, file.id])
        response = client.post(url)

        assert response.status_code == 200
        body = response.json()
        assert body['restarted'] is False
        assert body['uploaded_parts'] == [
            {
                'part_number': 1,
                'etag': f'etag-{file.upload_id}-1',
                'size': 10,
                'checksum_sha256': _part_checksum(b'\0' * 10),
            }
        ]
        assert body['part_count'] == added['part_count']

    def test_restarts_an_expired_upload(self, client, draft_transfer, fake_storage):
        client.force_login(draft_transfer.owner)
        add_url = reverse('file_transfer:send-add-file', args=[draft_transfer.id])
        added = _post_json(client, add_url, {'name': 'a.bin', 'size': 10}).json()
        from ..models import TransferFile

        file = TransferFile.objects.get(id=added['file_id'])
        old_upload_id = file.upload_id
        fake_storage.abort_multipart_upload(file.storage_key, old_upload_id)

        url = reverse('file_transfer:send-resume-file', args=[draft_transfer.id, file.id])
        response = client.post(url)

        assert response.status_code == 200
        body = response.json()
        assert body['restarted'] is True
        assert body['uploaded_parts'] == []
        file.refresh_from_db()
        assert file.upload_id != old_upload_id
        assert file.upload_id in fake_storage.active_uploads

    def test_404s_for_removed_file(self, client, draft_transfer, fake_storage):
        client.force_login(draft_transfer.owner)
        url = reverse('file_transfer:send-resume-file', args=[draft_transfer.id, draft_transfer.id])

        response = client.post(url)

        assert response.status_code == 404

    def test_resume_then_complete_round_trips_the_checksum(
        self, client, draft_transfer, fake_storage
    ):
        """End-to-end through the web JSON endpoints (issue #55 phase 3 review's critical fix): a
        part already landed in S3 is reported by `.../resume/` with its checksum, and completing
        the upload with that checksum carried straight through (exactly what `resumable_upload.js`
        now does) succeeds."""
        client.force_login(draft_transfer.owner)
        add_url = reverse('file_transfer:send-add-file', args=[draft_transfer.id])
        added = _post_json(client, add_url, {'name': 'a.bin', 'size': 10}).json()
        from ..models import TransferFile

        file = TransferFile.objects.get(id=added['file_id'])
        fake_storage.upload_part(file.storage_key, file.upload_id, 1, b'\0' * 10)

        resume_url = reverse('file_transfer:send-resume-file', args=[draft_transfer.id, file.id])
        resumed = client.post(resume_url).json()
        part = resumed['uploaded_parts'][0]
        assert part['checksum_sha256']

        complete_url = reverse(
            'file_transfer:send-complete-file', args=[draft_transfer.id, file.id]
        )
        complete_response = _post_json(
            client,
            complete_url,
            {
                'parts': [
                    {
                        'PartNumber': part['part_number'],
                        'ETag': part['etag'],
                        'ChecksumSHA256': part['checksum_sha256'],
                    }
                ]
            },
        )

        assert complete_response.status_code == 200
        file.refresh_from_db()
        assert file.uploaded is True
