"""In-memory fakes for the To-do tests: no network, deterministic.

`FakeTitleParser` (the AI date parsing, issue #183) is at the end of the file.

`FakeTodoStorage` implements the subset of `S3Storage` the To-do services call, modelled on
`apps.receipts.tests.fakes.FakeReceiptsStorage`.
"""

import base64
import hashlib
import io
from collections.abc import Callable
from dataclasses import dataclass, field

from botocore.exceptions import ClientError

from apps.core.services.storage import GET_URL_EXPIRES_SECONDS, ObjectInfo, ObjectTooLargeError

from ..services.parsing import ParsedTitle, ParseRequest


def b64_sha256(data: bytes) -> str:
    return base64.b64encode(hashlib.sha256(data).digest()).decode()


class _Body:
    """The streaming body `get_object_stream` returns."""

    def __init__(self, storage: 'FakeTodoStorage', data: io.BytesIO):
        self._storage = storage
        self._data = data
        self._served = 0

    def read(self, amt: int | None = None) -> bytes:
        self._storage.read_sizes.append(amt)
        limit = self._storage.fail_read_after
        if limit is not None and self._served >= limit:
            raise OSError('connection reset')
        chunk = self._data.read(amt)
        self._served += len(chunk)
        return chunk

    def close(self) -> None:
        self._data.close()


@dataclass
class FakeTodoStorage:
    bucket: str = 'fake-bucket'
    objects: dict[str, bytes] = field(default_factory=dict)
    put_urls: list[dict] = field(default_factory=list)
    get_urls: list[dict] = field(default_factory=list)
    deleted: list[str] = field(default_factory=list)
    #: Keys whose deletion should fail, to test retry behaviour.
    failing_deletes: set[str] = field(default_factory=set)
    #: Keys whose `put_object` should fail.
    failing_puts: set[str] = field(default_factory=set)
    #: The content type each key was written with.
    content_types: dict[str, str] = field(default_factory=dict)
    #: Raise on every `delete_prefix` while true.
    failing_prefix_delete: bool = False
    deleted_prefixes: list[str] = field(default_factory=list)
    #: False makes `head_object` answer without a checksum, like a store that doesn't echo one.
    echo_checksums: bool = True
    #: Raise from the body's `read` after this many bytes were served (a dropped connection).
    fail_read_after: int | None = None
    #: The sizes of the `read(n)` calls made on streams, to show the read-back is chunked.
    read_sizes: list[int | None] = field(default_factory=list)

    def put_object(self, key: str, body: bytes, *, content_type: str | None = None) -> None:
        if key in self.failing_puts:
            raise ClientError({'Error': {'Code': 'InternalError', 'Message': 'x'}}, 'PutObject')
        self.objects[key] = bytes(body)
        if content_type:
            self.content_types[key] = content_type

    def get_object_bytes(self, key: str, *, max_bytes: int | None = None) -> bytes:
        if key not in self.objects:
            raise ClientError({'Error': {'Code': 'NoSuchKey', 'Message': 'missing'}}, 'GetObject')
        data = self.objects[key]
        if max_bytes is not None and len(data) > max_bytes:
            raise ObjectTooLargeError(f'{key} is over the {max_bytes} byte limit')
        return data

    def copy_object(self, src_key: str, dst_key: str) -> None:
        if src_key not in self.objects:
            raise ClientError({'Error': {'Code': 'NoSuchKey', 'Message': 'missing'}}, 'CopyObject')
        self.objects[dst_key] = self.objects[src_key]

    def seed(self, key: str, data: bytes) -> None:
        """Test helper: stand in for the client's direct PUT."""
        self.objects[key] = data

    def head_object(self, key: str) -> ObjectInfo:
        if key not in self.objects:
            raise ClientError({'Error': {'Code': '404', 'Message': 'Not Found'}}, 'HeadObject')
        return ObjectInfo(
            size=len(self.objects[key]),
            checksum_sha256=b64_sha256(self.objects[key]) if self.echo_checksums else '',
        )

    def get_object_stream(self, key: str):
        if key not in self.objects:
            raise ClientError({'Error': {'Code': 'NoSuchKey', 'Message': 'missing'}}, 'GetObject')
        return _Body(self, io.BytesIO(self.objects[key]))

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


Reply = ParsedTitle | Callable[[ParseRequest], ParsedTitle] | Exception


@dataclass
class FakeTitleParser:
    #: Title -> the reply for it. A title not in the table has no date.
    replies: dict[str, Reply] = field(default_factory=dict)
    requests: list[ParseRequest] = field(default_factory=list)

    def parse(self, request: ParseRequest) -> ParsedTitle:
        self.requests.append(request)
        reply = self.replies.get(request.title)
        if reply is None:
            return ParsedTitle(has_date=False, clean_title=request.title)
        if isinstance(reply, Exception):
            raise reply
        if isinstance(reply, ParsedTitle):
            return reply
        return reply(request)

    @property
    def calls(self) -> int:
        return len(self.requests)
