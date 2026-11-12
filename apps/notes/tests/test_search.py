"""Search (issue #260, PR 5): the query language over titles and content, what is and is not
searched, the scopes, the snippets and their bounds, the PostgreSQL timeout branch, the structured
filters and the search views. To-do's `test_search.py` for notes."""

import re
import time
from datetime import date, timedelta
from html import unescape
from types import SimpleNamespace
from unittest.mock import MagicMock, call

import pytest
from django.core.exceptions import ValidationError
from django.db import connection
from django.db.utils import DataError, OperationalError
from django.http import QueryDict
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from apps.core import search as core_search
from apps.core.datetime_format import DisplayFormats

from ..models import Note
from ..services import (
    Editor,
    FilterParams,
    archive,
    create_note,
    create_tag,
    describe_params,
    filter_notes,
    filter_page,
    links,
    parse_filter_params,
    save_filter,
    save_note,
    search_notes,
    trash,
)
from ..services import search as search_module
from ..services.search import (
    SNIPPET_SCAN_MAX,
    SNIPPET_TIME_BUDGET,
    TIMEOUT_MESSAGE,
    make_snippet,
    prepare_query,
)
from .factories import make_link, make_note, make_tag, make_user, utc

pytestmark = pytest.mark.django_db

HTMX = {'HTTP_HX_REQUEST': 'true'}


def text(response) -> str:
    return unescape(response.content.decode())


def titles(page) -> list[str]:
    return [note.title for note in page.object_list]


def find(user, q, **kwargs):
    return search_notes(user, q=q, **kwargs)


def refusal(q: str, mode: str = 'glob') -> str:
    with pytest.raises(ValidationError) as caught:
        prepare_query(q, mode)
    return caught.value.messages[0]


def snippet_of(text: str, pattern: str):
    match = re.search(pattern, text)
    assert match is not None
    return make_snippet(text, match)


class TestWhatIsSearched:
    def test_accents_and_case_in_the_title(self, user):
        make_note(user, title='Walk the Cão')
        make_note(user, title='Nothing')

        assert titles(find(user, 'cao')) == ['Walk the Cão']
        assert titles(find(user, 'CÃO')) == ['Walk the Cão']
        assert titles(find(user, 'walk the cao')) == ['Walk the Cão']

    def test_a_match_in_the_content(self, user):
        make_note(user, title='Quiet', content='Remember to pay the Aluguel')
        make_note(user, title='Other', content='nothing here')

        assert titles(find(user, 'aluguel')) == ['Quiet']

    def test_the_title_and_the_content_are_both_searched(self, user):
        make_note(user, title='rent', content='x')
        make_note(user, title='y', content='the rent is due')

        assert sorted(titles(find(user, 'rent'))) == ['rent', 'y']

    def test_formatting_and_link_destinations_are_not_text(self, user):
        make_note(
            user,
            title='Links',
            content='[docs](https://example.com/secret-path) and **bold** and `code`',
        )

        assert titles(find(user, 'secret-path')) == []
        assert titles(find(user, 'secret path')) == []
        assert titles(find(user, 'docs')) == ['Links']
        assert titles(find(user, 'bold and code')) == ['Links']

    def test_punctuation_in_the_query_and_the_text(self, user):
        make_note(user, title='Pay-the rent!!', content='x')

        assert titles(find(user, 'pay the rent')) == ['Pay-the rent!!']
        assert titles(find(user, 'pay-the rent!')) == ['Pay-the rent!!']

    def test_a_code_block_is_searched(self, user):
        make_note(user, title='Snippet', content='```python\nprint("hello world")\n```')

        assert titles(find(user, 'hello world')) == ['Snippet']

    def test_a_table_is_searched(self, user):
        make_note(user, title='T', content='| name | qty |\n|---|---|\n| apples | 3 |')

        assert titles(find(user, 'apples')) == ['T']

    def test_there_is_no_number_shortcut(self, user):
        make_note(user, title='Seven', content='x')
        make_note(user, title='Other', content='room 1000 is on the second floor')

        # A task-style number only means text here: `#1000` is the text "1000".
        assert titles(find(user, '#1000')) == ['Other']
        assert titles(find(user, '1000')) == ['Other']

    def test_only_the_users_own_notes(self, user, other_user):
        make_note(user, title='mine rent')
        make_note(other_user, title='theirs rent')

        assert titles(find(user, 'rent')) == ['mine rent']

    def test_notes_in_the_trash_are_never_found_in_any_scope(self, funded_user):
        gone = create_note(funded_user, title='rent gone')
        create_note(funded_user, title='rent here')
        trash(gone)

        for scope in ('active', 'archived', 'all'):
            assert titles(find(funded_user, 'rent', scope=scope)) == (
                ['rent here'] if scope != 'archived' else []
            )

    def test_earlier_versions_are_not_searched(self, funded_user):
        note = create_note(funded_user, title='Plan', content='the old secret')
        save_note(
            note,
            editor=Editor('owner', funded_user, None),
            content='the new plan',
            base_version=note.version,
        )

        assert note.versions.count() >= 1
        assert titles(find(funded_user, 'secret')) == []
        assert titles(find(funded_user, 'new plan')) == ['Plan']

    def test_editing_the_content_changes_what_is_found(self, funded_user):
        note = create_note(funded_user, title='N', content='alpha')
        note.content = 'beta'
        note.save()

        assert titles(find(funded_user, 'alpha')) == []
        assert titles(find(funded_user, 'beta')) == ['N']


class TestScopes:
    @pytest.fixture
    def three(self, funded_user):
        create_note(funded_user, title='rent active')
        archived = create_note(funded_user, title='rent archived')
        archive(archived)
        gone = create_note(funded_user, title='rent trashed')
        trash(gone)
        return funded_user

    def test_active_is_the_default(self, three):
        assert titles(find(three, 'rent')) == ['rent active']

    def test_archived(self, three):
        assert titles(find(three, 'rent', scope='archived')) == ['rent archived']

    def test_all_is_both_but_never_the_trash(self, three):
        assert sorted(titles(find(three, 'rent', scope='all'))) == ['rent active', 'rent archived']

    def test_an_unknown_scope_is_the_default(self, three):
        assert titles(find(three, 'rent', scope='bogus')) == ['rent active']


