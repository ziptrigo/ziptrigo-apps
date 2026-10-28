"""The To-do JWT API's saved-filter, settings, inbox and parse endpoints (`/api/todo/filters`,
`/settings`, `/inbox`, `/parse`)."""

import uuid
from datetime import date, time
from typing import Any

import pytest

from ..models import InboxCode, SavedFilter, TodoAccount
from ..services import complete_task, create_task, get_account, save_filter
from ..services.filters import FilterParams
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
from .factories import make_label

pytestmark = [pytest.mark.django_db, pytest.mark.unit]

FILTER_KEYS = {'id', 'name', 'position', 'params', 'summary', 'created_at'}


def numbers(response) -> list[int]:
    return [task['number'] for task in response.json()['results']]


class TestFilters:
    def test_create_and_list(self, client, auth, funded_user):
        label = make_label(funded_user, 'Home')

        created = post(
            client,
            '/filters',
            auth,
            {
                'name': '  Urgent   home ',
                'params': {
                    'q': 'rent*',
                    'priorities': [1, 2],
                    'labels': [str(label.pk), 'none'],
                    'date_range': 'next_7_days',
                    'sort': 'priority',
                },
            },
        )

        assert created.status_code == 201
        body = created.json()
        assert set(body) == FILTER_KEYS
        assert body['name'] == 'Urgent home' and body['position'] == 1
        assert body['params']['q'] == 'rent*' and body['params']['priorities'] == [1, 2]
        assert body['params']['labels'] == sorted([str(label.pk), 'none'])
        assert (
            body['params']['date_range'] == 'next_7_days' and body['params']['sort'] == 'priority'
        )
        assert 'next 7 days' in body['summary'] and '@Home' in body['summary']
        assert get(client, '/filters', auth).json() == [body]

    def test_the_params_default_to_everything(self, client, auth):
        body = post(client, '/filters', auth, {'name': 'All open'}).json()

        assert body['params'] == FilterParams().to_dict()

    def test_a_label_that_is_not_yours_is_dropped(self, client, auth, other_user):
        theirs = make_label(other_user, 'Theirs')

        body = post(
            client, '/filters', auth, {'name': 'x', 'params': {'labels': [str(theirs.pk)]}}
        ).json()

        assert body['params']['labels'] == []

    @pytest.mark.parametrize(
        ('body', 'message'),
        [
            ({'name': ' '}, 'name: Name is required.'),
            ({'name': 'x', 'params': {'q': '/(/'}}, 'Invalid regular expression.'),
            ({'name': 'x', 'params': {'q': 'x' * 201}}, 'The pattern is too long.'),
        ],
    )
    def test_refusals_are_a_400(self, client, auth, body, message):
        response = post(client, '/filters', auth, body)

        assert response.status_code == 400 and message in response.json()['detail']

    @pytest.mark.parametrize(
        'params',
        [
            {'mode': 'fuzzy'},
            {'date_range': 'soon'},
            {'sort': 'size'},
            {'priorities': [4]},
            {'labels': ['home']},
            {'bogus': 1},
        ],
    )
    def test_a_malformed_params_is_a_422(self, client, auth, params):
        assert post(client, '/filters', auth, {'name': 'x', 'params': params}).status_code == 422

    def test_a_duplicate_name_is_a_400(self, client, auth, funded_user):
        save_filter(funded_user, 'Mine', FilterParams())

        response = post(client, '/filters', auth, {'name': 'MINE'})

        assert response.status_code == 400
        assert response.json()['detail'] == 'name: You already have a filter with this name.'

    def test_rename_and_replace_the_params(self, client, auth, funded_user):
        saved = save_filter(funded_user, 'Old', FilterParams(q='rent'))

        renamed = patch(client, f'/filters/{saved.pk}', auth, {'name': 'New'}).json()
        replaced = patch(
            client, f'/filters/{saved.pk}', auth, {'params': {'priorities': [3]}}
        ).json()

        assert renamed['name'] == 'New' and renamed['params']['q'] == 'rent'
        assert replaced['name'] == 'New'
        assert replaced['params']['q'] == '' and replaced['params']['priorities'] == [3]

    def test_a_refused_update_changes_nothing(self, client, auth, funded_user):
        save_filter(funded_user, 'Taken', FilterParams())
        saved = save_filter(funded_user, 'Mine', FilterParams(q='rent'))

        response = patch(
            client,
            f'/filters/{saved.pk}',
            auth,
            {'name': 'Renamed', 'params': {'q': '/(/'}},
        )

        assert response.status_code == 400
        saved.refresh_from_db()
        assert saved.name == 'Mine' and saved.params['q'] == 'rent'

    def test_reorder(self, client, auth, funded_user):
        a = save_filter(funded_user, 'A', FilterParams())
        b = save_filter(funded_user, 'B', FilterParams())

        response = post(client, '/filters/reorder', auth, {'ids': [str(b.pk), str(a.pk)]})

        assert [f['name'] for f in response.json()] == ['B', 'A']
        too_many = post(client, '/filters/reorder', auth, {'ids': [str(uuid.uuid4())] * 51})
        assert too_many.status_code == 422

    def test_delete(self, client, auth, funded_user):
        saved = save_filter(funded_user, 'Gone', FilterParams())

        assert delete(client, f'/filters/{saved.pk}', auth).status_code == 204

        assert not SavedFilter.objects.exists()
        assert delete(client, f'/filters/{saved.pk}', auth).status_code == 404

    def test_another_users_filter_is_a_404(self, client, auth, other_user):
        theirs = save_filter(other_user, 'Theirs', FilterParams())

        for call in (
            lambda: patch(client, f'/filters/{theirs.pk}', auth, {'name': 'x'}),
            lambda: delete(client, f'/filters/{theirs.pk}', auth),
            lambda: get(client, f'/filters/{theirs.pk}/tasks', auth),
        ):
            assert call().status_code == 404
        assert get(client, '/filters', auth).json() == []

    def test_run(self, client, auth, funded_user, wednesday):
        label = make_label(funded_user, 'Home')
        hit = create_task(funded_user, title='Pay the rent', priority=1, labels=[label])
        create_task(funded_user, title='Pay the rent too', priority=2, labels=[label])
        create_task(funded_user, title='Walk the dog', priority=1, labels=[label])
        done = complete_task(create_task(funded_user, title='Old rent', priority=1))
        saved = save_filter(
            funded_user,
            'Rent',
            FilterParams(
                q='rent', priorities=(1,), labels=(str(label.pk),), include_completed=True
            ),
        )

        response = get(client, f'/filters/{saved.pk}/tasks', auth)

        assert response.status_code == 200
        assert set(response.json()) == {'count', 'limit', 'offset', 'results'}
        assert numbers(response) == [hit.number]  # `Old rent` has no label
        assert done.number not in numbers(response)

    def test_run_includes_completed_ones_last_and_pages(self, client, auth, funded_user):
        first = create_task(funded_user, title='rent a')
        done = complete_task(create_task(funded_user, title='rent b'))
        last = create_task(funded_user, title='rent c')
        saved = save_filter(funded_user, 'Rent', FilterParams(q='rent', include_completed=True))

        everything = numbers(get(client, f'/filters/{saved.pk}/tasks', auth))
        paged = get(client, f'/filters/{saved.pk}/tasks', auth, limit=1, offset=2).json()

        assert everything == [first.number, last.number, done.number]
        assert paged['count'] == 3 and [t['number'] for t in paged['results']] == [done.number]

    def test_a_label_deleted_since_drops_out_of_the_filter(self, client, auth, funded_user):
        label = make_label(funded_user, 'Home')
        task = create_task(funded_user, title='x')
        saved = save_filter(funded_user, 'Home', FilterParams(labels=(str(label.pk),)))
        label.delete()

        listed = get(client, '/filters', auth).json()[0]

        assert listed['params']['labels'] == []
        assert numbers(get(client, f'/filters/{saved.pk}/tasks', auth)) == [task.number]

    def test_the_rate_limits(self, client, auth, funded_user, settings):
        saved = save_filter(funded_user, 'Rent', FilterParams(q='rent'))
        limit_rule(settings, 'TODO_SEARCH_USER')
        limit_rule(settings, 'TODO_EDIT_USER')

        assert get(client, f'/filters/{saved.pk}/tasks', auth).status_code == 200
        assert get(client, f'/filters/{saved.pk}/tasks', auth).status_code == 429
        assert get(client, '/filters', auth).status_code == 200  # listing is free
        assert post(client, '/filters', auth, {'name': 'a'}).status_code == 201
        assert post(client, '/filters', auth, {'name': 'b'}).status_code == 429


