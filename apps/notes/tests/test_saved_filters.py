"""Saved filters (issue #260, PR 5): the model, the services (CRUD, the cap, uniqueness, reordering,
counts), the filters pages and endpoints, the sidebar on every Notes page, the dropped-tag notice,
404s and rate limits. To-do's `test_saved_filters.py` for notes."""

from datetime import date
from html import unescape
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, connection, transaction
from django.db.utils import DataError, OperationalError
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from apps.core import search as core_search

from ..models import NotesAccount, SavedFilter
from ..services import (
    MAX_SAVED_FILTERS,
    FilterParams,
    archive,
    create_note,
    create_tag,
    delete_filter,
    links,
    list_filters,
    rename_filter,
    reorder_filters,
    save_filter,
    sidebar_filters,
    trash,
    update_filter_params,
)
from ..services.saved_filters import (
    SIDEBAR_LIMIT,
    dropped_tag_count,
    filter_counts,
    filter_rows,
    note_count,
    note_counts,
    stored_params,
)
from .factories import make_note, make_tag, make_user, utc

pytestmark = pytest.mark.django_db

HTMX = {'HTTP_HX_REQUEST': 'true'}


def text(response) -> str:
    return unescape(response.content.decode())


def names(user) -> list[str]:
    return [saved.name for saved in list_filters(user)]


def message(exc_info) -> str:
    return ' '.join(exc_info.value.messages)


class TestModel:
    def test_params_are_stored_canonically(self, user):
        saved = SavedFilter.objects.create(
            user=user, name='x', params={'q': ' rent ', 'scope': 'trash', 'shared': 'y', 'junk': 1}
        )

        assert saved.params == FilterParams(q='rent').to_dict()
        assert SavedFilter.objects.get(pk=saved.pk).params == saved.params

    def test_clean_accepts_a_canonical_dict_and_refuses_the_rest(self, user):
        good = SavedFilter(user=user, name='x', params=FilterParams(q='a').to_dict())
        good.full_clean(exclude=['user'])

        bad = SavedFilter(user=user, name='x', params={'scope': 'trash'})
        with pytest.raises(ValidationError):
            bad.full_clean(exclude=['user'])
        with pytest.raises(ValidationError):
            SavedFilter(user=user, name='x', params=['not', 'a', 'dict']).full_clean(
                exclude=['user']
            )

    def test_the_name_is_unique_per_user_ignoring_case(self, user, other_user):
        SavedFilter.objects.create(user=user, name='Rent')

        with pytest.raises(IntegrityError), transaction.atomic():
            SavedFilter.objects.create(user=user, name='rent')
        SavedFilter.objects.create(user=other_user, name='rent')

    def test_the_user_relation_is_not_todos(self, user):
        SavedFilter.objects.create(user=user, name='x')

        assert user.notes_saved_filters.count() == 1
        assert user.todo_saved_filters.count() == 0

    def test_deleting_the_user_deletes_the_filters(self, user):
        SavedFilter.objects.create(user=user, name='x')

        user.delete()

        assert not SavedFilter.objects.exists()


class TestSaveFilter:
    def test_it_saves_at_the_end_of_the_list(self, user):
        first = save_filter(user, 'First', FilterParams(q='a'))
        second = save_filter(user, 'Second', FilterParams(shared='yes'))

        assert names(user) == ['First', 'Second']
        assert first.position < second.position
        assert second.params['shared'] == 'yes'

    def test_it_takes_a_dict_too(self, user):
        saved = save_filter(user, 'x', {'q': 'rent', 'sort': 'title'})

        assert stored_params(saved) == FilterParams(q='rent', sort='title')

    def test_the_name_is_cleaned(self, user):
        assert save_filter(user, '  Next   week  ', FilterParams()).name == 'Next week'

    def test_a_name_is_required_and_capped(self, user):
        with pytest.raises(ValidationError) as empty:
            save_filter(user, '   ', FilterParams())
        with pytest.raises(ValidationError) as long:
            save_filter(user, 'x' * 51, FilterParams())

        assert 'Name is required.' in message(empty)
        assert 'at most 50' in message(long)
        save_filter(user, 'x' * 50, FilterParams())

    def test_names_are_unique_ignoring_case(self, user):
        save_filter(user, 'Rent', FilterParams())

        with pytest.raises(ValidationError) as caught:
            save_filter(user, ' rent ', FilterParams())

        assert message(caught) == 'You already have a filter with this name.'

    def test_two_users_may_share_a_name(self, user, other_user):
        save_filter(user, 'Rent', FilterParams())
        save_filter(other_user, 'Rent', FilterParams())

    def test_the_cap(self, user):
        for n in range(MAX_SAVED_FILTERS):
            save_filter(user, f'Filter {n}', FilterParams())

        with pytest.raises(ValidationError) as caught:
            save_filter(user, 'One more', FilterParams())

        assert message(caught) == f'You can have up to {MAX_SAVED_FILTERS} saved filters.'
        assert SavedFilter.objects.filter(user=user).count() == 50

    def test_another_users_filters_do_not_count_towards_the_cap(self, user, other_user):
        for n in range(MAX_SAVED_FILTERS):
            save_filter(other_user, f'F{n}', FilterParams())

        save_filter(user, 'Mine', FilterParams())

    def test_a_search_that_cannot_run_is_refused(self, user):
        for q in ('/(/', r'/(a)\1/', 'x' * 201):
            with pytest.raises(ValidationError):
                save_filter(user, 'Bad', FilterParams(q=q))
        assert not SavedFilter.objects.exists()

    def test_tags_that_are_not_the_users_are_dropped(self, user, other_user):
        mine = make_tag(user, 'Mine')
        theirs = make_tag(other_user, 'Theirs')

        saved = save_filter(user, 'T', FilterParams(tags=(str(mine.pk), str(theirs.pk), 'none')))

        assert saved.params['tags'] == sorted([str(mine.pk), 'none'])

    def test_it_creates_the_account_row_it_locks(self, user):
        assert not NotesAccount.objects.filter(user=user).exists()

        save_filter(user, 'x', FilterParams())

        assert NotesAccount.objects.filter(user=user).exists()

    def test_it_takes_the_account_lock_first(self, user, monkeypatch):
        calls = []
        from ..services import saved_filters as module

        real = module.lock_account
        monkeypatch.setattr(module, 'lock_account', lambda uid: calls.append(uid) or real(uid))

        save_filter(user, 'x', FilterParams())

        assert calls == [user.pk]


