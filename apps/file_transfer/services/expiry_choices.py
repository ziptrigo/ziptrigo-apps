"""The send page's expiry dropdown (spec section 3): 1/5/15/30 days, a specific date, or no
expiration for logged-in senders. Anonymous senders only ever see the fixed day values, and which
ones are offered is itself admin-configurable (`FileTransferSettings.anonymous_allowed_expiry_days`)."""

from datetime import datetime, timedelta

from django.core.exceptions import ValidationError
from django.utils import timezone

from ..models import FileTransferSettings

FIXED_DAY_CHOICES = ('1', '5', '15', '30')
CUSTOM_CHOICE = 'custom'
NO_EXPIRATION_CHOICE = 'none'

EXPIRY_CHOICES = [
    ('1', '1 day'),
    ('5', '5 days'),
    ('15', '15 days'),
    ('30', '30 days'),
    (CUSTOM_CHOICE, 'Set a date'),
    (NO_EXPIRATION_CHOICE, 'No expiration'),
]


def resolve_expiry(choice: str, custom_date: datetime | None = None) -> datetime | None:
    """Turn a dropdown choice into `expires_at` (`None` means no expiration)."""
    if choice in FIXED_DAY_CHOICES:
        return timezone.now() + timedelta(days=int(choice))
    if choice == CUSTOM_CHOICE:
        if not custom_date:
            raise ValidationError('Provide an expiry date.')
        if custom_date <= timezone.now():
            raise ValidationError('Expiry date must be in the future.')
        return custom_date
    if choice == NO_EXPIRATION_CHOICE:
        return None
    raise ValidationError('Invalid expiry choice.')


def anonymous_expiry_choices(settings_row: FileTransferSettings) -> list[tuple[str, str]]:
    """The dropdown choices offered to an anonymous sender: only the fixed day values
    `FileTransferSettings.anonymous_allowed_expiry_days` allows, no custom date and no "no
    expiration" (an anonymous transfer is always free, so it must always have an end date)."""
    allowed = settings_row.anonymous_allowed_expiry_days_list()
    return [(str(days), f'{days} day{"s" if days != 1 else ""}') for days in allowed]


def resolve_expiry_anonymous(choice: str, settings_row: FileTransferSettings) -> datetime:
    """Like `resolve_expiry`, but restricted to `FileTransferSettings.anonymous_allowed_expiry_days`
    (spec section 3: "anonymous only sees the fixed values")."""
    allowed = {str(days) for days in settings_row.anonymous_allowed_expiry_days_list()}
    if choice not in allowed:
        raise ValidationError('Invalid expiry choice.')
    return timezone.now() + timedelta(days=int(choice))
