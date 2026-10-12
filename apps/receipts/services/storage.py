"""Receipts storage: the key shapes and the `RECEIPTS_*` wiring on top of the generic
`apps.core.services.storage.S3Storage`.

One private bucket per environment, shared with file transfer; everything Receipts stores is under
`receipts/` (the infra IAM user is scoped to that prefix):

- `receipts/inbound/`: where SES drops incoming mail, picked up by the poller (`services.inbound`).
  The infra lifecycle rule expires objects here after a while.
- `receipts/failed/`: where a message that could not be ingested is moved to, so it never sits in
  the polled prefix (a pile of failures would crowd new mail out of the listing). Not expired;
  the admin "retry" moves it back. Within `receipts/*`, so the IAM user may use it.
- `receipts/users/<user_id>/files/<receipt_id>`: the original file. Keyed by id only -- never the
  filename, which is user/sender controlled -- for the same reasons as file transfer's keys (see
  `apps.file_transfer.services.storage`); the display name lives on `Receipt.original_filename`
  and goes into `Content-Disposition`.
- `receipts/users/<user_id>/previews/<receipt_id>.jpg`: a JPEG rendering of a HEIC original.
- `receipts/users/<user_id>/emails/<email_id>.eml`: the attachment-stripped raw email.

Putting the user id in the path is what lets deleting an account delete all of its objects with
one `delete_prefix` (`user_prefix`).

The client signs with SigV4 (`signature_version='s3v4'`): the presigned direct uploads
(`S3Storage.presigned_put_url`) depend on the signed `Content-Length` being enforced, which
botocore's default does not do in the older regions, and `presigned_put_url` fails closed on a
client that does not sign it.

Tests replace the process-wide instance with an in-memory fake (see
`apps/receipts/tests/conftest.py`), so nothing here needs the network.
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
    'FAILED_PREFIX',
    'INBOUND_PREFIX',
    'PUT_URL_EXPIRES_SECONDS',
    'USERS_PREFIX',
    'ObjectInfo',
    'S3Storage',
    'email_key',
    'failed_key_for',
    'file_key',
    'get_storage',
    'preview_key',
    'reset_storage_cache',
    'user_prefix',
]

#: Where SES writes incoming messages.
INBOUND_PREFIX = 'receipts/inbound/'

#: Where messages that failed to ingest are parked.
FAILED_PREFIX = 'receipts/failed/'

USERS_PREFIX = 'receipts/users/'


def failed_key_for(inbound_key: str) -> str:
    """Where the object at `inbound_key` is parked when its message fails: the same name under
    `FAILED_PREFIX`."""
    name = inbound_key[len(INBOUND_PREFIX) :] if inbound_key.startswith(INBOUND_PREFIX) else None
    return f'{FAILED_PREFIX}{name if name else inbound_key.replace("/", "_")}'


def user_prefix(user_id: object) -> str:
    """The key prefix covering every object of one user, with the trailing slash that stops
    `.../users/1` from also matching `.../users/10`."""
    return f'{USERS_PREFIX}{user_id}/'


def file_key(user_id: object, receipt_id: object) -> str:
    return f'{user_prefix(user_id)}files/{receipt_id}'


def preview_key(user_id: object, receipt_id: object) -> str:
    return f'{user_prefix(user_id)}previews/{receipt_id}.jpg'


def email_key(user_id: object, email_id: object) -> str:
    return f'{user_prefix(user_id)}emails/{email_id}.eml'


_storage: S3Storage | None = None


def get_storage() -> S3Storage:
    """The process-wide `S3Storage`, built lazily from settings (the bucket and client are
    captured when it is first built; tests call `reset_storage_cache()` to rebuild)."""
    global _storage
    if _storage is None:
        _storage = S3Storage(
            bucket=settings.RECEIPTS_S3_BUCKET,
            client=build_client(
                region=settings.RECEIPTS_S3_REGION,
                endpoint_url=settings.RECEIPTS_S3_ENDPOINT_URL,
                access_key_id=settings.RECEIPTS_AWS_ACCESS_KEY_ID,
                secret_access_key=settings.RECEIPTS_AWS_SECRET_ACCESS_KEY,
                signature_version='s3v4',
            ),
        )
    return _storage


def reset_storage_cache() -> None:
    """Test-only: drop the cached instance so the next `get_storage()` rebuilds it."""
    global _storage
    _storage = None
