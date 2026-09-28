"""Thin wrapper around boto3 S3 for transfer file storage (spec section 11).

Keys: `transfers/<transfer_id>/<file_id>` -- deliberately *not* `.../<filename>` (a deviation from
the spec's literal key shape, noted in `CLAUDE.md`): a user-supplied name can contain `/`, `..`,
quotes or other characters that would either add spurious key segments, get mangled by a
presigned URL's path normalization (breaking its signature), or need escaping to be safe inside
it. The display name lives only on `TransferFile.name` and is rendered into `Content-Disposition`
by `presigned_get_url` instead. One private bucket per environment (`FILE_TRANSFER_S3_*`
settings, provisioned in the `infra` repo -- see `CLAUDE.md`), CORS'd for direct browser
multipart PUTs -- which, since parts are uploaded with a per-part SHA-256 checksum (see
`presign_part_url`), must allow the `x-amz-checksum-sha256` request header and expose the
`ETag` response header for the browser to read back; a bucket lifecycle rule aborts incomplete
multipart uploads after a day as a backstop (`cleanup_drafts` is the primary mechanism, spec
section 10).

Everything that talks to S3 goes through `S3Storage`, built from a boto3 client. Callers get an
instance via `get_storage()`; tests construct `S3Storage(client=<a stub/mock>)` directly (or
monkeypatch `get_storage`) so nothing here ever needs the network in tests.
"""

import logging
import unicodedata
from dataclasses import dataclass, field
from typing import IO, TYPE_CHECKING, cast
from urllib.parse import quote

import boto3
from botocore.exceptions import ClientError
from django.conf import settings

if TYPE_CHECKING:
    from mypy_boto3_s3 import S3Client

logger = logging.getLogger(__name__)

# Client-side multipart part size (spec section 2 defaults): the browser splits a file into 64 MB
# parts. S3's own limits, for reference / validation: 5 MB minimum part size (except the last
# part) and 10,000 parts maximum.
PART_SIZE_BYTES = 64 * 1024 * 1024
S3_MIN_PART_SIZE_BYTES = 5 * 1024 * 1024
S3_MAX_PARTS = 10_000

# Presigned download URLs are short-lived (spec section 4).
GET_URL_EXPIRES_SECONDS = 15 * 60
# Presigned part-upload URLs just need to outlive a slow upload of one 64 MB part.
PUT_URL_EXPIRES_SECONDS = 60 * 60

# Delete_objects accepts at most 1000 keys per call.
_DELETE_BATCH_SIZE = 1000


def storage_key(transfer_id: object, file_id: object) -> str:
    """The S3 key for one transfer file, keyed by id only -- see the module docstring."""
    return f'transfers/{transfer_id}/{file_id}'


def transfer_prefix(transfer_id: object) -> str:
    """The S3 key prefix covering every object that belongs to a transfer."""
    return f'transfers/{transfer_id}/'


def _content_disposition(filename: str) -> str:
    """RFC 6266 `Content-Disposition: attachment`, with an ASCII-safe `filename` fallback (for
    clients that don't understand `filename*`) alongside a percent-encoded UTF-8 `filename*` (for
    the ones that do, so non-ASCII names still come through correctly). Guards against header
    injection from a stored name containing quotes, backslashes or control characters -- those
    shouldn't be possible any more given `services.limits.validate_filename` at add-file time, but
    this is the last line of defense before the value reaches an HTTP header.
    """
    stripped = ''.join(ch for ch in filename if unicodedata.category(ch) != 'Cc')
    ascii_fallback = ''.join(ch if ch.isascii() else '_' for ch in stripped)
    ascii_fallback = ascii_fallback.replace('\\', '_').replace('"', "'") or 'download'
    encoded = quote(stripped.encode('utf-8'), safe='')
    return f'attachment; filename="{ascii_fallback}"; filename*=UTF-8\'\'{encoded}'


@dataclass(slots=True)
class ObjectInfo:
    """What `S3Storage.head_object` reports about an uploaded file."""

    size: int
    checksum_sha256: str


