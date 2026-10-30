"""The To-do pages: every page renders, ownership, the 422 paths, rate limits, billing messages and
the htmx answers (rows, panel, list partial)."""

from datetime import date, time
from html import unescape

import pytest
from django.test import Client
from django.urls import reverse

from apps.billing.services import add_credits, get_balance

from ..models import Label, Task, TodoAccount
from ..services import complete_task, create_task, get_account, trash_task
from .factories import make_label, make_task, make_user

pytestmark = pytest.mark.django_db

HTMX = {'HTTP_HX_REQUEST': 'true'}

PAGES = [
    'todo:today',
    'todo:upcoming',
    'todo:all',
    'todo:completed',
    'todo:trash',
    'todo:labels',
    'todo:settings',
]


def text(response) -> str:
    return unescape(response.content.decode())


@pytest.fixture
def seeded(funded_user, wednesday):
    """A user with a bit of everything."""
    label = make_label(funded_user, 'Home', color='#336699', description='Around the house')
    parent = create_task(funded_user, title='Parent task', description='**bold** text', priority=1)
    create_task(funded_user, title='Child task', parent=parent)
    create_task(funded_user, title='Overdue task', due_date=date(2026, 10, 20))
    create_task(funded_user, title='Today task', due_date=wednesday, due_time=time(9, 30))
    create_task(funded_user, title='Tomorrow task', due_date=date(2026, 10, 29), labels=[label])
    done = create_task(funded_user, title='Done task')
    complete_task(done)
    gone = create_task(funded_user, title='Gone task')
    trash_task(gone)
    return funded_user


class TestPages:
    def test_anonymous_visitors_go_to_the_login_page(self, client, user):
        for name in PAGES:
            response = client.get(reverse(name))
            assert response.status_code == 302 and '/account/login' in response['Location']
        assert client.get(reverse('todo:task', args=[1000])).status_code == 302
        assert client.post(reverse('todo:create'), {'title': 'x'}).status_code == 302

    @pytest.mark.parametrize('name', PAGES)
    def test_every_page_renders_for_an_empty_account(self, web, name):
        response = web.get(reverse(name))

        assert response.status_code == 200
        assert 'To-do' in response.content.decode()

    @pytest.mark.parametrize('name', PAGES)
    def test_every_page_renders_for_a_seeded_account(self, seeded, name):
        client = Client()
        client.force_login(seeded)

        assert client.get(reverse(name)).status_code == 200

    def test_the_first_visit_creates_the_account(self, web, funded_user):
        assert not TodoAccount.objects.filter(user=funded_user).exists()

        web.get(reverse('todo:today'))

        assert TodoAccount.objects.filter(user=funded_user).exists()

    def test_today_lists_overdue_then_todays_tasks(self, seeded):
        client = Client()
        client.force_login(seeded)

        body = text(client.get(reverse('todo:today')))

        assert body.index('Overdue') < body.index('Overdue task') < body.index('Today task')
        assert 'Tomorrow task' not in body and 'Done task' not in body and 'Gone task' not in body
        assert 'Reschedule overdue to today' in body
        assert 'Add task' in body and 'name="title"' in body
        # The sidebar counts today and overdue.
        assert 'id="nav-today-count">2<' in body

    def test_upcoming_has_every_day_of_the_next_14(self, seeded):
        client = Client()
        client.force_login(seeded)

        body = text(client.get(reverse('todo:upcoming')))

        assert body.count('Nothing due') == 13
        assert 'Tomorrow, 29 Oct' in body and 'Tomorrow task' in body
        assert 'Later' in body

    def test_all_lists_the_open_tasks_and_sorts(self, seeded):
        client = Client()
        client.force_login(seeded)

        body = text(client.get(reverse('todo:all')))
        assert 'Parent task' in body and 'Child task' in body and 'Done task' not in body

        by_title = text(client.get(reverse('todo:all'), {'sort': 'title'}))
        assert by_title.index('Child task') < by_title.index('Overdue task')
        assert client.get(reverse('todo:all'), {'sort': 'bogus', 'page': 'x'}).status_code == 200

    def test_all_paginates_at_50(self, funded_user, config):
        config.free_tasks_per_month = 1000
        config.save()
        for number in range(55):
            make_task(funded_user, number=2000 + number, title=f'Task {number:02d}')
        client = Client()
        client.force_login(funded_user)

        first = text(client.get(reverse('todo:all')))
        second = text(client.get(reverse('todo:all'), {'page': 2}))

        assert 'Page 1 of 2' in first and 'Page 2 of 2' in second
        assert first.count('data-task-id=') == 50 and second.count('data-task-id=') == 5

    def test_completed_lists_newest_first_with_uncomplete(self, funded_user, config):
        one = create_task(funded_user, title='First done')
        two = create_task(funded_user, title='Second done')
        complete_task(one)
        complete_task(two)
        client = Client()
        client.force_login(funded_user)

        body = text(client.get(reverse('todo:completed')))

        assert body.index('Second done') < body.index('First done')
        assert 'title="Uncomplete"' in body

    def test_trash_shows_days_left_and_the_actions(self, seeded):
        client = Client()
        client.force_login(seeded)

        body = text(client.get(reverse('todo:trash')))

        assert 'Gone task' in body and 'deleted in 30 days' in body
        assert 'Restore' in body and 'Delete forever' in body
        assert 'Parent task' not in body

    def test_the_blocked_banner_and_free_line(self, user, config):
        for title in ('One', 'Two', 'Owed'):
            create_task(user, title=title, allow_unpaid=True)
        client = Client()
        client.force_login(user)

        body = text(client.get(reverse('todo:today')))

        assert 'Tasks are blocked.' in body
        assert reverse('billing:credits-history') in body

    def test_the_free_tasks_line_on_the_settings_page(self, funded_user):
        create_task(funded_user, title='One')
        client = Client()
        client.force_login(funded_user)

        body = text(client.get(reverse('todo:settings')))

        assert '1 of 2' in body and 'free tasks used this month' in body
        assert '5 credits' in body and reverse('accounts:account') in body

    def test_the_free_line_never_exceeds_the_allowance(self, funded_user):
        for title in ('One', 'Two', 'Three', 'Four'):
            create_task(funded_user, title=title)  # two free, two paid
        client = Client()
        client.force_login(funded_user)

        body = text(client.get(reverse('todo:settings')))

        assert '2 of 2' in body and '4 of 2' not in body


