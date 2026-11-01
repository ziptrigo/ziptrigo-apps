"""Saved filters (issue #181): the model, the services (CRUD, the cap, uniqueness, reordering,
counts), the filters pages and endpoints, the sidebar, 404s and rate limits."""

import re
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

from ..models import SavedFilter
from ..services import (
    MAX_SAVED_FILTERS,
    FilterParams,
    add_comment,
    complete_task,
    create_task,
    delete_filter,
    list_filters,
    rename_filter,
    reorder_filters,
    save_filter,
    sidebar_filters,
    update_filter_params,
)
from ..services import search as search_module
from ..services.saved_filters import (
    SIDEBAR_LIMIT,
    filter_counts,
    filter_rows,
    open_task_count,
    open_task_counts,
    stored_params,
)
from .factories import make_label, make_task, make_user

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
            user=user, name='x', params={'q': ' rent ', 'priorities': [3, 1, 9], 'junk': 1}
        )

        assert saved.params == FilterParams(q='rent', priorities=(1, 3)).to_dict()
        assert SavedFilter.objects.get(pk=saved.pk).params == saved.params

    def test_clean_accepts_a_canonical_dict_and_refuses_the_rest(self, user):
        good = SavedFilter(user=user, name='x', params=FilterParams(q='a').to_dict())
        good.full_clean(exclude=['user'])

        bad = SavedFilter(user=user, name='x', params={'priorities': [7]})
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

    def test_the_user_relation(self, user):
        SavedFilter.objects.create(user=user, name='x')

        assert user.todo_saved_filters.count() == 1

    def test_deleting_the_user_deletes_the_filters(self, user):
        SavedFilter.objects.create(user=user, name='x')

        user.delete()

        assert not SavedFilter.objects.exists()


