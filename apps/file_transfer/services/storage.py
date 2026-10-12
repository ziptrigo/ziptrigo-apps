"""Transfer file storage (spec section 11): the key shapes and the `FILE_TRANSFER_*` wiring on top
of the generic `apps.core.services.storage.S3Storage`.

Keys: `transfers/<transfer_id>/<file_id>` -- deliberately *not* `.../<filename>` (a deviation from
the spec's literal key shape, noted in `CLAUDE.md`): a user-supplied name can contain `/`, `..`,
quotes or other characters that would either add spurious key segments, get mangled by a
presigned URL's path normalization (breaking its signature), or need escaping to be safe inside
it. The display name lives only on `TransferFile.name` and is rendered into `Content-Disposition`
by `presigned_get_url` instead. One private bucket per environment (`FILE_TRANSFER_S3_*`
settings, provisioned in the `infra` repo -- see `CLAUDE.md`), CORS'd for direct browser
multipart PUTs -- which, since parts are uploaded with a per-part SHA-256 checksum (see
`S3Storage.presign_part_url`), must allow the `x-amz-checksum-sha256` request header and expose
the `ETag` response header for the browser to read back; a bucket lifecycle rule aborts
incomplete multipart uploads after a day as a backstop (`cleanup_drafts` is the primary
mechanism, spec section 10).

Everything that talks to S3 goes through `S3Storage` (re-exported here from `apps.core`, so
existing `from .storage import S3Storage` imports keep working), built from a boto3 client.
Callers get an instance via `get_storage()`; tests construct `S3Storage(bucket=..., client=<a
stub/mock>)` directly (or monkeypatch `_storage`, see the `fake_storage` fixture) so nothing here
ever needs the network in tests.
"""

from django.conf import settings

from apps.core.services.storage import (
    GET_URL_EXPIRES_SECONDS,
    PUT_URL_EXPIRES_SECONDS,
    ObjectInfo,
    S3Storage,
    build_client,
)

__all__ = [
    'GET_URL_EXPIRES_SECONDS',
    'PART_SIZE_BYTES',
    'PUT_URL_EXPIRES_SECONDS',
    'S3_MAX_PARTS',
    'S3_MIN_PART_SIZE_BYTES',
    'ObjectInfo',
    'S3Storage',
    'get_storage',
    'reset_storage_cache',
    'storage_key',
    'transfer_prefix',
]

# Client-side multipart part size (spec section 2 defaults): the browser splits a file into 64 MB
# parts. S3's own limits, for reference / validation: 5 MB minimum part size (except the last
# part) and 10,000 parts maximum.
PART_SIZE_BYTES = 64 * 1024 * 1024
S3_MIN_PART_SIZE_BYTES = 5 * 1024 * 1024
S3_MAX_PARTS = 10_000

# `GET_URL_EXPIRES_SECONDS` (presigned download URLs are short-lived, spec section 4) and
# `PUT_URL_EXPIRES_SECONDS` (a part-upload URL only has to outlive a slow upload of one 64 MB
# part) are defined in the core storage module and re-exported above.


def storage_key(transfer_id: object, file_id: object) -> str:
    """The S3 key for one transfer file, keyed by id only -- see the module docstring."""
    return f'transfers/{transfer_id}/{file_id}'


def transfer_prefix(transfer_id: object) -> str:
    """The S3 key prefix covering every object that belongs to a transfer."""
    return f'transfers/{transfer_id}/'


_storage: S3Storage | None = None


def get_storage() -> S3Storage:
    """The process-wide `S3Storage`, built lazily from settings."""
    global _storage
    if _storage is None:
        _storage = S3Storage(
            bucket=settings.FILE_TRANSFER_S3_BUCKET,
            client=build_client(
                region=settings.FILE_TRANSFER_S3_REGION,
                endpoint_url=settings.FILE_TRANSFER_S3_ENDPOINT_URL,
                access_key_id=settings.FILE_TRANSFER_AWS_ACCESS_KEY_ID,
                secret_access_key=settings.FILE_TRANSFER_AWS_SECRET_ACCESS_KEY,
            ),
        )
    return _storage


def reset_storage_cache() -> None:
    """Test-only: drop the cached client so the next `get_storage()` rebuilds it."""
    global _storage
    _storage = None
