"""Anonymous sending (spec section 2 phase 2): draft creation, options validation, email
confirmation (by code and by link), activation, and the per-IP-per-day caps.
"""

import re

import pytest
from django.contrib.sessions.backends.db import SessionStore
from django.core.exceptions import ValidationError

from apps.core.services.email_verification import (
    EmailVerificationError,
    EmailVerificationExpired,
    IncorrectCode,
    ResendTooSoon,
)

from ..models import FileTransferSettings, Transfer, TransferFile, TransferStatus
from ..services import anon_limits, anon_session
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

IP = '203.0.113.7'
COOKIE = 'cookie-id-1'


def _session() -> SessionStore:
    """A fresh, independent Django session for tests to pass to the session-based draft
    functions -- ownership is tracked by a per-draft token kept *inside* the session's own data
    (see `services.anon_session`), never by the session's own key, so a real `SessionStore` (not
    just a string) is needed to exercise that."""
    return SessionStore()


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
    return get_or_create_anonymous_draft(_session(), IP, COOKIE)


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
    session = _session()
    first = get_or_create_anonymous_draft(session, IP, COOKIE)
    second = get_or_create_anonymous_draft(session, IP, COOKIE)
    assert first.pk == second.pk
    assert first.owner is None
    assert first.draft_token_hash
    assert first.sender_ip == IP
    assert first.anon_cookie_id == COOKIE
    assert anon_session.owns_draft(session, first)


def test_get_or_create_anonymous_draft_does_not_reuse_a_draft_with_files(
    anon_enabled, fake_storage
):
    session = _session()
    first = get_or_create_anonymous_draft(session, IP, COOKIE)
    _upload(first, fake_storage)
    second = get_or_create_anonymous_draft(session, IP, COOKIE)
    assert second.pk != first.pk


def test_current_anonymous_transfer_ignores_other_sessions(anon_enabled):
    session = _session()
    get_or_create_anonymous_draft(session, IP, COOKIE)
    assert current_anonymous_transfer(_session()) is None
    assert current_anonymous_transfer(session) is not None


