"""Search (issue #181): the query language, what is and is not searched, the snippets, the
PostgreSQL timeout branch, the structured filters and the search views."""

import re
import time
from datetime import date, timedelta
from html import unescape
from types import SimpleNamespace
from unittest.mock import MagicMock, call

import pytest
from django.core.exceptions import ValidationError
from django.db import connection, transaction
from django.db.utils import DataError, OperationalError
from django.http import QueryDict
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from apps.accounts.services.timezones import user_today

from ..models import Comment, CommentRevision
from ..services import (
    FilterParams,
    add_comment,
    complete_task,
    create_task,
    delete_comment,
    describe_params,
    edit_comment,
    filter_page,
    filter_tasks,
    glob_to_regex,
    parse_filter_params,
    search_tasks,
    trash_task,
    update_task,
)
from ..services import search as search_module
from ..services.reminders import add_reminder
from ..services.search import (
    EMPTY_MESSAGE,
    INVALID_MESSAGE,
    MAX_QUERY_CHARS,
    SNIPPET_TIME_BUDGET,
    TIMEOUT_MESSAGE,
    TOO_LONG_MESSAGE,
    lower_literals,
    make_snippet,
    normalize_glob,
    prepare_query,
    to_postgres,
)
from .factories import make_label, make_task, make_user

pytestmark = pytest.mark.django_db

HTMX = {'HTTP_HX_REQUEST': 'true'}


def numbers(page) -> list[int]:
    return [task.number for task in page.object_list]


def find(user, q, **kwargs):
    return search_tasks(user, q=q, **kwargs)


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


class TestWhatIsSearched:
    def test_accents_and_case(self, user):
        make_task(user, number=1001, title='Walk the Cão')
        make_task(user, number=1002, title='Nothing')

        assert numbers(find(user, 'cao')) == [1001]
        assert numbers(find(user, 'CÃO')) == [1001]
        assert numbers(find(user, 'walk the cao')) == [1001]

    def test_punctuation_and_formatting_in_the_text(self, user):
        make_task(
            user, number=1001, title='Fix: the roof!', description='Call **Mr. Rent** about it'
        )

        assert numbers(find(user, 'fix the roof')) == [1001]
        assert numbers(find(user, 'rent')) == [1001]
        assert numbers(find(user, 'mr rent')) == [1001]
        assert numbers(find(user, '**rent**')) == [1001]

    def test_punctuation_in_the_query(self, user):
        make_task(user, number=1001, title='Pay the rent')

        assert numbers(find(user, 'Pay the RENT!')) == [1001]
        assert numbers(find(user, 'pay, the... rent')) == [1001]

    def test_a_match_in_the_description(self, user):
        make_task(user, number=1001, title='T', description='Remember the *garage* code')

        assert numbers(find(user, 'garage code')) == [1001]

    def test_link_destinations_and_the_markup_are_not_text(self, user):
        make_task(user, number=1001, title='T', description='[docs](https://example.com/secret)')

        assert numbers(find(user, 'docs')) == [1001]
        assert numbers(find(user, 'secret')) == []

    def test_a_match_in_a_comment(self, user):
        task = make_task(user, number=1001, title='Quiet task')
        add_comment(task, user, 'The **plumber** called again')

        assert numbers(find(user, 'plumber')) == [1001]

    def test_a_deleted_comment_is_not_found_and_a_restored_one_is(self, user):
        task = make_task(user, number=1001, title='Quiet task')
        comment = add_comment(task, user, 'the plumber called')
        delete_comment(comment)

        assert numbers(find(user, 'plumber')) == []

    def test_earlier_edits_are_not_searched(self, user):
        task = make_task(user, number=1001, title='Quiet task')
        comment = add_comment(task, user, 'buy apples')
        edit_comment(comment, 'buy pears')

        assert numbers(find(user, 'pears')) == [1001]
        assert numbers(find(user, 'apples')) == []
        assert CommentRevision.objects.filter(body='buy apples').exists()

    def test_a_task_with_two_matching_comments_is_listed_once(self, user):
        task = make_task(user, number=1001, title='Quiet task')
        add_comment(task, user, 'rent one')
        add_comment(task, user, 'rent two')

        page = find(user, 'rent')

        assert numbers(page) == [1001] and page.paginator.count == 1

    def test_only_the_users_own_tasks(self, user, other_user):
        make_task(other_user, number=1001, title='rent')
        make_task(user, number=1001, title='rent mine')

        page = find(user, 'rent')

        assert [task.user_id for task in page.object_list] == [user.pk]

    def test_trashed_tasks_are_not_found(self, funded_user):
        task = create_task(funded_user, title='Pay the rent')
        trash_task(task)

        assert numbers(find(funded_user, 'rent')) == []

    def test_a_comment_of_a_trashed_task_is_not_found(self, user):
        task = make_task(user, number=1001, title='Quiet')
        add_comment(task, user, 'findme')
        make_task(user, number=1002, title='Other')
        assert numbers(find(user, 'findme')) == [1001]

        trash_task(task)

        assert numbers(find(user, 'findme')) == []


