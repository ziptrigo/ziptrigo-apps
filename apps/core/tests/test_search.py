"""`apps.core.search`: the query language, the PostgreSQL timeout guard and the snippet helpers
(moved here from To-do's search, issue #181, for Notes, issue #260). Product-specific behaviour
(what is searched, the filters, the views) is tested in each product."""

import re
from unittest.mock import MagicMock, call

import pytest
from django.core.exceptions import ValidationError
from django.db import connection, transaction
from django.db.utils import DataError, OperationalError

from apps.core import search
from apps.core.search import (
    EMPTY_MESSAGE,
    INVALID_MESSAGE,
    MAX_QUERY_CHARS,
    TIMEOUT_MESSAGE,
    TOO_LONG_MESSAGE,
    glob_to_regex,
    lower_literals,
    make_snippet,
    normalize_glob,
    prepare_query,
    to_postgres,
)

pytestmark = pytest.mark.django_db


def snippet_of(text: str, pattern: str):
    match = re.search(pattern, text)
    assert match is not None
    return make_snippet(text, match)


def refusal(q: str, mode: str = 'glob') -> str:
    with pytest.raises(ValidationError) as caught:
        prepare_query(q, mode)
    return caught.value.messages[0]


class TestGlobToRegex:
    def test_wildcards(self):
        assert glob_to_regex('a*b?c') == 'a.*b.c'

    def test_everything_else_is_escaped(self):
        assert glob_to_regex('a.b (c)') == r'a\.b\ \(c\)'
        assert glob_to_regex('plain') == 'plain'

    def test_a_query_without_wildcards_is_a_substring_match(self):
        assert re.search(glob_to_regex('rent'), 'parent')
        assert re.search(glob_to_regex('pay*rent'), 'please pay the rent now')
        assert not re.search(glob_to_regex('rent'), 'rnet')


class TestNormalisation:
    def test_accents_case_and_punctuation_are_ignored(self):
        assert normalize_glob('Cão') == 'cao'
        assert normalize_glob('Pay the RENT!') == 'pay the rent'
        assert normalize_glob("  don't  stop  ") == 'don t stop'

    def test_wildcards_survive(self):
        assert normalize_glob('Pay*RENT?') == 'pay*rent?'

    def test_a_separator_next_to_a_wildcard_is_kept_as_one_space(self):
        assert normalize_glob('pay *') == 'pay *'
        assert normalize_glob('a * b') == 'a * b'
        assert normalize_glob('pay, * rent') == 'pay * rent'

    def test_a_run_of_stars_is_one(self):
        assert normalize_glob('a***b') == 'a*b'

    def test_only_punctuation_is_empty(self):
        assert normalize_glob('!!! ...') == ''


