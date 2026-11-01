"""The To-do suggestions of the E2E run #224 (TODO-F5 to TODO-F21, and the To-do parts of NA-F7
and NA-F9): editors that discard, chip contrast, rows that leave with their parent, empty states,
the blocked banner, the sidebar's copy of the filters, and the small copy and markup fixes."""

import re
from datetime import time, timedelta

import pytest
from django.contrib import admin
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from ..models import Reminder, ReminderDelivery, Task
from ..services import (
    complete_task,
    create_task,
    reminders,
    save_filter,
    trash_task,
)
from ..templatetags.todo_ui import _DARK_CARD, _contrast, dark_mode_color, label_chip
from .factories import make_label, make_task, make_user, utc

pytestmark = pytest.mark.django_db

HTMX = {'HTTP_HX_REQUEST': 'true'}


def text(response) -> str:
    return response.content.decode()


def without_templates(html: str) -> str:
    return re.sub(r'<template.*?</template>', '', html, flags=re.S)


class TestEditorsDiscard:
    """TODO-F5: Cancel and Escape put the saved text back."""

    def test_the_title_and_description_editors(self, web, funded_user):
        task = make_task(funded_user, title='Report', description='Saved text')

        page = text(web.get(reverse('todo:task', args=[task.number])))

        assert page.count("todoCancelEdit($el, '#task-msg')") == 2  # Escape in both forms
        assert page.count("todoCancelEdit($el.form, '#task-msg')") == 2  # and both Cancel buttons

    def test_the_label_and_filter_editors(self, web, funded_user):
        make_label(funded_user, 'Home')
        save_filter(funded_user, 'Urgent', {'priorities': [1]})

        labels = text(web.get(reverse('todo:labels')))
        filters = text(web.get(reverse('todo:filters')))

        assert "todoCancelEdit($el, '#label-msg')" in labels
        assert "todoCancelEdit($el.form, '#label-msg')" in labels
        assert "todoCancelEdit($el, '#filter-msg')" in filters
        assert "todoCancelEdit($el.form, '#filter-msg')" in filters


class TestLabelChipContrast:
    """TODO-F6."""

    def test_blue_is_lightened_until_it_reads_on_the_dark_card(self):
        light = dark_mode_color('#0000ff')

        assert light != '#0000ff'
        rgb = tuple(int(light[i : i + 2], 16) for i in (1, 3, 5))
        # The chip's background is the label's own colour at 20% over the card.
        background = tuple(0.8 * card + 0.2 * part for card, part in zip(_DARK_CARD, (0, 0, 255)))
        assert _contrast(rgb, background) >= 4.5

    def test_a_colour_that_already_reads_is_kept(self):
        assert dark_mode_color('#ffffff') == '#ffffff'
        assert dark_mode_color('#ffff99') == '#ffff99'

    def test_a_stray_value_is_left_alone(self):
        assert dark_mode_color('blue') == 'blue'

    def test_the_chip_carries_both_and_the_layout_applies_it_in_dark_mode(self, web, funded_user):
        label = make_label(funded_user, 'Home', color='#0000ff')

        chip = str(label_chip(label))

        assert 'background-color: #0000ff33; color: #0000ff; --chip-dark: #' in chip
        assert 'label-chip' in chip
        page = text(web.get(reverse('todo:labels')))
        assert '.dark .label-chip { color: var(--chip-dark) !important; }' in page


class TestRowsLeaveWithTheirParent:
    """TODO-F7: an action on a parent takes its subtasks' rows off the page too."""

    def test_restoring_a_parent_removes_its_subtask_rows_and_the_message(self, web, funded_user):
        parent = make_task(funded_user, title='Parent')
        first = make_task(funded_user, title='First', parent=parent)
        second = make_task(funded_user, title='Second', parent=parent)
        trash_task(parent)

        body = text(
            web.post(
                reverse('todo:restore', args=[parent.number]),
                {'view': 'trash', 'ctx': 'row'},
                **HTMX,
            )
        )

        assert f'<li id="task-{first.number}" hx-swap-oob="delete">' in body
        assert f'<li id="task-{second.number}" hx-swap-oob="delete">' in body
        assert 'id="todo-msg" hx-swap-oob="true"' in body

    def test_a_subtask_trashed_on_its_own_stays_listed(self, web, funded_user):
        parent = make_task(funded_user, title='Parent')
        alone = make_task(funded_user, title='Alone', parent=parent)
        trash_task(alone)
        Task.objects.filter(pk=parent.pk).update(deleted_at=timezone.now() + timedelta(minutes=1))

        body = text(
            web.post(
                reverse('todo:restore', args=[parent.number]),
                {'view': 'trash', 'ctx': 'row'},
                **HTMX,
            )
        )

        assert f'id="task-{alone.number}"' not in body

    def test_trashing_a_parent_removes_its_open_subtasks(self, web, funded_user):
        parent = make_task(funded_user, title='Parent')
        sub = make_task(funded_user, title='Sub', parent=parent)

        body = text(
            web.post(
                reverse('todo:trash-task', args=[parent.number]),
                {'view': 'all', 'ctx': 'row'},
                **HTMX,
            )
        )

        assert f'<li id="task-{sub.number}" hx-swap-oob="delete">' in body

    def test_deleting_a_parent_forever_removes_its_subtask_rows(self, web, funded_user):
        parent = make_task(funded_user, title='Parent')
        sub = make_task(funded_user, title='Sub', parent=parent)
        trash_task(parent)

        body = text(
            web.post(
                reverse('todo:delete', args=[parent.number]),
                {'view': 'trash', 'ctx': 'row'},
                **HTMX,
            )
        )

        assert f'<li id="task-{sub.number}" hx-swap-oob="delete">' in body


