"""`services.notes`: creating, saving, checklists, archive, Trash, duplicate and manual order."""

from datetime import timedelta

import pytest
from django.core.exceptions import ValidationError
from django.utils import timezone

from ..models import Note, NotesSettings
from ..services import (
    CostChangedError,
    Editor,
    NoteConflictError,
    archive,
    create_note,
    delete_forever,
    duplicate,
    purge_trash,
    reorder,
    restore,
    save_note,
    set_tags,
    toggle_checklist,
    trash,
    unarchive,
)
from .factories import make_note, make_tag, make_user

pytestmark = pytest.mark.django_db


def owner(note):
    return Editor.for_user(note, note.user)


def messages_of(exc_info) -> list[str]:
    return list(exc_info.value.messages)


class TestCreate:
    def test_creates_a_free_note_with_version_one(self, user, config):
        note = create_note(user, title='  Shopping  ', content='a\r\nb')
        assert note.title == 'Shopping'
        assert note.content == 'a\nb'
        assert note.version == 1
        assert note.is_free and note.paid_through is None and note.charged_credits == 0
        assert note.title_text == 'shopping'
        versions = list(note.versions.all())
        assert [(v.number, v.editor_kind, v.editor_id) for v in versions] == [(1, 'owner', user.pk)]
        assert versions[0].content == 'a\nb'

    def test_title_is_required(self, user, config):
        with pytest.raises(ValidationError) as exc:
            create_note(user, title='   ')
        assert exc.value.message_dict == {'title': ['Give the note a title.']}

    def test_title_and_content_caps(self, user, config):
        with pytest.raises(ValidationError) as exc:
            create_note(user, title='x' * 201)
        assert exc.value.message_dict == {'title': ['Keep the title to 200 characters or fewer.']}
        with pytest.raises(ValidationError) as exc:
            create_note(user, title='ok', content='x' * 50_001)
        assert exc.value.message_dict == {
            'content': ['Keep the note to 50,000 characters or fewer.']
        }
        assert create_note(user, title='ok', content='x' * 50_000).content == 'x' * 50_000
        assert Note.objects.filter(user=user).count() == 1

    def test_a_new_note_goes_first_in_the_manual_order(self, user, config):
        first = create_note(user, title='A')
        second = create_note(user, title='B')
        assert second.position == first.position - 1
        assert first.position == 0

    def test_tags_must_be_the_users_own(self, user, other_user, config):
        mine = make_tag(user, 'Mine')
        theirs = make_tag(other_user, 'Theirs')
        note = create_note(user, title='T', tags=[mine.pk])
        assert list(note.tags.all()) == [mine]
        with pytest.raises(ValidationError) as exc:
            create_note(user, title='T', tags=[theirs.pk])
        assert exc.value.message_dict == {'tags': ['Choose your own tags.']}
        with pytest.raises(ValidationError):
            create_note(user, title='T', tags=['not-a-uuid'])

    def test_the_cap_on_notes_outside_the_trash(self, user, config):
        config.max_notes_per_user = 2
        config.save()
        create_note(user, title='A')
        gone = create_note(user, title='B')
        with pytest.raises(ValidationError) as exc:
            create_note(user, title='C')
        assert messages_of(exc) == ['You have too many notes. Delete some first.']
        trash(gone)
        create_note(user, title='C')

    def test_expected_cost_must_match(self, user, config):
        with pytest.raises(CostChangedError) as exc:
            create_note(user, title='A', expected_cost=3)
        assert exc.value.quote == 0
        assert not Note.objects.filter(user=user).exists()
        create_note(user, title='A', expected_cost=0)

    def test_a_user_with_no_account_row_yet_is_fine(self, user, config):
        from ..models import NotesAccount

        assert not NotesAccount.objects.filter(user=user).exists()
        create_note(user, title='A')
        assert NotesAccount.objects.filter(user=user).exists()