class TestSettings:
    def test_defaults(self, client, auth, funded_user, config):
        body = get(client, '/settings', auth).json()

        assert body == {
            'default_due_time': '09:00:00',
            'default_reminder_minutes': 5,
            'default_reminder_channel': 'in_app',
            'sender_allowlist': [],
            'tasks_created_this_month': 0,
            'free_tasks_per_month': 2,
            'task_price': 5,
            'creation_blocked': False,
            'stored_bytes': 0,
            'storage_credits_per_day': '0.0000',
            'storage_frozen': False,
        }

    def test_usage_and_storage_figures(self, client, auth, funded_user, fake_storage):
        from .factories import make_attachment

        task = create_task(funded_user, title='a')
        create_task(funded_user, title='b')
        make_attachment(task, fake_storage)
        TodoAccount.objects.filter(pk=funded_user.pk).update(frozen_at=task.created_at)

        body = get(client, '/settings', auth).json()

        assert body['tasks_created_this_month'] == 2 and body['stored_bytes'] > 0
        assert body['storage_frozen'] is True

    def test_change_the_defaults(self, client, auth, funded_user):
        body = patch(
            client,
            '/settings',
            auth,
            {
                'default_due_time': '18:30',
                'default_reminder_minutes': 60,
                'default_reminder_channel': 'both',
                'sender_allowlist': ['Me@Example.com', '*@Work.example'],
            },
        )

        assert body.status_code == 200
        assert body.json()['default_due_time'] == '18:30:00'
        assert body.json()['default_reminder_minutes'] == 60
        assert body.json()['default_reminder_channel'] == 'both'
        assert body.json()['sender_allowlist'] == ['me@example.com', '*@work.example']
        account = get_account(funded_user)
        assert account.default_due_time == time(18, 30)

    def test_only_the_fields_present_change(self, client, auth, funded_user):
        patch(client, '/settings', auth, {'default_reminder_minutes': 30})
        body = patch(client, '/settings', auth, {'default_reminder_channel': 'email'}).json()

        assert (
            body['default_reminder_minutes'] == 30 and body['default_reminder_channel'] == 'email'
        )
        assert body['default_due_time'] == '09:00:00'

    def test_a_null_clears(self, client, auth, funded_user):
        patch(client, '/settings', auth, {'sender_allowlist': ['a@example.com']})

        body = patch(
            client, '/settings', auth, {'default_reminder_minutes': None, 'sender_allowlist': None}
        ).json()

        assert body['default_reminder_minutes'] is None and body['sender_allowlist'] == []

    def test_a_new_task_gets_the_default_reminder(self, client, auth, funded_user):
        patch(client, '/settings', auth, {'default_reminder_minutes': 120})

        body = post(client, '/tasks', auth, {'title': 'x', 'due_date': '2026-12-01'}).json()

        assert [r['minutes_before'] for r in body['reminders']] == [120]

    @pytest.mark.parametrize(
        ('body', 'message'),
        [
            ({'default_due_time': None}, 'default_due_time: Enter a time.'),
            ({'default_reminder_minutes': 99_999}, 'minutes_before: Choose a reminder time'),
            ({'default_reminder_channel': 'sms'}, 'channel: Choose in-app, email or both.'),
            ({'default_reminder_channel': None}, 'channel: Choose in-app, email or both.'),
            ({'sender_allowlist': ['not an address']}, 'sender_allowlist:'),
        ],
    )
    def test_refusals_are_a_400_and_change_nothing(self, client, auth, funded_user, body, message):
        response = patch(client, '/settings', auth, {'default_due_time': '07:00', **body})

        assert response.status_code == 400 and message in response.json()['detail']
        assert get_account(funded_user).default_due_time == time(9, 0)

    def test_an_unknown_key_is_a_422(self, client, auth):
        for body in ({'task_price': 0}, {'stored_bytes': 0}, {'default_due_time': 'noon'}):
            assert patch(client, '/settings', auth, body).status_code == 422

    def test_an_empty_patch_spends_nothing(self, client, auth, settings):
        limit_rule(settings, 'TODO_EDIT_USER')

        assert patch(client, '/settings', auth, {}).status_code == 200
        assert patch(client, '/settings', auth, {'default_reminder_minutes': 9}).status_code == 200
        assert patch(client, '/settings', auth, {'default_reminder_minutes': 8}).status_code == 429