class TestRenameUpdateDelete:
    def test_rename(self, user):
        saved = save_filter(user, 'Old', FilterParams())

        rename_filter(saved, '  New  name ')

        assert SavedFilter.objects.get(pk=saved.pk).name == 'New name'

    def test_renaming_to_its_own_name_in_another_case_is_fine(self, user):
        saved = save_filter(user, 'Rent', FilterParams())

        rename_filter(saved, 'RENT')

        assert SavedFilter.objects.get(pk=saved.pk).name == 'RENT'

    def test_renaming_onto_another_filter_is_refused(self, user):
        save_filter(user, 'Taken', FilterParams())
        saved = save_filter(user, 'Mine', FilterParams())

        with pytest.raises(ValidationError) as caught:
            rename_filter(saved, 'taken')

        assert 'already have a filter' in message(caught)
        assert SavedFilter.objects.get(pk=saved.pk).name == 'Mine'

    def test_update_params(self, user):
        saved = save_filter(user, 'F', FilterParams(q='old'))

        update_filter_params(saved, FilterParams(q='new', shared='no', scope='all'))

        assert stored_params(SavedFilter.objects.get(pk=saved.pk)) == FilterParams(
            q='new', shared='no', scope='all'
        )

    def test_update_refuses_a_search_that_cannot_run(self, user):
        saved = save_filter(user, 'F', FilterParams(q='old'))

        with pytest.raises(ValidationError):
            update_filter_params(saved, FilterParams(q='/(/'))

        assert stored_params(SavedFilter.objects.get(pk=saved.pk)).q == 'old'

    def test_delete(self, user):
        saved = save_filter(user, 'F', FilterParams())

        delete_filter(saved)

        assert not SavedFilter.objects.exists()


class TestReorder:
    def test_it_writes_the_positions(self, user):
        a = save_filter(user, 'A', FilterParams())
        b = save_filter(user, 'B', FilterParams())
        c = save_filter(user, 'C', FilterParams())

        changed = reorder_filters(user, [c.pk, a.pk, b.pk])

        assert changed == 3 and names(user) == ['C', 'A', 'B']

    def test_other_users_filters_and_unknown_ids_are_ignored(self, user, other_user):
        mine = save_filter(user, 'Mine', FilterParams())
        theirs = save_filter(other_user, 'Theirs', FilterParams())
        theirs_position = theirs.position

        changed = reorder_filters(user, [theirs.pk, uuid4(), mine.pk])

        assert changed == 1
        assert SavedFilter.objects.get(pk=theirs.pk).position == theirs_position


class TestCounts:
    def test_the_count_is_the_notes_it_selects(self, funded_user):
        create_note(funded_user, title='Pay rent')
        create_note(funded_user, title='Pay bills')
        archive(create_note(funded_user, title='Pay tax'))

        assert note_count(funded_user, FilterParams(q='pay')) == 2
        assert note_count(funded_user, FilterParams(q='pay', scope='all')) == 3
        assert note_count(funded_user, FilterParams(q='pay', scope='archived')) == 1
        assert note_count(funded_user, FilterParams(q='rent')) == 1
        assert note_count(funded_user, FilterParams()) == 2

    def test_notes_in_the_trash_are_never_counted(self, funded_user):
        trash(create_note(funded_user, title='Pay rent'))
        create_note(funded_user, title='Pay bills')

        assert note_count(funded_user, FilterParams(q='pay', scope='all')) == 1

    def test_a_note_with_two_matching_tags_counts_once(self, funded_user):
        home = create_tag(funded_user, 'Home')
        work = create_tag(funded_user, 'Work')
        create_note(funded_user, title='N', tags=[home.pk, work.pk])

        assert note_count(funded_user, FilterParams(tags=(str(home.pk), str(work.pk)))) == 1

    def test_a_search_that_cannot_run_has_no_count(self, user):
        assert note_count(user, FilterParams(q='/(/')) is None

    def test_rows_carry_params_and_summary(self, funded_user):
        create_note(funded_user, title='Pay rent')
        saved = save_filter(funded_user, 'Rent', FilterParams(q='rent', shared='no', sort='title'))

        [row] = filter_rows(funded_user, [saved])

        assert row.params == FilterParams(q='rent', shared='no', sort='title')
        assert row.summary == 'not shared · "rent" · by title' and row.dropped_tags == 0

    def test_a_deleted_tag_drops_out_of_the_filter(self, funded_user):
        home = create_tag(funded_user, 'Home')
        create_note(funded_user, title='A', tags=[home.pk])
        create_note(funded_user, title='B')
        saved = save_filter(funded_user, 'Home', FilterParams(tags=(str(home.pk),)))
        assert filter_counts(funded_user, [saved]) == {saved.pk: 1}

        home.delete()

        saved = SavedFilter.objects.get(pk=saved.pk)
        [row] = filter_rows(funded_user, [saved])
        assert row.params.tags == () and row.summary == 'all notes' and row.dropped_tags == 1
        assert dropped_tag_count(saved, funded_user) == 1
        assert filter_counts(funded_user, [saved]) == {saved.pk: 2}

    def test_dropped_tags_are_counted_per_tag(self, funded_user):
        a = create_tag(funded_user, 'A')
        b = create_tag(funded_user, 'B')
        keep = create_tag(funded_user, 'Keep')
        saved = save_filter(
            funded_user, 'F', FilterParams(tags=(str(a.pk), str(b.pk), str(keep.pk), 'none'))
        )
        a.delete()
        b.delete()

        assert dropped_tag_count(SavedFilter.objects.get(pk=saved.pk), funded_user) == 2

    def test_all_the_counts_are_one_query(self, funded_user):
        home = create_tag(funded_user, 'home')
        create_note(funded_user, title='Pay rent', tags=[home.pk])
        create_note(funded_user, title='Pay bills')
        archive(create_note(funded_user, title='Pay tax'))
        shared = create_note(funded_user, title='Quiet', content='the rent')
        links.create_link(shared, mode='read')
        specs = [
            FilterParams(tags=(str(home.pk),)),
            FilterParams(q='pay*'),
            FilterParams(q='/rent/'),  # a title and a content match
            FilterParams(tags=('none',)),
            FilterParams(shared='yes'),
            FilterParams(shared='no'),
            FilterParams(q='pay', scope='archived'),
            FilterParams(q='pay', scope='all'),
            FilterParams(),
        ]
        saved = [save_filter(funded_user, f'F{n}', params) for n, params in enumerate(specs)]

        with CaptureQueriesContext(connection) as queries:
            counts = filter_counts(funded_user, saved)

        counting = [q for q in queries if 'COUNT(' in q['sql'].upper() or 'CASE WHEN' in q['sql']]
        assert len(counting) == 1
        assert len(queries) == 2  # the user's tags, then the one aggregate
        assert [counts[s.pk] for s in saved] == [1, 2, 2, 2, 1, 2, 1, 3, 3]

    def test_a_filter_that_cannot_run_is_none_and_the_others_are_counted(self, funded_user):
        create_note(funded_user, title='Pay rent')

        counts = note_counts(
            funded_user, {'ok': FilterParams(q='rent'), 'bad': FilterParams(q='/(/')}
        )

        assert counts == {'ok': 1, 'bad': None}

    def test_nothing_to_count(self, funded_user):
        assert note_counts(funded_user, {}) == {}
        assert note_counts(funded_user, {'bad': FilterParams(q='/(/')}) == {'bad': None}

    def test_another_users_notes_are_not_counted(self, funded_user, other_user):
        make_note(other_user, title='Pay rent')

        assert note_count(funded_user, FilterParams(q='rent')) == 0

    @pytest.fixture
    def pg(self, monkeypatch):
        fake = MagicMock()
        fake.vendor = 'postgresql'
        cursor = MagicMock()
        fake.cursor.return_value.__enter__.return_value = cursor
        monkeypatch.setattr(core_search, 'connection', fake)
        return cursor

    @pytest.mark.parametrize(
        'error',
        [
            OperationalError('canceling statement due to statement timeout'),
            DataError('invalid regular expression: parentheses () not balanced'),
        ],
    )
    def test_a_timeout_makes_every_count_none(self, pg, funded_user, error):
        create_note(funded_user, title='Pay rent')
        pg.execute.side_effect = [None, None, error]  # read the setting, set it, then the query

        counts = note_counts(
            funded_user, {'a': FilterParams(q='rent'), 'b': FilterParams(shared='no')}
        )

        assert counts == {'a': None, 'b': None}

    def test_the_counts_run_under_the_one_second_timeout(self, pg, funded_user):
        create_note(funded_user, title='Pay rent')

        assert note_counts(funded_user, {'a': FilterParams(q='rent')}) == {'a': 1}

        pg.execute.assert_any_call('SET LOCAL statement_timeout = %s', ['1000'])

    def test_the_sidebar_lists_twenty_and_counts_the_rest(self, user):
        for n in range(SIDEBAR_LIMIT + 3):
            save_filter(user, f'Filter {n:02}', FilterParams())

        rows, more = sidebar_filters(user)

        assert len(rows) == SIDEBAR_LIMIT and more == 3
        assert rows[0].name == 'Filter 00'

    def test_no_filters(self, user):
        assert sidebar_filters(user) == ([], 0)