class TestPrepareQuery:
    def test_glob_is_the_default(self):
        query = prepare_query('Cão*Rent')
        assert query.mode == 'glob' and query.pattern == 'cao.*rent'

    def test_slashes_mean_regex_whatever_the_mode(self):
        for mode in ('glob', 'regex'):
            query = prepare_query('/pay.*rent/', mode)
            assert query.mode == 'regex' and query.pattern == 'pay.*rent'

    def test_regex_mode_lowers_the_letters_the_user_typed(self):
        query = prepare_query('Pay.*RENT', 'regex')
        assert query.pattern == 'pay.*rent'

    def test_lowering_leaves_escapes_alone(self):
        # `\D` is "not a digit", `\d` is the opposite: only literal characters are lower-cased.
        assert prepare_query(r'\D+ \W \Bab \x4F A\.B [A-C]', 'regex').pattern == (
            r'\D+ \W \Bab \x4f a\.b [a-c]'
        )
        assert lower_literals(r'\\A') == r'\\a'  # an escaped backslash, then a letter
        assert lower_literals('İ') == 'İ'  # lowering would make two characters

    def test_one_character_between_slashes_is_a_regex(self):
        assert prepare_query('/a/').mode == 'regex'

    def test_two_slashes_are_not(self):
        # Nothing between them: a glob of two slashes, which normalises to nothing.
        assert refusal('//') == EMPTY_MESSAGE

    def test_an_unknown_mode_is_glob(self):
        assert prepare_query('a.b', 'whatever').pattern == 'a\\ b'

    def test_empty_after_normalisation(self):
        assert refusal('') == EMPTY_MESSAGE
        assert refusal('   ') == EMPTY_MESSAGE
        assert refusal('!!!') == EMPTY_MESSAGE
        assert refusal('', 'regex') == EMPTY_MESSAGE

    def test_a_lone_wildcard_is_allowed(self):
        assert prepare_query('*').pattern == '.*'

    def test_too_long(self):
        assert refusal('a' * (MAX_QUERY_CHARS + 1)) == TOO_LONG_MESSAGE
        assert refusal('/' + 'a' * (MAX_QUERY_CHARS + 1) + '/') == TOO_LONG_MESSAGE
        assert refusal('a' * (MAX_QUERY_CHARS + 1), 'regex') == TOO_LONG_MESSAGE

    def test_the_longest_is_fine(self):
        assert prepare_query('a' * MAX_QUERY_CHARS)
        assert prepare_query('/' + 'a' * MAX_QUERY_CHARS + '/')

    def test_an_invalid_regex(self):
        assert refusal('/(/') == INVALID_MESSAGE
        assert refusal('[a', 'regex') == INVALID_MESSAGE
        assert refusal('*abc', 'regex') == INVALID_MESSAGE

    @pytest.mark.parametrize('pattern', [r'(a)\1', r'(a)(b)\2', r'(?P<x>a)(?P=x)', r'(a)(?(1)b)'])
    def test_backreferences_are_refused(self, pattern):
        assert refusal(pattern, 'regex') == INVALID_MESSAGE

    @pytest.mark.parametrize('pattern', ['a(?=b)', 'a(?!b)', '(?<=a)b', '(?<!a)b'])
    def test_lookarounds_are_refused(self, pattern):
        assert refusal(pattern, 'regex') == INVALID_MESSAGE

    @pytest.mark.parametrize(
        'pattern',
        [
            '(?>a+)b',  # atomic group
            'a*+b',
            'a++b',
            'a?+b',
            'a{2}+b',
            'a{2,}+b',
            'a{,3}+b',  # possessive quantifiers
            '(?P<word>a)b',
            r'\N{LATIN SMALL LETTER A}',
        ],
    )
    def test_python_only_syntax_is_refused(self, pattern):
        assert refusal(pattern, 'regex') == INVALID_MESSAGE

    @pytest.mark.parametrize(
        'pattern',
        [
            'a+?b',  # lazy
            'a*?b',
            r'a\++b',  # an escaped plus, then a quantifier
            '[+*?]+',  # in a bracket expression they are literals
            '[(?=]',
            '[]a+]+',
            'a{2}?',
            'a{x}+',  # not a quantifier: literal text
            r'\\N{',
        ],
    )
    def test_look_alikes_are_not_refused(self, pattern):
        assert prepare_query(pattern, 'regex')

    def test_an_escaped_backslash_before_a_digit_is_not_a_backreference(self):
        assert prepare_query(r'a\\1', 'regex')

    def test_other_escapes_are_fine(self):
        assert prepare_query(r'\d+ \w+ (a|b)+ [a-c]{2}', 'regex')

    def test_the_word_boundary_is_spelled_for_postgresql(self):
        assert to_postgres(r'\bpay\b') == r'\ypay\y'
        assert to_postgres(r'\Bx') == r'\Yx'
        assert to_postgres(r'[\b]') == r'[\b]'
        assert to_postgres(r'\\b') == r'\\b'
        assert to_postgres(r'\d+') == r'\d+'

    def test_a_closing_bracket_first_in_a_class_is_a_literal(self):
        # `[]\b]` is the class of `]` and backspace: the `\b` is still inside it.
        assert to_postgres(r'[]\b]\b') == r'[]\b]\y'
        assert to_postgres(r'[^]\b]\b') == r'[^]\b]\y'
        assert to_postgres(r'[a]\b') == r'[a]\y'
        assert to_postgres(r'[\]]\b') == r'[\]]\y'


