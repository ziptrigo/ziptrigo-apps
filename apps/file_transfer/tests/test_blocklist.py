"""The sender block list (issue #59): `services.blocklist` matching rules, and every checkpoint
that's supposed to enforce it -- logged-in draft creation (web + API), finalize/send, anonymous
draft creation, and both ends of anonymous email confirmation.
"""

import json

import pytest
from django.core.exceptions import ValidationError
from django.urls import reverse

from apps.accounts.tokens import CustomAccessToken

from ..models import BlockedSender, BlockedSenderKind, TransferStatus
from ..services import blocklist
from ..services.anonymous import (
    AnonymousSendOptions,
    confirm_by_code,
    get_or_create_anonymous_draft,
    start_confirmation,
)
from ..services.send import SendOptions, finalize_send
from ..services.uploads import add_file, complete_file_upload

pytestmark = [pytest.mark.django_db, pytest.mark.unit]


# -- Matching rules --


def test_is_blocked_false_with_no_entries():
    assert blocklist.is_blocked(email='nobody@example.com', ip='203.0.113.1') is False


def test_email_exact_match_is_case_insensitive():
    BlockedSender.objects.create(kind=BlockedSenderKind.EMAIL, value='blocked@example.com')
    assert blocklist.is_blocked(email='Blocked@Example.com') is True
    assert blocklist.is_blocked(email='other@example.com') is False


def test_email_domain_wildcard():
    BlockedSender.objects.create(kind=BlockedSenderKind.EMAIL, value='*@spam.example')
    assert blocklist.is_blocked(email='anyone@spam.example') is True
    assert blocklist.is_blocked(email='anyone@SPAM.example') is True
    assert blocklist.is_blocked(email='anyone@notspam.example') is False


def test_ipv4_cidr_match():
    BlockedSender.objects.create(kind=BlockedSenderKind.IP, value='203.0.113.0/24')
    assert blocklist.is_blocked(ip='203.0.113.42') is True
    assert blocklist.is_blocked(ip='198.51.100.1') is False


def test_ipv6_cidr_match():
    BlockedSender.objects.create(kind=BlockedSenderKind.IP, value='2001:db8::/32')
    assert blocklist.is_blocked(ip='2001:db8:1234::1') is True
    assert blocklist.is_blocked(ip='2001:db9::1') is False


def test_bare_ip_match():
    BlockedSender.objects.create(kind=BlockedSenderKind.IP, value='203.0.113.5')
    assert blocklist.is_blocked(ip='203.0.113.5') is True
    assert blocklist.is_blocked(ip='203.0.113.6') is False


def test_expired_entry_does_not_block():
    from datetime import timedelta

    from django.utils import timezone

    BlockedSender.objects.create(
        kind=BlockedSenderKind.EMAIL,
        value='was-blocked@example.com',
        expires_at=timezone.now() - timedelta(days=1),
    )
    assert blocklist.is_blocked(email='was-blocked@example.com') is False


def test_future_expiry_still_blocks():
    from datetime import timedelta

    from django.utils import timezone

    BlockedSender.objects.create(
        kind=BlockedSenderKind.EMAIL,
        value='still-blocked@example.com',
        expires_at=timezone.now() + timedelta(days=1),
    )
    assert blocklist.is_blocked(email='still-blocked@example.com') is True


def test_check_not_blocked_raises_blocked_sender_error():
    BlockedSender.objects.create(kind=BlockedSenderKind.EMAIL, value='blocked@example.com')
    with pytest.raises(blocklist.BlockedSenderError):
        blocklist.check_not_blocked(email='blocked@example.com')


def test_blocked_sender_error_is_a_validation_error():
    assert issubclass(blocklist.BlockedSenderError, ValidationError)


def test_block_transfer_sender_creates_email_and_ip_entries(draft_transfer, admin_user):
    draft_transfer.sender_ip = '203.0.113.9'
    draft_transfer.save()

    created = blocklist.block_transfer_sender(draft_transfer, created_by=admin_user, reason='abuse')

    assert len(created) == 2
    assert BlockedSender.objects.filter(
        kind=BlockedSenderKind.EMAIL, value=draft_transfer.owner.email.lower()
    ).exists()
    assert BlockedSender.objects.filter(kind=BlockedSenderKind.IP, value='203.0.113.9').exists()


def test_block_transfer_sender_does_not_duplicate(draft_transfer, admin_user):
    blocklist.block_transfer_sender(draft_transfer, created_by=admin_user)
    created_again = blocklist.block_transfer_sender(draft_transfer, created_by=admin_user)
    assert created_again == []
    assert BlockedSender.objects.filter(kind=BlockedSenderKind.EMAIL).count() == 1


# -- Checkpoint: logged-in web send page (draft creation) --


def test_send_page_blocked_by_email(client, funded_user):
    BlockedSender.objects.create(kind=BlockedSenderKind.EMAIL, value=funded_user.email)
    client.force_login(funded_user)

    response = client.get(reverse('file_transfer:send'))

    assert response.status_code == 403
    assert "can't send transfers" in response.content.decode()


