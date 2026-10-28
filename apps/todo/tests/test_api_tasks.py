"""The To-do JWT API's task endpoints (`/api/todo/tasks`): create, list and search, read, edit, the
state changes, ordering. Over Django's test client, against the real URLs."""

from datetime import date, time, timedelta
from typing import Any

import pytest

from apps.billing.services import get_balance, spend_credits

from ..models import Reminder, Task, TodoAccount
from ..services import (
    add_comment,
    complete_task,
    create_task,
    get_account,
    reorder,
    trash_task,
)
from ..services.parsing import ParsedTitle, ParseUnavailable
from .api_support import (  # noqa: F401 -- the fixtures are used by name
    auth,
    bearer,
    delete,
    get,
    limit_rule,
    other_auth,
    patch,
    post,
)
from .factories import make_attachment, make_label, make_user

pytestmark = [pytest.mark.django_db, pytest.mark.unit]

TASK_KEYS = {
    'id',
    'number',
    'title',
    'description',
    'description_html',
    'priority',
    'due_date',
    'due_time',
    'due_at',
    'completed_at',
    'deleted_at',
    'position',
    'source',
    'parent',
    'labels',
    'recurrence',
    'reminders',
    'subtask_count',
    'open_subtask_count',
    'comment_count',
    'attachment_count',
    'charged_credits',
    'unpaid',
    'created_at',
    'updated_at',
}


def numbers(response) -> list[int]:
    return [task['number'] for task in response.json()['results']]


def new_task(client, auth, title='Task', **fields) -> dict:
    response = post(client, '/tasks', auth, {'title': title, **fields})
    assert response.status_code == 201, response.content
    return response.json()


class TestAuth:
    def test_every_task_endpoint_needs_a_token(self, client, funded_user):
        task = create_task(funded_user, title='x')

        assert client.get('/api/todo/tasks').status_code == 401
        assert client.get(f'/api/todo/tasks/{task.number}').status_code == 401
        assert (
            client.post('/api/todo/tasks', '{}', content_type='application/json').status_code == 401
        )

    def test_an_inactive_user_is_refused(self, client, funded_user, auth):
        funded_user.status = 'INACTIVE'
        funded_user.save()

        assert get(client, '/tasks', auth).status_code == 401


