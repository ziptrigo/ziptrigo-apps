"""Projects (issue #314): `services.projects`, what an archived project mutes, the project filter,
and the pages."""

from datetime import date, time, timedelta
from html import unescape

import pytest
from django.core.exceptions import ValidationError
from django.test import Client
from django.urls import reverse

from apps.core.models import Notification

from ..models import Project, Reminder, Task, TaskEvent, TodoSettings
from ..services import (
    FilterParams,
    add_blocker,
    close_task,
    create_task,
    describe_params,
    filter_tasks,
    listed_tasks,
    open_tasks,
    overdue_set,
    projects,
    reminders,
    reschedule_overdue,
    save_filter,
    set_state,
    today_count,
    today_set,
    trash_task,
    upcoming_set,
    update_task,
)
from ..services.event_text import describe
from ..services.filters import label_names
from ..services.saved_filters import filter_counts
from .api_support import auth, get, post  # noqa: F401 -- `auth` is a fixture
from .factories import make_user, utc

pytestmark = pytest.mark.django_db

HTMX = {'HTTP_HX_REQUEST': 'true'}
TODAY = date(2026, 10, 28)
FIRES = utc(2026, 10, 28, 14, 26)  # the default 5-minute reminder of a task due at 14:30


def text(response) -> str:
    return unescape(response.content.decode())


def errors(callable_, *args, **kwargs) -> dict[str, list[str]]:
    with pytest.raises(ValidationError) as caught:
        callable_(*args, **kwargs)
    return caught.value.message_dict


def reload(task: Task) -> Task:
    return Task.objects.select_related('project', 'user').get(pk=task.pk)


def log(task: Task) -> list[str]:
    return [describe(event) for event in TaskEvent.objects.filter(task=task)]


@pytest.fixture
def project(funded_user) -> Project:
    return projects.create_project(funded_user, 'Website', '#5b8def')


# -- Projects themselves -------------------------------------------------------------------------


class TestProjectRules:
    def test_create_with_defaults(self, user):
        project = projects.create_project(user, '  Web   site  ')

        assert (project.name, project.color, project.description) == ('Web site', '#8fa89e', '')
        assert not project.is_archived

    def test_names_are_unique_ignoring_case_archived_ones_included(self, user):
        first = projects.create_project(user, 'Website')
        projects.archive_project(first)

        assert errors(projects.create_project, user, 'WEBSITE') == {
            'name': ['You already have a project with this name.']
        }

    def test_another_user_may_have_the_same_name(self, user, other_user):
        projects.create_project(user, 'Website')

        assert projects.create_project(other_user, 'Website').name == 'Website'

    @pytest.mark.parametrize(
        ('name', 'message'),
        [('   ', 'Name is required.'), ('x' * 81, 'Name is too long (at most 80 characters).')],
    )
    def test_a_bad_name_is_refused(self, user, name, message):
        assert errors(projects.create_project, user, name) == {'name': [message]}

    def test_the_colour_and_the_description_are_checked(self, user):
        assert errors(projects.create_project, user, 'A', 'red') == {
            'color': ['Use a colour like #8fa89e.']
        }
        assert errors(projects.create_project, user, 'A', '', 'x' * 10_001) == {
            'description': ['Description is too long (at most 10,000 characters).']
        }

    def test_the_cap_counts_archived_projects(self, user):
        config = TodoSettings.load()
        config.max_projects_per_user = 2
        config.save()
        projects.archive_project(projects.create_project(user, 'One'))
        projects.create_project(user, 'Two')

        assert errors(projects.create_project, user, 'Three') == {
            '__all__': ['You can have up to 2 projects.']
        }

    def test_update_changes_only_what_is_given(self, user):
        project = projects.create_project(user, 'Website', '#112233', 'About')

        projects.update_project(project, name='Site')

        project.refresh_from_db()
        assert (project.name, project.color, project.description) == ('Site', '#112233', 'About')

    def test_renaming_onto_another_project_is_refused(self, user):
        projects.create_project(user, 'One')
        two = projects.create_project(user, 'Two')

        assert errors(projects.update_project, two, name='one') == {
            'name': ['You already have a project with this name.']
        }

    def test_reorder_ignores_ids_that_are_not_the_users(self, user, other_user):
        one = projects.create_project(user, 'One')
        two = projects.create_project(user, 'Two')
        theirs = projects.create_project(other_user, 'Theirs')

        assert projects.reorder_projects(user, [two.pk, theirs.pk, one.pk]) == 2

        assert [p.name for p in projects.list_projects(user)] == ['Two', 'One']
        theirs.refresh_from_db()
        assert theirs.position == 1

    def test_the_list_counts_open_and_closed_tasks_without_the_trash(self, funded_user, project):
        create_task(funded_user, title='Open')
        parent = create_task(funded_user, title='Parent', project=project)
        create_task(funded_user, title='Sub', parent=parent)
        close_task(create_task(funded_user, title='Done', project=project))
        trash_task(create_task(funded_user, title='Gone', project=project))

        listed = projects.list_projects(funded_user).get()

        assert (listed.open_count, listed.closed_count) == (2, 1)
        assert listed.progress_percent == 33
        assert projects.unfiled_count(funded_user) == 1

    def test_the_list_is_one_query(self, funded_user, project, django_assert_num_queries):
        projects.create_project(funded_user, 'Other')
        create_task(funded_user, title='A', project=project)

        with django_assert_num_queries(1):
            list(projects.list_projects(funded_user, archived=None))


