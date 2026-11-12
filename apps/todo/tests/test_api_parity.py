"""To-do API 2.1 (issue #284): the additions the Android app needs for parity with the website --
`dir` on `GET /tasks`, comment files, the inbox's recent emails and a task's `email_from`, saved
filter counts, blocker suggestions, search snippets and the credit balance. Each calls the service
its web twin does, so these tests pin the contract (shapes, statuses, budgets, query counts)."""

import base64
import hashlib

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from ..models import Attachment, Comment, InboundEmail, InboundMessage
from ..services import (
    add_blocker,
    add_comment,
    complete_task,
    create_task,
    delete_comment,
    save_filter,
    trash_task,
)
from ..services import search as search_service
from ..services.filters import FilterParams
from ..services.inbound import INBOUND_PREFIX
from ..services.storage import attachment_key
from .api_support import (  # noqa: F401 -- the fixtures are used by name
    auth,
    delete,
    get,
    limit_rule,
    other_auth,
    patch,
    post,
)
from .factories import PNG_BYTES, make_task

pytestmark = [
    pytest.mark.django_db,
    pytest.mark.unit,
    pytest.mark.usefixtures('ratelimit_clock'),
]


def checksum(data: bytes) -> str:
    return base64.b64encode(hashlib.sha256(data).digest()).decode()


def numbers(response) -> list[int]:
    return [task['number'] for task in response.json()['results']]


def queries(call) -> int:
    with CaptureQueriesContext(connection) as captured:
        call()
    return len(captured)


# -- 1. dir --------------------------------------------------------------------------------------


class TestDirection:
    @pytest.fixture
    def tasks(self, funded_user, wednesday):
        from datetime import date

        first = create_task(funded_user, title='b first', due_date=date(2026, 11, 3), priority=2)
        second = create_task(funded_user, title='a second', due_date=date(2026, 11, 5), priority=1)
        third = create_task(funded_user, title='c third', priority=0)
        return first, second, third

    def test_each_sorts_default_direction_is_the_webs(self, client, auth, tasks):
        first, second, third = tasks

        def listed(**query):
            return numbers(get(client, '/tasks', auth, **query))

        assert (
            listed(sort='date')
            == listed(sort='date', dir='asc')
            == [first.number, second.number, third.number]
        )
        assert (
            listed(sort='title', dir='asc')
            == listed(sort='title')
            == [second.number, first.number, third.number]
        )
        assert listed(sort='created') == listed(sort='created', dir='desc')
        assert listed(sort='created', dir='desc') == [third.number, second.number, first.number]
        assert listed(sort='priority') == listed(sort='priority', dir='asc')

    def test_the_other_direction_is_the_exact_reverse(self, client, auth, tasks):
        for sort in ('date', 'priority', 'created', 'title'):
            default = numbers(get(client, '/tasks', auth, sort=sort))
            opposite = 'desc' if sort != 'created' else 'asc'
            assert numbers(get(client, '/tasks', auth, sort=sort, dir=opposite)) == default[::-1]

    def test_dir_alone_means_the_date_sort(self, client, auth, tasks):
        first, second, third = tasks

        assert numbers(get(client, '/tasks', auth, dir='desc')) == [
            third.number,
            second.number,
            first.number,
        ]

    def test_closed_tasks_stay_after_the_open_ones(self, client, auth, tasks, funded_user):
        first, second, third = tasks
        complete_task(second)

        asc = numbers(get(client, '/tasks', auth, include_closed='true', dir='asc'))
        desc = numbers(get(client, '/tasks', auth, include_closed='true', dir='desc'))

        assert asc == [first.number, third.number, second.number]
        assert desc == [third.number, first.number, second.number]

    def test_an_unknown_direction_is_a_400(self, client, auth, tasks):
        response = get(client, '/tasks', auth, dir='sideways')

        assert response.status_code == 400
        assert response.json()['detail'] == 'dir must be one of: asc, desc.'

    def test_it_works_with_a_view_and_with_filters(self, client, auth, tasks):
        first, second, third = tasks

        response = get(client, '/tasks', auth, view='upcoming', sort='date', dir='desc')

        assert numbers(response) == [second.number, first.number]
        filtered = get(client, '/tasks', auth, priority='1,2', sort='priority', dir='desc')
        assert numbers(filtered) == [first.number, second.number]


# -- 2. comment files ----------------------------------------------------------------------------


@pytest.fixture
def task(funded_user):
    return create_task(funded_user, title='With files')