class TestTaskNumber:
    def test_the_number_finds_the_task_and_what_mentions_it(self, user):
        make_task(user, number=1000, title='Call the dentist')
        make_task(user, number=1001, title='Ticket 1000 follow-up')
        make_task(user, number=1002, title='Quiet', description='see 1000 for details')
        commented = make_task(user, number=1003, title='Other')
        add_comment(commented, user, 'duplicate of 1000')
        make_task(user, number=1004, title='Unrelated')

        assert sorted(numbers(find(user, '1000'))) == [1000, 1001, 1002, 1003]

    def test_a_hash_prefix_works(self, user):
        make_task(user, number=1000, title='Call the dentist')
        make_task(user, number=1001, title='Unrelated')

        assert numbers(find(user, '#1000')) == [1000]

    def test_the_number_is_exact(self, user):
        make_task(user, number=1000, title='A')
        make_task(user, number=1001, title='B')
        make_task(user, number=10000, title='C')

        assert numbers(find(user, '100')) == []
        assert numbers(find(user, '1000')) == [1000]

    def test_text_is_not_a_number(self, user):
        make_task(user, number=1000, title='Call the dentist')

        assert numbers(find(user, 'dentist 1000')) == []
        assert numbers(find(user, '10*')) == []
        assert numbers(find(user, '/1000/', mode='regex')) == []

    def test_the_number_respects_trash_completed_and_owner(self, user, other_user):
        done = make_task(user, number=1000, title='Done')
        complete_task(done)
        trashed = make_task(user, number=1001, title='Gone')
        trash_task(trashed)
        make_task(other_user, number=1002, title='Theirs')

        assert numbers(find(user, '1000')) == []
        assert numbers(find(user, '1000', include_completed=True)) == [1000]
        assert numbers(find(user, '1001')) == []
        assert numbers(find(user, '1002')) == []

    def test_a_saved_filter_condition_includes_the_number(self, user):
        make_task(user, number=1000, title='Call the dentist')
        make_task(user, number=1001, title='Unrelated')

        params = FilterParams(q='1000')

        assert numbers(filter_page(user, params)) == [1000]


class TestGlobWildcards:
    @pytest.fixture(autouse=True)
    def _tasks(self, user):
        make_task(user, number=1001, title='Please pay the rent')
        make_task(user, number=1002, title='Visit the parent')
        make_task(user, number=1003, title='Pay later')

    def test_star(self, user):
        assert numbers(find(user, 'pay*rent')) == [1001]

    def test_the_pattern_is_unanchored(self, user):
        assert sorted(numbers(find(user, 'rent'))) == [1001, 1002]

    def test_question_mark_is_one_character(self, user):
        assert numbers(find(user, 'pa?ent')) == [1002]
        assert numbers(find(user, 'pa??ent')) == []

    def test_star_may_match_nothing(self, user):
        assert numbers(find(user, 'pay*later')) == [1003]
        assert numbers(find(user, 'visit*parent')) == [1002]

    def test_a_dot_is_not_a_wildcard(self, user):
        assert numbers(find(user, 'pa.ent')) == []


class TestRegex:
    @pytest.fixture(autouse=True)
    def _tasks(self, user):
        make_task(user, number=1001, title='Pay the rent')
        make_task(user, number=1002, title='Pay the electricity')
        make_task(user, number=1003, title='Rent a car')

    def test_slashes(self, user):
        assert sorted(numbers(find(user, '/pay.*(rent|electricity)/'))) == [1001, 1002]

    def test_the_checkbox(self, user):
        assert numbers(find(user, 'pay the (rent)', mode='regex')) == [1001]

    def test_the_text_is_lower_case_so_the_pattern_is_lowered_too(self, user):
        assert sorted(numbers(find(user, '/Rent/'))) == [1001, 1003]
        assert sorted(numbers(find(user, '/rent/'))) == [1001, 1003]

    def test_anchors(self, user):
        assert numbers(find(user, '/^rent/')) == [1003]
        assert numbers(find(user, '/rent$/')) == [1001]

    def test_slashes_win_over_the_glob_mode(self, user):
        # As a glob this would be the literal text "?" after "pa".
        assert numbers(find(user, '/^pay (the )?rent$/', mode='glob')) == [1001]

    def test_refusals_run_nothing(self, user):
        with pytest.raises(ValidationError):
            find(user, r'/(pay)\1/')
        with pytest.raises(ValidationError):
            find(user, '/(?=pay)/')

    def test_a_pattern_is_a_parameter_not_sql(self, user):
        assert numbers(find(user, "/'; drop table todo_task; --/")) == []
        assert numbers(find(user, '/rent/')) != []


class TestIncludeCompleted:
    def test_completed_tasks_are_left_out_by_default(self, funded_user):
        done = create_task(funded_user, title='Pay the rent')
        create_task(funded_user, title='Rent a car')
        complete_task(done)

        assert [t.title for t in find(funded_user, 'rent').object_list] == ['Rent a car']

    def test_and_listed_after_the_open_ones_when_included(self, funded_user):
        done = create_task(funded_user, title='Pay the rent', due_date=date(2026, 1, 1))
        create_task(funded_user, title='Rent a car', due_date=date(2026, 2, 1))
        complete_task(done)

        page = find(funded_user, 'rent', include_completed=True)

        assert [t.title for t in page.object_list] == ['Rent a car', 'Pay the rent']

    def test_an_open_task_with_a_later_date_still_precedes_a_completed_one(self, funded_user):
        done = create_task(funded_user, title='rent early', due_date=date(2026, 1, 1))
        create_task(funded_user, title='rent late', due_date=date(2030, 1, 1))
        create_task(funded_user, title='rent undated')
        complete_task(done)

        titles = [t.title for t in find(funded_user, 'rent', include_completed=True).object_list]

        assert titles == ['rent late', 'rent undated', 'rent early']


