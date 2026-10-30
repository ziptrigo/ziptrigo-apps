"""Files on comments: uploaded as drafts for a comment, tied to it when it is added or edited,
shown with it, removed by an edit, swept when never saved, and kept out of the task's own list."""

import base64
import hashlib
import json
import uuid
from datetime import timedelta

import pytest
from django.core.exceptions import ValidationError
from django.urls import reverse
from django.utils import timezone

from ..models import Attachment, Comment
from ..services import (
    add_comment,
    complete_task,
    complete_upload,
    delete_comment,
    delete_forever,
    edit_comment,
    get_account,
    restore_comment,
    start_upload,
    trash_task,
)
from ..services import attachments as attachment_service
from ..services.storage import attachment_key
from .factories import PNG_BYTES, make_attachment, make_task

pytestmark = [
    pytest.mark.django_db,
    pytest.mark.unit,
    pytest.mark.usefixtures('ratelimit_clock'),
]

HTMX = {'HTTP_HX_REQUEST': 'true'}


def checksum(data: bytes) -> str:
    return base64.b64encode(hashlib.sha256(data).digest()).decode()


def draft(task, fake_storage, data: bytes = PNG_BYTES, name: str = 'photo.png') -> Attachment:
    """A comment file uploaded and completed but not saved with a comment yet."""
    ticket = start_upload(
        task,
        filename=name,
        size=len(data),
        declared_type='image/png',
        checksum_sha256=checksum(data),
        for_comment=True,
    )
    fake_storage.seed(attachment_key(task.user_id, ticket.attachment.pk), data)
    return complete_upload(ticket.attachment)


@pytest.fixture
def task(funded_user):
    return make_task(funded_user)


class TestService:
    def test_a_draft_is_linked_by_adding_the_comment(self, funded_user, task, fake_storage):
        file = draft(task, fake_storage)

        comment = add_comment(task, funded_user, 'See the photo', [file.pk])

        file.refresh_from_db()
        assert file.comment_id == comment.pk
        assert file.for_comment

    def test_a_comment_can_be_added_to_a_completed_task_with_a_file(
        self, funded_user, task, fake_storage
    ):
        complete_task(task)
        task.refresh_from_db()
        file = draft(task, fake_storage)

        comment = add_comment(task, funded_user, 'Done, see photo', [file.pk])

        assert list(Attachment.objects.filter(comment=comment)) == [file]

    def test_a_task_attachment_still_refuses_a_completed_task(self, funded_user, task):
        complete_task(task)
        task.refresh_from_db()

        with pytest.raises(ValidationError, match='completed task'):
            start_upload(
                task,
                filename='a.png',
                size=len(PNG_BYTES),
                declared_type='image/png',
                checksum_sha256=checksum(PNG_BYTES),
            )

    def test_a_file_still_uploading_refuses_the_comment_and_adds_nothing(
        self, funded_user, task, fake_storage
    ):
        pending = make_attachment(task, fake_storage, uploaded=False, for_comment=True)

        with pytest.raises(ValidationError) as caught:
            add_comment(task, funded_user, 'x', [pending.pk])

        assert 'attachment_ids' in caught.value.message_dict
        assert not Comment.objects.filter(task=task).exists()

    @pytest.mark.parametrize('kind', ['other_user', 'other_task', 'task_attachment', 'garbage'])
    def test_files_that_are_not_the_users_draft_are_refused(
        self, funded_user, other_user, task, fake_storage, kind
    ):
        if kind == 'other_user':
            other_task = make_task(other_user)
            value = draft(other_task, fake_storage).pk
        elif kind == 'other_task':
            value = draft(make_task(funded_user), fake_storage).pk
        elif kind == 'task_attachment':
            value = make_attachment(task, fake_storage).pk
        else:
            value = 'not-a-uuid'

        with pytest.raises(ValidationError) as caught:
            add_comment(task, funded_user, 'x', [value])

        assert 'attachment_ids' in caught.value.message_dict
        assert not Comment.objects.filter(task=task).exists()

    def test_a_file_cannot_be_taken_by_a_second_comment(self, funded_user, task, fake_storage):
        file = draft(task, fake_storage)
        add_comment(task, funded_user, 'first', [file.pk])

        with pytest.raises(ValidationError):
            add_comment(task, funded_user, 'second', [file.pk])

    def test_editing_adds_and_removes_files(self, funded_user, task, fake_storage):
        keep = draft(task, fake_storage, name='keep.png')
        drop = draft(task, fake_storage, name='drop.png')
        comment = add_comment(task, funded_user, 'Files', [keep.pk, drop.pk])
        stored_before = get_account(funded_user).stored_bytes
        new = draft(task, fake_storage, name='new.png')

        edit_comment(comment, 'Files', [new.pk], [drop.pk])

        names = sorted(
            Attachment.objects.filter(comment=comment).values_list('filename', flat=True)
        )
        assert names == ['keep.png', 'new.png']
        assert not fake_storage.object_exists(attachment_key(funded_user.pk, drop.pk))
        # one removed, one added: the same number of bytes
        assert get_account(funded_user).stored_bytes == stored_before

    def test_changing_only_the_files_is_not_an_edit_of_the_text(
        self, funded_user, task, fake_storage
    ):
        comment = add_comment(task, funded_user, 'Same text')
        file = draft(task, fake_storage)

        edit_comment(comment, 'Same text', [file.pk])

        comment.refresh_from_db()
        assert comment.revision_count == 0
        assert comment.edited_at is None
        assert Attachment.objects.filter(comment=comment).count() == 1

    def test_removing_ignores_files_of_other_comments(self, funded_user, task, fake_storage):
        one = add_comment(task, funded_user, 'one', [draft(task, fake_storage).pk])
        two_file = draft(task, fake_storage)
        two = add_comment(task, funded_user, 'two', [two_file.pk])

        edit_comment(one, 'one', remove_attachment_ids=[two_file.pk])

        assert Attachment.objects.filter(comment=two).count() == 1

    def test_deleting_a_comment_keeps_its_files_until_it_is_restored(
        self, funded_user, task, fake_storage
    ):
        comment = add_comment(task, funded_user, 'x', [draft(task, fake_storage).pk])

        delete_comment(comment)
        assert Attachment.objects.filter(comment=comment).count() == 1
        restore_comment(comment)

        assert Attachment.objects.filter(comment=comment).count() == 1

    def test_comment_files_go_with_the_task(self, funded_user, task, fake_storage):
        file = draft(task, fake_storage)
        add_comment(task, funded_user, 'x', [file.pk])
        trash_task(task)

        delete_forever(task)

        assert not Attachment.objects.filter(pk=file.pk).exists()
        assert not fake_storage.object_exists(attachment_key(funded_user.pk, file.pk))
        assert get_account(funded_user).stored_bytes == 0

    def test_the_cap_on_files_is_shared_with_the_task(self, funded_user, task, fake_storage):
        from ..models import TodoSettings

        settings = TodoSettings.load()
        settings.max_attachments_per_task = 1
        settings.save()
        draft(task, fake_storage)

        with pytest.raises(ValidationError, match='up to 1 files'):
            start_upload(
                task,
                filename='b.png',
                size=len(PNG_BYTES),
                declared_type='image/png',
                checksum_sha256=checksum(PNG_BYTES),
                for_comment=True,
            )


