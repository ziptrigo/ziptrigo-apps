"""The "Download files" dialog, the quick XLSX, the exports pages and the actions on them
(`views/exports.py`, issue #326), and the To-do block of the account page's App actions."""

import io
import re
import uuid
from datetime import date, timedelta

import pytest
from django.contrib.messages import get_messages
from django.test import Client
from django.urls import reverse
from django.utils import timezone
from openpyxl import load_workbook

from apps.billing.models import CreditTransaction
from apps.billing.services import get_balance, spend_credits
from apps.core.services import exports as engine

from ..models import ExportStatus, Project, TodoAccount, TodoExport
from ..services import exports as svc
from ..services.costs import cost_section
from .factories import created, make_attachment, make_comment, make_task, utc

pytestmark = [
    pytest.mark.django_db,
    pytest.mark.unit,
    # Two requests per fixed rate-limit window: a real clock crossing a boundary breaks them.
    pytest.mark.usefixtures('ratelimit_clock'),
]

HTMX = {'HTTP_HX_REQUEST': 'true'}
NEW = 'todo:export-new'


@pytest.fixture(autouse=True)
def price(config):
    config.export_price_per_file = 3
    config.save()
    return config


@pytest.fixture
def freeze(funded_user):
    from ..services import get_account

    get_account(funded_user)
    TodoAccount.objects.filter(pk=funded_user.pk).update(frozen_at=timezone.now())


@pytest.fixture
def three(funded_user, fake_storage):
    home = Project.objects.create(user=funded_user, name='Home')
    rent = make_task(funded_user, number=7001, title='Pay rent', project=home)
    notes = make_task(funded_user, number=7002, title='Call the bank')
    comment = make_comment(rent, 'proof attached')
    return [
        make_attachment(rent, fake_storage, data=b'rent-' * 5, filename='rent.pdf'),
        make_attachment(
            rent,
            fake_storage,
            data=b'proof-' * 5,
            filename='proof.png',
            comment=comment,
            for_comment=True,
        ),
        make_attachment(notes, fake_storage, data=b'notes-' * 5, filename='notes.txt'),
    ]


def flashed(response) -> list[str]:
    return [str(m) for m in get_messages(response.wsgi_request)]


def post_new(web, **data):
    body = {'confirm_cost': '9', **data}
    return web.post(reverse(NEW), body, **HTMX)


def build(export: TodoExport) -> None:
    while (number := svc.next_part_to_build(export)) is not None:
        TodoExport.objects.filter(pk=export.pk).update(lease_until=None)
        svc.build_part.call(str(export.pk), number)
        export.refresh_from_db()


@pytest.fixture
def ready(funded_user, three):
    export = svc.create_export(funded_user, None)
    build(export)
    export.refresh_from_db()
    return export


# -- The account page ---------------------------------------------------------------------------


class TestAccountActions:
    def block(self, web, **query) -> str:
        content = web.get(reverse('accounts:account'), query).content.decode()
        return content.split('data-app-actions="todo"')[1].split('data-app-actions="')[0]

    def test_the_account_page_has_the_todo_block(self, web, three):
        content = web.get(reverse('accounts:account')).content.decode()
        block = self.block(web)

        assert 'data-app-actions="todo"' in content
        assert 'Download your task files as zip files, or your tasks as a spreadsheet.' in block
        assert 'id="todo-export-files"' in block and 'Download files' in block
        assert f'hx-get="{reverse(NEW)}"' in block and 'hx-include="closest form"' in block
        assert 'hx-target="#todo-export-modal"' in block and 'id="todo-export-modal"' in block
        assert 'id="todo-export-xlsx"' in block and 'Export XLSX' in block
        assert f'action="{reverse("todo:export-xlsx")}"' in block
        assert 'Task created' in block and 'name="date_range"' in block
        assert 'Last 15 days' in block and 'Custom' in block
        assert 'name="date_from"' in block and 'name="date_to"' in block
        assert reverse('todo:exports') in block and 'Your exports' in block

    def test_it_sits_next_to_receipts_without_colliding(self, web, three):
        content = web.get(reverse('accounts:account')).content.decode()
        receipts = content.split('data-app-actions="receipts"')[1].split('data-app-actions="')[0]
        todo = self.block(web)

        assert 'Receipt date' in receipts and 'Task created' not in receipts
        assert 'Task created' in todo and 'Receipt date' not in todo
        ids = lambda html: set(re.findall(r'\sid="([^"]+)"', html))  # noqa: E731
        assert ids(receipts) and ids(todo)
        assert not ids(receipts) & ids(todo)
        assert all(i.startswith('todo-export') for i in ids(todo) if 'export' in i)
        assert 'id="export-modal"' in receipts and 'id="export-modal"' not in todo
        assert '#export-modal' not in todo and '#todo-export-modal' not in receipts

    def test_the_dialog_script_is_loaded_once_before_both_blocks(self, web):
        content = web.get(reverse('accounts:account')).content.decode()
        assert content.count('core/js/fragment_modal.js') == 1
        assert content.index('core/js/fragment_modal.js') < content.index(
            'data-app-actions="receipts"'
        )

    def test_any_date_is_preselected_and_the_custom_dates_are_disabled(self, web):
        block = self.block(web)
        assert '<option value="any" selected>' in block
        assert re.search(r'name="date_from"[^>]*\sdisabled(?=[\s>])', block, re.S)

    def test_a_range_in_the_query_is_preselected(self, web):
        block = self.block(web, todo_date_from='2026-01-01', todo_date_to='2026-02-28')
        assert '<option value="custom" selected>' in block
        assert 'value="2026-01-01"' in block and 'value="2026-02-28"' in block
        assert not re.search(r'name="date_from"[^>]*\sdisabled(?=[\s>])', block, re.S)
        assert '<option value="this_month" selected>' in self.block(
            web, todo_date_range='this_month'
        )

    def test_unprefixed_or_bad_values_are_ignored(self, web):
        block = self.block(web, date_range='this_month', receipts_date_range='this_year')
        assert '<option value="any" selected>' in block
        assert '<option value="any" selected>' in self.block(web, todo_date_range='nope')

    def test_receipts_range_in_the_query_does_not_touch_the_todo_block(self, web):
        content = web.get(reverse('accounts:account'), {'receipts_date_range': 'this_year'})
        todo = content.content.decode().split('data-app-actions="todo"')[1]
        assert '<option value="this_year" selected>' not in todo.split('data-app-actions="')[0]

    def test_viewing_the_account_page_creates_no_todo_account(self, web, user):
        web.get(reverse('accounts:account'))
        assert not TodoAccount.objects.filter(pk=user.pk).exists()

    def test_the_block_is_there_for_a_user_who_switched_the_app_off(self, web, user):
        user.hidden_apps = ['todo']
        user.save()
        assert 'data-app-actions="todo"' in web.get(reverse('accounts:account')).content.decode()


