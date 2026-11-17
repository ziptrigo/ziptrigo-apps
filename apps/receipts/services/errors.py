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


class ExportCostChangedError(ValidationError):
    """What the export costs, worked out inside the transaction that would charge it, is not what
    the user confirmed (receipts were added or removed meanwhile, or the price changed). Nothing was
    charged or created; `quote` is the real cost, to confirm again."""

    MESSAGE = 'The cost changed. Please confirm the new total.'

    def __init__(self, quote: int) -> None:
        super().__init__(self.MESSAGE)
        self.quote = quote


class ExportInProgressError(ValidationError):
    """The user already has an export being prepared: one at a time."""

    MESSAGE = 'An export is already being prepared. Wait for it to finish first.'

    def __init__(self) -> None:
        super().__init__(self.MESSAGE)


class ExportPartMissingError(ValidationError):
    """The ready export has no such (built) zip file. (The API answers 404.)"""

    MESSAGE = 'There is no such file in this export.'

    def __init__(self) -> None:
        super().__init__(self.MESSAGE)
