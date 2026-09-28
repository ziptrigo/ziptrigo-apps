"""Anonymous sending (spec section 2 phase 2): draft creation, options validation, email
confirmation (by code and by link), activation, and the per-IP-per-day caps.
"""

import re

import pytest
from django.core.exceptions import ValidationError

from apps.core.services.email_verification import (
    EmailVerificationExpired,
    IncorrectCode,
    ResendTooSoon,
)

from ..models import FileTransferSettings, Transfer, TransferStatus
from ..services import anon_limits
from ..services.anonymous import (
    AnonymousSendOptions,
    confirm_by_code,
    confirm_by_link,
    current_anonymous_transfer,
    get_or_create_anonymous_draft,
    resend_confirmation,
    start_confirmation,
    validate_anonymous_send_options,
)
from ..services.uploads import add_file, complete_file_upload

pytestmark = [pytest.mark.django_db, pytest.mark.unit]

SESSION_KEY = 'test-session-key-1234567890123456'
IP = '203.0.113.7'
COOKIE = 'cookie-id-1'


def _options(
    *,
    sender_email: str = 'sender@example.com',
    recipients: list[str] | None = None,
    message: str = 'hi',
    expiry_choice: str = '1',
    max_downloads: int | None = None,
    password: str = '',
    notify_on_download: bool = True,
) -> AnonymousSendOptions:
    return AnonymousSendOptions(
        sender_email=sender_email,
        recipients=recipients if recipients is not None else ['recipient@example.com'],
        message=message,
        expiry_choice=expiry_choice,
        max_downloads=max_downloads,
        password=password,
        notify_on_download=notify_on_download,
    )


def _draft(anon_enabled) -> Transfer:
    return get_or_create_anonymous_draft(SESSION_KEY, IP, COOKIE)


def _upload(transfer: Transfer, fake_storage, size: int = 1024, name: str = 'report.pdf'):
    file = add_file(transfer, name, size, ip=IP, cookie_id=COOKIE, storage=fake_storage)
    fake_storage.put_object(file.storage_key, size)
    complete_file_upload(file, [{'PartNumber': 1, 'ETag': 'etag-1'}], storage=fake_storage)
    return file


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


def _extract_link_token(body: str) -> str:
    match = re.search(r'/confirm/link/([^/\s]+)/', body)
    assert match, f'no confirmation link found in {body!r}'
    return match.group(1)


def test_get_or_create_anonymous_draft_reuses_empty_draft(anon_enabled):
    first = get_or_create_anonymous_draft(SESSION_KEY, IP, COOKIE)
    second = get_or_create_anonymous_draft(SESSION_KEY, IP, COOKIE)
    assert first.pk == second.pk
    assert first.owner is None
    assert first.session_key == SESSION_KEY
    assert first.sender_ip == IP
    assert first.anon_cookie_id == COOKIE


def test_get_or_create_anonymous_draft_does_not_reuse_a_draft_with_files(
    anon_enabled, fake_storage
):
    first = get_or_create_anonymous_draft(SESSION_KEY, IP, COOKIE)
    _upload(first, fake_storage)
    second = get_or_create_anonymous_draft(SESSION_KEY, IP, COOKIE)
    assert second.pk != first.pk


def test_current_anonymous_transfer_ignores_other_sessions(anon_enabled):
    _draft(anon_enabled)
    assert current_anonymous_transfer('someone-elses-session') is None
    assert current_anonymous_transfer(SESSION_KEY) is not None


def test_validate_anonymous_send_options_rejects_non_allowed_expiry(anon_enabled):
    settings_row = FileTransferSettings.load()
    with pytest.raises(ValidationError):
        validate_anonymous_send_options(_options(expiry_choice='999'), settings_row)


def test_validate_anonymous_send_options_rejects_too_many_recipients(anon_enabled):
    settings_row = FileTransferSettings.load()
    settings_row.anonymous_max_recipients = 1
    settings_row.save()
    options = _options(recipients=['a@example.com', 'b@example.com'])
    with pytest.raises(ValidationError):
        validate_anonymous_send_options(options, settings_row)


def test_start_confirmation_requires_at_least_one_uploaded_file(anon_enabled, monkeypatch):
    _capture_email(monkeypatch)
    transfer = _draft(anon_enabled)
    with pytest.raises(ValidationError):
        start_confirmation(transfer, _options())