class TestOrderingAndPaging:
    def test_the_foundations_default_order(self, user):
        make_task(user, number=1001, title='rent c', due_date=date(2026, 3, 1))
        make_task(user, number=1002, title='rent a', due_date=date(2026, 1, 1))
        make_task(user, number=1003, title='rent b')

        assert numbers(find(user, 'rent')) == [1002, 1001, 1003]

    def test_the_sort_comes_from_the_filters(self, user):
        make_task(user, number=1001, title='rent b', priority=0)
        make_task(user, number=1002, title='rent a', priority=2)
        make_task(user, number=1003, title='rent c', priority=1)

        assert numbers(find(user, 'rent', filters=FilterParams(sort='priority'))) == [
            1003,
            1002,
            1001,
        ]
        assert numbers(find(user, 'rent', filters=FilterParams(sort='title'))) == [
            1002,
            1001,
            1003,
        ]

    def test_fifty_per_page(self, user):
        for n in range(60):
            make_task(user, number=2000 + n, title=f'rent {n:02}')

        first = find(user, 'rent')
        second = find(user, 'rent', page=2)

        assert len(first.object_list) == 50 and len(second.object_list) == 10
        assert first.paginator.count == 60 and second.number == 2

    def test_a_bad_page_number_is_the_nearest_page(self, user):
        make_task(user, number=1001, title='rent')

        assert find(user, 'rent', page='abc').number == 1
        assert find(user, 'rent', page=99).number == 1

    def test_the_decorator_loads_the_rows_in_order(self, user):
        make_task(user, number=1001, title='rent b', due_date=date(2026, 3, 1))
        make_task(user, number=1002, title='rent a', due_date=date(2026, 1, 1))

        from ..views.lists import decorate

        page = find(user, 'rent', decorate=decorate)

        assert numbers(page) == [1002, 1001]
        assert hasattr(page.object_list[0], 'subtask_total') and hasattr(
            page.object_list[0], 'comment_count'
        )

    def test_comments_do_not_multiply_the_subtask_counts(self, user):
        parent = make_task(user, number=1001, title='rent parent')
        make_task(user, number=1002, title='sub', parent=parent)
        add_comment(parent, user, 'rent one')
        add_comment(parent, user, 'rent two')

        from ..views.lists import decorate

        page = find(user, 'rent', decorate=decorate)

        assert page.object_list[0].subtask_total == 1
        assert page.object_list[0].comment_count == 2


class TestSnippets:
    def test_a_title_match(self, user):
        make_task(user, number=1001, title='Pay the Rent')

        task = find(user, 'rent').object_list[0]

        assert task.search_field == 'title'
        assert task.search_snippet == 'pay the <mark>rent</mark>'

    def test_a_description_match(self, user):
        make_task(user, number=1001, title='Quiet', description='Call the **landlord** today')

        task = find(user, 'landlord').object_list[0]

        assert task.search_field == 'description'
        assert '<mark>landlord</mark>' in task.search_snippet

    def test_a_comment_match(self, user):
        task = make_task(user, number=1001, title='Quiet')
        add_comment(task, user, 'The plumber called')

        found = find(user, 'plumber').object_list[0]

        assert found.search_field == 'comment'
        assert found.search_snippet == 'the <mark>plumber</mark> called'

    def test_the_title_wins_over_the_description_and_comments(self, user):
        task = make_task(user, number=1001, title='rent', description='rent')
        add_comment(task, user, 'rent')

        assert find(user, 'rent').object_list[0].search_field == 'title'

    def test_a_deleted_comment_is_not_the_snippet(self, user):
        task = make_task(user, number=1001, title='Quiet', description='the rent is due')
        comment = add_comment(task, user, 'rent in a comment')
        delete_comment(comment)

        assert find(user, 'rent').object_list[0].search_field == 'description'

    def test_sixty_characters_of_context_either_side(self):
        text = 'a' * 100 + ' rent ' + 'b' * 100

        snippet = snippet_of(text, 'rent')

        assert snippet == '…' + 'a' * 59 + ' <mark>rent</mark> ' + 'b' * 59 + '…'

    def test_no_ellipsis_when_the_text_is_short(self):
        assert snippet_of('a rent b', 'rent') == 'a <mark>rent</mark> b'

    def test_the_text_is_escaped(self, user):
        make_task(user, number=1001, title='x', description='<script>alert(1)</script> rent')

        task = find(user, 'rent').object_list[0]

        assert '<script>' not in task.search_snippet

    def test_a_long_match_highlights_only_its_start(self):
        text = 'a' + 'x' * 300 + 'b'
        snippet = snippet_of(text, 'a.*b')

        assert snippet.count('x') == 99 + 60

    def test_an_empty_match_has_no_mark(self):
        assert '<mark>' not in snippet_of('abc', 'x*')

    def test_no_snippet_without_a_search(self, user):
        make_task(user, number=1001, title='x')

        page = filter_page(user, FilterParams())

        assert page.object_list[0].search_snippet == '' and page.object_list[0].search_field == ''


def _four_as_then_c(text: str) -> bool:
    index = -1
    for _ in range(4):
        index = text.find('a', index + 1)
        if index < 0:
            return False
    return 'c' in text[index + 1 :]


#: What PostgreSQL's linear-time engine would answer for the patterns the bound tests use, in
#: linear time: SQLite's `REGEXP` is Python's backtracking `re`, which cannot even run them.
LINEAR_ANSWERS = {
    'a.*a.*a.*a.*c': _four_as_then_c,
    '(a+)+b': lambda text: 'ab' in text,
    '(a|aa)+$': lambda text: text.endswith('a'),
}


@pytest.fixture
def database_regexp():
    """The database side of a search as PostgreSQL would run it (see `LINEAR_ANSWERS`)."""
    from django.db.backends.sqlite3._functions import _sqlite_regexp

    def regexp(pattern, text):
        return int(LINEAR_ANSWERS[pattern](text or ''))

    connection.ensure_connection()
    connection.connection.create_function('regexp', 2, regexp, deterministic=True)
    yield
    connection.connection.create_function('regexp', 2, _sqlite_regexp, deterministic=True)


