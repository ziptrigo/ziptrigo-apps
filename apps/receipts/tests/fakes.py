"""In-memory fakes for the Receipts tests: no network, deterministic.

`FakeReceiptsStorage` implements the subset of `S3Storage` the receipts services call.
`FakeAIClient` stands in for the OpenAI client: it returns a canned `ExtractedReceipt` (or raises a
queued error) and records every request, so a test can assert on what would have been sent.
"""

import base64
import hashlib
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from botocore.exceptions import ClientError

from apps.core.services.storage import GET_URL_EXPIRES_SECONDS, ObjectInfo, ObjectTooLargeError

from ..services.ai import ExtractedReceipt, ExtractionRequest


def b64_sha256(data: bytes) -> str:
    return base64.b64encode(hashlib.sha256(data).digest()).decode()


@dataclass
class FakeReceiptsStorage:
    bucket: str = 'fake-bucket'
    objects: dict[str, bytes] = field(default_factory=dict)
    content_types: dict[str, str] = field(default_factory=dict)
    put_urls: list[dict] = field(default_factory=list)
    get_urls: list[dict] = field(default_factory=list)
    deleted: list[str] = field(default_factory=list)
    #: Keys whose `delete_object` should fail, to test retry behaviour.
    failing_deletes: set[str] = field(default_factory=set)

    def seed(self, key: str, data: bytes) -> None:
        """Test helper: stand in for the client's direct PUT (or SES writing an inbound object)."""
        self.objects[key] = data

    def put_object(self, key: str, body: bytes, *, content_type: str | None = None) -> None:
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

    def head_object(self, key: str) -> ObjectInfo:
        if key not in self.objects:
            raise ClientError({'Error': {'Code': '404', 'Message': 'Not Found'}}, 'HeadObject')
        return ObjectInfo(
            size=len(self.objects[key]), checksum_sha256=b64_sha256(self.objects[key])
        )

    def object_exists(self, key: str) -> bool:
        return key in self.objects

    def list_keys(self, prefix: str, *, max_keys: int | None = 1000) -> list[str]:
        keys = sorted(k for k in self.objects if k.startswith(prefix))
        return keys if max_keys is None else keys[:max_keys]

    def copy_object(self, src_key: str, dst_key: str) -> None:
        if src_key not in self.objects:
            raise ClientError({'Error': {'Code': 'NoSuchKey', 'Message': 'missing'}}, 'CopyObject')
        self.objects[dst_key] = self.objects[src_key]

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
        keys = [k for k in self.objects if k.startswith(prefix)]
        for key in keys:
            del self.objects[key]
        return len(keys)


def sample_extraction(**overrides) -> ExtractedReceipt:
    values = {
        'receipt_count': 1,
        'is_receipt': True,
        'vendor': 'Staples',
        'amount': Decimal('42.10'),
        'currency': 'USD',
        'receipt_date': date(2026, 10, 9),
        'document_type': 'receipt',
        'document_number': 'R-55102',
        'payment_method': 'Visa ••4242',
        'category': 'Office supplies',
        'confidence': 0.95,
    }
    values.update(overrides)
    return ExtractedReceipt.model_validate(values)


@dataclass
class FakeAIClient:
    """Returns `default` for every call unless `queue` has something: queue results or
    exceptions to be returned/raised in order."""

    default: ExtractedReceipt = field(default_factory=sample_extraction)
    queue: list[ExtractedReceipt | Exception] = field(default_factory=list)
    calls: list[ExtractionRequest] = field(default_factory=list)

    def extract(self, request: ExtractionRequest) -> ExtractedReceipt:
        self.calls.append(request)
        item = self.queue.pop(0) if self.queue else self.default
        if isinstance(item, Exception):
            raise item
        return item