class TestGlobWildcards:
    @pytest.fixture(autouse=True)
    def _notes(self, user):
        make_note(user, title='pay the rent')
        make_note(user, title='pay rent')
        make_note(user, title='parent')
        make_note(user, title='ren.t')

    def test_star(self, user):
        assert sorted(titles(find(user, 'pay*rent'))) == ['pay rent', 'pay the rent']

    def test_the_pattern_is_unanchored(self, user):
        assert sorted(titles(find(user, 'rent'))) == ['parent', 'pay rent', 'pay the rent']

    def test_question_mark_is_one_character(self, user):
        assert titles(find(user, 'pa?ent')) == ['parent']
        assert titles(find(user, 'pa??ent')) == []

    def test_a_dot_is_not_a_wildcard(self, user):
        assert titles(find(user, 'ren.t')) == ['ren.t']


class TestRegex:
    @pytest.fixture(autouse=True)
    def _notes(self, user):
        make_note(user, title='pay rent')
        make_note(user, title='rent later')
        make_note(user, title='Cão', content='a123')

    def test_slashes(self, user):
        assert titles(find(user, '/^rent/')) == ['rent later']

    def test_the_checkbox(self, user):
        assert titles(find(user, 'pay.*ent', mode='regex')) == ['pay rent']

    def test_the_text_is_lower_case_so_the_pattern_is_lowered_too(self, user):
        assert titles(find(user, '/^PAY/')) == ['pay rent']
        assert titles(find(user, r'/a\d+/')) == ['Cão']

    def test_slashes_win_over_the_glob_mode(self, user):
        assert titles(find(user, '/^pay/', mode='glob')) == ['pay rent']

    def test_refusals_run_nothing(self, user):
        for q in ('/(/', r'/(a)\1/', '/(?=a)/', '/(?>a)/', '/a*+/', '/(?P<n>a)/'):
            assert refusal(q) == 'Invalid regular expression.'
        assert refusal('') == 'Type something to search for.'
        assert refusal('x' * 201) == 'The pattern is too long.'

    def test_a_pattern_is_a_parameter_not_sql(self, user):
        assert titles(find(user, "/'; drop table notes_note; --/")) == []
        assert Note.objects.filter(user=user).count() == 3


class TestOrderingAndPaging:
    def test_the_default_order_is_newest_updated_first(self, user):
        make_note(user, title='rent old', updated_at=utc(2026, 1, 1))
        make_note(user, title='rent new', updated_at=utc(2026, 6, 1))

        assert titles(find(user, 'rent')) == ['rent new', 'rent old']

    def test_the_sort_comes_from_the_filters(self, user):
        make_note(user, title='rent b', updated_at=utc(2026, 6, 1))
        make_note(user, title='rent a', updated_at=utc(2026, 1, 1))

        page = search_notes(user, q='rent', filters=FilterParams(sort='title'))

        assert titles(page) == ['rent a', 'rent b']

    def test_the_direction_reverses_the_sort(self, user):
        make_note(user, title='rent b', updated_at=utc(2026, 6, 1))
        make_note(user, title='rent a', updated_at=utc(2026, 1, 1))

        def run(**changes):
            return titles(search_notes(user, q='rent', filters=FilterParams(**changes)))

        assert run() == ['rent b', 'rent a'] and run(dir='asc') == ['rent a', 'rent b']
        assert run(sort='title') == ['rent a', 'rent b']
        assert run(sort='title', dir='desc') == ['rent b', 'rent a']

    def test_fifty_per_page(self, user):
        for n in range(55):
            make_note(user, title=f'rent {n:02}')

        first = find(user, 'rent')
        second = find(user, 'rent', page=2)

        assert len(first.object_list) == 50 and len(second.object_list) == 5
        assert first.paginator.count == 55

    def test_a_bad_page_number_is_the_nearest_page(self, user):
        for n in range(55):
            make_note(user, title=f'rent {n:02}')

        assert find(user, 'rent', page=99).number == 2
        assert find(user, 'rent', page='x').number == 1

    def test_the_decorator_loads_the_rows_in_order(self, user):
        tag = make_tag(user, 'Work')
        make_note(user, title='rent a', updated_at=utc(2026, 1, 1), tags=[tag])
        make_note(user, title='rent b', updated_at=utc(2026, 6, 1))

        def decorate(queryset):
            return links.with_active_link_flag(queryset).prefetch_related('tags')

        page = find(user, 'rent', decorate=decorate)

        assert titles(page) == ['rent b', 'rent a']
        with CaptureQueriesContext(connection) as queries:
            assert [len(n.tags.all()) for n in page.object_list] == [0, 1]
            assert [n.has_active_link for n in page.object_list] == [False, False]
        assert len(queries) == 0  # the tags were prefetched