class TestSnippetBounds:
    """The snippet pass runs the user's pattern in Python: it must be bounded (review of #196)."""

    def test_the_glob_that_made_python_re_run_for_minutes(self, user, database_regexp):
        # `a*a*a*a*c` is `a.*a.*a.*a.*c`: about n^5 steps for `re` on a 500-character title.
        make_task(user, number=1001, title='a' * 500, description='aaaac')

        started = time.monotonic()
        page = find(user, 'a*a*a*a*c')
        elapsed = time.monotonic() - started

        assert elapsed < 2
        [task] = page.object_list
        assert task.number == 1001
        # The title did not match in the database, so it is never scanned: the snippet is the
        # description's.
        assert task.search_field == 'description' and '<mark>aaaac</mark>' in task.search_snippet

    def test_the_nested_quantifier(self, user, database_regexp):
        make_task(user, number=1001, title='a' * 500, description='ab')

        started = time.monotonic()
        page = find(user, '/(a+)+b/')

        assert time.monotonic() - started < 2
        assert numbers(page) == [1001]

    def test_a_matching_field_that_backtracks_is_listed_without_a_snippet(
        self, user, database_regexp
    ):
        # The database matches (it ends in "a"); Python's search from the start would take
        # 2^60 steps. The row is returned, bare.
        make_task(user, number=1001, title='a' * 60 + 'b' + 'a')
        make_task(user, number=1002, title='ba', priority=2)

        started = time.monotonic()
        page = find(user, r'/(a|aa)+$/')
        elapsed = time.monotonic() - started

        assert elapsed < SNIPPET_TIME_BUDGET + 2
        assert sorted(numbers(page)) == [1001, 1002]
        hostile = next(task for task in page.object_list if task.number == 1001)
        assert hostile.search_snippet == '' and hostile.search_field == ''

    def test_an_exhausted_budget_lists_the_rows_without_snippets(self, user, monkeypatch):
        make_task(user, number=1001, title='rent')
        make_task(user, number=1002, title='rent too')
        monkeypatch.setattr(search_module, 'SNIPPET_TIME_BUDGET', 0)

        page = find(user, 'rent')

        assert sorted(numbers(page)) == [1001, 1002]
        assert all(task.search_snippet == '' for task in page.object_list)

    def test_a_timeout_ends_the_snippets_and_keeps_the_rows(self, user, monkeypatch):
        make_task(user, number=1001, title='rent')
        make_task(user, number=1002, title='rent too')

        class Slow:
            calls: list[float] = []

            def search(self, text, timeout):
                self.calls.append(timeout)
                raise TimeoutError

        slow = Slow()
        monkeypatch.setattr(search_module, 'snippet_regex', lambda pattern: slow)

        page = find(user, 'rent')

        assert sorted(numbers(page)) == [1001, 1002]
        assert all(task.search_snippet == '' for task in page.object_list)
        assert len(slow.calls) == 1  # the second row was not even tried
        assert 0 < slow.calls[0] <= SNIPPET_TIME_BUDGET

    def test_each_search_gets_what_is_left_of_the_page_budget(self, user, monkeypatch):
        make_task(user, number=1001, title='rent')
        make_task(user, number=1002, title='rent too')
        clock = iter([0.0, 0.05, 0.12, 0.3, 0.4])
        monkeypatch.setattr(search_module, 'time', SimpleNamespace(monotonic=lambda: next(clock)))
        timeouts = []
        real = search_module.snippet_regex

        class Spy:
            def __init__(self, compiled):
                self.compiled = compiled

            def search(self, text, timeout):
                timeouts.append(round(timeout, 3))
                return self.compiled.search(text, timeout=timeout)

        monkeypatch.setattr(search_module, 'snippet_regex', lambda pattern: Spy(real(pattern)))

        page = find(user, 'rent')

        # The clock reads 0 (deadline 0.2), then 0.05 (0.15 left for the first search), 0.12
        # (0.08 left for the second) and 0.3 (past the deadline).
        assert timeouts == [pytest.approx(0.15), pytest.approx(0.08)]
        assert len(page.object_list) == 2

    def test_only_the_fields_the_database_matched_are_scanned(self, user, monkeypatch):
        make_task(user, number=1001, title='zzz title ' + 'q' * 400, description='a rent here')
        scanned = []
        real = search_module.snippet_regex

        class Spy:
            def __init__(self, compiled):
                self.compiled = compiled

            def search(self, text, timeout):
                scanned.append(text)
                return self.compiled.search(text, timeout=timeout)

        monkeypatch.setattr(search_module, 'snippet_regex', lambda pattern: Spy(real(pattern)))

        task = find(user, 'rent').object_list[0]

        assert scanned == ['a rent here'] and task.search_field == 'description'

    def test_a_comment_is_read_only_when_the_title_and_description_did_not_match(self, user):
        titled = make_task(user, number=1001, title='rent')
        add_comment(titled, user, 'rent in a comment')
        commented = make_task(user, number=1002, title='Quiet')
        add_comment(commented, user, 'first, nothing')
        add_comment(commented, user, 'the rent is late ' + 'x ' * 4000)
        add_comment(commented, user, 'rent again')

        with CaptureQueriesContext(connection) as queries:
            page = find(user, 'rent')

        by_number = {task.number: task for task in page.object_list}
        assert by_number[1001].search_field == 'title'
        assert by_number[1002].search_field == 'comment'
        assert by_number[1002].search_snippet.startswith('the <mark>rent</mark> is late')
        reads = [q['sql'] for q in queries if 'SUBSTR(' in q['sql'].upper()]
        # One query, for the one task that needs it, and it takes one cut comment, not all three.
        assert len(reads) == 1 and reads[0].upper().count('LIMIT 1') >= 1

    def test_no_comment_query_when_every_row_matched_in_its_title(self, user):
        task = make_task(user, number=1001, title='rent')
        add_comment(task, user, 'rent too')

        with CaptureQueriesContext(connection) as queries:
            find(user, 'rent')

        assert not [q for q in queries if 'SUBSTR(' in q['sql'].upper()]

    def test_at_most_five_thousand_characters_of_a_field_are_scanned(self, user):
        make_task(user, number=1001, title='early', description='rent ' + 'x ' * 6000)
        make_task(user, number=1002, title='late', description='x ' * 6000 + 'rent')

        by_number = {t.number: t for t in find(user, 'rent').object_list}

        assert by_number[1001].search_field == 'description'
        # Past the cut Python can't see it: listed (the database found it), no snippet.
        assert by_number[1002].search_field == '' and by_number[1002].search_snippet == ''

    def test_a_pattern_the_package_cannot_compile_gives_no_snippets(self, user, monkeypatch):
        make_task(user, number=1001, title='rent')
        monkeypatch.setattr(search_module, 'snippet_regex', lambda pattern: None)

        page = find(user, 'rent')

        assert numbers(page) == [1001] and page.object_list[0].search_snippet == ''

    def test_compiling_is_cheap_and_failures_are_none(self):
        assert search_module.snippet_regex('(') is None
        assert search_module.snippet_regex('(?:a{0,65535}){0,65535}x') is not None
        assert search_module.snippet_regex('a{1000}{1000}') is None

    def test_a_database_refusal_while_reading_the_matches_keeps_the_rows(self, user, monkeypatch):
        make_task(user, number=1001, title='rent')
        fake = MagicMock()
        fake.vendor = 'postgresql'
        cursor = MagicMock()
        fake.cursor.return_value.__enter__.return_value = cursor
        # The page's guard runs (3 statements); the matches' guard times out on its first.
        cursor.execute.side_effect = [None, None, None]
        monkeypatch.setattr(search_module, 'connection', fake)
        real_matched = search_module._matched_texts

        def timed_out(tasks, pattern):
            cursor.execute.side_effect = OperationalError('canceling: statement timeout')
            return real_matched(tasks, pattern)

        monkeypatch.setattr(search_module, '_matched_texts', timed_out)

        page = find(user, 'rent')

        assert numbers(page) == [1001] and page.object_list[0].search_snippet == ''


