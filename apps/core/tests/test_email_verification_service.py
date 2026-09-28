"""Unit tests for `apps.core.services.email_verification` (issue #58): the generic email
verification state machine that `accounts`' signup/email-change confirmation is ported onto, and
that a later `file_transfer` phase will reuse for anonymous senders.
"""

from datetime import timedelta

import pytest
from django.utils import timezone

from apps.core.models import CoreSettings, EmailVerification
from apps.core.services.email_verification import (
    EmailVerificationAlreadyConfirmed,
    EmailVerificationBurned,
    EmailVerificationContext,
    EmailVerificationExpired,
    EmailVerificationNotFound,
    EmailVerificationSuperseded,
    IncorrectCode,
    ResendTooSoon,
    confirm_by_code,
    confirm_by_token,
    purge_old,
    start,
)

pytestmark = [pytest.mark.django_db, pytest.mark.unit]

PURPOSE = 'test.purpose'


def _build_email(context: EmailVerificationContext) -> tuple[str, str, str]:
    return 'subject', f'code={context.code} token={context.token}', '<p>html</p>'


def _start(email: str = 'someone@example.com', purpose: str = PURPOSE, **kwargs):
    return start(email, purpose, build_email=_build_email, **kwargs)


def _latest_row(email: str = 'someone@example.com', purpose: str = PURPOSE) -> EmailVerification:
    return EmailVerification.objects.filter(email=email, purpose=purpose).latest('created_at')


class TestStart:
    def test_creates_a_row_and_sends_email(self, monkeypatch):
        sent = []
        monkeypatch.setattr(
            'apps.core.services.email_verification.send_email',
            lambda **kwargs: sent.append(kwargs) or (1, 0),
        )

        verification_id = _start()

        assert EmailVerification.objects.filter(pk=verification_id).exists()
        assert len(sent) == 1
        assert sent[0]['to'] == 'someone@example.com'
        assert sent[0]['subject'] == 'subject'

    def test_returns_the_row_id(self):
        verification_id = _start()
        assert EmailVerification.objects.filter(pk=verification_id).exists()

    def test_uses_configured_code_length_and_validity(self):
        settings_row = CoreSettings.load()
        settings_row.email_verification_code_length = 4
        settings_row.email_verification_validity_minutes = 10
        settings_row.save()

        captured = {}

        def build_email(context: EmailVerificationContext) -> tuple[str, str, str]:
            captured['code'] = context.code
            captured['validity_minutes'] = context.validity_minutes
            return 'subject', 'text', 'html'

        start('someone@example.com', PURPOSE, build_email=build_email)

        assert len(captured['code']) == 4
        assert captured['code'].isdigit()
        assert captured['validity_minutes'] == 10

        row = _latest_row()
        expected_expiry = timezone.now() + timedelta(minutes=10)
        assert abs((row.expires_at - expected_expiry).total_seconds()) < 5

    def test_invalidates_previous_pending_verification_for_same_email_and_purpose(self):
        first_id = _start()
        # Move the cooldown out of the way so the second `start()` isn't rejected.
        EmailVerification.objects.filter(pk=first_id).update(
            created_at=timezone.now() - timedelta(hours=1)
        )

        second_id = _start()

        first = EmailVerification.objects.get(pk=first_id)
        second = EmailVerification.objects.get(pk=second_id)
        assert first.invalidated_at is not None
        assert second.invalidated_at is None

    def test_does_not_invalidate_a_different_purpose_or_email(self):
        first_id = _start(email='someone@example.com', purpose=PURPOSE)
        EmailVerification.objects.filter(pk=first_id).update(
            created_at=timezone.now() - timedelta(hours=1)
        )
        _start(email='someone@example.com', purpose='other.purpose')
        _start(email='someone-else@example.com', purpose=PURPOSE)

        first = EmailVerification.objects.get(pk=first_id)
        assert first.invalidated_at is None

    def test_resend_within_cooldown_raises(self):
        _start()
        with pytest.raises(ResendTooSoon) as excinfo:
            _start()
        assert excinfo.value.retry_after_seconds > 0

    def test_resend_after_cooldown_succeeds(self):
        first_id = _start()
        settings_row = CoreSettings.load()
        cooldown = settings_row.email_verification_resend_cooldown_seconds
        EmailVerification.objects.filter(pk=first_id).update(
            created_at=timezone.now() - timedelta(seconds=cooldown + 1)
        )

        second_id = _start()
        assert second_id != first_id


