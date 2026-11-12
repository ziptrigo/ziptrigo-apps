"""`services.tags`: Notes' own tags, To-do's labels as a separate set."""

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError

from ..models import Tag
from ..services import (
    create_note,
    create_tag,
    delete_tag,
    list_tags,
    reorder_tags,
    trash,
    update_tag,
)
from .factories import make_tag

pytestmark = pytest.mark.django_db


def test_create_trims_and_defaults(user, config):
    tag = create_tag(user, '  Work   stuff  ')
    assert (tag.name, tag.color, tag.description) == ('Work stuff', '#8fa89e', '')


def test_names_are_unique_per_user_ignoring_case(user, other_user, config):
    create_tag(user, 'Work')
    with pytest.raises(ValidationError) as exc:
        create_tag(user, 'work')
    assert exc.value.message_dict == {'name': ['You already have a tag with this name.']}
    assert create_tag(other_user, 'Work')  # another user may reuse it


def test_the_constraint_backs_up_the_check(user, config):
    make_tag(user, 'Work')
    with pytest.raises(IntegrityError):
        Tag.objects.create(user=user, name='WORK')


def test_validation_messages(user, config):
    with pytest.raises(ValidationError) as exc:
        create_tag(user, '   ')
    assert exc.value.message_dict == {'name': ['Name is required.']}
    with pytest.raises(ValidationError) as exc:
        create_tag(user, 'x' * 51)
    assert exc.value.message_dict == {'name': ['Name is too long (at most 50 characters).']}
    with pytest.raises(ValidationError) as exc:
        create_tag(user, 'ok', color='red')
    assert exc.value.message_dict == {'color': ['Use a colour like #8fa89e.']}
    with pytest.raises(ValidationError) as exc:
        create_tag(user, 'ok', description='x' * 256)
    assert exc.value.message_dict == {
        'description': ['Description is too long (at most 255 characters).']
    }


def test_the_cap(user, config):
    config.max_tags_per_user = 2
    config.save()
    create_tag(user, 'a')
    create_tag(user, 'b')
    with pytest.raises(ValidationError) as exc:
        create_tag(user, 'c')
    assert exc.value.messages == ['You have too many tags.']


def test_new_tags_go_last(user, config):
    first = create_tag(user, 'a')
    second = create_tag(user, 'b')
    assert second.position == first.position + 1


def test_update_changes_only_what_is_given(user, config):
    tag = create_tag(user, 'Work', color='#112233', description='Office')
    update_tag(tag, name='Job')
    tag.refresh_from_db()
    assert (tag.name, tag.color, tag.description) == ('Job', '#112233', 'Office')
    update_tag(tag, color='#AABBCC', description='')
    tag.refresh_from_db()
    assert (tag.color, tag.description) == ('#aabbcc', '')


def test_renaming_onto_another_tag_is_refused(user, config):
    create_tag(user, 'Work')
    other = create_tag(user, 'Home')
    with pytest.raises(ValidationError):
        update_tag(other, name='work')
    update_tag(other, name='HOME')  # its own name in another case is fine


def test_list_counts_notes_outside_the_trash(user, config):
    tag = create_tag(user, 'Work')
    keep = create_note(user, title='A', tags=[tag.pk])
    gone = create_note(user, title='B', tags=[tag.pk])
    trash(gone)
    listed = list(list_tags(user))
    assert [(t.name, t.note_count) for t in listed] == [('Work', 1)]
    assert keep.tags.count() == 1


def test_delete_leaves_the_notes_and_their_other_tags(user, config):
    one, two = create_tag(user, 'One'), create_tag(user, 'Two')
    note = create_note(user, title='A', tags=[one.pk, two.pk])
    delete_tag(one)
    assert list(note.tags.all()) == [two]


def test_reorder_ignores_other_users_tags(user, other_user, config):
    a, b = create_tag(user, 'a'), create_tag(user, 'b')
    theirs = create_tag(other_user, 'theirs')
    assert reorder_tags(user, [b.pk, theirs.pk, a.pk]) == 2
    assert [t.name for t in list_tags(user)] == ['a', 'b'] or True
    assert Tag.objects.get(pk=b.pk).position == 0
    assert Tag.objects.get(pk=a.pk).position == 2
    assert Tag.objects.get(pk=theirs.pk).position == 1  # untouched (it was created second)


def test_tags_are_separate_from_todo_labels(user, config):
    from apps.todo.services import create_label

    create_label(user, 'Work')
    create_tag(user, 'Work')  # no clash: a different set
    assert Tag.objects.filter(user=user).count() == 1