class TestFiltersPage:
    def test_anonymous_visitors_go_to_the_login_page(self, client, user):
        saved = save_filter(user, 'F', FilterParams())
        for url in (reverse('notes:filters'), reverse('notes:filter', args=[saved.pk])):
            response = client.get(url)
            assert response.status_code == 302 and '/account/login' in response['Location']
        for name in ('rename', 'update', 'delete'):
            response = client.post(reverse(f'notes:filter-{name}', args=[saved.pk]))
            assert response.status_code == 302 and '/account/login' in response['Location']
        assert client.post(reverse('notes:filters'), {'name': 'x'}).status_code == 302
        assert client.post(reverse('notes:filter-reorder')).status_code == 302
        assert client.get(reverse('notes:filter-counts')).status_code == 302

    def test_empty(self, web):
        response = web.get(reverse('notes:filters'))

        assert response.status_code == 200 and 'No saved filters yet' in text(response)

    def test_the_list(self, web, funded_user):
        create_note(funded_user, title='Pay rent')
        save_filter(funded_user, 'Rent', FilterParams(q='rent', shared='yes', sort='title'))

        html = text(web.get(reverse('notes:filters')))

        assert 'Rent' in html
        assert 'shared · "rent" · by title' in html
        assert 'data-sortable' in html and reverse('notes:filter-reorder') in html
        # After a drag the sidebar's copy is reordered in place: nothing is fetched again.
        assert 'data-sync-order-with="#sidebar-filters"' in html
        assert 'data-after-reorder-url' not in html
        # The count is not computed with the page: an empty slot and a request for it.
        assert '1 note<' not in html
        assert f'id="page-count-{SavedFilter.objects.get().pk}"' in html
        assert f'hx-get="{reverse("notes:filter-counts")}?scope=all"' in html

    def test_it_creates_the_account_on_the_first_visit(self, web, funded_user):
        web.get(reverse('notes:filters'))

        assert NotesAccount.objects.filter(user=funded_user).exists()

    def test_a_deleted_tag_is_said_on_the_row(self, web, funded_user):
        home = create_tag(funded_user, 'Home')
        save_filter(funded_user, 'Home', FilterParams(tags=(str(home.pk),)))
        home.delete()

        html = text(web.get(reverse('notes:filters')))

        assert 'A tag this filter used was deleted and is ignored.' in html


