"""The task page's details save themselves (no Save button): the markup the script relies on.

`static/todo/js/tasks.js` submits `form[data-autosave]` on every change and takes only some parts
of the answered panel (`hx-select-oob`): those parts must be in every panel, with these ids. The
behaviour itself is covered in a browser by `tests_e2e/test_todo_details_autosave.py`.
"""

import re
from datetime import date

import pytest
from django.urls import reverse

from ..services import complete_task, create_task
from .factories import make_label

HTMX = {'HTTP_HX_REQUEST': 'true'}
PARTS = ('task-msg', 'task-details-fields', 'task-repeat-line', 'task-reminders')


def _details_form(html: str) -> str:
    match = re.search(r'<form id="task-details-form".*?</form>', html, re.S)
    assert match, 'no details form'
    return match.group(0)


def _without_noscript(html: str) -> str:
    return re.sub(r'<noscript>.*?</noscript>', '', html, flags=re.S)


@pytest.mark.django_db
class TestDetailsForm:
    def test_saves_itself_with_no_save_button(self, web, funded_user):
        make_label(funded_user, 'Home')
        task = create_task(funded_user, title='T', due_date=date(2026, 11, 3))
        form = _details_form(web.get(reverse('todo:task', args=[task.number])).content.decode())

        assert 'data-autosave' in form
        assert 'hx-swap="none"' in form
        assert 'hx-sync="this:queue last"' in form
        # The only submit button is for a browser without JavaScript.
        assert 'type="submit"' not in _without_noscript(form)
        assert re.search(r'<noscript><button type="submit".*?>Save</button></noscript>', form, re.S)
        # The status line is a live region of its own, outside what a save swaps in.
        assert 'id="details-save-status"' in form and 'role="status"' in form
        assert re.search(r'<div id="task-details-fields"[^>]*data-reminder-count="\d+"', form)
        assert form.index('id="task-details-fields"') < form.index('id="details-save-status"')
        # Labels and repeat save when their popover closes.
        assert form.count('data-save-on-close') == 2
        assert form.count("'popover-closed'") == 2

    def test_a_completed_task_has_no_repeat_popover(self, web, funded_user):
        task = create_task(funded_user, title='T', due_date=date(2026, 11, 3))
        complete_task(task)
        form = _details_form(web.get(reverse('todo:task', args=[task.number])).content.decode())
        assert form.count('data-save-on-close') == 1  # labels only

    @pytest.mark.parametrize('rrule', ['', 'FREQ=WEEKLY'])
    def test_every_part_a_save_takes_is_in_the_answer(self, web, funded_user, rrule):
        task = create_task(funded_user, title='T', due_date=date(2026, 11, 3))
        url = reverse('todo:edit', args=[task.number])
        data = {'part': 'details', 'ctx': 'page', 'due_date': '2026-11-04', 'priority': '1'}
        if rrule:
            data |= {'rrule': rrule, 'recurrence_mode': 'fixed'}

        response = web.post(url, data, **HTMX)

        assert response.status_code == 200
        html = response.content.decode()
        for part in PARTS:
            assert html.count(f'id="{part}"') == 1, part

    def test_the_page_has_every_part_too(self, web, funded_user):
        task = create_task(funded_user, title='T')
        html = web.get(reverse('todo:task', args=[task.number])).content.decode()
        for part in PARTS:
            assert html.count(f'id="{part}"') == 1, part

    def test_the_quick_add_form_does_not_save_itself(self, web):
        html = web.get(reverse('todo:today')).content.decode()
        match = re.search(r'<form id="quick-add-form".*?</form>', html, re.S)
        assert match is not None
        quick = match.group(0)
        assert 'data-autosave' not in quick
        assert 'type="submit"' in quick