class TestListPartial:
    def test_it_answers_htmx_with_the_page_url_pushed(self, web):
        response = web.get(reverse('todo:list'), {'view': 'all', 'sort': 'title'}, **HTMX)

        assert response.status_code == 200
        assert response['HX-Push-Url'] == f'{reverse("todo:all")}?sort=title'
        assert 'id="task-list"' not in response.content.decode()  # the inside only

    def test_the_default_sort_and_first_page_push_the_bare_url(self, web):
        response = web.get(reverse('todo:list'), {'view': 'all'}, **HTMX)

        assert response['HX-Push-Url'] == reverse('todo:all')

    @pytest.mark.parametrize('view', ['today', 'upcoming', 'all', 'completed', 'trash'])
    def test_every_view(self, web, view):
        response = web.get(reverse('todo:list'), {'view': view}, **HTMX)

        assert response.status_code == 200
        assert response['HX-Push-Url'] == reverse(f'todo:{view}')

    def test_an_unknown_view_is_today(self, web):
        assert web.get(reverse('todo:list'), {'view': 'x'}, **HTMX)['HX-Push-Url'] == reverse(
            'todo:today'
        )

    def test_opened_directly_it_redirects_to_the_page(self, web):
        response = web.get(reverse('todo:list'), {'view': 'all', 'sort': 'title', 'page': 2})

        assert response.status_code == 302
        assert response['Location'] == f'{reverse("todo:all")}?sort=title&page=2'


class TestSortDirection:
    @pytest.fixture
    def tasks(self, web, funded_user):
        make_task(funded_user, number=1000, title='Banana', priority=2, due_date=date(2026, 11, 2))
        make_task(funded_user, number=1001, title='Apple', priority=1, due_date=date(2026, 11, 1))
        make_task(funded_user, number=1002, title='Cherry', priority=0)

    def order(self, web, **params) -> list[int]:
        response = web.get(reverse('todo:list'), {'view': 'all', **params}, **HTMX)
        return [task.number for task in response.context['tasks']]

    @pytest.mark.parametrize(
        ('params', 'expected'),
        [
            ({}, [1001, 1000, 1002]),
            ({'dir': 'desc'}, [1002, 1000, 1001]),
            ({'sort': 'title'}, [1001, 1000, 1002]),
            ({'sort': 'title', 'dir': 'desc'}, [1002, 1000, 1001]),
            ({'sort': 'priority'}, [1001, 1000, 1002]),
            ({'sort': 'priority', 'dir': 'desc'}, [1002, 1000, 1001]),
            ({'sort': 'created'}, [1002, 1001, 1000]),
            ({'sort': 'created', 'dir': 'asc'}, [1000, 1001, 1002]),
            ({'sort': 'title', 'dir': 'sideways'}, [1001, 1000, 1002]),
        ],
    )
    def test_the_order(self, web, tasks, params, expected):
        assert self.order(web, **params) == expected

    def test_the_active_sort_links_to_the_opposite_direction_and_shows_an_arrow(self, web, tasks):
        html = (
            web.get(reverse('todo:list'), {'view': 'all', 'sort': 'title'}, **HTMX)
            .content.decode()
            .replace('&amp;', '&')
        )

        assert 'sort=title&dir=desc' in html
        assert 'fa-arrow-up' in html
        assert 'sorted ascending' in html
        # the other sorts keep asking for their own default direction
        assert 'sort=priority"' in html and 'sort=created"' in html

    def test_a_descending_sort_links_back_and_shows_a_down_arrow(self, web, tasks):
        response = web.get(
            reverse('todo:list'), {'view': 'all', 'sort': 'title', 'dir': 'desc'}, **HTMX
        )
        html = response.content.decode().replace('&amp;', '&')

        assert 'fa-arrow-down' in html
        assert 'sorted descending' in html
        assert 'hx-get="/todo/list/?view=all&sort=title"' in html
        assert response['HX-Push-Url'] == f'{reverse("todo:all")}?sort=title&dir=desc'

    def test_the_default_sort_reverses_with_just_dir(self, web, tasks):
        response = web.get(reverse('todo:list'), {'view': 'all', 'dir': 'desc'}, **HTMX)

        assert response['HX-Push-Url'] == f'{reverse("todo:all")}?dir=desc'
        assert 'fa-arrow-down' in response.content.decode().replace('&amp;', '&')

    def test_newest_is_descending_by_default(self, web, tasks):
        html = (
            web.get(reverse('todo:list'), {'view': 'all', 'sort': 'created'}, **HTMX)
            .content.decode()
            .replace('&amp;', '&')
        )

        assert 'fa-arrow-down' in html
        assert 'sort=created&dir=asc' in html

    def test_opened_directly_it_keeps_the_direction(self, web):
        response = web.get(reverse('todo:list'), {'view': 'all', 'sort': 'title', 'dir': 'desc'})

        assert response['Location'] == f'{reverse("todo:all")}?sort=title&dir=desc'

    def test_pagination_keeps_the_direction(self, web, funded_user):
        for number in range(1000, 1060):
            make_task(funded_user, number=number, title=f'Task {number}')

        response = web.get(
            reverse('todo:list'), {'view': 'all', 'sort': 'title', 'dir': 'desc'}, **HTMX
        )

        assert 'sort=title&dir=desc&page=2' in response.content.decode().replace('&amp;', '&')