class TestSaveFilter:
    def test_it_saves_at_the_end_of_the_list(self, user):
        first = save_filter(user, 'First', FilterParams(q='a'))
        second = save_filter(user, 'Second', FilterParams(priorities=(1,)))

        assert names(user) == ['First', 'Second']
        assert first.position < second.position
        assert second.params['priorities'] == [1]

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

    def test_labels_that_are_not_the_users_are_dropped(self, user, other_user):
        mine = make_label(user, 'Mine')
        theirs = make_label(other_user, 'Theirs')

        saved = save_filter(user, 'L', FilterParams(labels=(str(mine.pk), str(theirs.pk), 'none')))

        assert saved.params['labels'] == sorted([str(mine.pk), 'none'])

    def test_it_creates_the_account_row_it_locks(self, user):
        from ..models import TodoAccount

        assert not TodoAccount.objects.filter(user=user).exists()

        save_filter(user, 'x', FilterParams())

        assert TodoAccount.objects.filter(user=user).exists()


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

        update_filter_params(saved, FilterParams(q='new', priorities=(2,)))

        assert stored_params(SavedFilter.objects.get(pk=saved.pk)) == FilterParams(
            q='new', priorities=(2,)
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
    def test_the_count_is_the_open_tasks_it_selects(self, funded_user):
        create_task(funded_user, title='Pay rent', priority=1)
        create_task(funded_user, title='Pay bills', priority=1)
        done = create_task(funded_user, title='Pay tax', priority=1)
        complete_task(done)

        assert open_task_count(funded_user, FilterParams(priorities=(1,))) == 2
        assert open_task_count(funded_user, FilterParams(q='pay', include_completed=True)) == 2
        assert open_task_count(funded_user, FilterParams(q='rent')) == 1

    def test_a_comment_match_counts_once(self, user):
        task = make_task(user, number=1001, title='Quiet')
        add_comment(task, user, 'rent one')
        add_comment(task, user, 'rent two')

        assert open_task_count(user, FilterParams(q='rent')) == 1

    def test_a_search_that_cannot_run_has_no_count(self, user):
        assert open_task_count(user, FilterParams(q='/(/')) is None

    def test_rows_carry_params_and_summary(self, funded_user):
        home = make_label(funded_user, 'home')
        create_task(funded_user, title='Pay rent', priority=1, labels=[home])
        saved = save_filter(
            funded_user, 'Rent', FilterParams(q='rent', priorities=(1,), labels=(str(home.pk),))
        )

        [row] = filter_rows(funded_user, [saved])

        assert row.summary == 'priority 1 · @home · "rent"'
        assert row.params.q == 'rent'
        assert filter_counts(funded_user, [saved]) == {saved.pk: 1}

    def test_a_deleted_label_drops_out_of_the_filter(self, funded_user):
        home = make_label(funded_user, 'home')
        create_task(funded_user, title='A', labels=[home])
        create_task(funded_user, title='B')
        saved = save_filter(funded_user, 'Home', FilterParams(labels=(str(home.pk),)))
        assert filter_counts(funded_user, [saved]) == {saved.pk: 1}

        home.delete()

        saved = SavedFilter.objects.get(pk=saved.pk)
        [row] = filter_rows(funded_user, [saved])
        assert row.params.labels == () and row.summary == 'all open tasks'
        assert filter_counts(funded_user, [saved]) == {saved.pk: 2}

    def test_all_the_counts_are_one_query(self, funded_user):
        home = make_label(funded_user, 'home')
        create_task(funded_user, title='Pay rent', priority=1, labels=[home])
        create_task(funded_user, title='Pay bills', priority=2)
        done = create_task(funded_user, title='Pay tax', priority=1)
        complete_task(done)
        add_comment(create_task(funded_user, title='Quiet'), funded_user, 'the rent')
        specs = [
            FilterParams(priorities=(1,)),
            FilterParams(q='pay*'),
            FilterParams(q='/rent/'),  # a title and a comment
            FilterParams(labels=(str(home.pk),)),
            FilterParams(labels=('none',)),
            FilterParams(date_range='no_date'),
            FilterParams(q='pay', include_completed=True),  # completed are never counted
            FilterParams(),
        ]
        saved = [save_filter(funded_user, f'F{n}', params) for n, params in enumerate(specs)]

        with CaptureQueriesContext(connection) as queries:
            counts = filter_counts(funded_user, saved)

        counting = [q for q in queries if 'COUNT(' in q['sql'].upper() or 'CASE WHEN' in q['sql']]
        assert len(counting) == 1
        assert len(queries) == 2  # the user's labels, then the one aggregate
        assert [counts[s.pk] for s in saved] == [1, 2, 2, 1, 2, 3, 2, 3]

    def test_a_filter_that_cannot_run_is_none_and_the_others_are_counted(self, funded_user):
        create_task(funded_user, title='Pay rent')

        counts = open_task_counts(
            funded_user, {'ok': FilterParams(q='rent'), 'bad': FilterParams(q='/(/')}
        )

        assert counts == {'ok': 1, 'bad': None}

    def test_nothing_to_count(self, funded_user):
        assert open_task_counts(funded_user, {}) == {}
        assert open_task_counts(funded_user, {'bad': FilterParams(q='/(/')}) == {'bad': None}

    def test_another_users_tasks_are_not_counted(self, funded_user, other_user):
        make_task(other_user, number=1, title='Pay rent', priority=1)

        assert open_task_count(funded_user, FilterParams(q='rent')) == 0

    @pytest.fixture
    def pg(self, monkeypatch):
        fake = MagicMock()
        fake.vendor = 'postgresql'
        cursor = MagicMock()
        fake.cursor.return_value.__enter__.return_value = cursor
        monkeypatch.setattr(search_module, 'connection', fake)
        return cursor

    @pytest.mark.parametrize(
        'error',
        [
            OperationalError('canceling statement due to statement timeout'),
            DataError('invalid regular expression: parentheses () not balanced'),
        ],
    )
    def test_a_timeout_makes_every_count_none(self, pg, funded_user, error):
        create_task(funded_user, title='Pay rent', priority=1)
        pg.execute.side_effect = [None, None, error]  # read the setting, set it, then the query

        counts = open_task_counts(
            funded_user, {'a': FilterParams(q='rent'), 'b': FilterParams(priorities=(1,))}
        )

        assert counts == {'a': None, 'b': None}

    def test_the_counts_run_under_the_one_second_timeout(self, pg, funded_user):
        create_task(funded_user, title='Pay rent')

        assert open_task_counts(funded_user, {'a': FilterParams(q='rent')}) == {'a': 1}

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
        for url in (
            reverse('todo:filters'),
            reverse('todo:filter', args=[saved.pk]),
        ):
            response = client.get(url)
            assert response.status_code == 302 and '/account/login' in response['Location']
        for name in ('rename', 'update', 'delete'):
            response = client.post(reverse(f'todo:filter-{name}', args=[saved.pk]))
            assert response.status_code == 302 and '/account/login' in response['Location']
        assert client.post(reverse('todo:filters'), {'name': 'x'}).status_code == 302
        assert client.post(reverse('todo:filter-reorder')).status_code == 302

    def test_empty(self, web):
        response = web.get(reverse('todo:filters'))

        assert response.status_code == 200 and 'No saved filters yet' in text(response)

    def test_the_list(self, web, funded_user):
        create_task(funded_user, title='Pay rent', priority=1)
        save_filter(funded_user, 'Rent', FilterParams(q='rent', priorities=(1,), sort='title'))

        html = text(web.get(reverse('todo:filters')))

        assert 'Rent' in html
        assert 'priority 1 · "rent" · by title' in html
        assert 'data-sortable' in html and reverse('todo:filter-reorder') in html
        # The count is not computed with the page: an empty slot and a request for it.
        assert '1 open task' not in html
        assert f'id="page-count-{SavedFilter.objects.get().pk}"' in html
        assert f'hx-get="{reverse("todo:filter-counts")}?scope=all"' in html

    def test_it_creates_the_account_on_the_first_visit(self, web, funded_user):
        from ..models import TodoAccount

        web.get(reverse('todo:filters'))

        assert TodoAccount.objects.filter(user=funded_user).exists()


class TestCreateEndpoint:
    def post(self, web, **data):
        return web.post(reverse('todo:filters'), data, **HTMX)

    def test_it_saves_the_posted_search_and_redirects_to_it(self, web, funded_user):
        response = self.post(
            web, name='Rent', q='rent*', mode='regex', priorities=['1', '2'], sort='title'
        )
        saved = SavedFilter.objects.get(user=funded_user)

        assert response.status_code == 200
        assert response['HX-Redirect'] == reverse('todo:filter', args=[saved.pk])
        assert stored_params(saved) == FilterParams(
            q='rent*', mode='regex', priorities=(1, 2), sort='title'
        )

    def test_a_flash_message_follows(self, web):
        self.post(web, name='Rent', q='rent')

        assert 'Saved the filter Rent.' in text(web.get(reverse('todo:filters')))

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
        response = web.post(reverse('todo:filters'), {'name': 'Rent', 'q': 'rent'})
        saved = SavedFilter.objects.get(user=funded_user)

        assert response.status_code == 302
        assert response['Location'] == reverse('todo:filter', args=[saved.pk])

    def test_a_refusal_without_htmx_goes_back_with_a_flash(self, web):
        response = web.post(reverse('todo:filters'), {'name': '', 'q': 'rent'})

        assert response.status_code == 302 and response['Location'] == reverse('todo:search')

    def test_foreign_labels_are_dropped(self, web, funded_user, other_user):
        theirs = make_label(other_user, 'Theirs')

        self.post(web, name='L', labels=[str(theirs.pk)], priorities='1')

        assert SavedFilter.objects.get(user=funded_user).params['labels'] == []


class TestFilterPage:
    @pytest.fixture
    def saved(self, funded_user):
        create_task(funded_user, title='Pay rent', priority=1)
        create_task(funded_user, title='Pay bills', priority=2)
        create_task(funded_user, title='Walk')
        return save_filter(funded_user, 'Payments', FilterParams(q='pay', priorities=(1,)))

    def test_it_runs_the_filter(self, web, saved):
        response = web.get(reverse('todo:filter', args=[saved.pk]))
        html = text(response)

        assert response.status_code == 200
        assert '<h1' in html and 'Payments' in html
        assert 'Pay rent' in html and 'Pay bills' not in html and 'Walk' not in html
        assert 'Update filter' not in html

    def test_the_form_is_filled_in_from_the_filter(self, web, saved):
        html = web.get(reverse('todo:filter', args=[saved.pk])).content.decode()

        assert 'value="pay"' in html
        assert f'name="filter" value="{saved.pk}"' in html

    def test_edited_params_in_the_query_string_show_update_filter(self, web, saved):
        response = web.get(reverse('todo:filter', args=[saved.pk]), {'q': 'pay', 'sort': 'date'})
        html = text(response)

        assert 'Pay rent' in html and 'Pay bills' in html
        assert 'Update filter' in html
        assert reverse('todo:filter-update', args=[saved.pk]) in html

    def test_the_same_params_in_the_query_string_are_not_an_edit(self, web, saved):
        response = web.get(
            reverse('todo:filter', args=[saved.pk]),
            {'q': 'pay', 'priorities': '1', 'sort': 'date'},
        )

        assert 'Update filter' not in text(response)

    def test_clearing_everything_is_an_edit_not_the_saved_filter(self, web, saved):
        response = web.get(reverse('todo:filter', args=[saved.pk]), {'sort': 'date'})
        html = text(response)

        assert 'Type something to search for' in html and 'Pay rent' not in html

    def test_the_list_partial_knows_the_filter(self, web, saved):
        response = web.get(
            reverse('todo:search-list'),
            {'filter': str(saved.pk), 'q': 'pay', 'sort': 'date'},
            **HTMX,
        )

        assert 'Update filter' in text(response)
        assert (
            response['HX-Push-Url'] == f'{reverse("todo:filter", args=[saved.pk])}?q=pay&sort=date'
        )

    def test_a_bad_filter_id_in_the_list_is_ignored(self, web, saved):
        for value in ('nope', str(uuid4())):
            response = web.get(reverse('todo:search-list'), {'filter': value, 'q': 'pay'}, **HTMX)
            assert response.status_code == 200 and 'Update filter' not in text(response)

    def test_another_users_filter_is_not_found_in_the_list(self, web, other_user):
        theirs = save_filter(other_user, 'Theirs', FilterParams(q='x'))

        response = web.get(
            reverse('todo:search-list'),
            {'filter': str(theirs.pk), 'q': 'pay', 'sort': 'title'},
            **HTMX,
        )

        assert 'Update filter' not in text(response)
        assert response['HX-Push-Url'] == '/todo/search/?q=pay&sort=title'

    def test_a_filter_whose_label_was_deleted_still_runs(self, web, funded_user):
        home = make_label(funded_user, 'Home')
        create_task(funded_user, title='Task', labels=[home])
        saved = save_filter(funded_user, 'Home', FilterParams(labels=(str(home.pk),)))
        home.delete()

        response = web.get(reverse('todo:filter', args=[saved.pk]))

        assert response.status_code == 200 and 'Task' in text(response)


class TestRenameUpdateDeleteReorderEndpoints:
    @pytest.fixture
    def saved(self, funded_user):
        return save_filter(funded_user, 'Old', FilterParams(q='old'))

    def test_rename_rerenders_the_list(self, web, saved):
        response = web.post(reverse('todo:filter-rename', args=[saved.pk]), {'name': 'New'}, **HTMX)

        assert response.status_code == 200 and 'New' in text(response)
        assert SavedFilter.objects.get(pk=saved.pk).name == 'New'
        assert '<html' not in response.content.decode()

    def test_rename_refusals(self, web, saved, funded_user):
        save_filter(funded_user, 'Taken', FilterParams())

        for value, line in (('', 'Name is required.'), ('taken', 'already have a filter')):
            response = web.post(
                reverse('todo:filter-rename', args=[saved.pk]), {'name': value}, **HTMX
            )
            assert response.status_code == 422 and response['HX-Retarget'] == '#filter-msg'
            assert line in text(response)
        assert SavedFilter.objects.get(pk=saved.pk).name == 'Old'

    def test_rename_without_htmx_redirects(self, web, saved):
        response = web.post(reverse('todo:filter-rename', args=[saved.pk]), {'name': 'New'})

        assert response.status_code == 302 and response['Location'] == reverse('todo:filters')

    def test_update_replaces_the_params_and_redirects_to_the_filter(self, web, saved):
        response = web.post(
            reverse('todo:filter-update', args=[saved.pk]),
            {'q': 'new', 'priorities': '3', 'sort': 'title'},
            **HTMX,
        )

        assert response['HX-Redirect'] == reverse('todo:filter', args=[saved.pk])
        assert stored_params(SavedFilter.objects.get(pk=saved.pk)) == FilterParams(
            q='new', priorities=(3,), sort='title'
        )
        assert 'Updated the filter Old.' in text(web.get(response['HX-Redirect']))

    def test_update_refuses_an_invalid_search(self, web, saved):
        response = web.post(reverse('todo:filter-update', args=[saved.pk]), {'q': '/(/'}, **HTMX)

        assert response.status_code == 422 and response['HX-Retarget'] == '#search-msg'
        assert stored_params(SavedFilter.objects.get(pk=saved.pk)).q == 'old'

    def test_delete(self, web, saved):
        response = web.post(reverse('todo:filter-delete', args=[saved.pk]), **HTMX)

        assert response['HX-Redirect'] == reverse('todo:filters')
        assert not SavedFilter.objects.exists()
        assert 'Deleted the filter Old.' in text(web.get(reverse('todo:filters')))

    def test_delete_without_htmx_redirects(self, web, saved):
        response = web.post(reverse('todo:filter-delete', args=[saved.pk]))

        assert response.status_code == 302 and response['Location'] == reverse('todo:filters')

    def test_reorder(self, web, funded_user, saved):
        second = save_filter(funded_user, 'Second', FilterParams())

        response = web.post(
            reverse('todo:filter-reorder'), {'ids[]': [str(second.pk), str(saved.pk)]}, **HTMX
        )

        assert response.status_code == 204 and names(funded_user) == ['Second', 'Old']

    def test_reorder_refuses_more_ids_than_a_user_can_have(self, web, saved):
        ids = [str(uuid4()) for _ in range(MAX_SAVED_FILTERS + 1)]

        with CaptureQueriesContext(connection) as queries:
            response = web.post(reverse('todo:filter-reorder'), {'ids[]': ids}, **HTMX)

        assert response.status_code == 422
        assert not [q for q in queries if 'todo_savedfilter' in q['sql']]
        at_the_limit = [str(saved.pk), *ids[: MAX_SAVED_FILTERS - 1]]
        response = web.post(reverse('todo:filter-reorder'), {'ids[]': at_the_limit}, **HTMX)
        assert response.status_code == 204

    def test_reordering_in_the_service_is_capped_too(self, user):
        with pytest.raises(ValidationError):
            reorder_filters(user, [uuid4() for _ in range(MAX_SAVED_FILTERS + 1)])

    def test_reorder_with_a_bad_id(self, web, saved):
        response = web.post(reverse('todo:filter-reorder'), {'ids[]': ['nope']}, **HTMX)

        assert response.status_code == 422 and response['HX-Retarget'] == '#filter-msg'

    def test_only_post(self, web, saved):
        for name in ('rename', 'update', 'delete'):
            assert web.get(reverse(f'todo:filter-{name}', args=[saved.pk])).status_code == 405
        assert web.get(reverse('todo:filter-reorder')).status_code == 405


class TestOwnership:
    @pytest.fixture
    def theirs(self, other_user):
        return save_filter(other_user, 'Theirs', FilterParams(q='x'))

    def test_another_users_filter_is_a_404_everywhere(self, web, theirs):
        assert web.get(reverse('todo:filter', args=[theirs.pk])).status_code == 404
        for name in ('rename', 'update', 'delete'):
            response = web.post(reverse(f'todo:filter-{name}', args=[theirs.pk]), {'name': 'x'})
            assert response.status_code == 404
        assert SavedFilter.objects.get(pk=theirs.pk).name == 'Theirs'

    def test_an_unknown_filter_is_a_404(self, web):
        assert web.get(reverse('todo:filter', args=[uuid4()])).status_code == 404

    def test_the_lists_only_show_the_users_own(self, web, funded_user, theirs):
        save_filter(funded_user, 'Mine', FilterParams())

        for url in (reverse('todo:filters'), reverse('todo:today')):
            html = text(web.get(url))
            assert 'Mine' in html and 'Theirs' not in html

    def test_reordering_cannot_touch_another_users_filters(self, web, theirs):
        before = theirs.position

        web.post(reverse('todo:filter-reorder'), {'ids[]': [str(theirs.pk)]}, **HTMX)

        assert SavedFilter.objects.get(pk=theirs.pk).position == before


class TestSidebar:
    def test_every_page_lists_the_filters_and_asks_for_their_counts(self, web, funded_user):
        create_task(funded_user, title='Pay rent', priority=1)
        saved = save_filter(funded_user, 'Urgent', FilterParams(priorities=(1,)))

        for name in ('todo:today', 'todo:all', 'todo:labels', 'todo:settings'):
            html = text(web.get(reverse(name)))
            assert reverse('todo:filter', args=[saved.pk]) in html
            visible = re.sub(r'<template.*?</template>', '', html, flags=re.S)  # empty states
            assert 'Urgent' in html and 'open task' not in visible
            assert f'id="side-count-{saved.pk}"' in html
            assert f'hx-get="{reverse("todo:filter-counts")}"' in html
            assert 'scope=all' not in html

    def test_rendering_a_page_runs_no_count_query(self, web, funded_user):
        for n in range(3):
            save_filter(funded_user, f'F{n}', FilterParams(q='rent'))

        with CaptureQueriesContext(connection) as queries:
            assert web.get(reverse('todo:today')).status_code == 200

        assert not [q for q in queries if 'REGEXP' in q['sql'].upper()]

    def test_the_filters_page_asks_once_and_for_everything(self, web, funded_user):
        save_filter(funded_user, 'Urgent', FilterParams(priorities=(1,)))

        html = text(web.get(reverse('todo:filters')))

        assert html.count(f'hx-get="{reverse("todo:filter-counts")}') == 1
        assert 'scope=all' in html

    def test_no_request_without_filters(self, web):
        assert 'filter-counts' not in text(web.get(reverse('todo:today')))
        assert 'filter-counts' not in text(web.get(reverse('todo:filters')))

    def test_a_heading_links_to_the_filters_page_even_with_none(self, web):
        html = web.get(reverse('todo:today')).content.decode()

        assert reverse('todo:filters') in html and 'No saved filters' in html

    def test_the_current_filter_is_marked(self, web, funded_user):
        saved = save_filter(funded_user, 'Urgent', FilterParams(priorities=(1,)))

        html = web.get(reverse('todo:filter', args=[saved.pk])).content.decode()

        assert html.count('aria-current="page"') == 1

    def test_more_than_twenty(self, web, funded_user):
        for n in range(SIDEBAR_LIMIT + 2):
            save_filter(funded_user, f'Filter {n:02}', FilterParams())

        html = web.get(reverse('todo:today')).content.decode()

        assert 'and 2 more' in html and 'Filter 19' in html and 'Filter 20' not in html

    def test_a_filter_that_cannot_be_counted_still_lists(self, web, funded_user):
        saved = save_filter(funded_user, 'Odd', FilterParams(q='fine'))
        SavedFilter.objects.filter(pk=saved.pk).update(params={'q': '/(/'})

        response = web.get(reverse('todo:today'))

        assert response.status_code == 200 and 'Odd' in text(response)


class TestCountsEndpoint:
    url = '/todo/filters/counts/'

    def test_the_url(self):
        assert reverse('todo:filter-counts') == self.url

    def test_it_answers_every_count_out_of_band(self, web, funded_user):
        create_task(funded_user, title='Pay rent', priority=1)
        create_task(funded_user, title='Pay bills', priority=1)
        urgent = save_filter(funded_user, 'Urgent', FilterParams(priorities=(1,)))
        rent = save_filter(funded_user, 'Rent', FilterParams(q='rent'))

        response = web.get(self.url, **HTMX)

        assert response.status_code == 200
        html = text(response)
        for pk, count in ((urgent.pk, 2), (rent.pk, 1)):
            assert f'id="side-count-{pk}" hx-swap-oob="true"' in html
            assert re_count(html, f'side-count-{pk}').startswith(
                f'{count}<span class="sr-only"> open task'
            )
        assert 'page-count' not in html

    def test_the_filters_page_also_gets_its_own_list(self, web, funded_user):
        create_task(funded_user, title='Pay rent', priority=1)
        saved = save_filter(funded_user, 'Urgent', FilterParams(priorities=(1,)))

        html = text(web.get(self.url, {'scope': 'all'}, **HTMX))

        assert f'id="side-count-{saved.pk}"' in html
        assert f'id="page-count-{saved.pk}" hx-swap-oob="true"' in html
        assert '1 open task<' in html

    def test_it_counts_open_tasks_only(self, web, funded_user):
        done = create_task(funded_user, title='done', priority=1)
        create_task(funded_user, title='open', priority=1)
        complete_task(done)
        saved = save_filter(
            funded_user, 'Urgent', FilterParams(priorities=(1,), include_completed=True)
        )

        html = text(web.get(self.url, **HTMX))

        assert re_count(html, f'side-count-{saved.pk}').startswith('1<span')

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
        create_task(funded_user, title='Pay rent', priority=1)
        good = save_filter(funded_user, 'Good', FilterParams(priorities=(1,)))
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
        monkeypatch.setattr(search_module, 'connection', fake)

        response = web.get(self.url, {'scope': 'all'}, **HTMX)

        assert response.status_code == 200
        html = text(response)
        for item in saved:
            assert re_count(html, f'side-count-{item.pk}').startswith('<span aria-hidden="true">–')
            assert re_count(html, f'page-count-{item.pk}').startswith('<span aria-hidden="true">–')

    def test_no_filters_nothing_to_do(self, web):
        assert web.get(self.url, **HTMX).status_code == 204

    def test_it_counts_the_requesters_filters_only(self, web, other_user, funded_user):
        make_task(other_user, number=1, title='rent', priority=1)
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
            'TODO_EDIT_USER': (2, 60),
            'TODO_SEARCH_USER': (2, 60),
        }

    def test_saving_rename_update_delete_and_reorder_share_the_edit_budget(self, web, funded_user):
        saved = save_filter(funded_user, 'F', FilterParams())
        web.post(reverse('todo:filters'), {'name': 'A', 'q': 'a'}, **HTMX)
        web.post(reverse('todo:filter-rename', args=[saved.pk]), {'name': 'B'}, **HTMX)

        for response in (
            web.post(reverse('todo:filters'), {'name': 'C', 'q': 'a'}, **HTMX),
            web.post(reverse('todo:filter-rename', args=[saved.pk]), {'name': 'D'}, **HTMX),
            web.post(reverse('todo:filter-update', args=[saved.pk]), {'q': 'a'}, **HTMX),
            web.post(reverse('todo:filter-delete', args=[saved.pk]), **HTMX),
            web.post(reverse('todo:filter-reorder'), {'ids[]': [str(saved.pk)]}, **HTMX),
        ):
            assert response.status_code == 429
        assert SavedFilter.objects.get(pk=saved.pk).name == 'B'

    def test_a_filters_page_view_spends_the_search_budget(self, web, funded_user):
        saved = save_filter(funded_user, 'F', FilterParams(q='a'))
        for _ in range(2):
            assert web.get(reverse('todo:filter', args=[saved.pk])).status_code == 200

        assert web.get(reverse('todo:filter', args=[saved.pk])).status_code == 429

    def test_the_pages_themselves_spend_no_budget(self, web, funded_user):
        save_filter(funded_user, 'F', FilterParams(q='a'))
        for _ in range(5):
            assert web.get(reverse('todo:filters')).status_code == 200
            assert web.get(reverse('todo:today')).status_code == 200

    def test_the_counts_spend_the_search_budget_once_per_call(self, web, funded_user):
        for n in range(5):  # many filters, still one hit
            save_filter(funded_user, f'F{n}', FilterParams(q='a'))
        url = reverse('todo:filter-counts')
        for _ in range(2):
            html = text(web.get(url, **HTMX))
            assert 'aria-hidden="true">–' not in html

        over = web.get(url, **HTMX)

        # Over the limit nothing is computed: every count is a dash, not an error page.
        assert over.status_code == 200
        assert text(over).count('aria-hidden="true">–') == 5
        assert web.get(reverse('todo:search'), {'q': 'a'}).status_code == 429

    def test_a_search_leaves_less_for_the_counts(self, web, funded_user):
        save_filter(funded_user, 'F', FilterParams(q='a'))
        for _ in range(2):
            assert web.get(reverse('todo:search'), {'q': 'a'}).status_code == 200

        over = web.get(reverse('todo:filter-counts'), **HTMX)

        assert 'aria-hidden="true">–' in text(over)

    def test_other_users_have_their_own_budget(self, web, funded_user):
        for _ in range(3):
            web.post(reverse('todo:filters'), {'name': f'n{_}', 'q': 'a'}, **HTMX)
        other = make_user()
        client = Client()
        client.force_login(other)

        response = client.post(reverse('todo:filters'), {'name': 'mine', 'q': 'a'}, **HTMX)

        assert response.status_code == 200