def test_start_confirmation_moves_to_pending_and_applies_options(
    anon_enabled, fake_storage, monkeypatch
):
    sent = _capture_email(monkeypatch)
    transfer = _draft(anon_enabled)
    _upload(transfer, fake_storage)

    result = start_confirmation(transfer, _options(message='hello there'))

    assert result.status == TransferStatus.PENDING_CONFIRMATION
    assert result.sender_email == 'sender@example.com'
    assert result.message == 'hello there'
    assert result.expires_at is not None
    assert result.email_verification_id is not None
    assert result.recipients.filter(email='recipient@example.com').exists()
    assert sent, 'confirmation email was not sent'
    assert sent[-1]['to'] == 'sender@example.com'


def test_start_confirmation_rejects_a_non_draft_transfer(anon_enabled, fake_storage, monkeypatch):
    _capture_email(monkeypatch)
    transfer = _draft(anon_enabled)
    _upload(transfer, fake_storage)
    start_confirmation(transfer, _options())
    with pytest.raises(ValidationError):
        start_confirmation(transfer, _options())


def test_confirm_by_code_activates_transfer_and_queues_emails(
    anon_enabled, fake_storage, monkeypatch, django_capture_on_commit_callbacks
):
    sent = _capture_email(monkeypatch)
    transfer = _draft(anon_enabled)
    _upload(transfer, fake_storage, size=2048)
    start_confirmation(transfer, _options())
    code = _extract_code(sent[-1]['text_body'])

    with django_capture_on_commit_callbacks(execute=True):
        confirmed = confirm_by_code(transfer, code, IP, COOKIE)

    assert confirmed.status == TransferStatus.ACTIVE
    assert confirmed.completed_at is not None
    assert confirmed.size_bytes == 2048
    assert confirmed.owner is None
    assert confirmed.last_billed_at is None  # anonymous transfers are never metered until claimed
    recipient = confirmed.recipients.get(email='recipient@example.com')
    assert recipient.last_sent_at is not None


def test_confirm_by_code_wrong_code_raises_incorrect_code(anon_enabled, fake_storage, monkeypatch):
    _capture_email(monkeypatch)
    transfer = _draft(anon_enabled)
    _upload(transfer, fake_storage)
    start_confirmation(transfer, _options())

    with pytest.raises(IncorrectCode):
        confirm_by_code(transfer, '000000', IP, COOKIE)

    transfer.refresh_from_db()
    assert transfer.status == TransferStatus.PENDING_CONFIRMATION


def test_confirm_by_link_activates_transfer(anon_enabled, fake_storage, monkeypatch):
    sent = _capture_email(monkeypatch)
    transfer = _draft(anon_enabled)
    _upload(transfer, fake_storage)
    start_confirmation(transfer, _options())
    token = _extract_link_token(sent[-1]['text_body'])

    confirmed = confirm_by_link(transfer, token, IP, COOKIE)
    assert confirmed.status == TransferStatus.ACTIVE


def test_confirm_by_link_is_idempotent_for_an_already_active_transfer(
    anon_enabled, fake_storage, monkeypatch
):
    sent = _capture_email(monkeypatch)
    transfer = _draft(anon_enabled)
    _upload(transfer, fake_storage)
    start_confirmation(transfer, _options())
    token = _extract_link_token(sent[-1]['text_body'])

    confirm_by_link(transfer, token, IP, COOKIE)
    # A second click (email prefetch, or a genuine double-click) must not error.
    again = confirm_by_link(transfer, token, IP, COOKIE)
    assert again.status == TransferStatus.ACTIVE


def test_resend_confirmation_respects_cooldown(anon_enabled, fake_storage, monkeypatch):
    _capture_email(monkeypatch)
    transfer = _draft(anon_enabled)
    _upload(transfer, fake_storage)
    start_confirmation(transfer, _options())

    with pytest.raises(ResendTooSoon):
        resend_confirmation(transfer)


def test_resend_confirmation_issues_a_new_code(anon_enabled, fake_storage, monkeypatch):
    sent = _capture_email(monkeypatch)
    transfer = _draft(anon_enabled)
    _upload(transfer, fake_storage)
    start_confirmation(transfer, _options())
    old_verification_id = transfer.email_verification_id

    from apps.core.models import CoreSettings

    core_settings = CoreSettings.load()
    core_settings.email_verification_resend_cooldown_seconds = 0
    core_settings.save()

    transfer.refresh_from_db()
    resend_confirmation(transfer)
    transfer.refresh_from_db()
    assert transfer.email_verification_id != old_verification_id

    code = _extract_code(sent[-1]['text_body'])
    confirmed = confirm_by_code(transfer, code, IP, COOKIE)
    assert confirmed.status == TransferStatus.ACTIVE


