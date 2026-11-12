"""The Notes web pages: lists, the note page, the editor and its conflict panel, the actions, the
history, tags, reorder, and the rate limits."""

import html
import re
from datetime import timedelta

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.billing.services import get_balance

from ..models import Note, NoteVersion, Tag
from ..services import (
    Editor,
    archive,
    create_note,
    create_tag,
    save_note,
    trash,
)
from .factories import make_note, make_tag, make_user

pytestmark = pytest.mark.django_db

HTMX = {'HTTP_HX_REQUEST': 'true'}


def url(name, *args):
    return reverse(f'notes:{name}', args=args)


def content(response) -> str:
    return response.content.decode()


def rows(response) -> list[str]:
    """The ids of the notes a list page shows, in order."""
    return re.findall(r'data-sort-id="([0-9a-f-]{36})"', content(response))


def titles(response) -> list[str]:
    return re.findall(
        r'class="min-w-0 truncate text-sm font-medium[^"]*">([^<]*)</a>', content(response)
    )


class TestLists:
    def test_login_is_required(self):
        client = Client()
        for name in ('list', 'archived', 'trash', 'tags', 'new'):
            response = client.get(url(name))
            assert response.status_code == 302 and '/account/login' in response['Location']

    def test_the_main_list_shows_active_notes_in_manual_order(self, web, user, other_user):
        first = create_note(user, title='First')
        second = create_note(user, title='Second')  # newest first by position
        archive(create_note(user, title='Archived one'))
        trash(create_note(user, title='Trashed one'))
        make_note(other_user, title='Theirs')
        response = web.get(url('list'))
        assert response.status_code == 200
        assert titles(response) == ['Second', 'First']
        assert rows(response) == [str(second.pk), str(first.pk)]
        text = content(response)
        assert 'Theirs' not in text and 'Archived one' not in text and 'Trashed one' not in text

    def test_drag_handles_only_in_manual_order_without_a_tag_filter(self, web, user):
        tag = create_tag(user, 'Work')
        create_note(user, title='A', tags=[tag.pk])
        create_note(user, title='B')
        assert 'drag-handle' in content(web.get(url('list')))
        assert 'drag-handle' not in content(web.get(url('list'), {'sort': 'title'}))
        assert 'drag-handle' not in content(web.get(url('list'), {'sort': 'manual', 'dir': 'desc'}))
        assert 'drag-handle' not in content(web.get(url('list'), {'tag': str(tag.pk)}))
        assert 'drag-handle' not in content(web.get(url('archived')))

    def test_sorts_and_direction(self, web, user, freeze_now):
        freeze_now(2026, 10, 1)
        b = create_note(user, title='bravo')
        freeze_now(2026, 10, 2)
        a = create_note(user, title='alpha')
        freeze_now(2026, 10, 3)
        save_note(b, editor=Editor.for_user(b, user), content='x', base_version=1)
        assert titles(web.get(url('list'), {'sort': 'title'})) == ['alpha', 'bravo']
        assert titles(web.get(url('list'), {'sort': 'title', 'dir': 'desc'})) == ['bravo', 'alpha']
        assert titles(web.get(url('list'), {'sort': 'updated'})) == ['bravo', 'alpha']
        assert titles(web.get(url('list'), {'sort': 'updated', 'dir': 'asc'})) == ['alpha', 'bravo']
        assert titles(web.get(url('list'), {'sort': 'created'})) == ['alpha', 'bravo']
        assert titles(web.get(url('list'), {'sort': 'bogus'})) == ['alpha', 'bravo']  # manual
        assert a.pk and b.pk

    def test_the_sort_bar_links_the_active_sort_to_its_opposite(self, web, user):
        create_note(user, title='A')
        text = content(web.get(url('list'), {'sort': 'title'}))
        assert 'Click to sort descending' in text
        assert 'sorted ascending' in text
        text = content(web.get(url('list'), {'sort': 'title', 'dir': 'desc'}))
        assert 'Click to sort ascending' in text

    def test_archived_has_no_manual_sort_and_defaults_to_updated(self, web, user, freeze_now):
        freeze_now(2026, 10, 1)
        old = create_note(user, title='old')
        freeze_now(2026, 10, 2)
        new = create_note(user, title='new')
        archive(old)
        archive(new)
        response = web.get(url('archived'), {'sort': 'manual'})
        assert titles(response) == ['new', 'old']
        assert '>Manual<' not in content(response)

    def test_tag_filter(self, web, user, other_user):
        work = create_tag(user, 'Work')
        home = create_tag(user, 'Home')
        create_note(user, title='W', tags=[work.pk])
        create_note(user, title='H', tags=[home.pk])
        foreign = make_tag(other_user, 'Work2')
        assert titles(web.get(url('list'), {'tag': str(work.pk)})) == ['W']
        assert sorted(titles(web.get(url('list')))) == ['H', 'W']
        # Another user's tag, or junk, is ignored.
        assert len(titles(web.get(url('list'), {'tag': str(foreign.pk)}))) == 2
        assert len(titles(web.get(url('list'), {'tag': 'junk'}))) == 2

    def test_row_shows_excerpt_tags_and_lock(self, web, user):
        tag = create_tag(user, 'Work')
        note = create_note(
            user, title='Plan', content='# Heading\n\nSome **bold** words', tags=[tag.pk]
        )
        Note.objects.filter(pk=note.pk).update(locked_at=timezone.now())
        text = content(web.get(url('list')))
        assert 'Heading Some bold words' in text
        assert 'Work' in text and 'Locked' in text

    def test_pagination(self, web, user):
        for number in range(51):
            make_note(user, title=f'N{number:02}', position=number)
        first = web.get(url('list'))
        assert len(rows(first)) == 50 and 'Page 1 of 2' in content(first)
        second = web.get(url('list'), {'page': 2})
        assert len(rows(second)) == 1 and 'Page 2 of 2' in content(second)

    def test_trash_shows_days_left_and_restore(self, web, user):
        note = make_note(user, title='Gone', deleted_at=timezone.now() - timedelta(days=10))
        response = web.get(url('trash'))
        text = content(response)
        assert 'deleted in 20 days' in text
        assert url('restore', note.pk) in text and url('delete', note.pk) in text
        assert 'Sort by' not in text

    def test_trash_row_asks_to_confirm_a_costly_restore(self, web, user, config):
        config.free_notes = 0
        config.save()
        make_note(user, title='Gone', deleted_at=timezone.now(), is_free=True)
        text = content(web.get(url('trash')))
        assert 'data-cost="3"' in text and 'Restore for 3 credits' in text

    def test_the_trash_page_needs_no_queries_per_row(self, web, user, config):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        def trash_queries() -> int:
            with CaptureQueriesContext(connection) as captured:
                assert web.get(url('trash')).status_code == 200
            return len(captured)

        make_note(user, title='One', deleted_at=timezone.now())
        web.get(url('trash'))  # the account, sessions and the like exist now
        few = trash_queries()
        for number in range(19):
            make_note(user, title=f'More {number}', deleted_at=timezone.now(), is_free=number % 2)
        assert trash_queries() == few

    def test_empty_states(self, web):
        assert 'No notes yet' in content(web.get(url('list')))
        assert 'Nothing archived' in content(web.get(url('archived')))
        assert 'The Trash is empty' in content(web.get(url('trash')))

    def test_the_partial_redirects_when_opened_directly(self, web):
        response = web.get(url('list-partial'), {'view': 'trash', 'sort': 'title', 'tag': 'x"y'})
        assert response.status_code == 302
        assert response['Location'] == '/notes/trash/?sort=title'

    def test_the_partial_for_htmx_pushes_the_page_url(self, web, user):
        create_note(user, title='A')
        response = web.get(url('list-partial'), {'view': 'notes', 'sort': 'title'}, **HTMX)
        assert response.status_code == 200
        assert response['HX-Push-Url'] == '/notes/?sort=title'
        assert '<html' not in content(response)

    def test_the_sidebar_links(self, web):
        text = content(web.get(url('list')))
        for name in ('new', 'list', 'archived', 'trash', 'tags'):
            assert f'href="{url(name)}"' in text


