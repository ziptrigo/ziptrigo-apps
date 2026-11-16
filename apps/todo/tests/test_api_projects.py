"""The project endpoints of the JWT API (`/api/todo/projects`, issue #314 part 3), and a task's
`project` / `section` on the task endpoints."""

import pytest

from apps.billing.services import add_credits

from ..models import Project, Section, Task
from ..services import close_task, create_task, projects
from .api_support import auth, delete, get, patch, post  # noqa: F401 -- `auth` is a fixture
from .factories import make_user

pytestmark = pytest.mark.django_db


@pytest.fixture
def funded(user, config):
    add_credits(user, 100, description='Test top-up', source='test')
    return user


@pytest.fixture
def project(funded) -> Project:
    return projects.create_project(funded, 'Website', '#5b8def', 'Launch by **November**')


class TestProjects:
    def test_every_route_needs_a_token(self, client, project):
        assert client.get('/api/todo/projects').status_code == 401
        assert client.get(f'/api/todo/projects/{project.pk}').status_code == 401

    def test_create_and_list(self, client, auth, funded):
        created = post(client, '/projects', auth, {'name': ' House  move ', 'color': '#E9A23B'})

        assert created.status_code == 201
        body = created.json()
        assert (body['name'], body['color']) == ('House move', '#e9a23b')
        assert body['archived_at'] is None and body['sections'] == []
        assert (body['open_count'], body['closed_count']) == (0, 0)
        assert [p['name'] for p in get(client, '/projects', auth).json()] == ['House move']

    def test_the_fields_of_a_project(self, client, auth, funded, project):
        projects.create_section(project, 'Design')
        close_task(create_task(funded, title='Done', project=project))
        create_task(funded, title='Open', project=project)

        body = get(client, f'/projects/{project.pk}', auth).json()

        assert set(body) == {
            'id',
            'name',
            'color',
            'description',
            'description_html',
            'position',
            'archived_at',
            'open_count',
            'closed_count',
            'sections',
            'created_at',
        }
        assert '<strong>November</strong>' in body['description_html']
        assert (body['open_count'], body['closed_count']) == (1, 1)
        assert [s['name'] for s in body['sections']] == ['Design']

    @pytest.mark.parametrize(
        ('body', 'message'),
        [
            ({'name': ' '}, 'name: Name is required.'),
            ({'name': 'x' * 81}, 'name: Name is too long (at most 80 characters).'),
            ({'name': 'A', 'color': 'red'}, 'color: Use a colour like #8fa89e.'),
            ({'name': 'website'}, 'name: You already have a project with this name.'),
        ],
    )
    def test_a_refused_create_is_a_400(self, client, auth, project, body, message):
        response = post(client, '/projects', auth, body)

        assert response.status_code == 400
        assert response.json()['detail'] == message

    def test_an_unknown_key_is_a_422(self, client, auth, funded):
        assert (
            post(client, '/projects', auth, {'name': 'A', 'colour': '#000000'}).status_code == 422
        )

    def test_archived_filter(self, client, auth, funded, project):
        projects.archive_project(projects.create_project(funded, 'Old'))

        def names(query=''):
            return [p['name'] for p in get(client, f'/projects{query}', auth).json()]

        assert names() == ['Website']
        assert names('?archived=true') == ['Old']
        assert names('?archived=all') == ['Website', 'Old']
        assert get(client, '/projects?archived=maybe', auth).status_code == 400

    def test_update_only_what_is_sent(self, client, auth, project):
        body = patch(client, f'/projects/{project.pk}', auth, {'name': 'Site'}).json()

        assert (body['name'], body['color']) == ('Site', '#5b8def')
        assert body['description'] == 'Launch by **November**'
        cleared = patch(client, f'/projects/{project.pk}', auth, {'description': None}).json()
        assert cleared['description'] == '' and cleared['description_html'] == ''
        assert patch(client, f'/projects/{project.pk}', auth, {'name': None}).status_code == 400

    def test_an_empty_patch_spends_nothing(self, client, auth, project, settings):
        settings.RATELIMIT_ENABLE = True
        settings.RATELIMIT_RULES = {**settings.RATELIMIT_RULES, 'TODO_EDIT_USER': (1, 60)}
        url = f'/projects/{project.pk}'

        assert patch(client, url, auth, {}).status_code == 200
        assert patch(client, url, auth, {'name': ' '}).status_code == 400
        assert patch(client, url, auth, {'name': 'Site'}).status_code == 200
        assert patch(client, url, auth, {'name': 'Other'}).status_code == 429

    def test_archive_and_unarchive_are_idempotent(self, client, auth, project):
        url = f'/projects/{project.pk}'

        archived = post(client, f'{url}/archive', auth, {}).json()
        again = post(client, f'{url}/archive', auth, {}).json()
        assert archived['archived_at'] and again['archived_at'] == archived['archived_at']

        back = post(client, f'{url}/unarchive', auth, {}).json()
        assert back['archived_at'] is None
        assert post(client, f'{url}/unarchive', auth, {}).status_code == 200

    def test_delete_keeps_the_tasks(self, client, auth, funded, project):
        task = create_task(funded, title='A', project=project)

        assert delete(client, f'/projects/{project.pk}', auth).status_code == 204

        assert not Project.objects.exists()
        assert Task.objects.get(pk=task.pk).project is None
        assert delete(client, f'/projects/{project.pk}', auth).status_code == 404

    def test_reorder(self, client, auth, funded, project):
        other = projects.create_project(funded, 'Other')

        body = post(
            client, '/projects/reorder', auth, {'ids': [str(other.pk), str(project.pk)]}
        ).json()

        assert [p['name'] for p in body] == ['Other', 'Website']

    def test_another_users_project_is_a_404(self, client, auth, funded):
        theirs = projects.create_project(make_user(), 'Theirs')
        base = f'/projects/{theirs.pk}'

        assert get(client, base, auth).status_code == 404
        assert patch(client, base, auth, {'name': 'x'}).status_code == 404
        assert delete(client, base, auth).status_code == 404
        for action in ('archive', 'unarchive', 'sections', 'sections/reorder', 'tasks/reorder'):
            body = {'name': 'x'} if action == 'sections' else {'ids': []}
            assert post(client, f'{base}/{action}', auth, body).status_code == 404, action
        assert Project.objects.filter(pk=theirs.pk, name='Theirs').exists()

    def test_the_list_is_three_queries(self, client, auth, funded, django_assert_max_num_queries):
        for index in range(6):
            made = projects.create_project(funded, f'P{index}')
            projects.create_section(made, 'One')

        with django_assert_max_num_queries(4):  # the token's user, the projects, their sections
            assert len(get(client, '/projects', auth).json()) == 6