class TestDraftsAndCleanup:
    def test_discard_deletes_an_unsaved_draft_and_its_bytes(self, funded_user, task, fake_storage):
        file = draft(task, fake_storage)
        assert get_account(funded_user).stored_bytes == len(PNG_BYTES)

        attachment_service.discard_draft(file)

        assert not Attachment.objects.filter(pk=file.pk).exists()
        assert get_account(funded_user).stored_bytes == 0

    def test_discard_refuses_an_attached_file(self, funded_user, task, fake_storage):
        file = draft(task, fake_storage)
        add_comment(task, funded_user, 'x', [file.pk])
        file.refresh_from_db()

        with pytest.raises(ValidationError):
            attachment_service.discard_draft(file)

        plain = make_attachment(task, fake_storage)
        with pytest.raises(ValidationError):
            attachment_service.discard_draft(plain)

    def test_old_unsaved_drafts_are_swept_and_saved_ones_are_not(
        self, funded_user, task, fake_storage
    ):
        old = draft(task, fake_storage, name='old.png')
        fresh = draft(task, fake_storage, name='fresh.png')
        saved = draft(task, fake_storage, name='saved.png')
        add_comment(task, funded_user, 'x', [saved.pk])
        Attachment.objects.filter(pk__in=[old.pk, saved.pk]).update(
            created_at=timezone.now() - timedelta(days=2)
        )

        removed = attachment_service.abandon_comment_files()

        assert removed == 1
        remaining = set(Attachment.objects.values_list('pk', flat=True))
        assert remaining == {fresh.pk, saved.pk}


class TestTaskListsLeaveCommentFilesOut:
    def test_the_attachments_list_and_count_do_not_include_them(
        self, web, funded_user, task, fake_storage
    ):
        make_attachment(task, fake_storage, filename='task-file.png')
        add_comment(task, funded_user, 'x', [draft(task, fake_storage, name='comment-file.png').pk])

        response = web.get(reverse('todo:attachment-list', args=[task.number]), **HTMX)

        content = response.content.decode()
        assert 'task-file.png' in content
        assert 'comment-file.png' not in content
        counted = attachment_service.with_attachment_counts(type(task).objects.filter(pk=task.pk))
        assert counted.get().attachment_count == 1


