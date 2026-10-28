"""In-memory fake of the To-do storage: no network, deterministic.

`FakeTodoStorage` implements the subset of `S3Storage` the To-do services call, modelled on
`apps.receipts.tests.fakes.FakeReceiptsStorage`.
"""

import base64
import hashlib
import io
from dataclasses import dataclass, field

from botocore.exceptions import ClientError

from apps.core.services.storage import GET_URL_EXPIRES_SECONDS, ObjectInfo


def b64_sha256(data: bytes) -> str:
    return base64.b64encode(hashlib.sha256(data).digest()).decode()


@dataclass
class FakeTodoStorage:
    bucket: str = 'fake-bucket'
    objects: dict[str, bytes] = field(default_factory=dict)
    put_urls: list[dict] = field(default_factory=list)
    get_urls: list[dict] = field(default_factory=list)
    deleted: list[str] = field(default_factory=list)
    #: Keys whose deletion should fail, to test retry behaviour.
    failing_deletes: set[str] = field(default_factory=set)
    #: Raise on every `delete_prefix` while true.
    failing_prefix_delete: bool = False
    deleted_prefixes: list[str] = field(default_factory=list)

    def seed(self, key: str, data: bytes) -> None:
        """Test helper: stand in for the client's direct PUT."""
        self.objects[key] = data

    def head_object(self, key: str) -> ObjectInfo:
        if key not in self.objects:
            raise ClientError({'Error': {'Code': '404', 'Message': 'Not Found'}}, 'HeadObject')
        return ObjectInfo(
            size=len(self.objects[key]), checksum_sha256=b64_sha256(self.objects[key])
        )

    def get_object_stream(self, key: str):
        if key not in self.objects:
            raise ClientError({'Error': {'Code': 'NoSuchKey', 'Message': 'missing'}}, 'GetObject')
        return io.BytesIO(self.objects[key])

    def object_exists(self, key: str) -> bool:
        return key in self.objects

    def list_keys(self, prefix: str, *, max_keys: int | None = 1000) -> list[str]:
        keys = sorted(k for k in self.objects if k.startswith(prefix))
        return keys if max_keys is None else keys[:max_keys]

    def presigned_put_url(
        self,
        key: str,
        *,
        content_length: int,
        checksum_sha256: str | None = None,
        content_type: str | None = None,
        expires_in: int = 3600,
    ) -> str:
        self.put_urls.append(
            {
                'key': key,
                'content_length': content_length,
                'checksum_sha256': checksum_sha256,
                'content_type': content_type,
            }
        )
        return f'https://fake-s3.test/{key}?put'

    def presigned_get_url(
        self,
        key: str,
        filename: str,
        expires_in: int = GET_URL_EXPIRES_SECONDS,
        *,
        disposition: str = 'attachment',
        content_type: str | None = None,
    ) -> str:
        self.get_urls.append(
            {
                'key': key,
                'filename': filename,
                'disposition': disposition,
                'content_type': content_type,
            }
        )
        return f'https://fake-s3.test/{key}?get&disposition={disposition}'

    def delete_object(self, key: str) -> None:
        if key in self.failing_deletes:
            raise ClientError({'Error': {'Code': 'InternalError', 'Message': 'x'}}, 'DeleteObject')
        self.objects.pop(key, None)
        self.deleted.append(key)

    def delete_objects(self, keys) -> int:
        """Like `S3Storage.delete_objects`: the rest of the batch goes, then a failure raises."""
        key_list = list(keys)
        failed = [key for key in key_list if key in self.failing_deletes]
        for key in key_list:
            if key not in self.failing_deletes:
                self.objects.pop(key, None)
                self.deleted.append(key)
        if failed:
            raise RuntimeError(f'S3 could not delete {len(failed)} object(s)')
        return len(key_list)

    def delete_prefix(self, prefix: str) -> int:
        if self.failing_prefix_delete:
            raise RuntimeError('S3 could not delete the prefix')
        self.deleted_prefixes.append(prefix)
        keys = [k for k in self.objects if k.startswith(prefix)]
        for key in keys:
            del self.objects[key]
        return len(keys)
