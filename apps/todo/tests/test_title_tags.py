"""`#project` and `@label` in a new task's title (`services.title_tags`)."""

import uuid
from datetime import date

import pytest
from django.urls import reverse

from ..models import InboundMessage, Project, Task, TaskSource
from ..services import create_task, inbound, title_tags
from ..services.accounts import inbox_address_for
from ..services.parsing import ParsedTitle, parse_title
from ..services.projects import archive_project, create_project
from ..services.storage import INBOUND_PREFIX
from ..templatetags.todo_ui import dark_mode_color
from .api_support import auth, post  # noqa: F401 -- `auth` is a fixture
from .factories import make_label
from .test_parsing import ask

pytestmark = pytest.mark.django_db

HTMX = {'HTTP_HX_REQUEST': 'true'}


@pytest.fixture
def wsa(funded_user) -> Project:
    return create_project(funded_user, 'WSA')


class TestRead:
    def test_a_project_and_labels_are_read_and_taken_out(self, funded_user, wsa):
        home = make_label(funded_user, 'home')
        work = make_label(funded_user, 'Work')

        tags = title_tags.read(funded_user, 'BSpacey integration #WSA @home @work')

        assert tags.title == 'BSpacey integration'
        assert tags.project == wsa
        assert tags.labels == [home, work]

    def test_an_unknown_tag_stays_as_text_and_creates_nothing(self, funded_user):
        tags = title_tags.read(funded_user, 'Call Bob #Nowhere @nobody')

        assert tags == title_tags.TitleTags(title='Call Bob #Nowhere @nobody')
        assert not Project.objects.exists()

    def test_names_are_compared_ignoring_case_spaces_and_dashes(self, funded_user):
        relaunch = create_project(funded_user, 'Website relaunch')

        for tag in ('#website-relaunch', '#WebsiteRelaunch', '#website_relaunch'):
            assert title_tags.read(funded_user, f'Mockups {tag}').project == relaunch

    def test_trailing_punctuation_is_not_part_of_the_name(self, funded_user, wsa):
        tags = title_tags.read(funded_user, 'Ship it #WSA.')

        assert tags.project == wsa
        assert tags.title == 'Ship it.'

    def test_a_tag_must_start_a_word(self, funded_user):
        make_label(funded_user, 'example.com')

        assert title_tags.read(funded_user, 'Email bob@example.com').labels == []

    def test_a_task_reference_is_never_a_project(self, funded_user):
        create_project(funded_user, '1234')

        tags = title_tags.read(funded_user, 'Follow up #1234')

        assert tags.project is None and tags.title == 'Follow up #1234'

    def test_the_first_project_wins_and_the_others_stay(self, funded_user, wsa):
        create_project(funded_user, 'Home')

        tags = title_tags.read(funded_user, 'Plan #WSA #Home')

        assert tags.project == wsa and tags.title == 'Plan #Home'

    def test_an_archived_project_is_not_read(self, funded_user, wsa):
        archive_project(wsa)

        assert title_tags.read(funded_user, 'Plan #WSA').title == 'Plan #WSA'

    def test_two_projects_alike_are_ambiguous_unless_one_is_exact(self, funded_user):
        create_project(funded_user, 'web-site')
        create_project(funded_user, 'web_site')

        assert title_tags.read(funded_user, 'x #website').project is None
        exact = title_tags.read(funded_user, 'x #web-site').project
        assert exact is not None and exact.name == 'web-site'

    def test_another_users_project_is_not_read(self, funded_user, other_user):
        create_project(other_user, 'WSA')

        assert title_tags.read(funded_user, 'x #WSA').project is None

    def test_a_title_of_tags_alone_is_kept_whole(self, funded_user, wsa):
        tags = title_tags.read(funded_user, '#WSA')

        assert tags.title == '#WSA' and tags.project == wsa

    def test_projects_can_be_left_alone(self, funded_user, wsa):
        home = make_label(funded_user, 'home')

        tags = title_tags.read(funded_user, 'x #WSA @home', projects=False)

        assert tags.title == 'x #WSA' and tags.project is None and tags.labels == [home]

    def test_no_query_without_a_tag(self, funded_user, django_assert_num_queries):
        with django_assert_num_queries(0):
            title_tags.read(funded_user, 'Buy milk')


class TestKeepIn:
    def test_tags_the_model_dropped_are_put_back(self):
        assert title_tags.keep_in('pay rent tomorrow #WSA @home', 'pay rent') == (
            'pay rent #WSA @home'
        )

    def test_tags_still_there_are_not_repeated(self):
        assert title_tags.keep_in('pay rent tomorrow #WSA', 'pay rent #WSA') == 'pay rent #WSA'

    def test_the_date_reader_keeps_them(self, fake_title_parser):
        fake_title_parser.replies['pay rent tomorrow #WSA'] = ParsedTitle(
            has_date=True,
            clean_title='pay rent',
            due_date=date(2026, 10, 29),
            matched_text='tomorrow',
            confidence=0.9,
        )

        assert parse_title(ask('pay rent tomorrow #WSA')).clean_title == 'pay rent #WSA'