class TestEmptyStates:
    """TODO-F8: the lists carry what to show when their last row leaves."""

    @pytest.mark.parametrize('name', ['todo:completed', 'todo:trash', 'todo:all'])
    def test_the_main_lists(self, web, funded_user, name):
        make_task(funded_user, title='Plain')
        trash_task(make_task(funded_user, title='Gone'))
        complete_task(make_task(funded_user, title='Done'))

        page = text(web.get(reverse(name)))

        assert '<ul class="divide-y divide-gray-200 dark:divide-gray-700" data-empty-list>' in page
        assert '<template data-empty-state><li data-empty' in page

    def test_today_and_upcoming(self, web, funded_user, wednesday):
        make_task(funded_user, title='Late', due_date=wednesday - timedelta(days=2))

        today = text(web.get(reverse('todo:today')))
        upcoming = text(web.get(reverse('todo:upcoming')))

        assert 'data-vanish-when-empty' in today  # the Overdue section goes with its last row
        assert 'data-empty-list data-reorder-url' in today and 'Nothing due today' in today
        assert upcoming.count('<template data-empty-state>') == 14


class TestBlockedBanner:
    """TODO-F9."""

    def test_every_page_has_the_slot(self, web):
        page = text(web.get(reverse('todo:today')))

        assert '<div id="todo-blocked">' in page and 'Tasks are blocked' not in page

    def test_completing_the_task_that_blocks_shows_it_at_once(self, user, config, wednesday):
        daily = create_task(user, title='Daily', due_date=wednesday, rrule='FREQ=DAILY')  # free
        create_task(user, title='Second')  # free: the allowance is used, and there are no credits
        client = Client()
        client.force_login(user)

        row = client.post(
            reverse('todo:complete', args=[daily.number]), {'view': 'all', 'ctx': 'row'}, **HTMX
        )
        page = client.post(reverse('todo:complete', args=[daily.number]), {'ctx': 'page'}, **HTMX)

        assert 'id="todo-blocked" hx-swap-oob="true"' in text(row)
        assert 'Tasks are blocked.' in text(row)
        assert 'id="todo-blocked" hx-swap-oob="true"' in text(page)

    def test_an_ordinary_completion_adds_nothing(self, web, funded_user):
        task = make_task(funded_user, title='Plain')

        body = text(
            web.post(
                reverse('todo:complete', args=[task.number]), {'view': 'all', 'ctx': 'row'}, **HTMX
            )
        )

        assert 'todo-blocked' not in body


class TestSidebarFollowsTheFiltersPage:
    """TODO-F10."""

    def test_a_rename_refreshes_the_sidebar_out_of_band(self, web, funded_user):
        saved = save_filter(funded_user, 'Urgent', {'priorities': [1]})

        body = text(
            web.post(reverse('todo:filter-rename', args=[saved.pk]), {'name': 'Hot'}, **HTMX)
        )

        assert '<nav id="sidebar-filters" hx-swap-oob="true"' in body
        assert body.count('Hot') >= 2  # the list and the sidebar

    def test_the_sidebar_view_has_the_new_order_and_asks_for_counts(self, web, funded_user):
        first = save_filter(funded_user, 'First', {'priorities': [1]})
        second = save_filter(funded_user, 'Second', {'priorities': [2]})
        web.post(reverse('todo:filter-reorder'), {'ids[]': [str(second.pk), str(first.pk)]}, **HTMX)

        body = text(web.get(reverse('todo:filter-sidebar'), **HTMX))

        assert body.index('Second') < body.index('First')
        assert reverse('todo:filter-counts') in body and 'id="sidebar-filters"' in body

    def test_opened_directly_it_goes_to_the_page(self, web):
        assert web.get(reverse('todo:filter-sidebar')).status_code == 302

    def test_the_list_is_told_to_fetch_it_after_a_reorder(self, web, funded_user):
        save_filter(funded_user, 'Urgent', {'priorities': [1]})

        page = text(web.get(reverse('todo:filters')))

        assert f'data-after-reorder-url="{reverse("todo:filter-sidebar")}"' in page
        assert 'data-after-reorder-target="#sidebar-filters"' in page