class TestCreate:
    def test_a_minimal_task(self, client, auth, funded_user, wednesday):
        response = post(client, '/tasks', auth, {'title': '  Buy milk '})

        assert response.status_code == 201
        body = response.json()
        assert set(body) == TASK_KEYS | {'parsed'}
        assert body['title'] == 'Buy milk' and body['number'] == 1000
        assert body['source'] == 'api' and body['parent'] is None
        assert body['priority'] == 0 and body['due_date'] is None and body['due_at'] is None
        assert body['recurrence'] is None and body['reminders'] == [] and body['labels'] == []
        assert body['subtask_count'] == body['comment_count'] == body['attachment_count'] == 0
        assert body['unpaid'] is False and body['parsed'] is None
        assert Task.objects.get(number=1000).source == 'api'

    def test_every_field(self, client, auth, funded_user, wednesday):
        label = make_label(funded_user, 'Home', color='#336699')

        body = new_task(
            client,
            auth,
            'Pay rent',
            description='See **terms** and #1001',
            priority=2,
            due_date='2026-11-01',
            due_time='09:30',
            labels=[str(label.pk)],
            rrule='FREQ=MONTHLY;BYMONTHDAY=1',
            recurrence_mode='from_completion',
        )

        assert body['priority'] == 2
        assert body['due_date'] == '2026-11-01' and body['due_time'] == '09:30:00'
        assert body['due_at'].startswith('2026-11-01T09:30:00')
        assert body['labels'] == [{'id': str(label.pk), 'name': 'Home', 'color': '#336699'}]
        assert body['recurrence']['rrule'] == 'FREQ=MONTHLY;BYMONTHDAY=1'
        assert body['recurrence']['mode'] == 'from_completion'
        assert body['recurrence']['rrule_text'] == 'Every month on the 1st'
        assert body['recurrence']['ended_at'] is None
        assert '<strong>terms</strong>' in body['description_html']

    def test_the_default_reminder_when_none_are_given(self, client, auth, wednesday):
        body = new_task(client, auth, 'With a date', due_date='2026-11-01')

        assert [(r['minutes_before'], r['channel']) for r in body['reminders']] == [(5, 'in_app')]

    def test_reminders_replace_the_default(self, client, auth, wednesday):
        body = new_task(
            client,
            auth,
            'With reminders',
            due_date='2026-11-01',
            reminders=[
                {'minutes_before': 60, 'channel': 'email'},
                {'minutes_before': 1440},
            ],
        )

        assert [(r['minutes_before'], r['channel']) for r in body['reminders']] == [
            (1440, 'in_app'),
            (60, 'email'),
        ]

    def test_an_empty_list_means_no_reminders(self, client, auth, wednesday):
        body = new_task(client, auth, 'No reminders', due_date='2026-11-01', reminders=[])

        assert body['reminders'] == []

    def test_a_bad_reminder_creates_nothing_and_charges_nothing(
        self, client, auth, funded_user, wednesday
    ):
        for _ in range(2):  # the free ones
            create_task(funded_user, title='free')
        before = get_balance(funded_user)

        response = post(
            client,
            '/tasks',
            auth,
            {'title': 'x', 'reminders': [{'minutes_before': 5}]},  # no date: refused
        )

        assert response.status_code == 400
        assert 'due date' in response.json()['detail']
        assert not Task.objects.filter(title='x').exists()
        assert get_balance(funded_user) == before
        assert get_account(funded_user).next_task_number == 1002

    def test_a_subtask(self, client, auth, funded_user):
        parent = create_task(funded_user, title='Parent')

        body = new_task(client, auth, 'Child', parent=parent.number)

        assert body['parent'] == parent.number
        detail = get(client, f'/tasks/{parent.number}', auth).json()
        assert detail['subtask_count'] == 1 and detail['open_subtask_count'] == 1

    def test_a_subtask_of_a_subtask_is_a_400(self, client, auth, funded_user):
        parent = create_task(funded_user, title='Parent')
        child = create_task(funded_user, title='Child', parent=parent)

        response = post(client, '/tasks', auth, {'title': 'x', 'parent': child.number})

        assert response.status_code == 400
        assert response.json()['detail'] == 'parent: A subtask cannot have subtasks.'

    def test_another_users_parent_or_label_is_a_400(self, client, auth, other_user):
        theirs = create_task(other_user, title='Theirs')
        label = make_label(other_user, 'Theirs')

        parent = post(client, '/tasks', auth, {'title': 'x', 'parent': theirs.number})
        labelled = post(client, '/tasks', auth, {'title': 'x', 'labels': [str(label.pk)]})

        assert parent.status_code == 400
        assert parent.json()['detail'] == 'parent: Unknown parent task.'
        assert labelled.status_code == 400
        assert labelled.json()['detail'] == 'labels: Unknown label.'
        assert not Task.objects.filter(title='x').exists()

    @pytest.mark.parametrize(
        ('fields', 'message'),
        [
            ({'title': '   '}, 'title: Title is required.'),
            ({'title': 'x', 'priority': 7}, 'priority: Choose priority 1, 2 or 3, or none.'),
            ({'title': 'x', 'due_time': '09:00'}, 'due_time: A time needs a date.'),
            ({'title': 'x', 'rrule': 'FREQ=SECONDLY'}, 'Unsupported repeat rule.'),
            ({'title': 'x', 'recurrence_mode': 'weekly'}, 'recurrence_mode: Choose'),
        ],
    )
    def test_a_service_refusal_is_a_400(self, client, auth, fields, message):
        response = post(client, '/tasks', auth, fields)

        assert response.status_code == 400
        assert message in response.json()['detail']

    def test_a_malformed_body_is_a_422(self, client, auth):
        assert post(client, '/tasks', auth, {}).status_code == 422  # no title
        assert post(client, '/tasks', auth, {'title': 'x', 'status': 'done'}).status_code == 422
        assert post(client, '/tasks', auth, {'title': 'x', 'due_date': 'soon'}).status_code == 422
        assert post(client, '/tasks', auth, {'title': 'x', 'labels': ['nope']}).status_code == 422

    def test_the_free_allowance_then_the_price(self, client, auth, funded_user):
        free = [new_task(client, auth, f'free {n}')['charged_credits'] for n in range(2)]
        paid = new_task(client, auth, 'paid')

        assert free == [0, 0] and paid['charged_credits'] == 5
        assert get_balance(funded_user) == 95

    def test_not_enough_credits_is_a_402_and_creates_nothing(self, client, funded_user, config):
        broke = make_user()
        for _ in range(2):
            create_task(broke, title='free')

        response = post(client, '/tasks', bearer(broke), {'title': 'one too many'})

        assert response.status_code == 402
        assert Task.objects.filter(user=broke).count() == 2

    def test_a_task_waiting_to_be_paid_blocks_creation_with_a_409(
        self, client, auth, funded_user, wednesday
    ):
        for _ in range(2):  # the month's free tasks
            create_task(funded_user, title='free')
        repeating = create_task(
            funded_user, title='Rent', due_date=date(2026, 10, 28), rrule='FREQ=DAILY'
        )
        spend_credits(funded_user, get_balance(funded_user))
        complete_task(repeating)  # the next occurrence is created owed
        owed = Task.objects.get(unpaid_at__isnull=False)

        response = post(client, '/tasks', auth, {'title': 'blocked'})

        assert response.status_code == 409
        assert response.json() == {
            'detail': 'Top up your credits to add tasks: a repeating task is waiting to be paid.',
            'unpaid_task_numbers': [owed.number],
        }
        assert not Task.objects.filter(title='blocked').exists()

    def test_the_create_rate_limit(self, client, auth, settings):
        limit_rule(settings, 'TODO_CREATE_USER')

        assert post(client, '/tasks', auth, {'title': 'a'}).status_code == 201
        second = post(client, '/tasks', auth, {'title': 'b'})

        assert second.status_code == 429 and 'Retry-After' in second