class TestQuickAdd:
    def test_the_title_files_the_task_and_labels_it(self, web, funded_user, wsa):
        home = make_label(funded_user, 'home')

        response = web.post(
            reverse('todo:create'), {'title': 'Integration #WSA @home', 'view': 'today'}, **HTMX
        )

        assert response.status_code == 200
        task = Task.objects.get()
        assert task.title == 'Integration' and task.project == wsa
        assert list(task.labels.all()) == [home]

    def test_a_label_also_ticked_is_added_once(self, web, funded_user):
        home = make_label(funded_user, 'home')

        web.post(
            reverse('todo:create'),
            {'title': 'x @home', 'labels': [str(home.pk)], 'view': 'today'},
            **HTMX,
        )

        assert list(Task.objects.get().labels.all()) == [home]

    def test_the_project_select_wins_over_the_title(self, web, funded_user, wsa):
        other = create_project(funded_user, 'Other')

        web.post(
            reverse('todo:create'),
            {'title': 'x #WSA', 'project': str(other.pk), 'view': 'today'},
            **HTMX,
        )

        task = Task.objects.get()
        assert task.project == other and task.title == 'x #WSA'

    def test_an_unknown_project_is_kept_as_text(self, web):
        web.post(reverse('todo:create'), {'title': 'x #Later', 'view': 'today'}, **HTMX)

        task = Task.objects.get()
        assert task.title == 'x #Later' and task.project is None

    def test_a_subtask_takes_labels_only(self, web, funded_user, wsa):
        home = make_label(funded_user, 'home')
        parent = create_task(funded_user, title='Parent')

        web.post(reverse('todo:subtask-add', args=[parent.number]), {'title': 'Sub #WSA @home'})

        sub = Task.objects.get(parent=parent)
        assert sub.title == 'Sub #WSA' and sub.project is None
        assert list(sub.labels.all()) == [home]


class TestPills:
    """The quick-add form carries what static/todo/js/title_tags.js matches tags against."""

    def test_the_quick_add_form_offers_its_labels_and_projects(self, web, funded_user, wsa):
        make_label(funded_user, 'home', color='#0000ff')

        html = web.get(reverse('todo:all')).content.decode()

        assert 'data-title-tags' in html and 'id="quick-tag-pills"' in html
        assert 'data-tag-name="home" data-tag-color="#0000ff"' in html
        # The dark-mode colour of a label a script draws is the server's, like `label_chip`'s.
        assert f'data-tag-dark="{dark_mode_color("#0000ff")}"' in html
        assert dark_mode_color('#0000ff') != '#0000ff'
        assert f'value="{wsa.pk}" data-tag-color="{wsa.color}"' in html
        assert '<span data-labels-summary>Add labels</span>' in html
        assert 'todo/js/title_tags.js' in html

    def test_a_label_name_is_escaped(self, web, funded_user):
        make_label(funded_user, '"><b>x')

        html = web.get(reverse('todo:all')).content.decode()

        assert 'data-tag-name="&quot;&gt;&lt;b&gt;x"' in html

    def test_the_subtask_form_offers_the_labels_to_complete(self, web, funded_user):
        make_label(funded_user, 'home', color='#336699')
        parent = create_task(funded_user, title='Parent')

        html = web.get(reverse('todo:task', args=[parent.number])).content.decode()

        assert 'id="subtask-title"' in html and 'data-tag-input' in html
        assert 'id="subtask-tag-pills"' in html
        assert (
            '<span data-tag-option data-tag-name="home" data-tag-color="#336699" '
            f'data-tag-dark="{dark_mode_color("#336699")}"></span>'
        ) in html

    def test_a_completed_tag_is_read_when_the_subtask_is_created(self, web, funded_user):
        # The subtask pill writes the label's name with `-` for its spaces, as `title_tags` reads it.
        relaunch = make_label(funded_user, 'Website relaunch')
        parent = create_task(funded_user, title='Parent')

        web.post(
            reverse('todo:subtask-add', args=[parent.number]),
            {'title': 'Mockups @Website-relaunch '},
        )

        sub = Task.objects.get(parent=parent)
        assert sub.title == 'Mockups' and list(sub.labels.all()) == [relaunch]


class TestApi:
    def test_the_title_files_the_task_and_labels_it(self, client, auth, funded_user, wsa):
        home = make_label(funded_user, 'home')

        response = post(client, '/tasks', auth, {'title': 'Integration #WSA @home'})

        assert response.status_code == 201, response.content
        body = response.json()
        assert body['title'] == 'Integration'
        assert body['project']['id'] == str(wsa.pk)
        assert [label['id'] for label in body['labels']] == [str(home.pk)]

    def test_project_in_the_body_wins(self, client, auth, funded_user, wsa):
        other = create_project(funded_user, 'Other')

        body = post(client, '/tasks', auth, {'title': 'x #WSA', 'project': str(other.pk)}).json()

        assert body['title'] == 'x #WSA' and body['project']['id'] == str(other.pk)

    def test_labels_in_the_body_and_the_title_are_merged(self, client, auth, funded_user):
        home = make_label(funded_user, 'home')
        work = make_label(funded_user, 'work')

        body = post(
            client, '/tasks', auth, {'title': 'x @work @home', 'labels': [str(home.pk)]}
        ).json()

        assert {label['id'] for label in body['labels']} == {str(home.pk), str(work.pk)}


class TestEmail:
    def test_the_subject_files_the_task(self, funded_user, wsa, fake_storage):
        from .test_inbound import mail

        key = f'{INBOUND_PREFIX}{uuid.uuid4()}'
        fake_storage.seed(key, mail(inbox_address_for(funded_user), subject='Invoice #WSA'))
        message = InboundMessage.objects.create(s3_key=key)

        inbound.ingest(message.pk)

        task = Task.objects.get(source=TaskSource.EMAIL)
        assert task.title == 'Invoice' and task.project == wsa