def start(client, auth, task, *, data=PNG_BYTES, name='photo.png', **extra):
    return post(
        client,
        f'/tasks/{task.number}/attachments',
        auth,
        {
            'filename': name,
            'size': len(data),
            'content_type': 'image/png',
            'checksum_sha256': checksum(data),
            **extra,
        },
    )


def upload_draft(client, auth, task, fake_storage, *, data=PNG_BYTES, name='photo.png') -> str:
    """A comment file uploaded and completed through the API; its id."""
    ticket = start(client, auth, task, data=data, name=name, for_comment=True)
    assert ticket.status_code == 201, ticket.content
    attachment_id = ticket.json()['attachment_id']
    fake_storage.seed(attachment_key(task.user_id, attachment_id), data)
    done = post(client, f'/tasks/{task.number}/attachments/{attachment_id}/complete', auth)
    assert done.status_code == 200, done.content
    return attachment_id


class TestCommentFiles:
    def test_a_draft_may_be_started_on_a_completed_task_but_not_a_trashed_one(
        self, client, auth, task, funded_user
    ):
        complete_task(task)

        plain = start(client, auth, task)
        draft = start(client, auth, task, for_comment=True)

        assert plain.status_code == 400
        assert plain.json()['detail'] == 'Files cannot be attached to a completed task.'
        assert draft.status_code == 201
        assert Attachment.objects.get(pk=draft.json()['attachment_id']).for_comment is True

        trash_task(task)
        trashed = start(client, auth, task, for_comment=True)
        assert trashed.status_code == 400
        assert trashed.json()['detail'] == 'Files cannot be attached to a task in the trash.'

    def test_adding_a_comment_with_files(self, client, auth, task, fake_storage):
        attachment_id = upload_draft(client, auth, task, fake_storage)

        response = post(
            client,
            f'/tasks/{task.number}/comments',
            auth,
            {'body': 'See the photo', 'attachment_ids': [attachment_id]},
        )

        assert response.status_code == 201
        body = response.json()
        assert body['attachments'] == [
            {
                'id': attachment_id,
                'filename': 'photo.png',
                'size_bytes': len(PNG_BYTES),
                'inline_type': 'image/png',
                'inline': True,
            }
        ]
        # It shows with the comment, never in the task's own list or count.
        detail = get(client, f'/tasks/{task.number}', auth).json()
        assert detail['attachments'] == [] and detail['attachment_count'] == 0
        listed = get(client, f'/tasks/{task.number}/comments', auth).json()
        assert [item['id'] for item in listed[0]['attachments']] == [attachment_id]
        url = get(client, f'/attachments/{attachment_id}/url', auth, disposition='inline')
        assert url.status_code == 200 and url.json()['disposition'] == 'inline'

    def test_a_comment_without_files_has_an_empty_list(self, client, auth, task):
        body = post(client, f'/tasks/{task.number}/comments', auth, {'body': 'plain'}).json()

        assert body['attachments'] == []

    @pytest.mark.parametrize('kind', ['unknown', 'garbage', 'pending', 'attached', 'other'])
    def test_files_that_are_not_ready_refuse_the_comment_and_spend_nothing(
        self, client, auth, task, funded_user, other_user, fake_storage, settings, kind
    ):
        import uuid

        if kind == 'unknown':
            wrong = str(uuid.uuid4())
        elif kind == 'garbage':
            wrong = 'not-a-uuid'
        elif kind == 'pending':
            wrong = start(client, auth, task, for_comment=True).json()['attachment_id']
        elif kind == 'attached':
            used = upload_draft(client, auth, task, fake_storage)
            other_comment = post(
                client,
                f'/tasks/{task.number}/comments',
                auth,
                {'body': 'first', 'attachment_ids': [used]},
            )
            assert other_comment.status_code == 201
            wrong = used  # already on another comment: not takeable
        else:
            theirs = make_task(other_user, number=9100)
            ticket_file = Attachment.objects.create(
                task=theirs,
                user=other_user,
                filename='x.png',
                size_bytes=1,
                sha256='x',
                uploaded=True,
                for_comment=True,
            )
            wrong = str(ticket_file.pk)
        good = upload_draft(client, auth, task, fake_storage)
        limit_rule(settings, 'TODO_COMMENT_USER')

        refused = post(
            client,
            f'/tasks/{task.number}/comments',
            auth,
            {'body': 'nope', 'attachment_ids': [good, wrong]},
        )

        assert refused.status_code == 400
        assert (
            refused.json()['detail']
            == 'attachment_ids: A file is not ready to be attached. Wait for uploads to finish.'
        )
        # All or nothing: no comment, the good file still a free draft, and no budget spent.
        assert not Comment.objects.filter(task=task, body='nope').exists()
        assert Attachment.objects.get(pk=good).comment_id is None
        ok = post(
            client,
            f'/tasks/{task.number}/comments',
            auth,
            {'body': 'fine', 'attachment_ids': [good]},
        )
        assert ok.status_code == 201

    def test_a_draft_may_be_attached_to_a_comment_on_a_completed_task(
        self, client, auth, task, fake_storage
    ):
        attachment_id = upload_draft(client, auth, task, fake_storage)
        complete_task(task)

        response = post(
            client,
            f'/tasks/{task.number}/comments',
            auth,
            {'body': 'after the fact', 'attachment_ids': [attachment_id]},
        )

        assert response.status_code == 201 and len(response.json()['attachments']) == 1

    def test_editing_adds_and_removes_files(self, client, auth, task, funded_user, fake_storage):
        first = upload_draft(client, auth, task, fake_storage, name='one.png')
        comment = post(
            client,
            f'/tasks/{task.number}/comments',
            auth,
            {'body': 'text', 'attachment_ids': [first]},
        ).json()
        second = upload_draft(client, auth, task, fake_storage, name='two.png')
        path = f'/tasks/{task.number}/comments/{comment["id"]}'

        added = patch(client, path, auth, {'body': 'text', 'attachment_ids': [second]})
        removed = patch(
            client, path, auth, {'body': 'text', 'remove_attachment_ids': [first, 'junk']}
        )

        assert [item['filename'] for item in added.json()['attachments']] == ['one.png', 'two.png']
        # Changing only the files is not an edit of the text.
        assert added.json()['revision_count'] == 0 and added.json()['edited_at'] is None
        assert [item['filename'] for item in removed.json()['attachments']] == ['two.png']
        assert not Attachment.objects.filter(pk=first).exists()
        assert not fake_storage.object_exists(attachment_key(funded_user.pk, first))

    def test_a_refused_edit_changes_nothing_and_spends_nothing(
        self, client, auth, task, fake_storage, settings
    ):
        comment = post(client, f'/tasks/{task.number}/comments', auth, {'body': 'text'}).json()
        path = f'/tasks/{task.number}/comments/{comment["id"]}'
        limit_rule(settings, 'TODO_COMMENT_USER')

        refused = patch(client, path, auth, {'body': 'new', 'attachment_ids': ['nope']})
        empty = patch(client, path, auth, {'body': '  '})
        ok = patch(client, path, auth, {'body': 'new'})

        assert refused.status_code == 400 and 'attachment_ids:' in refused.json()['detail']
        assert empty.status_code == 400 and empty.json()['detail'] == 'body: Write a comment.'
        assert ok.status_code == 200 and ok.json()['body'] == 'new'

    def test_too_many_ids_is_a_422(self, client, auth, task):
        response = post(
            client,
            f'/tasks/{task.number}/comments',
            auth,
            {'body': 'x', 'attachment_ids': ['a'] * 101},
        )

        assert response.status_code == 422

    def test_a_deleted_comment_lists_no_files_unless_bodies_are_asked_for(
        self, client, auth, task, funded_user, fake_storage
    ):
        attachment_id = upload_draft(client, auth, task, fake_storage)
        comment = add_comment(task, funded_user, 'bye', [attachment_id])
        delete_comment(comment)

        hidden = get(client, f'/tasks/{task.number}/comments', auth).json()[0]
        shown = get(
            client, f'/tasks/{task.number}/comments', auth, include_deleted_bodies='1'
        ).json()[0]

        assert hidden['attachments'] == []
        assert [item['id'] for item in shown['attachments']] == [attachment_id]

    def test_listing_does_not_query_per_comment(
        self, client, auth, task, funded_user, fake_storage
    ):
        def add_one(index):
            file = upload_draft(client, auth, task, fake_storage, name=f'{index}.png')
            add_comment(task, funded_user, f'c{index}', [file])

        for index in range(2):
            add_one(index)
        few = queries(lambda: get(client, f'/tasks/{task.number}/comments', auth))
        for index in range(2, 8):
            add_one(index)
        many = queries(lambda: get(client, f'/tasks/{task.number}/comments', auth))

        assert few == many

    def test_another_users_task_is_a_404(self, client, other_auth, task):
        assert start(client, other_auth, task, for_comment=True).status_code == 404
        response = post(
            client,
            f'/tasks/{task.number}/comments',
            other_auth,
            {'body': 'x', 'attachment_ids': []},
        )
        assert response.status_code == 404