class TestParseTitle:
    def reading(self, fake, **fields: Any):
        defaults: dict[str, Any] = {
            'has_date': True,
            'clean_title': 'pay rent',
            'due_date': date(2026, 11, 1),
            'matched_text': 'every month on the 1st',
            'rrule': 'FREQ=MONTHLY;BYMONTHDAY=1',
            'confidence': 0.9,
        }
        fake.replies['pay rent every month on the 1st'] = ParsedTitle(**{**defaults, **fields})

    def test_the_reading_is_applied(self, client, auth, fake_title_parser, wednesday):
        self.reading(fake_title_parser, due_time=time(9, 0))

        body = new_task(client, auth, 'pay rent every month on the 1st', parse_title=True)

        assert body['title'] == 'pay rent'
        assert body['due_date'] == '2026-11-01' and body['due_time'] == '09:00:00'
        assert body['recurrence']['rrule'] == 'FREQ=MONTHLY;BYMONTHDAY=1'
        assert body['parsed']['applied'] is True
        assert body['parsed']['matched_text'] == 'every month on the 1st'
        assert body['parsed']['label'].startswith('Sun 1 Nov')

    def test_it_is_not_read_unless_asked(self, client, auth, fake_title_parser):
        self.reading(fake_title_parser)

        body = new_task(client, auth, 'pay rent every month on the 1st')

        assert body['title'] == 'pay rent every month on the 1st' and body['due_date'] is None
        assert fake_title_parser.calls == 0 and body['parsed'] is None

    def test_what_the_body_sets_wins(self, client, auth, fake_title_parser, wednesday):
        self.reading(fake_title_parser)

        body = new_task(
            client,
            auth,
            'pay rent every month on the 1st',
            parse_title=True,
            due_date='2026-12-24',
        )

        # The date is the body's; the repeat was read; the title keeps the words (nothing it said
        # vanishes unapplied).
        assert body['due_date'] == '2026-12-24'
        assert body['recurrence']['rrule'] == 'FREQ=MONTHLY;BYMONTHDAY=1'
        assert body['title'] == 'pay rent every month on the 1st'
        assert body['parsed']['applied'] is True and 'the date' in body['parsed']['reason']

    def test_everything_set_by_the_body_applies_nothing(
        self, client, auth, fake_title_parser, wednesday
    ):
        self.reading(fake_title_parser)

        body = new_task(
            client,
            auth,
            'pay rent every month on the 1st',
            parse_title=True,
            due_date=None,
            rrule='',
        )

        assert body['due_date'] is None and body['recurrence'] is None
        assert body['title'] == 'pay rent every month on the 1st'
        assert body['parsed']['applied'] is False

    def test_a_title_without_a_date_is_created_as_typed(self, client, auth, fake_title_parser):
        body = new_task(client, auth, 'call mom', parse_title=True)

        assert body['title'] == 'call mom' and body['due_date'] is None
        assert body['parsed'] == {
            'applied': False,
            'label': '',
            'matched_text': '',
            'reason': 'No date found in the title.',
        }

    def test_an_ai_that_fails_still_creates_the_task(self, client, auth, fake_title_parser):
        fake_title_parser.replies['pay rent every month on the 1st'] = ParseUnavailable('slow')

        body = new_task(client, auth, 'pay rent every month on the 1st', parse_title=True)

        assert body['title'] == 'pay rent every month on the 1st' and body['due_date'] is None
        assert body['parsed']['applied'] is False

    def test_an_unconfigured_ai_still_creates_the_task(self, client, auth, monkeypatch):
        from ..services import parsing

        monkeypatch.setattr(parsing, '_client', None)

        body = new_task(client, auth, 'pay rent on the 1st', parse_title=True)

        assert body['title'] == 'pay rent on the 1st' and body['parsed']['applied'] is False

    def test_the_parse_limits_are_spent_and_respected(
        self, client, auth, fake_title_parser, settings, wednesday
    ):
        self.reading(fake_title_parser)
        limit_rule(settings, 'TODO_PARSE_USER')

        first = new_task(client, auth, 'pay rent every month on the 1st', parse_title=True)
        second = new_task(client, auth, 'pay rent every month on the 1st', parse_title=True)

        assert first['parsed']['applied'] is True
        assert second['parsed']['applied'] is False  # over the limit: created as typed
        assert fake_title_parser.calls == 1