class TestOwnership:
    @pytest.mark.parametrize(
        'name',
        [
            'todo:task',
            'todo:edit',
            'todo:complete',
            'todo:uncomplete',
            'todo:postpone',
            'todo:trash-task',
            'todo:restore',
            'todo:delete',
            'todo:checklist',
            'todo:subtask-add',
        ],
    )
    def test_another_users_task_is_a_404_never_a_403(self, web, other_user, name):
        theirs = make_task(other_user, number=1000)
        url = reverse(name, args=[theirs.number])

        response = web.get(url) if name == 'todo:task' else web.post(url, {'title': 'x'})

        assert response.status_code == 404

    def test_an_unknown_number_is_a_404(self, web):
        assert web.get(reverse('todo:task', args=[4321])).status_code == 404

    def test_a_trashed_task_opens_but_cannot_be_edited(self, web, funded_user):
        task = create_task(funded_user, title='Gone')
        trash_task(task)

        assert web.get(reverse('todo:task', args=[task.number])).status_code == 200
        assert web.post(reverse('todo:edit', args=[task.number]), {'title': 'x'}).status_code == 404
        assert web.post(reverse('todo:complete', args=[task.number])).status_code == 404

    def test_restore_and_delete_need_a_trashed_task(self, web, funded_user):
        task = create_task(funded_user, title='Live')

        assert web.post(reverse('todo:restore', args=[task.number])).status_code == 404
        assert web.post(reverse('todo:delete', args=[task.number])).status_code == 404

    def test_another_users_label_is_a_404(self, web, other_user):
        theirs = make_label(other_user, 'Theirs')

        assert (
            web.post(reverse('todo:label-edit', args=[theirs.pk]), {'name': 'x'}).status_code == 404
        )
        assert web.post(reverse('todo:label-delete', args=[theirs.pk])).status_code == 404
        assert Label.objects.filter(pk=theirs.pk).exists()

    def test_gets_are_not_allowed_on_actions(self, web, funded_user):
        task = create_task(funded_user, title='T')

        assert web.get(reverse('todo:complete', args=[task.number])).status_code == 405