class TestSave:
    def test_saves_and_counts_the_version(self, user, config):
        note = create_note(user, title='A', content='one')
        saved = save_note(
            note, editor=owner(note), title='B', content='two', base_version=note.version
        )
        assert (saved.title, saved.content, saved.version) == ('B', 'two', 2)
        assert saved.title_text == 'b' and saved.content_text == 'two'
        note.refresh_from_db()
        assert note.version == 2

    def test_updated_at_moves_on_a_save_only(self, user, config, freeze_now):
        freeze_now(2026, 10, 28)
        note = create_note(user, title='A')
        created = note.updated_at
        freeze_now(2026, 10, 29)
        archive(note)
        set_tags(note, [])
        reorder(user, [note.pk])
        note.refresh_from_db()
        assert note.updated_at == created
        save_note(note, editor=owner(note), content='x', base_version=1)
        note.refresh_from_db()
        assert note.updated_at > created

    def test_a_partial_save_keeps_the_other_field(self, user, config):
        note = create_note(user, title='A', content='one')
        saved = save_note(note, editor=owner(note), content='two', base_version=1)
        assert (saved.title, saved.content) == ('A', 'two')
        saved = save_note(saved, editor=owner(note), title='B', base_version=2)
        assert (saved.title, saved.content) == ('B', 'two')

    def test_nothing_changed_writes_nothing(self, user, config):
        note = create_note(user, title='A', content='one')
        saved = save_note(note, editor=owner(note), title='A', content='one', base_version=1)
        assert saved.version == 1
        assert note.versions.count() == 1

    def test_nothing_changed_is_not_a_conflict(self, user, config):
        note = create_note(user, title='A', content='one')
        save_note(note, editor=owner(note), content='two', base_version=1)
        # A stale base_version with the same text as stored is nothing to save.
        saved = save_note(note, editor=owner(note), content='two', base_version=1)
        assert saved.version == 2

    def test_a_stale_base_version_is_a_conflict(self, user, config):
        note = create_note(user, title='A', content='one')
        save_note(note, editor=owner(note), content='two', base_version=1)
        with pytest.raises(NoteConflictError) as exc:
            save_note(note, editor=owner(note), content='mine', base_version=1)
        error = exc.value
        assert error.messages == ['This note was changed by someone else while you were editing.']
        assert (error.current_version, error.current_title, error.current_content) == (
            2,
            'A',
            'two',
        )
        note.refresh_from_db()
        assert note.content == 'two' and note.version == 2

    def test_validates_like_create(self, user, config):
        note = create_note(user, title='A')
        with pytest.raises(ValidationError) as exc:
            save_note(note, editor=owner(note), title=' ', base_version=1)
        assert exc.value.message_dict == {'title': ['Give the note a title.']}
        with pytest.raises(ValidationError):
            save_note(note, editor=owner(note), content='x' * 50_001, base_version=1)

    def test_a_note_in_the_trash_or_locked_is_read_only(self, user, config):
        note = create_note(user, title='A')
        note.locked_at = timezone.now()
        note.save()
        with pytest.raises(ValidationError) as exc:
            save_note(note, editor=owner(note), content='x', base_version=1)
        assert messages_of(exc) == ['This note is locked until its renewal is paid.']
        note.locked_at = None
        note.save()
        trash(note)
        with pytest.raises(ValidationError) as exc:
            save_note(note, editor=owner(note), content='x', base_version=1)
        assert messages_of(exc) == ['This note is in the Trash.']
        with pytest.raises(ValidationError):
            toggle_checklist(note, 0, True, editor=owner(note))

    def test_the_editor_kinds(self, user, other_user, config):
        note = create_note(user, title='A')
        assert Editor.for_user(note, user).kind == 'owner'
        assert Editor.for_user(note, other_user).kind == 'user'
        assert Editor.for_user(note, None).kind == 'anonymous'


