"""The review of PR #200: the API checks a request before it spends anything (the AI call, a
rate-limit budget), cancels pending uploads, answers the unpaid-task 409 from a dedicated
exception, refuses a `null` that can't clear, bounds the reorder lists and shares the web's list
querysets."""

import uuid
from datetime import date, timedelta

import pytest
from django.core.exceptions import ValidationError

from apps.billing.services import InsufficientCreditsError, get_balance, spend_credits

from ..limits import MAX_REORDER_IDS, TITLE_MAX
from ..models import Attachment, SavedFilter, Task, TodoSettings
from ..services import (
    TaskCreationBlockedError,
    TooManyTasksError,
    check_new_task,
    check_update_task,
    complete_task,
    create_task,
    dates,
)
from ..services.filters import FilterParams
from ..services.parsing import ParsedTitle
from ..services.saved_filters import save_filter
from ..services.storage import attachment_key
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
from .factories import PNG_BYTES, make_attachment, make_label, make_user

pytestmark = [pytest.mark.django_db, pytest.mark.unit]

RENT = 'pay rent every month on the 1st'


def reading() -> ParsedTitle:
    return ParsedTitle(
        has_date=True,
        clean_title='pay rent',
        due_date=date(2026, 11, 1),
        matched_text='every month on the 1st',
        rrule='FREQ=MONTHLY;BYMONTHDAY=1',
        confidence=0.9,
    )


@pytest.fixture
def blocked(funded_user, wednesday):
    """The user has a repeating task's next occurrence waiting to be paid for."""
    for _ in range(2):  # the month's free tasks
        create_task(funded_user, title='free')
    repeating = create_task(
        funded_user, title='Rent', due_date=date(2026, 10, 28), rrule='FREQ=DAILY'
    )
    spend_credits(funded_user, get_balance(funded_user))
    complete_task(repeating)
    return funded_user


class TestTitleBound:
    def test_a_title_over_the_limit_is_a_422_and_never_read(
        self, client, auth, fake_title_parser, settings
    ):
        limit_rule(settings, 'TODO_PARSE_USER', limit=5)
        long = 'pay rent on the 1st ' * 30
        assert len(long) > TITLE_MAX

        response = post(client, '/tasks', auth, {'title': long, 'parse_title': True})

        assert response.status_code == 422
        assert fake_title_parser.calls == 0
        assert not Task.objects.exists()

    def test_the_longest_title_is_accepted(self, client, auth):
        response = post(client, '/tasks', auth, {'title': 'x' * TITLE_MAX})

        assert response.status_code == 201

    def test_a_patched_title_over_the_limit_is_a_422(self, client, auth, funded_user):
        task = create_task(funded_user, title='short')

        response = patch(client, f'/tasks/{task.number}', auth, {'title': 'x' * (TITLE_MAX + 1)})

        assert response.status_code == 422
        task.refresh_from_db()
        assert task.title == 'short'


class TestNothingIsReadForARefusedCreate:
    @pytest.fixture(autouse=True)
    def _reading(self, fake_title_parser, settings):
        fake_title_parser.replies[RENT] = reading()
        limit_rule(settings, 'TODO_PARSE_USER', limit=5)
        limit_rule(settings, 'TODO_PARSE_USER_DAY', limit=5)

    def create(self, client, auth, **fields):
        return post(client, '/tasks', auth, {'title': RENT, 'parse_title': True, **fields})

    def test_a_creation_blocked_by_an_unpaid_task_is_a_409_without_a_call(
        self, client, blocked, fake_title_parser
    ):
        response = self.create(client, bearer(blocked))

        assert response.status_code == 409
        assert response.json()['unpaid_task_numbers']
        assert fake_title_parser.calls == 0

    def test_the_open_task_cap_is_a_400_without_a_call(
        self, client, auth, funded_user, fake_title_parser, config
    ):
        create_task(funded_user, title='the only one')
        TodoSettings.objects.update(max_open_tasks_per_user=1)

        response = self.create(client, auth)

        assert response.status_code == 400
        assert 'Too many open tasks' in response.json()['detail']
        assert fake_title_parser.calls == 0

    def test_a_balance_that_cannot_pay_is_a_402_without_a_call(
        self, client, fake_title_parser, config
    ):
        broke = make_user()
        for _ in range(2):  # the free allowance
            create_task(broke, title='free')

        response = self.create(client, bearer(broke))

        assert response.status_code == 402
        assert fake_title_parser.calls == 0
        assert Task.objects.filter(user=broke).count() == 2

    def test_an_invalid_field_is_a_400_without_a_call(self, client, auth, fake_title_parser):
        for fields in ({'priority': 9}, {'rrule': 'NOPE'}, {'labels': [str(uuid.uuid4())]}):
            assert self.create(client, auth, **fields).status_code == 400

        assert fake_title_parser.calls == 0

    def test_an_accepted_request_reads_the_title_once(
        self, client, auth, fake_title_parser, wednesday
    ):
        response = self.create(client, auth)

        assert response.status_code == 201 and response.json()['parsed']['applied'] is True
        assert fake_title_parser.calls == 1


