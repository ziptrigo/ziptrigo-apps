"""To-do storage: the key shapes and the `TODO_*` wiring on top of the generic
`apps.core.services.storage.S3Storage` (issue #184).

One private bucket per environment, shared with file transfer and Receipts; everything To-do
stores is under `todo/` (the infra IAM user is scoped to that prefix, issue #185):

- `todo/users/<user_id>/attachments/<attachment_id>`: an attachment. Keyed by id only -- never the
  filename, which the user controls -- for the same reasons as file transfer's and Receipts' keys;
  the display name lives on `Attachment.filename` and goes into `Content-Disposition`.

- `todo/inbound/<message id>`: the raw messages SES drops for the email intake (issue #186), read by
  the poller (`services.inbound`); a message that fails to ingest is parked under `todo/failed/`
  so a pile of failures cannot crowd new mail out of the listing. SES writes under `todo/inbound/`;
  the bucket's lifecycle rule expires that prefix (the `infra` repo, issue #185).

Putting the user id in the path is what lets deleting an account delete all of its objects with one
`delete_prefix` (`user_prefix`).

The client signs with SigV4 (`signature_version='s3v4'`): the presigned direct uploads
(`S3Storage.presigned_put_url`) depend on the signed `Content-Length` being enforced, which
botocore's default does not do in the older regions, and `presigned_put_url` fails closed on a
client that does not sign it.

Tests replace the process-wide instance with an in-memory fake (see `apps/todo/tests/conftest.py`),
so nothing here needs the network.
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
    'PUT_URL_EXPIRES_SECONDS',
    'USERS_PREFIX',
    'FAILED_PREFIX',
    'INBOUND_PREFIX',
    'ObjectInfo',
    'S3Storage',
    'attachment_key',
    'failed_key_for',
    'get_storage',
    'reset_storage_cache',
    'user_prefix',
]

USERS_PREFIX = 'todo/users/'

#: Where SES drops the raw messages sent to `todo+<code>@...`.
INBOUND_PREFIX = 'todo/inbound/'

#: Where messages that failed to ingest are parked.
FAILED_PREFIX = 'todo/failed/'


def failed_key_for(inbound_key: str) -> str:
    """Where the object at `inbound_key` is parked when its message fails: the same name under
    `FAILED_PREFIX`."""
    name = inbound_key[len(INBOUND_PREFIX) :] if inbound_key.startswith(INBOUND_PREFIX) else None
    return f'{FAILED_PREFIX}{name if name else inbound_key.replace("/", "_")}'


def user_prefix(user_id: object) -> str:
    """The key prefix covering every object of one user, with the trailing slash that stops
    `.../users/1` from also matching `.../users/10`."""
    return f'{USERS_PREFIX}{user_id}/'


def attachment_key(user_id: object, attachment_id: object) -> str:
    return f'{user_prefix(user_id)}attachments/{attachment_id}'


_storage: S3Storage | None = None


def get_storage() -> S3Storage:
    """The process-wide `S3Storage`, built lazily from settings (the bucket and client are
    captured when it is first built; tests call `reset_storage_cache()` to rebuild)."""
    global _storage
    if _storage is None:
        _storage = S3Storage(
            bucket=settings.TODO_S3_BUCKET,
            client=build_client(
                region=settings.TODO_S3_REGION,
                endpoint_url=settings.TODO_S3_ENDPOINT_URL,
                access_key_id=settings.TODO_AWS_ACCESS_KEY_ID,
                secret_access_key=settings.TODO_AWS_SECRET_ACCESS_KEY,
                signature_version='s3v4',
            ),
        )
    return _storage


def reset_storage_cache() -> None:
    """Test-only: drop the cached instance so the next `get_storage()` rebuilds it."""
    global _storage
    _storage = None
