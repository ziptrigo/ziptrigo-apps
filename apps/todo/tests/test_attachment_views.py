"""The attachment views (issue #184): the JSON upload endpoints, the list partial, download, remove,
the task page's block, the frozen banner and the settings page's storage line. Driven end to end
against the fake storage: start -> (the browser's PUT, simulated by seeding the object) -> complete.
"""

import base64
import hashlib
import json

import pytest
from django.urls import reverse
from django.utils import timezone

from ..models import Attachment, TodoAccount, TodoSettings
from ..services import get_account, trash_task
from ..services.storage import attachment_key
from .factories import PNG_BYTES, make_attachment, make_task

pytestmark = [
    pytest.mark.django_db,
    pytest.mark.unit,
    # Two requests per fixed rate-limit window: a real clock crossing a boundary breaks them.
    pytest.mark.usefixtures('ratelimit_clock'),
]

HTMX = {'HTTP_HX_REQUEST': 'true'}


def post_json(client, url, body):
    return client.post(url, data=json.dumps(body), content_type='application/json')


def start_body(data: bytes = PNG_BYTES, **overrides):
    body = {
        'name': 'photo.png',
        'size': len(data),
        'content_type': 'image/png',
        'checksum_sha256': base64.b64encode(hashlib.sha256(data).digest()).decode(),
    }
    body.update(overrides)
    return body


@pytest.fixture
def task(funded_user):
    return make_task(funded_user)


def start(web, task, **overrides):
    return post_json(
        web, reverse('todo:attachment-start', args=[task.number]), start_body(**overrides)
    )


def complete(web, task, attachment_id):
    return web.post(reverse('todo:attachment-complete', args=[task.number, attachment_id]))


class TestStart:
    def test_answers_a_presigned_put(self, web, task, fake_storage):
        response = start(web, task)

        assert response.status_code == 201
        body = response.json()
        attachment = Attachment.objects.get()
        assert body['attachment_id'] == str(attachment.pk)
        assert body['url'].startswith('https://fake-s3.test/')
        assert body['headers'] == {'x-amz-checksum-sha256': start_body()['checksum_sha256']}
        assert body['expires_in'] == 3600
        assert fake_storage.put_urls[0]['content_length'] == len(PNG_BYTES)
        assert attachment.uploaded is False

    def test_requires_login_and_post(self, client, web, task):
        url = reverse('todo:attachment-start', args=[task.number])

        assert web.get(url).status_code == 405
        assert client.__class__().post(url).status_code == 302

    @pytest.mark.parametrize(
        'size', [1.5, '12abc', True, None, [1], float('inf'), float('nan'), -1, '-5']
    )
    def test_a_size_that_is_not_a_whole_number_of_bytes_is_a_422(self, web, task, size):
        response = start(web, task, size=size)

        assert response.status_code == 422
        assert 'whole number of bytes' in response.json()['error']

    def test_a_huge_exponent_is_a_422_not_a_500(self, web, task):
        response = web.post(
            reverse('todo:attachment-start', args=[task.number]),
            data='{"name": "a", "size": 1e400, "checksum_sha256": "x"}',
            content_type='application/json',
        )

        assert response.status_code == 422

    def test_a_whole_number_sent_as_a_float_is_accepted(self, web, task):
        assert start(web, task, size=float(len(PNG_BYTES))).status_code == 201

    def test_a_missing_name_is_a_422(self, web, task):
        response = start(web, task, name='   ')

        assert response.status_code == 422
        assert response.json() == {'error': 'The file needs a name.'}

    def test_garbage_json_is_a_422(self, web, task):
        response = web.post(
            reverse('todo:attachment-start', args=[task.number]),
            data='not json',
            content_type='application/json',
        )

        assert response.status_code == 422

    def test_too_large_is_a_422_with_the_message(self, web, task):
        limit = TodoSettings.load().max_attachment_bytes

        response = start(web, task, size=limit + 1)

        assert response.status_code == 422
        assert response.json() == {'error': 'The file is too large (max 50 MB).'}

    def test_the_cap_per_task_is_a_422(self, web, task, fake_storage):
        TodoSettings.objects.update_or_create(pk=1, defaults={'max_attachments_per_task': 1})
        make_attachment(task, fake_storage)

        response = start(web, task)

        assert response.status_code == 422
        assert 'up to 1 files' in response.json()['error']

    def test_a_bad_checksum_is_a_422(self, web, task):
        assert start(web, task, checksum_sha256='nope').status_code == 422

    def test_a_frozen_account_is_a_422_with_a_top_up_link(self, web, task, funded_user):
        get_account(funded_user)
        TodoAccount.objects.filter(pk=funded_user.pk).update(frozen_at=timezone.now())

        response = start(web, task)

        assert response.status_code == 422
        body = response.json()
        assert body['frozen'] is True
        assert body['top_up_url'] == reverse('billing:credits-history')
        assert 'Storage is paused' in body['error']
        assert not Attachment.objects.exists()

    def test_another_users_task_and_a_trashed_one_are_a_404(self, web, other_user):
        theirs = make_task(other_user)
        assert start(web, theirs).status_code == 404

    def test_a_trashed_task_is_a_404(self, web, task):
        trash_task(task)

        assert start(web, task).status_code == 404

    def test_a_completed_task_is_a_422(self, web, task):
        task.completed_at = timezone.now()
        task.save()

        response = start(web, task)

        assert response.status_code == 422
        assert 'completed task' in response.json()['error']

    def test_is_rate_limited_per_user(self, web, task, settings):
        settings.RATELIMIT_ENABLE = True
        settings.RATELIMIT_RULES = {**settings.RATELIMIT_RULES, 'TODO_UPLOAD_USER': (2, 60)}

        assert start(web, task).status_code == 201
        assert start(web, task).status_code == 201
        response = start(web, task)

        assert response.status_code == 429
        assert response['Retry-After'] == '20'
        assert Attachment.objects.count() == 2


