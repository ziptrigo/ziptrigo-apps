from datetime import timedelta
from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError
from django.utils import timezone

from apps.billing.services import InsufficientCreditsError, get_balance, spend_credits

from ..models import (
    BlockedSender,
    BlockedSenderKind,
    Transfer,
    TransferRecipient,
    TransferStatus,
)
from ..services import actions

pytestmark = [pytest.mark.django_db, pytest.mark.unit]


def _active(transfer, **overrides):
    transfer.status = TransferStatus.ACTIVE
    for key, value in overrides.items():
        setattr(transfer, key, value)
    transfer.save()
    return transfer


def test_disable_requires_active_status(draft_transfer):
    with pytest.raises(ValidationError):
        actions.disable_transfer(draft_transfer)


def test_disable_then_reenable_round_trip(draft_transfer):
    _active(draft_transfer)
    actions.disable_transfer(draft_transfer)
    assert draft_transfer.status == TransferStatus.DISABLED

    actions.reenable_transfer_action(draft_transfer)
    assert draft_transfer.status == TransferStatus.ACTIVE


def test_reenable_disabled_also_requires_credit(draft_transfer):
    """Spec section 7's 1-credit minimum applies to re-enabling *any* paused transfer, not just a
    suspended one -- a disabled transfer resumes being metered the moment it's re-enabled, so an
    owner with zero credits shouldn't be able to un-pause one only to have it immediately
    suspended again on the next metering tick."""
    _active(draft_transfer, status=TransferStatus.DISABLED)
    spend_credits(draft_transfer.owner, get_balance(draft_transfer.owner), source='test')

    with pytest.raises(InsufficientCreditsError):
        actions.reenable_transfer_action(draft_transfer)
    assert draft_transfer.status == TransferStatus.DISABLED


def test_reenable_suspended_requires_credit(draft_transfer):
    _active(draft_transfer, status=TransferStatus.SUSPENDED, suspended_at=timezone.now())
    spend_credits(draft_transfer.owner, get_balance(draft_transfer.owner), source='test')

    with pytest.raises(InsufficientCreditsError):
        actions.reenable_transfer_action(draft_transfer)
    assert draft_transfer.status == TransferStatus.SUSPENDED


def test_reenable_suspended_succeeds_with_credit(draft_transfer):
    _active(draft_transfer, status=TransferStatus.SUSPENDED, suspended_at=timezone.now())
    actions.reenable_transfer_action(draft_transfer)
    assert draft_transfer.status == TransferStatus.ACTIVE
    assert draft_transfer.suspended_at is None


def test_reenable_rejects_active_transfer(draft_transfer):
    _active(draft_transfer)
    with pytest.raises(ValidationError):
        actions.reenable_transfer_action(draft_transfer)


def test_delete_transfer_now_is_terminal_and_deletes_files(
    draft_transfer, uploaded_file, fake_storage
):
    _active(draft_transfer)
    actions.delete_transfer_now(draft_transfer)
    assert draft_transfer.status == TransferStatus.DELETED
    assert draft_transfer.deleted_at is not None
    assert uploaded_file.storage_key not in fake_storage.objects


def test_delete_transfer_now_rejects_already_ended(draft_transfer):
    _active(draft_transfer, status=TransferStatus.DELETED, deleted_at=timezone.now())
    with pytest.raises(ValidationError):
        actions.delete_transfer_now(draft_transfer)


def test_set_expiry_extends_and_removes(draft_transfer):
    _active(draft_transfer)
    actions.set_expiry(draft_transfer, '5')
    assert draft_transfer.expires_at is not None

    actions.set_expiry(draft_transfer, 'none')
    assert draft_transfer.expires_at is None


def test_set_expiry_custom_requires_future_date(draft_transfer):
    _active(draft_transfer)
    with pytest.raises(ValidationError):
        actions.set_expiry(draft_transfer, 'custom', timezone.now() - timedelta(days=1))

    future = timezone.now() + timedelta(days=2)
    actions.set_expiry(draft_transfer, 'custom', future)
    assert draft_transfer.expires_at == future


