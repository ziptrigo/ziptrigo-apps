"""`services.labels` and `services.references`."""

import pytest
from django.core.exceptions import ValidationError

from apps.core.markdown import render

from ..models import Label
from ..services import (
    LABEL_DESCRIPTION_MAX,
    LABEL_NAME_MAX,
    TASK_URL_PATH,
    create_label,
    create_task,
    delete_label,
    list_labels,
    reorder_labels,
    resolver_for,
    trash_task,
    update_label,
)
from .factories import make_label, make_task

pytestmark = pytest.mark.django_db


def errors(callable_, *args, **kwargs) -> dict[str, list[str]]:
    with pytest.raises(ValidationError) as caught:
        callable_(*args, **kwargs)
    return caught.value.message_dict


class TestLabels:
    def test_create_with_defaults(self, user):
        label = create_label(user, '  Home  ')

        assert (label.name, label.color, label.description) == ('Home', '#8fa89e', '')

    def test_the_colour_is_lower_cased_and_validated(self, user):
        assert create_label(user, 'A', '#AABBCC').color == '#aabbcc'
        for bad in ('red', '#abc', '#gggggg', 'aabbcc'):
            assert 'color' in errors(create_label, user, 'B', bad)

    def test_the_name_is_required_and_capped(self, user):
        assert 'name' in errors(create_label, user, '  ')
        assert 'name' in errors(create_label, user, 'x' * (LABEL_NAME_MAX + 1))
        assert create_label(user, 'x' * LABEL_NAME_MAX)

    def test_the_description_is_capped(self, user):
        assert 'description' in errors(
            create_label, user, 'A', '', 'x' * (LABEL_DESCRIPTION_MAX + 1)
        )
        assert create_label(user, 'A', '', 'Shown on hover').description == 'Shown on hover'

    def test_names_are_unique_per_user_ignoring_case(self, user, other_user):
        create_label(user, 'Home')

        assert errors(create_label, user, 'home') == {
            'name': ['You already have a label with this name.']
        }
        assert create_label(other_user, 'Home')  # another user may

    def test_the_cap_per_user(self, user, config):
        config.max_labels_per_user = 2
        config.save()
        create_label(user, 'A')
        create_label(user, 'B')

        assert 'You can have up to 2 labels.' in errors(create_label, user, 'C')['__all__'][0]

    def test_new_labels_go_last(self, user):
        a, b = create_label(user, 'B'), create_label(user, 'A')

        assert [label.name for label in list_labels(user)] == [a.name, b.name]

    def test_update(self, user):
        label = create_label(user, 'Home', '#112233', 'old')
        other = create_label(user, 'Work')

        update_label(label, name='House')
        label.refresh_from_db()
        assert (label.name, label.color, label.description) == ('House', '#112233', 'old')

        update_label(label, color='#445566', description='')
        label.refresh_from_db()
        assert (label.color, label.description) == ('#445566', '')

        assert errors(update_label, label, name='WORK') == {
            'name': ['You already have a label with this name.']
        }
        update_label(label, name='HOUSE')  # its own name in another case is fine
        assert other.pk != label.pk

    def test_delete_keeps_the_tasks_other_labels(self, user, config):
        a, b = create_label(user, 'A'), create_label(user, 'B')
        task = create_task(user, title='T', labels=[a, b])

        delete_label(a)

        assert list(task.labels.all()) == [b]
        assert not Label.objects.filter(pk=a.pk).exists()

    def test_reorder(self, user, other_user):
        a, b, c = (make_label(user, name) for name in 'ABC')
        theirs = make_label(other_user, 'Theirs')

        assert reorder_labels(user, [c.pk, theirs.pk, a.pk, b.pk]) == 3

        assert [label.name for label in list_labels(user)] == ['C', 'A', 'B']
        theirs.refresh_from_db()
        assert theirs.position == 0

    def test_task_counts_leave_out_the_trash(self, user, config):
        label = create_label(user, 'A')
        create_task(user, title='1', labels=[label])
        gone = create_task(user, title='2', labels=[label])
        trash_task(gone)

        assert list_labels(user).get().task_count == 1


class TestReferences:
    def test_only_the_users_own_untrashed_tasks_resolve(self, user, other_user, config):
        mine = make_task(user, number=1234)
        trashed = make_task(user, number=1235)
        trash_task(trashed)
        theirs = make_task(other_user, number=1236)
        assert mine and theirs

        resolved = resolver_for(user)([1234, 1235, 1236, 9999])

        assert resolved == {1234: '/todo/1234/'}

    def test_one_query(self, user, django_assert_num_queries):
        make_task(user, number=1234)
        make_task(user, number=1235)
        resolver = resolver_for(user)

        with django_assert_num_queries(1):
            resolver([1234, 1235])

    def test_rendering_links_a_reference_and_a_task_url(self, user):
        make_task(user, number=1234)
        make_task(user, number=1300)

        html = render(
            'See #1234, #1301 and http://x.test/todo/1300/ and /todo/1234/',
            reference_resolver=resolver_for(user),
            task_url_path=TASK_URL_PATH,
        )

        assert '<a href="/todo/1234/" class="task-ref">#1234</a>' in html
        assert '#1301' in html and 'href="/todo/1301/"' not in html  # no such task: plain text
        assert 'href="/todo/1300/"' not in html  # a foreign host is an ordinary external link
        assert 'target="_blank"' in html

    def test_the_url_pattern(self):
        assert TASK_URL_PATH.match('/todo/1234/')
        assert TASK_URL_PATH.match('/todo/1234')
        assert not TASK_URL_PATH.match('/todo/labels/')
        assert not TASK_URL_PATH.match('/todo/1234/edit/')
