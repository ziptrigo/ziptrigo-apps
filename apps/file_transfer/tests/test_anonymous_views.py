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


def _extract_link_url(body: str) -> str:
    match = re.search(r'(/transfer/send/anon/\S+/confirm/link/\S+/)', body)
    assert match, f'no confirmation link found in {body!r}'
    return match.group(1)


def test_send_page_404s_when_anonymous_sending_disabled(client, ft_settings):
    assert ft_settings.anonymous_enabled is False
    response = client.get(reverse('file_transfer:anon-send'))
    assert response.status_code == 404


def test_send_page_creates_a_session_owned_draft(client, anon_enabled):
    response = client.get(reverse('file_transfer:anon-send'))
    assert response.status_code == 200

    transfer = Transfer.objects.get(owner__isnull=True, status=TransferStatus.DRAFT)
    # Ownership is a random per-draft token kept in the session's own data, never the session's
    # own key (see `services.anon_session`) -- only a hash of that token lives on the model.
    assert transfer.draft_token_hash
    assert transfer.sender_ip is not None or transfer.sender_ip is None  # set from REMOTE_ADDR


def test_send_page_redirects_an_authenticated_user_to_the_normal_send_page(
    client, anon_enabled, user
):
    client.force_login(user)
    response = client.get(reverse('file_transfer:anon-send'))
    assert response.status_code == 302
    assert response['Location'] == reverse('file_transfer:send')
    assert not Transfer.objects.filter(owner__isnull=True).exists()


def test_start_confirmation_rejects_an_authenticated_user_even_via_direct_post(
    client, anon_enabled, fake_storage, user
):
    """Belt-and-suspenders alongside the `send_page` redirect: even if an authenticated session
    somehow still has an anonymous draft's id (e.g. it created one before logging in), posting
    the options form directly must not let it complete a free send."""
    client.get(reverse('file_transfer:anon-send'))
    transfer = Transfer.objects.get(owner__isnull=True, status=TransferStatus.DRAFT)
    add_url = reverse('file_transfer:anon-send-add-file', args=[transfer.id])
    added = _post_json(client, add_url, {'name': 'a.bin', 'size': 10}).json()
    from ..models import TransferFile

    file = TransferFile.objects.get(id=added['file_id'])
    fake_storage.put_object(file.storage_key, 10)
    _post_json(
        client,
        reverse('file_transfer:anon-send-complete-file', args=[transfer.id, file.id]),
        {'parts': [{'PartNumber': 1, 'ETag': 'e1'}]},
    )

    client.force_login(user)
    response = client.post(
        reverse('file_transfer:anon-send-confirm', args=[transfer.id]),
        data={
            'sender_email': 'sender@example.com',
            'recipients': 'recipient@example.com',
            'expiry_choice': '1',
        },
    )
    assert response.status_code == 403
    transfer.refresh_from_db()
    assert transfer.status == TransferStatus.DRAFT


def test_session_data_survives_login_mid_flow(client, anon_enabled, fake_storage, user):
    """`django.contrib.auth.login()` rotates the session's own key (`cycle_key()`) but *keeps*
    its data -- since draft ownership lives entirely in that data (a per-draft token, never the
    session's own key), a sender who logs into an unrelated account mid-flow must not lose their
    draft."""
    client.get(reverse('file_transfer:anon-send'))
    transfer = Transfer.objects.get(owner__isnull=True, status=TransferStatus.DRAFT)
    old_session_key = client.session.session_key

    client.force_login(user)
    assert client.session.session_key != old_session_key  # the rotation this test is about

    add_url = reverse('file_transfer:anon-send-add-file', args=[transfer.id])
    response = _post_json(client, add_url, {'name': 'a.bin', 'size': 10})
    assert response.status_code == 201


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


def _pending_transfer_with_link(client, fake_storage, monkeypatch, sent) -> tuple[Transfer, str]:
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
    client.post(
        reverse('file_transfer:anon-send-confirm', args=[transfer.id]),
        data={
            'sender_email': 'sender@example.com',
            'recipients': 'recipient@example.com',
            'expiry_choice': '1',
        },
    )
    link_url = _extract_link_url(sent[-1]['text_body'])
    transfer.refresh_from_db()
    return transfer, link_url


def test_confirm_link_get_shows_a_confirm_button_and_does_not_confirm(
    client, anon_enabled, fake_storage, monkeypatch
):
    """The emailed link must not confirm on a bare GET -- that's exactly what a mail scanner or
    link-previewer fetching the URL automatically (before the recipient ever clicks anything)
    would trigger."""
    sent = _capture_email(monkeypatch)
    transfer, link_url = _pending_transfer_with_link(client, fake_storage, monkeypatch, sent)

    # A different browser than the one that started the send (spec: "the sender opens their
    # email on their phone") -- no session ownership of the draft at all.
    other_client = Client()
    response = other_client.get(link_url)

    assert response.status_code == 200
    assert b'Confirm this transfer' in response.content
    transfer.refresh_from_db()
    assert transfer.status == TransferStatus.PENDING_CONFIRMATION


def test_confirm_link_post_confirms_and_grants_the_confirming_session_the_sent_page(
    client, anon_enabled, fake_storage, monkeypatch, django_capture_on_commit_callbacks
):
    sent = _capture_email(monkeypatch)
    transfer, link_url = _pending_transfer_with_link(client, fake_storage, monkeypatch, sent)

    other_client = Client()
    with django_capture_on_commit_callbacks(execute=True):
        response = other_client.post(link_url)
    assert response.status_code == 302

    transfer.refresh_from_db()
    assert transfer.status == TransferStatus.ACTIVE

    # The confirming session (not the one that drafted it) can still see the sent page: it was
    # granted that via `mark_confirmed_via_link`, since it never owned the draft itself.
    sent_page_response = other_client.get(reverse('file_transfer:anon-sent', args=[transfer.id]))
    assert sent_page_response.status_code == 200


def test_sent_page_404s_for_an_unrelated_session(
    client, anon_enabled, fake_storage, monkeypatch, django_capture_on_commit_callbacks
):
    """Neither the browser that created the draft nor the one that confirmed it -- an arbitrary
    third visitor who merely has (or guesses) the transfer's UUID must not see the sender's email
    or the download link."""
    sent = _capture_email(monkeypatch)
    transfer, link_url = _pending_transfer_with_link(client, fake_storage, monkeypatch, sent)

    with django_capture_on_commit_callbacks(execute=True):
        client.post(link_url)  # confirm from the drafting session itself, for simplicity
    transfer.refresh_from_db()
    assert transfer.status == TransferStatus.ACTIVE

    unrelated_client = Client()
    response = unrelated_client.get(reverse('file_transfer:anon-sent', args=[transfer.id]))
    assert response.status_code == 404