class TestQuickAdd:
    def test_htmx_success_rerenders_the_list_and_clears_the_form(self, web, funded_user, wednesday):
        response = web.post(
            reverse('todo:create'),
            {
                'title': ' Buy milk ',
                'due_date': wednesday.isoformat(),
                'due_time': '08:15',
                'priority': '2',
                'view': 'today',
            },
            **HTMX,
        )

        body = text(response)
        assert response.status_code == 200
        task = Task.objects.get(user=funded_user)
        assert (task.title, task.priority, task.due_time, task.source) == (
            'Buy milk',
            2,
            time(8, 15),
            'web',
        )
        assert 'Buy milk' in body
        assert 'id="quick-add" hx-swap-oob="true"' in body  # the cleared form
        assert 'hx-swap-oob="true">1<' in body  # the sidebar count
        assert '1 of 2 free tasks used this month' in body

    def test_it_takes_labels(self, web, funded_user):
        mine = make_label(funded_user, 'Mine')

        web.post(reverse('todo:create'), {'title': 'T', 'labels': [str(mine.pk)]}, **HTMX)

        assert list(Task.objects.get().labels.all()) == [mine]

    def test_another_users_label_is_refused(self, web, other_user):
        theirs = make_label(other_user, 'Theirs')

        response = web.post(
            reverse('todo:create'), {'title': 'T', 'labels': [str(theirs.pk)]}, **HTMX
        )

        assert response.status_code == 422
        assert response['HX-Retarget'] == '#quick-add-msg'
        assert 'Labels:' in text(response)
        assert not Task.objects.exists()

    def test_errors_are_label_message_lines(self, web):
        response = web.post(
            reverse('todo:create'), {'title': '', 'due_time': '09:00', 'view': 'today'}, **HTMX
        )

        body = text(response)
        assert response.status_code == 422
        assert response['HX-Retarget'] == '#quick-add-msg'
        assert 'Title: Title is required.' in body
        assert 'Time: A time needs a date.' in body

    def test_not_enough_credits_is_a_422_with_a_top_up_link(self, user, config):
        create_task(user, title='One')
        create_task(user, title='Two')
        client = Client()
        client.force_login(user)

        response = client.post(reverse('todo:create'), {'title': 'Three'}, **HTMX)

        assert response.status_code == 422
        assert response['HX-Retarget'] == '#quick-add-msg'
        assert 'Top up your credits' in text(response)
        assert reverse('billing:credits-history') in text(response)
        assert Task.objects.count() == 2

    def test_a_blocked_account_says_so(self, user, config):
        for title in ('One', 'Two', 'Owed'):
            create_task(user, title=title, allow_unpaid=True)
        client = Client()
        client.force_login(user)

        response = client.post(reverse('todo:create'), {'title': 'More'}, **HTMX)

        assert response.status_code == 422
        assert 'Top up your credits to add tasks' in text(response)

    def test_without_htmx_it_redirects_back_with_a_message(self, web):
        response = web.post(
            reverse('todo:create'), {'title': 'T'}, HTTP_REFERER='http://testserver/todo/all/'
        )

        assert response.status_code == 302 and response['Location'] == 'http://testserver/todo/all/'
        assert Task.objects.count() == 1

    def test_a_foreign_referer_is_not_followed(self, web):
        response = web.post(
            reverse('todo:create'), {'title': 'T'}, HTTP_REFERER='https://evil.test/'
        )

        assert response['Location'] == reverse('todo:today')

    def test_without_htmx_a_refusal_is_a_flash_message(self, web):
        response = web.post(reverse('todo:create'), {'title': ''}, follow=True)

        assert 'Title: Title is required.' in text(response)


class TestTaskPage:
    def test_it_shows_the_task(self, seeded):
        client = Client()
        client.force_login(seeded)
        parent = Task.objects.get(title='Parent task')

        body = text(client.get(reverse('todo:task', args=[parent.number])))

        assert f'#{parent.number}' in body
        assert '<strong>bold</strong>' in body  # the description, rendered
        assert 'Child task' in body and 'New subtask' in body
        assert 'Move to trash' in body and 'Postpone' in body
        assert 'Supports' not in body and 'Markdown:' in body

    def test_a_subtask_page_links_its_parent_and_has_no_subtasks_block(self, seeded):
        client = Client()
        client.force_login(seeded)
        child = Task.objects.get(title='Child task')

        body = text(client.get(reverse('todo:task', args=[child.number])))

        assert 'Subtask of' in body and 'Parent task' in body
        assert 'New subtask' not in body

    def test_a_completed_task_shows_when_and_uncomplete(self, seeded):
        client = Client()
        client.force_login(seeded)
        done = Task.objects.get(title='Done task')

        body = text(client.get(reverse('todo:task', args=[done.number])))

        assert 'Completed 28 Oct 2026' in body and 'Uncomplete' in body

    def test_a_trashed_task_is_read_only_with_restore(self, seeded):
        client = Client()
        client.force_login(seeded)
        gone = Task.objects.get(title='Gone task')

        body = text(client.get(reverse('todo:task', args=[gone.number])))

        assert 'In the trash since' in body and 'Restore' in body
        assert 'name="part"' not in body  # no edit forms
        assert 'checklist' not in body

    def test_the_description_is_sanitised_and_references_link(self, funded_user, config):
        other = create_task(funded_user, title='Other')
        task = create_task(
            funded_user,
            title='T',
            description=f'<script>alert(1)</script> see #{other.number} and #1',
        )
        client = Client()
        client.force_login(funded_user)

        body = client.get(reverse('todo:task', args=[task.number])).content.decode()

        assert '<script>alert(1)</script>' not in body
        assert f'<a href="/todo/{other.number}/" class="task-ref">#{other.number}</a>' in body

    def test_checklist_boxes_render_disabled_for_the_script_to_enable(self, funded_user, config):
        task = create_task(funded_user, title='T', description='- [ ] a\n- [x] b')
        client = Client()
        client.force_login(funded_user)

        body = client.get(reverse('todo:task', args=[task.number])).content.decode()

        assert 'data-checklist-url="/todo/%d/checklist/"' % task.number in body
        assert body.count('class="task-list-item-checkbox"') == 2 and 'disabled' in body


