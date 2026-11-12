"""Version history: coalescing by editor and time, the cap, restore and conflicts."""

from datetime import timedelta

import pytest
from django.utils import timezone

from ..limits import VERSION_COALESCE
from ..models import NoteVersion
from ..services import (
    Editor,
    NoteConflictError,
    create_note,
    list_versions,
    restore_version,
    save_note,
    toggle_checklist,
)
from ..templatetags.notes_ui import version_author
from .factories import make_user

pytestmark = pytest.mark.django_db


def save(note, editor, content, **kwargs):
    fresh = type(note).objects.get(pk=note.pk)
    return save_note(fresh, editor=editor, content=content, base_version=fresh.version, **kwargs)


def test_creation_records_version_one(user, config):
    note = create_note(user, title='A', content='one')
    version = note.versions.get()
    assert (version.number, version.content, version.editor_kind) == (1, 'one', 'owner')


class TestCoalescing:
    def test_a_burst_of_saves_by_the_same_editor_is_one_version(self, user, config):
        note = create_note(user, title='A', content='one')
        owner = Editor.for_user(note, user)
        for text in ('two', 'three', 'four'):
            save(note, owner, text)
        assert list(note.versions.values_list('number', 'content')) == [(1, 'four')]
        assert type(note).objects.get(pk=note.pk).version == 4

    def test_ticked_boxes_coalesce_too(self, user, config):
        note = create_note(user, title='A', content='- [ ] a\n- [ ] b\n')
        owner = Editor.for_user(note, user)
        toggle_checklist(note, 0, True, editor=owner)
        toggle_checklist(note, 1, True, editor=owner)
        assert note.versions.count() == 1
        assert note.versions.get().content == '- [x] a\n- [x] b\n'

    def test_after_the_window_a_new_version_is_kept(self, user, config):
        note = create_note(user, title='A', content='one')
        owner = Editor.for_user(note, user)
        NoteVersion.objects.filter(note=note).update(
            updated_at=timezone.now() - VERSION_COALESCE - timedelta(seconds=1)
        )
        save(note, owner, 'two')
        assert list(note.versions.order_by('number').values_list('number', 'content')) == [
            (1, 'one'),
            (2, 'two'),
        ]

    def test_another_editor_never_overwrites_a_version(self, user, other_user, config):
        note = create_note(user, title='A', content='one')
        owner = Editor.for_user(note, user)
        other = Editor.for_user(note, other_user)
        stranger = Editor.for_user(note, None)
        save(note, other, 'two')
        save(note, stranger, 'three')
        save(note, owner, 'four')
        kinds = list(
            note.versions.order_by('number').values_list('number', 'editor_kind', 'content')
        )
        assert kinds == [
            (1, 'owner', 'one'),
            (2, 'user', 'two'),
            (3, 'anonymous', 'three'),
            (4, 'owner', 'four'),
        ]
        assert note.versions.get(number=2).editor_id == other_user.pk
        assert note.versions.get(number=3).editor_id is None

    def test_two_different_signed_in_users_are_different_editors(self, user, config):
        note = create_note(user, title='A', content='one')
        first, second = make_user(), make_user()
        save(note, Editor.for_user(note, first), 'two')
        save(note, Editor.for_user(note, second), 'three')
        assert note.versions.count() == 3


class TestCap:
    def test_the_oldest_versions_beyond_the_cap_are_deleted(self, user, config):
        from ..models import NotesSettings

        row = NotesSettings.load()
        row.max_versions_per_note = 3
        row.save()
        note = create_note(user, title='A', content='v1')
        editors = [Editor.for_user(note, make_user()) for _ in range(5)]
        for index, editor in enumerate(editors, start=2):
            save(note, editor, f'v{index}')
        numbers = list(note.versions.values_list('number', flat=True))  # newest first
        assert numbers == [6, 5, 4]
        assert note.versions.get(number=6).content == 'v6'


class TestRestore:
    def test_restores_title_and_content_as_a_new_save(self, user, config):
        note = create_note(user, title='First', content='one')
        NoteVersion.objects.filter(note=note).update(updated_at=timezone.now() - timedelta(hours=1))
        note = save_note(
            note,
            editor=Editor.for_user(note, user),
            title='Second',
            content='two',
            base_version=1,
        )
        old = note.versions.get(number=1)
        # Not coalesced: the old row is more than the window old.
        assert note.versions.count() == 2
        restored = restore_version(note, note.versions.get(number=1))
        assert (restored.title, restored.content, restored.version) == ('First', 'one', 3)
        # The state before the restore is kept in the history.
        assert note.versions.get(number=2).content == 'two'
        assert old.content == 'one'

    def test_a_version_of_another_note_is_refused(self, user, config):
        from django.core.exceptions import ValidationError

        one = create_note(user, title='A')
        two = create_note(user, title='B')
        with pytest.raises(ValidationError):
            restore_version(one, two.versions.get())


class TestConflict:
    def test_two_editors_from_the_same_version(self, user, other_user, config):
        note = create_note(user, title='A', content='base')
        first = save_note(
            note, editor=Editor.for_user(note, other_user), content='theirs', base_version=1
        )
        with pytest.raises(NoteConflictError) as exc:
            save_note(note, editor=Editor.for_user(note, user), content='mine', base_version=1)
        assert exc.value.current_content == 'theirs'
        # Saving again from the version that is current succeeds.
        mine = save_note(
            note, editor=Editor.for_user(note, user), content='mine', base_version=first.version
        )
        assert mine.version == 3

    def test_a_refused_save_records_no_version(self, user, config):
        note = create_note(user, title='A', content='base')
        save(note, Editor.for_user(note, user), 'next')
        before = note.versions.count()
        with pytest.raises(NoteConflictError):
            save_note(note, editor=Editor.for_user(note, user), content='x', base_version=1)
        assert note.versions.count() == before


def test_version_author_wording(user, other_user, config):
    note = create_note(user, title='A')
    assert version_author(note.versions.get()) == 'You'
    save(note, Editor.for_user(note, other_user), 'x')
    save(note, Editor.for_user(note, None), 'y')
    by_number = {v.number: v for v in list_versions(note)}
    assert version_author(by_number[2]) == 'Other User'
    assert version_author(by_number[3]) == 'Someone'
    other_user.delete()
    refreshed = {v.number: v for v in list_versions(note)}
    assert version_author(refreshed[2]) == 'Someone'