class TestChecklist:
    def test_ticks_and_unticks_a_box(self, user, config):
        note = create_note(user, title='A', content='- [ ] one\n- [ ] two\n')
        ticked = toggle_checklist(note, 1, True, editor=owner(note))
        assert ticked.content == '- [ ] one\n- [x] two\n'
        assert ticked.version == 2
        unticked = toggle_checklist(ticked, 1, False, editor=owner(note))
        assert unticked.content == '- [ ] one\n- [ ] two\n'

    def test_a_box_inside_a_table_document_counts_like_the_render(self, user, config):
        content = '| a | b |\n|---|---|\n| 1 | 2 |\n\n- [ ] first\n- [ ] second\n'
        note = create_note(user, title='A', content=content)
        ticked = toggle_checklist(note, 1, True, editor=owner(note))
        assert '- [x] second' in ticked.content and '- [ ] first' in ticked.content

    def test_two_boxes_ticked_one_after_the_other_both_survive(self, user, config):
        note = create_note(user, title='A', content='- [ ] one\n- [ ] two\n')
        stale = Note.objects.get(pk=note.pk)
        toggle_checklist(note, 0, True, editor=owner(note))
        toggle_checklist(stale, 1, True, editor=owner(note))  # a stale object: no base_version
        assert Note.objects.get(pk=note.pk).content == '- [x] one\n- [x] two\n'

    def test_no_such_item(self, user, config):
        note = create_note(user, title='A', content='- [ ] one\n')
        with pytest.raises(ValidationError) as exc:
            toggle_checklist(note, 5, True, editor=owner(note))
        assert exc.value.message_dict == {'index': ['That checklist item no longer exists.']}

    def test_the_same_state_writes_nothing(self, user, config):
        note = create_note(user, title='A', content='- [x] one\n')
        again = toggle_checklist(note, 0, True, editor=owner(note))
        assert again.version == 1


class TestArchiveAndTrash:
    def test_archive_and_unarchive(self, user, config):
        note = create_note(user, title='A')
        assert archive(note).is_archived
        assert archive(note).is_archived  # idempotent
        assert not unarchive(note).is_archived

    def test_archiving_a_locked_note_is_refused(self, user, config):
        note = create_note(user, title='A')
        note.locked_at = timezone.now()
        note.save()
        with pytest.raises(ValidationError) as exc:
            archive(note)
        assert messages_of(exc) == ['This note is locked until its renewal is paid.']

    def test_trash_is_always_allowed_and_idempotent(self, user, config):
        note = create_note(user, title='A')
        note.locked_at = timezone.now()
        note.save()
        first = trash(note).deleted_at
        assert first is not None
        assert trash(note).deleted_at == first

    def test_restore_a_free_note_with_a_slot_open(self, user, config):
        note = create_note(user, title='A')
        trash(note)
        restored = restore(note)
        assert not restored.is_trashed and restored.is_free

    def test_restoring_a_note_not_in_the_trash_is_a_no_op(self, user, config):
        note = create_note(user, title='A')
        assert restore(note).pk == note.pk

    def test_delete_forever_needs_the_trash(self, user, config):
        note = create_note(user, title='A')
        with pytest.raises(ValidationError) as exc:
            delete_forever(note)
        assert messages_of(exc) == ['Only a note in the Trash can be deleted for good.']
        trash(note)
        delete_forever(note)
        assert not Note.objects.filter(pk=note.pk).exists()

    def test_purge_deletes_only_what_was_trashed_long_enough_ago(self, user, config):
        old = make_note(user, deleted_at=timezone.now() - timedelta(days=31))
        recent = make_note(user, deleted_at=timezone.now() - timedelta(days=29))
        live = make_note(user)
        assert purge_trash() == 1
        assert not Note.objects.filter(pk=old.pk).exists()
        assert Note.objects.filter(pk__in=[recent.pk, live.pk]).count() == 2

    def test_purge_follows_the_trash_days_setting(self, user, config):
        settings_row = NotesSettings.load()
        settings_row.trash_days = 3
        settings_row.save()
        note = make_note(user, deleted_at=timezone.now() - timedelta(days=4))
        assert purge_trash() == 1
        assert not Note.objects.filter(pk=note.pk).exists()

    def test_purge_skips_a_bad_row_and_carries_on(self, user, config, monkeypatch):
        bad = make_note(user, deleted_at=timezone.now() - timedelta(days=40))
        good = make_note(user, deleted_at=timezone.now() - timedelta(days=40))
        from ..services import notes as notes_service

        real = notes_service.lock_account
        calls = []

        def flaky(user_id):
            calls.append(user_id)
            if len(calls) == 1:
                raise RuntimeError('boom')
            return real(user_id)

        monkeypatch.setattr(notes_service, 'lock_account', flaky)
        assert purge_trash() == 1
        assert Note.objects.filter(pk__in=[bad.pk, good.pk]).count() == 1