class TestNotePage:
    def test_renders_markdown_with_colour_and_tables(self, web, user):
        note = create_note(
            user,
            title='Doc',
            content='```python\nprint("hi")\n```\n\n| a | b |\n|:-:|--:|\n| 1 | 2 |\n\n- [ ] todo\n',
        )
        response = web.get(url('note', note.pk))
        text = content(response)
        assert response.status_code == 200
        assert 'class="hl"' in text and 'hl-nb' in text
        assert '<div class="md-table"><table>' in text
        assert 'class="text-center"' in text
        assert f'data-checklist-url="{url("checklist", note.pk)}"' in text
        assert 'Doc' in text

    def test_another_users_note_is_a_404(self, web, other_user):
        theirs = make_note(other_user)
        for name in ('note', 'edit', 'history', 'download'):
            assert web.get(url(name, theirs.pk)).status_code == 404
        for name in (
            'archive',
            'unarchive',
            'trash-note',
            'restore',
            'delete',
            'duplicate',
            'checklist',
            'tags-set',
        ):
            assert web.post(url(name, theirs.pk)).status_code == 404

    def test_script_in_a_title_or_content_is_escaped(self, web, user):
        note = create_note(user, title='<script>alert(1)</script>', content='<script>x</script>\n')
        text = content(web.get(url('note', note.pk)))
        assert '<script>alert(1)</script>' not in text and '<script>x</script>' not in text

    def test_banners(self, web, user):
        note = create_note(user, title='A')
        Note.objects.filter(pk=note.pk).update(locked_at=timezone.now())
        text = content(web.get(url('note', note.pk)))
        assert 'This note is locked until its renewal is paid.' in text
        assert 'Top up' in text and 'data-checklist-url' not in text
        assert url('edit', note.pk) not in text
        Note.objects.filter(pk=note.pk).update(locked_at=None, deleted_at=timezone.now())
        text = content(web.get(url('note', note.pk)))
        assert 'This note is in the Trash.' in text and 'Restore' in text

    def test_buttons(self, web, user):
        note = create_note(user, title='A')
        text = content(web.get(url('note', note.pk)))
        for name in ('edit', 'history', 'duplicate', 'download', 'archive', 'trash-note'):
            assert url(name, note.pk) in text
        assert 'Copy' in text

    def test_tag_popover_lists_tags_with_the_note_s_checked(self, web, user):
        one, two = create_tag(user, 'One'), create_tag(user, 'Two')
        note = create_note(user, title='A', tags=[one.pk])
        text = content(web.get(url('note', note.pk)))
        assert re.search(rf'value="{one.pk}"\s+checked', text)
        assert not re.search(rf'value="{two.pk}"\s+checked', text)

    def test_download(self, web, user):
        note = create_note(user, title='My: Great Note!', content='# hi\n')
        response = web.get(url('download', note.pk))
        assert response['Content-Type'].startswith('text/markdown')
        assert response['Content-Disposition'] == 'attachment; filename="my-great-note.md"'
        assert response['X-Content-Type-Options'] == 'nosniff'
        assert response.content == b'# hi\n'
        symbols = create_note(user, title='???')
        assert 'filename="note.md"' in web.get(url('download', symbols.pk))['Content-Disposition']

    def test_dates_use_the_local_time_tag(self, web, user):
        note = create_note(user, title='A')
        assert 'data-local-time' in content(web.get(url('note', note.pk)))