class TestEdit:
    def test_each_part_saves_on_its_own(self, web, funded_user):
        task = create_task(funded_user, title='Old', description='old', priority=3)
        url = reverse('todo:edit', args=[task.number])

        r = web.post(url, {'part': 'title', 'title': 'New', 'ctx': 'page'}, **HTMX)
        assert r.status_code == 200 and 'id="task-panel"' in r.content.decode()
        web.post(url, {'part': 'description', 'description': 'new **text**', 'ctx': 'page'}, **HTMX)
        web.post(
            url,
            {
                'part': 'details',
                'priority': '1',
                'due_date': '2026-11-03',
                'due_time': '14:00',
                'ctx': 'page',
            },
            **HTMX,
        )

        task.refresh_from_db()
        assert (task.title, task.description, task.priority) == ('New', 'new **text**', 1)
        assert (task.due_date, task.due_time) == (date(2026, 11, 3), time(14, 0))

    def test_details_set_and_clear_the_labels(self, web, funded_user):
        label = make_label(funded_user, 'Home')
        task = create_task(funded_user, title='T')
        url = reverse('todo:edit', args=[task.number])

        web.post(url, {'part': 'details', 'labels': [str(label.pk)], 'ctx': 'page'}, **HTMX)
        assert list(task.labels.all()) == [label]
        web.post(url, {'part': 'details', 'ctx': 'page'}, **HTMX)

        assert task.labels.count() == 0

    def test_clearing_the_date_clears_the_time(self, web, funded_user):
        task = create_task(funded_user, title='T', due_date=date(2026, 11, 3), due_time=time(8, 0))

        web.post(
            reverse('todo:edit', args=[task.number]),
            {'part': 'details', 'due_date': '', 'due_time': '08:00', 'ctx': 'page'},
            **HTMX,
        )

        task.refresh_from_db()
        assert (task.due_date, task.due_time) == (None, None)

    def test_errors_go_to_the_panel_slot(self, web, funded_user):
        task = create_task(funded_user, title='T')

        response = web.post(
            reverse('todo:edit', args=[task.number]),
            {'part': 'title', 'title': '  ', 'ctx': 'page'},
            **HTMX,
        )

        assert response.status_code == 422
        assert response['HX-Retarget'] == '#task-msg'
        assert 'Title: Title is required.' in text(response)

    def test_a_description_over_the_cap_is_refused(self, web, funded_user):
        task = create_task(funded_user, title='T')

        response = web.post(
            reverse('todo:edit', args=[task.number]),
            {'part': 'description', 'description': 'x' * 20_001, 'ctx': 'page'},
            **HTMX,
        )

        assert response.status_code == 422

    def test_without_htmx_it_redirects_to_the_task(self, web, funded_user):
        task = create_task(funded_user, title='T')

        response = web.post(
            reverse('todo:edit', args=[task.number]), {'part': 'title', 'title': 'U', 'ctx': 'page'}
        )

        assert response.status_code == 302
        assert response['Location'] == reverse('todo:task', args=[task.number])


class TestChecklist:
    def test_it_ticks_a_box_and_answers_the_description(self, web, funded_user):
        task = create_task(funded_user, title='T', description='- [ ] a\n- [ ] b')

        response = web.post(
            reverse('todo:checklist', args=[task.number]), {'index': '1', 'checked': 'true'}, **HTMX
        )

        task.refresh_from_db()
        assert task.description == '- [ ] a\n- [x] b'
        body = response.content.decode()
        assert response.status_code == 200 and 'id="task-description"' in body
        assert 'checked' in body

        web.post(
            reverse('todo:checklist', args=[task.number]),
            {'index': '1', 'checked': 'false'},
            **HTMX,
        )
        task.refresh_from_db()
        assert task.description == '- [ ] a\n- [ ] b'

    def test_a_missing_item_or_a_bad_index_is_a_422(self, web, funded_user):
        task = create_task(funded_user, title='T', description='- [ ] a')
        url = reverse('todo:checklist', args=[task.number])

        assert web.post(url, {'index': '4', 'checked': 'true'}, **HTMX).status_code == 422
        assert web.post(url, {'index': 'x'}, **HTMX).status_code == 422
        assert web.post(url, {'index': '-1'}, **HTMX).status_code == 422