def test_set_max_downloads_validates_minimum(draft_transfer):
    _active(draft_transfer)
    with pytest.raises(ValidationError):
        actions.set_max_downloads(draft_transfer, 0)

    actions.set_max_downloads(draft_transfer, 3)
    assert draft_transfer.max_downloads == 3

    actions.set_max_downloads(draft_transfer, None)
    assert draft_transfer.max_downloads is None


def test_set_and_remove_password(draft_transfer):
    _active(draft_transfer)
    actions.set_password(draft_transfer, 'sekret')
    assert draft_transfer.password_hash

    actions.remove_password(draft_transfer)
    assert draft_transfer.password_hash == ''


def test_set_notify_on_download_toggles(draft_transfer):
    _active(draft_transfer, notify_on_download=True)

    actions.set_notify_on_download(draft_transfer, False)
    assert draft_transfer.notify_on_download is False
    draft_transfer.refresh_from_db()
    assert draft_transfer.notify_on_download is False

    actions.set_notify_on_download(draft_transfer, True)
    assert draft_transfer.notify_on_download is True


def test_set_notify_on_download_rejects_ended_transfer(draft_transfer):
    with pytest.raises(ValidationError):
        actions.set_notify_on_download(draft_transfer, False)


def test_add_recipients_dedupes_against_existing(draft_transfer):
    _active(draft_transfer)
    TransferRecipient.objects.create(transfer=draft_transfer, email='a@example.com')

    created = actions.add_recipients(draft_transfer, ['a@example.com', 'b@example.com'])

    assert [r.email for r in created] == ['b@example.com']
    assert TransferRecipient.objects.filter(transfer=draft_transfer).count() == 2


def test_add_recipients_enforces_combined_max(draft_transfer, ft_settings):
    _active(draft_transfer)
    ft_settings.logged_in_max_recipients = 1
    ft_settings.save()
    TransferRecipient.objects.create(transfer=draft_transfer, email='a@example.com')

    with pytest.raises(ValidationError):
        actions.add_recipients(draft_transfer, ['b@example.com'])


def test_resend_recipient_email_updates_last_sent_at(draft_transfer):
    _active(draft_transfer)
    recipient = TransferRecipient.objects.create(transfer=draft_transfer, email='a@example.com')
    assert recipient.last_sent_at is None

    actions.resend_recipient_email(recipient)

    recipient.refresh_from_db()
    assert recipient.last_sent_at is not None


def test_resend_refused_during_cooldown_and_before_charge(draft_transfer):
    _active(draft_transfer)
    sent_at = timezone.now() - timedelta(minutes=3)
    recipient = TransferRecipient.objects.create(
        transfer=draft_transfer, email='a@example.com', last_sent_at=sent_at
    )
    charged: list[int] = []

    with pytest.raises(ValidationError, match='emailed recently'):
        actions.resend_recipient_email(recipient, charge=charged.append)

    recipient.refresh_from_db()
    assert charged == []  # a refused resend spends no budget
    assert recipient.last_sent_at == sent_at


def test_resend_allowed_after_cooldown_and_charges_one(draft_transfer):
    _active(draft_transfer)
    recipient = TransferRecipient.objects.create(
        transfer=draft_transfer,
        email='a@example.com',
        last_sent_at=timezone.now() - actions.RESEND_COOLDOWN - timedelta(seconds=1),
    )
    charged: list[int] = []

    actions.resend_recipient_email(recipient, charge=charged.append)

    assert charged == [1]


def test_add_recipients_charges_once_per_new_address(draft_transfer):
    _active(draft_transfer)
    TransferRecipient.objects.create(transfer=draft_transfer, email='a@example.com')
    charged: list[int] = []

    actions.add_recipients(
        draft_transfer, ['a@example.com', 'B@example.com', 'c@example.com'], charge=charged.append
    )

    assert charged == [2]  # the existing address isn't emailed, so isn't charged


