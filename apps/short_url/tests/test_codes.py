import string

import pytest
from django.core.exceptions import ValidationError

from ..models import LinkStatus, RetiredCode, ShortLink
from ..services import (
    AliasTakenError,
    LinkInput,
    generate_code,
    is_code_available,
    is_reserved,
    normalize_key,
    validate_alias,
)
from ..services import codes as codes_module
from ..services import create_link as create_link_service
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


class TestRetiredCodes:
    def test_deleting_the_owner_keeps_their_codes_taken(self, user, other_user):
        create_link(user, code='Mine01', is_alias=True)
        create_link(user, code='rand001', status=LinkStatus.DELETED)
        user.delete()
        assert not ShortLink.objects.filter(code_key='mine01').exists()
        assert RetiredCode.objects.filter(code_key__in=['mine01', 'rand001']).count() == 2
        assert not is_code_available('MINE01')
        with pytest.raises(AliasTakenError):
            validate_alias('mine01')
        with pytest.raises(AliasTakenError):
            create_link_service(other_user, LinkInput(destination='https://e.test', alias='Mine01'))
        assert not ShortLink.objects.filter(owner=other_user).exists()

    def test_the_generator_skips_a_retired_code(self, monkeypatch, user):
        RetiredCode.objects.create(code_key='aaaaaaa')
        picks = iter('AAAAAAA' + 'BBBBBBB')
        monkeypatch.setattr(codes_module.secrets, 'choice', lambda alphabet: next(picks))
        assert generate_code() == 'BBBBBBB'

    def test_retiring_twice_is_harmless(self, user):
        create_link(user, code='twice1')
        codes_module.retire_codes_of(user)
        codes_module.retire_codes_of(user)
        assert RetiredCode.objects.filter(code_key='twice1').count() == 1

    def test_another_users_codes_are_not_retired(self, user, other_user):
        create_link(other_user, code='stays1')
        user.delete()
        assert not RetiredCode.objects.filter(code_key='stays1').exists()