# -- A task's project ----------------------------------------------------------------------------


class TestTaskProject:
    def test_a_task_is_created_in_a_project_by_instance_or_id(self, funded_user, project):
        by_instance = create_task(funded_user, title='A', project=project)
        by_id = create_task(funded_user, title='B', project=str(project.pk))

        assert by_instance.project == project and by_id.project == project
        assert log(by_instance) == ['Created, in the project Website']

    def test_no_project_is_the_default(self, funded_user):
        assert create_task(funded_user, title='A').project is None

    @pytest.mark.parametrize('value', ['not-an-id', '7b0e3c1e-0000-4000-8000-000000000000'])
    def test_an_unknown_project_is_refused_and_nothing_is_created(self, funded_user, value):
        assert errors(create_task, funded_user, title='A', project=value) == {
            'project': ['Unknown project.']
        }
        assert not Task.objects.exists()

    def test_someone_elses_project_is_unknown(self, funded_user, other_user):
        theirs = projects.create_project(other_user, 'Theirs')

        assert errors(create_task, funded_user, title='A', project=theirs) == {
            'project': ['Unknown project.']
        }
        assert errors(create_task, funded_user, title='A', project=theirs.pk) == {
            'project': ['Unknown project.']
        }

    def test_an_archived_project_takes_no_new_task(self, funded_user, project):
        projects.archive_project(project)

        assert errors(create_task, funded_user, title='A', project=project) == {
            'project': ['This project is archived. Unarchive it to add tasks to it.']
        }

    def test_a_subtask_is_in_its_parents_project_whatever_is_asked(self, funded_user, project):
        other = projects.create_project(funded_user, 'Other')
        parent = create_task(funded_user, title='Parent', project=project)

        sub = create_task(funded_user, title='Sub', parent=parent, project=other)

        assert sub.project == project

    def test_moving_a_task_moves_its_subtasks_and_is_logged(self, funded_user, project):
        parent = create_task(funded_user, title='Parent')
        sub = create_task(funded_user, title='Sub', parent=parent)
        closed = close_task(create_task(funded_user, title='Closed sub', parent=parent))

        projects.move_task(parent, project)

        assert reload(parent).project == project
        assert reload(sub).project == project and reload(closed).project == project
        assert log(parent)[0] == 'Added to the project Website'
        entry = TaskEvent.objects.filter(task=sub).first()
        assert describe(entry) == (
            f'Added to the project Website with its parent, #{parent.number} Parent'
        )
        assert entry.automatic

    def test_moving_between_projects_and_out(self, funded_user, project):
        other = projects.create_project(funded_user, 'Other')
        task = create_task(funded_user, title='A', project=project)

        projects.move_task(task, other.pk)
        projects.move_task(task, None)

        assert reload(task).project is None
        assert log(task)[:2] == [
            'Removed from the project Other',
            'Moved from the project Website to Other',
        ]

    def test_the_same_project_records_nothing(self, funded_user, project):
        task = create_task(funded_user, title='A', project=project)

        projects.move_task(task, project)

        assert TaskEvent.objects.filter(task=task).count() == 1  # only `created`

    def test_a_subtask_cannot_move_on_its_own(self, funded_user, project):
        sub = create_task(funded_user, title='Sub', parent=create_task(funded_user, title='Parent'))

        assert errors(projects.move_task, sub, project) == {
            'project': ["A subtask is in its parent's project. Move the parent instead."]
        }

    def test_a_trashed_task_and_an_archived_target_are_refused(self, funded_user, project):
        trashed = trash_task(create_task(funded_user, title='A'))
        assert errors(projects.move_task, trashed, project) == {
            '__all__': ['Restore the task before changing it.']
        }

        projects.archive_project(project)
        assert errors(projects.move_task, create_task(funded_user, title='B'), project) == {
            'project': ['This project is archived. Unarchive it to add tasks to it.']
        }

    def test_a_closed_task_can_move(self, funded_user, project):
        task = close_task(create_task(funded_user, title='A'))

        projects.move_task(task, project)

        assert reload(task).project == project

    def test_a_task_moved_under_a_parent_takes_its_project(self, funded_user, project):
        parent = create_task(funded_user, title='Parent', project=project)
        loose = create_task(funded_user, title='Loose')

        update_task(loose, changes={'parent': parent})

        assert reload(loose).project == project

    def test_the_next_occurrence_stays_in_the_project(self, funded_user, project, wednesday):
        task = create_task(
            funded_user, title='Water', due_date=TODAY, rrule='FREQ=DAILY', project=project
        )

        close_task(task)

        following = Task.objects.exclude(pk=task.pk).get()
        assert following.project == project

    def test_also_while_the_project_is_archived(self, funded_user, project, wednesday):
        task = create_task(
            funded_user, title='Water', due_date=TODAY, rrule='FREQ=DAILY', project=project
        )
        projects.archive_project(project)

        close_task(task)

        assert Task.objects.exclude(pk=task.pk).get().project == project

    def test_deleting_a_project_keeps_its_tasks(self, funded_user, project):
        task = create_task(funded_user, title='A', project=project)
        trashed = trash_task(create_task(funded_user, title='B', project=project))

        assert projects.delete_project(project) == 2

        assert not Project.objects.exists()
        assert reload(task).project is None and reload(trashed).project is None
        entry = TaskEvent.objects.filter(task=task).first()
        assert describe(entry) == 'The project Website was deleted: the task is in no project'
        assert entry.automatic


