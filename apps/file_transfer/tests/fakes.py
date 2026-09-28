"""An in-memory fake of `S3Storage` for tests: no network, deterministic, and injectable via the
`storage=` keyword every service function that touches S3 accepts. Mirrors every method the
services layer calls.

Realistic enough for the send flow: `complete_file_upload` calls `complete_multipart_upload` (a
no-op here) and then `head_object` to verify the size, so tests register the "uploaded" bytes with
`put_object` before calling it -- standing in for the browser's direct-to-S3 PUTs, which this fake
never actually receives.
"""

import base64
import hashlib
import io
from dataclasses import dataclass, field

from botocore.exceptions import ClientError

from ..services.storage import S3_MIN_PART_SIZE_BYTES, ObjectInfo


def _b64_sha256(data: bytes) -> str:
    """Base64-encoded SHA-256 digest of `data` -- what a real per-part `ChecksumSHA256` would
    always equal, since S3 verifies it matches the bytes it received (see `list_parts` below)."""
    return base64.b64encode(hashlib.sha256(data).digest()).decode()


@dataclass
class FakeS3Storage:
    objects: dict[str, bytes] = field(default_factory=dict)
    active_uploads: set[str] = field(default_factory=set)
    aborted_uploads: set[str] = field(default_factory=set)
    checksum: str = 'ZmFrZWNoZWNrc3Vt'
    presign_calls: list[tuple[str, int]] = field(default_factory=list)
    #: Parts recorded through `upload_part` (the zip builder's server-side path, and tests that
    #: simulate a part already landed in S3 via a resumed upload), keyed by upload id then part
    #: number -- assembled into `objects[key]` on `complete_multipart_upload`. The browser-PUT
    #: path (`presign_part_url`) never populates this: those tests simulate the uploaded bytes
    #: with `put_object` instead, so `complete_multipart_upload` leaves `objects` alone (and skips
    #: the checksum enforcement below) when there's nothing recorded here for the upload id.
    multipart_parts: dict[str, dict[int, bytes]] = field(default_factory=dict)
    #: Upload id -> whether `create_multipart_upload` was asked for a checksum algorithm (real S3
    #: default: yes). Mirrors the real requirement that `CompleteMultipartUpload` must then carry
    #: a `ChecksumSHA256` for every part already known to S3 (issue #55 phase 3 review: this is
    #: exactly what a resumed upload's completion used to omit) -- see `complete_multipart_upload`.
    checksum_required: dict[str, bool] = field(default_factory=dict)

    def put_object(self, key: str, size: int) -> None:
        """Test helper: pretend `size` bytes were already PUT to `key`."""
        self.objects[key] = b'\0' * size

    def create_multipart_upload(
        self, key: str, *, checksum_algorithm: str | None = 'SHA256'
    ) -> str:
        upload_id = f'upload-{len(self.active_uploads) + len(self.aborted_uploads)}'
        self.active_uploads.add(upload_id)
        self.checksum_required[upload_id] = checksum_algorithm is not None
        return upload_id

    def presign_part_url(
        self,
        key: str,
        upload_id: str,
        part_number: int,
        expires_in: int = 3600,
        *,
        content_length: int | None = None,
        checksum_sha256: str | None = None,
    ) -> str:
        self.presign_calls.append((upload_id, part_number))
        url = f'https://fake-s3.test/{key}?uploadId={upload_id}&partNumber={part_number}'
        if checksum_sha256 is not None:
            url += f'&checksum={checksum_sha256}'
        return url

    def upload_part(self, key: str, upload_id: str, part_number: int, body: bytes) -> str:
        self.multipart_parts.setdefault(upload_id, {})[part_number] = bytes(body)
        return f'etag-{upload_id}-{part_number}'

    def get_object_stream(self, key: str) -> io.BytesIO:
        if key not in self.objects:
            raise ClientError({'Error': {'Code': '404', 'Message': 'Not Found'}}, 'GetObject')
        return io.BytesIO(self.objects[key])

    def complete_multipart_upload(self, key: str, upload_id: str, parts: list[dict]) -> None:
        self.active_uploads.discard(upload_id)
        recorded = self.multipart_parts.pop(upload_id, None)
        if recorded:
            if self.checksum_required.get(upload_id, True):
                # Mirrors real S3: once a multipart upload was created with a checksum algorithm,
                # completing it must repeat every already-landed part's checksum, or S3 rejects
                # the whole request (issue #55 phase 3 review's critical finding -- a resumed
                # upload's completion used to build its part list without one for parts it didn't
                # re-PUT). Only enforced for parts this fake actually knows landed via
                # `upload_part` (`recorded`) -- a test that only simulates the end result with
                # `put_object` isn't exercising this at all.
                by_number = {p.get('PartNumber'): p for p in parts}
                for part_number in recorded:
                    supplied = by_number.get(part_number)
                    if not supplied or not supplied.get('ChecksumSHA256'):
                        raise ClientError(
                            {
                                'Error': {
                                    'Code': 'InvalidRequest',
                                    'Message': (
                                        'The upload was created using the sha256 checksum '
                                        'algorithm. Please provide the checksum for part number '
                                        f'{part_number} in your request.'
                                    ),
                                }
                            },
                            'CompleteMultipartUpload',
                        )
            ordered = sorted(recorded)
            # Mirrors real S3: every part except the last must meet the service minimum, or the
            # whole multipart upload is rejected at completion time (see
            # `apps.file_transfer.services.zip`'s `_S3MultipartWriter`, which this enforcement
            # exists to keep honest -- a part-size regression there should fail a test, not pass
            # silently against a fake that accepts parts of any size).
            for part_number in ordered[:-1]:
                if len(recorded[part_number]) < S3_MIN_PART_SIZE_BYTES:
                    raise ClientError(
                        {
                            'Error': {
                                'Code': 'EntityTooSmall',
                                'Message': 'Your proposed upload is smaller than the minimum '
                                'allowed object size.',
                            }
                        },
                        'CompleteMultipartUpload',
                    )
            self.objects[key] = b''.join(recorded[number] for number in ordered)

    def list_parts(self, key: str, upload_id: str) -> list[dict]:
        if upload_id not in self.active_uploads:
            # Mirrors real S3: `ListParts` on an upload id it no longer knows about (aborted, or
            # never existed) fails with this error code -- see `services.uploads.UploadExpired`.
            raise ClientError(
                {
                    'Error': {
                        'Code': 'NoSuchUpload',
                        'Message': 'The specified upload does not exist.',
                    }
                },
                'ListParts',
            )
        recorded = self.multipart_parts.get(upload_id, {})
        return [
            {
                'PartNumber': number,
                'ETag': f'etag-{upload_id}-{number}',
                'Size': len(data),
                'ChecksumSHA256': _b64_sha256(data),
            }
            for number, data in sorted(recorded.items())
        ]

    def abort_multipart_upload(self, key: str, upload_id: str) -> None:
        self.active_uploads.discard(upload_id)
        self.aborted_uploads.add(upload_id)
        self.multipart_parts.pop(upload_id, None)

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