class TestChecklist:
    def test_ticks_a_box_and_answers_the_body(self, web, user):
        note = create_note(user, title='A', content='- [ ] one\n- [ ] two\n')
        response = web.post(url('checklist', note.pk), {'index': 1, 'checked': 'true'}, **HTMX)
        assert response.status_code == 200
        assert 'id="note-body"' in content(response) and 'checked' in content(response)
        assert Note.objects.get(pk=note.pk).content == '- [ ] one\n- [x] two\n'

    def test_unticks(self, web, user):
        note = create_note(user, title='A', content='- [x] one\n')
        web.post(url('checklist', note.pk), {'index': 0}, **HTMX)
        assert Note.objects.get(pk=note.pk).content == '- [ ] one\n'

    def test_refusals(self, web, user):
        note = create_note(user, title='A', content='- [ ] one\n')
        bad = web.post(url('checklist', note.pk), {'index': 7, 'checked': 'true'}, **HTMX)
        assert bad.status_code == 422 and bad['HX-Retarget'] == '#note-msg'
        assert 'That checklist item no longer exists.' in content(bad)
        assert web.post(url('checklist', note.pk), {'index': 'x'}, **HTMX).status_code == 422
        trash(note)
        gone = web.post(url('checklist', note.pk), {'index': 0, 'checked': 'true'}, **HTMX)
        assert gone.status_code == 422 and 'This note is in the Trash.' in content(gone)

    def test_without_htmx_it_redirects(self, web, user):
        note = create_note(user, title='A', content='- [ ] one\n')
        response = web.post(url('checklist', note.pk), {'index': 0, 'checked': 'true'})
        assert response.status_code == 302 and response['Location'] == url('note', note.pk)


class TestTags:
    def test_set_tags_answers_the_chips(self, web, user):
        one, two = create_tag(user, 'One'), create_tag(user, 'Two')
        note = create_note(user, title='A')
        response = web.post(url('tags-set', note.pk), {'tags': [str(one.pk), str(two.pk)]}, **HTMX)
        assert response.status_code == 200 and 'id="note-tag-chips"' in content(response)
        assert set(note.tags.all()) == {one, two}
        web.post(url('tags-set', note.pk), {}, **HTMX)
        assert note.tags.count() == 0

    def test_foreign_tag_is_refused(self, web, user, other_user):
        theirs = make_tag(other_user, 'Theirs')
        note = create_note(user, title='A')
        response = web.post(url('tags-set', note.pk), {'tags': [str(theirs.pk)]}, **HTMX)
        assert response.status_code == 422 and 'Choose your own tags.' in content(response)

    def test_tags_do_not_move_the_updated_time(self, web, user):
        tag = create_tag(user, 'One')
        note = create_note(user, title='A')
        before = Note.objects.get(pk=note.pk).updated_at
        web.post(url('tags-set', note.pk), {'tags': [str(tag.pk)]}, **HTMX)
        assert Note.objects.get(pk=note.pk).updated_at == before