class TestSnippetHelpers:
    def test_sixty_characters_of_context_either_side(self):
        text = 'a' * 100 + ' rent ' + 'b' * 100

        snippet = snippet_of(text, 'rent')

        assert snippet == '…' + 'a' * 59 + ' <mark>rent</mark> ' + 'b' * 59 + '…'

    def test_no_ellipsis_when_the_text_is_short(self):
        assert snippet_of('a rent b', 'rent') == 'a <mark>rent</mark> b'

    def test_a_long_match_highlights_only_its_start(self):
        text = 'a' + 'x' * 300 + 'b'
        snippet = snippet_of(text, 'a.*b')

        assert snippet.count('x') == 99 + 60

    def test_an_empty_match_has_no_mark(self):
        assert '<mark>' not in snippet_of('abc', 'x*')

    def test_compiling_is_cheap_and_failures_are_none(self):
        assert search.snippet_regex('(') is None
        assert search.snippet_regex('(?:a{0,65535}){0,65535}x') is not None
        assert search.snippet_regex('a{1000}{1000}') is None


class TestSearchGuard:
    @pytest.fixture
    def pg(self, monkeypatch):
        """`search.connection` as a PostgreSQL one whose cursor is a mock; the real database
        (SQLite) still runs the queries."""
        fake = MagicMock()
        fake.vendor = 'postgresql'
        cursor = MagicMock()
        fake.cursor.return_value.__enter__.return_value = cursor
        monkeypatch.setattr(search, 'connection', fake)
        return cursor

    def test_the_timeout_is_set_locally(self, pg):
        with search.search_guard():
            pass

        assert call('SET LOCAL statement_timeout = %s', ['1000']) in pg.execute.call_args_list

    def test_the_guard_puts_the_previous_timeout_back(self, pg):
        pg.fetchone.return_value = ('30s',)

        with search.search_guard():
            pass

        assert pg.execute.call_args_list == [
            call('SELECT current_setting(%s)', ['statement_timeout']),
            call('SET LOCAL statement_timeout = %s', ['1000']),
            call('SELECT set_config(%s, %s, true)', ['statement_timeout', '30s']),
        ]

    def test_inside_an_outer_transaction_the_timeout_does_not_leak(self, pg):
        # `SET LOCAL` outlives a released savepoint: the guard restores the value itself, before
        # the outer transaction goes on, and in the same savepoint.
        pg.fetchone.return_value = ('0',)

        with transaction.atomic():
            with search.search_guard():
                pass
            assert pg.execute.call_args_list[-1] == call(
                'SELECT set_config(%s, %s, true)', ['statement_timeout', '0']
            )
            assert connection.in_atomic_block

    def test_after_an_error_the_rollback_undoes_it(self, pg):
        # The savepoint (or transaction) is rolled back, which reverts a `SET LOCAL` made inside
        # it, so nothing is restored by hand -- and a restore could not run in an aborted one.
        pg.fetchone.return_value = ('0',)

        with pytest.raises(OperationalError), search.search_guard():
            raise OperationalError('connection lost')

        assert [c.args[0] for c in pg.execute.call_args_list] == [
            'SELECT current_setting(%s)',
            'SET LOCAL statement_timeout = %s',
        ]

    def test_a_nested_guard_is_a_savepoint_of_the_real_connection(self, pg):
        # The guard uses the real `atomic()`: nested, it opens a savepoint rather than failing.
        with transaction.atomic():
            depth = len(connection.savepoint_ids)
            with search.search_guard():
                assert len(connection.savepoint_ids) == depth + 1

    def test_a_statement_timeout_is_a_validation_error(self, pg):
        with pytest.raises(ValidationError) as caught:
            with search.search_guard():
                raise OperationalError('canceling statement due to statement timeout')

        assert caught.value.messages == [TIMEOUT_MESSAGE]

    def test_a_pattern_the_server_rejects_is_a_validation_error(self, pg):
        with pytest.raises(ValidationError) as caught:
            with search.search_guard():
                raise DataError('invalid regular expression: invalid escape')

        assert caught.value.messages == [TIMEOUT_MESSAGE]

    def test_another_operational_error_is_not_swallowed(self, pg):
        with pytest.raises(OperationalError):
            with search.search_guard():
                raise OperationalError('connection lost')

    def test_the_word_boundary_is_translated_for_the_server_only(self, pg):
        query = prepare_query(r'/\brent\b/')

        assert query.pattern == r'\brent\b' and query.db_pattern == r'\yrent\y'

    def test_the_guard_does_nothing_on_sqlite(self):
        with search.search_guard():
            pass