class TestSections:
    def test_add_rename_reorder_and_delete(self, client, auth, funded, project):
        base = f'/projects/{project.pk}/sections'

        added = post(client, base, auth, {'name': 'Design'})
        assert added.status_code == 201
        post(client, base, auth, {'name': 'Content'})
        design, content = Section.objects.filter(project=project).order_by('position')

        assert post(client, base, auth, {'name': 'design'}).json()['detail'] == (
            'name: This project already has a section with this name.'
        )
        renamed = patch(client, f'{base}/{content.pk}', auth, {'name': 'Copy'}).json()
        assert [s['name'] for s in renamed['sections']] == ['Design', 'Copy']

        ordered = post(
            client, f'{base}/reorder', auth, {'ids': [str(content.pk), str(design.pk)]}
        ).json()
        assert [s['name'] for s in ordered['sections']] == ['Copy', 'Design']

        task = create_task(funded, title='A', project=project, section=design)
        assert delete(client, f'{base}/{design.pk}', auth).status_code == 204
        fresh = Task.objects.get(pk=task.pk)
        assert fresh.section is None and fresh.project == project

    def test_a_section_of_another_project_is_a_404(self, client, auth, funded, project):
        other = projects.create_project(funded, 'Other')
        section = projects.create_section(other, 'Theirs')
        url = f'/projects/{project.pk}/sections/{section.pk}'

        assert patch(client, url, auth, {'name': 'x'}).status_code == 404
        assert delete(client, url, auth).status_code == 404

    def test_an_archived_projects_sections_are_a_400(self, client, auth, project):
        section = projects.create_section(project, 'Design')
        projects.archive_project(project)
        base = f'/projects/{project.pk}/sections'
        detail = 'Unarchive the project to change its sections.'

        assert post(client, base, auth, {'name': 'New'}).json()['detail'] == detail
        assert patch(client, f'{base}/{section.pk}', auth, {'name': 'x'}).json()['detail'] == detail
        assert delete(client, f'{base}/{section.pk}', auth).json()['detail'] == detail

    def test_reorder_tasks_of_one_list(self, client, auth, funded, project):
        design = projects.create_section(project, 'Design')
        a = create_task(funded, title='A', project=project)
        b = create_task(funded, title='B', project=project)
        url = f'/projects/{project.pk}/tasks/reorder'

        moved = post(client, url, auth, {'section': str(design.pk), 'ids': [str(b.pk), str(a.pk)]})

        assert moved.json() == {'count': 2}
        listed = get(client, f'/tasks?project={project.pk}&sort=manual', auth).json()['results']
        assert [task['title'] for task in listed] == ['B', 'A']
        assert {task['section']['name'] for task in listed} == {'Design'}
        bad = post(client, url, auth, {'section': str(project.pk), 'ids': []})
        assert bad.status_code == 400 and bad.json()['detail'] == 'section: Unknown section.'