def test_confirm_by_code_expired_verification_raises(anon_enabled, fake_storage, monkeypatch):
    from datetime import timedelta

    from django.utils import timezone

    from apps.core.models import EmailVerification

    _capture_email(monkeypatch)
    transfer = _draft(anon_enabled)
    _upload(transfer, fake_storage)
    start_confirmation(transfer, _options())

    EmailVerification.objects.filter(pk=transfer.email_verification_id).update(
        expires_at=timezone.now() - timedelta(minutes=1)
    )

    with pytest.raises(EmailVerificationExpired):
        confirm_by_code(transfer, '123456', IP, COOKIE)


class TestPerIpCaps:
    def test_check_send_caps_enforces_transfer_count(self, anon_enabled):
        settings_row = FileTransferSettings.load()
        settings_row.anonymous_max_transfers_per_ip_per_day = 1
        settings_row.save()

        already_sent = Transfer.objects.create(
            owner=None,
            status=TransferStatus.ACTIVE,
            sender_ip=IP,
            anon_cookie_id=COOKIE,
        )
        Transfer.objects.filter(pk=already_sent.pk).update(completed_at=already_sent.created_at)
        already_sent.refresh_from_db()

        new_transfer = Transfer.objects.create(
            owner=None,
            status=TransferStatus.PENDING_CONFIRMATION,
            sender_ip=IP,
            anon_cookie_id='other',
        )
        with pytest.raises(ValidationError):
            anon_limits.check_send_caps(new_transfer, IP, 'other', settings_row)

    def test_check_send_caps_uses_whichever_of_ip_or_cookie_is_higher(self, anon_enabled):
        settings_row = FileTransferSettings.load()
        settings_row.anonymous_max_transfers_per_ip_per_day = 1
        settings_row.save()

        # Same cookie as the new transfer, different IP -- the cookie count alone should trip
        # the cap even though the IP is fresh.
        already_sent = Transfer.objects.create(
            owner=None,
            status=TransferStatus.ACTIVE,
            sender_ip='198.51.100.1',
            anon_cookie_id=COOKIE,
        )
        Transfer.objects.filter(pk=already_sent.pk).update(completed_at=already_sent.created_at)

        new_transfer = Transfer.objects.create(
            owner=None,
            status=TransferStatus.PENDING_CONFIRMATION,
            sender_ip=IP,
            anon_cookie_id=COOKIE,
        )
        with pytest.raises(ValidationError):
            anon_limits.check_send_caps(new_transfer, IP, COOKIE, settings_row)

    def test_check_send_caps_enforces_byte_cap(self, anon_enabled):
        settings_row = FileTransferSettings.load()
        settings_row.anonymous_max_bytes_per_ip_per_day = 1000
        settings_row.save()

        already_sent = Transfer.objects.create(
            owner=None,
            status=TransferStatus.ACTIVE,
            sender_ip=IP,
            anon_cookie_id=COOKIE,
            size_bytes=900,
        )
        Transfer.objects.filter(pk=already_sent.pk).update(completed_at=already_sent.created_at)

        new_transfer = Transfer.objects.create(
            owner=None,
            status=TransferStatus.PENDING_CONFIRMATION,
            sender_ip=IP,
            anon_cookie_id='other',
            size_bytes=200,
        )
        with pytest.raises(ValidationError):
            anon_limits.check_send_caps(new_transfer, IP, 'other', settings_row)

    def test_check_upload_bytes_cap_counts_in_flight_drafts(self, anon_enabled, fake_storage):
        settings_row = FileTransferSettings.load()
        settings_row.anonymous_max_bytes_per_ip_per_day = 1000
        settings_row.save()

        other_draft = Transfer.objects.create(
            owner=None,
            status=TransferStatus.DRAFT,
            sender_ip=IP,
            anon_cookie_id='other-cookie',
            size_bytes=900,
        )
        transfer = Transfer.objects.create(
            owner=None, status=TransferStatus.DRAFT, sender_ip=IP, anon_cookie_id=COOKIE
        )
        with pytest.raises(ValidationError):
            anon_limits.check_upload_bytes_cap(transfer, 200, IP, COOKIE, settings_row)

        # A byte-cap check against a cookie/IP with no other usage should pass.
        anon_limits.check_upload_bytes_cap(transfer, 200, 'fresh-ip', 'fresh-cookie', settings_row)
        assert other_draft.pk  # keep reference alive/used