class TestInbox:
    def test_the_address(self, client, auth, funded_user, settings):
        settings.TODO_INBOUND_ADDRESS = 'todo@ziptrigo.com'

        body = get(client, '/inbox', auth).json()

        code = InboxCode.objects.get(account__user=funded_user, active=True).code
        assert body == {'address': f'todo+{code}@ziptrigo.com', 'sender_allowlist': []}
        assert get(client, '/inbox', auth).json() == body  # stable

    def test_it_shows_the_allowlist(self, client, auth):
        patch(client, '/settings', auth, {'sender_allowlist': ['a@example.com']})

        assert get(client, '/inbox', auth).json()['sender_allowlist'] == ['a@example.com']

    def test_regenerate_retires_the_old_address(self, client, auth, funded_user):
        before = get(client, '/inbox', auth).json()['address']

        after = post(client, '/inbox/regenerate', auth).json()['address']

        assert after != before
        assert InboxCode.objects.filter(account__user=funded_user, active=False).count() == 1
        assert get(client, '/inbox', auth).json()['address'] == after

    def test_each_user_has_their_own(self, client, auth, other_auth):
        assert get(client, '/inbox', auth).json() != get(client, '/inbox', other_auth).json()

    def test_regenerate_is_limited(self, client, auth, settings):
        limit_rule(settings, 'TODO_REGENERATE_USER')

        assert post(client, '/inbox/regenerate', auth).status_code == 200
        second = post(client, '/inbox/regenerate', auth)

        assert second.status_code == 429 and 'Retry-After' in second


