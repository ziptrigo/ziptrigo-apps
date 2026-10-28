"""The To-do JWT API's label and comment endpoints (`/api/todo/labels`, `.../comments`)."""

import uuid

import pytest

from ..models import DEFAULT_LABEL_COLOR, Comment, Label
from ..services import add_comment, create_task, delete_comment, edit_comment, trash_task
from .api_support import (  # noqa: F401 -- the fixtures are used by name
    auth,
    delete,
    get,
    limit_rule,
    other_auth,
    patch,
    post,
)
from .factories import make_label

pytestmark = [pytest.mark.django_db, pytest.mark.unit]

LABEL_KEYS = {'id', 'name', 'color', 'description', 'position', 'task_count'}
COMMENT_KEYS = {
    'id',
    'task',
    'body',
    'body_html',
    'created_at',
    'edited_at',
    'deleted_at',
    'revision_count',
}


def names(response) -> list[str]:
    return [label['name'] for label in response.json()]


class TestLabels:
    def test_list_in_order_with_task_counts(self, client, auth, funded_user):
        home = make_label(funded_user, 'Home', position=1)
        work = make_label(funded_user, 'Work', position=0)
        create_task(funded_user, title='a', labels=[home])
        trash_task(create_task(funded_user, title='b', labels=[home]))  # the Trash doesn't count

        response = get(client, '/labels', auth)

        assert response.status_code == 200
        assert names(response) == ['Work', 'Home']
        assert set(response.json()[0]) == LABEL_KEYS
        assert [label['task_count'] for label in response.json()] == [0, 1]
        assert work.pk and home.pk

    def test_only_the_callers_labels(self, client, auth, other_user):
        make_label(other_user, 'Secret')

        assert get(client, '/labels', auth).json() == []

    def test_create_appends(self, client, auth, funded_user):
        make_label(funded_user, 'First', position=3)

        response = post(
            client,
            '/labels',
            auth,
            {'name': '  Big   idea ', 'color': '#ABCDEF', 'description': 'for  later'},
        )

        assert response.status_code == 201
        body = response.json()
        assert set(body) == LABEL_KEYS
        assert body['name'] == 'Big idea' and body['color'] == '#abcdef'
        assert body['description'] == 'for later' and body['position'] == 4
        assert body['task_count'] == 0

    def test_the_default_colour(self, client, auth):
        assert post(client, '/labels', auth, {'name': 'x'}).json()['color'] == DEFAULT_LABEL_COLOR

    @pytest.mark.parametrize(
        ('body', 'message'),
        [
            ({'name': ' '}, 'name: Name is required.'),
            ({'name': 'x' * 51}, 'name: Name is too long'),
            ({'name': 'x', 'color': 'red'}, 'color: Use a colour like #8fa89e.'),
        ],
    )
    def test_create_refusals_are_a_400(self, client, auth, body, message):
        response = post(client, '/labels', auth, body)

        assert response.status_code == 400 and message in response.json()['detail']

    def test_a_duplicate_name_ignoring_case_is_a_400(self, client, auth, funded_user):
        make_label(funded_user, 'Home')

        response = post(client, '/labels', auth, {'name': 'HOME'})

        assert response.status_code == 400
        assert response.json()['detail'] == 'name: You already have a label with this name.'

    def test_update_only_what_is_present(self, client, auth, funded_user):
        label = make_label(funded_user, 'Home', color='#112233', description='old')

        renamed = patch(client, f'/labels/{label.pk}', auth, {'name': 'House'}).json()
        recoloured = patch(client, f'/labels/{label.pk}', auth, {'color': '#445566'}).json()

        assert renamed['name'] == 'House' and renamed['color'] == '#112233'
        assert recoloured['name'] == 'House' and recoloured['description'] == 'old'

    def test_a_null_clears(self, client, auth, funded_user):
        label = make_label(funded_user, 'Home', color='#112233', description='old')

        body = patch(client, f'/labels/{label.pk}', auth, {'description': None, 'color': None})

        assert body.json()['description'] == '' and body.json()['color'] == DEFAULT_LABEL_COLOR
        assert patch(client, f'/labels/{label.pk}', auth, {'name': None}).status_code == 400

    def test_update_refusals(self, client, auth, funded_user):
        make_label(funded_user, 'Taken')
        label = make_label(funded_user, 'Home')

        assert patch(client, f'/labels/{label.pk}', auth, {'name': 'taken'}).status_code == 400
        assert patch(client, f'/labels/{label.pk}', auth, {'nope': 1}).status_code == 422

    def test_delete_keeps_the_tasks(self, client, auth, funded_user):
        label = make_label(funded_user, 'Home')
        other = make_label(funded_user, 'Work')
        task = create_task(funded_user, title='x', labels=[label, other])

        assert delete(client, f'/labels/{label.pk}', auth).status_code == 204

        assert list(task.labels.all()) == [other]
        assert delete(client, f'/labels/{label.pk}', auth).status_code == 404

    def test_reorder(self, client, auth, funded_user):
        a = make_label(funded_user, 'A', position=0)
        b = make_label(funded_user, 'B', position=1)
        c = make_label(funded_user, 'C', position=2)

        response = post(client, '/labels/reorder', auth, {'ids': [str(c.pk), str(a.pk), str(b.pk)]})

        assert response.status_code == 200 and names(response) == ['C', 'A', 'B']

    def test_reorder_ignores_other_users_ids(self, client, auth, funded_user, other_user):
        mine = make_label(funded_user, 'Mine')
        theirs = make_label(other_user, 'Theirs', position=5)

        post(client, '/labels/reorder', auth, {'ids': [str(theirs.pk), str(mine.pk)]})

        theirs.refresh_from_db()
        assert theirs.position == 5

    def test_another_users_label_is_a_404(self, client, auth, other_user):
        theirs = make_label(other_user, 'Theirs')

        assert patch(client, f'/labels/{theirs.pk}', auth, {'name': 'x'}).status_code == 404
        assert delete(client, f'/labels/{theirs.pk}', auth).status_code == 404
        assert delete(client, f'/labels/{uuid.uuid4()}', auth).status_code == 404
        assert Label.objects.get(pk=theirs.pk).name == 'Theirs'

    def test_a_malformed_id_is_a_422(self, client, auth):
        assert delete(client, '/labels/not-a-uuid', auth).status_code == 422

    def test_the_edit_rate_limit(self, client, auth, settings):
        limit_rule(settings, 'TODO_EDIT_USER')

        assert post(client, '/labels', auth, {'name': 'a'}).status_code == 201
        assert post(client, '/labels', auth, {'name': 'b'}).status_code == 429