# -- The dialog ---------------------------------------------------------------------------------


class TestDialog:
    def test_states_the_files_parts_and_cost(self, web, three):
        response = web.get(reverse(NEW), **HTMX)
        content = response.content.decode()

        assert response.status_code == 200
        assert '3 files' in content and '1 zip file' in content
        assert 'of about 100.0\xa0MB each' in content
        assert 'This costs <strong class="font-semibold">9 credits</strong> (3 per file)' in content
        assert 'Your balance is 100' in content
        assert 'name="confirm_cost" value="9"' in content
        assert 'Download for 9 credits' in content
        assert 'tasks.xlsx' in content and '30 days' in content
        assert 'Files of tasks in the Trash and of deleted comments are not included' in content
        assert 'id="todo-export-modal-title"' in content and 'id="todo-export-msg"' in content
        assert 'x-data="fragmentModal"' in content and 'role="dialog"' in content

    def test_the_price_is_read_from_the_setting(self, web, three, price):
        price.export_price_per_file = 4
        price.save()
        content = web.get(reverse(NEW), **HTMX).content.decode()
        assert '12 credits' in content and '(4 per file)' in content

    def test_there_is_no_layout_choice(self, web, three):
        content = web.get(reverse(NEW), **HTMX).content.decode()
        assert 'name="layout"' not in content and 'Organize' not in content

    def test_a_free_export_says_so(self, web, three, price):
        price.export_price_per_file = 0
        price.save()
        content = web.get(reverse(NEW), **HTMX).content.decode()
        assert 'This is free.' in content and 'Download files' in content

    def test_too_few_credits_offers_a_top_up(self, web, funded_user, three):
        spend_credits(funded_user, 95, description='x', source='test')
        content = web.get(reverse(NEW), **HTMX).content.decode()
        assert 'Your balance is 5' in content and reverse('billing:credits-history') in content

    def test_nothing_to_download_points_at_the_xlsx(self, web):
        content = web.get(reverse(NEW), **HTMX).content.decode()
        assert 'No files in this range; use Export XLSX for the task list.' in content
        assert re.search(r'<button type="button" disabled', content)

    def test_a_task_with_no_file_is_nothing_to_download(self, web, funded_user):
        make_task(funded_user)
        content = web.get(reverse(NEW), **HTMX).content.decode()
        assert 'No files in this range' in content

    def test_a_frozen_account_cannot_start_one(self, web, three, freeze):
        content = web.get(reverse(NEW), **HTMX).content.decode()
        assert 'Storage is paused' in content and re.search(
            r'<button type="button" disabled', content
        )

    def test_opened_directly_it_goes_to_the_account_page(self, web):
        response = web.get(reverse(NEW), {'date_range': 'this_year'})
        assert response.status_code == 302
        assert response['Location'] == f'{reverse("accounts:account")}#apps'

    def test_a_custom_range_quotes_only_tasks_created_in_it(self, web, three):
        created(three[2].task, utc(2024, 5, 5))
        created(three[0].task, utc(2026, 5, 5))
        content = web.get(
            reverse(NEW),
            {'date_range': 'custom', 'date_from': '2026-01-01', 'date_to': '2026-12-31'},
            **HTMX,
        ).content.decode()
        assert '2 files' in content and 'name="confirm_cost" value="6"' in content
        assert 'Only tasks created in this range: Task created: Jan 1, 2026 to Dec 31, 2026' in (
            content
        )
        assert 'name="date_from" value="2026-01-01"' in content
        assert 'name="date_to" value="2026-12-31"' in content

    def test_a_preset_range_quotes_the_tasks_in_it(self, web, three, freeze_now):
        freeze_now(2026, 11, 16, 12)
        created(three[0].task, utc(2026, 11, 10))
        created(three[2].task, utc(2026, 1, 10))
        content = web.get(reverse(NEW), {'date_range': 'last_15_days'}, **HTMX).content.decode()
        assert '2 files' in content and 'name="date_range" value="last_15_days"' in content

    def test_any_date_quotes_everything_and_says_so(self, web, three):
        content = web.get(reverse(NEW), {'date_range': 'any'}, **HTMX).content.decode()
        assert '3 files' in content
        assert 'choose a date range first' in content

    def test_a_bad_range_falls_back_to_any_date(self, web, three):
        content = web.get(
            reverse(NEW), {'date_range': 'nope', 'date_from': 'garbage'}, **HTMX
        ).content.decode()
        assert '3 files' in content

    def test_it_needs_a_login(self, client):
        response = client.get(reverse(NEW), **HTMX)
        assert response.status_code == 200 and 'login' in response['HX-Redirect']
        assert client.get(reverse(NEW)).status_code == 302