class TestEditorCreate:
    def test_the_empty_editor(self, web):
        response = web.get(url('new'))
        text = content(response)
        assert response.status_code == 200
        assert url('preview') in text and 'name="base_version"' in text
        assert 'hx-trigger="input changed delay:400ms, load"' in text
        assert 'id="note-preview"' in text and 'role="tablist"' in text

    def test_creates_a_note(self, web, user):
        response = web.post(url('new'), {'title': 'Hello', 'content': 'a\r\nb'})
        note = Note.objects.get(user=user)
        assert response.status_code == 302 and response['Location'] == url('note', note.pk)
        assert (note.title, note.content, note.is_free) == ('Hello', 'a\nb', True)

    def test_htmx_gets_an_hx_redirect_and_a_flash(self, web, user):
        response = web.post(url('new'), {'title': 'Hello'}, **HTMX)
        note = Note.objects.get(user=user)
        assert response.status_code == 200 and response['HX-Redirect'] == url('note', note.pk)
        page = web.get(response['HX-Redirect'])
        assert 'Note created.' in content(page)

    def test_creates_with_tags(self, web, user):
        tag = create_tag(user, 'Work')
        web.post(url('new'), {'title': 'T', 'tags': [str(tag.pk)]})
        assert list(Note.objects.get(user=user).tags.all()) == [tag]

    def test_title_errors(self, web, user):
        response = web.post(url('new'), {'title': '  ', 'content': 'x'}, **HTMX)
        assert response.status_code == 422 and response['HX-Retarget'] == '#editor-msg'
        assert 'Title: Give the note a title.' in content(response)
        long = web.post(url('new'), {'title': 'x' * 201}, **HTMX)
        assert 'Title: Keep the title to 200 characters or fewer.' in content(long)
        big = web.post(url('new'), {'title': 'ok', 'content': 'x' * 50_001}, **HTMX)
        assert 'Content: Keep the note to 50,000 characters or fewer.' in content(big)
        assert not Note.objects.filter(user=user).exists()

    def test_without_htmx_the_editor_comes_back_with_what_was_typed(self, web):
        response = web.post(url('new'), {'title': '', 'content': 'my text'})
        assert response.status_code == 422
        assert 'my text' in content(response) and 'Give the note a title.' in content(response)

    def test_the_cap_is_a_refusal_not_a_cost_question(self, web, user, config):
        config.max_notes_per_user = 1
        config.free_notes = 0
        config.save()
        make_note(user)
        response = web.post(url('new'), {'title': 'B'}, **HTMX)
        assert response.status_code == 422
        assert 'You have too many notes. Delete some first.' in content(response)
        assert 'costs' not in content(response)

    def test_a_foreign_tag_is_refused(self, web, user, other_user):
        theirs = make_tag(other_user, 'Theirs')
        response = web.post(url('new'), {'title': 'T', 'tags': [str(theirs.pk)]}, **HTMX)
        assert response.status_code == 422 and 'Choose your own tags.' in content(response)


class TestCostConfirmation:
    @pytest.fixture(autouse=True)
    def _full_slots(self, config):
        config.free_notes = 0
        config.save()

    def test_asks_first_and_charges_nothing(self, web, user):
        before = get_balance(user)
        response = web.post(url('new'), {'title': 'Paid'}, **HTMX)
        text = content(response)
        assert response.status_code == 422
        assert response['HX-Retarget'] == '#cost-confirm'
        assert 'costs <strong>3 credits</strong>' in text
        assert 'then 5 credits every 365 days' in text
        assert 'name="confirm_cost" value="3"' in text
        assert not Note.objects.filter(user=user).exists() and get_balance(user) == before

    def test_the_confirmed_amount_creates_and_charges(self, web, user):
        before = get_balance(user)
        response = web.post(url('new'), {'title': 'Paid', 'confirm_cost': '3'}, **HTMX)
        assert response.status_code == 200 and 'HX-Redirect' in response
        note = Note.objects.get(user=user)
        assert not note.is_free and note.charged_credits == 3
        assert get_balance(user) == before - 3

    def test_the_wrong_amount_asks_again(self, web, user):
        response = web.post(url('new'), {'title': 'Paid', 'confirm_cost': '1'}, **HTMX)
        assert response.status_code == 422 and response['HX-Retarget'] == '#cost-confirm'
        assert not Note.objects.filter(user=user).exists()

    def test_the_cost_changing_between_the_ask_and_the_charge_asks_again(
        self, web, user, config, monkeypatch
    ):
        # The page quoted 3; by the time the account is locked a price change makes it 4.
        from ..services import notes as notes_service

        real = notes_service.lock_account

        def raise_the_price(user_id):
            real(user_id)
            config.creation_price = 4
            config.save()

        monkeypatch.setattr(notes_service, 'lock_account', raise_the_price)
        response = web.post(url('new'), {'title': 'Paid', 'confirm_cost': '3'}, **HTMX)
        assert response.status_code == 422 and 'costs <strong>4 credits</strong>' in content(
            response
        )
        assert not Note.objects.filter(user=user).exists()

    def test_not_enough_credits(self, user, config):
        poor = make_user()
        client = Client()
        client.force_login(poor)
        response = client.post(url('new'), {'title': 'Paid', 'confirm_cost': '3'}, **HTMX)
        assert response.status_code == 422
        assert "You don't have enough credits for this note." in html.unescape(content(response))
        assert 'Top up your credits' in content(response)
        assert not Note.objects.filter(user=poor).exists()

    def test_the_panel_links_to_top_up_when_short(self, user, config):
        poor = make_user()
        client = Client()
        client.force_login(poor)
        response = client.post(url('new'), {'title': 'Paid'}, **HTMX)
        assert 'Top up your credits' in content(response)

    def test_the_title_is_checked_before_asking_for_credits(self, web):
        response = web.post(url('new'), {'title': ''}, **HTMX)
        assert response['HX-Retarget'] == '#editor-msg'