def test_send_page_not_blocked(client, funded_user):
    client.force_login(funded_user)
    response = client.get(reverse('file_transfer:send'))
    assert response.status_code == 200


# -- Checkpoint: logged-in finalize/send --


def test_finalize_send_blocked_by_email(draft_transfer, uploaded_file):
    BlockedSender.objects.create(kind=BlockedSenderKind.EMAIL, value=draft_transfer.owner.email)
    options = SendOptions(recipients=['recipient@example.com'])

    with pytest.raises(blocklist.BlockedSenderError):
        finalize_send(draft_transfer, options)

    draft_transfer.refresh_from_db()
    assert draft_transfer.status == TransferStatus.DRAFT


# -- Checkpoint: JWT API create --


def test_api_create_transfer_blocked(client, funded_user):
    BlockedSender.objects.create(kind=BlockedSenderKind.EMAIL, value=funded_user.email)
    headers = {'HTTP_AUTHORIZATION': f'Bearer {CustomAccessToken.for_user(funded_user)}'}

    response = client.post('/api/ft/transfers/', **headers)

    assert response.status_code == 403


def test_api_finalize_transfer_blocked(client, funded_user, fake_storage):
    from ..services.uploads import create_draft

    transfer = create_draft(funded_user)
    file = add_file(transfer, 'a.txt', 10, storage=fake_storage)
    fake_storage.put_object(file.storage_key, 10)
    complete_file_upload(file, [{'PartNumber': 1, 'ETag': 'etag-1'}], storage=fake_storage)
    BlockedSender.objects.create(kind=BlockedSenderKind.EMAIL, value=funded_user.email)
    headers = {'HTTP_AUTHORIZATION': f'Bearer {CustomAccessToken.for_user(funded_user)}'}
    payload = {'recipients': ['recipient@example.com']}

    response = client.post(
        f'/api/ft/transfers/{transfer.id}/send',
        data=json.dumps(payload),
        content_type='application/json',
        **headers,
    )

    assert response.status_code == 403


# -- Checkpoint: anonymous draft creation (IP only) --


def test_anon_send_page_blocked_by_ip(client, anon_enabled):
    BlockedSender.objects.create(kind=BlockedSenderKind.IP, value='127.0.0.1')
    response = client.get(reverse('file_transfer:anon-send'))
    assert response.status_code == 403


# -- Checkpoint: anonymous confirmation start and activation --


def _upload(transfer, fake_storage, size=1024):
    file = add_file(
        transfer, 'report.pdf', size, ip='203.0.113.7', cookie_id='cookie', storage=fake_storage
    )
    fake_storage.put_object(file.storage_key, size)
    complete_file_upload(file, [{'PartNumber': 1, 'ETag': 'etag-1'}], storage=fake_storage)
    return file


def test_start_confirmation_blocked_by_email(anon_enabled, fake_storage):
    from django.contrib.sessions.backends.db import SessionStore

    session = SessionStore()
    transfer = get_or_create_anonymous_draft(session, '203.0.113.7', 'cookie')
    _upload(transfer, fake_storage)
    BlockedSender.objects.create(kind=BlockedSenderKind.EMAIL, value='blocked@example.com')

    options = AnonymousSendOptions(
        sender_email='blocked@example.com',
        recipients=['recipient@example.com'],
        expiry_choice='1',
    )

    with pytest.raises(blocklist.BlockedSenderError):
        start_confirmation(transfer, options, ip='203.0.113.7', cookie_id='cookie')


def test_activation_blocked_when_blocked_after_confirmation_started(
    anon_enabled, fake_storage, monkeypatch
):
    """The block list is re-checked at activation too -- blocking the sender in the window
    between starting confirmation and actually confirming still stops the send."""
    from django.contrib.sessions.backends.db import SessionStore

    from apps.core.services import email_verification

    sent = []
    monkeypatch.setattr(
        email_verification, 'send_email', lambda **kwargs: sent.append(kwargs) or (1, 0)
    )

    session = SessionStore()
    transfer = get_or_create_anonymous_draft(session, '203.0.113.7', 'cookie')
    _upload(transfer, fake_storage)
    options = AnonymousSendOptions(
        sender_email='sender@example.com',
        recipients=['recipient@example.com'],
        expiry_choice='1',
    )
    transfer = start_confirmation(transfer, options, ip='203.0.113.7', cookie_id='cookie')

    # Blocked only *after* confirmation started.
    BlockedSender.objects.create(kind=BlockedSenderKind.EMAIL, value='sender@example.com')

    code = _extract_code(sent[-1]['text_body'])

    with pytest.raises(blocklist.BlockedSenderError):
        confirm_by_code(transfer, code, '203.0.113.7', 'cookie')

    transfer.refresh_from_db()
    assert transfer.status == TransferStatus.DELETED


def _extract_code(body: str) -> str:
    import re

    match = re.search(r'sending from: (\d+)', body)
    assert match, f'no code found in {body!r}'
    return match.group(1)