class TestRepeatControlOnlyWhereItCanWork:
    """TODO-F11."""

    def details(self, web, task):
        return text(web.get(reverse('todo:task', args=[task.number])))

    def test_an_open_top_level_task_has_it(self, web, funded_user):
        task = make_task(funded_user, title='Open')

        assert 'x-data="repeatControl"' in self.details(web, task)

    def test_a_subtask_does_not(self, web, funded_user):
        parent = make_task(funded_user, title='Parent')
        sub = make_task(funded_user, title='Sub', parent=parent)

        assert 'repeatControl' not in self.details(web, sub)

    def test_a_completed_task_does_not(self, web, funded_user):
        task = make_task(funded_user, title='Done')
        complete_task(task)

        assert 'repeatControl' not in self.details(web, task)

    def test_the_quick_add_form_still_has_it(self, web):
        assert 'x-data="repeatControl"' in text(web.get(reverse('todo:today')))


class TestSmallerFixes:
    def test_the_label_edit_row_can_wrap(self, web, funded_user):
        make_label(funded_user, 'Office')

        page = text(web.get(reverse('todo:labels')))

        assert 'min-w-[9rem] flex-1' in page and 'min-w-[11rem] flex-[2]' in page

    def test_drag_handles_are_focusable_buttons_with_a_name(self, web, funded_user, wednesday):
        make_task(funded_user, title='Pay rent', due_date=wednesday)
        make_label(funded_user, 'Home')
        save_filter(funded_user, 'Urgent', {'priorities': [1]})

        for name, label in (
            ('todo:today', 'Reorder Pay rent'),
            ('todo:labels', 'Reorder Home'),
            ('todo:filters', 'Reorder Urgent'),
        ):
            page = text(web.get(reverse(name)))
            assert 'role="button" tabindex="0"' in page and f'aria-label="{label}:' in page

    def test_the_tab_title_follows_a_rename(self, web, funded_user):
        task = make_task(funded_user, title='Write report')

        page = text(web.get(reverse('todo:task', args=[task.number])))
        renamed = text(
            web.post(
                reverse('todo:edit', args=[task.number]),
                {'part': 'title', 'title': 'Write the report', 'ctx': 'page'},
                **HTMX,
            )
        )

        assert 'data-document-title="Write report - To-do - ZipTrigo"' in page
        assert 'data-document-title="Write the report - To-do - ZipTrigo"' in renamed

    def test_the_settings_title(self, web):
        assert '<title>Settings - To-do - ZipTrigo</title>' in text(
            web.get(reverse('todo:settings'))
        )

    def test_a_trashed_task_without_subtasks_has_no_subtasks_heading(self, web, funded_user):
        task = make_task(funded_user, title='Alone')
        trash_task(task)

        page = text(web.get(reverse('todo:task', args=[task.number])))

        assert 'id="subtasks-title"' not in page

    def test_an_open_task_keeps_the_heading_and_the_add_box(self, web, funded_user):
        task = make_task(funded_user, title='Open')

        assert 'id="subtasks-title"' in text(web.get(reverse('todo:task', args=[task.number])))

    def test_the_sidebar_filters_label_is_not_a_heading(self, web, funded_user):
        save_filter(funded_user, 'Urgent', {'priorities': [1]})

        page = text(web.get(reverse('todo:today')))

        assert '<h2 class="px-3 pb-1 text-xs' not in page
        assert page.index('aria-label="Saved filters"') > 0
        assert page.count('<h1') == 1 and page.index('Filters</a>') < page.index('<h1')

    def test_the_phone_menus_say_they_scroll(self, web, funded_user):
        save_filter(funded_user, 'Urgent', {'priorities': [1]})

        page = text(web.get(reverse('todo:today')))

        assert page.count('<ul data-scroll-hint') == 2
        assert '[data-scroll-hint][data-more-right="true"]' in page

    def test_checklist_boxes_get_the_dark_native_look(self, web):
        assert '.dark input.task-list-item-checkbox { color-scheme: dark; }' in text(
            web.get(reverse('todo:today'))
        )


