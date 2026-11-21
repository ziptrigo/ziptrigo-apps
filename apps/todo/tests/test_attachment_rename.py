"""Renaming attachments: a task's files (the row's Rename, `todo:attachment-rename`), a comment's
files (the name boxes of the comment forms), the API (`PATCH /attachments/{id}`) and the activity
log. The name is edited without its extension, which is kept."""

import pytest
from django.core.exceptions import ValidationError
from django.urls import reverse

from ..models import Attachment, EventKind, TaskEvent
from ..services import add_comment, edit_comment, trash_task
from ..services.attachments import rename_attachment
from ..services.event_text import describe
from .api_support import (  # noqa: F401 -- the fixtures are used by name
    auth,
    limit_rule,
    other_auth,
    patch,
)
from .factories import make_attachment, make_comment, make_task

pytestmark = [
    pytest.mark.django_db,
    pytest.mark.unit,
    pytest.mark.usefixtures('ratelimit_clock'),
]

HTMX = {'HTTP_HX_REQUEST': 'true'}


@pytest.fixture
def task(funded_user):
    return make_task(funded_user)


@pytest.fixture
def pasted(task, fake_storage):
    return make_attachment(task, fake_storage, filename='2026-11-21-10-45-30.webp')


def renames(task):
    return list(TaskEvent.objects.filter(task=task, kind=EventKind.ATTACHMENT_RENAMED))


class TestService:
    def test_keeps_the_extension(self, pasted, task):
        rename_attachment(pasted, 'Whiteboard')

        pasted.refresh_from_db()
        assert pasted.filename == 'Whiteboard.webp'
        [event] = renames(task)
        assert event.data == {
            'from': '2026-11-21-10-45-30.webp',
            'to': 'Whiteboard.webp',
            'comment': False,
        }
        assert describe(event) == 'File renamed from 2026-11-21-10-45-30.webp to Whiteboard.webp'

    def test_a_typed_extension_is_not_doubled(self, pasted):
        rename_attachment(pasted, 'Whiteboard.WEBP')

        pasted.refresh_from_db()
        assert pasted.filename == 'Whiteboard.webp'

    def test_slashes_and_control_characters_are_cleaned(self, pasted):
        rename_attachment(pasted, ' a/b\\c​\x07 ')

        pasted.refresh_from_db()
        assert pasted.filename == 'a_b_c.webp'

    def test_an_empty_name_is_refused(self, pasted):
        with pytest.raises(ValidationError) as caught:
            rename_attachment(pasted, ' ​ ')

        assert caught.value.message_dict == {'filename': ['Give the file a name.']}

    def test_the_same_name_logs_nothing(self, pasted, task):
        rename_attachment(pasted, '2026-11-21-10-45-30')

        assert renames(task) == []

    def test_refused_on_a_task_in_the_trash(self, pasted, task):
        trash_task(task)
        pasted.refresh_from_db()

        with pytest.raises(ValidationError):
            rename_attachment(pasted, 'x')

    def test_refused_for_an_upload_in_flight(self, task, fake_storage):
        pending = make_attachment(task, fake_storage, uploaded=False)

        with pytest.raises(ValidationError):
            rename_attachment(pending, 'x')

    def test_a_name_without_extension_is_edited_whole(self, task, fake_storage):
        readme = make_attachment(task, fake_storage, filename='README')

        rename_attachment(readme, 'NOTES')

        readme.refresh_from_db()
        assert readme.filename == 'NOTES'

    def test_a_long_name_is_cut_before_the_extension(self, pasted):
        rename_attachment(pasted, 'x' * 400)

        pasted.refresh_from_db()
        assert len(pasted.filename) == 255
        assert pasted.filename.endswith('x.webp')


class TestView:
    def url(self, attachment):
        return reverse('todo:attachment-rename', args=[attachment.pk])

    def test_renames_and_answers_the_list(self, web, pasted):
        response = web.post(self.url(pasted), {'filename': 'Whiteboard'}, **HTMX)

        assert response.status_code == 200
        html = response.content.decode()
        assert 'Whiteboard.webp' in html
        assert 'id="attachment-msg" hx-swap-oob="true"' in html
        assert response['HX-Trigger'] == 'taskChanged'

    def test_the_task_page_offers_rename_with_the_stem(self, web, task, pasted):
        html = web.get(reverse('todo:task', args=[task.number])).content.decode()

        assert 'Rename 2026-11-21-10-45-30.webp' in html
        assert 'value="2026-11-21-10-45-30"' in html
        assert '>.webp</span>' in html
        assert 'id="attachment-msg"' in html

    def test_an_empty_name_is_a_422_that_spends_nothing(self, web, pasted, settings):
        limit_rule(settings, 'TODO_EDIT_USER', limit=1)

        refused = web.post(self.url(pasted), {'filename': '  '}, **HTMX)
        assert refused.status_code == 422
        assert refused['HX-Retarget'] == '#attachment-msg'
        assert 'File name: Give the file a name.' in refused.content.decode()

        assert web.post(self.url(pasted), {'filename': 'ok'}, **HTMX).status_code == 200
        assert web.post(self.url(pasted), {'filename': 'again'}, **HTMX).status_code == 429

    def test_without_htmx_it_redirects_with_a_message(self, web, task, pasted):
        response = web.post(self.url(pasted), {'filename': 'Whiteboard'}, follow=True)

        assert 'Renamed to Whiteboard.webp.' in response.content.decode()

    def test_another_users_file_is_a_404(self, client, other_user, pasted):
        client.force_login(other_user)

        assert client.post(self.url(pasted), {'filename': 'x'}).status_code == 404

    def test_a_comment_file_is_not_renamed_here(self, web, task, fake_storage):
        comment = make_comment(task)
        file = make_attachment(task, fake_storage, for_comment=True, comment=comment)

        assert web.post(self.url(file), {'filename': 'x'}).status_code == 404

    def test_a_trashed_task_shows_no_rename(self, web, task, pasted):
        trash_task(task)

        html = web.get(reverse('todo:task', args=[task.number])).content.decode()
        assert 'Rename 2026-11-21-10-45-30.webp' not in html