class TestComments:
    @pytest.fixture
    def task(self, funded_user):
        return create_task(funded_user, title='Main')

    def test_add_and_list(self, client, auth, task):
        added = post(client, f'/tasks/{task.number}/comments', auth, {'body': '  **hi** #1000 '})

        assert added.status_code == 201
        body = added.json()
        assert set(body) == COMMENT_KEYS
        assert body['task'] == task.number and body['body'] == '**hi** #1000'
        assert '<strong>hi</strong>' in body['body_html']
        assert body['edited_at'] is None and body['deleted_at'] is None
        assert body['revision_count'] == 0
        listed = get(client, f'/tasks/{task.number}/comments', auth).json()
        assert [c['id'] for c in listed] == [body['id']]

    def test_comments_are_oldest_first(self, client, auth, funded_user, task):
        for text in ('one', 'two', 'three'):
            add_comment(task, funded_user, text)

        listed = get(client, f'/tasks/{task.number}/comments', auth).json()

        assert [c['body'] for c in listed] == ['one', 'two', 'three']

    @pytest.mark.parametrize(
        ('body', 'message'),
        [('  ', 'body: Write a comment.'), ('x' * 10_001, 'body: Comment is too long')],
    )
    def test_add_refusals_are_a_400(self, client, auth, task, body, message):
        response = post(client, f'/tasks/{task.number}/comments', auth, {'body': body})

        assert response.status_code == 400 and message in response.json()['detail']

    def test_a_task_in_the_trash_takes_none(self, client, auth, funded_user, task):
        trash_task(task)

        response = post(client, f'/tasks/{task.number}/comments', auth, {'body': 'x'})

        assert response.status_code == 400
        assert response.json()['detail'] == 'Restore the task before commenting on it.'

    def test_a_completed_task_takes_one(self, client, auth, funded_user, task):
        from ..services import complete_task

        complete_task(task)

        assert (
            post(client, f'/tasks/{task.number}/comments', auth, {'body': 'x'}).status_code == 201
        )

    def test_edit_keeps_the_history(self, client, auth, funded_user, task):
        comment = add_comment(task, funded_user, 'first')

        edited = patch(
            client, f'/tasks/{task.number}/comments/{comment.pk}', auth, {'body': 'second'}
        )
        history = get(client, f'/tasks/{task.number}/comments/{comment.pk}/history', auth)

        assert edited.status_code == 200
        assert edited.json()['body'] == 'second' and edited.json()['revision_count'] == 1
        assert edited.json()['edited_at'] is not None
        assert history.json()[0]['body'] == 'first' and set(history.json()[0]) == {
            'body',
            'body_html',
            'replaced_at',
        }
        assert len(history.json()) == 1

    def test_the_same_text_is_not_an_edit(self, client, auth, funded_user, task):
        comment = add_comment(task, funded_user, 'same')

        body = patch(client, f'/tasks/{task.number}/comments/{comment.pk}', auth, {'body': 'same'})

        assert body.json()['revision_count'] == 0

    def test_a_deleted_comment_has_no_body_unless_asked(self, client, auth, funded_user, task):
        comment = add_comment(task, funded_user, 'secret text')

        assert (
            delete(client, f'/tasks/{task.number}/comments/{comment.pk}', auth).status_code == 204
        )

        hidden = get(client, f'/tasks/{task.number}/comments', auth).json()[0]
        shown = get(
            client, f'/tasks/{task.number}/comments', auth, include_deleted_bodies=1
        ).json()[0]
        assert hidden['deleted_at'] is not None
        assert hidden['body'] is None and hidden['body_html'] is None
        assert shown['body'] == 'secret text' and '<p>secret text</p>' in shown['body_html']

    def test_a_deleted_comment_cannot_be_edited_but_can_be_restored(
        self, client, auth, funded_user, task
    ):
        comment = add_comment(task, funded_user, 'text')
        delete_comment(comment)

        edit = patch(client, f'/tasks/{task.number}/comments/{comment.pk}', auth, {'body': 'x'})
        restored = post(client, f'/tasks/{task.number}/comments/{comment.pk}/restore', auth)

        assert edit.status_code == 400 and edit.json()['detail'] == 'This comment was deleted.'
        assert restored.status_code == 200
        assert restored.json()['body'] == 'text' and restored.json()['deleted_at'] is None

    def test_the_history_of_a_deleted_comment_is_readable(self, client, auth, funded_user, task):
        comment = add_comment(task, funded_user, 'one')
        edit_comment(comment, 'two')
        delete_comment(comment)

        history = get(client, f'/tasks/{task.number}/comments/{comment.pk}/history', auth)

        assert [r['body'] for r in history.json()] == ['one']

    def test_delete_and_restore_are_idempotent_and_free_when_there_is_nothing_to_do(
        self, client, auth, funded_user, task, settings
    ):
        comment = add_comment(task, funded_user, 'x')
        limit_rule(settings, 'TODO_EDIT_USER')

        assert (
            post(client, f'/tasks/{task.number}/comments/{comment.pk}/restore', auth).status_code
            == 200
        )
        assert (
            post(client, f'/tasks/{task.number}/comments/{comment.pk}/restore', auth).status_code
            == 200
        )
        assert (
            delete(client, f'/tasks/{task.number}/comments/{comment.pk}', auth).status_code == 204
        )
        assert (
            delete(client, f'/tasks/{task.number}/comments/{comment.pk}', auth).status_code == 204
        )
        assert (
            post(client, f'/tasks/{task.number}/comments/{comment.pk}/restore', auth).status_code
            == 429
        )

    def test_comments_of_a_task_in_the_trash_are_still_readable(
        self, client, auth, funded_user, task
    ):
        comment = add_comment(task, funded_user, 'x')
        trash_task(task)

        assert len(get(client, f'/tasks/{task.number}/comments', auth).json()) == 1
        assert (
            get(client, f'/tasks/{task.number}/comments/{comment.pk}/history', auth).status_code
            == 200
        )

    def test_a_comment_of_another_task_is_a_404(self, client, auth, funded_user, task):
        other_task = create_task(funded_user, title='Other')
        comment = add_comment(other_task, funded_user, 'x')

        path = f'/tasks/{task.number}/comments/{comment.pk}'
        assert patch(client, path, auth, {'body': 'y'}).status_code == 404
        assert delete(client, path, auth).status_code == 404
        assert post(client, f'{path}/restore', auth).status_code == 404
        assert get(client, f'{path}/history', auth).status_code == 404

    def test_another_users_task_is_a_404(self, client, auth, other_user):
        theirs = create_task(other_user, title='Theirs')
        comment = add_comment(theirs, other_user, 'x')

        path = f'/tasks/{theirs.number}/comments'
        assert get(client, path, auth).status_code == 404
        assert post(client, path, auth, {'body': 'y'}).status_code == 404
        assert patch(client, f'{path}/{comment.pk}', auth, {'body': 'y'}).status_code == 404
        assert Comment.objects.get(pk=comment.pk).body == 'x'

    def test_an_unknown_key_is_a_422(self, client, auth, task):
        assert (
            post(client, f'/tasks/{task.number}/comments', auth, {'body': 'x', 'a': 1}).status_code
            == 422
        )

    def test_the_comment_rate_limit(self, client, auth, task, settings):
        limit_rule(settings, 'TODO_COMMENT_USER')

        assert (
            post(client, f'/tasks/{task.number}/comments', auth, {'body': 'a'}).status_code == 201
        )
        assert (
            post(client, f'/tasks/{task.number}/comments', auth, {'body': 'b'}).status_code == 429
        )

    def test_a_comment_updates_the_tasks_count_and_references(
        self, client, auth, funded_user, task
    ):
        other = create_task(funded_user, title='Other')

        post(client, f'/tasks/{task.number}/comments', auth, {'body': f'see #{other.number}'})

        assert get(client, f'/tasks/{task.number}', auth).json()['comment_count'] == 1
        refs = get(client, f'/tasks/{other.number}', auth).json()['referenced_by']
        assert [r['number'] for r in refs] == [task.number] and refs[0]['comment'] is not None