class TestAdminCopy:
    """TODO-F15."""

    def test_the_plural_is_spelled_out(self):
        assert str(ReminderDelivery._meta.verbose_name_plural) == 'reminder deliveries'  # ty: ignore[unresolved-attribute]

    def test_unpaid_is_text_for_the_exception_only(self, funded_user):
        from ..admin import TaskAdmin

        model_admin = TaskAdmin(Task, admin.site)
        paid = make_task(funded_user, title='Paid')
        owed = make_task(funded_user, title='Owed', unpaid_at=timezone.now())

        assert model_admin.unpaid(paid) == ''
        assert model_admin.unpaid(owed) == 'Unpaid'
        assert not getattr(model_admin.unpaid, 'boolean', False)


class TestSkippedHistoryRows:
    """TODO-F17: a reminder that was already due when it was created leaves no history row."""

    def test_a_default_reminder_of_a_task_made_after_its_time(self, funded_user, wednesday):
        # It is noon: the default reminder of a task due at 08:00 is already past when it is created.
        task = create_task(funded_user, title='Same day', due_date=wednesday, due_time=time(8, 0))

        reminders.deliver_due(utc(2026, 10, 28, 15, 0))

        [reminder] = Reminder.objects.filter(task=task)
        assert reminder.skipped and not ReminderDelivery.objects.exists()

    def test_one_that_was_ahead_and_missed_is_recorded(self, funded_user, wednesday):
        task = create_task(
            funded_user,
            title='Later',
            due_date=wednesday,
            due_time=time(14, 30),
        )

        reminders.deliver_due(utc(2026, 10, 28, 16, 0))  # the job was down for over an hour

        [delivery] = ReminderDelivery.objects.all()
        assert delivery.note == 'skipped' and delivery.task_id == task.pk

    def test_one_the_user_moves_into_the_past_by_editing_is_recorded(self, funded_user, wednesday):
        task = create_task(funded_user, title='Edit', due_date=wednesday, due_time=time(14, 30))
        reminder = Reminder.objects.get(task=task)

        reminders.update_reminder(reminder, minutes_before=3 * 1440)

        [delivery] = ReminderDelivery.objects.all()
        assert delivery.note == 'skipped'


class TestDeletedLabelNotice:
    """TODO-F19."""

    def test_the_filter_page_says_what_changed(self, web, funded_user):
        label = make_label(funded_user, 'Home')
        saved = save_filter(funded_user, 'Home stuff', {'labels': [str(label.pk)]})
        label.delete()

        page = text(web.get(reverse('todo:filter', args=[saved.pk])))

        assert 'id="filter-dropped-labels"' in page
        assert 'A label this filter used has been deleted' in page
        assert 'this filter now selects every open task' in page

    def test_a_filter_with_other_criteria_gets_the_shorter_notice(self, web, funded_user):
        label = make_label(funded_user, 'Home')
        saved = save_filter(funded_user, 'Mixed', {'labels': [str(label.pk)], 'priorities': [1]})
        label.delete()

        page = text(web.get(reverse('todo:filter', args=[saved.pk])))

        assert 'id="filter-dropped-labels"' in page
        assert 'selects every open task' not in page

    def test_no_notice_when_nothing_was_lost(self, web, funded_user):
        label = make_label(funded_user, 'Home')
        saved = save_filter(funded_user, 'Fine', {'labels': [str(label.pk)]})

        assert 'filter-dropped-labels' not in text(web.get(reverse('todo:filter', args=[saved.pk])))

    def test_the_filters_list_flags_the_row(self, web, funded_user):
        label = make_label(funded_user, 'Home')
        save_filter(funded_user, 'Home stuff', {'labels': [str(label.pk)]})
        label.delete()

        page = text(web.get(reverse('todo:filters')))

        assert 'A label this filter used was deleted and is ignored.' in page


class TestOtherUsersAreUnaffected:
    def test_another_users_subtasks_are_not_swept_up(self, web, funded_user):
        stranger = make_user()
        theirs = make_task(stranger, title='Theirs', number=1000)
        theirs_sub = make_task(stranger, title='Theirs sub', number=1001, parent=theirs)
        mine = make_task(funded_user, title='Mine', number=1000)

        body = text(
            web.post(
                reverse('todo:trash-task', args=[mine.number]),
                {'view': 'all', 'ctx': 'row'},
                **HTMX,
            )
        )

        assert f'id="task-{theirs_sub.number}"' not in body