# -- What an archived project mutes --------------------------------------------------------------


class TestArchived:
    def due(self, user, project, **fields) -> Task:
        fields.setdefault('due_date', TODAY)
        fields.setdefault('due_time', time(14, 30))
        return create_task(user, title='Pay rent', project=project, **fields)

    def test_archive_and_unarchive_are_idempotent(self, project):
        assert projects.archive_project(project) is True
        assert projects.archive_project(project) is False
        assert project.is_archived
        assert projects.unarchive_project(project) is True
        assert projects.unarchive_project(project) is False
        assert not project.is_archived

    def test_its_tasks_leave_today_upcoming_and_all(self, funded_user, project, wednesday):
        self.due(funded_user, project)
        self.due(funded_user, project, due_date=TODAY + timedelta(days=2))
        self.due(funded_user, project, due_date=TODAY - timedelta(days=2))
        kept = create_task(funded_user, title='Other', due_date=TODAY)

        projects.archive_project(project)

        assert list(today_set(funded_user)) == [kept]
        assert not upcoming_set(funded_user).exists()
        assert not overdue_set(funded_user).exists()
        assert list(listed_tasks(funded_user)) == [kept]
        assert today_count(funded_user) == 1
        assert open_tasks(funded_user).count() == 4  # still open: the cap counts them

    def test_they_are_never_overdue_and_not_rescheduled(self, funded_user, project, wednesday):
        task = self.due(funded_user, project, due_date=TODAY - timedelta(days=3))
        projects.archive_project(project)

        assert not reload(task).is_overdue(TODAY)
        assert reschedule_overdue(funded_user) == 0
        assert reload(task).due_date == TODAY - timedelta(days=3)

    def test_their_reminders_wait_and_are_not_swept(self, funded_user, project, wednesday):
        task = self.due(funded_user, project)
        projects.archive_project(project)

        assert reminders.deliver_due(FIRES) == 0
        reminders.deliver_due(utc(2026, 10, 28, 20, 0))  # hours later: not swept as stale

        reminder = Reminder.objects.get(task=task)
        assert reminder.sent_at is None and not reminder.skipped
        assert not Notification.objects.exists()

    def test_unarchiving_sends_one_reminder_per_task(
        self, funded_user, project, wednesday, freeze_now, django_capture_on_commit_callbacks
    ):
        task = self.due(funded_user, project)
        reminders.add_reminder(task, minutes_before=60, channel='in_app')
        quiet = create_task(funded_user, title='No date', project=project)
        projects.archive_project(project)
        freeze_now(2026, 10, 28, 15, 0)

        with django_capture_on_commit_callbacks(execute=True):
            projects.unarchive_project(project)

        assert Notification.objects.filter(user=funded_user).count() == 1  # one, not two
        assert not Reminder.objects.filter(task=task, sent_at__isnull=True).exists()
        assert reload(quiet).project == project

    def test_a_task_on_hold_keeps_waiting_after_the_unarchive(
        self, funded_user, project, wednesday, freeze_now
    ):
        task = self.due(funded_user, project)
        set_state(task, 'on_hold')
        projects.archive_project(project)
        freeze_now(2026, 10, 28, 15, 0)

        projects.unarchive_project(project)

        assert Reminder.objects.get(task=task).sent_at is None
        assert not Notification.objects.exists()

    def test_leaving_on_hold_inside_an_archived_project_releases_nothing(
        self, funded_user, project, wednesday, freeze_now
    ):
        task = self.due(funded_user, project)
        set_state(task, 'on_hold')
        projects.archive_project(project)
        freeze_now(2026, 10, 28, 15, 0)

        set_state(reload(task), 'todo')

        assert Reminder.objects.get(task=task).sent_at is None
        assert not Notification.objects.exists()

    def test_moving_a_task_out_releases_what_it_held(
        self, funded_user, project, wednesday, freeze_now, django_capture_on_commit_callbacks
    ):
        task = self.due(funded_user, project)
        projects.archive_project(project)
        freeze_now(2026, 10, 28, 15, 0)

        with django_capture_on_commit_callbacks(execute=True):
            projects.move_task(reload(task), None)

        assert Notification.objects.filter(user=funded_user).count() == 1
        assert Reminder.objects.get(task=task).sent_at is not None

    def test_deleting_an_archived_project_brings_its_tasks_back(
        self, funded_user, project, wednesday, freeze_now, django_capture_on_commit_callbacks
    ):
        task = self.due(funded_user, project)
        projects.archive_project(project)
        freeze_now(2026, 10, 28, 15, 0)

        with django_capture_on_commit_callbacks(execute=True):
            projects.delete_project(project)

        assert list(today_set(funded_user)) == [task]
        assert Notification.objects.filter(user=funded_user).count() == 1

    def test_a_task_it_blocks_outside_stays_blocked_and_is_counted(self, funded_user, project):
        blocker = create_task(funded_user, title='Inside', project=project)
        inside = create_task(funded_user, title='Also inside', project=project)
        outside = create_task(funded_user, title='Outside')
        add_blocker(inside, blocker)
        add_blocker(outside, blocker)

        assert projects.outside_blocked_count(project) == 1

        projects.archive_project(project)

        assert reload(outside).state == 'blocked'


