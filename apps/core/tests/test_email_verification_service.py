"""Unit tests for `apps.core.services.email_verification` (issue #58): the generic email
verification state machine that `accounts`' signup/email-change confirmation is ported onto, and
that a later `file_transfer` phase will reuse for anonymous senders.
"""

import uuid
from datetime import timedelta

import pytest
from django.core.exceptions import ValidationError
from django.utils import timezone

from apps.core.models import CoreSettings, EmailVerification
from apps.core.services.email_verification import (
    EmailVerificationAlreadyConfirmed,
    EmailVerificationBurned,
    EmailVerificationContext,
    EmailVerificationExpired,
    EmailVerificationNotFound,
    EmailVerificationSendFailed,
    EmailVerificationSuperseded,
    IncorrectCode,
    ResendTooSoon,
    confirm_by_code,
    confirm_by_token,
    confirm_by_token_verbose,
    invalidate,
    purge_old,
    start,
)

pytestmark = [pytest.mark.django_db, pytest.mark.unit]

PURPOSE = 'test.purpose'
OTHER_PURPOSE = 'test.other-purpose'


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
        settings_row.email_verification_code_length = 8
        settings_row.email_verification_validity_minutes = 10
        settings_row.save()

        captured = {}

        def build_email(context: EmailVerificationContext) -> tuple[str, str, str]:
            captured['code'] = context.code
            captured['validity_minutes'] = context.validity_minutes
            return 'subject', 'text', 'html'

        start('someone@example.com', PURPOSE, build_email=build_email)

        assert len(captured['code']) == 8
        assert captured['code'].isdigit()
        assert captured['validity_minutes'] == 10

        row = _latest_row()
        expected_expiry = timezone.now() + timedelta(minutes=10)
        assert abs((row.expires_at - expected_expiry).total_seconds()) < 5

    def test_validity_override_takes_precedence_over_settings(self):
        settings_row = CoreSettings.load()
        settings_row.email_verification_validity_minutes = 30
        settings_row.save()

        captured = {}

        def build_email(context: EmailVerificationContext) -> tuple[str, str, str]:
            captured['validity_minutes'] = context.validity_minutes
            return 'subject', 'text', 'html'

        start(
            'someone@example.com',
            PURPOSE,
            build_email=build_email,
            validity=timedelta(hours=48),
        )

        # The 48h override wins over the 30-minute setting.
        assert captured['validity_minutes'] == 48 * 60

        row = _latest_row()
        expected_expiry = timezone.now() + timedelta(hours=48)
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

    def test_resend_cooldown_is_case_insensitive_on_email(self):
        """`Foo@x.com` and `foo@x.com` must share the same cooldown -- otherwise the same address
        could dodge it by varying case (issue #58 follow-up)."""
        _start(email='Someone@Example.com')

        with pytest.raises(ResendTooSoon):
            _start(email='someone@example.com')

    def test_invalidate_previous_is_case_insensitive_on_email(self):
        """Likewise for "only the newest verification is valid": a resend in a different case
        must still invalidate the earlier one."""
        first_id = _start(email='Someone@Example.com')
        EmailVerification.objects.filter(pk=first_id).update(
            created_at=timezone.now() - timedelta(hours=1)
        )

        second_id = _start(email='someone@example.com')

        first = EmailVerification.objects.get(pk=first_id)
        assert first.invalidated_at is not None
        assert second_id != first_id

    def test_stored_email_keeps_the_caller_s_exact_case(self):
        """Case-insensitive matching must never change what's actually stored -- `accounts`
        round-trips this value straight into `User.objects.get(email=...)` (issue #58 follow-up:
        see the module docstring's note on `normalize_email` only lowercasing the domain)."""
        _start(email='MixedCase@Example.com')
        row = _latest_row(email='MixedCase@Example.com')
        assert row.email == 'MixedCase@Example.com'


