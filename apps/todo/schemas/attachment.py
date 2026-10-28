"""Schemas for the attachment endpoints (`/api/todo/tasks/{number}/attachments`,
`/api/todo/attachments/{id}`)."""

from datetime import datetime
from uuid import UUID

from ninja import Schema
from pydantic import ConfigDict


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


class UploadStartSchema(Schema):
    """Body for `POST /tasks/{number}/attachments`. `checksum_sha256` is the base64 of the file's
    SHA-256 digest; it is bound into the presigned URL's signature, so S3 refuses a body that
    differs."""

    model_config = ConfigDict(extra='forbid')

    filename: str
    size: int
    content_type: str = ''
    checksum_sha256: str


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
