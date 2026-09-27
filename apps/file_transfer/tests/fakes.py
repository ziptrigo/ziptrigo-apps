"""An in-memory fake of `S3Storage` for tests: no network, deterministic, and injectable via the
`storage=` keyword every service function that touches S3 accepts. Mirrors every method the
services layer calls.

Realistic enough for the send flow: `complete_file_upload` calls `complete_multipart_upload` (a
no-op here) and then `head_object` to verify the size, so tests register the "uploaded" bytes with
`put_object` before calling it -- standing in for the browser's direct-to-S3 PUTs, which this fake
never actually receives.
"""

from dataclasses import dataclass, field

from botocore.exceptions import ClientError

from ..services.storage import ObjectInfo


@dataclass
class FakeS3Storage:
    objects: dict[str, bytes] = field(default_factory=dict)
    active_uploads: set[str] = field(default_factory=set)
    aborted_uploads: set[str] = field(default_factory=set)
    checksum: str = 'ZmFrZWNoZWNrc3Vt'
    presign_calls: list[tuple[str, int]] = field(default_factory=list)

    def put_object(self, key: str, size: int) -> None:
        """Test helper: pretend `size` bytes were already PUT to `key`."""
        self.objects[key] = b'\0' * size

    def create_multipart_upload(self, key: str) -> str:
        upload_id = f'upload-{len(self.active_uploads) + len(self.aborted_uploads)}'
        self.active_uploads.add(upload_id)
        return upload_id

    def presign_part_url(
        self, key: str, upload_id: str, part_number: int, expires_in: int = 3600
    ) -> str:
        self.presign_calls.append((upload_id, part_number))
        return f'https://fake-s3.test/{key}?uploadId={upload_id}&partNumber={part_number}'

    def complete_multipart_upload(self, key: str, upload_id: str, parts: list[dict]) -> None:
        self.active_uploads.discard(upload_id)

    def abort_multipart_upload(self, key: str, upload_id: str) -> None:
        self.active_uploads.discard(upload_id)
        self.aborted_uploads.add(upload_id)

    def head_object(self, key: str) -> ObjectInfo:
        if key not in self.objects:
            raise ClientError({'Error': {'Code': '404', 'Message': 'Not Found'}}, 'HeadObject')
        return ObjectInfo(size=len(self.objects[key]), checksum_sha256=self.checksum)

    def object_exists(self, key: str) -> bool:
        return key in self.objects

    def presigned_get_url(self, key: str, filename: str, expires_in: int = 900) -> str:
        return f'https://fake-s3.test/{key}?filename={filename}'

    def delete_object(self, key: str) -> None:
        self.objects.pop(key, None)

    def delete_prefix(self, prefix: str) -> int:
        keys = [k for k in self.objects if k.startswith(prefix)]
        for key in keys:
            del self.objects[key]
        return len(keys)