class TestEditorEdit:
    def test_the_editor_is_prefilled_with_the_version(self, web, user):
        note = create_note(user, title='Plan', content='line one')
        text = content(web.get(url('edit', note.pk)))
        assert 'value="Plan"' in text and 'line one' in text
        assert 'name="base_version" value="1"' in text

    def test_saving(self, web, user):
        note = create_note(user, title='Plan', content='one')
        response = web.post(
            url('edit', note.pk),
            {'title': 'Plan B', 'content': 'two', 'base_version': '1'},
            **HTMX,
        )
        assert response.status_code == 200 and response['HX-Redirect'] == url('note', note.pk)
        saved = Note.objects.get(pk=note.pk)
        assert (saved.title, saved.content, saved.version) == ('Plan B', 'two', 2)
        assert 'Saved.' in content(web.get(url('note', note.pk)))

    def test_a_note_in_the_trash_or_locked_has_no_editor(self, web, user):
        note = create_note(user, title='A')
        Note.objects.filter(pk=note.pk).update(locked_at=timezone.now())
        response = web.get(url('edit', note.pk), follow=True)
        assert response.redirect_chain[-1][0] == url('note', note.pk)
        assert 'This note is locked until its renewal is paid.' in content(response)
        Note.objects.filter(pk=note.pk).update(locked_at=None, deleted_at=timezone.now())
        assert 'This note is in the Trash.' in content(web.get(url('edit', note.pk), follow=True))
        refused = web.post(
            url('edit', note.pk), {'title': 'x', 'content': '', 'base_version': '1'}, **HTMX
        )
        assert refused.status_code == 422 and 'This note is in the Trash.' in content(refused)

    def test_a_missing_or_unreadable_version(self, web, user):
        note = create_note(user, title='A')
        response = web.post(url('edit', note.pk), {'title': 'B', 'content': ''}, **HTMX)
        assert response.status_code == 422 and 'Version' in content(response)
        bad = web.post(url('edit', note.pk), {'title': 'B', 'base_version': 'x'}, **HTMX)
        assert bad.status_code == 422

    def test_validation_errors_keep_the_editor(self, web, user):
        note = create_note(user, title='A')
        response = web.post(
            url('edit', note.pk), {'title': '', 'content': 'c', 'base_version': '1'}, **HTMX
        )
        assert response.status_code == 422 and response['HX-Retarget'] == '#editor-msg'
        assert Note.objects.get(pk=note.pk).version == 1


class TestConflictPanel:
    def _conflict(self, web, user, **post):
        note = create_note(user, title='Shared', content='base')
        save_note(note, editor=Editor.for_user(note, None), content='theirs', base_version=1)
        data = {'title': 'Shared', 'content': 'my version', 'base_version': '1', **post}
        return note, web.post(url('edit', note.pk), data, **HTMX)

    def test_the_panel_keeps_my_text_and_shows_the_current_one(self, web, user):
        note, response = self._conflict(web, user)
        text = content(response)
        assert response.status_code == 422
        assert response['HX-Retarget'] == '#editor-form' and response['HX-Reswap'] == 'outerHTML'
        assert 'This note was changed by someone else while you were editing.' in text
        assert 'my version' in text and 'theirs' in text
        assert 'Current version' in text and 'Copy current version' in text
        assert 'Save mine anyway' in text and 'Discard my changes' in text
        # The form now carries the current version, so "Save mine anyway" is a plain re-post.
        assert 'name="base_version" value="2"' in text
        assert 'name="overwrite" value="1"' in text
        assert Note.objects.get(pk=note.pk).content == 'theirs'

    def test_save_mine_anyway_wins(self, web, user):
        note, _ = self._conflict(web, user)
        response = web.post(
            url('edit', note.pk),
            {'title': 'Shared', 'content': 'my version', 'base_version': '2', 'overwrite': '1'},
            **HTMX,
        )
        assert response.status_code == 200 and 'HX-Redirect' in response
        saved = Note.objects.get(pk=note.pk)
        assert (saved.content, saved.version) == ('my version', 3)

    def test_the_title_is_shown_when_it_differs(self, web, user):
        note = create_note(user, title='Their title', content='base')
        save_note(note, editor=Editor.for_user(note, None), content='x', base_version=1)
        response = web.post(
            url('edit', note.pk), {'title': 'My title', 'content': 'y', 'base_version': '1'}, **HTMX
        )
        assert '(title: Their title)' in content(response)

    def test_without_htmx_the_whole_editor_comes_back(self, web, user):
        note = create_note(user, title='Shared', content='base')
        save_note(note, editor=Editor.for_user(note, None), content='theirs', base_version=1)
        response = web.post(
            url('edit', note.pk), {'title': 'Shared', 'content': 'mine', 'base_version': '1'}
        )
        assert response.status_code == 422 and '<html' in content(response)
        assert 'Save mine anyway' in content(response)


class TestPreview:
    def test_renders_the_extended_profile(self, web):
        response = web.post(
            url('preview'), {'content': '```py\nx = 1\n```\n\n| a |\n|---|\n| 1 |\n'}, **HTMX
        )
        text = content(response)
        assert response.status_code == 200
        assert 'class="hl"' in text and '<table>' in text

    def test_empty_and_unsafe(self, web):
        assert 'Nothing to preview yet.' in content(web.post(url('preview'), {'content': '  '}))
        text = content(web.post(url('preview'), {'content': '<script>alert(1)</script>'}))
        assert '<script>' not in text

    def test_login_is_required_and_get_is_refused(self, web):
        assert Client().post(url('preview'), {'content': 'x'}).status_code == 302
        assert web.get(url('preview')).status_code == 405

    def test_too_long_is_shown_unrendered(self, web):
        response = web.post(url('preview'), {'content': '# x\n' + 'y' * 50_001})
        assert response.status_code == 200 and '<h1>' not in content(response)


