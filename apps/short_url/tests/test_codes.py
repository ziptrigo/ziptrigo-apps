import string

import pytest
from django.core.exceptions import ValidationError

from ..models import LinkStatus
from ..services import (
    AliasTakenError,
    generate_code,
    is_code_available,
    is_reserved,
    normalize_key,
    validate_alias,
)
from ..services import codes as codes_module
from .factories import create_link

pytestmark = pytest.mark.django_db


def test_generated_codes_are_seven_base62_characters():
    allowed = set(string.ascii_letters + string.digits)
    for _ in range(50):
        code = generate_code()
        assert len(code) == 7
        assert set(code) <= allowed


def test_generated_codes_differ():
    assert len({generate_code() for _ in range(50)}) == 50


def test_generation_retries_a_taken_code(monkeypatch, user):
    create_link(user, code='AAAAAAA')
    picks = iter('AAAAAAA' + 'BBBBBBB')
    monkeypatch.setattr(codes_module.secrets, 'choice', lambda alphabet: next(picks))
    assert generate_code() == 'BBBBBBB'


def test_generation_gives_up_instead_of_looping_forever(monkeypatch):
    monkeypatch.setattr(codes_module, 'is_available', lambda code: False)
    with pytest.raises(RuntimeError):
        generate_code()


def test_codes_are_compared_case_insensitively(user):
    create_link(user, code='Sale')
    assert normalize_key(' SALE ') == 'sale'
    assert not is_code_available('sale')
    assert not is_code_available('SALE')
    assert is_code_available('sale2')


def test_a_deleted_links_code_is_never_available_again(user):
    create_link(user, code='gone1', status=LinkStatus.DELETED)
    assert not is_code_available('gone1')
    with pytest.raises(AliasTakenError):
        validate_alias('GONE1')


@pytest.mark.parametrize('alias', ['abc', 'My-Link_1', 'a' * 32, '123', '___', '---'])
def test_valid_aliases(alias):
    assert validate_alias(alias) == alias


def test_an_alias_is_stripped():
    assert validate_alias('  spring  ') == 'spring'


@pytest.mark.parametrize(
    'alias',
    ['', 'ab', 'a' * 33, 'has space', 'dot.dot', 'slash/slash', 'ünï', 'a+b', 'a?b', '<b>x</b>'],
)
def test_malformed_aliases_are_refused(alias):
    with pytest.raises(ValidationError) as caught:
        validate_alias(alias)
    assert 'alias' in caught.value.message_dict
    assert not isinstance(caught.value, AliasTakenError)


@pytest.mark.parametrize('alias', ['admin', 'ADMIN', 'Api', 'go', 'qr', 'billing', 'login', 's'])
def test_reserved_words_are_refused(alias):
    if len(alias) < 3:
        # Too short to be an alias at all; still reserved for the check on a code.
        assert is_reserved(alias)
        return
    with pytest.raises(AliasTakenError):
        validate_alias(alias)
    assert is_reserved(alias)


def test_the_listed_reserved_words_are_all_reserved():
    for word in ('admin', 'api', 'accounts', 'auth', 'billing', 'qr', 'go', 't', 's', 'transfer'):
        assert is_reserved(word)
    for word in ('receipts', 'feedback', 'static', 'media', 'login', 'logout', 'help'):
        assert is_reserved(word)


def test_a_taken_alias_says_nothing_about_who_has_it(user):
    create_link(user, code='mine')
    with pytest.raises(AliasTakenError) as caught:
        validate_alias('Mine')
    assert caught.value.message_dict == {'alias': ['That alias is not available.']}