class TestDuplicate:
    def test_copy_has_the_content_and_no_tags(self, user, config):
        tag = make_tag(user)
        note = create_note(user, title='Recipe', content='- [x] eggs', tags=[tag.pk])
        copy = duplicate(note, user)
        assert copy.pk != note.pk
        assert (copy.title, copy.content) == ('Recipe (copy)', '- [x] eggs')
        assert list(copy.tags.all()) == []
        assert copy.versions.count() == 1

    def test_keep_title(self, user, config):
        note = create_note(user, title='Recipe')
        assert duplicate(note, user, keep_title=True).title == 'Recipe'

    def test_the_copy_title_is_cut_to_the_limit(self, user, config):
        note = make_note(user, title='x' * 200)
        copy = duplicate(note, user)
        assert len(copy.title) == 200 and copy.title.endswith(' (copy)')

    def test_into_another_users_account(self, user, other_user, config):
        note = create_note(user, title='Shared')
        copy = duplicate(note, other_user, keep_title=True)
        assert copy.user_id == other_user.pk and copy.is_free


class TestReorder:
    def test_hands_out_the_existing_positions_in_the_order_sent(self, user, config):
        a = create_note(user, title='A')
        b = create_note(user, title='B')
        c = create_note(user, title='C')
        positions = sorted(n.position for n in (a, b, c))
        assert reorder(user, [a.pk, b.pk, c.pk]) == 3
        got = {n.pk: n.position for n in Note.objects.filter(user=user)}
        assert [got[a.pk], got[b.pk], got[c.pk]] == positions

    def test_ignores_other_users_and_trashed_notes(self, user, other_user, config):
        a = create_note(user, title='A')
        b = create_note(user, title='B')
        theirs = make_note(other_user, position=-50)
        gone = create_note(user, title='G')
        trash(gone)
        before = Note.objects.get(pk=theirs.pk).position
        assert reorder(user, [theirs.pk, gone.pk, a.pk, b.pk]) == 2
        assert Note.objects.get(pk=theirs.pk).position == before

    def test_equal_positions_still_get_an_order(self, user, config):
        notes = [make_note(user, title=n, position=0) for n in 'abc']
        reorder(user, [notes[2].pk, notes[0].pk, notes[1].pk])
        ordered = list(Note.objects.filter(user=user).order_by('position'))
        assert [n.title for n in ordered] == ['c', 'a', 'b']

    def test_ties_are_renumbered_without_landing_on_a_note_outside_the_request(self, user, config):
        a = make_note(user, title='a', position=0)
        b = make_note(user, title='b', position=0)
        outside = make_note(user, title='outside', position=1)
        reorder(user, [b.pk, a.pk])
        positions = [n.position for n in Note.objects.filter(user=user)]
        assert len(set(positions)) == len(positions)
        ordered = Note.objects.filter(user=user).order_by('position')
        listed = [n.title for n in ordered if n.pk != outside.pk]
        assert listed == ['b', 'a']
        assert Note.objects.get(pk=outside.pk).position > min(positions)

    def test_a_partial_list_permutes_among_itself(self, user, config):
        a = create_note(user, title='A')
        create_note(user, title='B')
        c = create_note(user, title='C')  # c, b, a in the list
        reorder(user, [a.pk, c.pk])
        order = [n.title for n in Note.objects.filter(user=user).order_by('position')]
        assert order == ['A', 'B', 'C']


def test_set_tags_replaces_them(user, config):
    one, two = make_tag(user, 'One'), make_tag(user, 'Two')
    note = create_note(user, title='A', tags=[one.pk])
    set_tags(note, [two.pk])
    assert list(note.tags.all()) == [two]
    set_tags(note, [])
    assert note.tags.count() == 0


def test_other_users_tags_are_refused(user, other_user, config):
    theirs = make_tag(other_user, 'Theirs')
    note = create_note(user, title='A')
    with pytest.raises(ValidationError):
        set_tags(note, [theirs.pk])


def test_make_user_helper_makes_distinct_emails():
    assert make_user().email != make_user().email