class TestRefusedRequestsSpendNoBudget:
    def test_a_refused_create_leaves_the_create_budget(self, client, auth, settings):
        limit_rule(settings, 'TODO_CREATE_USER')

        refusals = [
            post(client, '/tasks', auth, {'title': 'a', 'priority': 9}),
            post(client, '/tasks', auth, {'title': '   '}),
            post(client, '/tasks', auth, {'title': 'a', 'labels': [str(uuid.uuid4())]}),
            post(client, '/tasks', auth, {'title': 'a', 'parent': 424242}),
            post(client, '/tasks', auth, {'title': 'a', 'reminders': [{'minutes_before': 5}]}),
            post(
                client,
                '/tasks',
                auth,
                {
                    'title': 'a',
                    'due_date': '2026-11-01',
                    'reminders': [{'minutes_before': 5}, {'minutes_before': 5}],
                },
            ),
            post(
                client,
                '/tasks',
                auth,
                {
                    'title': 'a',
                    'due_date': '2026-11-01',
                    'reminders': [{'minutes_before': 99999999}],
                },
            ),
        ]

        assert [r.status_code for r in refusals] == [400] * len(refusals)
        assert post(client, '/tasks', auth, {'title': 'fine'}).status_code == 201
        assert post(client, '/tasks', auth, {'title': 'second'}).status_code == 429

    def test_a_409_and_a_402_leave_the_create_budget(self, client, blocked, settings):
        limit_rule(settings, 'TODO_CREATE_USER')
        headers = bearer(blocked)

        assert post(client, '/tasks', headers, {'title': 'a'}).status_code == 409
        assert post(client, '/tasks', headers, {'title': 'b'}).status_code == 409

    def test_a_patch_that_is_refused_leaves_the_edit_budget(
        self, client, auth, funded_user, settings
    ):
        task = create_task(funded_user, title='x')
        limit_rule(settings, 'TODO_EDIT_USER')

        for body in (
            {'priority': 9},
            {'title': None},
            {'rrule': 'NOPE'},
            {'labels': [str(uuid.uuid4())]},
            {'parent': 999999},
        ):
            assert patch(client, f'/tasks/{task.number}', auth, body).status_code == 400

        assert patch(client, f'/tasks/{task.number}', auth, {'title': 'ok'}).status_code == 200
        assert patch(client, f'/tasks/{task.number}', auth, {'title': 'again'}).status_code == 429

    def test_an_empty_patch_spends_nothing(self, client, auth, funded_user, settings):
        task = create_task(funded_user, title='x')
        limit_rule(settings, 'TODO_EDIT_USER')

        for _ in range(3):
            assert patch(client, f'/tasks/{task.number}', auth, {}).status_code == 200

        assert patch(client, f'/tasks/{task.number}', auth, {'title': 'ok'}).status_code == 200

    def test_a_bad_postpone_and_reorder_leave_the_edit_budget(
        self, client, auth, funded_user, settings
    ):
        task = create_task(funded_user, title='x')
        limit_rule(settings, 'TODO_EDIT_USER')

        assert (
            post(client, f'/tasks/{task.number}/postpone', auth, {'preset': 'never'}).status_code
            == 400
        )
        assert (
            post(client, f'/tasks/{task.number}/postpone', auth, {'preset': 'date'}).status_code
            == 400
        )
        assert post(client, '/tasks/reorder', auth, {'scope': 'x', 'ids': []}).status_code == 400
        assert (
            post(client, '/tasks/reorder', auth, {'scope': 'subtasks', 'ids': []}).status_code
            == 400
        )

        assert (
            post(client, f'/tasks/{task.number}/postpone', auth, {'preset': 'tomorrow'}).status_code
            == 200
        )
        assert (
            post(client, f'/tasks/{task.number}/postpone', auth, {'preset': 'tomorrow'}).status_code
            == 429
        )

    def test_labels_comments_reminders_and_filters_check_before_charging(
        self, client, auth, funded_user, settings, wednesday
    ):
        task = create_task(funded_user, title='x', due_date=date(2026, 11, 1))
        label = make_label(funded_user, 'Home')
        saved = save_filter(funded_user, 'Mine', FilterParams())
        limit_rule(settings, 'TODO_EDIT_USER')
        limit_rule(settings, 'TODO_COMMENT_USER')

        refused = [
            post(client, '/labels', auth, {'name': ' '}),
            post(client, '/labels', auth, {'name': 'ok', 'color': 'red'}),
            patch(client, f'/labels/{label.pk}', auth, {'name': ''}),
            post(client, f'/tasks/{task.number}/comments', auth, {'body': '  '}),
            post(client, f'/tasks/{task.number}/reminders', auth, {'minutes_before': -1}),
            post(
                client,
                f'/tasks/{task.number}/reminders',
                auth,
                {'minutes_before': 5, 'channel': 'pigeon'},
            ),
            post(client, '/filters', auth, {'name': ' '}),
            post(
                client, '/filters', auth, {'name': 'bad', 'params': {'q': '/[/', 'mode': 'regex'}}
            ),
            patch(client, f'/filters/{saved.pk}', auth, {'name': ' '}),
            patch(client, '/settings', auth, {'default_reminder_minutes': -5}),
            patch(client, '/settings', auth, {'default_reminder_channel': 'pigeon'}),
            patch(client, '/settings', auth, {'default_due_time': None}),
        ]

        assert [r.status_code for r in refused] == [400] * len(refused), [
            r.content for r in refused
        ]
        assert (
            post(client, f'/tasks/{task.number}/comments', auth, {'body': 'hi'}).status_code == 201
        )
        assert post(client, '/labels', auth, {'name': 'Work'}).status_code == 201
        assert post(client, '/filters', auth, {'name': 'New'}).status_code == 429