# -- The project filter --------------------------------------------------------------------------


class TestFilter:
    def params(self, **fields) -> FilterParams:
        return FilterParams.from_dict(fields)

    def titles(self, user, **fields) -> set[str]:
        return {task.title for task in filter_tasks(user, self.params(**fields))}

    @pytest.fixture
    def tasks(self, funded_user, project):
        other = projects.create_project(funded_user, 'Archive me')
        create_task(funded_user, title='In website', project=project)
        create_task(funded_user, title='In archived', project=other)
        create_task(funded_user, title='Loose')
        projects.archive_project(other)
        return other

    def test_projects_select_any_of_them_and_none(self, funded_user, project, tasks):
        assert self.titles(funded_user, projects=[str(project.pk)]) == {'In website'}
        assert self.titles(funded_user, projects=['none']) == {'Loose'}
        assert self.titles(funded_user, projects=['none', str(project.pk)]) == {
            'In website',
            'Loose',
        }

    def test_archived_projects_are_left_out_unless_asked_or_chosen(
        self, funded_user, project, tasks
    ):
        assert self.titles(funded_user, priorities=[0]) == {'In website', 'Loose'}
        assert self.titles(funded_user, priorities=[0], include_archived=True) == {
            'In website',
            'In archived',
            'Loose',
        }
        assert self.titles(funded_user, projects=[str(tasks.pk)]) == {'In archived'}

    def test_a_project_alone_is_a_criterion(self, project):
        assert self.params(projects=[str(project.pk)]).has_criteria
        assert not self.params(include_archived=True).has_criteria

    def test_a_deleted_project_matches_nothing(self, funded_user, project, tasks):
        params = self.params(projects=[str(project.pk)])
        projects.delete_project(project)

        assert not filter_tasks(funded_user, params).exists()
        assert describe_params(params, label_names(funded_user)) == 'in a deleted project'

    def test_reading_is_lenient_and_writing_canonical(self, project):
        params = FilterParams.from_dict(
            {'projects': ['NONE', 'nope', str(project.pk), str(project.pk)], 'include_archived': 1}
        )

        assert params.projects == tuple(sorted(['none', str(project.pk)]))
        assert params.include_archived
        assert FilterParams.from_dict(params.to_dict()) == params
        assert f'projects={project.pk}' in params.to_query()
        assert 'include_archived=1' in params.to_query()

    def test_a_filter_saved_before_projects_reads_back_unchanged(self):
        stored = FilterParams.from_dict({'priorities': [1]}).to_dict()

        assert 'projects' not in stored and 'include_archived' not in stored

    def test_the_summary_names_the_projects(self, funded_user, project):
        names = label_names(funded_user)

        assert (
            describe_params(self.params(projects=['none', str(project.pk)]), names)
            == 'in Website, no project'
        )
        assert (
            describe_params(self.params(priorities=[1], include_archived=True), names)
            == 'priority 1 · including archived projects'
        )

    def test_saved_filter_counts_follow_the_project(self, funded_user, project, tasks):
        saved = save_filter(funded_user, 'Site', {'projects': [str(project.pk)]})
        everything = save_filter(funded_user, 'P0', {'priorities': [0]})

        counts = filter_counts(funded_user, [saved, everything])

        assert counts == {saved.pk: 1, everything.pk: 2}