class TestList:
    @pytest.fixture
    def tasks(self, funded_user, wednesday):
        label = make_label(funded_user, 'Home')
        base = date(2026, 10, 28)
        return {
            'overdue': create_task(funded_user, title='Overdue', due_date=base - timedelta(days=2)),
            'today': create_task(funded_user, title='Today', due_date=base, priority=1),
            'soon': create_task(
                funded_user, title='Soon', due_date=base + timedelta(days=3), labels=[label]
            ),
            'later': create_task(funded_user, title='Later', due_date=base + timedelta(days=30)),
            'nodate': create_task(funded_user, title='No date', priority=3),
            'done': complete_task(create_task(funded_user, title='Done')),
            'trashed': trash_task(create_task(funded_user, title='Trashed')),
            'label': label,
        }

    def test_the_default_is_the_open_tasks_in_date_order(self, client, auth, tasks):
        response = get(client, '/tasks', auth)

        assert response.status_code == 200
        body = response.json()
        assert set(body) == {'count', 'limit', 'offset', 'results'}
        assert body['limit'] == 50 and body['offset'] == 0 and body['count'] == 5
        assert set(body['results'][0]) == TASK_KEYS
        assert numbers(response) == [
            tasks[key].number for key in ('overdue', 'today', 'soon', 'later', 'nodate')
        ]

    @pytest.mark.parametrize(
        ('view', 'expected'),
        [
            ('today', ['overdue', 'today']),
            ('upcoming', ['soon']),
            ('all', ['overdue', 'today', 'soon', 'later', 'nodate']),
            ('completed', ['done']),
            ('trash', ['trashed']),
        ],
    )
    def test_the_views(self, client, auth, tasks, view, expected):
        response = get(client, '/tasks', auth, view=view)

        assert numbers(response) == [tasks[key].number for key in expected]

    def test_include_completed_lists_them_last(self, client, auth, tasks):
        response = get(client, '/tasks', auth, include_completed='true')

        assert numbers(response)[-1] == tasks['done'].number
        assert response.json()['count'] == 6
        assert tasks['trashed'].number not in numbers(response)

    def test_today_follows_the_order_the_user_gave_it(self, client, auth, funded_user, wednesday):
        first = create_task(funded_user, title='a', due_date=date(2026, 10, 28))
        second = create_task(funded_user, title='b', due_date=date(2026, 10, 28))
        overdue = create_task(funded_user, title='old', due_date=date(2026, 10, 20))
        reorder(funded_user, scope='today', ids=[second.pk, first.pk])
        Task.objects.filter(pk=overdue.pk).update(today_position=9)  # stale: set on its day

        response = get(client, '/tasks', auth, view='today')

        assert numbers(response) == [overdue.number, second.number, first.number]

    def test_paging(self, client, auth, tasks):
        page = get(client, '/tasks', auth, limit=2, offset=1)

        assert numbers(page) == [tasks['today'].number, tasks['soon'].number]
        assert page.json()['count'] == 5 and page.json()['limit'] == 2
        assert get(client, '/tasks', auth, limit=500).json()['limit'] == 100
        assert get(client, '/tasks', auth, limit=0).json()['limit'] == 1

    def test_the_subtasks_of_a_task(self, client, auth, funded_user, tasks):
        parent = tasks['today']
        first = create_task(funded_user, title='first', parent=parent)
        second = complete_task(create_task(funded_user, title='second', parent=parent))

        everything = get(client, '/tasks', auth, parent=parent.number)
        open_ones = get(client, '/tasks', auth, parent=parent.number, view='all')

        assert sorted(numbers(everything)) == sorted([first.number, second.number])
        assert numbers(open_ones) == [first.number]

    def test_filters_by_priority_label_and_dates(self, client, auth, tasks):
        base = date(2026, 10, 28)
        label = str(tasks['label'].pk)

        assert numbers(get(client, '/tasks', auth, priority='1,3')) == [
            tasks['today'].number,
            tasks['nodate'].number,
        ]
        assert numbers(get(client, '/tasks', auth, label=label)) == [tasks['soon'].number]
        assert tasks['soon'].number not in numbers(get(client, '/tasks', auth, label='none'))
        assert numbers(get(client, '/tasks', auth, date_range='no_date')) == [
            tasks['nodate'].number
        ]
        assert numbers(get(client, '/tasks', auth, date_range='overdue')) == [
            tasks['overdue'].number
        ]
        assert numbers(
            get(client, '/tasks', auth, date_from=str(base), date_to=str(base + timedelta(days=3)))
        ) == [tasks['today'].number, tasks['soon'].number]
        assert (
            numbers(get(client, '/tasks', auth, label=str(make_label(tasks['done'].user, 'x').pk)))
            == []
        )

    def test_sorts(self, client, auth, tasks):
        by_priority = numbers(get(client, '/tasks', auth, sort='priority'))
        by_title = [t['title'] for t in get(client, '/tasks', auth, sort='title').json()['results']]

        assert by_priority[:2] == [tasks['today'].number, tasks['nodate'].number]
        assert by_title == sorted(by_title)

    @pytest.mark.parametrize(
        'query',
        [
            {'view': 'nope'},
            {'mode': 'fuzzy'},
            {'sort': 'size'},
            {'priority': '9'},
            {'priority': 'high'},
            {'label': 'home'},
            {'date_range': 'soon'},
            {'date_from': 'yesterday'},
        ],
    )
    def test_a_bad_parameter_is_a_400(self, client, auth, query):
        assert get(client, '/tasks', auth, **query).status_code == 400

    def test_a_malformed_number_is_a_422(self, client, auth):
        assert get(client, '/tasks', auth, limit='many').status_code == 422
        assert get(client, '/tasks/not-a-number', auth).status_code == 422

    def test_another_users_parent_is_a_404(self, client, auth, other_user):
        theirs = create_task(other_user, title='Theirs')

        assert get(client, '/tasks', auth, parent=theirs.number).status_code == 404

    def test_only_the_callers_tasks(self, client, auth, tasks, other_user):
        create_task(other_user, title='Theirs')

        assert get(client, '/tasks', auth).json()['count'] == 5

    def test_a_row_carries_its_counts_and_its_relations(
        self, client, auth, funded_user, tasks, fake_storage
    ):
        parent = tasks['soon']
        create_task(funded_user, title='child', parent=parent)
        add_comment(parent, funded_user, 'a comment')
        make_attachment(parent, fake_storage)
        make_attachment(parent, fake_storage, uploaded=False)  # pending: not counted

        row = next(
            task
            for task in get(client, '/tasks', auth).json()['results']
            if task['number'] == parent.number
        )

        assert row['subtask_count'] == 1 and row['open_subtask_count'] == 1
        assert row['comment_count'] == 1 and row['attachment_count'] == 1
        assert [label['name'] for label in row['labels']] == ['Home']
        assert len(row['reminders']) == 1

    def test_the_number_of_queries_does_not_grow_with_the_tasks(
        self, client, auth, funded_user, django_assert_max_num_queries
    ):
        label = make_label(funded_user, 'Home')
        for n in range(8):
            task = create_task(
                funded_user, title=f'Task {n}', due_date=date(2026, 12, 1), labels=[label]
            )
            create_task(funded_user, title=f'Child {n}', parent=task)

        with django_assert_max_num_queries(12):
            assert get(client, '/tasks', auth, limit=100).json()['count'] == 16