class TestSnippets:
    def test_a_title_match(self, user):
        make_note(user, title='Pay the rent now', content='unrelated')

        note = find(user, 'rent').object_list[0]

        assert note.search_field == 'title'
        assert note.search_snippet == snippet_of('pay the rent now', 'rent')
        assert '<mark>rent</mark>' in note.search_snippet

    def test_a_content_match(self, user):
        make_note(user, title='Quiet', content='the rent is late')

        note = find(user, 'rent').object_list[0]

        assert note.search_field == 'content'
        assert note.search_snippet.startswith('the <mark>rent</mark> is late')
        assert note.search_parts == ('the ', 'rent', ' is late')

    def test_the_title_wins_over_the_content(self, user):
        make_note(user, title='rent', content='rent again')

        assert find(user, 'rent').object_list[0].search_field == 'title'

    def test_the_text_is_escaped(self, user):
        make_note(user, title='x', content='<script>alert(1)</script> rent')

        note = find(user, 'rent').object_list[0]

        # The stored search text has no markup left, and the snippet escapes whatever it holds.
        assert '<script>' not in note.search_snippet
        text = '<b>"x" & rent</b>'
        escaped = snippet_of(text, 'rent')
        assert escaped == '&lt;b&gt;&quot;x&quot; &amp; <mark>rent</mark>&lt;/b&gt;'

    def test_no_snippet_without_a_search(self, user):
        make_note(user, title='x', content='y')

        page = filter_page(user, FilterParams(shared='no'))

        assert page.object_list[0].search_snippet == ''
        assert page.object_list[0].search_field == ''


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
        # `a*a*a*a*c` is `a.*a.*a.*a.*c`: about n^5 steps for `re` on 500 characters.
        make_note(user, title='a', content='a' * 500)
        make_note(user, title='aaaac', content='-')

        started = time.monotonic()
        page = find(user, 'a*a*a*a*c')
        elapsed = time.monotonic() - started

        assert elapsed < 2
        # The content of the first note did not match in the database (no `c`), so it is never
        # scanned; the second's title is the snippet.
        [note] = page.object_list
        assert note.title == 'aaaac' and note.search_field == 'title'
        assert '<mark>aaaac</mark>' in note.search_snippet

    def test_the_nested_quantifier(self, user, database_regexp):
        make_note(user, title='x', content='a' * 500 + 'b')

        started = time.monotonic()
        page = find(user, '/(a+)+b/')

        assert time.monotonic() - started < 2
        assert titles(page) == ['x']

    def test_a_matching_field_that_backtracks_is_listed_without_a_snippet(
        self, user, database_regexp
    ):
        # The database matches (it ends in "a"); Python's search from the start would take
        # 2^60 steps. The row is returned, bare.
        make_note(user, title='hostile', content='a' * 60 + 'b' + 'a')
        make_note(user, title='ba', content='-')

        started = time.monotonic()
        page = find(user, r'/(a|aa)+$/')
        elapsed = time.monotonic() - started

        assert elapsed < SNIPPET_TIME_BUDGET + 2
        assert sorted(titles(page)) == ['ba', 'hostile']
        hostile = next(note for note in page.object_list if note.title == 'hostile')
        assert hostile.search_snippet == '' and hostile.search_field == ''

    def test_an_exhausted_budget_lists_the_rows_without_snippets(self, user, monkeypatch):
        make_note(user, title='rent')
        make_note(user, title='rent too')
        monkeypatch.setattr(search_module, 'SNIPPET_TIME_BUDGET', 0)

        page = find(user, 'rent')

        assert sorted(titles(page)) == ['rent', 'rent too']
        assert all(note.search_snippet == '' for note in page.object_list)

    def test_a_timeout_ends_the_snippets_and_keeps_the_rows(self, user, monkeypatch):
        make_note(user, title='rent')
        make_note(user, title='rent too')

        class Slow:
            calls: list[float] = []

            def search(self, text, timeout):
                self.calls.append(timeout)
                raise TimeoutError

        slow = Slow()
        monkeypatch.setattr(search_module, 'snippet_regex', lambda pattern: slow)

        page = find(user, 'rent')

        assert sorted(titles(page)) == ['rent', 'rent too']
        assert all(note.search_snippet == '' for note in page.object_list)
        assert len(slow.calls) == 1  # the second row was not even tried
        assert 0 < slow.calls[0] <= SNIPPET_TIME_BUDGET

    def test_each_search_gets_what_is_left_of_the_page_budget(self, user, monkeypatch):
        make_note(user, title='rent')
        make_note(user, title='rent too')
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

    def test_the_cut_text_comes_from_the_query_not_the_loaded_rows(self, user):
        note = make_note(user, title='zzz', content='rent ' + 'x' * 8000)
        stale = Note.objects.only('title').get(pk=note.pk)  # content never loaded
        with CaptureQueriesContext(connection) as queries:
            matched = search_module._matched_texts([stale], 'rent')

        assert matched is not None and len(matched[note.pk][0][1]) == SNIPPET_SCAN_MAX
        assert matched[note.pk][0][1].startswith('rent ')
        assert 'substr' in queries[-1]['sql'].lower()

    def test_only_the_fields_the_database_matched_are_scanned(self, user, monkeypatch):
        make_note(user, title='zzz title ' + 'q' * 100, content='a rent here')
        scanned = []
        real = search_module.snippet_regex

        class Spy:
            def __init__(self, compiled):
                self.compiled = compiled

            def search(self, text, timeout):
                scanned.append(text)
                return self.compiled.search(text, timeout=timeout)

        monkeypatch.setattr(search_module, 'snippet_regex', lambda pattern: Spy(real(pattern)))

        note = find(user, 'rent').object_list[0]

        assert scanned == ['a rent here'] and note.search_field == 'content'

    def test_one_query_flags_both_fields_for_the_whole_page(self, user):
        for n in range(5):
            make_note(user, title=f'rent {n}', content='x')

        with CaptureQueriesContext(connection) as queries:
            find(user, 'rent')

        # The page's count, the page's rows, and the one query that flags the title and the content
        # of all five: nothing per row.
        assert len(queries) == 3
        flagging = [q for q in queries if 'in_title' in q['sql'] and 'in_content' in q['sql']]
        assert len(flagging) == 1

    def test_at_most_five_thousand_characters_of_a_field_are_scanned(self, user):
        make_note(user, title='early', content='rent ' + 'x ' * 6000)
        make_note(user, title='late', content='x ' * 6000 + 'rent')

        by_title = {note.title: note for note in find(user, 'rent').object_list}

        assert by_title['early'].search_field == 'content'
        # Past the cut Python can't see it: listed (the database found it), no snippet.
        assert by_title['late'].search_field == '' and by_title['late'].search_snippet == ''

    def test_a_pattern_the_package_cannot_compile_gives_no_snippets(self, user, monkeypatch):
        make_note(user, title='rent')
        monkeypatch.setattr(search_module, 'snippet_regex', lambda pattern: None)

        page = find(user, 'rent')

        assert titles(page) == ['rent'] and page.object_list[0].search_snippet == ''

    def test_a_database_refusal_while_reading_the_matches_keeps_the_rows(self, user, monkeypatch):
        make_note(user, title='rent')
        fake = MagicMock()
        fake.vendor = 'postgresql'
        cursor = MagicMock()
        fake.cursor.return_value.__enter__.return_value = cursor
        # The page's guard runs (3 statements); the matches' guard times out on its first.
        cursor.execute.side_effect = [None, None, None]
        monkeypatch.setattr(core_search, 'connection', fake)
        real_matched = search_module._matched_texts

        def timed_out(notes, pattern):
            cursor.execute.side_effect = OperationalError('canceling: statement timeout')
            return real_matched(notes, pattern)

        monkeypatch.setattr(search_module, '_matched_texts', timed_out)

        page = find(user, 'rent')

        assert titles(page) == ['rent'] and page.object_list[0].search_snippet == ''


