import json

import pytest
from django.urls import reverse

from apps.accounts.tests.factories import UserFactory

pytestmark = [pytest.mark.django_db, pytest.mark.unit]


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
    parts_response = _post_json(client, parts_url, {'part_numbers': [1]})
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


def test_complete_file_rejects_size_mismatch(client, draft_transfer, fake_storage):
    client.force_login(draft_transfer.owner)
    add_url = reverse('file_transfer:send-add-file', args=[draft_transfer.id])
    added = _post_json(client, add_url, {'name': 'a.bin', 'size': 10}).json()
    file_id = added['file_id']

    complete_url = reverse('file_transfer:send-complete-file', args=[draft_transfer.id, file_id])
    response = _post_json(client, complete_url, {'parts': [{'PartNumber': 1, 'ETag': 'e1'}]})

    assert response.status_code == 422