class TestComplete:
    def started(self, web, task, fake_storage, data: bytes = PNG_BYTES) -> str:
        body = start(web, task).json()
        fake_storage.seed(attachment_key(task.user_id, body['attachment_id']), data)
        return body['attachment_id']

    def test_verifies_and_counts_the_file(self, web, task, fake_storage, funded_user):
        attachment_id = self.started(web, task, fake_storage)

        response = complete(web, task, attachment_id)

        assert response.status_code == 200
        assert response.json() == {
            'ok': True,
            'attachment_id': attachment_id,
            'filename': 'photo.png',
            'size_bytes': len(PNG_BYTES),
        }
        attachment = Attachment.objects.get(pk=attachment_id)
        assert attachment.uploaded and attachment.inline_type == 'image/png'
        assert get_account(funded_user).stored_bytes == len(PNG_BYTES)

    def test_a_wrong_size_is_a_422_and_removes_the_row(self, web, task, fake_storage):
        attachment_id = self.started(web, task, fake_storage, PNG_BYTES + b'x')

        response = complete(web, task, attachment_id)

        assert response.status_code == 422
        assert response.json() == {'error': 'The upload did not complete correctly.'}
        assert not Attachment.objects.exists()

    def test_an_object_that_never_arrived_is_a_422(self, web, task):
        attachment_id = start(web, task).json()['attachment_id']

        response = complete(web, task, attachment_id)

        assert response.status_code == 422
        assert 'has not been uploaded' in response.json()['error']

    def test_completing_twice_is_fine(self, web, task, fake_storage, funded_user):
        attachment_id = self.started(web, task, fake_storage)

        assert complete(web, task, attachment_id).status_code == 200
        assert complete(web, task, attachment_id).status_code == 200
        assert get_account(funded_user).stored_bytes == len(PNG_BYTES)

    def test_another_users_attachment_is_a_404(self, web, other_user, fake_storage):
        theirs = make_attachment(make_task(other_user), fake_storage, uploaded=False)

        response = web.post(
            reverse('todo:attachment-complete', args=[theirs.task.number, theirs.pk])
        )

        assert response.status_code == 404

    def test_the_attachment_must_belong_to_that_task(self, web, task, fake_storage, funded_user):
        other_task = make_task(funded_user)
        attachment_id = self.started(web, task, fake_storage)

        assert complete(web, other_task, attachment_id).status_code == 404

    def test_a_task_trashed_meanwhile_still_completes(self, web, task, fake_storage):
        attachment_id = self.started(web, task, fake_storage)
        trash_task(task)

        assert complete(web, task, attachment_id).status_code == 200

    def test_has_its_own_looser_rate_limit_charged_first(self, web, task, settings):
        settings.RATELIMIT_ENABLE = True
        settings.RATELIMIT_RULES = {
            **settings.RATELIMIT_RULES,
            'TODO_UPLOAD_COMPLETE_USER': (1, 60),
        }
        attachment_id = start(web, task).json()['attachment_id']

        first = complete(web, task, attachment_id)  # the object never arrived: a 422
        second = complete(web, task, attachment_id)

        assert first.status_code == 422
        assert second.status_code == 429 and second['Retry-After']

    def test_the_two_rules_are_separate_budgets(self, web, task, settings):
        settings.RATELIMIT_ENABLE = True
        settings.RATELIMIT_RULES = {**settings.RATELIMIT_RULES, 'TODO_UPLOAD_USER': (1, 60)}
        attachment_id = start(web, task).json()['attachment_id']

        assert start(web, task).status_code == 429
        assert complete(web, task, attachment_id).status_code == 422  # not 429


