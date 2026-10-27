"""The country of a click, from a MaxMind GeoLite2-Country database.

`settings.GEOIP_PATH` points at the `.mmdb` file. Without it (unset, missing or unreadable) every
lookup answers `''`, "unknown", and `apps.short_url.checks` warns (`short_url.W001`): analytics
still work, only the country table stays empty. Nothing here ever raises into a request.

The reader is opened once per process and memory-mapped, so it is cheap to call on every click. A
database replaced on disk (MaxMind publishes updates twice a week) is picked up by the next worker
restart. GeoLite2 needs a free MaxMind account; README's "Short URLs" section has the setup.
"""

import logging
import threading
from typing import Protocol

from django.conf import settings

logger = logging.getLogger(__name__)


class CountryReader(Protocol):
    """The part of `geoip2.database.Reader` this module uses (tests pass a fake)."""

    def country(self, ip_address: str): ...


_lock = threading.Lock()
#: path -> the open reader, or None when that path could not be opened (warned about once).
_readers: dict[str, CountryReader | None] = {}


def _open(path: str) -> CountryReader | None:
    import geoip2.database

    try:
        return geoip2.database.Reader(path)
    except Exception as exc:  # a missing file, a corrupt or non-MaxMind one
        logger.warning(
            'GeoIP database %s could not be opened (%s); countries are unknown.', path, exc
        )
        return None


def get_reader() -> CountryReader | None:
    """The cached reader for `settings.GEOIP_PATH`, or None when there is no usable database."""
    path = getattr(settings, 'GEOIP_PATH', '') or ''
    if not path:
        return None
    if path not in _readers:
        with _lock:
            if path not in _readers:
                _readers[path] = _open(path)
    return _readers[path]


def reset() -> None:
    """Forget (and close) the cached readers; for tests, and a process that swaps the file."""
    with _lock:
        for reader in _readers.values():
            close = getattr(reader, 'close', None)
            if close is not None:
                close()
        _readers.clear()


def country_code(ip: str | None) -> str:
    """The ISO 3166-1 alpha-2 country of `ip` (the real address, not a grouping key), or `''`."""
    if not ip:
        return ''
    reader = get_reader()
    if reader is None:
        return ''
    try:
        code = reader.country(ip).country.iso_code
    except Exception:  # AddressNotFoundError, an invalid address, a damaged database
        return ''
    return code.upper() if isinstance(code, str) and len(code) == 2 else ''
