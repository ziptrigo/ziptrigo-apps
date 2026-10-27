import pytest
from django.core.exceptions import ValidationError

from ..services import apply_utm, destination_host, validate_destination
from ..services.destinations import MAX_LENGTH


@pytest.mark.parametrize(
    ('given', 'stored'),
    [
        ('https://example.com', 'https://example.com'),
        ('  https://example.com/a/b?x=1#top  ', 'https://example.com/a/b?x=1#top'),
        ('HTTP://EXAMPLE.COM/Path', 'http://example.com/Path'),
        ('https://sub.example.co.uk:8443/x', 'https://sub.example.co.uk:8443/x'),
        ('https://8.8.8.8/dns', 'https://8.8.8.8/dns'),
        ('https://[2606:4700:4700::1111]/x', 'https://[2606:4700:4700::1111]/x'),
        # IDNA host, percent-encoded path, query and fragment; existing escapes are kept.
        ('https://bücher.example/', 'https://xn--bcher-kva.example/'),
        ('https://example.com/ü?q=é#ö', 'https://example.com/%C3%BC?q=%C3%A9#%C3%B6'),
        ('https://example.com/a%20b?x=%41', 'https://example.com/a%20b?x=%41'),
        ('https://example.com/a?b=c&d=e+f', 'https://example.com/a?b=c&d=e+f'),
        ('https://example.com./x', 'https://example.com/x'),
        ('https://my_host.example.com/', 'https://my_host.example.com/'),
        ('https://app.example.test.evil.com/s/abc', 'https://app.example.test.evil.com/s/abc'),
    ],
)
def test_valid_destinations_are_stored_normalized(given, stored):
    assert validate_destination(given) == stored


@pytest.mark.parametrize(
    'given',
    [
        '',
        '   ',
        'example.com',
        '//example.com/x',
        'ftp://example.com/file',
        'javascript:alert(1)',
        'data:text/html,hi',
        'file:///etc/passwd',
        'https://',
        'https:///path',
        'https://exa mple.com',
        'https://example.com/a b',
        'https://example.com/a\nb',
        'https://example.com/a\x00b',
        'https://user@example.com/',
        'https://user:pass@example.com/',
        'https://paypal.com@evil.example/',
        'https://example.com:99999/',
        'https://example.com:abc/',
        'https://-bad.example/',
        'https://bad-.example/',
        'https://a..b.example/',
        'https://exa$mple.com/',
        'https://' + 'a' * 64 + '.example/',
        'https://' + '.'.join(['a' * 60] * 5) + '/',
    ],
)
def test_malformed_destinations_are_refused(given):
    with pytest.raises(ValidationError) as caught:
        validate_destination(given)
    assert 'destination' in caught.value.message_dict


@pytest.mark.parametrize(
    'given',
    [
        'http://127.0.0.1/',
        'http://127.1.2.3:8080/',
        'http://10.0.0.5/',
        'http://172.16.0.1/',
        'http://192.168.1.1/admin',
        'http://169.254.169.254/latest/meta-data/',
        'http://0.0.0.0/',
        'http://100.64.0.1/x',
        'http://224.0.0.1/',
        'http://240.0.0.1/',
        'http://255.255.255.255/',
        'http://[::1]/',
        'http://[::]/',
        'http://[fe80::1]/',
        'http://[fc00::1]/',
        'http://[ff02::1]/',
        'http://[::ffff:127.0.0.1]/',
        'http://[::ffff:10.0.0.1]/',
        'http://localhost/',
        'http://LOCALHOST:8000/',
        'http://app.localhost/',
        'http://intranet/',
        'http://printer.local/',
        'http://db.internal/',
        'http://2130706433/',
        'http://127.1/',
        'http://0x7f.0.0.1/',
        'http://0x7f000001/',
    ],
)
def test_non_public_hosts_are_refused(given):
    with pytest.raises(ValidationError) as caught:
        validate_destination(given)
    assert 'destination' in caught.value.message_dict