# -- The pages -----------------------------------------------------------------------------------


@pytest.fixture
def stranger():
    client = Client()
    client.force_login(make_user())
    return client


class TestProjectsPage:
    def test_the_page_lists_active_projects_with_their_counts(self, web, funded_user, project):
        create_task(funded_user, title='A', project=project)

        page = text(web.get(reverse('todo:projects')))

        assert 'Website' in page and '1 open · 0 closed' in page
        assert 'No archived projects.' not in page

    def test_the_archived_tab(self, web, project):
        projects.archive_project(project)

        active = text(web.get(reverse('todo:projects')))
        archived = text(web.get(reverse('todo:projects') + '?tab=archived'))

        assert 'No projects yet. Add your first below.' in active
        assert 'Website' in archived and 'Unarchive Website' in archived

    def test_adding_answers_the_list_and_the_sidebar(self, web, funded_user):
        response = web.post(reverse('todo:projects'), {'name': 'House', 'color': '#e9a23b'}, **HTMX)

        assert response.status_code == 200
        body = text(response)
        assert 'House' in body and 'id="sidebar-projects" hx-swap-oob="true"' in body
        assert Project.objects.get(user=funded_user).color == '#e9a23b'

    def test_a_duplicate_is_a_422_in_the_slot(self, web, project):
        response = web.post(reverse('todo:projects'), {'name': 'website'}, **HTMX)

        assert response.status_code == 422
        assert response['HX-Retarget'] == '#project-msg'
        assert 'Name: You already have a project with this name.' in text(response)

    def test_a_refused_form_spends_no_budget(self, web, settings):
        settings.RATELIMIT_ENABLE = True
        settings.RATELIMIT_RULES = {**settings.RATELIMIT_RULES, 'TODO_EDIT_USER': (1, 60)}

        assert web.post(reverse('todo:projects'), {'name': ''}, **HTMX).status_code == 422
        assert web.post(reverse('todo:projects'), {'name': 'A'}, **HTMX).status_code == 200
        assert web.post(reverse('todo:projects'), {'name': 'B'}, **HTMX).status_code == 429

    def test_renaming_from_the_list(self, web, project):
        url = reverse('todo:project-edit', args=[project.pk])

        response = web.post(url, {'name': 'Site', 'color': '#000000'}, **HTMX)

        assert response.status_code == 200
        project.refresh_from_db()
        assert (project.name, project.color) == ('Site', '#000000')

    def test_reorder(self, web, funded_user, project):
        other = projects.create_project(funded_user, 'Other')

        response = web.post(
            reverse('todo:project-reorder'), {'ids[]': [str(other.pk), str(project.pk)]}
        )

        assert response.status_code == 204
        assert [p.name for p in projects.list_projects(funded_user)] == ['Other', 'Website']

    def test_archive_unarchive_and_delete_redirect_with_a_message(self, web, funded_user, project):
        create_task(funded_user, title='A', project=project)

        archived = web.post(reverse('todo:project-archive', args=[project.pk]), follow=True)
        assert 'Archived Website.' in text(archived)

        back = web.post(reverse('todo:project-unarchive', args=[project.pk]), follow=True)
        assert 'Website is active again.' in text(back)

        deleted = web.post(reverse('todo:project-delete', args=[project.pk]), follow=True)
        assert 'Deleted the project Website. Its 1 task is kept, in no project.' in text(deleted)
        assert not Project.objects.exists()

    def test_another_users_project_is_a_404_everywhere(self, stranger, project):
        for name in ('project', 'project-edit', 'project-archive', 'project-unarchive'):
            url = reverse(f'todo:{name}', args=[project.pk])
            response = stranger.get(url) if name == 'project' else stranger.post(url, {'name': 'x'})
            assert response.status_code == 404, name
        assert stranger.post(reverse('todo:project-delete', args=[project.pk])).status_code == 404
        assert Project.objects.filter(pk=project.pk, archived_at__isnull=True).exists()

    def test_the_pages_need_a_session(self, client, project):
        for url in (reverse('todo:projects'), reverse('todo:project', args=[project.pk])):
            assert client.get(url).status_code == 302