class TestCreateEndpoint:
    def post(self, web, **data):
        return web.post(reverse('notes:filters'), data, **HTMX)

    def test_it_saves_the_posted_search_and_redirects_to_it(self, web, funded_user):
        response = self.post(
            web, name='Rent', q='rent*', mode='regex', scope='all', shared='no', sort='title'
        )
        saved = SavedFilter.objects.get(user=funded_user)

        assert response.status_code == 200
        assert response['HX-Redirect'] == reverse('notes:filter', args=[saved.pk])
        assert stored_params(saved) == FilterParams(
            q='rent*', mode='regex', scope='all', shared='no', sort='title'
        )

    def test_a_flash_message_follows(self, web):
        self.post(web, name='Rent', q='rent')

        assert 'Saved the filter Rent.' in text(web.get(reverse('notes:filters')))

    def test_a_name_is_required(self, web):
        response = self.post(web, name='', q='rent')

        assert response.status_code == 422 and response['HX-Retarget'] == '#filter-save-msg'
        assert 'Name: Name is required.' in text(response)
        assert not SavedFilter.objects.exists()

    def test_a_duplicate_name(self, web):
        self.post(web, name='Rent', q='a')

        response = self.post(web, name='RENT', q='b')

        assert response.status_code == 422
        assert 'You already have a filter with this name.' in text(response)

    def test_an_invalid_search_is_refused(self, web):
        response = self.post(web, name='Bad', q='/(/')

        assert response.status_code == 422 and 'Invalid regular expression.' in text(response)
        assert not SavedFilter.objects.exists()

    def test_the_cap(self, web, funded_user):
        for n in range(MAX_SAVED_FILTERS):
            save_filter(funded_user, f'F{n}', FilterParams())

        response = self.post(web, name='One more', q='a')

        assert response.status_code == 422 and 'up to 50 saved filters' in text(response)

    def test_without_htmx_it_redirects_to_the_filter(self, web, funded_user):
        response = web.post(reverse('notes:filters'), {'name': 'Rent', 'q': 'rent'})
        saved = SavedFilter.objects.get(user=funded_user)

        assert response.status_code == 302
        assert response['Location'] == reverse('notes:filter', args=[saved.pk])

    def test_a_refusal_without_htmx_goes_back_with_a_flash(self, web):
        response = web.post(reverse('notes:filters'), {'name': '', 'q': 'rent'})

        assert response.status_code == 302 and response['Location'] == reverse('notes:search')

    def test_foreign_tags_are_dropped(self, web, funded_user, other_user):
        theirs = make_tag(other_user, 'Theirs')

        self.post(web, name='L', tags=[str(theirs.pk)], shared='yes')

        assert SavedFilter.objects.get(user=funded_user).params['tags'] == []


class TestFilterPage:
    @pytest.fixture
    def saved(self, funded_user):
        shared = create_note(funded_user, title='Pay rent')
        links.create_link(shared, mode='read')
        create_note(funded_user, title='Pay bills')
        create_note(funded_user, title='Walk')
        return save_filter(funded_user, 'Payments', FilterParams(q='pay', shared='yes'))

    def test_it_runs_the_filter(self, web, saved):
        response = web.get(reverse('notes:filter', args=[saved.pk]))
        html = text(response)

        assert response.status_code == 200
        assert '<h1' in html and 'Payments' in html
        assert 'Pay rent' in html and 'Pay bills' not in html and 'Walk' not in html
        assert 'Update filter' not in html
        assert 'aria-current="page"' in html

    def test_the_tags_are_read_once_per_request(self, web, funded_user):
        home = create_tag(funded_user, 'Home')
        gone = create_tag(funded_user, 'Gone')
        saved = save_filter(
            funded_user, 'Tagged', FilterParams(q='pay', tags=(str(home.pk), str(gone.pk)))
        )
        gone.delete()
        url = reverse('notes:filter', args=[saved.pk])

        with CaptureQueriesContext(connection) as queries:
            response = web.get(url)
        tag_reads = [
            q['sql']
            for q in queries
            if 'FROM "notes_tag"' in q['sql'] and 'notes_note_tags' not in q['sql']
        ]

        # The page lists the filter as saved, its summary and the dropped tag: one read of the tags.
        assert response.status_code == 200 and '@Home' in text(response)
        assert 'A tag this filter used has been deleted' in text(response)
        assert len(tag_reads) == 1, tag_reads

        with CaptureQueriesContext(connection) as queries:
            web.get(reverse('notes:search-list'), {'filter': str(saved.pk), 'q': 'pay'}, **HTMX)
        assert (
            len(
                [
                    q
                    for q in queries
                    if 'FROM "notes_tag"' in q['sql'] and 'notes_note_tags' not in q['sql']
                ]
            )
            == 1
        )

    def test_the_form_is_filled_in_from_the_filter(self, web, saved):
        html = web.get(reverse('notes:filter', args=[saved.pk])).content.decode()

        assert 'value="pay"' in html
        assert f'name="filter" value="{saved.pk}"' in html

    def test_the_saved_direction_is_kept_and_a_changed_one_is_an_edit(self, web, funded_user):
        create_note(funded_user, title='Pay a')
        create_note(funded_user, title='Pay b')
        saved = save_filter(funded_user, 'Oldest', FilterParams(q='pay', dir='asc'))
        url = reverse('notes:filter', args=[saved.pk])

        as_saved = text(web.get(url))
        edited = text(web.get(url, {'q': 'pay', 'sort': 'updated'}))  # the default direction

        assert as_saved.index('Pay a') < as_saved.index('Pay b') and 'Update filter' not in as_saved
        assert 'by updated (oldest first)' in as_saved
        assert 'Update filter' in edited and edited.index('Pay b') < edited.index('Pay a')

    def test_edited_params_in_the_query_string_show_update_filter(self, web, saved):
        response = web.get(
            reverse('notes:filter', args=[saved.pk]), {'q': 'pay', 'sort': 'updated'}
        )
        html = text(response)

        assert 'Pay rent' in html and 'Pay bills' in html
        assert 'Update filter' in html
        assert reverse('notes:filter-update', args=[saved.pk]) in html

    def test_the_same_params_in_the_query_string_are_not_an_edit(self, web, saved):
        response = web.get(
            reverse('notes:filter', args=[saved.pk]),
            {'q': 'pay', 'shared': 'yes', 'sort': 'updated'},
        )

        assert 'Update filter' not in text(response)

    def test_clearing_everything_is_an_edit_not_the_saved_filter(self, web, saved):
        response = web.get(reverse('notes:filter', args=[saved.pk]), {'sort': 'updated'})
        html = text(response)

        assert 'Type something to search for' in html and 'Pay rent' not in html

    def test_the_list_partial_knows_the_filter(self, web, saved):
        response = web.get(
            reverse('notes:search-list'),
            {'filter': str(saved.pk), 'q': 'pay', 'sort': 'updated'},
            **HTMX,
        )

        assert 'Update filter' in text(response)
        assert (
            response['HX-Push-Url']
            == f'{reverse("notes:filter", args=[saved.pk])}?q=pay&sort=updated'
        )

    def test_a_bad_filter_id_in_the_list_is_ignored(self, web, saved):
        for value in ('nope', str(uuid4())):
            response = web.get(reverse('notes:search-list'), {'filter': value, 'q': 'pay'}, **HTMX)
            assert response.status_code == 200 and 'Update filter' not in text(response)

    def test_another_users_filter_is_not_found_in_the_list(self, web, other_user):
        theirs = save_filter(other_user, 'Theirs', FilterParams(q='x'))

        response = web.get(
            reverse('notes:search-list'),
            {'filter': str(theirs.pk), 'q': 'pay', 'sort': 'title'},
            **HTMX,
        )

        assert 'Update filter' not in text(response)
        assert response['HX-Push-Url'] == '/notes/search/?q=pay&sort=title'

    def test_a_filter_whose_tag_was_deleted_still_runs_and_says_so(self, web, funded_user):
        home = create_tag(funded_user, 'Home')
        create_note(funded_user, title='Task', tags=[home.pk])
        create_note(funded_user, title='Other')
        saved = save_filter(funded_user, 'Home', FilterParams(tags=(str(home.pk),)))
        home.delete()

        response = web.get(reverse('notes:filter', args=[saved.pk]))
        html = text(response)

        assert response.status_code == 200 and 'Task' in html and 'Other' in html
        assert 'A tag this filter used has been deleted' in html
        assert 'this filter now selects every note in its scope' in html
        assert 'choose "Update filter"' in html

    def test_the_notice_names_how_many_and_goes_when_the_filter_is_updated(self, web, funded_user):
        a = create_tag(funded_user, 'A')
        b = create_tag(funded_user, 'B')
        saved = save_filter(funded_user, 'AB', FilterParams(tags=(str(a.pk), str(b.pk)), q='x'))
        a.delete()
        b.delete()
        url = reverse('notes:filter', args=[saved.pk])
        assert '2 tags this filter used have been deleted' in text(web.get(url))

        web.post(reverse('notes:filter-update', args=[saved.pk]), {'q': 'x'}, **HTMX)

        assert 'have been deleted' not in text(web.get(url))

    def test_archived_scope_filters_run_in_their_scope(self, web, funded_user):
        archive(create_note(funded_user, title='Old plan'))
        create_note(funded_user, title='New plan')
        saved = save_filter(funded_user, 'Old', FilterParams(q='plan', scope='archived'))

        html = text(web.get(reverse('notes:filter', args=[saved.pk])))

        assert 'Old plan' in html and 'New plan' not in html


