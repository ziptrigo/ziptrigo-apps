"""The errors the Short URLs services raise.

They are all Django's `ValidationError` (a web form shows them as a 422, the JWT API will map
them to a 400), with a field name as the key when the problem belongs to one field.
"""

from django.core.exceptions import ValidationError


def field_error(field: str, message: str) -> ValidationError:
    """A `ValidationError` for one field."""
    return ValidationError({field: message})


class AliasTakenError(ValidationError):
    """The alias is in use, reserved, or was once a link's code. Deliberately says nothing about
    whose it is (the API answers 409)."""

    def __init__(self) -> None:
        super().__init__({'alias': 'That alias is not available.'})


class LimitReachedError(ValidationError):
    """The owner already has the most links allowed (active and disabled ones count)."""

    def __init__(self, limit: int) -> None:
        super().__init__(f'You can have at most {limit:,} links. Delete one to make room.')


class CostChangedError(ValidationError):
    """What the action costs, worked out inside the transaction that would charge it, is not what
    the caller confirmed (another request used up the free allowance, or a price changed).
    Nothing was charged or changed; `quote` is the real cost, to confirm again."""

    def __init__(self, quote) -> None:
        super().__init__('The cost changed. Please confirm the new total.')
        self.quote = quote


class BlockedDomainError(ValidationError):
    """The destination's host is on the block list. A `ValidationError` subclass so every
    `except ValidationError` already handles it; the JWT API will catch this one first for a 403.
    Deliberately says nothing about *why* or that a block list exists."""

    MESSAGE = 'That address cannot be used.'

    def __init__(self) -> None:
        super().__init__({'destination': self.MESSAGE})


class LinkHeldError(ValidationError):
    """The link is held for abuse review: its owner can disable it and nothing else (the API
    answers 409). It says nothing about why."""

    MESSAGE = 'This link is under review, so it cannot be changed right now. You can disable it.'

    def __init__(self) -> None:
        super().__init__(self.MESSAGE)