class TestProjectPage:
    def test_it_shows_the_description_progress_and_open_tasks(self, web, funded_user, project):
        projects.update_project(project, description='Launch by **November**')
        create_task(funded_user, title='Open one', project=project)
        close_task(create_task(funded_user, title='Closed one', project=project))
        create_task(funded_user, title='Elsewhere')

        page = text(web.get(reverse('todo:project', args=[project.pk])))

        assert '<strong>November</strong>' in page
        assert '1 of 2 tasks closed' in page and '1 open · 1 closed' in page
        assert 'Open one' in page and 'Closed one' not in page and 'Elsewhere' not in page
        assert 'Show closed' in page

    def test_show_closed_lists_them_after_the_open_ones(self, web, funded_user, project):
        close_task(create_task(funded_user, title='Closed one', project=project))
        create_task(funded_user, title='Open one', project=project)

        page = text(web.get(reverse('todo:project', args=[project.pk]) + '?closed=1'))

        assert page.index('Open one') < page.index('Closed one')
        assert 'Hide closed' in page

    def test_the_sidebar_marks_it_and_rows_carry_no_project_chip(self, web, funded_user, project):
        task = create_task(funded_user, title='Open one', project=project)

        page = text(web.get(reverse('todo:project', args=[project.pk])))
        today = text(web.get(reverse('todo:all')))

        url = reverse('todo:project', args=[project.pk])
        assert f'<a href="{url}" aria-current="page"' in page
        assert f'<a href="{url}" title="Project"' not in page
        assert f'<a href="{url}" title="Project"' in today  # the chip, on another list
        assert f'data-url="{reverse("todo:move", args=[task.number])}"' in today

    def test_quick_add_files_the_task_in_the_project(self, web, funded_user, project):
        page = text(web.get(reverse('todo:project', args=[project.pk])))
        assert f'<option value="{project.pk}" selected>Website</option>' in page

        response = web.post(
            reverse('todo:create'),
            {'title': 'New one', 'project': str(project.pk), 'view': 'project'},
            **HTMX,
        )

        assert response['HX-Redirect'] == reverse('todo:project', args=[project.pk])
        assert Task.objects.get(title='New one').project == project

    def test_quick_add_there_into_no_project_reloads_the_same_page(self, web, project):
        page_url = reverse('todo:project', args=[project.pk])

        response = web.post(
            reverse('todo:create'),
            {'title': 'Loose one', 'project': '', 'view': 'project'},
            HTTP_REFERER=f'http://testserver{page_url}',
            **HTMX,
        )

        assert response['HX-Redirect'] == f'http://testserver{page_url}'
        assert Task.objects.get(title='Loose one').project is None

    def test_quick_add_into_an_unknown_project_is_a_422(self, web):
        response = web.post(
            reverse('todo:create'), {'title': 'New one', 'project': 'nope', 'view': 'today'}, **HTMX
        )

        assert response.status_code == 422
        assert 'Project: Unknown project.' in text(response)
        assert not Task.objects.exists()

    def test_an_archived_project_is_read_only(self, web, funded_user, project):
        create_task(funded_user, title='Open one', project=project)
        projects.archive_project(project)

        page = text(web.get(reverse('todo:project', args=[project.pk])))

        assert 'This project is archived.' in page
        assert 'id="quick-add-form"' not in page
        assert 'Unarchive' in page and 'Open one' in page

    def test_the_archive_question_names_what_stays_blocked(self, web, funded_user, project):
        blocker = create_task(funded_user, title='Inside', project=project)
        add_blocker(create_task(funded_user, title='Outside'), blocker)

        page = text(web.get(reverse('todo:project', args=[project.pk])))

        assert '1 task outside it is blocked by tasks in it and stays blocked.' in page

    def test_editing_from_the_page_redirects_back(self, web, project):
        response = web.post(
            reverse('todo:project-edit', args=[project.pk]),
            {'name': 'Site', 'color': '#5b8def', 'description': 'New text', 'ctx': 'page'},
            **HTMX,
        )

        assert response['HX-Redirect'] == reverse('todo:project', args=[project.pk])
        project.refresh_from_db()
        assert (project.name, project.description) == ('Site', 'New text')

    def test_a_row_action_keeps_the_row_on_the_page(self, web, funded_user, project):
        task = create_task(funded_user, title='Open one', project=project)

        response = web.post(
            reverse('todo:complete', args=[task.number]), {'view': 'project', 'ctx': 'row'}, **HTMX
        )

        assert f'id="task-{task.number}"' in text(response)

    def test_the_page_makes_a_bounded_number_of_queries(
        self, web, funded_user, project, django_assert_max_num_queries
    ):
        for index in range(12):
            create_task(funded_user, title=f'Task {index}', project=project)

        with django_assert_max_num_queries(30):
            web.get(reverse('todo:project', args=[project.pk]))