def build_client() -> 'S3Client':
    return boto3.client(
        's3',
        region_name=settings.FILE_TRANSFER_S3_REGION,
        endpoint_url=settings.FILE_TRANSFER_S3_ENDPOINT_URL,
        aws_access_key_id=settings.FILE_TRANSFER_AWS_ACCESS_KEY_ID or None,
        aws_secret_access_key=settings.FILE_TRANSFER_AWS_SECRET_ACCESS_KEY or None,
    )


@dataclass(slots=True)
class S3Storage:
    """S3 operations needed by the send/download flow, the dashboard and the background jobs."""

    client: 'S3Client' = field(default_factory=build_client)

    @property
    def bucket(self) -> str:
        return settings.FILE_TRANSFER_S3_BUCKET

    def create_multipart_upload(self, key: str) -> str:
        """Start a multipart upload with SHA-256 checksums and return its upload id."""
        response = self.client.create_multipart_upload(
            Bucket=self.bucket, Key=key, ChecksumAlgorithm='SHA256'
        )
        return response['UploadId']

    def presign_part_url(
        self,
        key: str,
        upload_id: str,
        part_number: int,
        expires_in: int = PUT_URL_EXPIRES_SECONDS,
        *,
        content_length: int | None = None,
        checksum_sha256: str | None = None,
    ) -> str:
        """A presigned URL the browser PUTs one part's bytes to directly.

        `content_length` and `checksum_sha256`, when given, are baked into the URL's signature
        (`generate_presigned_url` maps them to the `Content-Length` and `x-amz-checksum-sha256`
        request headers respectively) -- the PUT is then only valid for a body of exactly that
        size and that checksum, which is what actually enforces the declared file size and
        verifies the part's integrity server-side (S3 rejects the request outright if the header
        the browser sends doesn't match). `checksum_sha256` must be supplied by the caller (the
        browser computes it before requesting the URL, via `crypto.subtle`) -- it can't be
        computed here, since presigning happens before the bytes exist on this end at all.
        """
        params: dict = {
            'Bucket': self.bucket,
            'Key': key,
            'UploadId': upload_id,
            'PartNumber': part_number,
        }
        if content_length is not None:
            params['ContentLength'] = content_length
        if checksum_sha256 is not None:
            params['ChecksumSHA256'] = checksum_sha256
        return self.client.generate_presigned_url(
            'upload_part', Params=params, ExpiresIn=expires_in
        )

    def upload_part(self, key: str, upload_id: str, part_number: int, body: bytes) -> str:
        """Upload one part's bytes directly, server-side -- unlike `presign_part_url`, this part
        never goes through the browser. Used by the zip builder's streaming multipart writer
        (`apps.file_transfer.services.zip`, spec section 5). Returns the part's `ETag`."""
        response = self.client.upload_part(
            Bucket=self.bucket, Key=key, UploadId=upload_id, PartNumber=part_number, Body=body
        )
        return response['ETag']

    def get_object_stream(self, key: str) -> IO[bytes]:
        """A readable, streaming file-like object (`.read(n)`) for `key`'s bytes, for building
        the zip without ever loading a whole source file into memory. The real return value is
        botocore's `StreamingBody`, which satisfies `IO[bytes]` at runtime (`.read(amt)`) but
        isn't declared as one in `mypy_boto3_s3`'s stubs -- hence the `cast`."""
        body = self.client.get_object(Bucket=self.bucket, Key=key)['Body']
        return cast('IO[bytes]', body)

    def complete_multipart_upload(self, key: str, upload_id: str, parts: list[dict]) -> None:
        """Finish the upload. `parts` is `[{'PartNumber': n, 'ETag': etag}, ...]`, in order."""
        self.client.complete_multipart_upload(
            Bucket=self.bucket,
            Key=key,
            UploadId=upload_id,
            # `parts` comes straight from the browser's JSON (validated at the view layer, not
            # constructed as boto3-stubs' `CompletedPartTypeDef` TypedDict), so `ty` can't match
            # it structurally against `CompletedMultipartUploadTypeDef`.
            MultipartUpload={'Parts': parts},  # ty: ignore[invalid-argument-type]
        )

    def list_parts(self, key: str, upload_id: str) -> list[dict]:
        """List the parts already landed in S3 for an in-progress multipart upload (spec:
        resumable uploads) -- so a browser that reloaded mid-upload can ask what it already sent
        and only PUT the parts still missing, instead of starting the file over. Raises
        `ClientError` with code `NoSuchUpload` (via botocore) when S3 no longer knows the upload
        id at all (aborted by the bucket's lifecycle rule, or by `cleanup_drafts`); callers that
        care about that case (`apps.file_transfer.services.uploads.list_uploaded_parts`) catch it
        there rather than here, so this method stays a thin, faithful wrapper like every other one
        in this class.
        """
        parts: list[dict] = []
        paginator = self.client.get_paginator('list_parts')
        for page in paginator.paginate(Bucket=self.bucket, Key=key, UploadId=upload_id):
            for part in page.get('Parts', []):
                parts.append(
                    {'PartNumber': part['PartNumber'], 'ETag': part['ETag'], 'Size': part['Size']}
                )
        return parts

    def abort_multipart_upload(self, key: str, upload_id: str) -> None:
        """Abort an in-progress multipart upload. Safe to call on one already gone (the bucket's
        lifecycle rule, or a previous retry of the same cleanup job, may have beaten us to it)."""
        try:
            self.client.abort_multipart_upload(Bucket=self.bucket, Key=key, UploadId=upload_id)
        except ClientError as exc:
            logger.info('abort_multipart_upload(%s, %s) ignored: %s', key, upload_id, exc)

    def head_object(self, key: str) -> ObjectInfo:
        """Verify a file landed in S3 and read back its size and checksum."""
        response = self.client.head_object(Bucket=self.bucket, Key=key, ChecksumMode='ENABLED')
        return ObjectInfo(
            size=response['ContentLength'],
            checksum_sha256=response.get('ChecksumSHA256', ''),
        )

    def object_exists(self, key: str) -> bool:
        try:
            self.head_object(key)
        except ClientError:
            return False
        return True

    def presigned_get_url(
        self, key: str, filename: str, expires_in: int = GET_URL_EXPIRES_SECONDS
    ) -> str:
        """A short-lived download URL that forces "Save As" with the file's original name."""
        return self.client.generate_presigned_url(
            'get_object',
            Params={
                'Bucket': self.bucket,
                'Key': key,
                'ResponseContentDisposition': _content_disposition(filename),
            },
            ExpiresIn=expires_in,
        )

    def delete_object(self, key: str) -> None:
        self.client.delete_object(Bucket=self.bucket, Key=key)

    def delete_prefix(self, prefix: str) -> int:
        """Delete every object under `prefix` (a transfer's files, or its whole directory).
        Returns the number of objects deleted."""
        deleted = 0
        paginator = self.client.get_paginator('list_objects_v2')
        keys: list[dict] = []
        for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
            for obj in page.get('Contents', []):
                keys.append({'Key': obj['Key']})

        for start in range(0, len(keys), _DELETE_BATCH_SIZE):
            batch = keys[start : start + _DELETE_BATCH_SIZE]
            # Same reasoning as `complete_multipart_upload` above: `batch` is a plain
            # `list[dict]`, not boto3-stubs' `ObjectIdentifierTypeDef` TypedDict.
            self.client.delete_objects(
                Bucket=self.bucket,
                Delete={'Objects': batch},  # ty: ignore[invalid-argument-type]
            )
            deleted += len(batch)
        return deleted


_storage: S3Storage | None = None


def get_storage() -> S3Storage:
    """The process-wide `S3Storage`, built lazily from settings."""
    global _storage
    if _storage is None:
        _storage = S3Storage()
    return _storage


def reset_storage_cache() -> None:
    """Test-only: drop the cached client so the next `get_storage()` rebuilds it."""
    global _storage
    _storage = None
