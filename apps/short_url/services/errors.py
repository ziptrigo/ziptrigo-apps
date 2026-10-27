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
    """The owner already has the most active links allowed."""

    def __init__(self, limit: int) -> None:
        super().__init__(
            f'You can have at most {limit:,} active links. Disable or delete one to make room.'
        )