class TestStartSendFailure:
    def test_all_backends_failing_rolls_back_and_raises(self, monkeypatch):
        monkeypatch.setattr(
            'apps.core.services.email_verification.send_email',
            lambda **kwargs: (0, 2),
        )

        with pytest.raises(EmailVerificationSendFailed) as excinfo:
            _start()

        assert excinfo.value.failures == 2
        assert not EmailVerification.objects.filter(
            email='someone@example.com', purpose=PURPOSE
        ).exists()

    def test_failed_resend_does_not_invalidate_the_previous_valid_link(self, monkeypatch):
        first_id = _start()
        EmailVerification.objects.filter(pk=first_id).update(
            created_at=timezone.now() - timedelta(hours=1)
        )

        monkeypatch.setattr(
            'apps.core.services.email_verification.send_email',
            lambda **kwargs: (0, 1),
        )
        with pytest.raises(EmailVerificationSendFailed):
            _start()

        first = EmailVerification.objects.get(pk=first_id)
        assert first.invalidated_at is None

    def test_failed_send_does_not_start_the_cooldown(self, monkeypatch):
        monkeypatch.setattr(
            'apps.core.services.email_verification.send_email',
            lambda **kwargs: (0, 1),
        )
        with pytest.raises(EmailVerificationSendFailed):
            _start()

        # A genuine (successful) send right after must not be rejected as "too soon" -- there was
        # never a row committed to measure a cooldown from.
        monkeypatch.setattr(
            'apps.core.services.email_verification.send_email',
            lambda **kwargs: (1, 0),
        )
        verification_id = _start()
        assert EmailVerification.objects.filter(pk=verification_id).exists()

    def test_partial_success_does_not_raise(self, monkeypatch):
        monkeypatch.setattr(
            'apps.core.services.email_verification.send_email',
            lambda **kwargs: (1, 1),
        )
        verification_id = _start()
        assert EmailVerification.objects.filter(pk=verification_id).exists()


class TestInvalidate:
    def test_invalidates_pending_rows_for_email_and_purpose(self):
        verification_id = _start()

        count = invalidate('someone@example.com', PURPOSE)

        assert count == 1
        row = EmailVerification.objects.get(pk=verification_id)
        assert row.invalidated_at is not None

    def test_does_not_touch_a_different_purpose_or_email(self):
        other_purpose_id = _start(email='someone@example.com', purpose=OTHER_PURPOSE)
        other_email_id = _start(email='someone-else@example.com', purpose=PURPOSE)

        invalidate('someone@example.com', PURPOSE)

        assert EmailVerification.objects.get(pk=other_purpose_id).invalidated_at is None
        assert EmailVerification.objects.get(pk=other_email_id).invalidated_at is None

    def test_does_not_touch_an_already_confirmed_row(self):
        captured = {}

        def build_email(context: EmailVerificationContext) -> tuple[str, str, str]:
            captured['code'] = context.code
            return 'subject', 'text', 'html'

        verification_id = start('someone@example.com', PURPOSE, build_email=build_email)
        confirm_by_code(verification_id, captured['code'], PURPOSE)

        invalidate('someone@example.com', PURPOSE)

        row = EmailVerification.objects.get(pk=verification_id)
        assert row.invalidated_at is None

    def test_is_case_insensitive_on_email(self):
        verification_id = _start(email='Someone@Example.com')

        count = invalidate('someone@example.com', PURPOSE)

        assert count == 1
        assert EmailVerification.objects.get(pk=verification_id).invalidated_at is not None

    def test_no_pending_rows_returns_zero(self):
        assert invalidate('nobody@example.com', PURPOSE) == 0