class TestMoveView:
    def test_from_a_row_it_redirects_back_with_a_message(self, web, funded_user, project):
        task = create_task(funded_user, title='A')

        response = web.post(
            reverse('todo:move', args=[task.number]), {'project': str(project.pk)}, follow=True
        )

        assert 'Moved A to Website.' in text(response)
        assert reload(task).project == project

    def test_from_the_task_page_it_answers_the_panel(self, web, funded_user, project):
        task = create_task(funded_user, title='A', project=project)

        response = web.post(
            reverse('todo:move', args=[task.number]), {'project': '', 'ctx': 'page'}, **HTMX
        )

        assert response.status_code == 200
        assert 'id="task-panel"' in text(response) and 'id="task-project-line"' not in text(
            response
        )
        assert response['HX-Trigger'] == 'taskChanged'
        assert reload(task).project is None

    def test_a_refusal_spends_no_budget(self, web, funded_user, project, settings):
        settings.RATELIMIT_ENABLE = True
        settings.RATELIMIT_RULES = {**settings.RATELIMIT_RULES, 'TODO_EDIT_USER': (1, 60)}
        task = create_task(funded_user, title='A')
        url = reverse('todo:move', args=[task.number])

        refused = web.post(url, {'project': 'nope', 'ctx': 'page'}, **HTMX)
        moved = web.post(url, {'project': str(project.pk), 'ctx': 'page'}, **HTMX)

        assert refused.status_code == 422 and 'Project: Unknown project.' in text(refused)
        assert moved.status_code == 200

    def test_another_users_task_is_a_404(self, stranger, funded_user, project):
        task = create_task(funded_user, title='A')

        response = stranger.post(reverse('todo:move', args=[task.number]), {'project': ''})

        assert response.status_code == 404