class TestDiscard:
    def test_a_draft_is_deleted(self, client, auth, task, funded_user, fake_storage):
        attachment_id = upload_draft(client, auth, task, fake_storage)

        response = post(client, f'/attachments/{attachment_id}/discard', auth)

        assert response.status_code == 204
        assert not Attachment.objects.filter(pk=attachment_id).exists()
        assert not fake_storage.object_exists(attachment_key(funded_user.pk, attachment_id))
        from ..models import TodoAccount

        assert TodoAccount.objects.get(pk=funded_user.pk).stored_bytes == 0

    def test_a_pending_draft_too(self, client, auth, task):
        attachment_id = start(client, auth, task, for_comment=True).json()['attachment_id']

        assert post(client, f'/attachments/{attachment_id}/discard', auth).status_code == 204
        assert not Attachment.objects.filter(pk=attachment_id).exists()

    def test_it_is_idempotent_and_free_for_what_is_gone(self, client, auth, settings):
        import uuid

        limit_rule(settings, 'TODO_EDIT_USER')

        for _ in range(3):
            assert post(client, f'/attachments/{uuid.uuid4()}/discard', auth).status_code == 204

    def test_a_saved_file_is_a_400(self, client, auth, task, funded_user, fake_storage, settings):
        draft = upload_draft(client, auth, task, fake_storage)
        add_comment(task, funded_user, 'kept', [draft])
        task_file = Attachment.objects.create(
            task=task, user=funded_user, filename='f', size_bytes=1, sha256='x', uploaded=True
        )
        limit_rule(settings, 'TODO_EDIT_USER')

        saved = post(client, f'/attachments/{draft}/discard', auth)
        plain = post(client, f'/attachments/{task_file.pk}/discard', auth)

        assert saved.status_code == plain.status_code == 400
        assert saved.json()['detail'] == 'This file is attached.'
        assert Attachment.objects.filter(pk=draft).exists()
        # Both refusals were free: the one hit of the budget is still there.
        free = upload_draft(client, auth, task, fake_storage)
        assert post(client, f'/attachments/{free}/discard', auth).status_code == 204

    def test_another_users_draft_is_left_alone(self, client, auth, other_auth, task, fake_storage):
        attachment_id = upload_draft(client, auth, task, fake_storage)

        response = post(client, f'/attachments/{attachment_id}/discard', other_auth)

        assert response.status_code == 204  # as `cancel`: nothing says whose it is
        assert Attachment.objects.filter(pk=attachment_id).exists()

    def test_it_spends_the_edit_budget(self, client, auth, task, fake_storage, settings):
        first = upload_draft(client, auth, task, fake_storage)
        second = upload_draft(client, auth, task, fake_storage)
        limit_rule(settings, 'TODO_EDIT_USER')

        assert post(client, f'/attachments/{first}/discard', auth).status_code == 204
        assert post(client, f'/attachments/{second}/discard', auth).status_code == 429

    def test_a_storage_failure_is_a_503_and_keeps_the_row(
        self, client, auth, task, fake_storage, monkeypatch
    ):
        from ..services import attachments

        attachment_id = upload_draft(client, auth, task, fake_storage)

        def boom(*args, **kwargs):
            raise RuntimeError('down')

        monkeypatch.setattr(attachments, 'delete_attachment', boom)

        response = post(client, f'/attachments/{attachment_id}/discard', auth)

        assert response.status_code == 503
        assert Attachment.objects.filter(pk=attachment_id).exists()


