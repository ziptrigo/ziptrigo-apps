"""Schemas for the direct-to-S3 upload endpoints (`POST /uploads`, `.../complete`)."""

from uuid import UUID

from ninja import Schema


class UploadStartSchema(Schema):
    """Body for `POST /uploads`. `checksum_sha256` is the base64 of the file's SHA-256 digest; it
    is bound into the presigned URL's signature, so S3 refuses a body that differs."""

    filename: str
    size: int
    content_type: str = ''
    checksum_sha256: str


class UploadTicketSchema(Schema):
    """What a client needs to upload one file: `PUT` the bytes to `url` with exactly these
    `headers` (and a `Content-Length` of the declared size) within `expires_in` seconds, then call
    `POST /uploads/{receipt_id}/complete`."""

    receipt_id: UUID
    url: str
    headers: dict[str, str]
    expires_in: int
