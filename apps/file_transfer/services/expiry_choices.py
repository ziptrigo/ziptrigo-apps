"""The send page's expiry dropdown (spec section 3): 1/5/15/30 days, a specific date, or no
expiration for logged-in senders."""

from datetime import datetime, timedelta

from django.core.exceptions import ValidationError
from django.utils import timezone

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