class TestSearch:
    @pytest.fixture
    def found(self, funded_user):
        return {
            'title': create_task(funded_user, title='Pay the RENT'),
            'description': create_task(funded_user, title='Other', description='the landlord: Cão'),
            'comment': add_comment(
                create_task(funded_user, title='Third'), funded_user, 'ask about parking'
            ).task,
            'none': create_task(funded_user, title='Unrelated'),
        }

    def test_title_description_and_comments_ignoring_case_and_accents(self, client, auth, found):
        assert numbers(get(client, '/tasks', auth, q='rent')) == [found['title'].number]
        assert numbers(get(client, '/tasks', auth, q='cao')) == [found['description'].number]
        assert numbers(get(client, '/tasks', auth, q='parking')) == [found['comment'].number]

    def test_glob_wildcards_and_regex(self, client, auth, found):
        assert numbers(get(client, '/tasks', auth, q='pay*rent')) == [found['title'].number]
        assert sorted(numbers(get(client, '/tasks', auth, q='/^(pay|third)/', mode='regex'))) == (
            sorted([found['title'].number, found['comment'].number])
        )
        assert numbers(get(client, '/tasks', auth, q='/land.ord/')) == [found['description'].number]

    @pytest.mark.parametrize('q', ['/(?=x)/', '/(/', 'x' * 201])
    def test_an_invalid_pattern_is_a_400(self, client, auth, q):
        response = get(client, '/tasks', auth, q=q)

        assert response.status_code == 400

    def test_completed_tasks_only_when_asked(self, client, auth, funded_user, found):
        complete_task(found['title'])

        assert numbers(get(client, '/tasks', auth, q='rent')) == []
        assert numbers(get(client, '/tasks', auth, q='rent', include_completed='true')) == [
            found['title'].number
        ]

    def test_a_search_and_a_filter_spend_the_search_budget(self, client, auth, settings, found):
        limit_rule(settings, 'TODO_SEARCH_USER')

        assert get(client, '/tasks', auth).status_code == 200  # a plain list is free
        assert get(client, '/tasks', auth).status_code == 200
        assert get(client, '/tasks', auth, q='rent').status_code == 200
        assert get(client, '/tasks', auth, priority='1').status_code == 429
        assert get(client, '/tasks', auth, q='rent').status_code == 429

    def test_a_refused_pattern_still_spends_the_budget(self, client, auth, settings):
        limit_rule(settings, 'TODO_SEARCH_USER')

        assert get(client, '/tasks', auth, q='/(/').status_code == 400
        assert get(client, '/tasks', auth, q='rent').status_code == 429


class TestRead:
    def test_a_task_with_everything(self, client, auth, funded_user, fake_storage):
        task = create_task(funded_user, title='Main', description='details')
        mentioner = create_task(funded_user, title='Mentions', description=f'see #{task.number}')
        commenter = create_task(funded_user, title='Comments')
        comment = add_comment(commenter, funded_user, f'also #{task.number}')
        make_attachment(task, fake_storage, filename='plan.png')
        make_attachment(task, fake_storage, uploaded=False)

        body = get(client, f'/tasks/{task.number}', auth).json()

        assert set(body) == TASK_KEYS | {'attachments', 'referenced_by'}
        assert [a['filename'] for a in body['attachments']] == ['plan.png']
        assert set(body['attachments'][0]) == {
            'id',
            'task',
            'filename',
            'size_bytes',
            'declared_type',
            'inline_type',
            'created_at',
        }
        assert body['referenced_by'] == [
            {'number': mentioner.number, 'title': 'Mentions', 'comment': None},
            {'number': commenter.number, 'title': 'Comments', 'comment': str(comment.pk)},
        ]
        assert body['attachment_count'] == 1

    def test_the_description_links_the_tasks_it_mentions(self, client, auth, funded_user):
        other = create_task(funded_user, title='Other')
        task = create_task(funded_user, title='Main', description=f'see #{other.number}')

        body = get(client, f'/tasks/{task.number}', auth).json()

        assert f'href="/todo/{other.number}/"' in body['description_html']

    def test_a_trashed_task_is_readable(self, client, auth, funded_user):
        task = trash_task(create_task(funded_user, title='Gone'))

        body = get(client, f'/tasks/{task.number}', auth).json()

        assert body['deleted_at'] is not None and body['referenced_by'] == []

    def test_another_users_task_is_a_404(self, client, auth, other_user):
        theirs = create_task(other_user, title='Theirs')

        assert get(client, f'/tasks/{theirs.number}', auth).status_code == 404
        assert get(client, '/tasks/999999', auth).status_code == 404

    def test_the_due_instant_is_in_the_owners_zone(self, client, auth, funded_user):
        funded_user.timezone = 'America/Chicago'
        funded_user.save()
        task = create_task(funded_user, title='Zoned', due_date=date(2026, 11, 5))
        account = get_account(funded_user)
        account.default_due_time = time(9, 0)
        account.save()

        body = get(client, f'/tasks/{task.number}', auth).json()

        assert body['due_at'] == '2026-11-05T15:00:00Z'  # 09:00 CST