class TestCommentFiles:
    def test_add_renames_a_draft_as_it_is_attached(self, task, funded_user, fake_storage):
        draft = make_attachment(task, fake_storage, for_comment=True, filename='clip.png')

        add_comment(task, funded_user, 'Look', [draft.pk], file_names={str(draft.pk): 'Sketch'})

        draft.refresh_from_db()
        assert draft.filename == 'Sketch.png'
        assert renames(task)[0].data['comment'] is True

    def test_edit_renames_but_not_a_file_being_removed(self, task, fake_storage):
        comment = make_comment(task, 'Hi')
        keep = make_attachment(task, fake_storage, for_comment=True, comment=comment)
        drop = make_attachment(task, fake_storage, for_comment=True, comment=comment)

        edit_comment(
            comment,
            'Hi',
            remove_attachment_ids=[str(drop.pk)],
            file_names={str(keep.pk): 'Kept', str(drop.pk): 'Gone'},
        )

        keep.refresh_from_db()
        assert keep.filename == 'Kept.png'
        assert not Attachment.objects.filter(pk=drop.pk).exists()
        comment.refresh_from_db()
        assert comment.revision_count == 0  # the files alone are not an edit of the text

    def test_an_empty_name_refuses_the_whole_edit(self, task, fake_storage):
        comment = make_comment(task, 'Hi')
        file = make_attachment(task, fake_storage, for_comment=True, comment=comment)

        with pytest.raises(ValidationError):
            edit_comment(comment, 'Changed', file_names={str(file.pk): ''})

        comment.refresh_from_db()
        assert comment.body == 'Hi'

    def test_the_edit_form_posts_the_name_boxes(self, web, task, fake_storage):
        comment = make_comment(task, 'Hi')
        file = make_attachment(task, fake_storage, for_comment=True, comment=comment)

        response = web.post(
            reverse('todo:comment-edit', args=[task.number, comment.pk]),
            {'body': 'Hi', f'file_name_{file.pk}': 'Plan'},
            **HTMX,
        )

        assert response.status_code == 200
        file.refresh_from_db()
        assert file.filename == 'Plan.png'

    def test_the_edit_form_shows_a_name_box(self, web, task, fake_storage):
        comment = make_comment(task, 'Hi')
        file = make_attachment(task, fake_storage, for_comment=True, comment=comment)

        html = web.get(reverse('todo:task', args=[task.number])).content.decode()

        assert f'name="file_name_{file.pk}" value="photo"' in html


class TestApi:
    def test_patch_renames(self, client, auth, pasted, task):
        response = patch(client, f'/attachments/{pasted.pk}', auth, {'filename': 'Whiteboard'})

        assert response.status_code == 200
        assert response.json()['filename'] == 'Whiteboard.webp'
        assert response.json()['task'] == task.number

    def test_an_empty_name_is_a_400_that_spends_nothing(self, client, auth, pasted, settings):
        limit_rule(settings, 'TODO_EDIT_USER', limit=1)

        refused = patch(client, f'/attachments/{pasted.pk}', auth, {'filename': ' '})
        assert refused.status_code == 400
        assert refused.json()['detail'] == 'filename: Give the file a name.'
        assert (
            patch(client, f'/attachments/{pasted.pk}', auth, {'filename': 'a'}).status_code == 200
        )

    def test_another_users_file_is_a_404(self, client, other_auth, pasted):
        response = patch(client, f'/attachments/{pasted.pk}', other_auth, {'filename': 'x'})

        assert response.status_code == 404

    def test_an_unknown_key_is_a_422(self, client, auth, pasted):
        response = patch(client, f'/attachments/{pasted.pk}', auth, {'name': 'x'})

        assert response.status_code == 422

    def test_api_changes_are_logged_from_the_api(self, client, auth, pasted, task):
        patch(client, f'/attachments/{pasted.pk}', auth, {'filename': 'Whiteboard'})

        assert renames(task)[0].channel == 'api'