class TestConfirmByCode:
    def test_correct_code_confirms_and_returns_email(self):
        captured = {}

        def build_email(context: EmailVerificationContext) -> tuple[str, str, str]:
            captured['id_placeholder'] = context.code
            return 'subject', 'text', 'html'

        verification_id = start('someone@example.com', PURPOSE, build_email=build_email)
        code = captured['id_placeholder']

        email = confirm_by_code(verification_id, code, PURPOSE)

        assert email == 'someone@example.com'
        row = EmailVerification.objects.get(pk=verification_id)
        assert row.confirmed_at is not None

    def test_wrong_code_raises_incorrect_code_and_counts_attempt(self):
        verification_id = _start()

        with pytest.raises(IncorrectCode) as excinfo:
            confirm_by_code(verification_id, '000000', PURPOSE)

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
                confirm_by_code(verification_id, 'wrong', PURPOSE)

        with pytest.raises(EmailVerificationBurned):
            confirm_by_code(verification_id, 'wrong', PURPOSE)

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
            confirm_by_code(verification_id, 'wrong-code', PURPOSE)

        # Burned: even the real code no longer works.
        with pytest.raises(EmailVerificationBurned):
            confirm_by_code(verification_id, captured['code'], PURPOSE)

    def test_expired_raises(self):
        verification_id = _start()
        EmailVerification.objects.filter(pk=verification_id).update(
            expires_at=timezone.now() - timedelta(seconds=1)
        )

        with pytest.raises(EmailVerificationExpired):
            confirm_by_code(verification_id, '000000', PURPOSE)

    def test_already_confirmed_raises_not_idempotent(self):
        captured = {}

        def build_email(context: EmailVerificationContext) -> tuple[str, str, str]:
            captured['code'] = context.code
            return 'subject', 'text', 'html'

        verification_id = start('someone@example.com', PURPOSE, build_email=build_email)
        confirm_by_code(verification_id, captured['code'], PURPOSE)

        with pytest.raises(EmailVerificationAlreadyConfirmed):
            confirm_by_code(verification_id, captured['code'], PURPOSE)

    def test_superseded_raises(self):
        first_id = _start()
        EmailVerification.objects.filter(pk=first_id).update(
            created_at=timezone.now() - timedelta(hours=1)
        )
        _start()

        with pytest.raises(EmailVerificationSuperseded):
            confirm_by_code(first_id, '000000', PURPOSE)

    def test_unknown_id_raises_not_found(self):
        with pytest.raises(EmailVerificationNotFound):
            confirm_by_code(uuid.uuid4(), '000000', PURPOSE)

    def test_garbage_id_raises_not_found(self):
        with pytest.raises(EmailVerificationNotFound):
            confirm_by_code('not-a-uuid', '000000', PURPOSE)

    def test_wrong_purpose_raises_not_found(self):
        """A code/id minted for one purpose must never confirm a row scoped to another (issue
        #58 follow-up) -- indistinguishable from an unknown id."""
        captured = {}

        def build_email(context: EmailVerificationContext) -> tuple[str, str, str]:
            captured['code'] = context.code
            return 'subject', 'text', 'html'

        verification_id = start('someone@example.com', PURPOSE, build_email=build_email)

        with pytest.raises(EmailVerificationNotFound):
            confirm_by_code(verification_id, captured['code'], OTHER_PURPOSE)

        # The row is untouched -- the mismatched attempt was rejected before it could be
        # evaluated at all, so it must not count against the real purpose's attempt budget.
        row = EmailVerification.objects.get(pk=verification_id)
        assert row.attempts == 0
        assert row.confirmed_at is None

    def test_concurrent_wrong_guesses_cannot_bypass_the_attempt_counter(self, monkeypatch):
        """Reproduces the race the original implementation was vulnerable to, without real
        threads: `timezone.now()` is called once, after the row is read and before the attempts
        update, so patching it lets a fully independent, "concurrent" `confirm_by_code` call run
        and commit in between -- exactly the interleaving that two simultaneous requests reading
        the same stale `attempts` snapshot would produce.

        Before the fix, both calls would compute `attempts = 0 + 1 = 1` from their shared stale
        snapshot and both blindly write `1`, so two wrong guesses only ever cost the counter "1"
        -- letting an attacker fit unlimited guesses into what should be one attempt slot. With
        the fix, the second (stale) call's conditional update matches 0 rows and it fails closed
        (`EmailVerificationBurned`) instead of silently succeeding as an under-counted guess.
        """
        settings_row = CoreSettings.load()
        settings_row.email_verification_max_attempts = 5
        settings_row.save()

        verification_id = _start()
        triggered = {'done': False}
        real_now = timezone.now

        def racing_now():
            if not triggered['done']:
                triggered['done'] = True
                with pytest.raises(IncorrectCode):
                    confirm_by_code(verification_id, 'also-wrong', PURPOSE)
            return real_now()

        monkeypatch.setattr('apps.core.services.email_verification.timezone.now', racing_now)

        with pytest.raises(EmailVerificationBurned):
            confirm_by_code(verification_id, 'wrong', PURPOSE)

        row = EmailVerification.objects.get(pk=verification_id)
        # Only the "concurrent" call's guess was ever actually recorded -- the racing, stale call
        # was rejected outright rather than silently granted a free guess.
        assert row.attempts == 1

    def test_concurrent_correct_codes_cannot_both_succeed(self, monkeypatch):
        """Same technique as above, but for the `confirmed_at` write: two "concurrent" correct
        submissions must not both report success."""
        captured = {}

        def build_email(context: EmailVerificationContext) -> tuple[str, str, str]:
            captured['code'] = context.code
            return 'subject', 'text', 'html'

        verification_id = start('someone@example.com', PURPOSE, build_email=build_email)
        code = captured['code']

        triggered = {'done': False}
        real_now = timezone.now
        results = {}

        def racing_now():
            if not triggered['done']:
                triggered['done'] = True
                results['inner'] = confirm_by_code(verification_id, code, PURPOSE)
            return real_now()

        monkeypatch.setattr('apps.core.services.email_verification.timezone.now', racing_now)

        with pytest.raises(EmailVerificationAlreadyConfirmed):
            confirm_by_code(verification_id, code, PURPOSE)

        assert results['inner'] == 'someone@example.com'
        row = EmailVerification.objects.get(pk=verification_id)
        assert row.confirmed_at is not None


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

        email = confirm_by_token(token, PURPOSE)

        assert email == 'someone@example.com'
        row = _latest_row()
        assert row.confirmed_at is not None

    def test_confirming_the_same_token_twice_is_idempotent(self):
        token = self._start_and_capture_token()

        first = confirm_by_token(token, PURPOSE)
        second = confirm_by_token(token, PURPOSE)

        assert first == second == 'someone@example.com'

    def test_wrong_attempts_on_code_do_not_burn_the_token(self):
        settings_row = CoreSettings.load()
        settings_row.email_verification_max_attempts = 1
        settings_row.save()

        token = self._start_and_capture_token()
        row = _latest_row()

        # With max_attempts=1, a single wrong code already burns the row for `confirm_by_code`...
        with pytest.raises(EmailVerificationBurned):
            confirm_by_code(row.pk, 'wrong', PURPOSE)

        # ...but the token path never checks `attempts` at all, so the link still works.
        email = confirm_by_token(token, PURPOSE)
        assert email == 'someone@example.com'

    def test_expired_token_raises(self):
        token = self._start_and_capture_token()
        EmailVerification.objects.filter(pk=_latest_row().pk).update(
            expires_at=timezone.now() - timedelta(seconds=1)
        )

        with pytest.raises(EmailVerificationExpired):
            confirm_by_token(token, PURPOSE)

    def test_superseded_token_raises(self):
        old_token = self._start_and_capture_token()
        EmailVerification.objects.filter(pk=_latest_row().pk).update(
            created_at=timezone.now() - timedelta(hours=1)
        )
        self._start_and_capture_token()

        with pytest.raises(EmailVerificationSuperseded):
            confirm_by_token(old_token, PURPOSE)

    def test_unknown_token_raises_not_found(self):
        with pytest.raises(EmailVerificationNotFound):
            confirm_by_token('does-not-exist', PURPOSE)

    def test_wrong_purpose_raises_not_found(self):
        token = self._start_and_capture_token()

        with pytest.raises(EmailVerificationNotFound):
            confirm_by_token(token, OTHER_PURPOSE)

        row = _latest_row()
        assert row.confirmed_at is None

    def test_confirmed_token_past_expiry_raises_expired_not_idempotent_success(self):
        """The idempotent "same token twice" path is only for a link still within its validity
        window -- past `expires_at`, even an already-confirmed row must behave like an expired
        one (issue #58 follow-up: matches the old JWT link, which stopped validating once it
        expired rather than succeeding forever)."""
        token = self._start_and_capture_token()
        assert confirm_by_token(token, PURPOSE) == 'someone@example.com'

        EmailVerification.objects.filter(pk=_latest_row().pk).update(
            expires_at=timezone.now() - timedelta(seconds=1)
        )

        with pytest.raises(EmailVerificationExpired):
            confirm_by_token(token, PURPOSE)

    def test_concurrent_valid_confirms_both_report_success_idempotently(self, monkeypatch):
        """Unlike `confirm_by_code`, two truly concurrent *correct* token submissions are
        supposed to both succeed (the token path is deliberately idempotent) -- the second must
        fall back to the idempotent path rather than erroring or double-writing
        `confirmed_at`."""
        token = self._start_and_capture_token()
        triggered = {'done': False}
        real_now = timezone.now
        results = {}

        def racing_now():
            if not triggered['done']:
                triggered['done'] = True
                results['inner'] = confirm_by_token(token, PURPOSE)
            return real_now()

        monkeypatch.setattr('apps.core.services.email_verification.timezone.now', racing_now)

        outer = confirm_by_token(token, PURPOSE)

        assert results['inner'] == outer == 'someone@example.com'


