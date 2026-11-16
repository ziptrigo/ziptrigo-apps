"""Schemas of the JWT API's file request endpoints (`/api/ft/requests`, issue #306). Class names
carry the `FileRequest` prefix: the OpenAPI document keys a component by class name alone, and
`Recipient...`/`Transfer...` belong to the transfer endpoints."""

from datetime import datetime
from uuid import UUID

from ninja import Schema

from ..services.expiry_choices import NO_EXPIRATION_CHOICE
from .transfer import ExpiryDate


class FileRequestFileSchema(Schema):
    """One file uploaded to a request, with who uploaded it. The uploader's IP is never in the API
    (staff see it in the admin)."""

    id: UUID
    name: str
    size: int
    uploaded: bool
    uploaded_at: datetime | None
    uploader_name: str
    uploader_email: str
    created_at: datetime | None


class FileRequestInviteSchema(Schema):
    """One person a request is for (the audience `selected`): enough for a client to resend their
    invite email."""

    id: UUID
    email: str
    last_sent_at: datetime | None


class FileRequestSchema(Schema):
    """One file request, as every read and write endpoint returns it. Never the password."""

    id: UUID
    status: str
    title: str
    message: str
    upload_audience: str
    max_files: int | None
    max_total_size_bytes: int | None
    effective_max_files: int
    effective_max_total_size_bytes: int
    is_open: bool
    done_at: datetime | None
    done_by: str
    expires_at: datetime | None
    has_password: bool
    size_bytes: int
    file_count: int
    uploader_count: int
    download_count: int
    credits_charged: int
    url: str
    created_at: datetime | None
    files: list[FileRequestFileSchema]
    invites: list[FileRequestInviteSchema]

    @staticmethod
    def resolve_is_open(obj) -> bool:
        return obj.is_open

    @staticmethod
    def resolve_done_by(obj) -> str:
        if obj.done_at is None:
            return ''
        user = obj.done_by_user
        if user is not None:
            return user.name or user.email
        return obj.done_by_name

    @staticmethod
    def resolve_has_password(obj) -> bool:
        return bool(obj.password_hash)

    @staticmethod
    def resolve_file_count(obj) -> int:
        return obj.uploaded_file_count

    @staticmethod
    def resolve_url(obj) -> str:
        return obj.absolute_download_url

    @staticmethod
    def resolve_files(obj) -> list:
        return [f for f in obj.files.all() if f.uploaded]

    @staticmethod
    def resolve_invites(obj) -> list:
        return list(obj.invites.all())


class FileRequestListSchema(Schema):
    """A page of `FileRequestSchema`."""

    count: int
    limit: int
    offset: int
    results: list[FileRequestSchema]


class FileRequestCreateSchema(Schema):
    """Body for `POST /requests`: what the web form collects, validated by the same service
    (`services.requests.create_request`). `emails` is the invitee list for the audience
    `selected` (and ignored otherwise); `max_files` and `max_total_size_bytes` left out mean the
    site's maximum; the expiry is as in `PATCH /transfers`. `title` is optional (issue #318): up to
    100 characters, blank for none."""

    title: str = ''
    message: str = ''
    upload_audience: str = 'anyone'
    emails: list[str] = []
    max_files: int | None = None
    max_total_size_bytes: int | None = None
    expiry_choice: str = NO_EXPIRATION_CHOICE
    expiry_date: ExpiryDate = None
    password: str = ''


class FileRequestUpdateSchema(Schema):
    """Body for `PATCH /requests/{id}`. Every field is optional and only the ones present are
    applied, all or nothing (`payload.dict(exclude_unset=True)`): omitting a field leaves it as it
    is, an explicit `null` clears `max_files` and `max_total_size_bytes` (the site's maximum
    again). `title` may be blank to remove it. `emails` replaces the whole invitee list (new addresses are emailed). A blank
    `password` is a no-op; `remove_password: true` clears it."""

    title: str | None = None
    message: str | None = None
    upload_audience: str | None = None
    emails: list[str] | None = None
    max_files: int | None = None
    max_total_size_bytes: int | None = None
    expiry_choice: str | None = None
    expiry_date: ExpiryDate = None
    password: str | None = None
    remove_password: bool = False


class FileRequestFileUrlSchema(Schema):
    """A short-lived presigned download URL for one file of a request (never the storage key)."""

    url: str
    filename: str
    expires_in: int