class TestTaskProject:
    def test_a_task_carries_its_project_and_section(self, client, auth, funded, project):
        design = projects.create_section(project, 'Design')
        task = create_task(funded, title='A', project=project, section=design)
        loose = create_task(funded, title='B')

        body = get(client, f'/tasks/{task.number}', auth).json()

        assert body['project'] == {
            'id': str(project.pk),
            'name': 'Website',
            'color': '#5b8def',
            'archived': False,
        }
        assert body['section'] == {'id': str(design.pk), 'name': 'Design'}
        other = get(client, f'/tasks/{loose.number}', auth).json()
        assert other['project'] is None and other['section'] is None

    def test_create_in_a_project_and_section(self, client, auth, funded, project):
        design = projects.create_section(project, 'Design')

        created = post(
            client,
            '/tasks',
            auth,
            {'title': 'New', 'project': str(project.pk), 'section': str(design.pk)},
        )

        assert created.status_code == 201
        assert created.json()['project']['name'] == 'Website'
        assert created.json()['section']['name'] == 'Design'

    @pytest.mark.parametrize('field', ['project', 'section'])
    def test_an_id_that_is_not_yours_is_a_400_and_spends_nothing(
        self, client, auth, funded, project, field, settings
    ):
        settings.RATELIMIT_ENABLE = True
        settings.RATELIMIT_RULES = {**settings.RATELIMIT_RULES, 'TODO_CREATE_USER': (1, 60)}
        unknown = '7b0e3c1e-0000-4000-8000-000000000000'
        body = {'title': 'New', 'project': str(project.pk), field: unknown}

        refused = post(client, '/tasks', auth, body)

        assert refused.status_code == 400
        assert refused.json()['detail'] == f'{field}: Unknown {field}.'
        assert not Task.objects.exists()
        assert post(client, '/tasks', auth, {'title': 'New'}).status_code == 201

    def test_patch_moves_between_projects_and_sections(self, client, auth, funded, project):
        design = projects.create_section(project, 'Design')
        other = projects.create_project(funded, 'Other')
        task = create_task(funded, title='A')
        sub = create_task(funded, title='Sub', parent=task)
        url = f'/tasks/{task.number}'

        both = patch(
            client, url, auth, {'project': str(project.pk), 'section': str(design.pk)}
        ).json()
        assert both['project']['name'] == 'Website' and both['section']['name'] == 'Design'
        assert Task.objects.get(pk=sub.pk).section == design

        moved = patch(client, url, auth, {'project': str(other.pk)}).json()
        assert moved['project']['name'] == 'Other' and moved['section'] is None

        out = patch(client, url, auth, {'project': None}).json()
        assert out['project'] is None

    def test_patch_refusals(self, client, auth, funded, project):
        design = projects.create_section(project, 'Design')
        loose = create_task(funded, title='Loose')
        sub = create_task(funded, title='Sub', parent=create_task(funded, title='P'))

        def detail(task, body):
            response = patch(client, f'/tasks/{task.number}', auth, body)
            assert response.status_code == 400
            return response.json()['detail']

        assert detail(loose, {'section': str(design.pk)}) == 'section: Unknown section.'
        assert detail(sub, {'project': str(project.pk)}) == (
            "project: A subtask is in its parent's project. Move the parent instead."
        )
        assert detail(sub, {'section': str(design.pk)}) == (
            "section: A subtask is in its parent's section. Move the parent instead."
        )
        projects.archive_project(project)
        assert detail(loose, {'project': str(project.pk)}) == (
            'project: This project is archived. Unarchive it to add tasks to it.'
        )

    def test_the_values_it_has_spend_nothing(self, client, auth, funded, project, settings):
        task = create_task(funded, title='A', project=project)
        settings.RATELIMIT_ENABLE = True
        settings.RATELIMIT_RULES = {**settings.RATELIMIT_RULES, 'TODO_EDIT_USER': (1, 60)}
        url = f'/tasks/{task.number}'
        same = {'project': str(project.pk), 'section': None}

        assert patch(client, url, auth, same).status_code == 200
        assert patch(client, url, auth, same).status_code == 200
        assert patch(client, url, auth, {'project': None}).status_code == 200
        assert patch(client, url, auth, {'project': str(project.pk)}).status_code == 429