class TestList:
    def test_htmx_gets_the_list_partial(self, web, task, fake_storage):
        make_attachment(task, fake_storage, filename='notes.txt', data=b'hello')
        make_attachment(task, fake_storage, uploaded=False, filename='pending.bin')

        response = web.get(reverse('todo:attachment-list', args=[task.number]), **HTMX)

        content = response.content.decode()
        assert response.status_code == 200
        assert 'notes.txt' in content and 'pending.bin' not in content
        assert '<html' not in content

    def test_opened_directly_it_goes_to_the_task_page(self, web, task):
        response = web.get(reverse('todo:attachment-list', args=[task.number]))

        assert response.status_code == 302
        assert response['Location'] == reverse('todo:task', args=[task.number])

    def test_another_users_task_is_a_404(self, web, other_user):
        theirs = make_task(other_user)

        assert (
            web.get(reverse('todo:attachment-list', args=[theirs.number]), **HTMX).status_code
            == 404
        )


class TestDownload:
    def test_redirects_to_a_presigned_attachment_url(self, web, task, fake_storage):
        attachment = make_attachment(task, fake_storage)

        response = web.get(reverse('todo:attachment-download', args=[attachment.pk]))

        assert response.status_code == 302
        assert response['Location'].startswith('https://fake-s3.test/')
        assert 'disposition=attachment' in response['Location']
        assert 'no-store' in response['Cache-Control']
        assert fake_storage.get_urls[0]['filename'] == 'photo.png'

    def test_open_is_inline_for_a_sniffed_image(self, web, task, fake_storage):
        attachment = make_attachment(task, fake_storage)

        response = web.get(reverse('todo:attachment-download', args=[attachment.pk]) + '?inline=1')

        assert 'disposition=inline' in response['Location']
        assert fake_storage.get_urls[0]['content_type'] == 'image/png'

    def test_open_is_a_download_when_the_bytes_are_not_an_inline_type(
        self, web, task, fake_storage
    ):
        attachment = make_attachment(
            task, fake_storage, data=b'MZ' + b'\x00' * 30, declared_type='image/png'
        )

        response = web.get(reverse('todo:attachment-download', args=[attachment.pk]) + '?inline=1')

        assert 'disposition=attachment' in response['Location']

    def test_frozen_goes_back_to_the_task_with_a_message(
        self, web, task, fake_storage, funded_user
    ):
        attachment = make_attachment(task, fake_storage)
        TodoAccount.objects.filter(pk=funded_user.pk).update(frozen_at=timezone.now())

        response = web.get(reverse('todo:attachment-download', args=[attachment.pk]), follow=True)

        assert response.redirect_chain == [(reverse('todo:task', args=[task.number]), 302)]
        assert 'Storage is paused' in response.content.decode()
        assert fake_storage.get_urls == []

    def test_another_users_attachment_is_a_404(self, web, other_user, fake_storage):
        theirs = make_attachment(make_task(other_user), fake_storage)

        assert web.get(reverse('todo:attachment-download', args=[theirs.pk])).status_code == 404

    def test_a_pending_upload_is_a_404(self, web, task, fake_storage):
        pending = make_attachment(task, fake_storage, uploaded=False)

        assert web.get(reverse('todo:attachment-download', args=[pending.pk])).status_code == 404

    def test_a_trashed_tasks_file_can_still_be_downloaded(self, web, task, fake_storage):
        attachment = make_attachment(task, fake_storage)
        trash_task(task)

        response = web.get(reverse('todo:attachment-download', args=[attachment.pk]))

        assert response.status_code == 302

    def test_is_rate_limited(self, web, task, fake_storage, settings):
        settings.RATELIMIT_ENABLE = True
        settings.RATELIMIT_RULES = {**settings.RATELIMIT_RULES, 'TODO_DOWNLOAD_USER': (1, 60)}
        attachment = make_attachment(task, fake_storage)
        url = reverse('todo:attachment-download', args=[attachment.pk])

        assert web.get(url).status_code == 302
        assert web.get(url).status_code == 429

    def test_requires_login(self, client, task, fake_storage):
        attachment = make_attachment(task, fake_storage)

        response = client.get(reverse('todo:attachment-download', args=[attachment.pk]))

        assert response.status_code == 302 and '/login' in response['Location']