class TestPostgresTimeout:
    @pytest.fixture
    def pg(self, monkeypatch):
        """`apps.core.search.connection` as a PostgreSQL one whose cursor is a mock; the real
        database (SQLite) still runs the queries."""
        fake = MagicMock()
        fake.vendor = 'postgresql'
        cursor = MagicMock()
        fake.cursor.return_value.__enter__.return_value = cursor
        monkeypatch.setattr(core_search, 'connection', fake)
        return cursor

    def test_the_timeout_is_set_locally_before_the_query(self, pg, user):
        make_note(user, title='rent')

        page = find(user, 'rent')

        assert titles(page) == ['rent']
        assert call('SET LOCAL statement_timeout = %s', ['1000']) in pg.execute.call_args_list

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

    def test_sqlite_runs_with_no_timeout(self, user):
        make_note(user, title='rent')

        assert titles(find(user, 'rent')) == ['rent']

    def test_a_filter_page_without_text_takes_no_timeout(self, pg, user):
        make_note(user, title='x')

        filter_page(user, FilterParams(shared='no'))

        pg.execute.assert_not_called()

    def test_the_pattern_reaches_postgres_in_its_dialect(self, user):
        assert prepare_query(r'/\bfoo\b/').db_pattern == r'\bfoo\b'  # SQLite
        fake = MagicMock()
        fake.vendor = 'postgresql'
        from unittest.mock import patch

        with patch.object(core_search, 'connection', fake):
            assert prepare_query(r'/\bfoo\b/').db_pattern == r'\yfoo\y'


class TestFilterParams:
    def test_every_param_is_read(self):
        tag = '8f1c6e0e-6b36-4d52-8f7b-2c5d9c6a1e11'
        query = QueryDict(
            f'q=deploy*&mode=regex&scope=all&tags={tag}&tags=none&shared=yes'
            f'&updated=custom&updated_from=2026-11-01&updated_to=2026-11-30&sort=title'
        )

        params = FilterParams.from_query(query)

        assert params == FilterParams(
            q='deploy*',
            mode='regex',
            scope='all',
            tags=(tag, 'none'),
            shared='yes',
            updated='custom',
            updated_from=date(2026, 11, 1),
            updated_to=date(2026, 11, 30),
            sort='title',
        )

    def test_defaults(self):
        params = FilterParams.from_query(QueryDict(''))

        assert params == FilterParams()
        assert not params.has_criteria
        assert params.to_query() == ''
        assert params.scope == 'active' and params.sort == 'updated'

    def test_bad_values_are_dropped(self):
        query = QueryDict(
            'mode=sql&scope=trash&tags=not-a-uuid&tags=1&shared=maybe&updated=forever'
            '&updated_from=yesterday&updated_to=2026-13-40&sort=random&q=%20%20hello%20%20'
        )

        params = FilterParams.from_query(query)

        assert params == FilterParams(q='hello')

    def test_a_start_after_the_end_is_swapped(self):
        params = FilterParams.from_query(
            QueryDict('updated=custom&updated_from=2026-11-30&updated_to=2026-11-01')
        )

        assert params.updated_from == date(2026, 11, 1) and params.updated_to == date(2026, 11, 30)
        assert 'updated_from=2026-11-01&updated_to=2026-11-30' in params.to_query()
        assert params.to_dict()['updated_from'] == '2026-11-01'
        stored = FilterParams.from_dict(
            {'updated': 'custom', 'updated_from': '2026-12-31', 'updated_to': '2026-12-01'}
        )
        assert stored.updated_from == date(2026, 12, 1) and stored.updated_to == date(2026, 12, 31)
        # Dates with no range (a hand-made link) are swapped too; one date alone and equal ones stay.
        assert FilterParams.from_query(
            QueryDict('updated_from=2026-11-30&updated_to=2026-11-01')
        ).updated_from == date(2026, 11, 1)
        same = FilterParams.from_query(QueryDict('updated_from=2026-11-05&updated_to=2026-11-05'))
        assert same.updated_from == same.updated_to == date(2026, 11, 5)

    def test_a_backwards_range_selects_the_days_between(self, user):
        make_note(user, title='inside', updated_at=utc(2026, 11, 15, 12))
        make_note(user, title='outside', updated_at=utc(2026, 12, 15, 12))
        params = FilterParams.from_query(
            QueryDict('updated=custom&updated_from=2026-11-30&updated_to=2026-11-01')
        )

        assert [n.title for n in filter_notes(user, params)] == ['inside']

    def test_the_direction_defaults_follow_the_sort(self):
        for sort, default in (
            ('updated', 'desc'),
            ('created', 'desc'),
            ('title', 'asc'),
            ('manual', 'asc'),
        ):
            params = FilterParams(sort=sort)
            assert params.direction == default and params.dir == ''
            assert FilterParams(sort=sort, dir=default) == params  # the default is no direction
            opposite = 'asc' if default == 'desc' else 'desc'
            assert FilterParams(sort=sort, dir=opposite).direction == opposite

    def test_the_direction_is_written_only_when_it_is_not_the_default(self):
        assert FilterParams(sort='title').to_query(keep_sort=True) == 'sort=title'
        assert FilterParams(sort='title', dir='desc').to_query() == 'sort=title&dir=desc'
        assert FilterParams(dir='asc').to_query() == 'dir=asc'
        assert FilterParams(dir='desc').to_query() == ''
        # A filter saved before the direction existed reads back, and stores, identically.
        assert 'dir' not in FilterParams(sort='title', q='x').to_dict()
        assert FilterParams(dir='asc').to_dict()['dir'] == 'asc'
        legacy = FilterParams.from_dict(FilterParams(sort='title', q='x').to_dict())
        assert legacy == FilterParams(sort='title', q='x') and legacy.to_dict() == {
            key: value for key, value in FilterParams(sort='title', q='x').to_dict().items()
        }

    def test_the_direction_is_read_leniently(self):
        assert FilterParams.from_query(QueryDict('dir=ASC')).dir == 'asc'
        assert FilterParams.from_query(QueryDict('dir=up')).dir == ''
        assert FilterParams.from_query(QueryDict('sort=title&dir=desc')).dir == 'desc'
        assert FilterParams.from_query(QueryDict('sort=title&dir=asc')).dir == ''
        assert FilterParams.from_dict({'sort': 'created', 'dir': 'asc'}).dir == 'asc'
        assert FilterParams.from_dict({'dir': 7}) == FilterParams()

    def test_it_agrees_with_the_lists_on_defaults_and_parsing(self):
        from ..services.lists import DEFAULT_DIRECTIONS, sort_direction

        for sort in DEFAULT_DIRECTIONS:
            for raw in (None, '', 'asc', 'desc', 'ASC', 'sideways'):
                params = FilterParams.from_query(QueryDict(f'sort={sort}&dir={raw or ""}'))
                # The lists don't lower-case a direction; the search is the more lenient of the two.
                expected = sort_direction(sort, raw.lower() if raw else raw)
                assert params.direction == expected

    def test_the_trash_is_not_a_scope(self):
        assert FilterParams.from_query(QueryDict('scope=trash')).scope == 'active'
        assert FilterParams.from_query(QueryDict('scope=ARCHIVED')).scope == 'archived'

    def test_comma_separated_lists(self):
        assert FilterParams.from_query(QueryDict('tags=none,none')).tags == ('none',)

    def test_the_shared_switch(self):
        for raw, expected in (
            ('yes', 'yes'),
            ('no', 'no'),
            ('1', 'yes'),
            ('true', 'yes'),
            ('', ''),
        ):
            assert FilterParams.from_query(QueryDict(f'shared={raw}')).shared == expected

    def test_dates_without_a_range_mean_custom(self):
        params = FilterParams.from_query(QueryDict('updated_from=2026-11-01'))

        assert params.updated == 'custom' and params.updated_from == date(2026, 11, 1)

    def test_an_explicit_range_wins_over_the_dates(self):
        params = FilterParams.from_query(
            QueryDict('updated=any&updated_from=2026-11-01&updated_to=2026-11-02')
        )
        assert params.updated == 'any' and params.updated_from is None
        params = FilterParams.from_query(QueryDict('updated=today&updated_from=2026-11-01'))
        assert params.updated == 'today' and params.updated_from is None

    def test_a_custom_range_without_dates_is_any_time(self):
        assert FilterParams.from_query(QueryDict('updated=custom')).updated == 'any'

    def test_tags_that_are_not_the_users_are_dropped(self, user, other_user):
        mine = make_tag(user, 'Home')
        theirs = make_tag(other_user, 'Away')
        query = QueryDict(f'tags={mine.pk}&tags={theirs.pk}&tags=none')

        params = parse_filter_params(query, user)

        assert params.tags == tuple(sorted([str(mine.pk), 'none']))

    def test_the_canonical_query_leaves_defaults_out_and_sorts_lists(self):
        params = FilterParams(q='deploy', shared='yes', updated='today')

        assert params.to_query() == 'q=deploy&shared=yes&updated=today'
        assert params.to_query(keep_sort=True).endswith('&sort=updated')
        assert params.to_query(page=2).endswith('&page=2')
        assert FilterParams(scope='all', sort='manual').to_query() == 'scope=all&sort=manual'

    def test_round_trips(self):
        params = FilterParams(
            q='a b',
            mode='regex',
            scope='archived',
            tags=('none',),
            shared='no',
            updated='custom',
            updated_from=date(2026, 1, 2),
            sort='title',
        )

        assert FilterParams.from_dict(params.to_dict()) == params
        assert FilterParams.from_query(QueryDict(params.to_query())) == params

    def test_from_dict_is_lenient_too(self):
        assert FilterParams.from_dict(None) == FilterParams()
        assert FilterParams.from_dict({'tags': 'x', 'sort': 7, 'scope': 3}) == FilterParams()

    def test_regex_detection(self):
        assert FilterParams(q='/a/').is_regex and FilterParams(q='a', mode='regex').is_regex
        assert not FilterParams(q='a/b').is_regex

    def test_only_selecting_params_count_as_criteria(self):
        assert not FilterParams(sort='title', scope='all').has_criteria
        assert FilterParams(q='x').has_criteria and FilterParams(tags=('none',)).has_criteria
        assert FilterParams(shared='no').has_criteria
        assert FilterParams(updated='today').has_criteria