class TestAdmin:
    def test_the_changelist_is_read_only(self, client, user):
        from django.contrib.auth import get_user_model

        admin_user = get_user_model().objects.create_superuser(
            email='boss@example.com', password='x', name='Boss'
        )
        save_filter(user, 'Rent', FilterParams(q='rent'))
        client.force_login(admin_user)

        listing = client.get(reverse('admin:todo_savedfilter_changelist'))

        assert listing.status_code == 200 and 'Rent' in listing.content.decode()
        assert client.get(reverse('admin:todo_savedfilter_add')).status_code == 403
        saved = SavedFilter.objects.get()
        detail = client.get(reverse('admin:todo_savedfilter_change', args=[saved.pk]))
        assert detail.status_code == 200
        assert (
            client.post(reverse('admin:todo_savedfilter_delete', args=[saved.pk])).status_code
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
    create_task(funded_user, title='soon', due_date=date(2026, 10, 30))
    create_task(funded_user, title='later', due_date=date(2026, 12, 30))
    saved = save_filter(funded_user, 'Soon', FilterParams(date_range='next_7_days'))

    assert open_task_count(funded_user, stored_params(saved)) == 1
    freeze_now(2026, 12, 25)
    assert open_task_count(funded_user, stored_params(saved)) == 1
    freeze_now(2027, 3, 1)
    assert open_task_count(funded_user, stored_params(saved)) == 0