class TestDelete:
    def test_removes_the_file_and_stops_billing_it(self, web, task, fake_storage, funded_user):
        attachment = make_attachment(task, fake_storage)

        response = web.post(reverse('todo:attachment-delete', args=[attachment.pk]), follow=True)

        assert response.redirect_chain[-1][0] == reverse('todo:task', args=[task.number])
        assert 'Removed photo.png.' in response.content.decode()
        assert not Attachment.objects.exists()
        assert fake_storage.objects == {}
        assert get_account(funded_user).stored_bytes == 0

    def test_get_is_not_allowed(self, web, task, fake_storage):
        attachment = make_attachment(task, fake_storage)

        assert web.get(reverse('todo:attachment-delete', args=[attachment.pk])).status_code == 405

    def test_htmx_redirects_the_page(self, web, task, fake_storage):
        attachment = make_attachment(task, fake_storage)

        response = web.post(reverse('todo:attachment-delete', args=[attachment.pk]), **HTMX)

        assert response['HX-Redirect'] == reverse('todo:task', args=[task.number])

    def test_a_storage_error_is_a_message_not_a_500(self, web, task, fake_storage):
        attachment = make_attachment(task, fake_storage)
        fake_storage.failing_deletes.add(attachment_key(task.user_id, attachment.pk))

        response = web.post(reverse('todo:attachment-delete', args=[attachment.pk]), follow=True)

        assert response.status_code == 200
        assert 'could not be removed' in response.content.decode()
        assert Attachment.objects.filter(pk=attachment.pk).exists()

    def test_another_users_attachment_is_a_404(self, web, other_user, fake_storage):
        theirs = make_attachment(make_task(other_user), fake_storage)

        assert web.post(reverse('todo:attachment-delete', args=[theirs.pk])).status_code == 404
        assert Attachment.objects.filter(pk=theirs.pk).exists()

    def test_removing_works_on_a_trashed_task_and_while_frozen(
        self, web, task, fake_storage, funded_user
    ):
        attachment = make_attachment(task, fake_storage)
        trash_task(task)
        TodoAccount.objects.filter(pk=funded_user.pk).update(frozen_at=timezone.now())

        web.post(reverse('todo:attachment-delete', args=[attachment.pk]))

        assert not Attachment.objects.exists()

    def test_uses_the_edit_rate_limit(self, web, task, fake_storage, settings):
        settings.RATELIMIT_ENABLE = True
        settings.RATELIMIT_RULES = {**settings.RATELIMIT_RULES, 'TODO_EDIT_USER': (1, 60)}
        first = make_attachment(task, fake_storage)
        second = make_attachment(task, fake_storage)

        assert web.post(reverse('todo:attachment-delete', args=[first.pk])).status_code == 302
        assert web.post(reverse('todo:attachment-delete', args=[second.pk])).status_code == 429


