"""The To-do JWT API's reminder and attachment endpoints (`/api/todo/tasks/{number}/reminders`,
`/reminders/history`, `.../attachments`, `/attachments/{id}`)."""

import uuid
from datetime import date

import pytest
from django.utils import timezone

from ..models import Attachment, Reminder, ReminderDelivery, TodoAccount
from ..services import complete_task, create_task, get_account, trash_task
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
from .factories import PNG_BYTES, make_attachment, make_task
from .fakes import b64_sha256

pytestmark = [pytest.mark.django_db, pytest.mark.unit]

REMINDER_KEYS = {'id', 'minutes_before', 'channel', 'fire_at', 'sent_at'}


@pytest.fixture
def dated(funded_user, wednesday):
    """A task with a date and no reminders."""
    task = create_task(funded_user, title='Dated', due_date=date(2026, 11, 1))
    Reminder.objects.filter(task=task).delete()
    return task


class TestReminders:
    def test_add(self, client, auth, dated, funded_user):
        response = post(client, f'/tasks/{dated.number}/reminders', auth, {'minutes_before': 30})

        assert response.status_code == 201
        body = response.json()
        assert set(body) == REMINDER_KEYS
        assert body['minutes_before'] == 30 and body['channel'] == 'in_app'
        assert body['sent_at'] is None and body['fire_at'].startswith('2026-11-01T08:30')
        assert Reminder.objects.filter(task=dated).count() == 1

    def test_the_channel_defaults_to_the_accounts(self, client, auth, dated, funded_user):
        account = get_account(funded_user)
        account.default_reminder_channel = 'both'
        account.save()

        body = post(client, f'/tasks/{dated.number}/reminders', auth, {'minutes_before': 1}).json()
        explicit = post(
            client,
            f'/tasks/{dated.number}/reminders',
            auth,
            {'minutes_before': 2, 'channel': 'email'},
        ).json()

        assert body['channel'] == 'both' and explicit['channel'] == 'email'

    @pytest.mark.parametrize(
        ('body', 'message'),
        [
            ({'minutes_before': -1}, 'minutes_before: Choose a reminder time'),
            ({'minutes_before': 50_000}, 'minutes_before: Choose a reminder time'),
            ({'minutes_before': 5, 'channel': 'sms'}, 'channel: Choose in-app, email or both.'),
        ],
    )
    def test_refusals_are_a_400(self, client, auth, dated, body, message):
        response = post(client, f'/tasks/{dated.number}/reminders', auth, body)

        assert response.status_code == 400 and message in response.json()['detail']

    def test_a_duplicate_time_is_a_400(self, client, auth, dated):
        post(client, f'/tasks/{dated.number}/reminders', auth, {'minutes_before': 5})

        response = post(client, f'/tasks/{dated.number}/reminders', auth, {'minutes_before': 5})

        assert response.status_code == 400
        assert (
            response.json()['detail'] == 'minutes_before: There is already a reminder at that time.'
        )

    def test_a_task_without_a_date_has_none(self, client, auth, funded_user):
        task = create_task(funded_user, title='Undated')

        response = post(client, f'/tasks/{task.number}/reminders', auth, {'minutes_before': 5})

        assert response.status_code == 400
        assert response.json()['detail'] == 'Set a due date first.'

    def test_a_completed_task_has_none(self, client, auth, dated):
        complete_task(dated)

        assert (
            post(
                client, f'/tasks/{dated.number}/reminders', auth, {'minutes_before': 5}
            ).status_code
            == 400
        )

    def test_at_most_ten(self, client, auth, dated):
        for minutes in range(10):
            assert (
                post(
                    client, f'/tasks/{dated.number}/reminders', auth, {'minutes_before': minutes}
                ).status_code
                == 201
            )

        response = post(client, f'/tasks/{dated.number}/reminders', auth, {'minutes_before': 99})

        assert response.status_code == 400 and 'at most 10' in response.json()['detail']

    def test_update_the_time_and_the_channel(self, client, auth, dated):
        reminder = post(
            client, f'/tasks/{dated.number}/reminders', auth, {'minutes_before': 5}
        ).json()

        moved = patch(
            client,
            f'/tasks/{dated.number}/reminders/{reminder["id"]}',
            auth,
            {'minutes_before': 60},
        )
        channel = patch(
            client, f'/tasks/{dated.number}/reminders/{reminder["id"]}', auth, {'channel': 'email'}
        )
        nothing = patch(
            client, f'/tasks/{dated.number}/reminders/{reminder["id"]}', auth, {'channel': None}
        )

        assert moved.json()['minutes_before'] == 60 and moved.json()['fire_at'].startswith(
            '2026-11-01T08:00'
        )
        assert channel.json()['channel'] == 'email' and channel.json()['minutes_before'] == 60
        assert nothing.json()['channel'] == 'email'

    def test_update_to_a_taken_time_is_a_400(self, client, auth, dated):
        post(client, f'/tasks/{dated.number}/reminders', auth, {'minutes_before': 5})
        other = post(
            client, f'/tasks/{dated.number}/reminders', auth, {'minutes_before': 10}
        ).json()

        response = patch(
            client, f'/tasks/{dated.number}/reminders/{other["id"]}', auth, {'minutes_before': 5}
        )

        assert response.status_code == 400

    def test_delete(self, client, auth, dated):
        reminder = post(
            client, f'/tasks/{dated.number}/reminders', auth, {'minutes_before': 5}
        ).json()

        assert (
            delete(client, f'/tasks/{dated.number}/reminders/{reminder["id"]}', auth).status_code
            == 204
        )

        assert not Reminder.objects.filter(task=dated).exists()
        assert (
            delete(client, f'/tasks/{dated.number}/reminders/{reminder["id"]}', auth).status_code
            == 404
        )

    def test_reminders_show_on_the_task(self, client, auth, dated):
        post(client, f'/tasks/{dated.number}/reminders', auth, {'minutes_before': 5})
        post(client, f'/tasks/{dated.number}/reminders', auth, {'minutes_before': 60})

        body = get(client, f'/tasks/{dated.number}', auth).json()

        assert [r['minutes_before'] for r in body['reminders']] == [60, 5]  # the earliest first

    def test_a_reminder_of_another_task_or_user_is_a_404(
        self, client, auth, dated, funded_user, other_user
    ):
        mine = create_task(funded_user, title='Other', due_date=date(2026, 11, 2))
        reminder = Reminder.objects.get(task=mine)
        theirs = make_task(other_user, title='Theirs', due_date=date(2026, 11, 2))  # number 5000+

        assert (
            patch(
                client, f'/tasks/{dated.number}/reminders/{reminder.pk}', auth, {'channel': 'both'}
            ).status_code
            == 404
        )
        assert (
            delete(client, f'/tasks/{dated.number}/reminders/{reminder.pk}', auth).status_code
            == 404
        )
        assert (
            post(
                client, f'/tasks/{theirs.number}/reminders', auth, {'minutes_before': 5}
            ).status_code
            == 404
        )

    def test_an_unknown_key_is_a_422(self, client, auth, dated):
        assert (
            post(
                client, f'/tasks/{dated.number}/reminders', auth, {'minutes_before': 5, 'x': 1}
            ).status_code
            == 422
        )
        assert post(client, f'/tasks/{dated.number}/reminders', auth, {}).status_code == 422

    def test_the_edit_rate_limit(self, client, auth, dated, settings):
        limit_rule(settings, 'TODO_EDIT_USER')

        assert (
            post(
                client, f'/tasks/{dated.number}/reminders', auth, {'minutes_before': 5}
            ).status_code
            == 201
        )
        assert (
            post(
                client, f'/tasks/{dated.number}/reminders', auth, {'minutes_before': 6}
            ).status_code
            == 429
        )

    def test_the_history(self, client, auth, funded_user, dated):
        now = timezone.now()
        for n in range(3):
            ReminderDelivery.objects.create(
                user=funded_user,
                task=dated,
                task_number=dated.number,
                task_title=dated.title,
                channel='in_app',
                note='',
                sent_at=now + timezone.timedelta(minutes=n),
                fire_at=now,
            )
        gone = ReminderDelivery.objects.create(
            user=funded_user,
            task=None,
            task_number=1234,
            task_title='Deleted task',
            channel='email',
            note='unconfirmed',
            sent_at=now - timezone.timedelta(days=1),
            fire_at=now,
        )

        body = get(client, '/reminders/history', auth, limit=3).json()

        assert body['count'] == 4 and body['limit'] == 3
        assert set(body['results'][0]) == {
            'id',
            'task',
            'task_number',
            'task_title',
            'channel',
            'note',
            'fire_at',
            'sent_at',
        }
        assert [r['task'] for r in body['results']] == [dated.number] * 3
        last = get(client, '/reminders/history', auth, limit=3, offset=3).json()['results'][0]
        assert last['id'] == str(gone.pk) and last['task'] is None
        assert last['task_number'] == 1234 and last['note'] == 'unconfirmed'

    def test_the_history_is_the_callers_own(self, client, auth, other_user):
        task = create_task(other_user, title='Theirs', due_date=date(2026, 11, 1))
        ReminderDelivery.objects.create(
            user=other_user,
            task=task,
            task_number=task.number,
            task_title='Theirs',
            channel='in_app',
            sent_at=timezone.now(),
            fire_at=timezone.now(),
        )

        assert get(client, '/reminders/history', auth).json()['count'] == 0