class TestUpdate:
    def test_only_the_fields_present_change(self, client, auth, funded_user):
        task = create_task(funded_user, title='Old', description='keep', priority=2)

        body = patch(client, f'/tasks/{task.number}', auth, {'title': 'New'}).json()

        assert body['title'] == 'New' and body['description'] == 'keep' and body['priority'] == 2

    def test_an_explicit_null_clears(self, client, auth, funded_user):
        label = make_label(funded_user, 'Home')
        task = create_task(
            funded_user,
            title='Full',
            description='text',
            priority=1,
            due_date=date(2026, 11, 1),
            due_time=time(8, 0),
            labels=[label],
        )

        body = patch(
            client,
            f'/tasks/{task.number}',
            auth,
            {
                'description': None,
                'priority': None,
                'due_date': None,
                'labels': None,
            },
        ).json()

        assert body['description'] == '' and body['priority'] == 0
        assert body['due_date'] is None and body['due_time'] is None  # the time goes with the date
        assert body['labels'] == []

    def test_a_null_title_is_a_400(self, client, auth, funded_user):
        task = create_task(funded_user, title='x')

        response = patch(client, f'/tasks/{task.number}', auth, {'title': None})

        assert response.status_code == 400

    def test_the_time_alone(self, client, auth, funded_user):
        task = create_task(funded_user, title='x', due_date=date(2026, 11, 1))

        body = patch(client, f'/tasks/{task.number}', auth, {'due_time': '17:45'}).json()

        assert body['due_time'] == '17:45:00'

    def test_a_change_of_date_moves_the_reminders(self, client, auth, funded_user):
        task = create_task(funded_user, title='x', due_date=date(2026, 11, 1))
        before = Reminder.objects.get(task=task).fire_at

        body = patch(client, f'/tasks/{task.number}', auth, {'due_date': '2026-11-03'}).json()

        assert body['reminders'][0]['fire_at'] != before.isoformat().replace('+00:00', 'Z')
        assert Reminder.objects.get(task=task).fire_at == before + timedelta(days=2)

    def test_the_repeat_rule(self, client, auth, funded_user):
        task = create_task(funded_user, title='x', due_date=date(2026, 11, 2))

        started = patch(
            client,
            f'/tasks/{task.number}',
            auth,
            {'rrule': 'FREQ=WEEKLY;BYDAY=MO', 'recurrence_mode': 'from_completion'},
        ).json()
        stopped = patch(client, f'/tasks/{task.number}', auth, {'rrule': None}).json()

        assert started['recurrence']['rrule'] == 'FREQ=WEEKLY;BYDAY=MO'
        assert started['recurrence']['mode'] == 'from_completion'
        assert stopped['recurrence']['ended_at'] is not None

    def test_moving_under_a_parent_and_back(self, client, auth, funded_user):
        parent = create_task(funded_user, title='Parent')
        task = create_task(funded_user, title='Child')

        under = patch(client, f'/tasks/{task.number}', auth, {'parent': parent.number}).json()
        free = patch(client, f'/tasks/{task.number}', auth, {'parent': None}).json()

        assert under['parent'] == parent.number and free['parent'] is None

    def test_a_refusal_changes_nothing(self, client, auth, funded_user):
        task = create_task(funded_user, title='Same', priority=1)

        response = patch(client, f'/tasks/{task.number}', auth, {'title': 'Changed', 'priority': 9})

        assert response.status_code == 400
        task.refresh_from_db()
        assert task.title == 'Same' and task.priority == 1

    def test_a_trashed_task_must_be_restored_first(self, client, auth, funded_user):
        task = trash_task(create_task(funded_user, title='x'))

        response = patch(client, f'/tasks/{task.number}', auth, {'title': 'y'})

        assert response.status_code == 400
        assert response.json()['detail'] == 'Restore the task before changing it.'

    def test_an_unknown_key_is_a_422(self, client, auth, funded_user):
        task = create_task(funded_user, title='x')

        for key in ('status', 'reminders', 'parse_title', 'number'):
            assert patch(client, f'/tasks/{task.number}', auth, {key: 1}).status_code == 422

    def test_an_empty_body_changes_nothing_and_spends_nothing(
        self, client, auth, funded_user, settings
    ):
        task = create_task(funded_user, title='x')
        limit_rule(settings, 'TODO_EDIT_USER')

        assert patch(client, f'/tasks/{task.number}', auth, {}).status_code == 200
        assert patch(client, f'/tasks/{task.number}', auth, {'title': 'y'}).status_code == 200
        assert patch(client, f'/tasks/{task.number}', auth, {'title': 'z'}).status_code == 429

    def test_another_users_task_is_a_404(self, client, auth, other_user):
        theirs = create_task(other_user, title='Theirs')

        assert patch(client, f'/tasks/{theirs.number}', auth, {'title': 'x'}).status_code == 404
        theirs.refresh_from_db()
        assert theirs.title == 'Theirs'