# -- Confirming ---------------------------------------------------------------------------------


class TestConfirm:
    def test_charges_creates_and_goes_to_the_exports_page(self, web, funded_user, three):
        response = post_new(web)

        export = TodoExport.objects.get()
        assert response.status_code == 200
        assert response['HX-Redirect'] == reverse('todo:export-detail', args=[export.pk])
        assert get_balance(funded_user) == 91
        assert export.status == ExportStatus.BUILDING and export.charged_credits == 9
        assert any('preparing your zip files' in m for m in flashed(response))

    def test_without_htmx_it_redirects(self, web, three):
        response = web.post(reverse(NEW), {'confirm_cost': '9'})
        export = TodoExport.objects.get()
        assert response.status_code == 302
        assert response['Location'] == reverse('todo:export-detail', args=[export.pk])

    def test_the_range_in_the_form_is_what_is_exported(self, web, three):
        created(three[2].task, utc(2024, 5, 5))
        response = post_new(
            web, confirm_cost='6', date_range='custom', date_from='2026-01-01', date_to='2030-12-31'
        )
        assert response.status_code == 200
        export = TodoExport.objects.get()
        assert export.file_count == 2
        assert export.filters['summary'] == ['Task created: Jan 1, 2026 to Dec 31, 2030']
        assert export.filters['params'] == {'date_from': '2026-01-01', 'date_to': '2030-12-31'}

    def test_a_changed_cost_charges_nothing_and_asks_again(
        self, web, funded_user, three, fake_storage
    ):
        make_attachment(three[0].task, fake_storage, filename='late.png')

        response = post_new(web)

        content = response.content.decode()
        assert response.status_code == 422
        assert 'The cost changed. Please confirm the new total.' in content
        assert 'name="confirm_cost" value="12"' in content and 'Download for 12 credits' in content
        assert get_balance(funded_user) == 100 and not TodoExport.objects.exists()
        # confirming the new total works
        assert post_new(web, confirm_cost='12').status_code == 200
        assert get_balance(funded_user) == 88

    def test_the_form_is_swapped_in_place(self, web, three):
        content = post_new(web, confirm_cost='1').content.decode()
        assert '<form' in content and 'fragmentModal' not in content

    def test_not_enough_credits(self, web, funded_user, three):
        spend_credits(funded_user, 95, description='x', source='test')
        response = post_new(web)
        content = response.content.decode()
        assert response.status_code == 422
        assert 'enough credits' in content
        assert reverse('billing:credits-history') in content
        assert get_balance(funded_user) == 5 and not TodoExport.objects.exists()

    def test_a_frozen_account(self, web, funded_user, three, freeze):
        response = post_new(web)
        assert response.status_code == 422
        assert 'paused' in response.content.decode() and not TodoExport.objects.exists()
        assert reverse('billing:credits-history') in response.content.decode()

    def test_one_at_a_time(self, web, three):
        assert post_new(web).status_code == 200
        response = post_new(web)
        assert response.status_code == 422
        assert 'already being prepared' in response.content.decode()
        assert TodoExport.objects.count() == 1

    def test_nothing_to_export(self, web):
        response = post_new(web, confirm_cost='0')
        assert response.status_code == 422
        assert 'No files in this range; use Export XLSX for the task list.' in (
            response.content.decode()
        )

    @pytest.mark.parametrize('value', [None, '', 'abc', '-3'])
    def test_the_confirmed_cost_is_required(self, web, funded_user, three, value):
        body = {}
        if value is not None:
            body['confirm_cost'] = value
        response = web.post(reverse(NEW), body, **HTMX)
        assert response.status_code == 422 and get_balance(funded_user) == 100
        assert not TodoExport.objects.exists()

    def test_a_refusal_without_htmx_is_a_flash_and_the_account_page(self, web, three):
        response = web.post(reverse(NEW), {'confirm_cost': '1'})
        assert response.status_code == 302 and response['Location'] == (
            f'{reverse("accounts:account")}#apps'
        )
        assert 'cost changed' in ' '.join(flashed(response))

    def test_a_refusal_without_htmx_keeps_the_chosen_range(self, web, three):
        response = web.post(
            reverse(NEW),
            {'confirm_cost': '99', 'date_from': '2026-01-01', 'date_to': '2026-12-31'},
        )
        assert response.status_code == 302
        assert response['Location'] == (
            f'{reverse("accounts:account")}?todo_date_from=2026-01-01&todo_date_to=2026-12-31#apps'
        )

        preset = web.post(reverse(NEW), {'confirm_cost': '99', 'date_range': 'this_year'})
        assert preset['Location'] == (
            f'{reverse("accounts:account")}?todo_date_range=this_year#apps'
        )

    def test_get_and_post_only(self, web):
        assert web.put(reverse(NEW)).status_code == 405

    def test_it_needs_a_login(self, client):
        assert client.post(reverse(NEW), {}).status_code == 302

    def test_another_users_files_are_never_exported(self, web, other_user, three, fake_storage):
        make_attachment(make_task(other_user), fake_storage, filename='theirs.png')
        assert post_new(web).status_code == 200
        assert TodoExport.objects.get().file_count == 3