class TestConfirmByTokenVerbose:
    """`confirm_by_token_verbose` is what `confirm_by_token` itself is now built on -- the same
    behaviour, plus the row's own id, which lets a caller whose token comes from a URL that names
    some other object (`file_transfer`'s anonymous-sender confirmation link) require that id to
    match what it stored when it called `start`, closing a token-swap between two objects sharing
    the same email+purpose (see `apps.file_transfer.services.anonymous`)."""

    def test_returns_the_email_and_the_row_id(self):
        captured = {}

        def build_email(context: EmailVerificationContext) -> tuple[str, str, str]:
            captured['token'] = context.token
            return 'subject', 'text', 'html'

        verification_id = start('someone@example.com', PURPOSE, build_email=build_email)

        result = confirm_by_token_verbose(captured['token'], PURPOSE)

        assert result.email == 'someone@example.com'
        assert result.verification_id == verification_id

    def test_confirm_by_token_is_a_thin_wrapper_around_it(self):
        captured = {}

        def build_email(context: EmailVerificationContext) -> tuple[str, str, str]:
            captured['token'] = context.token
            return 'subject', 'text', 'html'

        start('someone@example.com', PURPOSE, build_email=build_email)

        assert confirm_by_token(captured['token'], PURPOSE) == 'someone@example.com'


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


