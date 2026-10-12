"""Errors the Receipts services raise for the web views and the API to map (402, 400, ...)."""

from django.core.exceptions import ValidationError


class AccountFrozenError(Exception):
    """The account's storage could not be paid for, so previews, downloads, new uploads and
    processing are refused until the balance is topped up. (The API answers 402.)"""

    def __init__(self, message: str = 'Your receipts are paused until you add credits.') -> None:
        super().__init__(message)
        self.message = message


class DailyLimitExceededError(ValidationError):
    """The user already added the maximum number of receipts in the last 24 hours."""