class TestCreationBlockedError:
    def test_create_task_raises_a_dedicated_class(self, blocked):
        with pytest.raises(TaskCreationBlockedError) as caught:
            create_task(blocked, title='no')

        assert isinstance(caught.value, ValidationError)
        assert 'Top up your credits' in caught.value.messages[0]

    def test_the_409_does_not_depend_on_the_text(self, client, blocked, monkeypatch):
        from ..services import tasks

        monkeypatch.setattr(tasks, 'BLOCKED_MESSAGE', 'Reworded entirely.')

        response = post(client, '/tasks', bearer(blocked), {'title': 'no'})

        assert response.status_code == 409

    def test_a_plain_refusal_with_the_same_words_is_still_a_400(self, client, auth, monkeypatch):
        from ..services import tasks

        monkeypatch.setattr(tasks, 'TOO_MANY_MESSAGE', tasks.BLOCKED_MESSAGE)
        TodoSettings.objects.update(max_open_tasks_per_user=1)
        create_task(make_user(), title='another user')

        assert post(client, '/tasks', auth, {'title': 'a'}).status_code == 201
        second = post(client, '/tasks', auth, {'title': 'b'})
        assert second.status_code == 400  # the cap, not the unpaid block

    def test_the_cap_has_its_own_class(self, funded_user):
        create_task(funded_user, title='a')
        TodoSettings.objects.update(max_open_tasks_per_user=1)

        with pytest.raises(TooManyTasksError):
            create_task(funded_user, title='b')


class TestChecks:
    def test_check_new_task_makes_the_refusals_without_writing(self, funded_user):
        assert check_new_task(funded_user, title='fine') is None
        with pytest.raises(ValidationError):
            check_new_task(funded_user, title='')
        assert Task.objects.filter(title='fine').count() == 0

    def test_check_new_task_refuses_a_creation_blocked_by_an_unpaid_task(self, blocked):
        with pytest.raises(TaskCreationBlockedError):
            check_new_task(blocked, title='no')

    def test_check_new_task_returns_the_date_a_repeat_would_get(self, funded_user, wednesday):
        assert check_new_task(funded_user, title='x', rrule='FREQ=DAILY') == date(2026, 10, 28)

    def test_check_new_task_refuses_a_balance_that_cannot_pay(self, config):
        broke = make_user()
        for _ in range(2):
            create_task(broke, title='free')

        with pytest.raises(InsufficientCreditsError):
            check_new_task(broke, title='one too many')

    def test_check_update_task_refuses_bad_fields_without_writing(self, funded_user):
        task = create_task(funded_user, title='x')

        with pytest.raises(ValidationError):
            check_update_task(task, changes={'priority': 9})
        with pytest.raises(ValidationError):
            check_update_task(task, changes={'rrule': 'NOPE'})
        check_update_task(task, changes={'title': 'fine'})
        task.refresh_from_db()
        assert task.title == 'x'