class TestRowActions:
    def row(self, client, name, task, **data):
        return client.post(reverse(name, args=[task.number]), data, **HTMX)

    def test_complete_removes_the_row_from_an_open_list_and_updates_the_count(
        self, web, funded_user, wednesday
    ):
        task = create_task(funded_user, title='T', due_date=wednesday)

        response = self.row(web, 'todo:complete', task, view='today', ctx='row')

        task.refresh_from_db()
        assert task.completed_at is not None
        assert response.status_code == 200
        assert '<li' not in response.content.decode()  # nothing to swap in: the row goes
        assert 'hx-swap-oob="true"' in response.content.decode()  # the sidebar count

    def test_complete_keeps_the_row_in_a_subtask_list(self, web, funded_user):
        parent = create_task(funded_user, title='P')
        sub = create_task(funded_user, title='S', parent=parent)

        response = self.row(web, 'todo:complete', sub, view='subtask', ctx='row')

        body = text(response)
        assert f'id="task-{sub.number}"' in body and 'title="Uncomplete"' in body

    def test_uncomplete_removes_the_row_from_the_completed_list(self, web, funded_user):
        task = create_task(funded_user, title='T')
        complete_task(task)

        response = self.row(web, 'todo:uncomplete', task, view='completed', ctx='row')

        assert Task.objects.get(pk=task.pk).completed_at is None
        assert '<li' not in response.content.decode()

    def test_postpone_presets_and_a_date(self, web, funded_user, wednesday):
        task = create_task(funded_user, title='T', due_date=wednesday, due_time=time(7, 0))

        r = self.row(web, 'todo:postpone', task, preset='tomorrow', view='all', ctx='row')
        task.refresh_from_db()
        assert task.due_date == date(2026, 10, 29) and f'id="task-{task.number}"' in text(r)
        self.row(web, 'todo:postpone', task, preset='next_week', view='all', ctx='row')
        task.refresh_from_db()
        assert task.due_date == date(2026, 11, 2)
        self.row(web, 'todo:postpone', task, preset='next_weekend', view='all', ctx='row')
        task.refresh_from_db()
        assert task.due_date == date(2026, 10, 31)
        self.row(
            web, 'todo:postpone', task, preset='date', date='2026-12-25', view='all', ctx='row'
        )
        task.refresh_from_db()
        assert (task.due_date, task.due_time) == (date(2026, 12, 25), time(7, 0))

    def test_postponing_out_of_today_removes_the_row(self, web, funded_user, wednesday):
        task = create_task(funded_user, title='T', due_date=wednesday)

        response = self.row(web, 'todo:postpone', task, preset='tomorrow', view='today', ctx='row')

        assert '<li' not in response.content.decode()

    def test_postpone_errors(self, web, funded_user, wednesday):
        task = create_task(funded_user, title='T')

        past = self.row(web, 'todo:postpone', task, preset='date', date='2020-01-01')
        assert past.status_code == 422 and 'Date: Choose today or a later date.' in text(past)
        nothing = self.row(web, 'todo:postpone', task, preset='date')
        assert nothing.status_code == 422
        unknown = self.row(web, 'todo:postpone', task, preset='someday')
        assert unknown.status_code == 422 and 'When:' in text(unknown)

    def test_trash_restore_and_delete(self, web, funded_user):
        task = create_task(funded_user, title='T')

        trashed = self.row(web, 'todo:trash-task', task, view='all', ctx='row')
        assert Task.objects.get(pk=task.pk).deleted_at is not None
        assert '<li' not in trashed.content.decode()

        self.row(web, 'todo:restore', task, view='trash', ctx='row')
        assert Task.objects.get(pk=task.pk).deleted_at is None

        trash_task(task)
        response = web.post(reverse('todo:delete', args=[task.number]), **HTMX)
        assert response.status_code == 200 and not Task.objects.filter(pk=task.pk).exists()

    def test_deleting_forever_without_htmx_redirects_to_the_trash(self, web, funded_user):
        task = create_task(funded_user, title='T')
        trash_task(task)

        response = web.post(reverse('todo:delete', args=[task.number]))

        assert response.status_code == 302 and response['Location'] == reverse('todo:trash')

    def test_restoring_a_subtask_of_a_trashed_parent_is_refused(self, web, funded_user):
        parent = create_task(funded_user, title='P')
        sub = create_task(funded_user, title='S', parent=parent)
        trash_task(parent)

        response = web.post(reverse('todo:restore', args=[sub.number]), {'ctx': 'page'}, **HTMX)

        assert response.status_code == 422
        assert 'Restore the parent task first.' in text(response)
        assert response['HX-Retarget'] == '#task-msg'

    def test_from_the_task_page_the_panel_comes_back(self, web, funded_user):
        task = create_task(funded_user, title='T')

        response = self.row(web, 'todo:complete', task, ctx='page')

        body = text(response)
        assert 'id="task-panel"' in body and 'Uncomplete' in body

    def test_completing_a_parent_with_open_subtasks_completes_them(self, web, funded_user):
        parent = create_task(funded_user, title='P')
        sub = create_task(funded_user, title='S', parent=parent)

        web.post(reverse('todo:complete', args=[parent.number]))  # the confirm modal's plain POST

        assert Task.objects.get(pk=sub.pk).completed_at is not None

    def test_the_row_asks_before_completing_a_parent_with_open_subtasks(self, web, funded_user):
        parent = create_task(funded_user, title='P')
        create_task(funded_user, title='S', parent=parent)
        client = web

        body = text(client.get(reverse('todo:all')))

        assert 'has 1 open subtask. Completing it completes it too.' in body
        assert f'data-url="{reverse("todo:complete", args=[parent.number])}"' in body

    def test_reschedule_overdue(self, web, funded_user, wednesday):
        late = create_task(funded_user, title='Late', due_date=date(2026, 10, 1))

        response = web.post(reverse('todo:reschedule-overdue'), **HTMX)

        assert response.status_code == 200 and response['HX-Redirect'] == reverse('todo:today')
        late.refresh_from_db()
        assert late.due_date == wednesday


