"""`email_verification.confirm_latest_by_code` and `confirm_by_token(..., single_use=True)`
(issue #295), the two additions the accounts sign-in code and link are built on."""

import re
from datetime import timedelta

import pytest
from django.utils import timezone

from apps.core.models import EmailVerification
from apps.core.services.email_verification import (
    EmailVerificationAlreadyConfirmed,
    EmailVerificationBurned,
    EmailVerificationExpired,
    EmailVerificationNotFound,
    EmailVerificationSuperseded,
    IncorrectCode,
    confirm_by_code,
    confirm_by_token,
    confirm_by_token_verbose,
    confirm_latest_by_code,
    invalidate,
    start,
)

pytestmark = [pytest.mark.django_db, pytest.mark.unit]

PURPOSE = 'test.otp'
EMAIL = 'Someone@example.com'


def _build(context):
    return 'subject', f'{context.code} {context.token}', '<p>html</p>'


def _start(email=EMAIL, purpose=PURPOSE):
    start(email, purpose, build_email=_build)
    row = EmailVerification.objects.filter(email=email, purpose=purpose).latest('created_at')
    return row


def _credentials(monkeypatch):
    """Start a verification and return its (code, token) as the email carried them."""
    sent = []
    monkeypatch.setattr(
        'apps.core.services.email_verification.send_email',
        lambda **kwargs: sent.append(kwargs) or (1, 0),
    )
    row = _start()
    match = re.match(r'(\d+) (\S+)', sent[-1]['text_body'])
    assert match is not None
    code, token = match.groups()
    return row, code, token


def _age(minutes=5):
    EmailVerification.objects.update(created_at=timezone.now() - timedelta(minutes=minutes))


class TestConfirmLatestByCode:
    def test_confirms_the_newest_row_and_returns_the_stored_email(self, monkeypatch):
        row, code, _ = _credentials(monkeypatch)

        assert confirm_latest_by_code('someone@EXAMPLE.com', code, PURPOSE) == EMAIL
        row.refresh_from_db()
        assert row.confirmed_at is not None

    def test_no_row_is_not_found(self):
        with pytest.raises(EmailVerificationNotFound):
            confirm_latest_by_code(EMAIL, '123456', PURPOSE)

    def test_another_purpose_is_not_found(self, monkeypatch):
        _, code, _ = _credentials(monkeypatch)

        with pytest.raises(EmailVerificationNotFound):
            confirm_latest_by_code(EMAIL, code, 'test.other')

    def test_a_wrong_code_counts_an_attempt_and_burns_the_row(self, monkeypatch):
        row, code, _ = _credentials(monkeypatch)

        with pytest.raises(IncorrectCode):
            confirm_latest_by_code(EMAIL, '000000' if code != '000000' else '111111', PURPOSE)
        row.refresh_from_db()
        assert row.attempts == 1
        for _ in range(4):
            with pytest.raises((IncorrectCode, EmailVerificationBurned)):
                confirm_latest_by_code(EMAIL, '000000' if code != '000000' else '111111', PURPOSE)
        with pytest.raises(EmailVerificationBurned):
            confirm_latest_by_code(EMAIL, code, PURPOSE)

    def test_only_the_newest_row_is_consulted(self, monkeypatch):
        old, old_code, _ = _credentials(monkeypatch)
        _age()
        new, new_code, _ = _credentials(monkeypatch)
        assert old.pk != new.pk

        if old_code != new_code:
            with pytest.raises(IncorrectCode):
                confirm_latest_by_code(EMAIL, old_code, PURPOSE)
        assert confirm_latest_by_code(EMAIL, new_code, PURPOSE) == EMAIL

    def test_single_use(self, monkeypatch):
        _, code, _ = _credentials(monkeypatch)
        confirm_latest_by_code(EMAIL, code, PURPOSE)

        with pytest.raises(EmailVerificationAlreadyConfirmed):
            confirm_latest_by_code(EMAIL, code, PURPOSE)

    def test_expired(self, monkeypatch):
        _, code, _ = _credentials(monkeypatch)
        EmailVerification.objects.update(expires_at=timezone.now() - timedelta(seconds=1))

        with pytest.raises(EmailVerificationExpired):
            confirm_latest_by_code(EMAIL, code, PURPOSE)

    def test_invalidated(self, monkeypatch):
        _, code, _ = _credentials(monkeypatch)
        invalidate(EMAIL, PURPOSE)

        with pytest.raises(EmailVerificationSuperseded):
            confirm_latest_by_code(EMAIL, code, PURPOSE)

    def test_matches_confirm_by_code_on_the_row(self, monkeypatch):
        row, code, _ = _credentials(monkeypatch)

        assert confirm_by_code(row.pk, code, PURPOSE) == EMAIL


class TestSingleUseToken:
    def test_the_default_stays_idempotent(self, monkeypatch):
        _, _, token = _credentials(monkeypatch)

        assert confirm_by_token(token, PURPOSE) == EMAIL
        assert confirm_by_token(token, PURPOSE) == EMAIL

    def test_single_use_refuses_a_second_use(self, monkeypatch):
        row, _, token = _credentials(monkeypatch)

        assert confirm_by_token_verbose(token, PURPOSE, single_use=True).verification_id == row.pk
        with pytest.raises(EmailVerificationAlreadyConfirmed):
            confirm_by_token(token, PURPOSE, single_use=True)

    def test_single_use_refuses_a_token_whose_code_was_used(self, monkeypatch):
        _, code, token = _credentials(monkeypatch)
        confirm_latest_by_code(EMAIL, code, PURPOSE)

        with pytest.raises(EmailVerificationAlreadyConfirmed):
            confirm_by_token(token, PURPOSE, single_use=True)

    def test_single_use_still_checks_purpose_expiry_and_supersession(self, monkeypatch):
        _, _, token = _credentials(monkeypatch)

        with pytest.raises(EmailVerificationNotFound):
            confirm_by_token(token, 'test.other', single_use=True)
        EmailVerification.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
        with pytest.raises(EmailVerificationExpired):
            confirm_by_token(token, PURPOSE, single_use=True)

    def test_a_lost_race_is_already_confirmed(self, monkeypatch):
        """Two correct submissions must not both succeed: simulate the other winning between the
        read and the conditional write."""
        row, _, token = _credentials(monkeypatch)
        real_now = timezone.now
        calls = {'n': 0}

        def racing_now():
            calls['n'] += 1
            if calls['n'] == 1:
                EmailVerification.objects.filter(pk=row.pk).update(confirmed_at=real_now())
            return real_now()

        monkeypatch.setattr('apps.core.services.email_verification.timezone.now', racing_now)

        with pytest.raises(EmailVerificationAlreadyConfirmed):
            confirm_by_token(token, PURPOSE, single_use=True)
