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


def test_block_transfer_sender_reblocks_after_expiry(draft_transfer, admin_user):
    """Issue #59 code review: `get_or_create` matched an *expired* row too, so re-blocking
    someone whose earlier block had lapsed created nothing ("Added 0 entries") and left them
    unblocked. Now it reactivates the lapsed row instead."""
    from datetime import timedelta

    from django.utils import timezone

    email = draft_transfer.owner.email.lower()
    expired = BlockedSender.objects.create(
        kind=BlockedSenderKind.EMAIL,
        value=email,
        reason='first offense',
        expires_at=timezone.now() - timedelta(days=1),
    )
    assert blocklist.is_blocked(email=email) is False

    created = blocklist.block_transfer_sender(
        draft_transfer, created_by=admin_user, reason='second offense', block_ip=False
    )

    assert len(created) == 1
    assert created[0].pk == expired.pk  # reactivated, not a new row
    assert blocklist.is_blocked(email=email) is True
    expired.refresh_from_db()
    assert expired.expires_at is None
    assert expired.reason == 'second offense'
    assert BlockedSender.objects.filter(kind=BlockedSenderKind.EMAIL, value=email).count() == 1


def test_block_transfer_sender_does_not_touch_an_unrelated_active_block(draft_transfer, admin_user):
    """Two identical *active* rows would make a plain `get_or_create` raise
    `MultipleObjectsReturned` (issue #59 code review) -- `_block` never creates a second active
    row while one already exists, so this can no longer happen through this path."""
    email = draft_transfer.owner.email.lower()
    first = BlockedSender.objects.create(kind=BlockedSenderKind.EMAIL, value=email)

    created = blocklist.block_transfer_sender(draft_transfer, created_by=admin_user)

    assert created == []
    assert BlockedSender.objects.get(kind=BlockedSenderKind.EMAIL, value=email).pk == first.pk


def test_active_transfers_for_sender_excludes_ended_transfers(funded_user):
    from ..models import Transfer

    active = Transfer.objects.create(owner=funded_user, status=TransferStatus.ACTIVE)
    Transfer.objects.create(owner=funded_user, status=TransferStatus.DELETED)

    result = blocklist.active_transfers_for_sender(active)

    assert list(result) == [active]


# -- `BlockedSender.clean()` validation (issue #59 code review) --


def test_blocked_sender_clean_rejects_invalid_email():
    entry = BlockedSender(kind=BlockedSenderKind.EMAIL, value='not-an-email')
    with pytest.raises(ValidationError):
        entry.full_clean()


def test_blocked_sender_clean_rejects_email_missing_wildcard_prefix():
    """`example.com` (missing the `*@` prefix) would otherwise silently never match anything."""
    entry = BlockedSender(kind=BlockedSenderKind.EMAIL, value='example.com')
    with pytest.raises(ValidationError):
        entry.full_clean()


def test_blocked_sender_clean_accepts_valid_wildcard():
    entry = BlockedSender(kind=BlockedSenderKind.EMAIL, value='*@spam.example')
    entry.full_clean()  # does not raise


def test_blocked_sender_clean_rejects_invalid_cidr():
    entry = BlockedSender(kind=BlockedSenderKind.IP, value='not-an-ip')
    with pytest.raises(ValidationError):
        entry.full_clean()


def test_blocked_sender_clean_accepts_valid_cidr():
    entry = BlockedSender(kind=BlockedSenderKind.IP, value='203.0.113.0/24')
    entry.full_clean()  # does not raise


def test_blocked_sender_clean_rejects_second_active_duplicate():
    BlockedSender.objects.create(kind=BlockedSenderKind.EMAIL, value='dup@example.com')
    entry = BlockedSender(kind=BlockedSenderKind.EMAIL, value='dup@example.com')
    with pytest.raises(ValidationError):
        entry.full_clean()


def test_blocked_sender_clean_allows_duplicate_of_an_expired_entry():
    from datetime import timedelta

    from django.utils import timezone

    BlockedSender.objects.create(
        kind=BlockedSenderKind.EMAIL,
        value='dup@example.com',
        expires_at=timezone.now() - timedelta(days=1),
    )
    entry = BlockedSender(kind=BlockedSenderKind.EMAIL, value='dup@example.com')
    entry.full_clean()  # does not raise -- history, not a conflict


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


def test_finalize_send_blocked_by_ip(draft_transfer, uploaded_file):
    """Issue #59 code review: `finalize_send` used to check only the owner's email -- `ip` is now
    threaded through from the web send page and the JWT API's finalize endpoint, for consistency
    with every other send-path checkpoint."""
    BlockedSender.objects.create(kind=BlockedSenderKind.IP, value='203.0.113.9')
    options = SendOptions(recipients=['recipient@example.com'])

    with pytest.raises(blocklist.BlockedSenderError):
        finalize_send(draft_transfer, options, ip='203.0.113.9')

    draft_transfer.refresh_from_db()
    assert draft_transfer.status == TransferStatus.DRAFT


def test_finalize_send_web_checkpoint_blocked_by_ip(client, funded_user, uploaded_file):
    """The web send page's own submit (`views.send.send_submit`) passes the client IP through to
    `finalize_send`."""
    BlockedSender.objects.create(kind=BlockedSenderKind.IP, value='127.0.0.1')
    client.force_login(funded_user)
    draft = uploaded_file.transfer

    response = client.post(
        reverse('file_transfer:send-submit', args=[draft.id]),
        data={'recipients': 'recipient@example.com', 'expiry_choice': 'none'},
    )

    assert response.status_code == 422
    draft.refresh_from_db()
    assert draft.status == TransferStatus.DRAFT


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
