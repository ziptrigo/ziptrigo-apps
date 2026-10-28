"""Errors the To-do services raise for the web views (and, later, the API) to map."""

from django.core.exceptions import ValidationError

FROZEN_MESSAGE = 'Storage is paused: top up your credits to upload or download files.'


class AccountFrozenError(ValidationError):
    """The account's attachment storage could not be paid for, so uploads and downloads are
    refused until the balance is topped up. Nothing is deleted while frozen.

    A `ValidationError` so that a caller that doesn't care about the difference still shows the
    message, but it must be caught *before* a generic `ValidationError`: the web answers it with a
    "Top up" link and the API with a 402."""

    def __init__(self) -> None:
        super().__init__(FROZEN_MESSAGE, code='frozen')