def test_add_recipients_charge_failure_creates_nothing(draft_transfer):
    _active(draft_transfer)

    def refuse(count: int) -> None:
        raise RuntimeError('over budget')

    with pytest.raises(RuntimeError):
        actions.add_recipients(draft_transfer, ['a@example.com'], charge=refuse)

    assert not TransferRecipient.objects.filter(transfer=draft_transfer).exists()


def test_add_recipients_does_not_charge_when_nothing_is_new_or_invalid(draft_transfer):
    _active(draft_transfer)
    TransferRecipient.objects.create(transfer=draft_transfer, email='a@example.com')
    charged: list[int] = []

    assert actions.add_recipients(draft_transfer, ['a@example.com'], charge=charged.append) == []
    with pytest.raises(ValidationError, match='not a valid email'):
        actions.add_recipients(draft_transfer, ['not-an-email'], charge=charged.append)

    assert charged == []


def test_actions_reject_ended_transfer(draft_transfer):
    _active(draft_transfer, status=TransferStatus.EXPIRED)
    with pytest.raises(ValidationError):
        actions.set_max_downloads(draft_transfer, 1)
    with pytest.raises(ValidationError):
        actions.add_recipients(draft_transfer, ['a@example.com'])


# -- Hold freezes the transfer (issue #59 code review): while `held_for_review_at` is set, every
# owner action is refused except `disable_transfer`, which is deliberately left untouched. --


def test_held_transfer_blocks_add_recipients(draft_transfer):
    _active(draft_transfer, held_for_review_at=timezone.now())
    with pytest.raises(actions.TransferHeldError):
        actions.add_recipients(draft_transfer, ['a@example.com'])


def test_held_transfer_blocks_resend_recipient_email(draft_transfer):
    _active(draft_transfer, held_for_review_at=timezone.now())
    recipient = TransferRecipient.objects.create(transfer=draft_transfer, email='a@example.com')
    with pytest.raises(actions.TransferHeldError):
        actions.resend_recipient_email(recipient)


def test_held_transfer_blocks_set_expiry(draft_transfer):
    _active(draft_transfer, held_for_review_at=timezone.now())
    with pytest.raises(actions.TransferHeldError):
        actions.set_expiry(draft_transfer, '5')


def test_held_transfer_blocks_set_max_downloads(draft_transfer):
    _active(draft_transfer, held_for_review_at=timezone.now())
    with pytest.raises(actions.TransferHeldError):
        actions.set_max_downloads(draft_transfer, 3)


def test_held_transfer_blocks_set_password(draft_transfer):
    _active(draft_transfer, held_for_review_at=timezone.now())
    with pytest.raises(actions.TransferHeldError):
        actions.set_password(draft_transfer, 'sekret')


def test_held_transfer_blocks_delete_now(draft_transfer):
    _active(draft_transfer, held_for_review_at=timezone.now())
    with pytest.raises(actions.TransferHeldError):
        actions.delete_transfer_now(draft_transfer)


def test_held_transfer_blocks_reenable(draft_transfer):
    _active(draft_transfer, status=TransferStatus.DISABLED, held_for_review_at=timezone.now())
    with pytest.raises(actions.TransferHeldError):
        actions.reenable_transfer_action(draft_transfer)


def test_held_transfer_still_allows_disable(draft_transfer):
    """The one action a hold does *not* freeze -- taking the link down entirely stays available
    regardless."""
    _active(draft_transfer, held_for_review_at=timezone.now())
    actions.disable_transfer(draft_transfer)
    assert draft_transfer.status == TransferStatus.DISABLED


def test_transfer_held_error_is_a_validation_error():
    """So every existing `except ValidationError` call site (web dashboard views) already handles
    it correctly, same pattern as `blocklist.BlockedSenderError`."""
    assert issubclass(actions.TransferHeldError, ValidationError)