class TestPostgresTimeout:
    @pytest.fixture
    def pg(self, monkeypatch):
        """`search.connection` as a PostgreSQL one whose cursor is a mock; the real database
        (SQLite) still runs the queries."""
        fake = MagicMock()
        fake.vendor = 'postgresql'
        cursor = MagicMock()
        fake.cursor.return_value.__enter__.return_value = cursor
        monkeypatch.setattr(search_module, 'connection', fake)
        return cursor

    def test_the_timeout_is_set_locally_before_the_query(self, pg, user):
        make_task(user, number=1001, title='rent')

        page = find(user, 'rent')

        assert numbers(page) == [1001]
        assert call('SET LOCAL statement_timeout = %s', ['1000']) in pg.execute.call_args_list

    def test_the_guard_puts_the_previous_timeout_back(self, pg):
        pg.fetchone.return_value = ('30s',)

        with search_module.search_guard():
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
            with search_module.search_guard():
                pass
            assert pg.execute.call_args_list[-1] == call(
                'SELECT set_config(%s, %s, true)', ['statement_timeout', '0']
            )
            assert connection.in_atomic_block

    def test_after_an_error_the_rollback_undoes_it(self, pg):
        # The savepoint (or transaction) is rolled back, which reverts a `SET LOCAL` made inside
        # it, so nothing is restored by hand -- and a restore could not run in an aborted one.
        pg.fetchone.return_value = ('0',)

        with pytest.raises(OperationalError), search_module.search_guard():
            raise OperationalError('connection lost')

        assert [c.args[0] for c in pg.execute.call_args_list] == [
            'SELECT current_setting(%s)',
            'SET LOCAL statement_timeout = %s',
        ]

    def test_a_nested_guard_is_a_savepoint_of_the_real_connection(self, pg):
        # The guard uses the real `atomic()`: nested, it opens a savepoint rather than failing.
        with transaction.atomic():
            depth = len(connection.savepoint_ids)
            with search_module.search_guard():
                assert len(connection.savepoint_ids) == depth + 1

    def test_a_statement_timeout_is_a_validation_error(self, pg, user):
        pg.execute.side_effect = OperationalError('canceling statement due to statement timeout')

        with pytest.raises(ValidationError) as caught:
            find(user, '/(a+)+b/')

        assert caught.value.messages == [TIMEOUT_MESSAGE]

    def test_a_pattern_the_server_rejects_is_a_validation_error(self, pg, user):
        pg.execute.side_effect = DataError('invalid regular expression: invalid escape')

        with pytest.raises(ValidationError) as caught:
            find(user, '/a{1,2}/')

        assert caught.value.messages == [TIMEOUT_MESSAGE]

    def test_another_operational_error_is_not_swallowed(self, pg, user):
        pg.execute.side_effect = OperationalError('connection lost')

        with pytest.raises(OperationalError):
            find(user, 'rent')

    def test_the_word_boundary_is_translated_for_the_server_only(self, pg, user):
        query = prepare_query(r'/\brent\b/')

        assert query.pattern == r'\brent\b' and query.db_pattern == r'\yrent\y'

    def test_sqlite_runs_with_no_timeout(self, user):
        make_task(user, number=1001, title='rent')

        assert numbers(find(user, 'rent')) == [1001]

    def test_the_guard_does_nothing_on_sqlite(self):
        with search_module.search_guard():
            pass

    def test_a_filter_page_without_text_takes_no_timeout(self, pg, user):
        make_task(user, number=1001, title='x', priority=1)

        filter_page(user, FilterParams(priorities=(1,)))

        pg.execute.assert_not_called()