class TestRateLimit:
    @pytest.fixture(autouse=True)
    def limit(self, settings):
        settings.RATELIMIT_ENABLE = True
        settings.RATELIMIT_RULES = {
            **settings.RATELIMIT_RULES,
            'TODO_EXPORT_FILES_USER': (1, 3600),
            'TODO_EXPORT_DIALOG_USER': (1, 60),
        }

    def test_the_second_export_in_the_window_is_limited(self, web, three):
        first = post_new(web)
        TodoExport.objects.update(status=ExportStatus.READY)
        second = post_new(web)

        assert first.status_code == 200
        assert second.status_code == 429 and second['Retry-After']
        assert second['HX-Retarget'] == '#todo-export-msg'
        assert TodoExport.objects.count() == 1

    def test_a_refused_request_spends_none_of_the_export_budget(
        self, web, funded_user, three, settings
    ):
        settings.RATELIMIT_RULES = {
            **settings.RATELIMIT_RULES,
            'TODO_EXPORT_DIALOG_USER': (100, 60),
        }
        for _ in range(3):
            assert post_new(web, confirm_cost='1').status_code == 422  # the cost changed
        assert post_new(web).status_code == 200

    def test_a_refusal_that_read_the_files_spends_the_dialog_budget(self, web, three):
        assert post_new(web, confirm_cost='1').status_code == 422  # reads the files: one hit
        second = post_new(web, confirm_cost='1')

        assert second.status_code == 429 and second['HX-Retarget'] == '#todo-export-msg'
        assert not TodoExport.objects.exists()

    def test_over_the_files_limit_nothing_is_read_or_spent(
        self, web, funded_user, three, settings, monkeypatch
    ):
        from apps.core import ratelimit
        from apps.todo.views import exports as views

        settings.RATELIMIT_RULES = {
            **settings.RATELIMIT_RULES,
            'TODO_EXPORT_DIALOG_USER': (5, 60),
        }
        assert post_new(web).status_code == 200
        TodoExport.objects.update(status=ExportStatus.READY)
        reads: list[int] = []
        real = views.prepare_export
        monkeypatch.setattr(
            views, 'prepare_export', lambda *a, **k: reads.append(1) or real(*a, **k)
        )
        dialog_left = ratelimit.peek_user(funded_user, 'TODO_EXPORT_DIALOG_USER').remaining

        for data in ({}, {}, {'confirm_cost': '1'}):
            limited = post_new(web, **data)
            assert limited.status_code == 429 and limited['HX-Retarget'] == '#todo-export-msg'

        assert reads == []  # not one whole-list read
        assert ratelimit.peek_user(funded_user, 'TODO_EXPORT_DIALOG_USER').remaining == dialog_left
        assert TodoExport.objects.count() == 1

    def test_opening_the_dialog_is_limited(self, web, three):
        first = web.get(reverse(NEW), **HTMX)
        second = web.get(reverse(NEW), **HTMX)
        assert first.status_code == 200
        assert second.status_code == 429 and second['Retry-After']
        assert 'This costs' not in second.content.decode()

    def test_a_dialog_redirected_to_the_account_page_spends_none_of_it(self, web, three):
        assert web.get(reverse(NEW)).status_code == 302
        assert web.get(reverse(NEW)).status_code == 302
        assert web.get(reverse(NEW), **HTMX).status_code == 200

    def test_an_invalid_form_spends_none_of_it(self, web, three):
        assert post_new(web, confirm_cost='x').status_code == 422
        assert post_new(web).status_code == 200

    def test_the_quick_xlsx_has_its_own_limit(self, web, three, settings):
        settings.RATELIMIT_RULES = {
            **settings.RATELIMIT_RULES,
            'TODO_EXPORT_XLSX_USER': (1, 60),
        }
        assert web.get(reverse('todo:export-xlsx')).status_code == 200
        limited = web.get(reverse('todo:export-xlsx'))
        assert limited.status_code == 429 and limited['Retry-After']


