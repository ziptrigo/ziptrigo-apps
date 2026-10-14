"""A generic, bucket-agnostic wrapper around boto3 S3, shared by every app that stores objects.

`S3Storage` is a dataclass over one bucket and one boto3 client; an app builds its own instance
from its own settings (see `apps.file_transfer.services.storage.get_storage`), so two apps can
use two buckets (or two sets of credentials) without sharing any state. Nothing in here knows what
a key means -- key shapes and the settings they come from belong to the caller.

Besides the multipart-upload primitives the browser/CLI-driven uploads use (`create_multipart_upload`
through `abort_multipart_upload`, with the per-part SHA-256 checksum handling described on
`presign_part_url`), it covers the small-object cases: `put_object` / `get_object_bytes` /
`copy_object` / `list_keys` for server-side reads and writes, and `presigned_put_url` for a
single presigned PUT (one object, no multipart) whose size and checksum are signed into the URL.

Tests construct `S3Storage(bucket=..., client=<a stub/mock>)` directly, so nothing here ever
needs the network.
"""

import logging
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass
from typing import IO, TYPE_CHECKING, Literal, cast
from urllib.parse import parse_qs, quote, urlsplit

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

if TYPE_CHECKING:
    from mypy_boto3_s3 import S3Client

logger = logging.getLogger(__name__)

# Presigned download URLs are short-lived.
GET_URL_EXPIRES_SECONDS = 15 * 60
# Presigned upload URLs just need to outlive a slow upload of one part (or one small object).
PUT_URL_EXPIRES_SECONDS = 60 * 60

# Delete_objects accepts at most 1000 keys per call.
_DELETE_BATCH_SIZE = 1000

# `ListObjectsV2` returns at most 1000 keys per page.
DEFAULT_LIST_MAX_KEYS = 1000

Disposition = Literal['attachment', 'inline']

# The only content types `presigned_get_url` will serve with `disposition='inline'` when given an
# explicit `content_type`: formats a browser renders without running script. Notably absent are
# `text/html` and `image/svg+xml`, which would execute script on the bucket's origin.
INLINE_CONTENT_TYPES = frozenset(
    {
        'application/pdf',
        'image/jpeg',
        'image/png',
        'image/webp',
        'image/gif',
        'text/plain',
    }
)


class UnsupportedSignatureVersionError(RuntimeError):
    """Raised by `S3Storage.presigned_put_url` / `presign_part_url` when the client isn't signing
    with SigV4, because under any other signature version the signed `Content-Length` (the URL's
    whole point) isn't enforced. Build the client with `build_client(...,
    signature_version='s3v4')`."""


def _require_signed(url: str, required: set[str], method: str) -> None:
    """Raise `UnsupportedSignatureVersionError` unless every header in `required` is among the
    presigned `url`'s `X-Amz-SignedHeaders` -- which only a SigV4 client produces. Inspects the
    URL rather than `client.meta.config.signature_version`, which reports `'s3v4'` even for a
    default client whose presigned URLs are SigV2."""
    signed = set(parse_qs(urlsplit(url).query).get('X-Amz-SignedHeaders', [''])[0].split(';'))
    if not required <= signed:
        raise UnsupportedSignatureVersionError(
            f'{method} needs a SigV4 client (build_client(..., '
            f"signature_version='s3v4')): the generated URL does not sign "
            f'{sorted(required - signed)}, so S3 would not enforce them'
        )


class ObjectTooLargeError(ValueError):
    """Raised by `S3Storage.get_object_bytes` when the object exceeds the caller's `max_bytes`."""


def content_disposition(filename: str, disposition: Disposition = 'attachment') -> str:
    """RFC 6266 `Content-Disposition` (`attachment` by default, or `inline`), with an ASCII-safe
    `filename` fallback (for clients that don't understand `filename*`) alongside a
    percent-encoded UTF-8 `filename*` (for the ones that do, so non-ASCII names still come
    through correctly). Guards against header injection from a stored name containing quotes,
    backslashes or control characters -- callers should already have validated the name, but this
    is the last line of defense before the value reaches an HTTP header.
    """
    if disposition not in ('attachment', 'inline'):
        raise ValueError(f'disposition must be "attachment" or "inline", not {disposition!r}')
    stripped = ''.join(ch for ch in filename if unicodedata.category(ch) != 'Cc')
    ascii_fallback = ''.join(ch if ch.isascii() else '_' for ch in stripped)
    ascii_fallback = ascii_fallback.replace('\\', '_').replace('"', "'") or 'download'
    encoded = quote(stripped.encode('utf-8'), safe='')
    return f'{disposition}; filename="{ascii_fallback}"; filename*=UTF-8\'\'{encoded}'


