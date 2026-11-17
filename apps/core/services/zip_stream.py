"""Streaming a zip straight into S3, shared by every product that builds one (file transfer's
"download all", Receipts' exports; products may not import each other, so the helpers live here).

Nothing here ever holds a whole file, let alone the whole zip, in memory:
`S3Storage.get_object_stream` reads a source file a chunk at a time (`copy_stream`), and
`S3MultipartWriter` turns `zipfile`'s output into an S3 multipart upload, buffering `part_size`
bytes before each part goes out. `zipfile.ZipFile` can write to a stream it can't `tell()` (it
falls back to tracking its own offset -- see `S3MultipartWriter`'s docstring), which is what makes
writing directly into that upload possible: the finished zip's total size is never needed up front.
"""

from typing import IO

from .storage import S3Storage

#: Read (and re-write) source files this many bytes at a time.
COPY_CHUNK_BYTES = 1024 * 1024

#: How much `S3MultipartWriter` buffers before it uploads a part, unless told otherwise. Large
#: enough that even a many-GB zip stays comfortably under S3's 10,000-part ceiling.
DEFAULT_PART_SIZE_BYTES = 64 * 1024 * 1024


def safe_entry_name(name: str) -> str:
    """Flatten a stored file name into a safe zip entry name: no path separators, so nothing
    inside the zip can be written outside its own directory by a naive/vulnerable extractor that
    doesn't guard against "zip slip" (a name that reached here as `../../etc/x` or similar).
    Callers' own validation may strip control characters, but names are otherwise unrestricted --
    `/`, `\\` and `..` all reach this function unchanged.
    """
    flattened = name.replace('\\', '_').replace('/', '_').lstrip('.')
    return flattened or 'file'


def dedupe_entry_name(name: str, used: set[str]) -> str:
    """`name`, or `name` renamed to `stem (1).ext`, `stem (2).ext`, ... if it's already in `used`
    -- two files may share a name (nothing stops two uploads of `report.pdf`), and a zip can't have
    two entries with the same path."""
    if name not in used:
        used.add(name)
        return name

    stem, dot, ext = name.rpartition('.')
    if not dot:
        stem, ext = name, ''
    n = 1
    while True:
        candidate = f'{stem} ({n}).{ext}' if ext else f'{stem} ({n})'
        if candidate not in used:
            used.add(candidate)
            return candidate
        n += 1


class S3MultipartWriter:
    """Write-only, non-seekable file-like object that streams into an S3 multipart upload.

    Buffers up to `part_size` bytes (except the final part, which may be smaller) before
    uploading a part, so the whole zip is never held in memory at once. Deliberately defines no
    `.tell()`: `zipfile.ZipFile.__init__` tries `self.fp.tell()` and, on `AttributeError`, wraps
    the file object in its own offset-tracking shim instead of relying on the file object being
    seekable -- that fallback is what makes writing a zip directly into this work at all, since an
    S3 multipart upload has no concept of seeking backwards to patch up local file headers the way
    a real seekable zip file would.
    """

    def __init__(
        self, storage: S3Storage, key: str, *, part_size: int = DEFAULT_PART_SIZE_BYTES
    ) -> None:
        self._storage = storage
        self._key = key
        self._part_size = part_size
        # No `ChecksumAlgorithm`, unlike a browser/CLI-driven upload: this writer's parts never
        # cross an untrusted network hop -- they're written server-side, straight from
        # `S3Storage.get_object_stream`'s reads of other objects in the same bucket -- so there's
        # nothing for a client-computed checksum to verify, and requesting one would only
        # obligate `_upload_part`/`finish` to compute and carry one for no benefit (see
        # `S3Storage.create_multipart_upload`'s docstring).
        self._upload_id = storage.create_multipart_upload(key, checksum_algorithm=None)
        self._buffer = bytearray()
        self._parts: list[dict] = []
        self._part_number = 1
        #: Bytes written so far (the finished zip's size once `finish()` has run).
        self.size = 0

    def write(self, data: bytes) -> int:
        self._buffer += data
        self.size += len(data)
        while len(self._buffer) >= self._part_size:
            self._upload_part(bytes(self._buffer[: self._part_size]))
            del self._buffer[: self._part_size]
        return len(data)

    def flush(self) -> None:
        """`zipfile` calls this; there's nothing to do until a part is actually full."""

    def _upload_part(self, chunk: bytes) -> None:
        etag = self._storage.upload_part(self._key, self._upload_id, self._part_number, chunk)
        self._parts.append({'PartNumber': self._part_number, 'ETag': etag})
        self._part_number += 1

    def finish(self) -> None:
        """Flush whatever's left -- even an empty zip needs at least one (possibly empty) part
        -- and complete the multipart upload. Call only once every entry has been written
        successfully; see `abort` for the failure path. Not named `close`, since `zipfile.ZipFile`
        never calls `close()` on a file object it didn't open itself (it was handed one), and a
        method named `close` here could be mistaken for something `zipfile` might call on its own.
        """
        if self._buffer or not self._parts:
            self._upload_part(bytes(self._buffer))
            self._buffer.clear()
        self._storage.complete_multipart_upload(self._key, self._upload_id, self._parts)

    def abort(self) -> None:
        self._storage.abort_multipart_upload(self._key, self._upload_id)


def copy_stream(src: IO[bytes], dest: IO[bytes]) -> None:
    """Copy `src` into `dest` a chunk at a time, closing `src` when done (or on failure)."""
    try:
        while True:
            chunk = src.read(COPY_CHUNK_BYTES)
            if not chunk:
                return
            dest.write(chunk)
    finally:
        close = getattr(src, 'close', None)
        if close:
            close()
