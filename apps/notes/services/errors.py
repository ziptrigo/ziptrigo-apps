"""Errors the Notes services raise that callers tell apart from a plain `ValidationError`."""

from django.core.exceptions import ValidationError


class CostChangedError(ValidationError):
    """What the action costs, worked out inside the transaction that would charge it, is not what
    the caller confirmed (another request used up a free slot, or a price changed). Nothing was
    charged or changed; `quote` is the real cost, to confirm again. (Short URLs' class of the
    same name, with a whole number of credits for a quote.)"""

    MESSAGE = 'The cost changed. Please confirm the new total.'

    def __init__(self, quote: int) -> None:
        super().__init__(self.MESSAGE)
        self.quote = quote


class NoteConflictError(ValidationError):
    """The note was saved by someone else after the editor opened it (`base_version` is stale).
    Nothing was written. Carries what is stored now, so the editor can merge by hand."""

    MESSAGE = 'This note was changed by someone else while you were editing.'

    def __init__(self, *, current_version: int, current_title: str, current_content: str) -> None:
        super().__init__(self.MESSAGE)
        self.current_version = current_version
        self.current_title = current_title
        self.current_content = current_content


class LinkUnavailableError(ValidationError):
    """The share link a save came through can no longer be used (revoked, expired, or no longer an
    editable link) by the time the write ran under its locks. Nothing was written."""

    MESSAGE = 'This link is no longer available.'

    def __init__(self) -> None:
        super().__init__(self.MESSAGE)


class ReportUnavailableError(LinkUnavailableError):
    """The note a report was for stopped being shared (its link was revoked or expired, or the note
    was trashed, locked or taken down) between the page being served and the report arriving.
    Nothing was recorded. The same neutral page answers it as for any link that doesn't work."""

    MESSAGE = 'This note is no longer available.'
