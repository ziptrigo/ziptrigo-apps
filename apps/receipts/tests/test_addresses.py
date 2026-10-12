import pytest
from django.core.exceptions import ValidationError

from ..services.addresses import matches_allowlist, normalize_address, parse_allowlist

pytestmark = [pytest.mark.unit]


@pytest.mark.parametrize(
    ('raw', 'expected'),
    [
        ('Me@Example.COM', 'me@example.com'),
        ('me+invoices@example.com', 'me@example.com'),
        ('Jane Doe <Jane+x@Example.com>', 'jane@example.com'),
        ('  me@example.com ', 'me@example.com'),
        ('noreply+caf_=user=example.com@gmail.com', 'noreply@gmail.com'),
        ('not-an-address', 'not-an-address'),
    ],
)
def test_normalize_address(raw, expected):
    assert normalize_address(raw) == expected


def test_parse_allowlist_from_text_splits_normalizes_and_dedupes():
    parsed = parse_allowlist('Me@Acme.com, *@acme.com;\nme+tag@acme.com  other@x.org')

    assert parsed == ['me@acme.com', '*@acme.com', 'other@x.org']


def test_parse_allowlist_accepts_a_list():
    assert parse_allowlist(['a@b.co', '*@b.co']) == ['a@b.co', '*@b.co']


@pytest.mark.parametrize('value', [None, '', '  ', []])
def test_parse_allowlist_empty_means_anyone(value):
    assert parse_allowlist(value) == []


@pytest.mark.parametrize('bad', ['nope', '@x.com', '*@', '*@localhost', '*.acme.com', 'a@@x.com'])
def test_parse_allowlist_rejects_invalid_entries(bad):
    with pytest.raises(ValidationError):
        parse_allowlist(bad)


def test_parse_allowlist_error_names_the_bad_entries():
    with pytest.raises(ValidationError) as exc:
        parse_allowlist('good@x.com, bad')

    assert 'bad' in exc.value.messages[0]


def test_parse_allowlist_caps_the_number_of_entries():
    with pytest.raises(ValidationError):
        parse_allowlist([f'user{i}@example.com' for i in range(101)])


def test_empty_allowlist_allows_everyone():
    assert matches_allowlist([], 'anyone@anywhere.com')


def test_exact_and_wildcard_matching():
    allow = ['me@acme.com', '*@corp.com']

    assert matches_allowlist(allow, 'Me+receipts@Acme.com')
    assert matches_allowlist(allow, 'anyone@corp.com')
    assert not matches_allowlist(allow, 'other@acme.com')


def test_wildcard_does_not_match_subdomains():
    assert not matches_allowlist(['*@corp.com'], 'a@mail.corp.com')


def test_any_of_several_addresses_may_match():
    """The From address or the envelope sender: either one is enough."""
    allow = ['me@acme.com']

    assert matches_allowlist(allow, 'forwarder@gmail.com', 'me@acme.com')
    assert not matches_allowlist(allow, 'forwarder@gmail.com', 'x@y.com')