class TestSubtasks:
    def test_add_a_subtask(self, web, funded_user):
        parent = create_task(funded_user, title='P')

        response = web.post(
            reverse('todo:subtask-add', args=[parent.number]),
            {'title': 'Sub', 'ctx': 'page'},
            **HTMX,
        )

        assert response.status_code == 200 and 'id="task-panel"' in response.content.decode()
        assert Task.objects.get(title='Sub').parent == parent

    def test_errors_and_credits(self, user, config):
        parent = create_task(user, title='P')
        create_task(user, title='Sub')
        client = Client()
        client.force_login(user)
        url = reverse('todo:subtask-add', args=[parent.number])

        empty = client.post(url, {'title': ''}, **HTMX)
        assert empty.status_code == 422 and empty['HX-Retarget'] == '#subtask-msg'
        broke = client.post(url, {'title': 'Third'}, **HTMX)
        assert broke.status_code == 422 and 'Top up your credits' in text(broke)

    def test_a_subtask_cannot_have_subtasks(self, web, funded_user):
        parent = create_task(funded_user, title='P')
        sub = create_task(funded_user, title='S', parent=parent)

        response = web.post(
            reverse('todo:subtask-add', args=[sub.number]), {'title': 'Deep'}, **HTMX
        )

        assert response.status_code == 422
        assert 'A subtask cannot have subtasks.' in text(response)


class TestReorder:
    def test_it_writes_positions_and_answers_204(self, web, funded_user, wednesday):
        a = create_task(funded_user, title='A', due_date=wednesday)
        b = create_task(funded_user, title='B', due_date=wednesday)

        response = web.post(
            reverse('todo:reorder'), {'scope': 'today', 'ids[]': [str(b.pk), str(a.pk)]}, **HTMX
        )

        assert response.status_code == 204
        assert (
            Task.objects.get(pk=b.pk).today_position,
            Task.objects.get(pk=a.pk).today_position,
        ) == (
            0,
            1,
        )
        body = text(web.get(reverse('todo:today')))
        assert body.index('>B<') < body.index('>A<')

    def test_subtasks(self, web, funded_user):
        parent = create_task(funded_user, title='P')
        a = create_task(funded_user, title='A', parent=parent)
        b = create_task(funded_user, title='B', parent=parent)

        response = web.post(
            reverse('todo:reorder'),
            {'scope': 'subtasks', 'parent': str(parent.number), 'ids[]': [str(b.pk), str(a.pk)]},
            **HTMX,
        )

        assert response.status_code == 204 and Task.objects.get(pk=b.pk).position == 0
        assert Task.objects.get(pk=a.pk).position == 1

    def test_bad_input(self, web, funded_user, other_user):
        theirs = make_task(other_user, number=1000)

        assert (
            web.post(
                reverse('todo:reorder'), {'scope': 'today', 'ids[]': ['x']}, **HTMX
            ).status_code
            == 422
        )
        assert web.post(reverse('todo:reorder'), {'scope': 'nope'}, **HTMX).status_code == 422
        assert (
            web.post(
                reverse('todo:reorder'), {'scope': 'subtasks', 'parent': 'x'}, **HTMX
            ).status_code
            == 422
        )
        assert (
            web.post(
                reverse('todo:reorder'), {'scope': 'subtasks', 'parent': str(theirs.number)}, **HTMX
            ).status_code
            == 404
        )


class TestLabelsPage:
    def test_create_edit_delete_reorder(self, web, funded_user):
        created = web.post(
            reverse('todo:labels'),
            {'name': 'Home', 'color': '#336699', 'description': 'Around the house'},
            **HTMX,
        )
        assert created.status_code == 200 and 'Home' in text(created)
        label = Label.objects.get()
        assert (label.color, label.description) == ('#336699', 'Around the house')

        web.post(reverse('todo:label-edit', args=[label.pk]), {'name': 'House'}, **HTMX)
        label.refresh_from_db()
        assert (label.name, label.color, label.description) == (
            'House',
            '#336699',
            'Around the house',
        )

        web.post(
            reverse('todo:label-edit', args=[label.pk]),
            {'name': 'House', 'color': '#445566', 'description': ''},
            **HTMX,
        )
        label.refresh_from_db()
        assert (label.color, label.description) == ('#445566', '')

        second = make_label(funded_user, 'Work')
        r = web.post(
            reverse('todo:label-reorder'), {'ids[]': [str(second.pk), str(label.pk)]}, **HTMX
        )
        assert r.status_code == 204 and Label.objects.get(pk=second.pk).position == 0

        response = web.post(reverse('todo:label-delete', args=[label.pk]), **HTMX)
        assert response.status_code == 200 and response['HX-Redirect'] == reverse('todo:labels')
        assert not Label.objects.filter(pk=label.pk).exists()

    def test_a_duplicate_or_bad_label_is_a_422_in_the_label_slot(self, web, funded_user):
        make_label(funded_user, 'Home')

        duplicate = web.post(reverse('todo:labels'), {'name': 'home'}, **HTMX)
        assert duplicate.status_code == 422 and duplicate['HX-Retarget'] == '#label-msg'
        assert 'You already have a label with this name.' in text(duplicate)
        bad = web.post(reverse('todo:labels'), {'name': 'X', 'color': 'red'}, **HTMX)
        assert bad.status_code == 422 and 'Colour:' in text(bad)
        blank = web.post(reverse('todo:labels'), {'name': ''}, **HTMX)
        assert 'Name: Name is required.' in text(blank)

    def test_the_page_lists_labels_with_counts_and_a_delete_modal(self, web, funded_user, config):
        label = make_label(funded_user, 'Home', description='Around the house')
        create_task(funded_user, title='T', labels=[label])

        body = text(web.get(reverse('todo:labels')))

        assert 'Home' in body and '1 task' in body
        assert 'title="Around the house"' in body  # the chip's tooltip
        assert 'Are you sure you want to delete Home?' in body

    def test_a_chip_shows_in_a_task_row_with_its_colour(self, web, funded_user):
        label = make_label(funded_user, 'Home', color='#336699', description='Hover me')
        create_task(funded_user, title='T', labels=[label])

        body = web.get(reverse('todo:all')).content.decode()

        assert 'background-color: #33669933; color: #336699' in body
        assert 'title="Hover me"' in body