@dataclass(slots=True)
class ObjectInfo:
    """What `S3Storage.head_object` reports about an uploaded object."""

    size: int
    checksum_sha256: str


def build_client(
    region: str | None,
    endpoint_url: str | None,
    access_key_id: str | None,
    secret_access_key: str | None,
    *,
    signature_version: str | None = None,
) -> 'S3Client':
    """A boto3 S3 client. An empty `endpoint_url` / credential means "not set" (the default AWS
    endpoint, the default credential chain), so callers can pass settings values straight in.

    `signature_version=None` keeps botocore's default, which for presigned URLs is the legacy
    SigV2 in the older regions (`us-east-1`, `eu-west-1`, ...) and SigV4 elsewhere. Under SigV2 a
    presigned PUT's `Content-Length` is *not* part of the signature, so `presign_part_url` /
    `presigned_put_url` cannot enforce a declared size there; pass `'s3v4'` for a client whose
    presigned uploads must pin the body's size (and have `x-amz-checksum-sha256` signed as a
    header rather than carried as an unsigned-by-SigV2 query parameter). `presigned_put_url` and
    `presign_part_url` (when asked to sign a size or checksum) refuse to run on a client that
    isn't SigV4 (`UnsupportedSignatureVersionError`); file transfer and receipts both build theirs
    with `'s3v4'`.
    """
    return boto3.client(
        's3',
        region_name=region or None,
        endpoint_url=endpoint_url or None,
        aws_access_key_id=access_key_id or None,
        aws_secret_access_key=secret_access_key or None,
        config=Config(signature_version=signature_version) if signature_version else None,
    )