class TestRenameUpdateDeleteReorderEndpoints:
    @pytest.fixture
    def saved(self, funded_user):
        return save_filter(funded_user, 'Old', FilterParams(q='old'))

    def test_rename_rerenders_the_list_and_the_sidebar(self, web, saved):
        response = web.post(
            reverse('notes:filter-rename', args=[saved.pk]), {'name': 'New'}, **HTMX
        )

        assert response.status_code == 200 and 'New' in text(response)
        assert SavedFilter.objects.get(pk=saved.pk).name == 'New'
        assert '<html' not in response.content.decode()
        assert 'id="sidebar-filters" hx-swap-oob="true"' in response.content.decode()

    def test_rename_refusals(self, web, saved, funded_user):
        save_filter(funded_user, 'Taken', FilterParams())

        for value, line in (('', 'Name is required.'), ('taken', 'already have a filter')):
            response = web.post(
                reverse('notes:filter-rename', args=[saved.pk]), {'name': value}, **HTMX
            )
            assert response.status_code == 422 and response['HX-Retarget'] == '#filter-msg'
            assert line in text(response)
        assert SavedFilter.objects.get(pk=saved.pk).name == 'Old'

    def test_rename_without_htmx_redirects(self, web, saved):
        response = web.post(reverse('notes:filter-rename', args=[saved.pk]), {'name': 'New'})

        assert response.status_code == 302 and response['Location'] == reverse('notes:filters')

    def test_update_replaces_the_params_and_redirects_to_the_filter(self, web, saved):
        response = web.post(
            reverse('notes:filter-update', args=[saved.pk]),
            {'q': 'new', 'shared': 'yes', 'sort': 'title'},
            **HTMX,
        )

        assert response['HX-Redirect'] == reverse('notes:filter', args=[saved.pk])
        assert stored_params(SavedFilter.objects.get(pk=saved.pk)) == FilterParams(
            q='new', shared='yes', sort='title'
        )
        assert 'Updated the filter Old.' in text(web.get(response['HX-Redirect']))

    def test_update_refuses_an_invalid_search(self, web, saved):
        response = web.post(reverse('notes:filter-update', args=[saved.pk]), {'q': '/(/'}, **HTMX)

        assert response.status_code == 422 and response['HX-Retarget'] == '#search-msg'
        assert stored_params(SavedFilter.objects.get(pk=saved.pk)).q == 'old'

    def test_delete(self, web, saved):
        response = web.post(reverse('notes:filter-delete', args=[saved.pk]), **HTMX)

        assert response['HX-Redirect'] == reverse('notes:filters')
        assert not SavedFilter.objects.exists()
        assert 'Deleted the filter Old.' in text(web.get(reverse('notes:filters')))

    def test_delete_leaves_the_notes_alone(self, web, saved, funded_user):
        create_note(funded_user, title='old things')

        web.post(reverse('notes:filter-delete', args=[saved.pk]), **HTMX)

        assert funded_user.notes.count() == 1

    def test_delete_without_htmx_redirects(self, web, saved):
        response = web.post(reverse('notes:filter-delete', args=[saved.pk]))

        assert response.status_code == 302 and response['Location'] == reverse('notes:filters')

    def test_reorder(self, web, funded_user, saved):
        second = save_filter(funded_user, 'Second', FilterParams())

        response = web.post(
            reverse('notes:filter-reorder'), {'ids[]': [str(second.pk), str(saved.pk)]}, **HTMX
        )

        assert response.status_code == 204 and names(funded_user) == ['Second', 'Old']

    def test_reorder_refuses_more_ids_than_a_user_can_have(self, web, saved):
        ids = [str(uuid4()) for _ in range(MAX_SAVED_FILTERS + 1)]

        with CaptureQueriesContext(connection) as queries:
            response = web.post(reverse('notes:filter-reorder'), {'ids[]': ids}, **HTMX)

        assert response.status_code == 422
        assert not [q for q in queries if 'notes_savedfilter' in q['sql']]
        at_the_limit = [str(saved.pk), *ids[: MAX_SAVED_FILTERS - 1]]
        response = web.post(reverse('notes:filter-reorder'), {'ids[]': at_the_limit}, **HTMX)
        assert response.status_code == 204

    def test_reordering_in_the_service_is_capped_too(self, user):
        with pytest.raises(ValidationError):
            reorder_filters(user, [uuid4() for _ in range(MAX_SAVED_FILTERS + 1)])

    def test_reorder_with_a_bad_id(self, web, saved):
        response = web.post(reverse('notes:filter-reorder'), {'ids[]': ['nope']}, **HTMX)

        assert response.status_code == 422 and response['HX-Retarget'] == '#filter-msg'

    def test_only_post(self, web, saved):
        for name in ('rename', 'update', 'delete'):
            assert web.get(reverse(f'notes:filter-{name}', args=[saved.pk])).status_code == 405
        assert web.get(reverse('notes:filter-reorder')).status_code == 405

    def test_the_sidebar_items_carry_their_filter_id_for_the_client_side_reorder(
        self, web, funded_user
    ):
        first = save_filter(funded_user, 'A', FilterParams())
        second = save_filter(funded_user, 'B', FilterParams())

        html = text(web.get(reverse('notes:filters')))

        assert f'data-filter-id="{first.pk}"' in html and f'data-filter-id="{second.pk}"' in html

    def test_there_is_no_sidebar_endpoint_any_more(self, web):
        assert web.get('/notes/filters/sidebar/', **HTMX).status_code == 404