class TestFilterNotes:
    def test_tags_are_any_of_and_none_means_untagged(self, user):
        home = make_tag(user, 'Home')
        work = make_tag(user, 'Work')
        make_note(user, title='a', tags=[home])
        make_note(user, title='b', tags=[work])
        make_note(user, title='c', tags=[home, work])
        make_note(user, title='d')

        def run(*tags):
            return sorted(n.title for n in filter_notes(user, FilterParams(tags=tags)))

        assert run(str(home.pk)) == ['a', 'c']
        assert run(str(home.pk), str(work.pk)) == ['a', 'b', 'c']
        assert run('none') == ['d']
        assert run('none', str(work.pk)) == ['b', 'c', 'd']

    def test_a_note_with_two_matching_tags_is_listed_once(self, user):
        home = make_tag(user, 'Home')
        work = make_tag(user, 'Work')
        make_note(user, title='both', tags=[home, work])

        page = filter_page(user, FilterParams(tags=(str(home.pk), str(work.pk))))

        assert page.paginator.count == 1

    def test_without_text_the_scope_decides(self, funded_user):
        create_note(funded_user, title='live')
        archive(create_note(funded_user, title='kept'))
        trash(create_note(funded_user, title='gone'))

        def run(scope):
            return sorted(n.title for n in filter_notes(funded_user, FilterParams(scope=scope)))

        assert run('active') == ['live'] and run('archived') == ['kept']
        assert run('all') == ['kept', 'live']

    def test_filters_combine_with_the_text(self, user):
        home = make_tag(user, 'Home')
        make_note(user, title='rent home', tags=[home])
        make_note(user, title='rent away')
        make_note(user, title='other home', tags=[home])

        params = FilterParams(q='rent', tags=(str(home.pk),))

        assert [n.title for n in filter_notes(user, params)] == ['rent home']

    def test_search_notes_narrows_with_the_filters_but_takes_its_own_text(self, user):
        home = make_tag(user, 'Home')
        make_note(user, title='rent home', tags=[home])
        make_note(user, title='rent away')

        page = search_notes(
            user,
            q='rent',
            scope='active',
            filters=FilterParams(q='ignored', scope='archived', tags=(str(home.pk),)),
        )

        assert titles(page) == ['rent home']

    def test_a_search_that_cannot_run_raises_at_once(self, user):
        with pytest.raises(ValidationError):
            filter_notes(user, FilterParams(q='/(/'))

    def test_the_order_follows_the_sort(self, user):
        make_note(user, title='b', updated_at=utc(2026, 1, 1), position=2)
        make_note(user, title='a', updated_at=utc(2026, 6, 1), position=1)

        def run(sort):
            return [n.title for n in filter_notes(user, FilterParams(sort=sort))]

        assert run('updated') == ['a', 'b'] and run('title') == ['a', 'b']
        assert run('manual') == ['a', 'b']


