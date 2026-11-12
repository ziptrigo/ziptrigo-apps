"""Schemas for the attachment endpoints (`/api/todo/tasks/{number}/attachments`,
`/api/todo/attachments/{id}`)."""

from datetime import datetime
from uuid import UUID

from ninja import Schema
from pydantic import ConfigDict, Field


class AttachmentSchema(Schema):
    """An uploaded attachment. `task` is its task's number. `inline_type` is the type sniffed from
    the bytes when a browser may show the file inline (a PDF or an image), else empty."""

    id: UUID
    task: int
    filename: str
    size_bytes: int
    declared_type: str
    inline_type: str
    created_at: datetime


class CommentAttachmentSchema(Schema):
    """A file attached to a comment. `inline` says whether it may be shown in a browser (a PDF or
    an image, by what the bytes are: `inline_type` is the sniffed type, empty otherwise);
    download it with `GET /attachments/{id}/url`."""

    id: UUID = Field(description='The attachment id (`GET /attachments/{id}/url`).')
    filename: str = Field(description='The file name as uploaded (cleaned).')
    size_bytes: int = Field(description='The size in bytes.')
    inline_type: str = Field(
        description='The type sniffed from the bytes when a browser may show the file inline '
        '(PDF, JPEG, PNG, GIF, WebP), else empty.'
    )
    inline: bool = Field(
        description='Whether `GET /attachments/{id}/url?disposition=inline` works.'
    )


class UploadStartSchema(Schema):
    """Body for `POST /tasks/{number}/attachments`. `checksum_sha256` is the base64 of the file's
    SHA-256 digest; it is bound into the presigned URL's signature, so S3 refuses a body that
    differs.

    `for_comment` makes the file a **draft for a comment**: it is uploaded and completed the same
    way, shown with the comment (never in the task's own attachment list) once its id is sent in
    `attachment_ids` when the comment is added or edited, and allowed on a completed task (but not
    on one in the Trash). A draft nobody attaches is removed after a day; remove one yourself with
    `POST /attachments/{id}/discard`."""

    model_config = ConfigDict(extra='forbid')

    filename: str
    size: int
    content_type: str = ''
    checksum_sha256: str
    for_comment: bool = Field(
        default=False, description='Upload the file as a draft for a comment (see above).'
    )


class UploadTicketSchema(Schema):
    """What a client needs to upload one file: `PUT` the bytes to `url` with exactly these
    `headers` within `expires_in` seconds, then `POST .../attachments/{attachment_id}/complete`."""

    attachment_id: UUID
    url: str
    headers: dict[str, str]
    expires_in: int


class FileUrlSchema(Schema):
    """A short-lived presigned URL for an attachment. `disposition` is what the URL does:
    `inline` only when asked for and the file is a PDF or an image, else `attachment`."""

    url: str
    disposition: str
    filename: str
    expires_in: int