class TestOwnership:
    @pytest.fixture
    def theirs(self, other_user):
        return save_filter(other_user, 'Theirs', FilterParams(q='x'))

    def test_another_users_filter_is_a_404_everywhere(self, web, theirs):
        assert web.get(reverse('notes:filter', args=[theirs.pk])).status_code == 404
        for name in ('rename', 'update', 'delete'):
            response = web.post(reverse(f'notes:filter-{name}', args=[theirs.pk]), {'name': 'x'})
            assert response.status_code == 404
        assert SavedFilter.objects.get(pk=theirs.pk).name == 'Theirs'

    def test_an_unknown_filter_is_a_404(self, web):
        assert web.get(reverse('notes:filter', args=[uuid4()])).status_code == 404

    def test_the_lists_only_show_the_users_own(self, web, funded_user, theirs):
        save_filter(funded_user, 'Mine', FilterParams())

        for url in (reverse('notes:filters'), reverse('notes:list')):
            html = text(web.get(url))
            assert 'Mine' in html and 'Theirs' not in html

    def test_reordering_cannot_touch_another_users_filters(self, web, theirs):
        before = theirs.position

        web.post(reverse('notes:filter-reorder'), {'ids[]': [str(theirs.pk)]}, **HTMX)

        assert SavedFilter.objects.get(pk=theirs.pk).position == before


class TestSidebar:
    def test_every_page_lists_the_filters_and_asks_for_their_counts(self, web, funded_user):
        create_note(funded_user, title='Pay rent')
        saved = save_filter(funded_user, 'Pending', FilterParams(shared='no'))
        note = funded_user.notes.get()

        for url in (
            reverse('notes:list'),
            reverse('notes:archived'),
            reverse('notes:trash'),
            reverse('notes:tags'),
            reverse('notes:note', args=[note.pk]),
        ):
            html = text(web.get(url))
            assert reverse('notes:filter', args=[saved.pk]) in html
            assert 'Pending' in html and f'id="side-count-{saved.pk}"' in html
            assert f'hx-get="{reverse("notes:filter-counts")}"' in html
            assert 'scope=all' not in html

    def test_rendering_a_page_runs_no_count_query(self, web, funded_user):
        for n in range(3):
            save_filter(funded_user, f'F{n}', FilterParams(q='rent'))

        with CaptureQueriesContext(connection) as queries:
            assert web.get(reverse('notes:list')).status_code == 200

        assert not [q for q in queries if 'REGEXP' in q['sql'].upper()]

    def test_the_filters_page_asks_once_and_for_everything(self, web, funded_user):
        save_filter(funded_user, 'Pending', FilterParams(shared='no'))

        html = text(web.get(reverse('notes:filters')))

        assert html.count(f'hx-get="{reverse("notes:filter-counts")}') == 1
        assert 'scope=all' in html

    def test_no_request_without_filters(self, web):
        assert 'filter-counts' not in text(web.get(reverse('notes:list')))
        assert 'filter-counts' not in text(web.get(reverse('notes:filters')))

    def test_a_heading_links_to_the_filters_page_even_with_none(self, web):
        html = web.get(reverse('notes:list')).content.decode()

        assert reverse('notes:filters') in html and 'No saved filters' in html

    def test_the_current_filter_is_marked(self, web, funded_user):
        saved = save_filter(funded_user, 'Pending', FilterParams(shared='no'))

        html = web.get(reverse('notes:filter', args=[saved.pk])).content.decode()

        # The sidebar's current entry: the Notes menu has none here.
        assert html.count('aria-current="page"') == 1

    def test_more_than_twenty(self, web, funded_user):
        for n in range(SIDEBAR_LIMIT + 2):
            save_filter(funded_user, f'Filter {n:02}', FilterParams())

        html = web.get(reverse('notes:list')).content.decode()

        assert 'and 2 more' in html and 'Filter 19' in html and 'Filter 20' not in html

    def test_a_filter_that_cannot_be_counted_still_lists(self, web, funded_user):
        saved = save_filter(funded_user, 'Odd', FilterParams(q='fine'))
        SavedFilter.objects.filter(pk=saved.pk).update(params={'q': '/(/'})

        response = web.get(reverse('notes:list'))

        assert response.status_code == 200 and 'Odd' in text(response)