class TestSharedFilter:
    def _shared(self, user, title, **link):
        note = make_note(user, title=title)
        make_link(note, **link)
        return note

    def run(self, user, shared):
        return sorted(n.title for n in filter_notes(user, FilterParams(shared=shared)))

    def test_an_active_link_makes_a_note_shared(self, user):
        self._shared(user, 'with link')
        make_note(user, title='no link')

        assert self.run(user, 'yes') == ['with link']
        assert self.run(user, 'no') == ['no link']
        assert self.run(user, '') == ['no link', 'with link']

    def test_a_revoked_or_expired_link_does_not(self, user):
        now = timezone.now()
        self._shared(user, 'revoked', revoked_at=now)
        self._shared(user, 'expired', expires_at=now - timedelta(days=1))
        self._shared(user, 'future', expires_at=now + timedelta(days=1))

        assert self.run(user, 'yes') == ['future']
        assert self.run(user, 'no') == ['expired', 'revoked']

    def test_a_note_whose_links_do_not_work_is_not_shared(self, user):
        now = timezone.now()
        locked = make_note(user, title='locked', locked_at=now)
        make_link(locked)
        down = make_note(user, title='down', taken_down_at=now)
        make_link(down)
        self._shared(user, 'fine')

        assert self.run(user, 'yes') == ['fine']
        assert self.run(user, 'no') == ['down', 'locked']

    def test_several_links_are_one_note(self, user):
        note = self._shared(user, 'two links')
        make_link(note, mode='edit')

        page = filter_page(user, FilterParams(shared='yes'))

        assert page.paginator.count == 1

    def test_another_users_link_does_not_count(self, user, other_user):
        make_note(user, title='mine')
        self._shared(other_user, 'theirs')

        assert self.run(user, 'yes') == []

    def test_it_follows_the_share_services(self, funded_user):
        note = create_note(funded_user, title='N')
        link = links.create_link(note, mode='read')
        assert self.run(funded_user, 'yes') == ['N']

        links.revoke_link(link)

        assert self.run(funded_user, 'yes') == [] and self.run(funded_user, 'no') == ['N']


class TestUpdatedRange:
    @pytest.fixture
    def notes(self, user):
        # `now` is frozen at 2026-10-28 12:00 UTC by the tests that need it.
        make_note(user, title='today', updated_at=utc(2026, 10, 28, 9))
        make_note(user, title='yesterday', updated_at=utc(2026, 10, 27, 9))
        make_note(user, title='six days ago', updated_at=utc(2026, 10, 22, 9))
        make_note(user, title='seven days ago', updated_at=utc(2026, 10, 21, 9))
        make_note(user, title='a month ago', updated_at=utc(2026, 9, 29, 9))
        make_note(user, title='long ago', updated_at=utc(2026, 1, 1))

    def run(self, user, **params):
        return sorted(n.title for n in filter_notes(user, FilterParams(**params)))

    def test_today(self, user, notes, freeze_now):
        freeze_now(2026, 10, 28, 12)

        assert self.run(user, updated='today') == ['today']

    def test_the_last_7_days_end_today(self, user, notes, freeze_now):
        freeze_now(2026, 10, 28, 12)

        assert self.run(user, updated='last_7_days') == ['six days ago', 'today', 'yesterday']

    def test_the_last_30_days(self, user, notes, freeze_now):
        freeze_now(2026, 10, 28, 12)

        assert self.run(user, updated='last_30_days') == [
            'a month ago',
            'seven days ago',
            'six days ago',
            'today',
            'yesterday',
        ]

    def test_a_custom_range_is_inclusive_and_may_be_open(self, user, notes):
        both = self.run(
            user, updated='custom', updated_from=date(2026, 10, 22), updated_to=date(2026, 10, 27)
        )
        assert both == ['six days ago', 'yesterday']
        assert self.run(user, updated='custom', updated_from=date(2026, 10, 27)) == [
            'today',
            'yesterday',
        ]
        assert self.run(user, updated='custom', updated_to=date(2026, 9, 29)) == [
            'a month ago',
            'long ago',
        ]

    def test_the_day_is_the_whole_day_in_the_users_zone(self, user, freeze_now):
        user.timezone = 'Pacific/Auckland'
        user.save()
        freeze_now(
            2026, 10, 28, 12, 0
        )  # still the 28th in UTC, already the 29th (01:00) in Auckland
        # 2026-10-29 00:00 Auckland (NZDT, +13) is 2026-10-28 11:00 UTC.
        make_note(user, title='just inside', updated_at=utc(2026, 10, 28, 11, 30))
        make_note(user, title='just outside', updated_at=utc(2026, 10, 28, 10, 30))

        assert self.run(user, updated='today') == ['just inside']

    def test_the_end_of_the_day_in_the_users_zone(self, user):
        user.timezone = 'America/Chicago'
        user.save()
        # 2026-10-27 23:30 Chicago (CDT, -5) is 2026-10-28 04:30 UTC.
        make_note(user, title='late evening', updated_at=utc(2026, 10, 28, 4, 30))
        make_note(user, title='next morning', updated_at=utc(2026, 10, 28, 5, 30))

        got = self.run(
            user, updated='custom', updated_from=date(2026, 10, 27), updated_to=date(2026, 10, 27)
        )

        assert got == ['late evening']

    def test_a_saved_range_stays_relative(self, user, notes, freeze_now):
        freeze_now(2026, 10, 28, 12)
        assert self.run(user, updated='today') == ['today']
        freeze_now(2026, 10, 27, 12)
        assert self.run(user, updated='today') == ['yesterday']


