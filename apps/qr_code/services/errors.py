"""Errors the QR code sharing services raise that callers tell apart from a plain
`ValidationError`."""

from django.core.exceptions import NON_FIELD_ERRORS, ValidationError


class LinkUnavailableError(ValidationError):
    """The share link a request came through can no longer be used (revoked, expired, or its QR
    code deleted or taken down) by the time the write ran under its locks. Nothing was written."""

    MESSAGE = 'This link is no longer available.'

    def __init__(self) -> None:
        super().__init__(self.MESSAGE)


class ReportUnavailableError(LinkUnavailableError):
    """The QR code a report was for stopped being shared (its link was revoked or expired, or the
    code was deleted or taken down) between the page being served and the report arriving. Nothing
    was recorded. The same neutral page answers it as for any link that doesn't work."""

    MESSAGE = 'This QR code is no longer available.'


class QRCodeTakenDownError(ValidationError):
    """A share link, or an email of one, refused because staff took the QR code down. Final. Its
    own class so the API of a later PR can answer 409 without comparing message text. Same shape
    as the plain non-field `ValidationError` it stands for."""

    MESSAGE = 'This QR code was taken down and cannot be shared.'

    def __init__(self, message: str = MESSAGE) -> None:
        super().__init__({NON_FIELD_ERRORS: message})