# -- Adding recipients, resending, and re-enabling are send paths too (issue #59 code review):
# blocked the same way starting a new transfer is. --


def test_add_recipients_blocked_by_owner_email(draft_transfer):
    _active(draft_transfer)
    BlockedSender.objects.create(kind=BlockedSenderKind.EMAIL, value=draft_transfer.owner.email)
    with pytest.raises(ValidationError):
        actions.add_recipients(draft_transfer, ['a@example.com'])


def test_add_recipients_blocked_by_ip(draft_transfer):
    _active(draft_transfer)
    BlockedSender.objects.create(kind=BlockedSenderKind.IP, value='203.0.113.9')
    with pytest.raises(ValidationError):
        actions.add_recipients(draft_transfer, ['a@example.com'], ip='203.0.113.9')


def test_resend_recipient_email_blocked_by_owner_email(draft_transfer):
    _active(draft_transfer)
    recipient = TransferRecipient.objects.create(transfer=draft_transfer, email='a@example.com')
    BlockedSender.objects.create(kind=BlockedSenderKind.EMAIL, value=draft_transfer.owner.email)
    with pytest.raises(ValidationError):
        actions.resend_recipient_email(recipient)


def test_reenable_blocked_by_owner_email(draft_transfer):
    _active(draft_transfer, status=TransferStatus.DISABLED)
    BlockedSender.objects.create(kind=BlockedSenderKind.EMAIL, value=draft_transfer.owner.email)
    with pytest.raises(ValidationError):
        actions.reenable_transfer_action(draft_transfer)


def test_add_recipients_not_blocked_when_sender_is_clean(draft_transfer):
    _active(draft_transfer)
    created = actions.add_recipients(draft_transfer, ['a@example.com'], ip='203.0.113.1')
    assert [r.email for r in created] == ['a@example.com']


def test_reenable_suspended_refused_when_balance_below_a_days_cost(
    draft_transfer, billing_settings
):
    """The finding's case: a 3-credit-a-day transfer and a 2-credit balance used to re-enable."""
    billing_settings.storage_price_per_gb_per_day = Decimal('3.0')
    billing_settings.save()
    _active(
        draft_transfer,
        status=TransferStatus.SUSPENDED,
        suspended_at=timezone.now(),
        size_bytes=1024**3,
    )
    spend_credits(draft_transfer.owner, get_balance(draft_transfer.owner) - 2, source='test')

    with pytest.raises(InsufficientCreditsError, match='at least 3 credits'):
        actions.reenable_transfer_action(draft_transfer)
    assert draft_transfer.status == TransferStatus.SUSPENDED


def test_reenable_disabled_refused_when_balance_below_a_days_cost(draft_transfer, billing_settings):
    """The DISABLED path needs a day's cost too -- it's the next charge the transfer incurs."""
    billing_settings.storage_price_per_gb_per_day = Decimal('3.0')
    billing_settings.save()
    _active(draft_transfer, status=TransferStatus.DISABLED, size_bytes=1024**3)
    spend_credits(draft_transfer.owner, get_balance(draft_transfer.owner) - 2, source='test')

    with pytest.raises(InsufficientCreditsError, match='at least 3 credits'):
        actions.reenable_transfer_action(draft_transfer)
    assert draft_transfer.status == TransferStatus.DISABLED


def test_reenable_refused_when_the_transfer_ended_since_it_was_read(draft_transfer):
    _active(draft_transfer, status=TransferStatus.SUSPENDED, suspended_at=timezone.now())
    Transfer.objects.filter(pk=draft_transfer.pk).update(status=TransferStatus.DELETED)

    with pytest.raises(ValidationError, match='can no longer be re-enabled'):
        actions.reenable_transfer_action(draft_transfer)
    draft_transfer.refresh_from_db()
    assert draft_transfer.status == TransferStatus.DELETED
