"""Things an action leaves stale on the page (E2E run #224): the parent's Complete question after
a subtask changed, the "Also stop repeating" dialog after a sibling occurrence left, a refusal
that outlives a later success, and the sidebar's scroll container."""

import pytest
from django.urls import reverse

from ..models import Task
from ..services import complete_task, create_task, save_filter, uncomplete_task
from .factories import make_task

pytestmark = pytest.mark.django_db

HTMX = {'HTTP_HX_REQUEST': 'true'}


def text(response) -> str:
    return response.content.decode()


class TestSidebarOverflow:
    def test_the_scroll_containers_are_positioned(self, web, funded_user):
        """An `sr-only` span (absolutely positioned) inside a scroll container that is not
        positioned is not clipped by it, so it widened the page on a phone."""
        save_filter(funded_user, 'Urgent', {'priorities': [1]})

        page = text(web.get(reverse('todo:today')))

        assert page.count('<ul class="relative flex gap-1 overflow-x-auto') == 2


class TestParentCompleteButton:
    def row(self, web, number, view='subtask'):
        return web.post(
            reverse('todo:complete', args=[number]), {'view': view, 'ctx': 'row'}, **HTMX
        )

    def test_completing_a_subtask_refreshes_the_question(self, web, funded_user):
        parent = make_task(funded_user, title='Report')
        first = make_task(funded_user, title='First', parent=parent)
        second = make_task(funded_user, title='Second', parent=parent)

        body = text(self.row(web, first.number))
        assert 'id="complete-action"' in body and 'hx-swap-oob="true"' in body
        assert 'Report has 1 open subtask.' in body

        body = text(self.row(web, second.number))
        assert 'id="complete-action"' in body
        assert 'open subtask' not in body
        assert f'hx-post="{reverse("todo:complete", args=[parent.number])}"' in body

    def test_an_ordinary_row_answer_has_no_parent_button(self, web, funded_user):
        task = make_task(funded_user, title='Plain')

        assert 'complete-action' not in text(self.row(web, task.number, view='all'))

    def test_the_panel_renders_the_button_once(self, web, funded_user):
        parent = make_task(funded_user, title='Report')
        make_task(funded_user, title='First', parent=parent)

        page = text(web.get(reverse('todo:task', args=[parent.number])))

        assert page.count('id="complete-action"') == 1
        assert 'hx-swap-oob' not in page.split('id="complete-action"')[1].split('>')[0]


class TestOtherOccurrences:
    def test_trashing_one_occurrence_refreshes_the_other_row(self, web, funded_user, wednesday):
        first = create_task(funded_user, title='Rent', due_date=wednesday, rrule='FREQ=DAILY')
        complete_task(first)
        uncomplete_task(first)  # two open occurrences: no dialog on either
        newer = Task.objects.get(
            recurrence=first.recurrence_id, completed_at__isnull=True, number__gt=first.number
        )
        older = first

        response = web.post(
            reverse('todo:trash-task', args=[older.number]), {'view': 'all', 'ctx': 'row'}, **HTMX
        )

        body = text(response)
        assert f'id="task-{newer.number}"' in body and 'hx-swap-oob="true"' in body
        assert 'data-checkbox-name="stop_recurrence"' in body

    def test_a_plain_task_adds_nothing(self, web, funded_user, wednesday):
        task = make_task(funded_user, title='Plain')

        response = web.post(
            reverse('todo:trash-task', args=[task.number]), {'view': 'all', 'ctx': 'row'}, **HTMX
        )

        assert '<li' not in text(response)


class TestRefusalsAreCleared:
    def test_adding_a_label_clears_the_message_slot(self, web):
        body = text(web.post(reverse('todo:labels'), {'name': 'Work'}, **HTMX))

        assert 'id="label-msg" hx-swap-oob="true"' in body

    def test_the_labels_page_has_one_message_slot(self, web):
        assert text(web.get(reverse('todo:labels'))).count('id="label-msg"') == 1

    def test_a_filter_rename_clears_the_message_slot(self, web, funded_user):
        saved = save_filter(funded_user, 'Urgent', {'priorities': [1]})

        body = text(
            web.post(reverse('todo:filter-rename', args=[saved.pk]), {'name': 'Hot'}, **HTMX)
        )

        assert 'id="filter-msg" hx-swap-oob="true"' in body

    def test_the_filters_page_has_one_message_slot(self, web):
        assert text(web.get(reverse('todo:filters'))).count('id="filter-msg"') == 1