class TestListFilter:
    @pytest.fixture
    def tasks(self, funded, project):
        old = projects.create_project(funded, 'Old')
        create_task(funded, title='In website', project=project)
        create_task(funded, title='In old', project=old)
        create_task(funded, title='Loose')
        projects.archive_project(old)
        return old

    def titles(self, client, auth, query) -> set[str]:
        response = get(client, f'/tasks?{query}', auth)
        assert response.status_code == 200, response.content
        return {task['title'] for task in response.json()['results']}

    def test_project_and_none(self, client, auth, project, tasks):
        assert self.titles(client, auth, f'project={project.pk}') == {'In website'}
        assert self.titles(client, auth, 'project=none') == {'Loose'}
        assert self.titles(client, auth, f'project=none,{project.pk}') == {'In website', 'Loose'}

    def test_archived_projects(self, client, auth, project, tasks):
        assert self.titles(client, auth, 'view=all') == {'In website', 'Loose'}
        assert self.titles(client, auth, 'include_archived=true') == {
            'In website',
            'In old',
            'Loose',
        }
        assert self.titles(client, auth, f'project={tasks.pk}') == {'In old'}

    def test_bad_values(self, client, auth, project, tasks):
        assert get(client, '/tasks?project=nope', auth).status_code == 400
        manual = get(client, '/tasks?sort=manual', auth)
        assert manual.status_code == 400
        assert manual.json()['detail'] == 'sort: manual needs project to be exactly one project id.'
        assert (
            get(client, f'/tasks?project={project.pk}&sort=manual&dir=desc', auth).status_code
            == 400
        )

    def test_a_page_of_tasks_in_projects_costs_no_query_per_task(
        self, client, auth, funded, project, django_assert_max_num_queries
    ):
        design = projects.create_section(project, 'Design')
        for index in range(30):
            create_task(funded, title=f'T{index}', project=project, section=design)

        with django_assert_max_num_queries(12):
            assert get(client, f'/tasks?project={project.pk}&limit=100', auth).json()['count'] == 30