class TestStateChanges:
    def test_complete_and_uncomplete(self, client, auth, funded_user):
        task = create_task(funded_user, title='x')
        child = create_task(funded_user, title='child', parent=task)

        done = post(client, f'/tasks/{task.number}/complete', auth).json()
        again = post(client, f'/tasks/{task.number}/complete', auth)
        reopened = post(client, f'/tasks/{task.number}/uncomplete', auth).json()

        assert done['completed_at'] is not None and again.status_code == 200
        child.refresh_from_db()
        assert child.completed_at is not None  # its open subtasks went with it
        assert reopened['completed_at'] is None

    def test_completing_a_repeating_task_creates_the_next_one(
        self, client, auth, funded_user, wednesday
    ):
        task = create_task(
            funded_user, title='Rent', due_date=date(2026, 10, 28), rrule='FREQ=DAILY'
        )

        post(client, f'/tasks/{task.number}/complete', auth)

        nxt = Task.objects.get(title='Rent', completed_at__isnull=True)
        assert nxt.due_date == date(2026, 10, 29) and nxt.source == 'recurrence'

    def test_trash_restore_and_delete_forever(self, client, auth, funded_user):
        task = create_task(funded_user, title='x')

        assert delete(client, f'/tasks/{task.number}', auth).status_code == 204
        assert delete(client, f'/tasks/{task.number}', auth).status_code == 204  # idempotent
        assert get(client, f'/tasks/{task.number}', auth).json()['deleted_at'] is not None
        assert numbers(get(client, '/tasks', auth, view='trash')) == [task.number]
        restored = post(client, f'/tasks/{task.number}/restore', auth).json()
        assert restored['deleted_at'] is None
        assert post(client, f'/tasks/{task.number}/restore', auth).status_code == 200

        delete(client, f'/tasks/{task.number}', auth)
        assert post(client, f'/tasks/{task.number}/delete-forever', auth).status_code == 204
        assert not Task.objects.filter(pk=task.pk).exists()
        assert get(client, f'/tasks/{task.number}', auth).status_code == 404

    def test_a_task_outside_the_trash_is_not_deleted_forever(self, client, auth, funded_user):
        task = create_task(funded_user, title='x')

        assert post(client, f'/tasks/{task.number}/delete-forever', auth).status_code == 404
        assert Task.objects.filter(pk=task.pk).exists()

    def test_restoring_a_subtask_under_a_trashed_parent_is_a_400(self, client, auth, funded_user):
        parent = create_task(funded_user, title='Parent')
        child = create_task(funded_user, title='Child', parent=parent)
        trash_task(parent)

        response = post(client, f'/tasks/{child.number}/restore', auth)

        assert response.status_code == 400
        assert response.json()['detail'] == 'Restore the parent task first.'

    def test_an_unpaid_task_cannot_be_deleted_forever(self, client, auth, funded_user, wednesday):
        for _ in range(2):  # the month's free tasks
            create_task(funded_user, title='free')
        task = create_task(
            funded_user, title='Rent', due_date=date(2026, 10, 28), rrule='FREQ=DAILY'
        )
        spend_credits(funded_user, get_balance(funded_user))
        complete_task(task)
        owed = Task.objects.get(unpaid_at__isnull=False)
        trash_task(owed)

        response = post(client, f'/tasks/{owed.number}/delete-forever', auth)

        assert response.status_code == 400 and 'Top up your credits' in response.json()['detail']

    def test_a_completed_or_trashed_task_refuses_what_the_services_refuse(
        self, client, auth, funded_user
    ):
        trashed = trash_task(create_task(funded_user, title='x', due_date=date(2026, 11, 1)))

        assert post(client, f'/tasks/{trashed.number}/complete', auth).status_code == 400
        assert (
            post(
                client, f'/tasks/{trashed.number}/postpone', auth, {'preset': 'tomorrow'}
            ).status_code
            == 400
        )

    def test_postpone(self, client, auth, funded_user, wednesday):
        task = create_task(funded_user, title='x', due_date=date(2026, 10, 28), due_time=time(8, 0))

        tomorrow = post(client, f'/tasks/{task.number}/postpone', auth, {'preset': 'tomorrow'})
        week = post(client, f'/tasks/{task.number}/postpone', auth, {'preset': 'next_week'})
        weekend = post(client, f'/tasks/{task.number}/postpone', auth, {'preset': 'next_weekend'})
        picked = post(
            client,
            f'/tasks/{task.number}/postpone',
            auth,
            {'preset': 'date', 'date': '2026-12-24'},
        )

        assert tomorrow.json()['due_date'] == '2026-10-29'
        assert week.json()['due_date'] == '2026-11-02'  # the next Monday
        assert weekend.json()['due_date'] == '2026-10-31'
        assert picked.json()['due_date'] == '2026-12-24' and picked.json()['due_time'] == '08:00:00'

    @pytest.mark.parametrize(
        'body',
        [
            {'preset': 'someday'},
            {'preset': 'date'},
            {'preset': 'date', 'date': '2020-01-01'},
        ],
    )
    def test_a_bad_postpone_is_a_400(self, client, auth, funded_user, wednesday, body):
        task = create_task(funded_user, title='x', due_date=date(2026, 10, 28))

        assert post(client, f'/tasks/{task.number}/postpone', auth, body).status_code == 400

    def test_a_malformed_postpone_is_a_422(self, client, auth, funded_user):
        task = create_task(funded_user, title='x')

        assert post(client, f'/tasks/{task.number}/postpone', auth, {}).status_code == 422
        assert (
            post(
                client, f'/tasks/{task.number}/postpone', auth, {'preset': 'date', 'date': 'x'}
            ).status_code
            == 422
        )

    def test_stop_repeating(self, client, auth, funded_user, wednesday):
        task = create_task(funded_user, title='x', due_date=date(2026, 10, 28), rrule='FREQ=DAILY')

        body = post(client, f'/tasks/{task.number}/recurrence/stop', auth).json()
        again = post(client, f'/tasks/{task.number}/recurrence/stop', auth)

        assert body['recurrence']['ended_at'] is not None and again.status_code == 200

    def test_reschedule_overdue(self, client, auth, funded_user, wednesday):
        late = create_task(funded_user, title='late', due_date=date(2026, 10, 20))
        fine = create_task(funded_user, title='fine', due_date=date(2026, 11, 20))

        response = post(client, '/tasks/reschedule-overdue', auth)

        assert response.json() == {'count': 1}
        late.refresh_from_db()
        fine.refresh_from_db()
        assert late.due_date == date(2026, 10, 28) and fine.due_date == date(2026, 11, 20)

    def test_checklist(self, client, auth, funded_user):
        task = create_task(funded_user, title='x', description='- [ ] one\n- [ ] two')

        body = post(client, f'/tasks/{task.number}/checklist', auth, {'index': 1, 'checked': True})

        assert body.json()['description'] == '- [ ] one\n- [x] two'
        missing = post(
            client, f'/tasks/{task.number}/checklist', auth, {'index': 7, 'checked': True}
        )
        assert missing.status_code == 400
        assert missing.json()['detail'] == 'index: No such checklist item.'

    def test_reorder(self, client, auth, funded_user, wednesday):
        a = create_task(funded_user, title='a', due_date=date(2026, 10, 28))
        b = create_task(funded_user, title='b', due_date=date(2026, 10, 28))
        parent = create_task(funded_user, title='p')
        c = create_task(funded_user, title='c', parent=parent)
        d = create_task(funded_user, title='d', parent=parent)

        today = post(
            client, '/tasks/reorder', auth, {'scope': 'today', 'ids': [str(b.pk), str(a.pk)]}
        )
        subs = post(
            client,
            '/tasks/reorder',
            auth,
            {'scope': 'subtasks', 'parent': parent.number, 'ids': [str(d.pk), str(c.pk)]},
        )

        assert today.json() == {'count': 2} and subs.json() == {'count': 2}
        assert numbers(get(client, '/tasks', auth, view='today')) == [b.number, a.number]
        assert numbers(get(client, '/tasks', auth, parent=parent.number)) == [d.number, c.number]

    def test_reorder_refusals(self, client, auth, funded_user, other_user):
        theirs = create_task(other_user, title='Theirs')

        assert post(client, '/tasks/reorder', auth, {'scope': 'nope', 'ids': []}).status_code == 400
        assert (
            post(client, '/tasks/reorder', auth, {'scope': 'subtasks', 'ids': []}).status_code
            == 400
        )
        assert (
            post(
                client,
                '/tasks/reorder',
                auth,
                {'scope': 'subtasks', 'ids': [], 'parent': theirs.number},
            ).status_code
            == 404
        )

    def test_another_users_task_is_a_404_everywhere(self, client, auth, other_user):
        theirs = create_task(other_user, title='Theirs')
        n = theirs.number

        for path, payload in [
            (f'/tasks/{n}/complete', None),
            (f'/tasks/{n}/uncomplete', None),
            (f'/tasks/{n}/restore', None),
            (f'/tasks/{n}/delete-forever', None),
            (f'/tasks/{n}/postpone', {'preset': 'tomorrow'}),
            (f'/tasks/{n}/checklist', {'index': 0, 'checked': True}),
            (f'/tasks/{n}/recurrence/stop', None),
        ]:
            assert post(client, path, auth, payload).status_code == 404, path
        assert delete(client, f'/tasks/{n}', auth).status_code == 404

    def test_the_edit_rate_limit(self, client, auth, funded_user, settings):
        task = create_task(funded_user, title='x')
        limit_rule(settings, 'TODO_EDIT_USER')

        assert post(client, f'/tasks/{task.number}/complete', auth).status_code == 200
        assert post(client, f'/tasks/{task.number}/uncomplete', auth).status_code == 429
        assert post(client, '/tasks/reschedule-overdue', auth).status_code == 429


def test_the_account_is_created_on_first_use(client, auth, funded_user):
    assert not TodoAccount.objects.filter(user=funded_user).exists()

    assert get(client, '/tasks', auth).status_code == 200