def test_a_destination_over_the_length_limit_is_refused():
    with pytest.raises(ValidationError):
        validate_destination('https://example.com/' + 'a' * MAX_LENGTH)
    # Exactly at the limit is fine.
    url = 'https://example.com/' + 'a' * (MAX_LENGTH - len('https://example.com/'))
    assert validate_destination(url) == url


def test_the_limit_applies_to_the_normalized_form():
    # Each `ü` becomes six characters once percent-encoded.
    with pytest.raises(ValidationError):
        validate_destination('https://example.com/' + 'ü' * 400)


@pytest.mark.parametrize(
    'given',
    [
        'https://app.example.test/s/abc',
        'https://APP.example.test/S/abc',
        'http://app.example.test/s/abc?x=1',
        'https://app.example.test:8443/s/abc',
        'https://app.example.test/s',
        'https://app.example.test/s/',
        'https://app.example.test/go/abc12345',
        'https://app.example.test/GO/abc',
        'https://app.example.test/x/../s/abc',
        'https://app.example.test/./s/abc',
        'https://app.example.test/%73/abc',
        'https://app.example.test/%2e%2e/s/abc',
        'https://app.example.test/x/%2e%2e/go/abc',
        'https://app.example.test/%252e%252e/s/abc',
        'https://app.example.test//s/abc',
        'https://app.example.test/s/abc+',
    ],
)
def test_loops_to_our_own_short_links_are_refused(given):
    with pytest.raises(ValidationError) as caught:
        validate_destination(given)
    assert 'destination' in caught.value.message_dict


@pytest.mark.parametrize(
    'given',
    [
        'https://app.example.test/',
        'https://app.example.test/links/',
        'https://app.example.test/something/s/abc',
        'https://app.example.test/sale',
        'https://app.example.test/gone',
        'https://other.example.test/s/abc',
        'https://other.example.test/go/abc',
    ],
)
def test_other_pages_on_the_site_are_fine(given):
    assert validate_destination(given)


def test_destination_host():
    assert destination_host('https://Sub.Example.com:8443/x') == 'sub.example.com'
    assert destination_host('https://[2606:4700:4700::1111]/x') == '2606:4700:4700::1111'


class TestApplyUtm:
    url = 'https://example.com/page'

    def test_adds_the_parameters(self):
        result = apply_utm(self.url, utm_source='news', utm_medium='email', utm_campaign='may')
        assert result == (
            'https://example.com/page?utm_source=news&utm_medium=email&utm_campaign=may'
        )

    def test_keeps_the_rest_of_the_query_exactly_as_it_was(self):
        result = apply_utm('https://example.com/p?a=b+c&x=%41&flag', utm_source='s')
        assert result == 'https://example.com/p?a=b+c&x=%41&flag&utm_source=s'

    def test_replaces_a_parameter_that_is_already_there(self):
        result = apply_utm(
            'https://example.com/p?utm_source=old&keep=1&UTM_SOURCE=older', utm_source='new'
        )
        assert result == 'https://example.com/p?keep=1&utm_source=new'

    def test_empty_and_missing_values_change_nothing(self):
        url = 'https://example.com/p?utm_source=old'
        assert apply_utm(url, utm_source='', utm_medium=None, utm_campaign='  ') == url
        assert apply_utm(url) == url

    def test_values_are_percent_encoded(self):
        result = apply_utm(self.url, utm_campaign='spring sale & more')
        assert result == 'https://example.com/page?utm_campaign=spring%20sale%20%26%20more'

    def test_keeps_the_fragment(self):
        assert apply_utm('https://example.com/p#top', utm_term='x') == (
            'https://example.com/p?utm_term=x#top'
        )

    def test_term_and_content(self):
        result = apply_utm(self.url, utm_term='shoes', utm_content='banner')
        assert result.endswith('?utm_term=shoes&utm_content=banner')

    def test_an_unknown_parameter_is_a_programming_error(self):
        with pytest.raises(ValueError):
            apply_utm(self.url, ref='x')