class TestActions:
    def test_archive_unarchive_and_trash_flash_and_go_back(self, web, user):
        note = create_note(user, title='A')
        response = web.post(url('archive', note.pk), {'next': url('list')}, follow=True)
        assert 'Note archived.' in content(response)
        assert Note.objects.get(pk=note.pk).is_archived
        response = web.post(url('unarchive', note.pk), {'next': url('archived')}, follow=True)
        assert 'Note moved back to your notes.' in content(response)
        response = web.post(url('trash-note', note.pk), {'next': url('list')}, follow=True)
        assert 'Note moved to the Trash.' in content(response)
        assert Note.objects.get(pk=note.pk).is_trashed

    def test_next_must_be_on_this_site(self, web, user):
        note = create_note(user, title='A')
        response = web.post(url('archive', note.pk), {'next': 'https://evil.example/'})
        assert response['Location'] == url('note', note.pk)

    def test_archiving_a_locked_note_flashes_the_refusal(self, web, user):
        note = create_note(user, title='A')
        Note.objects.filter(pk=note.pk).update(locked_at=timezone.now())
        response = web.post(url('archive', note.pk), follow=True)
        assert 'This note is locked until its renewal is paid.' in content(response)
        assert not Note.objects.get(pk=note.pk).is_archived

    def test_a_trashed_note_cannot_be_archived_or_duplicated(self, web, user):
        note = create_note(user, title='A')
        trash(note)
        for name in ('archive', 'unarchive', 'trash-note', 'duplicate'):
            assert web.post(url(name, note.pk)).status_code == 404

    def test_restore(self, web, user):
        note = create_note(user, title='A')
        trash(note)
        response = web.post(url('restore', note.pk), follow=True)
        assert response.redirect_chain[-1][0] == url('note', note.pk)
        assert 'Note restored.' in content(response)
        assert not Note.objects.get(pk=note.pk).is_trashed

    def test_restore_that_costs_needs_the_confirmed_amount(self, web, user, config):
        note = create_note(user, title='A')
        trash(note)
        config.free_notes = 0
        config.save()
        before = get_balance(user)
        response = web.post(url('restore', note.pk), follow=True)
        assert 'Restoring this note costs 3 credits.' in content(response)
        assert Note.objects.get(pk=note.pk).is_trashed and get_balance(user) == before
        response = web.post(url('restore', note.pk), {'confirm_cost': '3'}, follow=True)
        assert 'Note restored.' in content(response)
        restored = Note.objects.get(pk=note.pk)
        assert not restored.is_free and get_balance(user) == before - 3

    def test_restore_confirming_a_cost_that_is_no_longer_asked_charges_nothing(
        self, web, user, config
    ):
        note = create_note(user, title='A')
        trash(note)
        response = web.post(url('restore', note.pk), {'confirm_cost': '9'}, follow=True)
        # A slot is open, so it is free: the stale amount is not a reason to refuse or to charge.
        assert 'Note restored.' in content(response)
        assert get_balance(user) == 100

    def test_restore_when_the_cost_changed_in_between(self, web, user, config, monkeypatch):
        from ..services import notes as notes_service

        note = create_note(user, title='A')
        trash(note)
        config.free_notes = 0
        config.save()
        real = notes_service.lock_account

        def raise_the_price(user_id):
            real(user_id)
            config.creation_price = 4
            config.save()

        monkeypatch.setattr(notes_service, 'lock_account', raise_the_price)
        response = web.post(url('restore', note.pk), {'confirm_cost': '3'}, follow=True)
        assert 'Restoring this note now costs 4 credits.' in content(response)
        assert Note.objects.get(pk=note.pk).is_trashed and get_balance(user) == 100

    def test_restore_without_enough_credits(self, other_user, config):
        client = Client()
        client.force_login(other_user)
        note = create_note(other_user, title='A')
        trash(note)
        config.free_notes = 0
        config.save()
        response = client.post(url('restore', note.pk), {'confirm_cost': '3'}, follow=True)
        assert "You don't have enough credits to restore this note." in html.unescape(
            content(response)
        )
        assert Note.objects.get(pk=note.pk).is_trashed

    def test_delete_forever(self, web, user):
        note = create_note(user, title='A')
        assert web.post(url('delete', note.pk)).status_code == 404  # not in the Trash
        trash(note)
        response = web.post(url('delete', note.pk), {'next': url('note', note.pk)}, follow=True)
        assert 'Note deleted for good.' in content(response)
        assert response.redirect_chain[-1][0] == url('trash')
        assert not Note.objects.filter(pk=note.pk).exists()

    def test_duplicate(self, web, user):
        tag = create_tag(user, 'Work')
        note = create_note(user, title='Recipe', content='- [x] eggs', tags=[tag.pk])
        response = web.post(url('duplicate', note.pk), follow=True)
        copy = Note.objects.exclude(pk=note.pk).get(user=user)
        assert response.redirect_chain[-1][0] == url('note', copy.pk)
        assert 'Note duplicated.' in content(response)
        assert (copy.title, copy.content, copy.tags.count()) == ('Recipe (copy)', '- [x] eggs', 0)

    def test_duplicate_that_costs_is_confirmed_first(self, web, user, config):
        note = create_note(user, title='A')
        config.free_notes = 0
        config.save()
        response = web.post(url('duplicate', note.pk), follow=True)
        assert 'Duplicating this note costs 3 credits.' in content(response)
        assert Note.objects.filter(user=user).count() == 1
        web.post(url('duplicate', note.pk), {'confirm_cost': '3'})
        assert Note.objects.filter(user=user).count() == 2
        assert get_balance(user) == 97

    def test_duplicate_without_enough_credits(self, other_user, config):
        client = Client()
        client.force_login(other_user)
        note = create_note(other_user, title='A')
        config.free_notes = 0
        config.save()
        response = client.post(url('duplicate', note.pk), {'confirm_cost': '3'}, follow=True)
        assert 'enough credits to duplicate this note' in content(response)
        assert Note.objects.filter(user=other_user).count() == 1

    def test_get_is_refused(self, web, user):
        note = create_note(user, title='A')
        for name in (
            'archive',
            'trash-note',
            'duplicate',
            'restore',
            'delete',
            'checklist',
            'tags-set',
        ):
            assert web.get(url(name, note.pk)).status_code == 405