class TestPatchPlanning:
    """`PATCH /tasks/{number}` judges `project` and `section` against the task as the same request
    leaves it, and the values it will have anyway are a no-op."""

    def test_the_values_it_has_never_refuse_the_rest(self, client, auth, funded, project):
        task = create_task(funded, title='A', project=project)
        sub = create_task(funded, title='Sub', parent=task)
        projects.archive_project(project)

        archived = patch(
            client, f'/tasks/{task.number}', auth, {'title': 'B', 'project': str(project.pk)}
        )
        echoed = patch(
            client,
            f'/tasks/{sub.number}',
            auth,
            {'title': 'Sub B', 'project': str(project.pk), 'section': None},
        )

        assert archived.status_code == 200 and archived.json()['title'] == 'B'
        assert echoed.status_code == 200 and echoed.json()['title'] == 'Sub B'

    def test_made_top_level_and_moved_in_one_request(self, client, auth, funded, project):
        other = projects.create_project(funded, 'Other')
        parent = create_task(funded, title='P', project=project)
        sub = create_task(funded, title='Sub', parent=parent)

        body = patch(
            client, f'/tasks/{sub.number}', auth, {'parent': None, 'project': str(other.pk)}
        )

        assert body.status_code == 200
        assert body.json()['parent'] is None and body.json()['project']['name'] == 'Other'

    def test_a_new_parent_with_another_project_is_refused_before_the_budget(
        self, client, auth, funded, project, settings
    ):
        other = projects.create_project(funded, 'Other')
        parent = create_task(funded, title='P', project=project)
        task = create_task(funded, title='A')
        settings.RATELIMIT_ENABLE = True
        settings.RATELIMIT_RULES = {**settings.RATELIMIT_RULES, 'TODO_EDIT_USER': (1, 60)}
        url = f'/tasks/{task.number}'

        refused = patch(client, url, auth, {'parent': parent.number, 'project': str(other.pk)})
        followed = patch(client, url, auth, {'parent': parent.number, 'project': str(project.pk)})

        assert refused.status_code == 400
        assert refused.json()['detail'].startswith("project: A subtask is in its parent's project")
        assert followed.status_code == 200 and followed.json()['project']['name'] == 'Website'

    def test_a_section_change_in_an_archived_project_is_a_400(self, client, auth, funded, project):
        design = projects.create_section(project, 'Design')
        task = create_task(funded, title='A', project=project)
        projects.archive_project(project)

        response = patch(client, f'/tasks/{task.number}', auth, {'section': str(design.pk)})

        assert response.status_code == 400
        assert response.json()['detail'] == 'Unarchive the project to change its sections.'

    def test_today_never_lists_an_archived_project_even_when_named(
        self, client, auth, funded, project
    ):
        from apps.accounts.services.timezones import user_today

        create_task(funded, title='Due', project=project, due_date=user_today(funded))
        projects.archive_project(project)

        today = get(client, f'/tasks?view=today&project={project.pk}', auth).json()
        everything = get(client, f'/tasks?view=all&project={project.pk}', auth).json()

        assert today['count'] == 0 and everything['count'] == 1


class TestBudgetAndOrder:
    def test_refusals_that_need_no_write_spend_nothing(
        self, client, auth, funded, project, settings
    ):
        projects.create_section(project, 'Design')
        settings.RATELIMIT_ENABLE = True
        settings.RATELIMIT_RULES = {**settings.RATELIMIT_RULES, 'TODO_EDIT_USER': (1, 60)}
        base = f'/projects/{project.pk}'

        assert post(client, '/projects', auth, {'name': 'website'}).status_code == 400
        assert post(client, f'{base}/sections', auth, {'name': 'design'}).status_code == 400
        assert post(client, '/projects/reorder', auth, {'ids': []}).status_code == 200
        assert post(client, f'{base}/sections/reorder', auth, {'ids': []}).status_code == 200
        assert post(client, f'{base}/tasks/reorder', auth, {'ids': []}).json() == {'count': 0}
        assert post(client, '/projects', auth, {'name': 'New'}).status_code == 201
        assert post(client, '/projects', auth, {'name': 'Newer'}).status_code == 429

    def test_archived_sections_cannot_be_reordered(self, client, auth, project):
        section = projects.create_section(project, 'Design')
        projects.archive_project(project)

        response = post(
            client, f'/projects/{project.pk}/sections/reorder', auth, {'ids': [str(section.pk)]}
        )

        assert response.status_code == 400

    def test_a_partial_reorder_makes_no_ties(self, client, auth, funded, project):
        tasks = [create_task(funded, title=title, project=project) for title in 'ABCDE']
        url = f'/projects/{project.pk}/tasks/reorder'
        post(client, url, auth, {'ids': [str(task.pk) for task in tasks]})

        # Only D and B are sent, swapped: they trade the places they hold; the rest keep theirs.
        post(client, url, auth, {'ids': [str(tasks[3].pk), str(tasks[1].pk)]})

        listed = get(client, f'/tasks?project={project.pk}&sort=manual', auth).json()['results']
        assert [task['title'] for task in listed] == ['A', 'D', 'C', 'B', 'E']
        positions = [task['project_position'] for task in listed]
        assert positions == sorted(set(positions))

    def test_a_subtask_sits_at_its_parents_place(self, client, auth, funded, project):
        a = create_task(funded, title='A', project=project)
        create_task(funded, title='B', project=project)
        create_task(funded, title='Sub of A', parent=a)

        listed = get(client, f'/tasks?project={project.pk}&sort=manual', auth).json()['results']

        assert [task['title'] for task in listed] == ['A', 'Sub of A', 'B']

    def test_one_project_is_read_once(self, client, auth, project, django_assert_max_num_queries):
        with django_assert_max_num_queries(4):  # the token's user, the project, its sections
            assert get(client, f'/projects/{project.pk}', auth).status_code == 200