def test_current_anonymous_transfer_none_for_a_brand_new_session(anon_enabled):
    assert current_anonymous_transfer(_session()) is None


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
        """A draft's `size_bytes` column stays `0` until it's actually confirmed/sent (see
        `anon_limits.check_upload_bytes_cap`'s docstring) -- so the cap must sum the real,
        already-uploaded `TransferFile.size` of every other in-flight transfer instead, or an
        unconfirmed draft's uploads would never count against anyone's daily byte cap at all."""
        settings_row = FileTransferSettings.load()
        settings_row.anonymous_max_bytes_per_ip_per_day = 1000
        settings_row.save()

        other_draft = Transfer.objects.create(
            owner=None, status=TransferStatus.DRAFT, sender_ip=IP, anon_cookie_id='other-cookie'
        )
        other_file = add_file(other_draft, 'a.bin', 900, ip=IP, cookie_id='other-cookie')
        fake_storage.put_object(other_file.storage_key, 900)
        complete_file_upload(
            other_file, [{'PartNumber': 1, 'ETag': 'etag-1'}], storage=fake_storage
        )
        assert Transfer.objects.get(pk=other_draft.pk).size_bytes == 0  # still unconfirmed

        transfer = Transfer.objects.create(
            owner=None, status=TransferStatus.DRAFT, sender_ip=IP, anon_cookie_id=COOKIE
        )
        with pytest.raises(ValidationError):
            anon_limits.check_upload_bytes_cap(transfer, 200, IP, COOKIE, settings_row)

        # A byte-cap check against a cookie/IP with no other usage should pass.
        anon_limits.check_upload_bytes_cap(transfer, 200, 'fresh-ip', 'fresh-cookie', settings_row)

    def test_check_upload_bytes_cap_counts_files_not_yet_marked_uploaded(
        self, anon_enabled, fake_storage
    ):
        """A file whose multipart upload hasn't completed yet (`uploaded=False`) still occupies
        real S3 storage the moment it's created (`services.uploads.add_file` already started its
        multipart upload) -- it must count too, not just fully-completed ones."""
        settings_row = FileTransferSettings.load()
        settings_row.anonymous_max_bytes_per_ip_per_day = 1000
        settings_row.save()

        other_draft = Transfer.objects.create(
            owner=None, status=TransferStatus.DRAFT, sender_ip=IP, anon_cookie_id='other-cookie'
        )
        TransferFile.objects.create(
            transfer=other_draft, name='a.bin', size=900, storage_key='k', uploaded=False
        )

        transfer = Transfer.objects.create(
            owner=None, status=TransferStatus.DRAFT, sender_ip=IP, anon_cookie_id=COOKIE
        )
        with pytest.raises(ValidationError):
            anon_limits.check_upload_bytes_cap(transfer, 200, IP, COOKIE, settings_row)

    def test_check_send_caps_uses_the_transfers_own_recorded_ip_even_if_confirmed_elsewhere(
        self, anon_enabled
    ):
        """The bug this closes: a sender could previously blow past the cap entirely by
        confirming from a different network/browser with no history of its own -- `ip`/
        `cookie_id` here are the *confirming* request's, deliberately unrelated to the transfer's
        own recorded `sender_ip`/`anon_cookie_id` (`IP`/`COOKIE`), so if the check only looked at
        the confirming request's identifiers it would see zero usage and let this through."""
        settings_row = FileTransferSettings.load()
        settings_row.anonymous_max_transfers_per_ip_per_day = 1
        settings_row.save()

        already_sent = Transfer.objects.create(
            owner=None, status=TransferStatus.ACTIVE, sender_ip=IP, anon_cookie_id=COOKIE
        )
        Transfer.objects.filter(pk=already_sent.pk).update(completed_at=already_sent.created_at)

        new_transfer = Transfer.objects.create(
            owner=None,
            status=TransferStatus.PENDING_CONFIRMATION,
            sender_ip=IP,
            anon_cookie_id=COOKIE,
        )
        with pytest.raises(ValidationError):
            anon_limits.check_send_caps(
                new_transfer, 'confirming-from-a-fresh-ip', 'fresh-cookie', settings_row
            )

    def test_check_send_caps_still_considers_the_confirming_requests_own_identifiers_too(
        self, anon_enabled
    ):
        """The other half: a transfer drafted from one IP/cookie but confirmed from one that's
        itself over the cap must still be rejected -- checking only the transfer's own recorded
        identifiers would miss that."""
        settings_row = FileTransferSettings.load()
        settings_row.anonymous_max_transfers_per_ip_per_day = 1
        settings_row.save()

        confirming_ip = '198.51.100.9'
        already_sent = Transfer.objects.create(
            owner=None,
            status=TransferStatus.ACTIVE,
            sender_ip=confirming_ip,
            anon_cookie_id='some-other-cookie',
        )
        Transfer.objects.filter(pk=already_sent.pk).update(completed_at=already_sent.created_at)

        new_transfer = Transfer.objects.create(
            owner=None,
            status=TransferStatus.PENDING_CONFIRMATION,
            sender_ip='203.0.113.99',
            anon_cookie_id='drafting-cookie',
        )
        with pytest.raises(ValidationError):
            anon_limits.check_send_caps(new_transfer, confirming_ip, 'fresh-cookie', settings_row)


class TestCapsPreCheckedBeforeConfirmationIsSent:
    def test_start_confirmation_rejects_when_already_over_the_transfer_count_cap(
        self, anon_enabled, fake_storage, monkeypatch
    ):
        """Checking the caps only at confirmation (after the code/link is already sent) burns a
        one-time code on a transfer that can never actually be confirmed; `start_confirmation`
        pre-checks the same caps first so the sender gets a clear, immediate answer instead."""
        sent = _capture_email(monkeypatch)
        settings_row = FileTransferSettings.load()
        settings_row.anonymous_max_transfers_per_ip_per_day = 1
        settings_row.save()

        already_sent = Transfer.objects.create(
            owner=None, status=TransferStatus.ACTIVE, sender_ip=IP, anon_cookie_id=COOKIE
        )
        Transfer.objects.filter(pk=already_sent.pk).update(completed_at=already_sent.created_at)

        transfer = get_or_create_anonymous_draft(_session(), IP, COOKIE)
        _upload(transfer, fake_storage)

        with pytest.raises(ValidationError):
            start_confirmation(transfer, _options(), ip=IP, cookie_id=COOKIE)

        assert not sent, 'no confirmation email should have been sent'
        transfer.refresh_from_db()
        assert transfer.status == TransferStatus.DRAFT