class TestTaskPageAndLists:
    def test_the_task_page_offers_the_projects(self, web, funded_user, project):
        task = create_task(funded_user, title='A', project=project)

        page = text(web.get(reverse('todo:task', args=[task.number])))

        assert 'id="task-project-form"' in page
        assert f'<option value="{project.pk}" selected>Website</option>' in page
        assert 'id="task-project-line"' in page

    def test_a_subtask_says_it_follows_its_parent(self, web, funded_user, project):
        parent = create_task(funded_user, title='Parent', project=project)
        sub = create_task(funded_user, title='Sub', parent=parent)

        page = text(web.get(reverse('todo:task', args=[sub.number])))

        assert 'id="task-project-form"' not in page
        assert ', with its parent' in page

    def test_a_task_of_an_archived_project_says_why_it_is_quiet(self, web, funded_user, project):
        task = create_task(funded_user, title='A', project=project)
        projects.archive_project(project)

        page = text(web.get(reverse('todo:task', args=[task.number])))

        assert 'id="archived-note"' in page
        assert "This task's project is archived" in page
        assert 'Website (archived)' in page

    def test_all_and_today_leave_archived_projects_out(self, web, funded_user, project, wednesday):
        create_task(funded_user, title='Hidden one', due_date=TODAY, project=project)
        create_task(funded_user, title='Shown one', due_date=TODAY)
        projects.archive_project(project)

        for name in ('todo:today', 'todo:all'):
            page = text(web.get(reverse(name)))
            assert 'Shown one' in page and 'Hidden one' not in page, name

    def test_the_sidebar_lists_projects_with_counts_and_the_archived_link(
        self, web, funded_user, project
    ):
        create_task(funded_user, title='A', project=project)
        projects.archive_project(projects.create_project(funded_user, 'Old'))

        page = text(web.get(reverse('todo:today')))

        assert 'id="sidebar-projects"' in page
        assert '1<span class="sr-only"> open task</span>' in page
        assert (
            '1 archived' in page
            and 'Old' not in page.split('id="sidebar-projects"')[1].split('</nav>')[0]
        )

    def test_the_search_page_filters_by_project(self, web, funded_user, project):
        create_task(funded_user, title='In website', project=project)
        create_task(funded_user, title='Loose')

        page = text(web.get(reverse('todo:search') + f'?projects={project.pk}'))

        assert 'In website' in page and '>Loose<' not in page
        assert f'name="projects" value="{project.pk}" checked' in page
        assert 'Include archived projects' in page

    def test_the_admin_lists_projects(self, admin_client, project):
        page = text(admin_client.get('/admin/todo/project/'))

        assert 'Website' in page


# -- The API ---------------------------------------------------------------------------------------


class TestApi:
    def test_the_plain_lists_leave_archived_projects_out(self, client, auth, user, config):
        from apps.billing.services import add_credits

        add_credits(user, 100, description='Test top-up', source='test')
        project = projects.create_project(user, 'Website')
        create_task(user, title='Hidden one', project=project)
        create_task(user, title='Shown one')
        projects.archive_project(project)

        body = get(client, '/tasks?view=all', auth).json()

        assert [task['title'] for task in body['results']] == ['Shown one']

    def test_a_saved_filter_carries_projects(self, client, auth, user):
        project = projects.create_project(user, 'Website')

        body = post(
            client,
            '/filters',
            auth,
            {'name': 'Site', 'params': {'projects': [str(project.pk), 'none']}},
        ).json()

        assert sorted(body['params']['projects']) == sorted([str(project.pk), 'none'])
        assert body['summary'] == 'in Website, no project'

    def test_a_malformed_project_id_is_a_422(self, client, auth):
        response = post(client, '/filters', auth, {'name': 'x', 'params': {'projects': ['nope']}})

        assert response.status_code == 422