class TestDescribeParams:
    def test_the_summary(self, user):
        work = make_tag(user, 'work')
        params = FilterParams(
            q='deploy*', tags=(str(work.pk),), shared='yes', updated='last_7_days'
        )

        assert (
            describe_params(params, {str(work.pk): 'work'})
            == '@work · shared · last 7 days · "deploy*"'
        )

    def test_everything(self):
        params = FilterParams(
            q='/a.b/',
            scope='all',
            tags=('none',),
            shared='no',
            updated='custom',
            updated_from=date(2026, 11, 1),
            updated_to=date(2026, 11, 30),
            sort='title',
        )

        text = describe_params(params, {})

        assert text == (
            'no tag · not shared · Nov 1, 2026 to Nov 30, 2026 · /a.b/ · including archived · by title'
        )

    def test_the_scopes_and_open_ended_ranges(self):
        assert describe_params(FilterParams(scope='archived')) == 'archived only'
        assert (
            describe_params(FilterParams(updated='custom', updated_from=date(2026, 11, 1)), {})
            == 'from Nov 1, 2026'
        )
        assert (
            describe_params(FilterParams(updated='custom', updated_to=date(2026, 11, 1)), {})
            == 'until Nov 1, 2026'
        )
        assert describe_params(FilterParams(q='a b', mode='regex')) == '/a b/'

    def test_dates_are_written_in_the_formats_asked_for(self):
        params = FilterParams(updated='custom', updated_from=date(2026, 11, 1))

        assert describe_params(params, {}, DisplayFormats('dmy', '24h')) == 'from 01/11/2026'
        assert describe_params(params, {}, DisplayFormats('mdy', '12h')) == 'from 11/01/2026'

    def test_a_tag_that_is_gone_is_left_out(self):
        assert describe_params(
            FilterParams(tags=('8f1c6e0e-6b36-4d52-8f7b-2c5d9c6a1e11',)), {}
        ) == ('all notes')

    def test_nothing(self):
        assert describe_params(FilterParams()) == 'all notes'


class TestSearchViews:
    @pytest.fixture
    def seeded(self, funded_user):
        home = create_tag(funded_user, 'Home')
        create_note(funded_user, title='Pay the rent', content='by the 1st', tags=[home.pk])
        create_note(funded_user, title='Walk the Cão', content='Rent a bike')
        old = create_note(funded_user, title='Old rent')
        archive(old)
        return funded_user

    def test_anonymous_visitors_go_to_the_login_page(self, client):
        for name in ('notes:search', 'notes:search-list', 'notes:filters'):
            response = client.get(reverse(name))
            assert response.status_code == 302 and '/account/login' in response['Location']

    def test_the_page_without_a_query_asks_for_one(self, web):
        response = web.get(reverse('notes:search'))

        assert response.status_code == 200
        assert 'Type something to search for' in response.content.decode()

    def test_results(self, web, seeded):
        response = web.get(reverse('notes:search'), {'q': 'rent'})
        html = unescape(response.content.decode())

        assert response.status_code == 200
        assert 'Pay the rent' in html and 'Walk the' in html and 'Old rent' not in html
        assert '2 results' in html
        assert '<mark>rent</mark>' in html
        assert 'matches shown without accents or formatting' in html
        assert 'Title:' in html and 'Content:' in html

    def test_results_are_decorated_like_the_lists(self, web, funded_user):
        note = create_note(funded_user, title='Shared rent', content='x')
        links.create_link(note, mode='read')
        tag = create_tag(funded_user, 'Money')
        note2 = create_note(funded_user, title='Tagged rent', content='x', tags=[tag.pk])

        html = unescape(web.get(reverse('notes:search'), {'q': 'rent'}).content.decode())

        assert 'Shared by link' in html  # the share icon, from the active-link flag
        assert 'Money' in html  # the tag chip
        assert reverse('notes:note', args=[note2.pk]) in html

    def test_a_result_row_has_the_lists_menu(self, web, seeded):
        html = web.get(reverse('notes:search'), {'q': 'rent'}).content.decode()

        assert 'Move to trash' in html and 'Archive' in html and 'Duplicate' in html

    def test_accents(self, web, seeded):
        assert 'Walk the' in web.get(reverse('notes:search'), {'q': 'cao'}).content.decode()

    def test_the_scope(self, web, seeded):
        archived = unescape(
            web.get(reverse('notes:search'), {'q': 'rent', 'scope': 'archived'}).content.decode()
        )
        everything = unescape(
            web.get(reverse('notes:search'), {'q': 'rent', 'scope': 'all'}).content.decode()
        )

        assert 'Old rent' in archived and 'Pay the rent' not in archived and '1 result<' in archived
        assert 'Old rent' in everything and '3 results' in everything
        assert 'Archived</span>' in everything  # the archived row is marked

    def test_regex_checkbox_and_slashes(self, web, seeded):
        html = web.get(reverse('notes:search'), {'q': '^pay', 'mode': 'regex'}).content.decode()
        assert 'Pay the rent' in html
        html = web.get(reverse('notes:search'), {'q': '/^walk/'}).content.decode()
        assert 'Walk the' in html

    def test_the_sort_control_has_a_direction(self, web, seeded):
        html = web.get(reverse('notes:search'), {'q': 'rent'}).content.decode()

        assert 'id="search-dir"' in html and 'name="dir"' in html
        assert '<option value="desc" selected>Descending</option>' in html
        asc = web.get(reverse('notes:search'), {'q': 'rent', 'sort': 'title'}).content.decode()
        assert '<option value="asc" selected>Ascending</option>' in asc

    def test_the_direction_orders_the_results_and_is_canonical_in_the_url(self, web, funded_user):
        create_note(funded_user, title='rent a')
        create_note(funded_user, title='rent b')

        newest = text(web.get(reverse('notes:search'), {'q': 'rent', 'dir': 'desc'}))
        oldest = web.get(reverse('notes:search-list'), {'q': 'rent', 'dir': 'asc'}, **HTMX)

        assert newest.index('rent b') < newest.index('rent a')
        assert text(oldest).index('rent a') < text(oldest).index('rent b')
        assert oldest['HX-Push-Url'].endswith('?q=rent&dir=asc')
        default = web.get(reverse('notes:search-list'), {'q': 'rent', 'dir': 'desc'}, **HTMX)
        assert default['HX-Push-Url'].endswith('?q=rent')

    def test_a_saved_filter_keeps_the_direction(self, web, funded_user):
        saved = save_filter(funded_user, 'F', FilterParams(q='rent', sort='title', dir='desc'))

        assert saved.params['dir'] == 'desc'
        assert 'by title (Z to A)' in text(web.get(reverse('notes:filter', args=[saved.pk])))

    def test_the_filter_bar(self, web, seeded):
        tag = seeded.notes_tags.get()

        html = web.get(reverse('notes:search'), {'tags': str(tag.pk)}).content.decode()

        assert 'Pay the rent' in html and 'Walk the' not in html

    def test_the_shared_filter(self, web, funded_user):
        shared = create_note(funded_user, title='Open note')
        create_note(funded_user, title='Private note')
        links.create_link(shared, mode='read')

        html = web.get(reverse('notes:search'), {'shared': 'yes'}).content.decode()
        none = web.get(reverse('notes:search'), {'shared': 'no'}).content.decode()

        assert 'Open note' in html and 'Private note' not in html
        assert 'Private note' in none and 'Open note' not in none

    def test_a_refusal_is_a_422_line(self, web):
        response = web.get(reverse('notes:search'), {'q': '/(/'})

        assert response.status_code == 422
        assert 'Invalid regular expression.' in response.content.decode()

    def test_an_empty_text_with_a_filter_is_not_an_error(self, web, seeded):
        response = web.get(reverse('notes:search'), {'q': '', 'shared': 'no'})

        assert response.status_code == 200 and 'Pay the rent' in response.content.decode()

    def test_nothing_matches(self, web, seeded):
        html = web.get(reverse('notes:search'), {'q': 'zzzz'}).content.decode()

        assert 'Nothing matches.' in html and '0 results' in html

    def test_the_list_partial_for_htmx(self, web, seeded):
        response = web.get(reverse('notes:search-list'), {'q': 'rent', 'sort': 'updated'}, **HTMX)

        assert response.status_code == 200
        assert response['HX-Push-Url'] == '/notes/search/?q=rent'
        html = response.content.decode()
        assert 'Pay the rent' in html and '<html' not in html
        assert 'id="search-msg" hx-swap-oob' in html

    def test_the_push_url_is_canonical(self, web, seeded):
        response = web.get(
            reverse('notes:search-list'),
            {'q': 'rent', 'mode': 'regex', 'shared': 'no', 'sort': 'bogus', 'junk': 'x'},
            **HTMX,
        )

        assert response['HX-Push-Url'] == '/notes/search/?q=rent&mode=regex&shared=no'

    def test_the_list_partial_refusal(self, web):
        response = web.get(reverse('notes:search-list'), {'q': '(a)\\1', 'mode': 'regex'}, **HTMX)

        assert response.status_code == 422 and response['HX-Retarget'] == '#search-msg'
        assert 'Invalid regular expression.' in response.content.decode()

    def test_the_list_partial_opened_directly_goes_to_the_page(self, web):
        response = web.get(reverse('notes:search-list'), {'q': 'rent', 'sort': 'title'})

        assert response.status_code == 302
        assert response['Location'] == '/notes/search/?q=rent&sort=title'

    def test_paging(self, web, funded_user):
        for n in range(55):
            make_note(funded_user, title=f'rent {n:02}')

        first = web.get(reverse('notes:search-list'), {'q': 'rent'}, **HTMX).content.decode()
        second = web.get(reverse('notes:search-list'), {'q': 'rent', 'page': 2}, **HTMX)

        assert 'Page 1 of 2' in first and 'page=2' in first
        assert 'Page 2 of 2' in second.content.decode()
        assert second['HX-Push-Url'] == '/notes/search/?q=rent&page=2'

    def test_a_row_action_from_the_results_comes_back_to_the_search(self, web, seeded):
        note = Note.objects.get(title='Pay the rent')
        page = web.get(reverse('notes:search'), {'q': 'rent'}).content.decode()
        assert 'value="/notes/search/?q=rent"' in page  # the row's `next`

        response = web.post(
            reverse('notes:archive', args=[note.pk]), {'next': '/notes/search/?q=rent'}
        )

        assert response.status_code == 302 and response['Location'] == '/notes/search/?q=rent'
        note.refresh_from_db()
        assert note.is_archived

    def test_the_sidebar_search_box(self, web):
        html = web.get(reverse('notes:list')).content.decode()

        assert 'role="search"' in html and 'name="q"' in html and 'autocomplete="off"' in html
        assert 'value="regex"' in html and 'name="scope"' in html
        assert 'Search help' in html

    def test_every_notes_page_has_the_box_and_the_filters(self, web, funded_user):
        note = create_note(funded_user, title='N')
        for url in (
            reverse('notes:list'),
            reverse('notes:archived'),
            reverse('notes:trash'),
            reverse('notes:tags'),
            reverse('notes:note', args=[note.pk]),
            reverse('notes:edit', args=[note.pk]),
            reverse('notes:new'),
            reverse('notes:history', args=[note.pk]),
        ):
            html = web.get(url).content.decode()
            assert 'role="search"' in html and 'aria-label="Saved filters"' in html, url

    def test_the_search_page_does_not_repeat_the_sidebar_box(self, web):
        html = web.get(reverse('notes:search')).content.decode()

        assert html.count('role="search"') == 1

    def test_the_search_help_says_what_is_searched(self, web):
        html = unescape(web.get(reverse('notes:search')).content.decode())

        assert (
            'Notes in the Trash are not' in html
            and 'Earlier versions of a note are not searched' in html
        )


