import pytest
from django.core import checks

from ..checks import check_geoip_database
from ..services import geoip


class FakeCountry:
    def __init__(self, iso_code):
        self.iso_code = iso_code


class FakeResponse:
    def __init__(self, iso_code):
        self.country = FakeCountry(iso_code)


class FakeReader:
    """Stands in for `geoip2.database.Reader`: a table of address -> country."""

    def __init__(self, table):
        self.table = table
        self.closed = False
        self.lookups = []
        self.opened: list[str] = []

    def country(self, ip):
        self.lookups.append(ip)
        if ip not in self.table:
            raise LookupError(ip)  # like AddressNotFoundError
        return FakeResponse(self.table[ip])

    def close(self):
        self.closed = True


@pytest.fixture
def fake_reader(settings, monkeypatch):
    """Install a fake reader for the configured path (no file needed)."""
    settings.GEOIP_PATH = '/fake/GeoLite2-Country.mmdb'
    reader = FakeReader({'203.0.113.5': 'DE', '198.51.100.7': 'us', '192.0.2.1': None})

    def fake_open(path):
        reader.opened.append(path)
        return reader

    monkeypatch.setattr(geoip, '_open', fake_open)
    geoip.reset()
    return reader


def test_without_a_path_every_country_is_unknown(settings):
    settings.GEOIP_PATH = ''
    assert geoip.get_reader() is None
    assert geoip.country_code('203.0.113.5') == ''


def test_a_known_address_gives_its_upper_cased_country(fake_reader):
    assert geoip.country_code('203.0.113.5') == 'DE'
    assert geoip.country_code('198.51.100.7') == 'US'


def test_an_unknown_address_or_a_missing_code_is_unknown(fake_reader):
    assert geoip.country_code('203.0.113.99') == ''  # not in the database
    assert geoip.country_code('192.0.2.1') == ''  # in it, but with no country
    assert geoip.country_code(None) == ''
    assert geoip.country_code('') == ''


def test_the_reader_is_opened_once_and_reused(fake_reader):
    geoip.country_code('203.0.113.5')
    geoip.country_code('203.0.113.5')
    assert fake_reader.opened == ['/fake/GeoLite2-Country.mmdb']
    assert len(fake_reader.lookups) == 2


def test_a_database_that_cannot_be_opened_is_unknown_and_warned_about_once(
    settings, monkeypatch, caplog
):
    settings.GEOIP_PATH = '/nonexistent/GeoLite2-Country.mmdb'
    geoip.reset()
    caplog.set_level('WARNING', logger='apps.short_url.services.geoip')
    assert geoip.country_code('203.0.113.5') == ''
    assert geoip.country_code('203.0.113.5') == ''
    warnings = [r for r in caplog.records if 'could not be opened' in r.message]
    assert len(warnings) == 1


def test_a_lookup_that_blows_up_is_unknown_never_an_error(fake_reader, monkeypatch):
    monkeypatch.setattr(fake_reader, 'country', lambda ip: 1 / 0)
    assert geoip.country_code('203.0.113.5') == ''


def test_reset_closes_the_readers(fake_reader):
    geoip.country_code('203.0.113.5')
    geoip.reset()
    assert fake_reader.closed


class TestSystemCheck:
    def test_unset_path_warns_never_errors(self, settings):
        settings.GEOIP_PATH = ''
        messages = check_geoip_database()
        assert [m.id for m in messages] == ['short_url.W001']
        assert messages[0].level == checks.WARNING
        assert 'GEOIP_PATH is not set' in messages[0].msg

    def test_a_path_that_is_not_a_file_warns(self, settings, tmp_path):
        settings.GEOIP_PATH = str(tmp_path / 'missing.mmdb')
        messages = check_geoip_database()
        assert [m.id for m in messages] == ['short_url.W001']
        assert 'is not a file' in messages[0].msg

    def test_an_existing_file_is_quiet(self, settings, tmp_path):
        database = tmp_path / 'GeoLite2-Country.mmdb'
        database.write_bytes(b'')
        settings.GEOIP_PATH = str(database)
        assert check_geoip_database() == []

    def test_the_check_is_registered(self):
        registered = checks.registry.registry.get_checks()
        assert check_geoip_database in registered