class TestFilterPatchNulls:
    def test_a_null_name_or_params_is_a_400(self, client, auth, funded_user):
        saved = save_filter(funded_user, 'Mine', FilterParams())

        for body in ({'name': None}, {'params': None}, {'name': 'New', 'params': None}):
            response = patch(client, f'/filters/{saved.pk}', auth, body)
            assert response.status_code == 400, body
            assert 'null' in response.json()['detail']

        saved.refresh_from_db()
        assert saved.name == 'Mine'

    def test_a_null_spends_no_budget_and_an_empty_body_changes_nothing(
        self, client, auth, funded_user, settings
    ):
        saved = save_filter(funded_user, 'Mine', FilterParams())
        limit_rule(settings, 'TODO_EDIT_USER')

        assert patch(client, f'/filters/{saved.pk}', auth, {'name': None}).status_code == 400
        assert patch(client, f'/filters/{saved.pk}', auth, {}).status_code == 200
        assert patch(client, f'/filters/{saved.pk}', auth, {'name': 'New'}).status_code == 200

        assert SavedFilter.objects.get(pk=saved.pk).name == 'New'


class TestReorderBounds:
    def ids(self, count: int) -> list[str]:
        return [str(uuid.uuid4()) for _ in range(count)]

    def test_the_task_reorder_list_is_bounded(self, client, auth):
        ok = post(
            client, '/tasks/reorder', auth, {'scope': 'today', 'ids': self.ids(MAX_REORDER_IDS)}
        )
        too_long = post(
            client, '/tasks/reorder', auth, {'scope': 'today', 'ids': self.ids(MAX_REORDER_IDS + 1)}
        )

        assert ok.status_code == 200 and too_long.status_code == 422

    def test_the_label_reorder_list_is_bounded(self, client, auth):
        ok = post(client, '/labels/reorder', auth, {'ids': self.ids(MAX_REORDER_IDS)})
        too_long = post(client, '/labels/reorder', auth, {'ids': self.ids(MAX_REORDER_IDS + 1)})

        assert ok.status_code == 200 and too_long.status_code == 422

    def test_the_filter_reorder_list_is_bounded(self, client, auth):
        ok = post(client, '/filters/reorder', auth, {'ids': self.ids(50)})
        too_long = post(client, '/filters/reorder', auth, {'ids': self.ids(51)})

        assert ok.status_code == 200 and too_long.status_code == 422