class TestReorder:
    def test_posts_the_order(self, web, user):
        a = create_note(user, title='A')
        b = create_note(user, title='B')
        c = create_note(user, title='C')
        response = web.post(url('reorder'), {'ids[]': [str(a.pk), str(c.pk), str(b.pk)]}, **HTMX)
        assert response.status_code == 204
        assert titles(web.get(url('list'))) == ['A', 'C', 'B']

    def test_junk_ids_are_refused(self, web):
        response = web.post(url('reorder'), {'ids[]': ['nope']}, **HTMX)
        assert response.status_code == 422 and 'Could not read the new order.' in content(response)

    def test_too_many_ids(self, web):
        response = web.post(
            url('reorder'), {'ids[]': ['00000000-0000-0000-0000-000000000000'] * 501}, **HTMX
        )
        assert response.status_code == 422

    def test_other_users_notes_are_ignored(self, web, user, other_user):
        theirs = make_note(other_user, position=-9)
        mine = create_note(user, title='A')
        web.post(url('reorder'), {'ids[]': [str(theirs.pk), str(mine.pk)]}, **HTMX)
        assert Note.objects.get(pk=theirs.pk).position == -9


class TestHistory:
    def test_lists_versions_newest_first_with_who(self, web, user, other_user):
        note = create_note(user, title='A', content='one')
        NoteVersion.objects.filter(note=note).update(updated_at=timezone.now() - timedelta(hours=1))
        save_note(note, editor=Editor.for_user(note, other_user), content='two', base_version=1)
        save_note(note, editor=Editor.for_user(note, None), content='three', base_version=2)
        text = content(web.get(url('history', note.pk)))
        assert text.index('Version 3') < text.index('Version 2') < text.index('Version 1')
        assert 'by You' in text and 'by Other User' in text and 'by Someone' in text
        assert text.count('Current') == 1
        newest = note.versions.get(number=3)
        assert url('history-restore', note.pk, newest.pk) not in text
        assert url('history-restore', note.pk, note.versions.get(number=1).pk) in text

    def test_one_version_rendered(self, web, user):
        note = create_note(user, title='A', content='# Old heading\n')
        version = note.versions.get()
        text = content(web.get(url('history-version', note.pk, version.pk)))
        assert '<h1>Old heading</h1>' in text

    def test_a_version_of_another_note_is_a_404(self, web, user):
        one, two = create_note(user, title='A'), create_note(user, title='B')
        version = two.versions.get()
        assert web.get(url('history-version', one.pk, version.pk)).status_code == 404
        assert web.post(url('history-restore', one.pk, version.pk)).status_code == 404

    def test_restore_a_version(self, web, user):
        note = create_note(user, title='First', content='one')
        NoteVersion.objects.filter(note=note).update(updated_at=timezone.now() - timedelta(hours=1))
        save_note(
            note, editor=Editor.for_user(note, user), title='Second', content='two', base_version=1
        )
        old = note.versions.get(number=1)
        response = web.post(url('history-restore', note.pk, old.pk), follow=True)
        assert 'Restored version 1.' in content(response)
        restored = Note.objects.get(pk=note.pk)
        assert (restored.title, restored.content, restored.version) == ('First', 'one', 3)

    def test_restore_on_a_locked_note_is_refused(self, web, user):
        note = create_note(user, title='A', content='one')
        old = note.versions.get()
        Note.objects.filter(pk=note.pk).update(locked_at=timezone.now())
        response = web.post(url('history-restore', note.pk, old.pk), follow=True)
        assert 'This note is locked until its renewal is paid.' in content(response)
        page = web.get(url('history', note.pk))
        assert url('history-restore', note.pk, old.pk) not in content(page)


