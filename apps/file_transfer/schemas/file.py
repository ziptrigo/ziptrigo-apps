"""Schemas for the JWT API's per-file upload endpoints (`/api/ft/transfers/{id}/files/...`, spec
section 14): the same add/presign/complete/resume shapes the web send page's JSON endpoints use
(`apps.file_transfer.views.uploads`), just typed for ninja instead of hand-parsed JSON.
"""

from uuid import UUID

from ninja import Schema


class AddFileSchema(Schema):
    name: str
    size: int
    #: The browser/CLI's notion of the file's last-modified time (ms since epoch), if it has one --
    #: stored so a later `.../resume/` call can be matched back to this file by name + size (+ this
    #: value) after a break in the upload. See `apps.file_transfer.services.uploads.add_file`.
    client_last_modified: int | None = None


class AddFileResponseSchema(Schema):
    file_id: UUID
    part_size_bytes: int
    part_count: int


class PartRequestSchema(Schema):
    part_number: int
    checksum_sha256: str


class PartUrlsRequestSchema(Schema):
    parts: list[PartRequestSchema]


class PartUrlsResponseSchema(Schema):
    #: Part number (as a string -- JSON object keys are always strings) -> presigned PUT URL.
    urls: dict[str, str]


class CompletedPartSchema(Schema):
    part_number: int
    etag: str
    checksum_sha256: str | None = None


class CompleteFileSchema(Schema):
    parts: list[CompletedPartSchema]


class UploadedPartSchema(Schema):
    part_number: int
    etag: str
    size: int
    #: S3 requires this checksum on every part when the upload was completed (see
    #: `apps.file_transfer.services.storage.S3Storage.complete_multipart_upload`'s docstring) --
    #: round-trip it back into `CompleteFileSchema`'s part list for a part the caller resumes
    #: without re-uploading (issue #55 phase 3 review's critical finding).
    checksum_sha256: str = ''


class OkSchema(Schema):
    ok: bool = True


class ResumeResponseSchema(Schema):
    """Response for `POST /transfers/{id}/files/{file_id}/resume/` (spec: resumable uploads --
    issue #55 phase 3). `restarted` is set when S3 no longer recognized the previous upload id (it
    was aborted, e.g. by the bucket's lifecycle rule) and a fresh one was started transparently;
    `uploaded_parts` is then always empty."""

    restarted: bool
    part_size_bytes: int
    part_count: int
    uploaded_parts: list[UploadedPartSchema]