@dataclass(slots=True)
class S3Storage:
    """S3 operations on one bucket, through one boto3 client."""

    bucket: str
    client: 'S3Client'

    # -- Multipart uploads ------------------------------------------------------------------

    def create_multipart_upload(
        self, key: str, *, checksum_algorithm: str | None = 'SHA256'
    ) -> str:
        """Start a multipart upload and return its upload id.

        `checksum_algorithm` defaults to `'SHA256'` for the browser/CLI-driven uploads this class
        exists for (a client-computed per-part checksum, verified by S3, proves the bytes made it
        over the network intact). Once a multipart upload is created with a checksum algorithm,
        S3 requires a matching checksum on every `UploadPart` *and* on every entry of
        `CompleteMultipartUpload`'s part list (see `list_parts` and `complete_multipart_upload`'s
        docstrings) -- passing `checksum_algorithm=None` opts out of that requirement for a
        caller that uploads parts itself, server-side, over a connection S3 already authenticates
        and encrypts (`apps.file_transfer.services.zip`'s `_S3MultipartWriter`), where a second,
        client-side integrity check buys nothing.
        """
        # Two explicit calls rather than building a `dict` and spreading it in: unlike
        # `presign_part_url`'s `Params: dict`, `create_multipart_upload`'s keyword arguments are
        # individually typed by `mypy_boto3_s3`'s stubs, so spreading an untyped `dict` into them
        # would need the same kind of ignore-and-explain escape hatch `complete_multipart_upload`
        # already needs below for its own (genuinely unavoidable) untyped `Parts` list.
        if checksum_algorithm is not None:
            response = self.client.create_multipart_upload(
                Bucket=self.bucket,
                Key=key,
                # This method's own signature only accepts `str`, not the narrower
                # `mypy_boto3_s3`-stubs `Literal` `create_multipart_upload` wants -- callers pass a
                # plain string (`'SHA256'` by every caller so far), so there's nothing narrower to
                # type this parameter as without importing that stub's own literal type here.
                ChecksumAlgorithm=checksum_algorithm,  # ty: ignore[invalid-argument-type]
            )
        else:
            response = self.client.create_multipart_upload(Bucket=self.bucket, Key=key)
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
        request headers respectively) -- under SigV4 the PUT is then only valid for a body of
        exactly that size and that checksum, which enforces the declared part size and verifies
        the part's integrity server-side (S3 rejects the request outright if the header the
        browser sends doesn't match). **Under SigV2** (botocore's default in `us-east-1`,
        `eu-west-1` and other older regions, unless the client is built with
        `signature_version='s3v4'`) `Content-Length` is not signed and the checksum is only an
        unsigned query parameter, so neither would be enforced: when either is given this method
        fails closed with `UnsupportedSignatureVersionError`, like `presigned_put_url`
        (ziptrigo-apps#100). `checksum_sha256` must be supplied by the caller (the
        browser computes it before requesting the URL, via `crypto.subtle`) -- it can't be
        computed here, since presigning happens before the bytes exist on this end at all.
        """
        params: dict = {
            'Bucket': self.bucket,
            'Key': key,
            'UploadId': upload_id,
            'PartNumber': part_number,
        }
        required: set[str] = set()
        if content_length is not None:
            params['ContentLength'] = content_length
            required.add('content-length')
        if checksum_sha256 is not None:
            params['ChecksumSHA256'] = checksum_sha256
            required.add('x-amz-checksum-sha256')
        url = self.client.generate_presigned_url('upload_part', Params=params, ExpiresIn=expires_in)
        if required:
            _require_signed(url, required, 'presign_part_url')
        return url

    def upload_part(self, key: str, upload_id: str, part_number: int, body: bytes) -> str:
        """Upload one part's bytes directly, server-side -- unlike `presign_part_url`, this part
        never goes through the browser. Used by the zip builder's streaming multipart writer
        (`apps.file_transfer.services.zip`). Returns the part's `ETag`."""
        response = self.client.upload_part(
            Bucket=self.bucket, Key=key, UploadId=upload_id, PartNumber=part_number, Body=body
        )
        return response['ETag']

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
        """List the parts already landed in S3 for an in-progress multipart upload (resumable
        uploads) -- so a browser that reloaded mid-upload can ask what it already sent and only
        PUT the parts still missing, instead of starting the file over. Raises `ClientError` with
        code `NoSuchUpload` (via botocore) when S3 no longer knows the upload id at all (aborted
        by the bucket's lifecycle rule, or by a cleanup job); callers that care about that case
        (`apps.file_transfer.services.uploads.list_uploaded_parts`) catch it there rather than
        here, so this method stays a thin, faithful wrapper like every other one in this class.

        Each part's `ChecksumSHA256` (empty when the part somehow has none) is included alongside
        `PartNumber`/`ETag`/`Size` -- the upload was created with `ChecksumAlgorithm='SHA256'`
        (`create_multipart_upload`), so `CompleteMultipartUpload` later requires that checksum for
        *every* part, including ones a resumed upload skips re-sending (completing a resumed
        upload without it fails with `InvalidRequest`). Callers must round-trip it back into the
        part list they eventually complete with.
        """
        parts: list[dict] = []
        paginator = self.client.get_paginator('list_parts')
        for page in paginator.paginate(Bucket=self.bucket, Key=key, UploadId=upload_id):
            for part in page.get('Parts', []):
                parts.append(
                    {
                        'PartNumber': part['PartNumber'],
                        'ETag': part['ETag'],
                        'Size': part['Size'],
                        'ChecksumSHA256': part.get('ChecksumSHA256', ''),
                    }
                )
        return parts

    def abort_multipart_upload(self, key: str, upload_id: str) -> None:
        """Abort an in-progress multipart upload. Safe to call on one already gone (the bucket's
        lifecycle rule, or a previous retry of the same cleanup job, may have beaten us to it)."""
        try:
            self.client.abort_multipart_upload(Bucket=self.bucket, Key=key, UploadId=upload_id)
        except ClientError as exc:
            logger.info('abort_multipart_upload(%s, %s) ignored: %s', key, upload_id, exc)

    # -- Single objects ---------------------------------------------------------------------

    def put_object(self, key: str, body: bytes, *, content_type: str | None = None) -> None:
        """Write `body` to `key` in one request (server-side; for anything big enough to need
        parts, use the multipart methods). Overwrites an existing object."""
        if content_type is not None:
            self.client.put_object(Bucket=self.bucket, Key=key, Body=body, ContentType=content_type)
        else:
            self.client.put_object(Bucket=self.bucket, Key=key, Body=body)

    def copy_object(self, src_key: str, dst_key: str) -> None:
        """Copy `src_key` to `dst_key` within this bucket, server-side (S3 caps a single
        `CopyObject` at 5 GB). Overwrites an existing `dst_key`; the source is left in place."""
        self.client.copy_object(
            Bucket=self.bucket,
            Key=dst_key,
            CopySource={'Bucket': self.bucket, 'Key': src_key},
        )

    def get_object_bytes(self, key: str, *, max_bytes: int | None = None) -> bytes:
        """The whole object's bytes, in memory -- for small objects only; use
        `get_object_stream` for anything large. Raises `ClientError` (`NoSuchKey`) if absent.

        `max_bytes`, when given, bounds what is pulled into memory: an object whose
        `ContentLength` exceeds it raises `ObjectTooLargeError` before any of the body is read
        (and, should a response ever lack `ContentLength`, the read itself is capped at
        `max_bytes + 1` so the check still holds).
        """
        response = self.client.get_object(Bucket=self.bucket, Key=key)
        body = response['Body']
        if max_bytes is None:
            return body.read()
        if max_bytes < 0:
            raise ValueError('max_bytes must not be negative')
        length = response.get('ContentLength')
        if length is not None and length > max_bytes:
            body.close()
            raise ObjectTooLargeError(f'{key} is {length} bytes, over the {max_bytes} limit')
        data = body.read(max_bytes + 1)
        body.close()
        if len(data) > max_bytes:
            raise ObjectTooLargeError(f'{key} is over the {max_bytes} byte limit')
        return data

    def get_object_stream(self, key: str) -> IO[bytes]:
        """A readable, streaming file-like object (`.read(n)`) for `key`'s bytes, for building
        the zip without ever loading a whole source file into memory. The real return value is
        botocore's `StreamingBody`, which satisfies `IO[bytes]` at runtime (`.read(amt)`) but
        isn't declared as one in `mypy_boto3_s3`'s stubs -- hence the `cast`."""
        body = self.client.get_object(Bucket=self.bucket, Key=key)['Body']
        return cast('IO[bytes]', body)

    def head_object(self, key: str) -> ObjectInfo:
        """Verify an object landed in S3 and read back its size and checksum."""
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

    def list_keys(self, prefix: str, *, max_keys: int | None = DEFAULT_LIST_MAX_KEYS) -> list[str]:
        """The keys under `prefix` (`ListObjectsV2`, paginated transparently), in S3's
        lexicographic order, at most `max_keys` of them (`None` for no limit). The cap exists so
        a caller polling a prefix that unexpectedly holds a huge number of objects doesn't pull
        them all into memory.

        **The result may be incomplete**: it is a plain list with no truncation marker, so a
        result of exactly `max_keys` entries means "possibly more were cut off", not "that is
        all". That is fine for polling a prefix for new objects, but a caller that must see every
        object (cleanup, reconciliation) has to pass `max_keys=None`."""
        if max_keys is not None and max_keys < 1:
            raise ValueError('max_keys must be at least 1 (or None for no limit)')
        keys: list[str] = []
        paginator = self.client.get_paginator('list_objects_v2')
        config = {'MaxItems': max_keys} if max_keys is not None else {}
        for page in paginator.paginate(
            Bucket=self.bucket,
            Prefix=prefix,
            PaginationConfig=config,  # ty: ignore[invalid-argument-type]
        ):
            for obj in page.get('Contents', []):
                keys.append(obj['Key'])
        return keys

    # -- Presigned URLs ---------------------------------------------------------------------

    def presigned_put_url(
        self,
        key: str,
        *,
        content_length: int,
        checksum_sha256: str | None = None,
        content_type: str | None = None,
        expires_in: int = PUT_URL_EXPIRES_SECONDS,
    ) -> str:
        """A presigned URL for a single (non-multipart) PUT of exactly `content_length` bytes.

        The same signing trick as `presign_part_url`: `content_length` is signed into the URL as
        the `Content-Length` header, and `checksum_sha256` (base64, supplied by the caller since
        the bytes don't exist on this end) as `x-amz-checksum-sha256`, so under SigV4 S3 rejects
        a PUT whose body differs in size or content from what was declared. When `content_type`
        is given it is signed too, so the client must send that exact `Content-Type`.

        Fails closed: raises `UnsupportedSignatureVersionError` unless the URL it just generated
        actually has `content-length` (and `x-amz-checksum-sha256` / `content-type`, when given)
        among its signed headers, which only a SigV4 client does (`build_client(...,
        signature_version='s3v4')`). Under the legacy SigV2 presigning botocore falls back to in
        `us-east-1` / `eu-west-1` and other older regions, `Content-Length` is not signed at all,
        so the URL would accept a body of any size -- the one thing this method exists to prevent.
        The check inspects the generated URL rather than `client.meta.config.signature_version`,
        which reports `'s3v4'` even for a default client whose presigned URLs are SigV2.
        """
        params: dict = {'Bucket': self.bucket, 'Key': key, 'ContentLength': content_length}
        required = {'content-length'}
        if checksum_sha256 is not None:
            params['ChecksumSHA256'] = checksum_sha256
            required.add('x-amz-checksum-sha256')
        if content_type is not None:
            params['ContentType'] = content_type
            required.add('content-type')
        url = self.client.generate_presigned_url('put_object', Params=params, ExpiresIn=expires_in)
        _require_signed(url, required, 'presigned_put_url')
        return url

    def presigned_get_url(
        self,
        key: str,
        filename: str,
        expires_in: int = GET_URL_EXPIRES_SECONDS,
        *,
        disposition: Disposition = 'attachment',
        content_type: str | None = None,
    ) -> str:
        """A short-lived download URL. By default it forces "Save As" with `filename`
        (`disposition='attachment'`); `'inline'` lets the browser display it instead (a PDF, an
        image). `content_type`, when given, overrides the `Content-Type` S3 responds with.

        **Inline is a security-sensitive choice.** An `inline` response renders on the bucket's
        origin, so the content type must come from a trusted, server-determined value (sniffed
        or fixed on the server when the object was stored) -- never from user input or the
        uploader's claimed type -- and must never be `text/html` or `image/svg+xml`, which would
        run script. To make that hard to get wrong, an `inline` request with an explicit
        `content_type` outside `INLINE_CONTENT_TYPES` (PDF, JPEG, PNG, WebP, GIF, plain text)
        raises `ValueError`. With no `content_type`, S3 serves whatever type the object was
        stored with, which the caller must have already vetted.
        """
        if (
            disposition == 'inline'
            and content_type is not None
            and content_type.split(';')[0].strip().lower() not in INLINE_CONTENT_TYPES
        ):
            raise ValueError(f'content type {content_type!r} may not be served inline')
        params: dict = {
            'Bucket': self.bucket,
            'Key': key,
            'ResponseContentDisposition': content_disposition(filename, disposition),
        }
        if content_type is not None:
            params['ResponseContentType'] = content_type
        return self.client.generate_presigned_url('get_object', Params=params, ExpiresIn=expires_in)

    # -- Deletion ---------------------------------------------------------------------------

    def delete_object(self, key: str) -> None:
        self.client.delete_object(Bucket=self.bucket, Key=key)

    def delete_objects(self, keys: Iterable[str]) -> int:
        """Delete `keys` (up to 1000 per request). A key that does not exist is a no-op success,
        as with `delete_object`. If S3 reports a failure for any key the rest of the batch is
        still deleted and a `RuntimeError` naming the failed keys is raised afterwards. Returns the
        number of keys asked for."""
        key_list = list(keys)
        failed: list[str] = []
        for start in range(0, len(key_list), _DELETE_BATCH_SIZE):
            batch = [{'Key': key} for key in key_list[start : start + _DELETE_BATCH_SIZE]]
            response = self.client.delete_objects(
                Bucket=self.bucket,
                Delete={'Objects': batch, 'Quiet': True},  # ty: ignore[invalid-argument-type]
            )
            failed.extend(
                f'{error.get("Key")}: {error.get("Code")}' for error in response.get('Errors', [])
            )
        if failed:
            raise RuntimeError(f'S3 could not delete {len(failed)} object(s): {"; ".join(failed)}')
        return len(key_list)

    def delete_prefix(self, prefix: str) -> int:
        """Delete every object under `prefix`. Returns the number of objects deleted."""
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