class TestSettingsPage:
    def test_save_the_default_due_time(self, web, funded_user):
        response = web.post(reverse('todo:settings'), {'default_due_time': '07:45'}, **HTMX)

        assert response.status_code == 200 and 'Saved.' in text(response)
        assert get_account(funded_user).default_due_time == time(7, 45)

    def test_a_bad_time_is_a_422(self, web):
        response = web.post(reverse('todo:settings'), {'default_due_time': 'noon'}, **HTMX)

        assert response.status_code == 422 and response['HX-Retarget'] == '#settings-msg'
        assert 'Default due time: Enter a valid time.' in text(response)

    def test_without_htmx_it_redirects(self, web):
        response = web.post(reverse('todo:settings'), {'default_due_time': '10:00'})

        assert response.status_code == 302 and response['Location'] == reverse('todo:settings')


class TestRateLimits:
    @pytest.fixture(autouse=True)
    def _limits(self, settings, ratelimit_clock):
        settings.RATELIMIT_ENABLE = True
        settings.RATELIMIT_RULES = {
            **settings.RATELIMIT_RULES,
            'TODO_CREATE_USER': (2, 60),
            'TODO_EDIT_USER': (2, 60),
        }

    def test_creating_is_limited_per_user(self, web):
        for n in range(2):
            assert web.post(reverse('todo:create'), {'title': f'T{n}'}, **HTMX).status_code == 200

        response = web.post(reverse('todo:create'), {'title': 'T3'}, **HTMX)

        assert response.status_code == 429 and response['Retry-After'] == '20'
        assert response['HX-Retarget'] == '#quick-add-msg'
        assert Task.objects.count() == 2

    def test_a_refused_form_spends_no_budget(self, web):
        for _ in range(5):
            assert web.post(reverse('todo:create'), {'title': ''}, **HTMX).status_code == 422

        assert web.post(reverse('todo:create'), {'title': 'ok'}, **HTMX).status_code == 200

    def test_subtasks_share_the_creation_budget(self, web, funded_user):
        parent = create_task(funded_user, title='P')
        web.post(reverse('todo:create'), {'title': 'a'}, **HTMX)
        web.post(reverse('todo:create'), {'title': 'b'}, **HTMX)

        response = web.post(
            reverse('todo:subtask-add', args=[parent.number]), {'title': 's'}, **HTMX
        )

        assert response.status_code == 429

    def test_edits_are_limited_per_user(self, web, funded_user):
        task = create_task(funded_user, title='T')
        for _ in range(2):
            assert (
                web.post(
                    reverse('todo:postpone', args=[task.number]), {'preset': 'tomorrow'}, **HTMX
                ).status_code
                == 200
            )

        response = web.post(reverse('todo:trash-task', args=[task.number]), **HTMX)

        assert response.status_code == 429
        assert Task.objects.get(pk=task.pk).deleted_at is None

    def test_every_other_mutation_shares_the_edit_budget(self, web, funded_user):
        task = create_task(funded_user, title='T')
        label = make_label(funded_user, 'L')
        web.post(reverse('todo:settings'), {'default_due_time': '08:00'}, **HTMX)
        web.post(reverse('todo:label-edit', args=[label.pk]), {'name': 'M'}, **HTMX)

        assert web.post(reverse('todo:complete', args=[task.number]), **HTMX).status_code == 429
        assert web.post(reverse('todo:reorder'), {'scope': 'today'}, **HTMX).status_code == 429
        assert web.post(reverse('todo:labels'), {'name': 'N'}, **HTMX).status_code == 429
        assert web.post(reverse('todo:reschedule-overdue'), **HTMX).status_code == 429

    def test_a_non_htmx_request_gets_the_429_page(self, web, funded_user):
        task = create_task(funded_user, title='T')
        for _ in range(2):
            web.post(reverse('todo:postpone', args=[task.number]), {'preset': 'tomorrow'})

        response = web.post(reverse('todo:postpone', args=[task.number]), {'preset': 'tomorrow'})

        assert response.status_code == 429

    def test_other_users_have_their_own_budget(self, web, funded_user):
        for _ in range(3):
            web.post(reverse('todo:create'), {'title': 'x'}, **HTMX)
        other = make_user()
        add_credits(other, 10, description='x', source='test')
        client = Client()
        client.force_login(other)

        assert client.post(reverse('todo:create'), {'title': 'y'}, **HTMX).status_code == 200
        assert get_balance(other) == 10