class TestTaskPage:
    def test_an_open_task_has_the_drop_zone_and_the_script(self, web, task):
        content = web.get(reverse('todo:task', args=[task.number])).content.decode()

        assert 'Attachments' in content and 'Drop files here' in content and 'Add files' in content
        assert f'data-start-url="{reverse("todo:attachment-start", args=[task.number])}"' in content
        assert 'No files attached.' in content
        # `initDropZone` lives in core's shared script, which must load before the page's own.
        assert content.index('core/js/drop_zone.js') < content.index('todo/js/attachments.js')
        assert 'up to 50' in content.lower() or 'Up to 50' in content

    def test_lists_the_files_with_download_open_and_remove(self, web, task, fake_storage):
        png = make_attachment(task, fake_storage, filename='photo.png')
        exe = make_attachment(
            task, fake_storage, data=b'MZ' + b'\x00' * 30, filename='tool.exe', inline_type=''
        )

        content = web.get(reverse('todo:task', args=[task.number])).content.decode()

        assert 'photo.png' in content and 'tool.exe' in content
        assert reverse('todo:attachment-download', args=[png.pk]) + '?inline=1' in content
        assert reverse('todo:attachment-download', args=[exe.pk]) + '?inline=1' not in content
        assert reverse('todo:attachment-delete', args=[exe.pk]) in content
        assert 'rel="noopener noreferrer"' in content

    def test_a_filename_is_escaped(self, web, task, fake_storage):
        make_attachment(task, fake_storage, filename='<script>alert(1)</script>.png')

        content = web.get(reverse('todo:task', args=[task.number])).content.decode()

        assert '<script>alert(1)</script>.png' not in content
        assert '&lt;script&gt;alert(1)&lt;/script&gt;.png' in content

    def test_a_trashed_task_lists_files_but_takes_no_uploads(self, web, task, fake_storage):
        make_attachment(task, fake_storage, filename='kept.png')
        trash_task(task)

        content = web.get(reverse('todo:task', args=[task.number])).content.decode()

        assert 'kept.png' in content
        assert 'Drop files here' not in content
        assert 'todo/js/attachments.js' not in content

    def test_a_completed_task_takes_no_uploads(self, web, task):
        task.completed_at = timezone.now()
        task.save()

        content = web.get(reverse('todo:task', args=[task.number])).content.decode()

        assert 'Drop files here' not in content

    def test_a_frozen_account_disables_adding_and_says_so(self, web, task, funded_user):
        get_account(funded_user)
        TodoAccount.objects.filter(pk=funded_user.pk).update(frozen_at=timezone.now())

        content = web.get(reverse('todo:task', args=[task.number])).content.decode()

        assert 'Uploads are paused until you add credits' in content
        assert 'data-frozen="true"' in content


class TestFrozenBanner:
    PAGES = ['todo:today', 'todo:all', 'todo:settings', 'todo:labels', 'todo:trash']

    @pytest.mark.parametrize('name', PAGES)
    def test_every_page_shows_the_banner_with_a_top_up_link(self, web, funded_user, name):
        get_account(funded_user)
        TodoAccount.objects.filter(pk=funded_user.pk).update(frozen_at=timezone.now())

        content = web.get(reverse(name)).content.decode()

        assert 'Storage is paused.' in content
        assert reverse('billing:credits-history') in content

    @pytest.mark.parametrize('name', PAGES)
    def test_no_banner_while_not_frozen(self, web, funded_user, name):
        assert 'Storage is paused.' not in web.get(reverse(name)).content.decode()


class TestSettingsPage:
    def test_shows_the_stored_size_and_the_daily_cost(self, web, funded_user):
        get_account(funded_user)
        TodoAccount.objects.filter(pk=funded_user.pk).update(stored_bytes=25 * 1024**3)

        content = web.get(reverse('todo:settings')).content.decode()

        # 25 GB at the default 0.1 credit/GB/day is 2.5, rounded up to 3.
        assert 'Files' in content
        assert '25.0' in content and 'GB' in content
        assert 'about <strong>3</strong> credits a day' in content

    def test_nothing_stored(self, web):
        assert 'No files stored.' in web.get(reverse('todo:settings')).content.decode()

    def test_the_saved_form_still_shows_it(self, web, funded_user):
        get_account(funded_user)
        TodoAccount.objects.filter(pk=funded_user.pk).update(stored_bytes=1024**3)

        response = web.post(reverse('todo:settings'), {'default_due_time': '10:30'}, **HTMX)

        assert response.status_code == 200
        assert 'a day.' in response.content.decode()