# -- 3. email intake -----------------------------------------------------------------------------


class TestEmailIntake:
    def test_a_task_from_an_email_says_who_sent_it(self, client, auth, funded_user, task):
        web = get(client, f'/tasks/{task.number}', auth).json()
        mailed = make_task(
            funded_user,
            number=4000,
            source='email',
            email_from='boss@work.test',
            email_from_authenticated=True,
        )
        unproven = make_task(funded_user, number=4001, source='email', email_from='x@y.test')

        body = get(client, f'/tasks/{mailed.number}', auth).json()

        assert web['email_from'] is None and web['email_from_authenticated'] is None
        assert body['email_from'] == 'boss@work.test' and body['email_from_authenticated'] is True
        assert (
            get(client, f'/tasks/{unproven.number}', auth).json()['email_from_authenticated']
            is False
        )
        listed = {item['number']: item for item in get(client, '/tasks', auth).json()['results']}
        assert listed[4000]['email_from'] == 'boss@work.test'
        assert listed[task.number]['email_from'] is None

    def test_recent_emails(self, client, auth, funded_user, other_user):
        message = InboundMessage.objects.create(s3_key=f'{INBOUND_PREFIX}m1')
        made = make_task(funded_user, number=4100)
        accepted = InboundEmail.objects.create(
            user=funded_user,
            message=message,
            recipient='todo+a@ziptrigo.com',
            from_address='boss@work.test',
            sender_authenticated=True,
            subject='Pay rent',
            status='accepted',
            note='1 of 2 files skipped: too large',
            task=made,
        )
        rejected = InboundEmail.objects.create(
            user=funded_user,
            message=message,
            recipient='todo+b@ziptrigo.com',
            from_address='spam@x.test',
            subject='Win',
            status='rejected_sender',
        )
        InboundEmail.objects.create(
            user=other_user, message=message, recipient='todo+c@x', from_address='theirs@x.test'
        )

        body = get(client, '/inbox', auth).json()

        by_id = {item['id']: item for item in body['recent_emails']}
        assert set(by_id) == {str(accepted.pk), str(rejected.pk)}
        assert by_id[str(accepted.pk)] == {
            'id': str(accepted.pk),
            'from_address': 'boss@work.test',
            'sender_authenticated': True,
            'subject': 'Pay rent',
            'received_at': None,
            'status': 'accepted',
            'note': '1 of 2 files skipped: too large',
            'task': 4100,
        }
        assert by_id[str(rejected.pk)]['task'] is None
        assert by_id[str(rejected.pk)]['status'] == 'rejected_sender'

    def test_only_the_latest_20_newest_first_in_a_constant_number_of_queries(
        self, client, auth, funded_user
    ):
        message = InboundMessage.objects.create(s3_key=f'{INBOUND_PREFIX}m2')
        for index in range(25):
            InboundEmail.objects.create(
                user=funded_user,
                message=message,
                recipient=f'r{index}',
                subject=f'Subject {index}',
                task=make_task(funded_user, number=4200 + index),
            )
        # (a first call creates the account and its address)
        get(client, '/inbox', auth)

        with CaptureQueriesContext(connection) as many:
            body = get(client, '/inbox', auth).json()

        assert len(body['recent_emails']) == 20
        assert body['recent_emails'][0]['subject'] == 'Subject 24'
        assert len([q for q in many.captured_queries if 'todo_inboundemail' in q['sql']]) == 1

    def test_regenerating_returns_the_same_shape(self, client, auth, funded_user):
        body = post(client, '/inbox/regenerate', auth).json()

        assert set(body) == {'address', 'sender_allowlist', 'recent_emails'}