class TestConfirmByCode:
    def test_correct_code_confirms_and_returns_email(self):
        captured = {}

        def build_email(context: EmailVerificationContext) -> tuple[str, str, str]:
            captured['id_placeholder'] = context.code
            return 'subject', 'text', 'html'

        verification_id = start('someone@example.com', PURPOSE, build_email=build_email)
        code = captured['id_placeholder']

        email = confirm_by_code(verification_id, code)

        assert email == 'someone@example.com'
        row = EmailVerification.objects.get(pk=verification_id)
        assert row.confirmed_at is not None

    def test_wrong_code_raises_incorrect_code_and_counts_attempt(self):
        verification_id = _start()

        with pytest.raises(IncorrectCode) as excinfo:
            confirm_by_code(verification_id, '000000')

        settings_row = CoreSettings.load()
        assert excinfo.value.attempts_remaining == settings_row.email_verification_max_attempts - 1
        row = EmailVerification.objects.get(pk=verification_id)
        assert row.attempts == 1

    def test_burns_after_max_attempts(self):
        settings_row = CoreSettings.load()
        settings_row.email_verification_max_attempts = 3
        settings_row.save()

        verification_id = _start()

        for _ in range(2):
            with pytest.raises(IncorrectCode):
                confirm_by_code(verification_id, 'wrong')

        with pytest.raises(EmailVerificationBurned):
            confirm_by_code(verification_id, 'wrong')

        row = EmailVerification.objects.get(pk=verification_id)
        assert row.attempts == 3

    def test_burned_rejects_even_the_correct_code(self):
        settings_row = CoreSettings.load()
        settings_row.email_verification_max_attempts = 1
        settings_row.save()

        captured = {}

        def build_email(context: EmailVerificationContext) -> tuple[str, str, str]:
            captured['code'] = context.code
            return 'subject', 'text', 'html'

        verification_id = start('someone@example.com', PURPOSE, build_email=build_email)

        with pytest.raises(EmailVerificationBurned):
            confirm_by_code(verification_id, 'wrong-code')

        # Burned: even the real code no longer works.
        with pytest.raises(EmailVerificationBurned):
            confirm_by_code(verification_id, captured['code'])

    def test_expired_raises(self):
        verification_id = _start()
        EmailVerification.objects.filter(pk=verification_id).update(
            expires_at=timezone.now() - timedelta(seconds=1)
        )

        with pytest.raises(EmailVerificationExpired):
            confirm_by_code(verification_id, '000000')

    def test_already_confirmed_raises_not_idempotent(self):
        captured = {}

        def build_email(context: EmailVerificationContext) -> tuple[str, str, str]:
            captured['code'] = context.code
            return 'subject', 'text', 'html'

        verification_id = start('someone@example.com', PURPOSE, build_email=build_email)
        confirm_by_code(verification_id, captured['code'])

        with pytest.raises(EmailVerificationAlreadyConfirmed):
            confirm_by_code(verification_id, captured['code'])

    def test_superseded_raises(self):
        first_id = _start()
        EmailVerification.objects.filter(pk=first_id).update(
            created_at=timezone.now() - timedelta(hours=1)
        )
        _start()

        with pytest.raises(EmailVerificationSuperseded):
            confirm_by_code(first_id, '000000')

    def test_unknown_id_raises_not_found(self):
        import uuid

        with pytest.raises(EmailVerificationNotFound):
            confirm_by_code(uuid.uuid4(), '000000')

    def test_garbage_id_raises_not_found(self):
        with pytest.raises(EmailVerificationNotFound):
            confirm_by_code('not-a-uuid', '000000')


class TestConfirmByToken:
    def _start_and_capture_token(self, email: str = 'someone@example.com') -> str:
        captured = {}

        def build_email(context: EmailVerificationContext) -> tuple[str, str, str]:
            captured['token'] = context.token
            return 'subject', 'text', 'html'

        start(email, PURPOSE, build_email=build_email)
        return captured['token']

    def test_correct_token_confirms_and_returns_email(self):
        token = self._start_and_capture_token('someone@example.com')

        email = confirm_by_token(token)

        assert email == 'someone@example.com'
        row = _latest_row()
        assert row.confirmed_at is not None

    def test_confirming_the_same_token_twice_is_idempotent(self):
        token = self._start_and_capture_token()

        first = confirm_by_token(token)
        second = confirm_by_token(token)

        assert first == second == 'someone@example.com'

    def test_wrong_attempts_on_code_do_not_burn_the_token(self):
        settings_row = CoreSettings.load()
        settings_row.email_verification_max_attempts = 1
        settings_row.save()

        token = self._start_and_capture_token()
        row = _latest_row()

        # With max_attempts=1, a single wrong code already burns the row for `confirm_by_code`...
        with pytest.raises(EmailVerificationBurned):
            confirm_by_code(row.pk, 'wrong')

        # ...but the token path never checks `attempts` at all, so the link still works.
        email = confirm_by_token(token)
        assert email == 'someone@example.com'

    def test_expired_token_raises(self):
        token = self._start_and_capture_token()
        EmailVerification.objects.filter(pk=_latest_row().pk).update(
            expires_at=timezone.now() - timedelta(seconds=1)
        )

        with pytest.raises(EmailVerificationExpired):
            confirm_by_token(token)

    def test_superseded_token_raises(self):
        old_token = self._start_and_capture_token()
        EmailVerification.objects.filter(pk=_latest_row().pk).update(
            created_at=timezone.now() - timedelta(hours=1)
        )
        self._start_and_capture_token()

        with pytest.raises(EmailVerificationSuperseded):
            confirm_by_token(old_token)

    def test_unknown_token_raises_not_found(self):
        with pytest.raises(EmailVerificationNotFound):
            confirm_by_token('does-not-exist')


class TestPurgeOld:
    def test_deletes_rows_older_than_cutoff_only(self):
        old_id = _start(email='old@example.com')
        EmailVerification.objects.filter(pk=old_id).update(
            created_at=timezone.now() - timedelta(days=31)
        )
        recent_id = _start(email='recent@example.com')

        deleted = purge_old(older_than=timedelta(days=30))

        assert deleted == 1
        assert not EmailVerification.objects.filter(pk=old_id).exists()
        assert EmailVerification.objects.filter(pk=recent_id).exists()
