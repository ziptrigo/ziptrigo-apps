"""The streaming zip helpers (moved here from file transfer so Receipts' exports share them)."""

import io
import zipfile

import pytest

from ..services.zip_stream import (
    COPY_CHUNK_BYTES,
    DEFAULT_PART_SIZE_BYTES,
    S3MultipartWriter,
    copy_stream,
    dedupe_entry_name,
    safe_entry_name,
)

pytestmark = [pytest.mark.unit]

MIN_PART = 5 * 1024 * 1024


class FakeStorage:
    def __init__(self) -> None:
        self.parts: dict[int, bytes] = {}
        self.objects: dict[str, bytes] = {}
        self.checksum_algorithm: object = 'unset'
        self.aborted = False

    def create_multipart_upload(self, key, *, checksum_algorithm='SHA256'):
        self.checksum_algorithm = checksum_algorithm
        return 'upload-1'

    def upload_part(self, key, upload_id, number, body):
        self.parts[number] = bytes(body)
        return f'etag-{number}'

    def complete_multipart_upload(self, key, upload_id, parts):
        assert [p['PartNumber'] for p in parts] == sorted(self.parts)
        self.objects[key] = b''.join(self.parts[n] for n in sorted(self.parts))

    def abort_multipart_upload(self, key, upload_id):
        self.aborted = True


class TestNames:
    @pytest.mark.parametrize(
        ('name', 'expected'),
        [
            ('report.pdf', 'report.pdf'),
            ('a/b\\c.txt', 'a_b_c.txt'),
            ('../../etc/passwd', '_.._etc_passwd'),
            ('..', 'file'),
            ('.hidden', 'hidden'),
            ('', 'file'),
        ],
    )
    def test_safe_entry_name(self, name, expected):
        assert safe_entry_name(name) == expected

    def test_dedupe_numbers_repeats(self):
        used: set[str] = set()
        assert [dedupe_entry_name('a.txt', used) for _ in range(3)] == [
            'a.txt',
            'a (1).txt',
            'a (2).txt',
        ]
        assert dedupe_entry_name('noext', used) == 'noext'
        assert dedupe_entry_name('noext', used) == 'noext (1)'


class TestWriter:
    def test_a_zip_streams_through_it_and_the_size_is_counted(self):
        storage = FakeStorage()
        writer = S3MultipartWriter(storage, 'k', part_size=MIN_PART)  # ty: ignore[invalid-argument-type]
        with zipfile.ZipFile(writer, mode='w', compression=zipfile.ZIP_STORED) as zf:  # ty: ignore[no-matching-overload]
            zf.writestr('a.txt', b'hello')
        writer.finish()

        assert zipfile.ZipFile(io.BytesIO(storage.objects['k'])).read('a.txt') == b'hello'
        assert writer.size == len(storage.objects['k'])

    def test_it_asks_for_no_checksum_algorithm(self):
        storage = FakeStorage()
        S3MultipartWriter(storage, 'k')  # ty: ignore[invalid-argument-type]
        assert storage.checksum_algorithm is None

    def test_parts_are_a_full_buffer_except_the_last(self):
        storage = FakeStorage()
        writer = S3MultipartWriter(storage, 'k', part_size=MIN_PART)  # ty: ignore[invalid-argument-type]
        writer.write(b'x' * (MIN_PART + 100))
        writer.write(b'y' * 42)
        writer.finish()
        assert [len(storage.parts[n]) for n in sorted(storage.parts)] == [MIN_PART, 142]

    def test_an_empty_zip_still_has_one_part(self):
        storage = FakeStorage()
        writer = S3MultipartWriter(storage, 'k')  # ty: ignore[invalid-argument-type]
        writer.finish()
        assert storage.objects['k'] == b''

    def test_abort(self):
        storage = FakeStorage()
        S3MultipartWriter(storage, 'k').abort()  # ty: ignore[invalid-argument-type]
        assert storage.aborted

    def test_the_default_part_size_is_64_mb(self):
        assert DEFAULT_PART_SIZE_BYTES == 64 * 1024 * 1024


class TestCopyStream:
    def test_copies_in_chunks_and_closes_the_source(self):
        data = b'z' * (COPY_CHUNK_BYTES * 2 + 5)
        src, dest = io.BytesIO(data), io.BytesIO()
        copy_stream(src, dest)
        assert dest.getvalue() == data and src.closed

    def test_closes_the_source_on_failure(self):
        src = io.BytesIO(b'abc')

        class Broken:
            def write(self, chunk):
                raise OSError('disk full')

        with pytest.raises(OSError):
            copy_stream(src, Broken())  # ty: ignore[invalid-argument-type]
        assert src.closed