def upload_body(data: bytes = PNG_BYTES, **fields) -> dict:
    return {
        'filename': 'photo.png',
        'size': len(data),
        'content_type': 'image/png',
        'checksum_sha256': b64_sha256(data),
        **fields,
    }


def start(client, auth, task, data: bytes = PNG_BYTES, **fields):
    return post(client, f'/tasks/{task.number}/attachments', auth, upload_body(data, **fields))


class TestAttachments:
    @pytest.fixture
    def task(self, funded_user):
        return create_task(funded_user, title='With files')

    def test_the_whole_flow(self, client, auth, task, funded_user, fake_storage):
        started = start(client, auth, task)

        assert started.status_code == 201
        ticket = started.json()
        assert set(ticket) == {'attachment_id', 'url', 'headers', 'expires_in'}
        assert ticket['headers'] == {'x-amz-checksum-sha256': b64_sha256(PNG_BYTES)}
        key = attachment_key(funded_user.pk, ticket['attachment_id'])
        assert fake_storage.put_urls[-1] == {
            'key': key,
            'content_length': len(PNG_BYTES),
            'checksum_sha256': b64_sha256(PNG_BYTES),
            'content_type': None,
        }
        assert get(client, f'/tasks/{task.number}', auth).json()['attachments'] == []  # not yet

        fake_storage.seed(key, PNG_BYTES)  # the client's PUT
        done = post(
            client, f'/tasks/{task.number}/attachments/{ticket["attachment_id"]}/complete', auth
        )

        assert done.status_code == 200
        assert done.json()['filename'] == 'photo.png' and done.json()['task'] == task.number
        assert done.json()['inline_type'] == 'image/png'
        assert done.json()['size_bytes'] == len(PNG_BYTES)
        assert get_account(funded_user).stored_bytes == len(PNG_BYTES)
        detail = get(client, f'/tasks/{task.number}', auth).json()
        assert detail['attachment_count'] == 1 and len(detail['attachments']) == 1

    def test_complete_is_idempotent(self, client, auth, task, funded_user, fake_storage):
        ticket = start(client, auth, task).json()
        fake_storage.seed(attachment_key(funded_user.pk, ticket['attachment_id']), PNG_BYTES)
        path = f'/tasks/{task.number}/attachments/{ticket["attachment_id"]}/complete'

        assert post(client, path, auth).status_code == 200
        assert post(client, path, auth).status_code == 200
        assert get_account(funded_user).stored_bytes == len(PNG_BYTES)

    def test_complete_before_the_put_is_a_400_and_keeps_the_row(self, client, auth, task):
        ticket = start(client, auth, task).json()

        response = post(
            client, f'/tasks/{task.number}/attachments/{ticket["attachment_id"]}/complete', auth
        )

        assert response.status_code == 400
        assert response.json()['detail'] == 'The file has not been uploaded.'
        assert Attachment.objects.filter(pk=ticket['attachment_id']).exists()

    def test_a_corrupted_upload_is_discarded(self, client, auth, task, funded_user, fake_storage):
        ticket = start(client, auth, task).json()
        key = attachment_key(funded_user.pk, ticket['attachment_id'])
        fake_storage.seed(key, b'\x89PNG\r\n\x1a\n' + b'\x01' * 64)  # same size, other bytes

        response = post(
            client, f'/tasks/{task.number}/attachments/{ticket["attachment_id"]}/complete', auth
        )

        assert response.status_code == 400
        assert response.json()['detail'] == 'The upload did not complete correctly.'
        assert not Attachment.objects.filter(pk=ticket['attachment_id']).exists()
        assert key not in fake_storage.objects
        assert get_account(funded_user).stored_bytes == 0

    def test_complete_of_an_upload_that_is_gone_is_a_404_and_removes_the_object(
        self, client, auth, task, funded_user, fake_storage
    ):
        ticket = start(client, auth, task).json()
        key = attachment_key(funded_user.pk, ticket['attachment_id'])
        fake_storage.seed(key, PNG_BYTES)
        Attachment.objects.filter(pk=ticket['attachment_id']).delete()

        response = post(
            client, f'/tasks/{task.number}/attachments/{ticket["attachment_id"]}/complete', auth
        )

        assert response.status_code == 404
        assert key not in fake_storage.objects
        missing = post(client, f'/tasks/{task.number}/attachments/{uuid.uuid4()}/complete', auth)
        assert missing.status_code == 404

    @pytest.mark.parametrize(
        ('fields', 'message'),
        [
            ({'size': 0}, 'The file is empty.'),
            ({'size': 60 * 1024 * 1024}, 'The file is too large (max 50 MB).'),
            ({'checksum_sha256': 'nope'}, 'The file checksum must be a base64-encoded SHA-256.'),
        ],
    )
    def test_start_refusals_are_a_400(self, client, auth, task, fields, message):
        response = start(client, auth, task, **fields)

        assert response.status_code == 400 and response.json()['detail'] == message
        assert not Attachment.objects.exists()

    def test_start_on_a_trashed_or_completed_task_is_a_400(self, client, auth, funded_user, task):
        done = complete_task(create_task(funded_user, title='Done'))
        gone = trash_task(task)

        assert start(client, auth, done).status_code == 400
        assert start(client, auth, gone).status_code == 400

    def test_the_cap_per_task(self, client, auth, task, config):
        from ..models import TodoSettings

        row = TodoSettings.load()
        row.max_attachments_per_task = 1
        row.save()
        assert start(client, auth, task).status_code == 201

        response = start(client, auth, task)

        assert response.status_code == 400
        assert response.json()['detail'] == 'A task can have up to 1 files.'

    def test_a_frozen_account_is_a_402(self, client, auth, task, funded_user):
        TodoAccount.objects.filter(pk=funded_user.pk).update(frozen_at=timezone.now())

        response = start(client, auth, task)

        assert response.status_code == 402
        assert response.json()['detail'].startswith('Storage is paused')

    def test_another_users_task_is_a_404(self, client, auth, other_user, fake_storage):
        theirs = create_task(other_user, title='Theirs')

        assert start(client, auth, theirs).status_code == 404
        assert not Attachment.objects.exists()

    def test_a_malformed_body_is_a_422(self, client, auth, task):
        assert (
            post(client, f'/tasks/{task.number}/attachments', auth, {'filename': 'x'}).status_code
            == 422
        )
        assert start(client, auth, task, extra=1).status_code == 422

    def test_the_rate_limits(self, client, auth, task, settings, fake_storage, funded_user):
        limit_rule(settings, 'TODO_UPLOAD_USER')
        limit_rule(settings, 'TODO_UPLOAD_COMPLETE_USER')

        ticket = start(client, auth, task)
        again = start(client, auth, task)
        done = post(
            client,
            f'/tasks/{task.number}/attachments/{ticket.json()["attachment_id"]}/complete',
            auth,
        )
        done_again = post(
            client,
            f'/tasks/{task.number}/attachments/{ticket.json()["attachment_id"]}/complete',
            auth,
        )

        assert ticket.status_code == 201 and again.status_code == 429
        assert done.status_code == 400  # nothing was PUT, but it was allowed to look
        assert done_again.status_code == 429

    def test_the_download_url(self, client, auth, task, fake_storage):
        attachment = make_attachment(task, fake_storage, filename='plan.png')

        body = get(client, f'/attachments/{attachment.pk}/url', auth).json()

        assert set(body) == {'url', 'disposition', 'filename', 'expires_in'}
        assert body['disposition'] == 'attachment' and body['filename'] == 'plan.png'
        assert body['url'].startswith('https://fake-s3.test/')
        assert fake_storage.get_urls[-1]['disposition'] == 'attachment'

    def test_inline_only_for_a_sniffed_image_or_pdf(self, client, auth, task, fake_storage):
        image = make_attachment(task, fake_storage, filename='photo.png')
        exe = make_attachment(
            task, fake_storage, data=b'MZ\x90\x00' + b'\x00' * 32, filename='photo.png'
        )

        shown = get(client, f'/attachments/{image.pk}/url', auth, disposition='inline').json()
        refused = get(client, f'/attachments/{exe.pk}/url', auth, disposition='inline').json()

        assert shown['disposition'] == 'inline'
        assert refused['disposition'] == 'attachment'  # the name says png, the bytes say no
        assert (
            get(client, f'/attachments/{image.pk}/url', auth, disposition='sideways').status_code
            == 400
        )

    def test_a_pending_upload_is_not_downloadable(self, client, auth, task, fake_storage):
        pending = make_attachment(task, fake_storage, uploaded=False)

        assert get(client, f'/attachments/{pending.pk}/url', auth).status_code == 404
        assert delete(client, f'/attachments/{pending.pk}', auth).status_code == 404

    def test_a_frozen_account_cannot_download(self, client, auth, task, funded_user, fake_storage):
        attachment = make_attachment(task, fake_storage)
        TodoAccount.objects.filter(pk=funded_user.pk).update(frozen_at=timezone.now())

        assert get(client, f'/attachments/{attachment.pk}/url', auth).status_code == 402

    def test_another_users_attachment_is_a_404(self, client, auth, other_user, fake_storage):
        theirs = make_attachment(create_task(other_user, title='Theirs'), fake_storage)

        assert get(client, f'/attachments/{theirs.pk}/url', auth).status_code == 404
        assert delete(client, f'/attachments/{theirs.pk}', auth).status_code == 404
        assert Attachment.objects.filter(pk=theirs.pk).exists()

    def test_delete_removes_the_object_and_the_billing(
        self, client, auth, task, funded_user, fake_storage
    ):
        attachment = make_attachment(task, fake_storage)
        key = attachment_key(funded_user.pk, attachment.pk)
        assert get_account(funded_user).stored_bytes == len(PNG_BYTES)

        assert delete(client, f'/attachments/{attachment.pk}', auth).status_code == 204

        assert key not in fake_storage.objects and not Attachment.objects.exists()
        assert get_account(funded_user).stored_bytes == 0

    def test_delete_is_allowed_for_a_trashed_task_and_while_frozen(
        self, client, auth, task, funded_user, fake_storage
    ):
        attachment = make_attachment(task, fake_storage)
        trash_task(task)
        TodoAccount.objects.filter(pk=funded_user.pk).update(frozen_at=timezone.now())

        assert delete(client, f'/attachments/{attachment.pk}', auth).status_code == 204

    def test_a_storage_failure_on_delete_is_a_503_and_keeps_the_row(
        self, client, auth, task, funded_user, fake_storage
    ):
        attachment = make_attachment(task, fake_storage)
        fake_storage.failing_deletes.add(attachment_key(funded_user.pk, attachment.pk))

        response = delete(client, f'/attachments/{attachment.pk}', auth)

        assert response.status_code == 503
        assert Attachment.objects.filter(pk=attachment.pk).exists()
        assert get_account(funded_user).stored_bytes == len(PNG_BYTES)

    def test_the_download_and_delete_rate_limits(self, client, auth, task, settings, fake_storage):
        attachment = make_attachment(task, fake_storage)
        limit_rule(settings, 'TODO_DOWNLOAD_USER')
        limit_rule(settings, 'TODO_EDIT_USER')

        assert get(client, f'/attachments/{attachment.pk}/url', auth).status_code == 200
        assert get(client, f'/attachments/{attachment.pk}/url', auth).status_code == 429
        assert delete(client, f'/attachments/{attachment.pk}', auth).status_code == 204
        assert delete(client, f'/attachments/{attachment.pk}', auth).status_code == 429

    def test_a_token_for_another_user_cannot_see_it(self, client, task, other_user, fake_storage):
        attachment = make_attachment(task, fake_storage)

        assert (
            get(client, f'/attachments/{attachment.pk}/url', bearer(other_user)).status_code == 404
        )