class TestCoreSettingsValidation:
    """`CoreSettings`' email-verification knobs must reject values that would break the service
    outright (issue #58 follow-up): a 0-digit code always matches, 0 max attempts burns every row
    on arrival, 0 minutes' validity expires a link before it can ever be used."""

    @pytest.mark.parametrize(
        'field,value',
        [
            ('email_verification_code_length', 5),
            ('email_verification_code_length', 11),
            ('email_verification_max_attempts', 0),
            ('email_verification_max_attempts', 11),
            ('email_verification_validity_minutes', 0),
            ('email_verification_resend_cooldown_seconds', -1),
        ],
    )
    def test_out_of_bounds_values_fail_validation(self, field, value):
        settings_row = CoreSettings.load()
        setattr(settings_row, field, value)

        with pytest.raises(ValidationError):
            settings_row.full_clean()

    @pytest.mark.parametrize(
        'field,value',
        [
            ('email_verification_code_length', 6),
            ('email_verification_code_length', 10),
            ('email_verification_max_attempts', 1),
            ('email_verification_max_attempts', 10),
            ('email_verification_validity_minutes', 1),
            ('email_verification_resend_cooldown_seconds', 0),
        ],
    )
    def test_boundary_values_pass_validation(self, field, value):
        settings_row = CoreSettings.load()
        setattr(settings_row, field, value)

        settings_row.full_clean()