# -- 4. filter counts ----------------------------------------------------------------------------


class TestFilterCounts:
    def test_counts_for_every_filter_in_one_query(self, client, auth, funded_user):
        create_task(funded_user, title='Pay rent')
        create_task(funded_user, title='Pay tax', priority=1)
        done = create_task(funded_user, title='Pay old bill')
        complete_task(done)
        rent = save_filter(funded_user, 'Pay', FilterParams(q='pay'))
        urgent = save_filter(funded_user, 'Urgent', FilterParams(priorities=(1,)))
        nothing = save_filter(funded_user, 'None', FilterParams(q='zzz'))

        response = get(client, '/filters/counts', auth)

        assert response.status_code == 200
        assert response.json() == {str(rent.pk): 2, str(urgent.pk): 1, str(nothing.pk): 0}

    def test_one_aggregate_query_whatever_the_number_of_filters(self, client, auth, funded_user):
        create_task(funded_user, title='Pay rent')
        save_filter(funded_user, 'one', FilterParams(q='pay'))
        get(client, '/filters/counts', auth)  # warm: the account row
        few = queries(lambda: get(client, '/filters/counts', auth))
        for index in range(8):
            save_filter(funded_user, f'f{index}', FilterParams(q=f'pay{index}'))
        many = queries(lambda: get(client, '/filters/counts', auth))

        assert few == many

    def test_a_filter_that_cannot_run_is_null(self, client, auth, funded_user):
        from ..models import SavedFilter

        good = save_filter(funded_user, 'Good', FilterParams(q='pay'))
        bad = save_filter(funded_user, 'Bad', FilterParams(q='pay'))
        SavedFilter.objects.filter(pk=bad.pk).update(params={'q': '/(/', 'mode': 'regex'})

        body = get(client, '/filters/counts', auth).json()

        assert body[str(good.pk)] == 0 and body[str(bad.pk)] is None

    def test_a_timeout_makes_every_count_null(self, client, auth, funded_user, monkeypatch):
        from django.core.exceptions import ValidationError

        from ..services import saved_filters

        first = save_filter(funded_user, 'A', FilterParams(q='pay'))
        second = save_filter(funded_user, 'B', FilterParams(priorities=(1,)))

        def slow(*args, **kwargs):
            raise ValidationError('too slow')

        monkeypatch.setattr(
            saved_filters, 'open_task_counts', lambda *a, **k: dict.fromkeys((first.pk, second.pk))
        )

        body = get(client, '/filters/counts', auth)

        assert body.status_code == 200
        assert body.json() == {str(first.pk): None, str(second.pk): None}

    def test_one_search_hit_and_over_the_limit_every_count_is_null(
        self, client, auth, funded_user, settings
    ):
        saved = save_filter(funded_user, 'A', FilterParams(q='pay'))
        other = save_filter(funded_user, 'B', FilterParams(q='rent'))
        limit_rule(settings, 'TODO_SEARCH_USER', limit=2)

        first = get(client, '/filters/counts', auth).json()
        second = get(client, '/filters/counts', auth).json()
        third = get(client, '/filters/counts', auth)

        # Two calls spent two hits (one each, however many filters), the third is over.
        assert first == {str(saved.pk): 0, str(other.pk): 0} == second
        assert third.status_code == 200
        assert third.json() == {str(saved.pk): None, str(other.pk): None}

    def test_no_filters_is_empty_and_free(self, client, auth, settings):
        limit_rule(settings, 'TODO_SEARCH_USER')

        assert get(client, '/filters/counts', auth).json() == {}
        assert get(client, '/filters/counts', auth).json() == {}

    def test_only_your_own_filters_are_counted(self, client, auth, other_user):
        theirs = save_filter(other_user, 'Theirs', FilterParams(q='x'))

        assert get(client, '/filters/counts', auth).json() == {}
        assert str(theirs.pk) not in get(client, '/filters/counts', auth).json()

    def test_it_needs_a_token(self, client):
        assert client.get('/api/todo/filters/counts').status_code == 401


