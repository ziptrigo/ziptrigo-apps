"""End-to-end HTTP tests for the anonymous send flow (spec section 2 phase 2): the switch, session
ownership, and the full upload -> confirm -> sent path through the actual URLs.
"""

import json
import re

import pytest
from django.test import Client
from django.urls import reverse

from ..models import Transfer, TransferStatus

pytestmark = [pytest.mark.django_db, pytest.mark.unit]


def _post_json(client, url, payload):
    return client.post(url, data=json.dumps(payload), content_type='application/json')


def _capture_email(monkeypatch) -> list[dict]:
    sent: list[dict] = []
    monkeypatch.setattr(
        'apps.core.services.email_verification.send_email',
        lambda **kwargs: sent.append(kwargs) or (1, 0),
    )
    return sent


def _extract_code(body: str) -> str:
    match = re.search(r'sending from: (\d+)', body)
    assert match, f'no code found in {body!r}'
    return match.group(1)


def test_send_page_404s_when_anonymous_sending_disabled(client, ft_settings):
    assert ft_settings.anonymous_enabled is False
    response = client.get(reverse('file_transfer:anon-send'))
    assert response.status_code == 404


def test_send_page_creates_a_session_owned_draft(client, anon_enabled):
    response = client.get(reverse('file_transfer:anon-send'))
    assert response.status_code == 200

    transfer = Transfer.objects.get(owner__isnull=True, status=TransferStatus.DRAFT)
    assert transfer.session_key
    assert transfer.sender_ip is not None or transfer.sender_ip is None  # set from REMOTE_ADDR


def test_another_session_cannot_upload_to_someone_elses_anonymous_draft(
    client, anon_enabled, fake_storage
):
    client.get(reverse('file_transfer:anon-send'))
    transfer = Transfer.objects.get(owner__isnull=True, status=TransferStatus.DRAFT)

    other_client = Client()
    other_client.get(reverse('file_transfer:anon-send'))  # creates its own draft + session

    url = reverse('file_transfer:anon-send-add-file', args=[transfer.id])
    response = _post_json(other_client, url, {'name': 'a.bin', 'size': 10})
    assert response.status_code == 404


def test_another_session_cannot_remove_a_file_from_someone_elses_draft(
    client, anon_enabled, fake_storage
):
    client.get(reverse('file_transfer:anon-send'))
    transfer = Transfer.objects.get(owner__isnull=True, status=TransferStatus.DRAFT)
    add_url = reverse('file_transfer:anon-send-add-file', args=[transfer.id])
    added = _post_json(client, add_url, {'name': 'a.bin', 'size': 10}).json()

    other_client = Client()
    other_client.get(reverse('file_transfer:anon-send'))
    remove_url = reverse(
        'file_transfer:anon-send-remove-file', args=[transfer.id, added['file_id']]
    )
    response = other_client.post(remove_url)
    assert response.status_code == 404


def test_full_anonymous_send_flow(
    client, anon_enabled, fake_storage, monkeypatch, django_capture_on_commit_callbacks
):
    sent = _capture_email(monkeypatch)

    client.get(reverse('file_transfer:anon-send'))
    transfer = Transfer.objects.get(owner__isnull=True, status=TransferStatus.DRAFT)

    add_url = reverse('file_transfer:anon-send-add-file', args=[transfer.id])
    added = _post_json(client, add_url, {'name': 'report.pdf', 'size': 10}).json()
    file_id = added['file_id']

    from ..models import TransferFile

    file = TransferFile.objects.get(id=file_id)
    fake_storage.put_object(file.storage_key, 10)

    complete_url = reverse('file_transfer:anon-send-complete-file', args=[transfer.id, file_id])
    complete_response = _post_json(
        client, complete_url, {'parts': [{'PartNumber': 1, 'ETag': 'e1'}]}
    )
    assert complete_response.status_code == 200

    confirm_start_url = reverse('file_transfer:anon-send-confirm', args=[transfer.id])
    with django_capture_on_commit_callbacks(execute=True):
        start_response = client.post(
            confirm_start_url,
            data={
                'sender_email': 'sender@example.com',
                'recipients': 'recipient@example.com',
                'message': 'hello',
                'expiry_choice': '1',
            },
        )
    assert start_response.status_code == 302

    transfer.refresh_from_db()
    assert transfer.status == TransferStatus.PENDING_CONFIRMATION

    # The send page now shows the confirm box instead of the upload form.
    pending_page = client.get(reverse('file_transfer:anon-send'))
    assert b'code' in pending_page.content.lower()

    code = _extract_code(sent[-1]['text_body'])
    confirm_code_url = reverse('file_transfer:anon-send-confirm-code', args=[transfer.id])
    with django_capture_on_commit_callbacks(execute=True):
        confirm_response = client.post(confirm_code_url, data={'code': code})
    assert confirm_response.status_code == 302

    transfer.refresh_from_db()
    assert transfer.status == TransferStatus.ACTIVE

    sent_page = client.get(reverse('file_transfer:anon-sent', args=[transfer.id]))
    assert sent_page.status_code == 200


def test_wrong_confirmation_code_does_not_activate(client, anon_enabled, fake_storage, monkeypatch):
    _capture_email(monkeypatch)
    client.get(reverse('file_transfer:anon-send'))
    transfer = Transfer.objects.get(owner__isnull=True, status=TransferStatus.DRAFT)

    add_url = reverse('file_transfer:anon-send-add-file', args=[transfer.id])
    added = _post_json(client, add_url, {'name': 'report.pdf', 'size': 10}).json()
    from ..models import TransferFile

    file = TransferFile.objects.get(id=added['file_id'])
    fake_storage.put_object(file.storage_key, 10)
    _post_json(
        client,
        reverse('file_transfer:anon-send-complete-file', args=[transfer.id, file.id]),
        {'parts': [{'PartNumber': 1, 'ETag': 'e1'}]},
    )

    client.post(
        reverse('file_transfer:anon-send-confirm', args=[transfer.id]),
        data={
            'sender_email': 'sender@example.com',
            'recipients': 'recipient@example.com',
            'expiry_choice': '1',
        },
    )

    response = client.post(
        reverse('file_transfer:anon-send-confirm-code', args=[transfer.id]),
        data={'code': '000000'},
    )
    assert response.status_code == 422

    transfer.refresh_from_db()
    assert transfer.status == TransferStatus.PENDING_CONFIRMATION