class TestCancelUpload:
    @pytest.fixture
    def task(self, funded_user):
        return create_task(funded_user, title='With files')

    def test_a_pending_upload_is_cancelled(self, client, auth, task, fake_storage):
        pending = make_attachment(task, fake_storage, uploaded=False)
        key = attachment_key(task.user_id, pending.pk)
        fake_storage.seed(key, PNG_BYTES)  # the PUT landed

        response = post(client, f'/attachments/{pending.pk}/cancel', auth)

        assert response.status_code == 204
        assert not Attachment.objects.filter(pk=pending.pk).exists()
        assert key not in fake_storage.objects

    def test_it_is_idempotent(self, client, auth, task, fake_storage):
        pending = make_attachment(task, fake_storage, uploaded=False)

        first = post(client, f'/attachments/{pending.pk}/cancel', auth)
        second = post(client, f'/attachments/{pending.pk}/cancel', auth)
        unknown = post(client, f'/attachments/{uuid.uuid4()}/cancel', auth)

        assert first.status_code == second.status_code == unknown.status_code == 204

    def test_a_completed_file_is_a_400_and_stays(self, client, auth, task, fake_storage):
        done = make_attachment(task, fake_storage)

        response = post(client, f'/attachments/{done.pk}/cancel', auth)

        assert response.status_code == 400
        assert Attachment.objects.filter(pk=done.pk).exists()
        assert attachment_key(task.user_id, done.pk) in fake_storage.objects

    def test_another_users_upload_is_left_alone(self, client, auth, other_user, fake_storage):
        theirs = make_attachment(
            create_task(other_user, title='Theirs'), fake_storage, uploaded=False
        )
        key = attachment_key(other_user.pk, theirs.pk)
        fake_storage.seed(key, PNG_BYTES)

        response = post(client, f'/attachments/{theirs.pk}/cancel', auth)

        assert response.status_code == 204  # as for any id that isn't there
        assert Attachment.objects.filter(pk=theirs.pk).exists()
        assert key in fake_storage.objects

    def test_cancelling_frees_the_slot_toward_the_task_cap(
        self, client, auth, task, fake_storage, funded_user
    ):
        TodoSettings.objects.update(max_attachments_per_task=1)
        body = {
            'filename': 'a.png',
            'size': len(PNG_BYTES),
            'checksum_sha256': __import__('base64')
            .b64encode(__import__('hashlib').sha256(PNG_BYTES).digest())
            .decode(),
        }
        first = post(client, f'/tasks/{task.number}/attachments', auth, body)
        assert first.status_code == 201
        assert post(client, f'/tasks/{task.number}/attachments', auth, body).status_code == 400

        post(client, f'/attachments/{first.json()["attachment_id"]}/cancel', auth)

        assert post(client, f'/tasks/{task.number}/attachments', auth, body).status_code == 201

    def test_the_edit_budget_is_charged_but_not_for_a_refusal(
        self, client, auth, task, fake_storage, settings
    ):
        done = make_attachment(task, fake_storage)
        pending = make_attachment(task, fake_storage, uploaded=False)
        limit_rule(settings, 'TODO_EDIT_USER')

        assert post(client, f'/attachments/{done.pk}/cancel', auth).status_code == 400
        assert post(client, f'/attachments/{pending.pk}/cancel', auth).status_code == 204
        assert post(client, f'/attachments/{pending.pk}/cancel', auth).status_code == 429


class TestSharedQuerysets:
    def test_the_web_lists_use_the_shared_sets(self, funded_user, wednesday, monkeypatch):
        from django.test import Client

        from ..views import lists

        seen: list[str] = []
        for name in ('upcoming_set', 'completed_set', 'trash_set'):
            real = getattr(dates, name)

            def spy(user, _real=real, _name=name):
                seen.append(_name)
                return _real(user)

            monkeypatch.setattr(lists, name, spy)
        done = create_task(funded_user, title='done')
        complete_task(done)
        client = Client()
        client.force_login(funded_user)

        for page in ('upcoming', 'completed', 'trash'):
            assert client.get(f'/todo/{page}/').status_code == 200

        assert seen == ['upcoming_set', 'completed_set', 'trash_set']

    def test_the_sets_are_what_the_pages_list(self, funded_user, wednesday):
        soon = create_task(
            funded_user, title='soon', due_date=date(2026, 10, 28) + timedelta(days=3)
        )
        create_task(funded_user, title='today', due_date=date(2026, 10, 28))

        assert list(dates.upcoming_set(funded_user)) == [soon]


class TestParseSchema:
    def test_the_openapi_response_is_a_schema_not_a_free_object(self, client):
        spec = client.get('/api/openapi.json').json()
        operation = spec['paths']['/api/todo/parse']['post']

        ref = operation['responses']['200']['content']['application/json']['schema']

        assert ref == {'$ref': '#/components/schemas/ParseAnswerSchema'}

    def test_unavailable_is_just_available_false(self, client, auth, monkeypatch):
        from ..services import parsing

        monkeypatch.setattr(parsing, '_client', None)

        response = post(client, '/parse', auth, {'title': 'pay rent on the 1st'})

        assert response.json() == {'available': False}

    def test_a_reading_has_the_documented_fields(self, client, auth, fake_title_parser, wednesday):
        fake_title_parser.replies[RENT] = reading()

        body = post(client, '/parse', auth, {'title': RENT}).json()

        assert set(body) == {
            'available',
            'has_date',
            'clean_title',
            'due_date',
            'due_time',
            'rrule',
            'rrule_text',
            'matched_text',
            'label',
        }
        assert body['due_time'] is None and body['due_date'] == '2026-11-01'