class TestFilterParams:
    def test_every_param_is_read(self):
        label = '8f1c6e0e-6b36-4d52-8f7b-2c5d9c6a1e11'
        query = QueryDict(
            f'q=pay*rent&mode=regex&include_completed=1&priorities=2&priorities=1'
            f'&labels={label}&labels=none&date_range=custom&date_from=2026-11-01'
            f'&date_to=2026-11-30&sort=priority'
        )

        params = FilterParams.from_query(query)

        assert params == FilterParams(
            q='pay*rent',
            mode='regex',
            include_completed=True,
            priorities=(1, 2),
            labels=(label, 'none'),
            date_range='custom',
            date_from=date(2026, 11, 1),
            date_to=date(2026, 11, 30),
            sort='priority',
        )

    def test_defaults(self):
        params = FilterParams.from_query(QueryDict(''))

        assert params == FilterParams()
        assert not params.has_criteria
        assert params.to_query() == ''

    def test_bad_values_are_dropped(self):
        query = QueryDict(
            'mode=sql&include_completed=maybe&priorities=9&priorities=x&priorities=-1&priorities=3'
            '&labels=not-a-uuid&labels=1&date_range=forever&date_from=yesterday&date_to=2026-13-40'
            '&sort=random&q=%20%20hello%20%20'
        )

        params = FilterParams.from_query(query)

        assert params == FilterParams(q='hello', priorities=(3,))

    def test_comma_separated_lists(self):
        params = FilterParams.from_query(QueryDict('priorities=1,2,,3&labels=none,none'))

        assert params.priorities == (1, 2, 3) and params.labels == ('none',)

    def test_priority_zero_is_no_priority(self):
        assert FilterParams.from_query(QueryDict('priorities=0')).priorities == (0,)

    def test_dates_without_a_range_mean_custom(self):
        params = FilterParams.from_query(QueryDict('date_from=2026-11-01'))

        assert params.date_range == 'custom' and params.date_from == date(2026, 11, 1)

    def test_an_explicit_range_wins_over_the_dates(self):
        params = FilterParams.from_query(
            QueryDict('date_range=any&date_from=2026-11-01&date_to=2026-11-02')
        )
        assert params.date_range == 'any' and params.date_from is None
        params = FilterParams.from_query(QueryDict('date_range=today&date_from=2026-11-01'))
        assert params.date_range == 'today' and params.date_from is None

    def test_a_custom_range_without_dates_is_any_date(self):
        assert FilterParams.from_query(QueryDict('date_range=custom')).date_range == 'any'

    def test_labels_that_are_not_the_users_are_dropped(self, user, other_user):
        mine = make_label(user, 'Home')
        theirs = make_label(other_user, 'Away')
        query = QueryDict(f'labels={mine.pk}&labels={theirs.pk}&labels=none')

        params = parse_filter_params(query, user)

        assert params.labels == tuple(sorted([str(mine.pk), 'none']))

    def test_the_canonical_query_leaves_defaults_out_and_sorts_lists(self):
        params = FilterParams(q='rent', priorities=(1, 2), date_range='today')

        assert params.to_query() == 'q=rent&priorities=1&priorities=2&date_range=today'
        assert params.to_query(keep_sort=True).endswith('&sort=date')
        assert params.to_query(page=2).endswith('&page=2')

    def test_round_trips(self):
        params = FilterParams(
            q='a b',
            mode='regex',
            include_completed=True,
            priorities=(0, 3),
            labels=('none',),
            date_range='custom',
            date_from=date(2026, 1, 2),
            sort='title',
        )

        assert FilterParams.from_dict(params.to_dict()) == params
        assert FilterParams.from_query(QueryDict(params.to_query())) == params

    def test_from_dict_is_lenient_too(self):
        assert FilterParams.from_dict(None) == FilterParams()
        assert (
            FilterParams.from_dict({'priorities': 'x', 'sort': 7, 'labels': [3]}) == FilterParams()
        )

    def test_regex_detection(self):
        assert FilterParams(q='/a/').is_regex and FilterParams(q='a', mode='regex').is_regex
        assert not FilterParams(q='a/b').is_regex

    def test_only_selecting_params_count_as_criteria(self):
        assert not FilterParams(sort='title', include_completed=True).has_criteria
        assert FilterParams(q='x').has_criteria and FilterParams(priorities=(1,)).has_criteria
        assert FilterParams(labels=('none',)).has_criteria
        assert FilterParams(date_range='no_date').has_criteria


class TestFilterTasks:
    @pytest.fixture
    def today(self, user, wednesday):
        return user_today(user)

    def test_priorities_are_any_of(self, user):
        make_task(user, number=1001, priority=1)
        make_task(user, number=1002, priority=2)
        make_task(user, number=1003, priority=0)

        assert sorted(t.number for t in filter_tasks(user, FilterParams(priorities=(1, 0)))) == [
            1001,
            1003,
        ]

    def test_labels_are_any_of_and_none_means_unlabelled(self, user):
        home = make_label(user, 'Home')
        work = make_label(user, 'Work')
        make_task(user, number=1001, labels=[home])
        make_task(user, number=1002, labels=[work])
        make_task(user, number=1003, labels=[home, work])
        make_task(user, number=1004)

        def run(*labels):
            params = FilterParams(labels=labels)
            return sorted(t.number for t in filter_tasks(user, params))

        assert run(str(home.pk)) == [1001, 1003]
        assert run(str(home.pk), str(work.pk)) == [1001, 1002, 1003]
        assert run('none') == [1004]
        assert run('none', str(work.pk)) == [1002, 1003, 1004]

    def test_the_date_ranges(self, user, today):
        make_task(user, number=1001, due_date=today - timedelta(days=1))
        make_task(user, number=1002, due_date=today)
        make_task(user, number=1003, due_date=today + timedelta(days=7))
        make_task(user, number=1004, due_date=today + timedelta(days=8))
        make_task(user, number=1005, due_date=today + timedelta(days=30))
        make_task(user, number=1006)

        def run(**kwargs):
            return sorted(t.number for t in filter_tasks(user, FilterParams(**kwargs)))

        assert run(date_range='overdue') == [1001]
        assert run(date_range='today') == [1002]
        assert run(date_range='next_7_days') == [1002, 1003]
        assert run(date_range='next_30_days') == [1002, 1003, 1004, 1005]
        assert run(date_range='no_date') == [1006]
        assert run(date_range='custom', date_from=today, date_to=today + timedelta(days=8)) == [
            1002,
            1003,
            1004,
        ]
        assert run(date_range='custom', date_from=today + timedelta(days=8)) == [1004, 1005]
        assert run(date_range='custom', date_to=today - timedelta(days=1)) == [1001]

    def test_a_completed_task_is_never_overdue(self, funded_user, wednesday):
        task = create_task(funded_user, title='x', due_date=wednesday - timedelta(days=3))
        complete_task(task)

        params = FilterParams(date_range='overdue', include_completed=True)

        assert list(filter_tasks(funded_user, params)) == []

    def test_presets_follow_the_users_time_zone(self, user, freeze_now):
        user.timezone = 'Pacific/Auckland'
        user.save()
        freeze_now(2026, 10, 28, 12, 0)  # still the 28th in UTC, already the 29th in Auckland
        make_task(user, number=1001, due_date=date(2026, 10, 29))
        make_task(user, number=1002, due_date=date(2026, 10, 28))

        assert [t.number for t in filter_tasks(user, FilterParams(date_range='today'))] == [1001]

    def test_without_text_open_tasks_unless_completed_are_included(self, funded_user):
        done = create_task(funded_user, title='done')
        create_task(funded_user, title='open')
        trashed = create_task(funded_user, title='gone')
        complete_task(done)
        trash_task(trashed)

        assert [t.title for t in filter_tasks(funded_user, FilterParams())] == ['open']
        assert sorted(
            t.title for t in filter_tasks(funded_user, FilterParams(include_completed=True))
        ) == ['done', 'open']

    def test_filters_combine_with_the_text(self, user):
        home = make_label(user, 'Home')
        make_task(user, number=1001, title='pay rent', priority=1, labels=[home])
        make_task(user, number=1002, title='pay rent', priority=2, labels=[home])
        make_task(user, number=1003, title='pay bills', priority=1, labels=[home])
        make_task(user, number=1004, title='pay rent', priority=1)

        params = FilterParams(q='rent', priorities=(1,), labels=(str(home.pk),))

        assert [t.number for t in filter_tasks(user, params)] == [1001]

    def test_search_tasks_narrows_with_the_filters_but_takes_its_own_text(self, user):
        make_task(user, number=1001, title='pay rent', priority=1)
        make_task(user, number=1002, title='pay rent', priority=2)

        page = search_tasks(
            user,
            q='rent',
            filters=FilterParams(q='ignored', priorities=(2,), include_completed=True),
        )

        assert numbers(page) == [1002]

    def test_a_search_that_cannot_run_raises_at_once(self, user):
        with pytest.raises(ValidationError):
            filter_tasks(user, FilterParams(q='/(/'))

    def test_the_order_follows_the_sort(self, user):
        make_task(user, number=1001, title='b')
        make_task(user, number=1002, title='a')

        assert [t.number for t in filter_tasks(user, FilterParams(sort='title'))] == [1002, 1001]