class TestTagsPage:
    def test_the_page_lists_tags_with_counts(self, web, user):
        tag = create_tag(user, 'Work', description='Office things')
        create_note(user, title='A', tags=[tag.pk])
        text = content(web.get(url('tags')))
        assert 'Work' in text and '1 note' in text and 'Office things' in text

    def test_add_edit_delete(self, web, user):
        response = web.post(url('tags'), {'name': 'Home', 'color': '#336699'}, **HTMX)
        assert response.status_code == 200 and 'Home' in content(response)
        tag = Tag.objects.get(user=user)
        assert tag.color == '#336699'
        renamed = web.post(url('tag-edit', tag.pk), {'name': 'House'}, **HTMX)
        assert renamed.status_code == 200
        tag.refresh_from_db()
        assert (tag.name, tag.color) == ('House', '#336699')
        gone = web.post(url('tag-delete', tag.pk), **HTMX)
        assert gone['HX-Redirect'] == url('tags')
        assert not Tag.objects.filter(user=user).exists()
        assert 'Deleted the tag House.' in content(web.get(url('tags')))

    def test_refusals(self, web, user):
        create_tag(user, 'Work')
        dup = web.post(url('tags'), {'name': 'work'}, **HTMX)
        assert dup.status_code == 422 and dup['HX-Retarget'] == '#tag-msg'
        assert 'You already have a tag with this name.' in content(dup)
        blank = web.post(url('tags'), {'name': ''}, **HTMX)
        assert blank.status_code == 422 and 'Name is required.' in content(blank)
        colour = web.post(url('tags'), {'name': 'x', 'color': 'red'}, **HTMX)
        assert colour.status_code == 422

    def test_another_users_tag_is_a_404(self, web, other_user):
        theirs = make_tag(other_user)
        assert web.post(url('tag-edit', theirs.pk), {'name': 'x'}).status_code == 404
        assert web.post(url('tag-delete', theirs.pk)).status_code == 404

    def test_reorder(self, web, user):
        a, b = create_tag(user, 'a'), create_tag(user, 'b')
        response = web.post(url('tags-reorder'), {'ids[]': [str(b.pk), str(a.pk)]}, **HTMX)
        assert response.status_code == 204
        assert [t.name for t in Tag.objects.filter(user=user)] == ['b', 'a']
        assert web.post(url('tags-reorder'), {'ids[]': ['x']}, **HTMX).status_code == 422


class TestRateLimits:
    @pytest.fixture(autouse=True)
    def _limits(self, settings, ratelimit_clock):
        settings.RATELIMIT_ENABLE = True
        settings.RATELIMIT_RULES = {
            **settings.RATELIMIT_RULES,
            'NOTES_CREATE_USER': (2, 60),
            'NOTES_EDIT_USER': (2, 60),
            'NOTES_PREVIEW_USER': (2, 60),
        }

    def test_creating_is_limited(self, web):
        for number in range(2):
            assert web.post(url('new'), {'title': f'T{number}'}, **HTMX).status_code == 200
        response = web.post(url('new'), {'title': 'T3'}, **HTMX)
        assert response.status_code == 429 and response['Retry-After'] == '20'
        assert response['HX-Retarget'] == '#editor-msg'

    def test_a_refused_form_spends_nothing(self, web):
        for _ in range(5):
            assert web.post(url('new'), {'title': ''}, **HTMX).status_code == 422
        assert web.post(url('new'), {'title': 'ok'}, **HTMX).status_code == 200

    def test_saving_is_limited(self, web, user):
        note = create_note(user, title='A')
        for number in range(2):
            ok = web.post(
                url('edit', note.pk),
                {'title': 'A', 'content': str(number), 'base_version': str(number + 1)},
                **HTMX,
            )
            assert ok.status_code == 200
        response = web.post(
            url('edit', note.pk), {'title': 'A', 'content': 'z', 'base_version': '3'}, **HTMX
        )
        assert response.status_code == 429

    def test_the_preview_answers_204_over_the_limit(self, web):
        assert web.post(url('preview'), {'content': 'a'}).status_code == 200
        assert web.post(url('preview'), {'content': 'a'}).status_code == 200
        assert web.post(url('preview'), {'content': 'a'}).status_code == 204

    def test_actions_spend_the_edit_budget(self, web, user):
        note = create_note(user, title='A')
        web.post(url('archive', note.pk))
        web.post(url('unarchive', note.pk))
        assert web.post(url('archive', note.pk)).status_code == 429

    def test_a_refused_checklist_tick_spends_nothing(self, web, user):
        note = create_note(user, title='A', content='- [ ] one\n')
        for _ in range(5):
            bad = web.post(url('checklist', note.pk), {'index': 9, 'checked': 'true'}, **HTMX)
            assert bad.status_code == 422
        ok = web.post(url('checklist', note.pk), {'index': 0, 'checked': 'true'}, **HTMX)
        assert ok.status_code == 200

    def test_a_refused_tag_change_spends_nothing(self, web, user, other_user):
        theirs = make_tag(other_user, 'Theirs')
        note = create_note(user, title='A')
        for _ in range(5):
            bad = web.post(url('tags-set', note.pk), {'tags': [str(theirs.pk)]}, **HTMX)
            assert bad.status_code == 422
        assert web.post(url('tags-set', note.pk), {}, **HTMX).status_code == 200

    def test_a_refused_archive_spends_nothing(self, web, user):
        locked = make_note(user, title='L', locked_at=timezone.now(), is_free=False)
        for _ in range(5):
            assert web.post(url('archive', locked.pk)).status_code == 302
        note = create_note(user, title='A')
        assert web.post(url('archive', note.pk)).status_code == 302
        assert Note.objects.get(pk=note.pk).archived_at is not None

    def test_an_unreadable_reorder_spends_nothing(self, web, user):
        for _ in range(5):
            assert web.post(url('reorder'), {'ids[]': ['nope']}, **HTMX).status_code == 422
        assert web.post(url('reorder'), {'ids[]': []}, **HTMX).status_code == 204

    def test_reorder_and_tags_are_limited(self, web, user):
        for _ in range(2):
            assert web.post(url('reorder'), {'ids[]': []}, **HTMX).status_code == 204
        assert web.post(url('reorder'), {'ids[]': []}, **HTMX).status_code == 429