class TestCountsEndpoint:
    url = '/notes/filters/counts/'

    def test_the_url(self):
        assert reverse('notes:filter-counts') == self.url

    def test_it_answers_every_count_out_of_band(self, web, funded_user):
        create_note(funded_user, title='Pay rent')
        create_note(funded_user, title='Pay bills')
        everything = save_filter(funded_user, 'Everything', FilterParams())
        rent = save_filter(funded_user, 'Rent', FilterParams(q='rent'))

        response = web.get(self.url, **HTMX)

        assert response.status_code == 200
        html = text(response)
        for pk, count in ((everything.pk, 2), (rent.pk, 1)):
            assert f'id="side-count-{pk}" hx-swap-oob="true"' in html
            assert re_count(html, f'side-count-{pk}').startswith(
                f'{count}<span class="sr-only"> note'
            )
        assert 'page-count' not in html

    def test_the_filters_page_also_gets_its_own_list(self, web, funded_user):
        create_note(funded_user, title='Pay rent')
        saved = save_filter(funded_user, 'Pay', FilterParams(q='pay'))

        html = text(web.get(self.url, {'scope': 'all'}, **HTMX))

        assert f'id="side-count-{saved.pk}"' in html
        assert f'id="page-count-{saved.pk}" hx-swap-oob="true"' in html
        assert '1 note<' in html

    def test_each_filter_counts_its_own_scope(self, web, funded_user):
        create_note(funded_user, title='a note')
        archive(create_note(funded_user, title='an archived note'))
        trash(create_note(funded_user, title='a trashed note'))
        live = save_filter(funded_user, 'Live', FilterParams(q='note'))
        kept = save_filter(funded_user, 'Kept', FilterParams(q='note', scope='archived'))
        both = save_filter(funded_user, 'Both', FilterParams(q='note', scope='all'))

        html = text(web.get(self.url, **HTMX))

        assert re_count(html, f'side-count-{live.pk}').startswith('1<span')
        assert re_count(html, f'side-count-{kept.pk}').startswith('1<span')
        assert re_count(html, f'side-count-{both.pk}').startswith('2<span')

    def test_the_sidebar_part_is_the_first_twenty(self, web, funded_user):
        for n in range(SIDEBAR_LIMIT + 2):
            save_filter(funded_user, f'Filter {n:02}', FilterParams())

        sidebar = text(web.get(self.url, **HTMX))
        everything = text(web.get(self.url, {'scope': 'all'}, **HTMX))

        assert sidebar.count('side-count-') == SIDEBAR_LIMIT and 'page-count' not in sidebar
        assert everything.count('side-count-') == SIDEBAR_LIMIT
        assert everything.count('page-count-') == SIDEBAR_LIMIT + 2

    def test_all_the_counts_cost_one_aggregate(self, web, funded_user):
        for n in range(5):
            save_filter(funded_user, f'F{n}', FilterParams(q=f'rent{n}'))

        with CaptureQueriesContext(connection) as queries:
            web.get(self.url, {'scope': 'all'}, **HTMX)

        assert len([q for q in queries if 'REGEXP' in q['sql'].upper()]) == 1

    def test_a_filter_that_cannot_run_shows_a_dash_and_the_rest_a_count(self, web, funded_user):
        create_note(funded_user, title='Pay rent')
        good = save_filter(funded_user, 'Good', FilterParams(q='rent'))
        odd = save_filter(funded_user, 'Odd', FilterParams(q='fine'))
        SavedFilter.objects.filter(pk=odd.pk).update(params={'q': '/(/'})

        html = text(web.get(self.url, **HTMX))

        assert re_count(html, f'side-count-{good.pk}').startswith('1<span')
        assert re_count(html, f'side-count-{odd.pk}').startswith('<span aria-hidden="true">–')

    def test_a_timeout_is_a_dash_everywhere(self, web, funded_user, monkeypatch):
        saved = [save_filter(funded_user, f'F{n}', FilterParams(q='rent')) for n in range(2)]
        fake = MagicMock()
        fake.vendor = 'postgresql'
        fake.cursor.return_value.__enter__.return_value.execute.side_effect = [
            None,
            None,
            OperationalError('canceling statement due to statement timeout'),
        ]
        monkeypatch.setattr(core_search, 'connection', fake)

        response = web.get(self.url, {'scope': 'all'}, **HTMX)

        assert response.status_code == 200
        html = text(response)
        for item in saved:
            assert re_count(html, f'side-count-{item.pk}').startswith('<span aria-hidden="true">–')
            assert re_count(html, f'page-count-{item.pk}').startswith('<span aria-hidden="true">–')

    def test_no_filters_nothing_to_do(self, web):
        assert web.get(self.url, **HTMX).status_code == 204

    def test_it_counts_the_requesters_filters_only(self, web, other_user, funded_user):
        make_note(other_user, title='rent')
        theirs = save_filter(other_user, 'Theirs', FilterParams(q='rent'))
        mine = save_filter(funded_user, 'Mine', FilterParams(q='rent'))

        html = text(web.get(self.url, {'scope': 'all'}, **HTMX))

        assert str(mine.pk) in html and str(theirs.pk) not in html
        assert re_count(html, f'side-count-{mine.pk}').startswith('0<span')

    def test_anonymous_visitors_go_to_the_login_page(self, client, user):
        save_filter(user, 'F', FilterParams())

        assert client.get(self.url).status_code == 302

    def test_only_get(self, web):
        assert web.post(self.url).status_code == 405


def re_count(html: str, slot_id: str) -> str:
    """The start of the content of the out-of-band span `slot_id` in `html`."""
    start = html.index('>', html.index(f'id="{slot_id}"')) + 1
    return html[start : start + 80]