class TestViews:
    def start(self, web, task, **overrides):
        body = {
            'name': 'photo.png',
            'size': len(PNG_BYTES),
            'content_type': 'image/png',
            'checksum_sha256': checksum(PNG_BYTES),
            'for_comment': True,
        }
        body.update(overrides)
        return web.post(
            reverse('todo:attachment-start', args=[task.number]),
            data=json.dumps(body),
            content_type='application/json',
        )

    def test_the_whole_flow_from_the_browser(self, web, funded_user, task, fake_storage):
        started = self.start(web, task)
        assert started.status_code == 201
        attachment_id = started.json()['attachment_id']
        fake_storage.seed(attachment_key(funded_user.pk, uuid.UUID(attachment_id)), PNG_BYTES)
        done = web.post(reverse('todo:attachment-complete', args=[task.number, attachment_id]))
        assert done.status_code == 200

        posted = web.post(
            reverse('todo:comment-add', args=[task.number]),
            {'body': 'With a file', 'attachment_ids': [attachment_id]},
            **HTMX,
        )

        assert posted.status_code == 200
        html = posted.content.decode()
        assert 'With a file' in html
        assert 'photo.png' in html
        assert reverse('todo:attachment-download', args=[attachment_id]) in html
        assert Attachment.objects.get(pk=attachment_id).comment is not None

    def test_a_refused_file_is_a_422_that_adds_no_comment(self, web, task):
        response = web.post(
            reverse('todo:comment-add', args=[task.number]),
            {'body': 'x', 'attachment_ids': [str(uuid.uuid4())]},
            **HTMX,
        )

        assert response.status_code == 422
        assert (
            'Attachment ids' in response.content.decode()
            or 'not ready' in response.content.decode()
        )
        assert not Comment.objects.filter(task=task).exists()

    def test_the_edit_form_lists_files_with_a_remove_box(
        self, web, funded_user, task, fake_storage
    ):
        file = draft(task, fake_storage, name='listed.png')
        comment = add_comment(task, funded_user, 'x', [file.pk])

        html = web.get(reverse('todo:task', args=[task.number])).content.decode()

        assert 'listed.png' in html
        assert f'name="remove_attachment_ids" value="{file.pk}"' in html
        assert 'data-comment-files' in html
        assert f'comment-{comment.pk}' in html

    def test_an_edit_removes_a_ticked_file(self, web, funded_user, task, fake_storage):
        file = draft(task, fake_storage)
        comment = add_comment(task, funded_user, 'x', [file.pk])

        response = web.post(
            reverse('todo:comment-edit', args=[task.number, comment.pk]),
            {'body': 'x', 'remove_attachment_ids': [str(file.pk)]},
            **HTMX,
        )

        assert response.status_code == 200
        assert not Attachment.objects.filter(pk=file.pk).exists()

    def test_the_comment_form_is_wired_on_a_completed_task(self, web, funded_user, task):
        complete_task(task)

        html = web.get(reverse('todo:task', args=[task.number])).content.decode()

        assert 'todo/js/comment_files.js' in html
        assert 'todo/js/uploader.js' in html
        assert 'data-comment-files' in html

    def test_a_comment_start_works_on_a_completed_task(self, web, funded_user, task):
        complete_task(task)
        task.refresh_from_db()

        assert self.start(web, task).status_code == 201
        assert self.start(web, task, for_comment=False).status_code == 422

    def test_discard_deletes_an_unsaved_file_and_is_idempotent(
        self, web, funded_user, task, fake_storage
    ):
        file = draft(task, fake_storage)
        url = reverse('todo:attachment-discard', args=[task.number, file.pk])

        assert web.post(url).status_code == 200
        assert not Attachment.objects.filter(pk=file.pk).exists()
        assert web.post(url).status_code == 200

    def test_discard_refuses_a_saved_file(self, web, funded_user, task, fake_storage):
        file = draft(task, fake_storage)
        add_comment(task, funded_user, 'x', [file.pk])

        response = web.post(reverse('todo:attachment-discard', args=[task.number, file.pk]))

        assert response.status_code == 422
        assert Attachment.objects.filter(pk=file.pk).exists()

    def test_discard_of_another_users_file_is_a_404(self, web, other_user, fake_storage):
        other_task = make_task(other_user)
        file = draft(other_task, fake_storage)

        response = web.post(reverse('todo:attachment-discard', args=[other_task.number, file.pk]))

        assert response.status_code == 404
        assert Attachment.objects.filter(pk=file.pk).exists()
