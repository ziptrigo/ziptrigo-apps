"""`services.comments` and the comment views (issue #180): add, edit with its revision chain,
delete, restore, the history and deleted-text partials, ownership, rate limits and the row's
comment count."""

from html import unescape

import pytest
from django.core.exceptions import ValidationError
from django.test import Client
from django.urls import reverse

from ..models import Comment, CommentRevision, Task, TaskReference
from ..services import (
    COMMENT_MAX,
    MAX_REVISIONS,
    add_comment,
    comment_history,
    complete_task,
    create_task,
    delete_comment,
    edit_comment,
    restore_comment,
    trash_task,
    with_comment_counts,
)
from .factories import make_task, make_user

pytestmark = pytest.mark.django_db

HTMX = {'HTTP_HX_REQUEST': 'true'}


def text(response) -> str:
    return unescape(response.content.decode())


def errors(callable_, *args, **kwargs) -> dict[str, list[str]]:
    with pytest.raises(ValidationError) as caught:
        callable_(*args, **kwargs)
    return caught.value.message_dict


@pytest.fixture
def task(user):
    return make_task(user, number=1500, title='Write report')


class TestAdd:
    def test_adds_a_stripped_comment_with_its_search_text(self, task, user):
        comment = add_comment(task, user, '  **Bold** Café, #999!\r\nline two  ')

        comment.refresh_from_db()
        assert comment.body == '**Bold** Café, #999!\nline two'
        assert comment.body_text == 'bold cafe 999 line two'
        assert (comment.task, comment.user) == (task, user)
        assert (comment.edited_at, comment.deleted_at, comment.revision_count) == (None, None, 0)

    def test_an_empty_comment_is_refused(self, task, user):
        for blank in ('', '   \n ', None):
            assert errors(add_comment, task, user, blank) == {'body': ['Write a comment.']}
        assert not Comment.objects.exists()

    def test_the_cap(self, task, user):
        assert add_comment(task, user, 'x' * COMMENT_MAX)
        assert 'body' in errors(add_comment, task, user, 'x' * (COMMENT_MAX + 1))

    def test_the_cap_counts_a_browsers_line_breaks_once(self, task, user):
        assert add_comment(task, user, 'a\r\n' * (COMMENT_MAX // 2 - 1) + 'b')

    def test_only_the_owners_task(self, task, other_user):
        assert '__all__' in errors(add_comment, task, other_user, 'hello')

    def test_not_a_trashed_task(self, task, user):
        trash_task(task)

        assert 'Restore the task' in errors(add_comment, task, user, 'hello')['__all__'][0]

    def test_a_task_deleted_since_it_was_loaded(self, task, user):
        Task.objects.filter(pk=task.pk).delete()

        assert errors(add_comment, task, user, 'hello') == {
            '__all__': ['This task no longer exists.']
        }

    def test_a_completed_task_can_be_commented_on(self, funded_user):
        done = create_task(funded_user, title='Done')
        complete_task(done)

        assert add_comment(done, funded_user, 'Afterthought').pk

    def test_comments_are_listed_oldest_first(self, task, user):
        first = add_comment(task, user, 'one')
        second = add_comment(task, user, 'two')

        assert list(task.comments.all()) == [first, second]


class TestEdit:
    def test_three_edits_make_three_revisions_with_the_right_bodies(self, task, user, freeze_now):
        comment = add_comment(task, user, 'v0')
        moments = []
        for n in (1, 2, 3):
            moments.append(freeze_now(2026, 10, 28, 12, n))
            edit_comment(comment, f'v{n}')

        comment.refresh_from_db()
        assert comment.body == 'v3'
        assert comment.body_text == 'v3'
        assert comment.revision_count == 3
        assert comment.edited_at == moments[-1]
        revisions = comment_history(comment)
        assert [revision.body for revision in revisions] == ['v0', 'v1', 'v2']
        assert [revision.replaced_at for revision in revisions] == moments

    def test_an_unchanged_body_is_a_no_op(self, task, user):
        comment = add_comment(task, user, 'same')

        edit_comment(comment, '  same \r\n')

        comment.refresh_from_db()
        assert (comment.edited_at, comment.revision_count) == (None, 0)
        assert not CommentRevision.objects.exists()

    def test_the_instance_passed_in_is_updated(self, task, user):
        comment = add_comment(task, user, 'before')

        assert edit_comment(comment, 'after') is comment
        assert (comment.body, comment.revision_count) == ('after', 1)
        assert comment.edited_at is not None

    def test_a_stale_instance_edits_the_current_row(self, task, user):
        comment = add_comment(task, user, 'v0')
        stale = Comment.objects.get(pk=comment.pk)
        edit_comment(comment, 'v1')

        edit_comment(stale, 'v2')  # re-read under its lock: keeps v1 as a revision

        assert [revision.body for revision in comment_history(comment)] == ['v0', 'v1']

    def test_the_search_text_follows_the_edit(self, task, user):
        comment = add_comment(task, user, 'old words')

        edit_comment(comment, 'New *words*')

        comment.refresh_from_db()
        assert comment.body_text == 'new words'

    def test_validation_is_the_adds(self, task, user):
        comment = add_comment(task, user, 'text')

        assert errors(edit_comment, comment, ' ') == {'body': ['Write a comment.']}
        assert 'body' in errors(edit_comment, comment, 'x' * (COMMENT_MAX + 1))
        comment.refresh_from_db()
        assert comment.revision_count == 0

    def test_a_deleted_comment_cannot_be_edited(self, task, user):
        comment = add_comment(task, user, 'text')
        delete_comment(comment)

        assert errors(edit_comment, comment, 'new') == {'__all__': ['This comment was deleted.']}
        comment.refresh_from_db()
        assert comment.body == 'text'

    def test_the_cap_on_revisions(self, task, user):
        comment = add_comment(task, user, 'v0')
        Comment.objects.filter(pk=comment.pk).update(revision_count=MAX_REVISIONS - 1)

        edit_comment(comment, 'the hundredth')
        assert comment.revision_count == MAX_REVISIONS
        assert errors(edit_comment, comment, 'one too many') == {
            '__all__': ['This comment has been edited too many times.']
        }

        comment.refresh_from_db()
        assert comment.body == 'the hundredth'
        assert edit_comment(comment, 'the hundredth').revision_count == MAX_REVISIONS  # no-op


class TestDeleteAndRestore:
    def test_delete_keeps_the_text_and_clears_what_search_reads(self, task, user, freeze_now):
        comment = add_comment(task, user, 'findable words')
        edit_comment(comment, 'findable words, edited')
        moment = freeze_now(2026, 10, 28, 13, 0)

        delete_comment(comment)

        comment.refresh_from_db()
        assert comment.deleted_at == moment
        assert comment.body == 'findable words, edited'
        assert comment.body_text == ''
        assert comment.revision_count == 1 and comment.revisions.count() == 1

    def test_restore_rebuilds_the_search_text(self, task, user):
        comment = add_comment(task, user, '**Findable** words')
        delete_comment(comment)

        restore_comment(comment)

        comment.refresh_from_db()
        assert comment.deleted_at is None
        assert comment.body_text == 'findable words'

    def test_both_are_no_ops_in_the_state_they_ask_for(self, task, user, freeze_now):
        comment = add_comment(task, user, 'text')
        restore_comment(comment)  # not deleted
        comment.refresh_from_db()
        assert comment.deleted_at is None

        first = freeze_now(2026, 10, 28, 13, 0)
        delete_comment(comment)
        freeze_now(2026, 10, 28, 14, 0)
        delete_comment(comment)  # already deleted: the first stamp stays

        comment.refresh_from_db()
        assert comment.deleted_at == first

    def test_a_stale_instance_does_not_undo_a_restore(self, task, user):
        comment = add_comment(task, user, 'text')
        stale = Comment.objects.get(pk=comment.pk)
        delete_comment(comment)
        restore_comment(comment)

        restore_comment(stale)  # stale says "not deleted"; the row is not, either

        comment.refresh_from_db()
        assert comment.deleted_at is None and comment.body_text == 'text'

    def test_history_survives_a_delete_and_restore(self, task, user):
        comment = add_comment(task, user, 'v0')
        edit_comment(comment, 'v1')
        delete_comment(comment)
        restore_comment(comment)

        assert [revision.body for revision in comment_history(comment)] == ['v0']

    def test_deleting_the_task_deletes_comments_and_revisions(self, task, user):
        comment = add_comment(task, user, 'v0')
        edit_comment(comment, 'v1')

        task.delete()

        assert not Comment.objects.exists() and not CommentRevision.objects.exists()


class TestCounts:
    def test_only_comments_that_are_not_deleted_count(self, task, user):
        other = make_task(user, number=1501)
        make_task(user, number=1502)
        kept = add_comment(task, user, 'one')
        add_comment(task, user, 'two')
        gone = add_comment(task, user, 'three')
        add_comment(other, user, 'elsewhere')
        delete_comment(gone)

        counts = {t.number: t.comment_count for t in with_comment_counts(Task.objects.all())}

        assert counts == {1500: 2, 1501: 1, 1502: 0}
        assert kept.pk


# -- Views ---------------------------------------------------------------------------------------


@pytest.fixture
def my_task(funded_user):
    return create_task(funded_user, title='My task')


@pytest.fixture
def comment(my_task, funded_user):
    return add_comment(my_task, funded_user, 'First **comment**')


def url(name, task, *args):
    return reverse(f'todo:{name}', args=[task.number, *args])


class TestTaskPage:
    def test_a_task_with_no_comments_shows_the_box(self, web, my_task):
        body = text(web.get(url('task', my_task)))

        assert 'id="task-comments"' in body and 'Add a comment' in body
        assert 'name="body"' in body and 'Comment\n' in body

    def test_a_comment_shows_its_rendered_text_author_and_menu(self, web, my_task, comment):
        body = text(web.get(url('task', my_task)))

        assert f'id="comment-{comment.pk}"' in body
        assert '<strong>comment</strong>' in body
        assert 'Test User' in body
        assert 'Comment actions' in body and 'Delete comment' in body
        assert 'data-local-time-title' in body  # the full local time, as the tooltip
        assert '>edited</time>' not in body  # never edited: no marker

    def test_an_edited_comment_has_the_marker(self, web, my_task, comment):
        edit_comment(comment, 'Changed')

        body = text(web.get(url('task', my_task)))

        assert url('comment-history', my_task, comment.pk) in body
        assert '>edited</time>' in body

    def test_a_deleted_comment_is_a_muted_line_with_show(self, web, my_task, comment):
        delete_comment(comment)

        body = text(web.get(url('task', my_task)))

        assert 'Comment deleted' in body
        assert url('comment-deleted', my_task, comment.pk) in body
        assert 'First' not in body  # the text only comes with "Show"

    def test_a_trashed_tasks_comments_are_read_only(self, web, my_task, comment):
        trash_task(my_task)

        body = text(web.get(url('task', my_task)))

        assert 'First' in body
        assert 'Comment actions' not in body and 'Add a comment' not in body

    def test_hash_numbers_link_to_the_users_tasks_only(self, web, funded_user, my_task):
        mentioned = create_task(funded_user, title='Mentioned')
        add_comment(my_task, funded_user, f'See #{mentioned.number} and #999 and #8888')

        body = text(web.get(url('task', my_task)))

        assert (
            f'<a href="/todo/{mentioned.number}/" class="task-ref">#{mentioned.number}</a>' in body
        )
        assert '#999' in body and 'href="/todo/999/"' not in body
        assert 'href="/todo/8888/"' not in body

    def test_the_rows_show_the_comment_count(self, web, my_task, comment, funded_user):
        add_comment(my_task, funded_user, 'second')

        row = text(web.get(reverse('todo:all'))).split(f'id="task-{my_task.number}"', 1)[1]

        assert 'fa-comment' in row.split('</li>')[0] and '>2<span class="sr-only"> comments' in row

    def test_a_row_without_comments_has_no_icon(self, web, my_task):
        row = text(web.get(reverse('todo:all'))).split(f'id="task-{my_task.number}"', 1)[1]

        assert 'fa-comment' not in row.split('</li>')[0]


class TestAddView:
    def test_htmx_answers_the_section_with_the_new_comment(self, web, my_task):
        response = web.post(url('comment-add', my_task), {'body': 'Hello *there*'}, **HTMX)

        assert response.status_code == 200
        body = text(response)
        assert 'id="task-comments"' in body and '<em>there</em>' in body
        assert Comment.objects.get().body == 'Hello *there*'

    def test_without_htmx_it_redirects_to_the_page(self, web, my_task):
        response = web.post(url('comment-add', my_task), {'body': 'Hello'})

        assert response.status_code == 302
        assert response['Location'] == url('task', my_task) + f'#comment-{Comment.objects.get().pk}'

    def test_an_empty_comment_is_a_422_in_the_add_slot(self, web, my_task):
        response = web.post(url('comment-add', my_task), {'body': '  '}, **HTMX)

        assert response.status_code == 422
        assert 'Comment: Write a comment.' in text(response)
        assert response['HX-Retarget'] == '#comment-msg'
        assert not Comment.objects.exists()

    def test_an_over_long_comment_is_a_422(self, web, my_task):
        response = web.post(url('comment-add', my_task), {'body': 'x' * 10_001}, **HTMX)

        assert response.status_code == 422 and 'too long' in text(response)

    def test_a_comment_can_be_added_to_a_completed_task(self, web, my_task):
        complete_task(my_task)

        assert web.post(url('comment-add', my_task), {'body': 'late'}, **HTMX).status_code == 200

    def test_get_is_not_allowed(self, web, my_task):
        assert web.get(url('comment-add', my_task)).status_code == 405

    def test_a_comment_mentioning_a_task_records_the_reference(self, web, funded_user, my_task):
        other = create_task(funded_user, title='Other')

        web.post(url('comment-add', my_task), {'body': f'about #{other.number}'}, **HTMX)

        row = TaskReference.objects.get()
        assert (row.from_task, row.to_task, row.comment) == (my_task, other, Comment.objects.get())


class TestEditView:
    def test_edit_answers_the_section_and_keeps_the_history(self, web, my_task, comment):
        response = web.post(url('comment-edit', my_task, comment.pk), {'body': 'Second'}, **HTMX)

        assert response.status_code == 200 and 'Second' in text(response)
        comment.refresh_from_db()
        assert (comment.body, comment.revision_count) == ('Second', 1)

    def test_a_refusal_goes_to_the_comments_own_slot(self, web, my_task, comment):
        response = web.post(url('comment-edit', my_task, comment.pk), {'body': ''}, **HTMX)

        assert response.status_code == 422
        assert response['HX-Retarget'] == f'#comment-msg-{comment.pk}'

    def test_a_deleted_comment_cannot_be_edited(self, web, my_task, comment):
        delete_comment(comment)

        response = web.post(url('comment-edit', my_task, comment.pk), {'body': 'x'}, **HTMX)

        assert response.status_code == 422 and 'This comment was deleted.' in text(response)

    def test_without_htmx_it_redirects(self, web, my_task, comment):
        response = web.post(url('comment-edit', my_task, comment.pk), {'body': 'x'})

        assert response.status_code == 302


class TestDeleteAndRestoreViews:
    def test_delete_without_htmx_redirects_back_to_the_comments(self, web, my_task, comment):
        response = web.post(url('comment-delete', my_task, comment.pk))

        assert response.status_code == 302 and response['Location'].endswith(
            f'#comment-{comment.pk}'
        )
        comment.refresh_from_db()
        assert comment.is_deleted

    def test_delete_with_htmx_answers_the_section(self, web, my_task, comment):
        response = web.post(url('comment-delete', my_task, comment.pk), **HTMX)

        assert response.status_code == 200 and 'Comment deleted' in text(response)

    def test_restore(self, web, my_task, comment):
        delete_comment(comment)

        response = web.post(url('comment-restore', my_task, comment.pk), **HTMX)

        assert response.status_code == 200 and 'First' in text(response)
        comment.refresh_from_db()
        assert not comment.is_deleted

    def test_both_are_idempotent(self, web, my_task, comment):
        assert web.post(url('comment-restore', my_task, comment.pk), **HTMX).status_code == 200
        web.post(url('comment-delete', my_task, comment.pk), **HTMX)
        assert web.post(url('comment-delete', my_task, comment.pk), **HTMX).status_code == 200


class TestPartials:
    def test_history_lists_the_earlier_texts_newest_first(self, web, my_task, comment):
        edit_comment(comment, 'Second **version**')
        edit_comment(comment, 'Third')

        response = web.get(url('comment-history', my_task, comment.pk))

        body = text(response)
        assert response.status_code == 200
        assert body.index('Second <strong>version</strong>') < body.index('First <strong>comment')
        assert 'Third' not in body  # the current text is not an earlier version
        assert body.count('Replaced') == 2

    def test_history_of_a_comment_never_edited(self, web, my_task, comment):
        assert 'No earlier versions' in text(web.get(url('comment-history', my_task, comment.pk)))

    def test_history_of_a_deleted_comment_and_a_trashed_task(self, web, my_task, comment):
        edit_comment(comment, 'Second')
        delete_comment(comment)
        trash_task(my_task)

        response = web.get(url('comment-history', my_task, comment.pk))

        assert response.status_code == 200 and 'First' in text(response)

    def test_history_renders_task_references(self, web, funded_user, my_task):
        other = create_task(funded_user, title='Other')
        comment = add_comment(my_task, funded_user, f'old #{other.number}')
        edit_comment(comment, 'new')

        assert f'href="/todo/{other.number}/"' in text(
            web.get(url('comment-history', my_task, comment.pk))
        )

    def test_the_deleted_partial_shows_the_original_with_restore(self, web, my_task, comment):
        edit_comment(comment, 'Edited **text**')
        delete_comment(comment)

        response = web.get(url('comment-deleted', my_task, comment.pk))

        body = text(response)
        assert response.status_code == 200
        assert 'Edited <strong>text</strong>' in body
        assert url('comment-restore', my_task, comment.pk) in body
        assert url('comment-history', my_task, comment.pk) in body

    def test_the_deleted_partial_of_a_live_comment_is_a_404(self, web, my_task, comment):
        assert web.get(url('comment-deleted', my_task, comment.pk)).status_code == 404

    def test_the_partials_are_get_only(self, web, my_task, comment):
        assert web.post(url('comment-history', my_task, comment.pk)).status_code == 405
        assert web.post(url('comment-deleted', my_task, comment.pk)).status_code == 405


class TestOwnership:
    @pytest.fixture
    def stranger(self, db):
        client = Client()
        client.force_login(make_user())
        return client

    @pytest.mark.parametrize(
        ('name', 'method'),
        [
            ('comment-edit', 'post'),
            ('comment-delete', 'post'),
            ('comment-restore', 'post'),
            ('comment-history', 'get'),
            ('comment-deleted', 'get'),
        ],
    )
    def test_another_users_comment_is_a_404(self, stranger, my_task, comment, name, method):
        delete_comment(comment)  # so the `deleted` partial has something to show

        response = getattr(stranger, method)(url(name, my_task, comment.pk), {'body': 'x'})

        assert response.status_code == 404

    def test_another_users_task_cannot_be_commented_on(self, stranger, my_task):
        assert stranger.post(url('comment-add', my_task), {'body': 'x'}).status_code == 404
        assert not Comment.objects.exists()

    def test_a_comment_of_another_task_is_a_404(self, web, funded_user, my_task, comment):
        other = create_task(funded_user, title='Other')

        for name in ('comment-edit', 'comment-delete', 'comment-restore'):
            assert web.post(url(name, other, comment.pk), {'body': 'x'}).status_code == 404
        assert web.get(url('comment-history', other, comment.pk)).status_code == 404
        comment.refresh_from_db()
        assert comment.body.startswith('First') and not comment.is_deleted

    def test_an_unknown_comment_is_a_404(self, web, my_task):
        missing = '00000000-0000-0000-0000-000000000000'

        assert web.post(url('comment-edit', my_task, missing), {'body': 'x'}).status_code == 404

    def test_a_trashed_task_accepts_no_changes(self, web, my_task, comment):
        trash_task(my_task)

        assert web.post(url('comment-add', my_task), {'body': 'x'}).status_code == 404
        assert web.post(url('comment-edit', my_task, comment.pk), {'body': 'x'}).status_code == 404
        assert web.post(url('comment-delete', my_task, comment.pk)).status_code == 404

    def test_anonymous_visitors_go_to_the_login_page(self, client, my_task, comment):
        for name in ('comment-add', 'comment-edit', 'comment-delete', 'comment-restore'):
            args = (comment.pk,) if name != 'comment-add' else ()
            response = client.post(url(name, my_task, *args), {'body': 'x'})
            assert response.status_code == 302 and '/account/login' in response['Location']
        assert client.get(url('comment-history', my_task, comment.pk)).status_code == 302


class TestRateLimits:
    @pytest.fixture(autouse=True)
    def _limits(self, settings, ratelimit_clock):
        settings.RATELIMIT_ENABLE = True
        settings.RATELIMIT_RULES = {
            **settings.RATELIMIT_RULES,
            'TODO_COMMENT_USER': (2, 60),
            'TODO_EDIT_USER': (2, 60),
        }

    def test_adding_and_editing_share_the_comment_budget(self, web, my_task):
        first = web.post(url('comment-add', my_task), {'body': 'one'}, **HTMX)
        assert first.status_code == 200
        comment = Comment.objects.get()
        assert (
            web.post(url('comment-edit', my_task, comment.pk), {'body': 'two'}, **HTMX).status_code
            == 200
        )

        response = web.post(url('comment-add', my_task), {'body': 'three'}, **HTMX)

        assert response.status_code == 429 and response['Retry-After'] == '20'
        assert response['HX-Retarget'] == '#comment-msg'
        assert Comment.objects.count() == 1

    def test_a_refused_form_spends_no_budget(self, web, my_task):
        for _ in range(5):
            assert web.post(url('comment-add', my_task), {'body': ''}, **HTMX).status_code == 422

        assert web.post(url('comment-add', my_task), {'body': 'ok'}, **HTMX).status_code == 200

    def test_deleting_and_restoring_use_the_edit_budget(self, web, my_task, comment):
        assert web.post(url('comment-delete', my_task, comment.pk), **HTMX).status_code == 200
        assert web.post(url('comment-restore', my_task, comment.pk), **HTMX).status_code == 200

        assert web.post(url('comment-delete', my_task, comment.pk), **HTMX).status_code == 429
        # The comment budget is a separate one.
        assert (
            web.post(url('comment-add', my_task), {'body': 'still ok'}, **HTMX).status_code == 200
        )


class TestAdmin:
    @pytest.fixture
    def admin_client(self, db):
        from apps.accounts.tests.factories import UserFactory

        client = Client()
        client.force_login(UserFactory(is_staff=True, is_superuser=True))
        return client

    def test_the_changelist_and_the_detail_page_with_its_revisions(self, admin_client, task, user):
        comment = add_comment(task, user, 'Original findable text')
        edit_comment(comment, 'Edited text')
        delete_comment(comment)

        listing = admin_client.get(reverse('admin:todo_comment_changelist'), {'q': 'Edited'})
        assert listing.status_code == 200 and '#1500' in text(listing)
        assert 'Edited text' in text(admin_client.get(reverse('admin:todo_comment_changelist')))

        page = admin_client.get(reverse('admin:todo_comment_change', args=[comment.pk]))
        body = text(page)
        assert page.status_code == 200
        assert 'Original findable text' in body and 'Edited text' in body  # revision + body
        assert '_save' not in body  # read-only: no save button

    def test_it_cannot_be_added_changed_or_deleted(self, admin_client, task, user):
        comment = add_comment(task, user, 'text')

        assert admin_client.get(reverse('admin:todo_comment_add')).status_code == 403
        assert (
            admin_client.post(
                reverse('admin:todo_comment_change', args=[comment.pk]), {'body': 'x'}
            ).status_code
            == 403
        )
        assert (
            admin_client.get(reverse('admin:todo_comment_delete', args=[comment.pk])).status_code
            == 403
        )

    def test_deleting_a_user_with_comments_is_not_blocked(self, admin_client, task, user):
        add_comment(task, user, 'text')

        response = admin_client.get(reverse('admin:accounts_user_delete', args=[user.pk]))

        assert response.status_code == 200 and 'cannot be deleted' not in text(response)