class TestParse:
    def reply(self, fake, **fields: Any):
        defaults: dict[str, Any] = {
            'has_date': True,
            'clean_title': 'pay rent',
            'due_date': date(2026, 11, 1),
            'matched_text': 'every month on the 1st',
            'rrule': 'FREQ=MONTHLY;BYMONTHDAY=1',
            'confidence': 0.9,
        }
        fake.replies['pay rent every month on the 1st'] = ParsedTitle(**{**defaults, **fields})

    def test_a_title_with_a_date(self, client, auth, fake_title_parser, wednesday):
        self.reply(fake_title_parser, due_time=time(9, 0))

        response = post(client, '/parse', auth, {'title': ' pay rent  every month on the 1st '})

        assert response.status_code == 200
        body = response.json()
        assert body == {
            'available': True,
            'has_date': True,
            'clean_title': 'pay rent',
            'due_date': '2026-11-01',
            'due_time': '09:00',
            'rrule': 'FREQ=MONTHLY;BYMONTHDAY=1',
            'rrule_text': 'Every month on the 1st',
            'matched_text': 'every month on the 1st',
            'label': 'Sun 1 Nov, 09:00 · Every month on the 1st',
        }
        assert fake_title_parser.requests[0].title == 'pay rent every month on the 1st'
        assert fake_title_parser.requests[0].today == date(2026, 10, 28)

    def test_the_same_body_as_the_web_endpoint(
        self, client, web, auth, fake_title_parser, wednesday
    ):
        import json

        self.reply(fake_title_parser)
        title = 'pay rent every month on the 1st'

        api = post(client, '/parse', auth, {'title': title}).json()
        page = web.post(
            '/todo/parse/', json.dumps({'title': title}), content_type='application/json'
        ).json()

        assert api == page

    def test_a_title_without_a_date(self, client, auth, fake_title_parser):
        body = post(client, '/parse', auth, {'title': 'call mom'}).json()

        assert body['available'] is True and body['has_date'] is False
        assert body['clean_title'] == 'call mom' and body['due_date'] is None
        assert fake_title_parser.calls == 0

    def test_a_failing_reader_asks_to_retry(self, client, auth, fake_title_parser):
        fake_title_parser.replies['pay rent every month on the 1st'] = ParseUnavailable('slow')

        body = post(client, '/parse', auth, {'title': 'pay rent every month on the 1st'}).json()

        assert body == {'available': True, 'has_date': False, 'retry': True}

    def test_an_unconfigured_reader(self, client, auth, monkeypatch):
        from ..services import parsing

        monkeypatch.setattr(parsing, '_client', None)

        assert post(client, '/parse', auth, {'title': 'x tomorrow'}).json() == {'available': False}

    @pytest.mark.parametrize('title', ['   ', 'x' * 501])
    def test_a_bad_title_is_a_422(self, client, auth, title):
        response = post(client, '/parse', auth, {'title': title})

        assert response.status_code == 422 and isinstance(response.json()['detail'], str)

    def test_a_missing_title_is_a_422(self, client, auth):
        assert post(client, '/parse', auth, {}).status_code == 422

    def test_both_limits_are_spent_only_when_the_model_is_called(
        self, client, auth, fake_title_parser, settings
    ):
        self.reply(fake_title_parser)
        limit_rule(settings, 'TODO_PARSE_USER')
        title = 'pay rent every month on the 1st'

        assert post(client, '/parse', auth, {'title': 'call mom'}).status_code == 200  # no call
        assert post(client, '/parse', auth, {'title': 'call mom'}).status_code == 200
        assert post(client, '/parse', auth, {'title': title}).status_code == 200
        second = post(client, '/parse', auth, {'title': title})

        assert second.status_code == 429 and 'Retry-After' in second
        assert fake_title_parser.calls == 1

    def test_the_daily_limit(self, client, auth, fake_title_parser, settings):
        self.reply(fake_title_parser)
        limit_rule(settings, 'TODO_PARSE_USER_DAY')
        title = 'pay rent every month on the 1st'

        assert post(client, '/parse', auth, {'title': title}).status_code == 200
        assert post(client, '/parse', auth, {'title': title}).status_code == 429