class TestSearchRateLimit:
    @pytest.fixture(autouse=True)
    def _limits(self, settings, ratelimit_clock):
        settings.RATELIMIT_ENABLE = True
        settings.RATELIMIT_RULES = {**settings.RATELIMIT_RULES, 'NOTES_SEARCH_USER': (2, 60)}

    def test_the_limit_is_charged_per_search(self, web):
        for _ in range(2):
            assert web.get(reverse('notes:search'), {'q': 'x'}).status_code == 200

        response = web.get(reverse('notes:search-list'), {'q': 'x'}, **HTMX)

        assert response.status_code == 429 and response['HX-Retarget'] == '#search-msg'

    def test_the_page_gets_the_429_page(self, web):
        for _ in range(2):
            web.get(reverse('notes:search'), {'q': 'x'})

        assert web.get(reverse('notes:search'), {'q': 'x'}).status_code == 429

    def test_a_page_with_nothing_to_search_for_is_free(self, web):
        for _ in range(5):
            assert web.get(reverse('notes:search')).status_code == 200

    def test_a_refused_query_is_still_charged(self, web):
        for _ in range(2):
            assert web.get(reverse('notes:search'), {'q': '/(/'}).status_code == 422

        assert web.get(reverse('notes:search'), {'q': 'x'}).status_code == 429

    def test_other_users_have_their_own_budget(self, web):
        for _ in range(2):
            web.get(reverse('notes:search'), {'q': 'x'})
        client = Client()
        client.force_login(make_user())

        assert client.get(reverse('notes:search'), {'q': 'x'}).status_code == 200


def test_a_content_edit_through_save_note_changes_what_is_found(funded_user):
    note = create_note(funded_user, title='N', content='alpha')

    save_note(note, editor=Editor('owner', funded_user, None), content='beta', base_version=1)

    assert titles(find(funded_user, 'alpha')) == []
    assert titles(find(funded_user, 'beta')) == ['N']