class TestDescribeParams:
    def test_the_summary(self, user):
        home = make_label(user, 'home')
        params = FilterParams(
            q='rent*', priorities=(1,), labels=(str(home.pk),), date_range='next_7_days'
        )

        text = describe_params(params, {str(home.pk): 'home'})

        assert text == 'priority 1 · @home · next 7 days · "rent*"'

    def test_everything(self):
        params = FilterParams(
            q='/a.b/',
            include_completed=True,
            priorities=(0, 2),
            labels=('none',),
            date_range='custom',
            date_from=date(2026, 11, 1),
            date_to=date(2026, 11, 30),
            sort='title',
        )

        assert describe_params(params) == (
            'priority none, 2 · no label · 1 Nov 2026 to 30 Nov 2026 · /a.b/ · '
            'including completed · by title'
        )

    def test_regex_mode_and_open_ended_ranges(self):
        assert describe_params(FilterParams(q='a+', mode='regex')) == '/a+/'
        assert (
            describe_params(FilterParams(date_range='custom', date_from=date(2026, 1, 5)))
            == 'from 5 Jan 2026'
        )
        assert (
            describe_params(FilterParams(date_range='custom', date_to=date(2026, 1, 5)))
            == 'until 5 Jan 2026'
        )

    def test_a_label_that_is_gone_is_left_out(self):
        text = describe_params(FilterParams(labels=('8f1c6e0e-6b36-4d52-8f7b-2c5d9c6a1e11',)), {})

        assert text == 'all open tasks'

    def test_nothing(self):
        assert describe_params(FilterParams()) == 'all open tasks'
        assert describe_params(FilterParams(date_range='no_date')) == 'no date'