class TestRateLimits:
    @pytest.fixture(autouse=True)
    def _limits(self, settings, ratelimit_clock):
        settings.RATELIMIT_ENABLE = True
        settings.RATELIMIT_RULES = {
            **settings.RATELIMIT_RULES,
            'NOTES_EDIT_USER': (2, 60),
            'NOTES_SEARCH_USER': (2, 60),
        }

    def test_saving_rename_update_delete_and_reorder_share_the_edit_budget(self, web, funded_user):
        saved = save_filter(funded_user, 'F', FilterParams())
        web.post(reverse('notes:filters'), {'name': 'A', 'q': 'a'}, **HTMX)
        web.post(reverse('notes:filter-rename', args=[saved.pk]), {'name': 'B'}, **HTMX)

        for response in (
            web.post(reverse('notes:filters'), {'name': 'C', 'q': 'a'}, **HTMX),
            web.post(reverse('notes:filter-rename', args=[saved.pk]), {'name': 'D'}, **HTMX),
            web.post(reverse('notes:filter-update', args=[saved.pk]), {'q': 'a'}, **HTMX),
            web.post(reverse('notes:filter-delete', args=[saved.pk]), **HTMX),
            web.post(reverse('notes:filter-reorder'), {'ids[]': [str(saved.pk)]}, **HTMX),
        ):
            assert response.status_code == 429
        assert SavedFilter.objects.get(pk=saved.pk).name == 'B'

    def test_a_reorder_spends_no_search_budget(self, web, funded_user):
        first = save_filter(funded_user, 'A', FilterParams(q='a'))
        second = save_filter(funded_user, 'B', FilterParams(q='b'))
        ids = [str(second.pk), str(first.pk)]
        for _ in range(2):  # the whole edit budget
            assert (
                web.post(reverse('notes:filter-reorder'), {'ids[]': ids}, **HTMX).status_code == 204
            )

        # Nothing was fetched, counted or searched for: the search budget is whole.
        for _ in range(2):
            assert web.get(reverse('notes:filter', args=[first.pk])).status_code == 200
        assert web.get(reverse('notes:filter', args=[first.pk])).status_code == 429
        assert [saved.name for saved in list_filters(funded_user)] == ['B', 'A']

    def test_a_filters_page_view_spends_the_search_budget(self, web, funded_user):
        saved = save_filter(funded_user, 'F', FilterParams(q='a'))
        for _ in range(2):
            assert web.get(reverse('notes:filter', args=[saved.pk])).status_code == 200

        assert web.get(reverse('notes:filter', args=[saved.pk])).status_code == 429

    def test_the_pages_themselves_spend_no_budget(self, web, funded_user):
        save_filter(funded_user, 'F', FilterParams(q='a'))
        for _ in range(5):
            assert web.get(reverse('notes:filters')).status_code == 200
            assert web.get(reverse('notes:list')).status_code == 200

    def test_the_counts_spend_the_search_budget_once_per_call(self, web, funded_user):
        for n in range(5):  # many filters, still one hit
            save_filter(funded_user, f'F{n}', FilterParams(q='a'))
        url = reverse('notes:filter-counts')
        for _ in range(2):
            html = text(web.get(url, **HTMX))
            assert 'aria-hidden="true">–' not in html

        over = web.get(url, **HTMX)

        # Over the limit nothing is computed: every count is a dash, not an error page.
        assert over.status_code == 200
        assert text(over).count('aria-hidden="true">–') == 5
        assert web.get(reverse('notes:search'), {'q': 'a'}).status_code == 429

    def test_a_search_leaves_less_for_the_counts(self, web, funded_user):
        save_filter(funded_user, 'F', FilterParams(q='a'))
        for _ in range(2):
            assert web.get(reverse('notes:search'), {'q': 'a'}).status_code == 200

        over = web.get(reverse('notes:filter-counts'), **HTMX)

        assert 'aria-hidden="true">–' in text(over)

    def test_other_users_have_their_own_budget(self, web):
        for _ in range(3):
            web.post(reverse('notes:filters'), {'name': f'n{_}', 'q': 'a'}, **HTMX)
        client = Client()
        client.force_login(make_user())

        response = client.post(reverse('notes:filters'), {'name': 'mine', 'q': 'a'}, **HTMX)

        assert response.status_code == 200


class TestAdmin:
    def test_the_changelist_is_read_only(self, client, user):
        from django.contrib.auth import get_user_model

        admin_user = get_user_model().objects.create_superuser(
            email='boss@example.com', password='x', name='Boss'
        )
        save_filter(user, 'Rent', FilterParams(q='rent'))
        client.force_login(admin_user)

        listing = client.get(reverse('admin:notes_savedfilter_changelist'))

        assert listing.status_code == 200 and 'Rent' in listing.content.decode()
        found = client.get(reverse('admin:notes_savedfilter_changelist'), {'q': user.email})
        assert 'Rent' in found.content.decode()
        assert client.get(reverse('admin:notes_savedfilter_add')).status_code == 403
        saved = SavedFilter.objects.get()
        detail = client.get(reverse('admin:notes_savedfilter_change', args=[saved.pk]))
        assert detail.status_code == 200
        assert (
            client.post(reverse('admin:notes_savedfilter_delete', args=[saved.pk])).status_code
            == 403
        )

    def test_deleting_a_user_with_filters_is_not_blocked(self, client, user):
        from django.contrib.auth import get_user_model

        admin_user = get_user_model().objects.create_superuser(
            email='boss@example.com', password='x', name='Boss'
        )
        save_filter(user, 'Rent', FilterParams())
        client.force_login(admin_user)

        response = client.get(reverse('admin:accounts_user_delete', args=[user.pk]))

        assert response.status_code == 200
        assert 'cannot be deleted' not in response.content.decode().lower()


def test_a_saved_filter_keeps_relative_dates_relative(funded_user, freeze_now):
    freeze_now(2026, 10, 28)
    make_note(funded_user, title='fresh', updated_at=utc(2026, 10, 27, 10))
    make_note(funded_user, title='stale', updated_at=utc(2026, 8, 1))
    saved = save_filter(funded_user, 'Recent', FilterParams(updated='last_7_days'))

    assert note_count(funded_user, stored_params(saved)) == 1
    freeze_now(2026, 11, 3)  # the fresh note is now 7 days old: out of "today and the 6 before"
    assert note_count(funded_user, stored_params(saved)) == 0
    freeze_now(2026, 11, 2)
    assert note_count(funded_user, stored_params(saved)) == 1


def test_a_custom_range_is_saved_as_dates(funded_user):
    saved = save_filter(
        funded_user,
        'Q4',
        FilterParams(
            updated='custom', updated_from=date(2026, 10, 1), updated_to=date(2026, 12, 31)
        ),
    )

    assert (
        saved.params['updated_from'] == '2026-10-01' and saved.params['updated_to'] == '2026-12-31'
    )
    assert stored_params(saved).updated_to == date(2026, 12, 31)