# -- 5. blocker suggestions ----------------------------------------------------------------------


class TestBlockerSuggestions:
    def path(self, task):
        return f'/tasks/{task.number}/blockers/suggest'

    def test_open_tasks_that_would_be_accepted(self, client, auth, funded_user):
        main = create_task(funded_user, title='Main')
        pay = create_task(funded_user, title='Pay rent')
        current = create_task(funded_user, title='Call dentist')
        dependent = create_task(funded_user, title='Later work')
        via = create_task(funded_user, title='Later via')
        closed = create_task(funded_user, title='Paid already')
        complete_task(closed)
        trashed = create_task(funded_user, title='Pay trashed')
        trash_task(trashed)
        add_blocker(main, current)
        add_blocker(via, main)  # via depends on main ...
        add_blocker(dependent, via)  # ... and dependent on via: both are cycles if offered

        everything = get(client, self.path(main), auth).json()
        narrowed = get(client, self.path(main), auth, q='pa').json()

        assert [item['number'] for item in everything] == [pay.number]
        assert everything[0] == {'number': pay.number, 'title': 'Pay rent'}
        assert narrowed == [{'number': pay.number, 'title': 'Pay rent'}]

    def test_by_number_and_the_cap_of_ten(self, client, auth, funded_user):
        main = create_task(funded_user, title='Main')
        others = [create_task(funded_user, title=f'Task {i}') for i in range(15)]

        everything = get(client, self.path(main), auth).json()
        by_number = get(client, self.path(main), auth, q=f'#{others[3].number}').json()
        digits = get(client, self.path(main), auth, q=str(others[4].number)).json()

        assert len(everything) == 10
        assert [item['number'] for item in everything] == sorted(
            item['number'] for item in everything
        )
        assert [item['number'] for item in by_number] == [others[3].number]
        assert [item['number'] for item in digits] == [others[4].number]

    def test_another_users_tasks_are_never_offered_and_their_task_is_a_404(
        self, client, auth, other_auth, funded_user, other_user
    ):
        main = create_task(funded_user, title='Main')
        make_task(other_user, number=7006, title='Secret plan')

        assert get(client, self.path(main), auth, q='secret').json() == []
        assert get(client, self.path(main), other_auth).status_code == 404

    def test_a_trashed_task_can_still_be_asked_about(self, client, auth, funded_user):
        main = create_task(funded_user, title='Main')
        other = create_task(funded_user, title='Other')
        trash_task(main)

        assert [item['number'] for item in get(client, self.path(main), auth).json()] == [
            other.number
        ]

    def test_over_the_limit_is_an_empty_list_not_an_error(
        self, client, auth, funded_user, settings
    ):
        main = create_task(funded_user, title='Main')
        create_task(funded_user, title='Other')
        limit_rule(settings, 'TODO_BLOCKER_SUGGEST_USER')

        first = get(client, self.path(main), auth)
        second = get(client, self.path(main), auth)

        assert len(first.json()) == 1
        assert second.status_code == 200 and second.json() == []

    def test_a_missing_task_does_not_spend_the_budget(self, client, auth, funded_user, settings):
        main = create_task(funded_user, title='Main')
        create_task(funded_user, title='Other')
        limit_rule(settings, 'TODO_BLOCKER_SUGGEST_USER')

        assert get(client, '/tasks/999999/blockers/suggest', auth).status_code == 404
        assert len(get(client, self.path(main), auth).json()) == 1

    def test_the_route_does_not_shadow_removing_a_blocker(self, client, auth, funded_user):
        main = create_task(funded_user, title='Main')
        other = create_task(funded_user, title='Other')
        add_blocker(main, other)

        removed = delete(client, f'/tasks/{main.number}/blockers/{other.number}', auth)

        assert removed.status_code == 204
        assert get(client, self.path(main), auth).status_code == 200

    def test_a_long_query_is_a_422(self, client, auth, funded_user):
        main = create_task(funded_user, title='Main')

        assert get(client, self.path(main), auth, q='x' * 201).status_code == 422