class TestSearchViews:
    @pytest.fixture
    def seeded(self, funded_user):
        home = make_label(funded_user, 'Home')
        create_task(funded_user, title='Pay the rent', priority=1, labels=[home])
        create_task(funded_user, title='Walk the Cão', priority=2)
        done = create_task(funded_user, title='Old rent')
        complete_task(done)
        return funded_user

    def test_anonymous_visitors_go_to_the_login_page(self, client):
        for name in ('todo:search', 'todo:search-list', 'todo:filters'):
            response = client.get(reverse(name))
            assert response.status_code == 302 and '/account/login' in response['Location']

    def test_the_page_without_a_query_asks_for_one(self, web):
        response = web.get(reverse('todo:search'))

        assert response.status_code == 200
        assert 'Type something to search for' in response.content.decode()

    def test_results(self, web, seeded):
        response = web.get(reverse('todo:search'), {'q': 'rent'})
        html = unescape(response.content.decode())

        assert response.status_code == 200
        assert 'Pay the rent' in html and 'Old rent' not in html and 'Walk the' not in html
        assert '1 result' in html
        assert '<mark>rent</mark>' in html
        assert 'matches shown without accents or formatting' in html

    def test_results_are_decorated_like_the_lists(self, web, funded_user):
        """A repeating task and a task with a reminder show their chip and bell in the results,
        and a comment match still shows its count (the rows go through `views.lists.decorate`)."""
        due = user_today(funded_user) + timedelta(days=3)
        rent = create_task(funded_user, title='Pay the rent', due_date=due, rrule='FREQ=DAILY')
        add_reminder(rent, minutes_before=15)
        talk = create_task(funded_user, title='Call the bank')
        add_comment(talk, funded_user, 'mention the rent too')

        html = unescape(web.get(reverse('todo:search'), {'q': 'rent'}).content.decode())

        assert 'Pay the rent' in html and 'Call the bank' in html
        assert 'fa-repeat' in html
        assert 'Has reminders' in html
        assert 'Comments' in html

    def test_accents(self, web, seeded):
        assert 'Walk the' in web.get(reverse('todo:search'), {'q': 'cao'}).content.decode()

    def test_include_completed(self, web, seeded):
        html = web.get(
            reverse('todo:search'), {'q': 'rent', 'include_completed': '1'}
        ).content.decode()

        assert 'Old rent' in html and '2 results' in html

    def test_regex_checkbox_and_slashes(self, web, seeded):
        html = web.get(reverse('todo:search'), {'q': '^pay', 'mode': 'regex'}).content.decode()
        assert 'Pay the rent' in html
        html = web.get(reverse('todo:search'), {'q': '/^walk/'}).content.decode()
        assert 'Walk the' in html

    def test_the_filter_bar(self, web, seeded):
        html = web.get(reverse('todo:search'), {'priorities': '2'}).content.decode()

        assert 'Walk the' in html and 'Pay the rent' not in html

    def test_a_refusal_is_a_422_line(self, web):
        response = web.get(reverse('todo:search'), {'q': '/(/'})

        assert response.status_code == 422
        assert 'Invalid regular expression.' in response.content.decode()

    def test_an_empty_text_with_a_filter_is_not_an_error(self, web, seeded):
        response = web.get(reverse('todo:search'), {'q': '', 'priorities': '1'})

        assert response.status_code == 200 and 'Pay the rent' in response.content.decode()

    def test_the_list_partial_for_htmx(self, web, seeded):
        response = web.get(reverse('todo:search-list'), {'q': 'rent', 'sort': 'date'}, **HTMX)

        assert response.status_code == 200
        assert response['HX-Push-Url'] == '/todo/search/?q=rent'
        html = response.content.decode()
        assert 'Pay the rent' in html and '<html' not in html
        assert 'id="search-msg" hx-swap-oob' in html

    def test_the_push_url_is_canonical(self, web, seeded):
        response = web.get(
            reverse('todo:search-list'),
            {'q': 'rent', 'mode': 'regex', 'priorities': ['1', '1'], 'sort': 'bogus', 'junk': 'x'},
            **HTMX,
        )

        assert response['HX-Push-Url'] == '/todo/search/?q=rent&mode=regex&priorities=1'

    def test_the_list_partial_refusal(self, web):
        response = web.get(reverse('todo:search-list'), {'q': '(a)\\1', 'mode': 'regex'}, **HTMX)

        assert response.status_code == 422 and response['HX-Retarget'] == '#search-msg'
        assert 'Invalid regular expression.' in response.content.decode()

    def test_the_list_partial_opened_directly_goes_to_the_page(self, web):
        response = web.get(reverse('todo:search-list'), {'q': 'rent', 'sort': 'title'})

        assert response.status_code == 302
        assert response['Location'] == '/todo/search/?q=rent&sort=title'

    def test_paging(self, web, funded_user):
        for n in range(55):
            make_task(funded_user, number=3000 + n, title=f'rent {n:02}')

        first = web.get(reverse('todo:search-list'), {'q': 'rent'}, **HTMX).content.decode()
        second = web.get(reverse('todo:search-list'), {'q': 'rent', 'page': 2}, **HTMX)

        assert 'Page 1 of 2' in first and 'page=2' in first
        assert 'Page 2 of 2' in second.content.decode()
        assert second['HX-Push-Url'] == '/todo/search/?q=rent&page=2'

    def test_a_row_action_from_the_results_keeps_the_row(self, web, seeded):
        from ..models import Task

        task = Task.objects.get(title='Pay the rent')

        response = web.post(
            reverse('todo:complete', args=[task.number]), {'view': 'search', 'ctx': 'row'}, **HTMX
        )

        assert response.status_code == 200 and 'Pay the rent' in response.content.decode()

    def test_the_sidebar_search_box(self, web):
        html = web.get(reverse('todo:today')).content.decode()

        assert 'role="search"' in html and 'name="q"' in html and 'autocomplete="off"' in html
        assert 'value="regex"' in html and 'name="include_completed"' in html
        assert 'Search help' in html

    def test_the_search_page_does_not_repeat_the_sidebar_box(self, web):
        html = web.get(reverse('todo:search')).content.decode()

        assert html.count('role="search"') == 1


class TestSearchRateLimit:
    @pytest.fixture(autouse=True)
    def _limits(self, settings, ratelimit_clock):
        settings.RATELIMIT_ENABLE = True
        settings.RATELIMIT_RULES = {**settings.RATELIMIT_RULES, 'TODO_SEARCH_USER': (2, 60)}

    def test_the_limit_is_charged_per_search(self, web):
        for _ in range(2):
            assert web.get(reverse('todo:search'), {'q': 'x'}).status_code == 200

        response = web.get(reverse('todo:search-list'), {'q': 'x'}, **HTMX)

        assert response.status_code == 429 and response['HX-Retarget'] == '#search-msg'

    def test_the_page_gets_the_429_page(self, web):
        for _ in range(2):
            web.get(reverse('todo:search'), {'q': 'x'})

        assert web.get(reverse('todo:search'), {'q': 'x'}).status_code == 429

    def test_a_page_with_nothing_to_search_for_is_free(self, web):
        for _ in range(5):
            assert web.get(reverse('todo:search')).status_code == 200

    def test_a_refused_query_is_still_charged(self, web):
        for _ in range(2):
            assert web.get(reverse('todo:search'), {'q': '/(/'}).status_code == 422

        assert web.get(reverse('todo:search'), {'q': 'x'}).status_code == 429

    def test_other_users_have_their_own_budget(self, web, funded_user):
        from django.test import Client

        for _ in range(2):
            web.get(reverse('todo:search'), {'q': 'x'})
        other = make_user()
        client = Client()
        client.force_login(other)

        assert client.get(reverse('todo:search'), {'q': 'x'}).status_code == 200


def test_the_comment_search_text_is_what_is_searched(user):
    task = make_task(user, number=1001, title='Quiet')
    comment = add_comment(task, user, '**Bold** and `code`')

    assert Comment.objects.get(pk=comment.pk).body_text == 'bold and code'
    assert numbers(find(user, 'bold and code')) == [1001]


def test_editing_a_description_changes_what_is_found(funded_user):
    task = create_task(funded_user, title='Quiet', description='old words')
    update_task(task, changes={'description': 'new words'})

    assert numbers(find(funded_user, 'new')) == [task.number]
    assert numbers(find(funded_user, 'old')) == []