class TestReadsTheFilesOnce:
    @pytest.fixture
    def reads(self, monkeypatch):
        calls = []
        real = svc.select_rows
        monkeypatch.setattr(svc, 'select_rows', lambda *a, **k: calls.append(1) or real(*a, **k))
        return calls

    def test_the_dialog(self, web, three, reads):
        web.get(reverse(NEW), **HTMX)
        assert len(reads) == 1

    def test_a_confirm(self, web, three, reads):
        assert post_new(web).status_code == 200
        assert len(reads) == 1

    def test_a_refusal_with_a_changed_cost(self, web, three, reads, fake_storage):
        make_attachment(three[0].task, fake_storage, filename='late.png')
        assert post_new(web).status_code == 422
        assert len(reads) == 1

    def test_a_refusal_for_too_few_credits(self, web, funded_user, three, reads):
        spend_credits(funded_user, 95, description='x', source='test')
        assert post_new(web).status_code == 422
        assert len(reads) == 1


# -- The quick XLSX -----------------------------------------------------------------------------


class TestXlsx:
    URL = 'todo:export-xlsx'

    def open(self, response):
        return load_workbook(io.BytesIO(b''.join(response.streaming_content)))

    def test_downloads_the_workbook(self, web, three, freeze_now):
        freeze_now(2026, 11, 16, 12)
        response = web.get(reverse(self.URL))

        assert response.status_code == 200
        assert response['Content-Type'] == (
            'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        )
        assert response['Content-Disposition'] == 'attachment; filename="tasks-2026-11-16.xlsx"'
        assert response['Cache-Control'] == 'private, no-store'
        workbook = self.open(response)
        assert workbook.sheetnames == ['Tasks', 'Comments']
        tasks = workbook['Tasks']
        assert [row[0].value for row in tasks.iter_rows(min_row=2)] == [7001, 7002]

    def test_the_files_columns_hold_file_names_not_zip_paths(self, web, three):
        workbook = self.open(web.get(reverse(self.URL)))
        by_number = {row[0].value: row[15].value for row in workbook['Tasks'].iter_rows(min_row=2)}
        assert by_number == {7001: 'rent.pdf', 7002: 'notes.txt'}
        [comment] = list(workbook['Comments'].iter_rows(min_row=2))
        assert comment[4].value == 'proof attached' and comment[5].value == 'proof.png'

    def test_a_task_with_no_files_is_in_it(self, web, funded_user, three):
        make_task(funded_user, number=7003, title='Nothing attached')
        workbook = self.open(web.get(reverse(self.URL)))
        assert [row[0].value for row in workbook['Tasks'].iter_rows(min_row=2)] == [
            7001,
            7002,
            7003,
        ]

    def test_it_is_free_and_stores_nothing(self, web, funded_user, three, fake_storage):
        before = dict(fake_storage.objects)
        web.get(reverse(self.URL))
        assert get_balance(funded_user) == 100 and not TodoExport.objects.exists()
        assert fake_storage.objects == before
        assert not CreditTransaction.objects.filter(description__icontains='export').exists()

    def test_it_works_with_no_files_at_all_and_when_storage_is_paused(
        self, web, funded_user, freeze
    ):
        make_task(funded_user, number=7001)
        response = web.get(reverse(self.URL))
        assert response.status_code == 200
        assert [row[0].value for row in self.open(response)['Tasks'].iter_rows(min_row=2)] == [7001]

    def test_the_range_narrows_it(self, web, three):
        created(three[2].task, utc(2024, 5, 5))
        response = web.get(
            reverse(self.URL),
            {'date_range': 'custom', 'date_from': '2026-01-01', 'date_to': '2030-12-31'},
        )
        assert [row[0].value for row in self.open(response)['Tasks'].iter_rows(min_row=2)] == [7001]

    def test_a_bad_range_means_every_task(self, web, three):
        response = web.get(reverse(self.URL), {'date_range': 'nope', 'date_from': 'x'})
        assert len(list(self.open(response)['Tasks'].iter_rows(min_row=2))) == 2

    def test_another_users_tasks_are_not_in_it(self, web, other_user):
        make_task(other_user, number=7009, title='Theirs')
        assert list(self.open(web.get(reverse(self.URL)))['Tasks'].iter_rows(min_row=2)) == []

    def test_a_title_that_looks_like_a_formula_is_neutralised(self, web, funded_user):
        make_task(funded_user, title='=HYPERLINK("http://evil","x")')
        cell = self.open(web.get(reverse(self.URL)))['Tasks']['B2']
        # Kept as typed (no visible apostrophe), a string cell with the quote-prefix style.
        assert cell.data_type == 's' and cell.value == '=HYPERLINK("http://evil","x")'
        assert cell.quotePrefix is True

    def test_the_temporary_file_is_closed_when_the_build_fails(self, web, monkeypatch):
        from ..views import exports as views

        targets = []

        def boom(user, chosen, target):
            targets.append(target)
            raise RuntimeError('boom')

        monkeypatch.setattr(views, 'build_xlsx', boom)
        with pytest.raises(RuntimeError):
            web.get(reverse(self.URL))
        [target] = targets
        assert target.closed

    def test_it_needs_a_login_and_get_only(self, client, web):
        assert client.get(reverse(self.URL)).status_code == 302
        assert web.post(reverse(self.URL)).status_code == 405


# -- The pages ----------------------------------------------------------------------------------


class TestPages:
    def test_the_list(self, web, ready, funded_user):
        deleted = svc.create_export(funded_user, None)
        TodoExport.objects.filter(pk=deleted.pk).update(status=ExportStatus.DELETED)

        content = web.get(reverse('todo:exports')).content.decode()

        assert reverse('todo:export-detail', args=[ready.pk]) in content
        assert reverse('todo:export-detail', args=[deleted.pk]) not in content
        assert '3 files' in content and 'Ready' in content and 'until' in content
        assert 'Exports - To-do - ZipTrigo' in content

    def test_an_empty_list(self, web):
        content = web.get(reverse('todo:exports')).content.decode()
        assert 'No exports yet' in content

    def test_a_ready_export_has_a_download_button_per_part(self, web, ready):
        content = web.get(reverse('todo:export-detail', args=[ready.pk])).content.decode()

        assert 'Ready' in content and 'Available to download until' in content
        assert reverse('todo:export-part-download', args=[ready.pk, 1]) in content
        assert svc.part_name(ready, 1) in content and 'with tasks.xlsx' in content
        assert 'hx-get' not in content.split('id="todo-export-panel"')[1].split('>')[0]
        assert reverse('todo:export-delete', args=[ready.pk]) in content

    def test_a_building_export_polls_and_cannot_be_deleted(self, web, funded_user, three):
        export = svc.create_export(funded_user, None)

        content = web.get(reverse('todo:export-detail', args=[export.pk])).content.decode()

        assert 'Preparing your zip files: 0 of 1 done' in content
        assert f'hx-get="{reverse("todo:export-panel", args=[export.pk])}?polls=1"' in content
        assert reverse('todo:export-delete', args=[export.pk]) not in content
        assert reverse('todo:export-part-download', args=[export.pk, 1]) not in content

    def test_polling_stops_after_a_while(self, web, funded_user, three):
        export = svc.create_export(funded_user, None)
        response = web.get(reverse('todo:export-panel', args=[export.pk]), {'polls': 40}, **HTMX)
        assert 'hx-get' not in response.content.decode().split('>')[1]
        assert 'Reload the page' in response.content.decode()

    def test_the_panel_swaps_to_the_buttons_when_ready(self, web, funded_user, three):
        export = svc.create_export(funded_user, None)
        build(export)
        content = web.get(reverse('todo:export-panel', args=[export.pk]), **HTMX).content.decode()
        assert reverse('todo:export-part-download', args=[export.pk, 1]) in content

    def test_the_panel_opened_directly_goes_to_the_page(self, web, ready):
        response = web.get(reverse('todo:export-panel', args=[ready.pk]))
        assert response.status_code == 302
        assert response['Location'] == reverse('todo:export-detail', args=[ready.pk])

    def test_the_page_shows_what_was_selected(self, web, funded_user, three):
        export = svc.create_export(
            funded_user,
            __import__('apps.todo.services.export_range', fromlist=['x']).ExportRange(
                'custom', date(2020, 1, 1), None
            ),
        )
        content = web.get(reverse('todo:export-detail', args=[export.pk])).content.decode()
        assert 'Only: Task created: from Jan 1, 2020.' in content

    def test_a_failed_export_says_the_credits_came_back(self, web, funded_user, three):
        export = svc.create_export(funded_user, None)
        svc.fail_export(export.pk, 'boom')
        content = web.get(reverse('todo:export-detail', args=[export.pk])).content.decode()
        assert "couldn't prepare" in content or 'couldn&#x27;t prepare' in content
        assert 'The 9 credits you paid were refunded.' in content and 'Failed' in content

    def test_an_expired_export_stays_listed_with_its_state(self, web, ready):
        engine.end(svc.KIND, ready, to=ExportStatus.EXPIRED)
        content = web.get(reverse('todo:export-detail', args=[ready.pk])).content.decode()
        assert 'Expired' in content and 'were deleted' in content
        assert reverse('todo:export-part-download', args=[ready.pk, 1]) not in content

    def test_skipped_files_are_explained(self, web, funded_user, three, fake_storage):
        from ..services.storage import attachment_key

        export = svc.create_export(funded_user, None)
        del fake_storage.objects[attachment_key(funded_user.pk, three[0].pk)]
        build(export)
        content = web.get(reverse('todo:export-detail', args=[export.pk])).content.decode()
        assert '1 file was no longer there and left out; 3 credits refunded' in content

    def test_the_settings_page_links_to_the_exports(self, web):
        content = web.get(reverse('todo:settings')).content.decode()
        assert reverse('todo:exports') in content and 'Your exports' in content

    def test_the_pages_keep_the_todo_layout(self, web, ready):
        content = web.get(reverse('todo:export-detail', args=[ready.pk])).content.decode()
        assert 'aria-label="To-do"' in content and 'id="todo-msg"' in content
        assert 'x-data="confirmModal"' in content and content.count('x-data="confirmModal"') == 1

    def test_everything_needs_a_login(self, client, ready):
        for name, args in (
            ('exports', []),
            ('export-detail', [ready.pk]),
            ('export-panel', [ready.pk]),
            ('export-part-download', [ready.pk, 1]),
        ):
            assert client.get(reverse(f'todo:{name}', args=args)).status_code == 302
        assert client.post(reverse('todo:export-delete', args=[ready.pk])).status_code == 302


class TestOtherUsers:
    @pytest.fixture
    def intruder(self, other_user):
        client = Client()
        client.force_login(other_user)
        return client

    def test_another_users_export_is_a_404_everywhere(self, intruder, ready):
        for name, args in (
            ('export-detail', [ready.pk]),
            ('export-panel', [ready.pk]),
            ('export-part-download', [ready.pk, 1]),
        ):
            assert intruder.get(reverse(f'todo:{name}', args=args), **HTMX).status_code == 404
        assert intruder.post(reverse('todo:export-delete', args=[ready.pk])).status_code == 404
        ready.refresh_from_db()
        assert ready.status == ExportStatus.READY

    def test_an_unknown_one_is_a_404(self, web):
        assert web.get(reverse('todo:export-detail', args=[uuid.uuid4()])).status_code == 404

    def test_a_deleted_export_is_a_404(self, web, ready):
        svc.delete_export(ready)
        assert web.get(reverse('todo:export-detail', args=[ready.pk])).status_code == 404

    def test_the_list_shows_only_my_exports(self, intruder, ready):
        assert str(ready.pk) not in intruder.get(reverse('todo:exports')).content.decode()


# -- Downloading and deleting -------------------------------------------------------------------


class TestPartDownload:
    def url(self, export, number=1):
        return reverse('todo:export-part-download', args=[export.pk, number])

    def test_redirects_to_a_presigned_url(self, web, ready, fake_storage):
        response = web.get(self.url(ready))

        assert response.status_code == 302
        assert response['Location'].startswith('https://fake-s3.test/')
        assert 'part-01.zip' in response['Location']
        assert response['Cache-Control'] == 'private, no-store'
        assert fake_storage.get_urls[-1]['disposition'] == 'attachment'
        assert fake_storage.get_urls[-1]['filename'].startswith('task-files-')

    def test_a_frozen_account_is_sent_back_with_a_message(self, web, ready, freeze):
        response = web.get(self.url(ready))
        assert response.status_code == 302
        assert response['Location'] == reverse('todo:export-detail', args=[ready.pk])
        assert 'paused' in ' '.join(flashed(response))

    def test_an_export_that_is_not_ready_is_sent_back(self, web, funded_user, three):
        export = svc.create_export(funded_user, None)
        response = web.get(self.url(export))
        assert response.status_code == 302 and flashed(response)

    def test_an_unknown_part_is_sent_back(self, web, ready):
        response = web.get(self.url(ready, 7))
        assert response.status_code == 302 and 'no such file' in ' '.join(flashed(response)).lower()

    def test_it_is_rate_limited(self, web, ready, settings):
        settings.RATELIMIT_ENABLE = True
        settings.RATELIMIT_RULES = {
            **settings.RATELIMIT_RULES,
            'TODO_EXPORT_DOWNLOAD_USER': (1, 60),
        }
        assert web.get(self.url(ready)).status_code == 302
        limited = web.get(self.url(ready))
        assert limited.status_code == 429 and limited['Retry-After']

    def test_post_is_not_allowed(self, web, ready):
        assert web.post(self.url(ready)).status_code == 405


class TestDelete:
    def test_deletes_the_export_and_its_files(self, web, ready, fake_storage):
        key = ready.parts.get().key

        response = web.post(reverse('todo:export-delete', args=[ready.pk]))

        assert response.status_code == 302 and response['Location'] == reverse('todo:exports')
        ready.refresh_from_db()
        assert ready.status == ExportStatus.DELETED and key not in fake_storage.objects
        assert 'The export was deleted.' in flashed(response)

    def test_a_building_export_is_refused(self, web, funded_user, three):
        export = svc.create_export(funded_user, None)
        response = web.post(reverse('todo:export-delete', args=[export.pk]))
        assert response.status_code == 302
        assert 'still being prepared' in ' '.join(flashed(response))
        export.refresh_from_db()
        assert export.status == ExportStatus.BUILDING

    def test_get_is_not_allowed(self, web, ready):
        assert web.get(reverse('todo:export-delete', args=[ready.pk])).status_code == 405

    def test_no_refund(self, web, funded_user, ready):
        balance = get_balance(funded_user)
        web.post(reverse('todo:export-delete', args=[ready.pk]))
        assert get_balance(funded_user) == balance

    def test_expiry_makes_the_old_export_say_so(self, web, ready):
        TodoExport.objects.filter(pk=ready.pk).update(expires_at=timezone.now() - timedelta(days=1))
        svc.expire_exports()
        assert 'Expired' in web.get(reverse('todo:exports')).content.decode()


# -- Costs, admin -------------------------------------------------------------------------------


def download_line(user):
    [line] = [
        line
        for line in cost_section(user).lines
        if getattr(line, 'action', '') == 'Download all files as zip'
    ]
    return line


class TestCostsPage:
    def test_the_costs_section_lists_the_download_at_the_live_price(self, funded_user, price):
        line = download_line(funded_user)
        assert line.cost == '3 credits' and line.unit == 'per file'
        assert 'You confirm the cost first.' in line.note and '30 days' in line.note

        price.export_price_per_file = 8
        price.export_retention_days = 14
        price.save()
        line = download_line(funded_user)
        assert line.cost == '8 credits' and '14 days' in line.note

    def test_a_free_download_shows_as_free(self, funded_user, price):
        price.export_price_per_file = 0
        price.save()
        assert download_line(funded_user).free

    def test_the_costs_page_shows_it(self, web):
        content = web.get(reverse('billing:costs')).content.decode()
        assert 'Download all files as zip' in content
        assert 'You confirm the cost first.' in content


class TestAdmin:
    @pytest.fixture
    def admin_client(self):
        from apps.accounts.tests.factories import UserFactory

        client = Client()
        client.force_login(UserFactory(is_staff=True, is_superuser=True))
        return client

    def test_the_settings_page_labels_the_price_export_per_file(self, admin_client):
        response = admin_client.get(reverse('admin:todo_todosettings_changelist'))
        content = admin_client.get(response['Location']).content.decode()

        assert 'Export per file' in content
        assert 'name="export_price_per_file"' in content
        assert 'name="export_retention_days"' in content and 'name="export_part_bytes"' in content

    def test_the_exports_are_listed_read_only(self, admin_client, funded_user, three):
        export = svc.create_export(funded_user, None)

        listing = admin_client.get(reverse('admin:todo_todoexport_changelist'))
        detail = admin_client.get(reverse('admin:todo_todoexport_change', args=[export.pk]))

        assert listing.status_code == 200 and str(funded_user.email) in listing.content.decode()
        assert detail.status_code == 200
        assert 'part-01.zip' in detail.content.decode()
        assert b'name="_save"' not in detail.content
        assert admin_client.get(reverse('admin:todo_todoexport_add')).status_code == 403
        assert (
            admin_client.post(
                reverse('admin:todo_todoexport_delete', args=[export.pk]), {'post': 'yes'}
            ).status_code
            == 403
        )

    def test_filters_by_status(self, admin_client):
        response = admin_client.get(
            reverse('admin:todo_todoexport_changelist') + '?status__exact=ready'
        )
        assert response.status_code == 200

    def test_deleting_a_user_with_an_export_is_not_blocked(self, admin_client, funded_user, three):
        svc.create_export(funded_user, None)
        response = admin_client.get(reverse('admin:accounts_user_delete', args=[funded_user.pk]))
        assert response.status_code == 200 and 'Cannot delete' not in response.content.decode()


def test_the_rate_limit_rules_are_the_planned_ones():
    from django.conf import settings

    RATELIMIT_RULES = settings.RATELIMIT_RULES

    assert RATELIMIT_RULES['TODO_EXPORT_FILES_USER'] == (5, 60 * 60)
    assert RATELIMIT_RULES['TODO_EXPORT_DIALOG_USER'] == (30, 60)
    assert RATELIMIT_RULES['TODO_EXPORT_DOWNLOAD_USER'] == (60, 60)
    assert RATELIMIT_RULES['TODO_EXPORT_XLSX_USER'] == (10, 60)
