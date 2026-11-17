"""Errors the export lifecycle raises for the web views and the API to map (402, 400, 409...).
Shared by every product's export (issue #315, moved here for the To-do export, issue #326); a
product's own `AccountFrozenError` stays with the product (its message says "your receipts" or
"your tasks")."""

from django.core.exceptions import ValidationError


class ExportCostChangedError(ValidationError):
    """What the export costs, worked out inside the transaction that would charge it, is not what
    the user confirmed (files were added or removed meanwhile, or the price changed). Nothing was
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