class TestCapRejectionAtConfirmationLeavesATerminalState:
    def test_confirm_ends_the_transfer_rather_than_leaving_it_pending_forever(
        self, anon_enabled, fake_storage, monkeypatch
    ):
        """A cap rejection at actual confirmation time happens *after* the code/token was already
        burned (single-use) -- the transfer must not be left stuck `PENDING_CONFIRMATION` with no
        way for the sender to ever get a clear answer out of it."""
        sent = _capture_email(monkeypatch)
        transfer = _draft(anon_enabled)
        _upload(transfer, fake_storage)
        start_confirmation(transfer, _options())
        code = _extract_code(sent[-1]['text_body'])

        settings_row = FileTransferSettings.load()
        settings_row.anonymous_max_transfers_per_ip_per_day = 0
        settings_row.save()

        with pytest.raises(ValidationError):
            confirm_by_code(transfer, code, IP, COOKIE)

        transfer.refresh_from_db()
        assert transfer.status == TransferStatus.DELETED
        assert transfer.deleted_at is not None
        # The normal UI path (`views.anonymous._owned_pending`) only ever looks a transfer up by
        # `status=PENDING_CONFIRMATION`, so a `DELETED` one now 404s there instead of offering a
        # confirm box for a transfer that can never activate -- a clear, terminal answer rather
        # than a transfer stuck "awaiting confirmation" forever.


class TestConfirmationBoundToItsOwnTransfer:
    def test_two_pending_transfers_with_the_same_email_do_not_supersede_each_other(
        self, anon_enabled, fake_storage, monkeypatch
    ):
        sent = _capture_email(monkeypatch)

        transfer_a = get_or_create_anonymous_draft(_session(), IP, COOKIE)
        _upload(transfer_a, fake_storage, name='a.pdf')
        start_confirmation(transfer_a, _options(sender_email='same@example.com'))
        code_a = _extract_code(sent[-1]['text_body'])

        transfer_b = get_or_create_anonymous_draft(_session(), IP, 'other-cookie')
        _upload(transfer_b, fake_storage, name='b.pdf')
        start_confirmation(transfer_b, _options(sender_email='same@example.com'))
        code_b = _extract_code(sent[-1]['text_body'])

        # Before the per-transfer purpose fix, starting transfer_b's confirmation would have
        # invalidated transfer_a's still-pending verification (both shared one
        # `(email, purpose)` row in `core`) -- confirming transfer_a would then raise
        # `EmailVerificationSuperseded` instead of succeeding.
        confirmed_a = confirm_by_code(transfer_a, code_a, IP, COOKIE)
        assert confirmed_a.status == TransferStatus.ACTIVE

        confirmed_b = confirm_by_code(transfer_b, code_b, IP, 'other-cookie')
        assert confirmed_b.status == TransferStatus.ACTIVE

    def test_confirm_by_link_cannot_be_replayed_against_a_different_transfer(
        self, anon_enabled, fake_storage, monkeypatch
    ):
        """Swapping the transfer id in a confirmation link's URL while keeping the original
        token must not activate the *other* transfer, even when both share a sender email (e.g.
        an attacker sets a malicious transfer's `sender_email` to the victim's address, hoping the
        victim's own confirmation link for their real transfer will activate the attacker's one
        instead)."""
        sent = _capture_email(monkeypatch)

        victim_transfer = get_or_create_anonymous_draft(_session(), IP, COOKIE)
        _upload(victim_transfer, fake_storage, name='real.pdf')
        start_confirmation(victim_transfer, _options(sender_email='victim@example.com'))
        token = _extract_link_token(sent[-1]['text_body'])

        attacker_transfer = get_or_create_anonymous_draft(_session(), 'attacker-ip', 'attacker-c')
        _upload(attacker_transfer, fake_storage, name='malicious.pdf')
        start_confirmation(attacker_transfer, _options(sender_email='victim@example.com'))

        with pytest.raises(EmailVerificationError):
            confirm_by_link(attacker_transfer, token, 'attacker-ip', 'attacker-c')

        attacker_transfer.refresh_from_db()
        assert attacker_transfer.status == TransferStatus.PENDING_CONFIRMATION

        # The real link still works against the transfer it was actually issued for.
        confirmed = confirm_by_link(victim_transfer, token, IP, COOKIE)
        assert confirmed.status == TransferStatus.ACTIVE