# -- 6. search snippets --------------------------------------------------------------------------


class TestSnippets:
    def test_a_title_match(self, client, auth, funded_user):
        task = create_task(funded_user, title='Pay the RENT today')

        row = get(client, '/tasks', auth, q='rent').json()['results'][0]

        assert row['number'] == task.number
        assert row['search_field'] == 'title'
        assert row['search_snippet'] == 'pay the rent today'
        assert row['search_snippet'][row['match_start'] : row['match_end']] == 'rent'

    def test_description_and_comment_matches_and_plain_text(self, client, auth, funded_user):
        described = create_task(
            funded_user, title='Alpha', description='Remember <b>the landlord</b> & the rent'
        )
        commented = create_task(funded_user, title='Beta')
        add_comment(commented, funded_user, 'ask about the **landlord** tomorrow')

        rows = {
            row['number']: row
            for row in get(client, '/tasks', auth, q='landlord').json()['results']
        }

        row = rows[described.number]
        assert row['search_field'] == 'description'
        assert '<' not in row['search_snippet'] and '&' not in row['search_snippet']
        assert row['search_snippet'][row['match_start'] : row['match_end']] == 'landlord'
        row = rows[commented.number]
        assert row['search_field'] == 'comment'
        assert row['search_snippet'][row['match_start'] : row['match_end']] == 'landlord'

    def test_context_is_cut_with_ellipses_and_offsets_follow(self, client, auth, funded_user):
        text = ('word ' * 40) + 'needle' + (' word' * 40)
        create_task(funded_user, title='Long', description=text)

        row = get(client, '/tasks', auth, q='needle').json()['results'][0]

        snippet = row['search_snippet']
        assert snippet.startswith('…') and snippet.endswith('…')
        assert snippet[row['match_start'] : row['match_end']] == 'needle'
        assert len(snippet) < 150

    def test_a_long_match_is_cut_to_its_first_100_characters(self, client, auth, funded_user):
        create_task(funded_user, title='Long match', description='a' * 300)

        row = get(client, '/tasks', auth, q='a*', mode='glob').json()['results'][0]

        assert row['match_end'] - row['match_start'] <= 100

    def test_a_regex_and_a_task_number_search(self, client, auth, funded_user):
        task = create_task(funded_user, title='Pay rent')

        by_regex = get(client, '/tasks', auth, q='/p.y/').json()['results'][0]
        by_number = get(client, '/tasks', auth, q=str(task.number)).json()['results'][0]

        assert by_regex['search_snippet'][by_regex['match_start'] : by_regex['match_end']] == 'pay'
        # Found by its number only: the text doesn't contain it, so no snippet.
        assert by_number['number'] == task.number
        assert by_number['search_field'] == ''
        assert by_number['search_snippet'] is None
        assert by_number['match_start'] is None and by_number['match_end'] is None

    def test_without_a_search_the_fields_are_null(self, client, auth, funded_user):
        create_task(funded_user, title='Plain')

        row = get(client, '/tasks', auth).json()['results'][0]
        filtered = get(client, '/tasks', auth, priority='0').json()['results'][0]

        for item in (row, filtered):
            assert item['search_field'] is None and item['search_snippet'] is None
            assert item['match_start'] is None and item['match_end'] is None

    def test_a_spent_budget_lists_the_rows_without_snippets(
        self, client, auth, funded_user, monkeypatch
    ):
        for index in range(3):
            create_task(funded_user, title=f'rent {index}')
        monkeypatch.setattr(search_service, 'SNIPPET_TIME_BUDGET', 0)

        body = get(client, '/tasks', auth, q='rent').json()

        assert body['count'] == 3
        assert all(
            row['search_field'] == '' and row['search_snippet'] is None for row in body['results']
        )

    def test_only_the_scan_cap_is_read(self, client, auth, funded_user, monkeypatch):
        create_task(funded_user, title='Far', description=('x ' * 3000) + 'needle')

        row = get(client, '/tasks', auth, q='needle').json()['results'][0]

        # The match is past `SNIPPET_SCAN_MAX`: listed (it matches), without a snippet.
        assert row['search_field'] == '' and row['search_snippet'] is None

    def test_no_query_per_row(self, client, auth, funded_user):
        def make(count):
            for index in range(count):
                task = create_task(funded_user, title=f'unique{index} rent')
                add_comment(task, funded_user, 'a comment')

        make(2)
        get(client, '/tasks', auth, q='rent')  # warm
        few = queries(lambda: get(client, '/tasks', auth, q='rent'))
        make(10)
        many = queries(lambda: get(client, '/tasks', auth, q='rent'))

        assert few == many

    def test_a_filter_run_carries_snippets_too(self, client, auth, funded_user):
        create_task(funded_user, title='Pay rent')
        saved = save_filter(funded_user, 'Rent', FilterParams(q='rent'))
        plain = save_filter(funded_user, 'All', FilterParams(priorities=(0,)))

        with_text = get(client, f'/filters/{saved.pk}/tasks', auth).json()['results'][0]
        without = get(client, f'/filters/{plain.pk}/tasks', auth).json()['results'][0]

        assert with_text['search_field'] == 'title'
        assert (
            with_text['search_snippet'][with_text['match_start'] : with_text['match_end']] == 'rent'
        )
        assert without['search_field'] is None

    def test_the_snippet_pass_is_the_webs(self, funded_user):
        """The API's plain snippet is the very `(before, hit, after)` the HTML one is made from."""
        from apps.core.search import make_snippet, snippet_parts

        class Match:
            def span(self):
                return (70, 76)

        text = 'x' * 70 + 'needle' + 'y' * 70
        before, hit, after = snippet_parts(text, Match())

        assert hit == 'needle' and before.startswith('…') and after.endswith('…')
        assert str(make_snippet(text, Match())) == f'{before}<mark>{hit}</mark>{after}'


# -- 7. credits ----------------------------------------------------------------------------------


class TestCredits:
    def test_the_balance(self, client, auth, funded_user):
        assert get(client, '/settings', auth).json()['credits_balance'] == 100

    def test_it_follows_the_balance(self, client, auth, funded_user):
        from apps.billing.services import add_credits

        add_credits(funded_user, 25, description='More', source='test')

        assert get(client, '/settings', auth).json()['credits_balance'] == 125

    def test_a_user_with_no_credit_account_has_zero(self, client, other_user, other_auth):
        assert get(client, '/settings', other_auth).json()['credits_balance'] == 0

    def test_patch_answers_it_too_and_it_is_read_only(self, client, auth):
        body = patch(client, '/settings', auth, {'snooze_minutes': 60}).json()
        refused = patch(client, '/settings', auth, {'credits_balance': 5})

        assert body['credits_balance'] == 100
        assert refused.status_code == 422
