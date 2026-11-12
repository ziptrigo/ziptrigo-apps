"""The shared note pages `/n/<code>`: what a visitor sees, the headers, every unavailable case
rendering the same page, raw and download, editing through an editable link (content only,
attribution, conflicts, the owner's coalesced notice), the checklist, the preview, the rate limits
and Duplicate for a signed-in visitor."""

from datetime import timedelta

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.billing.services import add_credits, get_balance
from apps.core.models import Notification

from ..models import Note, NoteVersion, ShareLink
from ..services import create_note, create_tag, links, restore_version, takedown, trash
from .factories import make_note, make_user

pytestmark = pytest.mark.django_db

HTMX = {'HTTP_HX_REQUEST': 'true'}
CONTENT = '# Shopping\n\n```python\nx = 1\n```\n\n| a | b |\n|---|---|\n| 1 | 2 |\n\n- [ ] eggs\n- [ ] milk\n'


def public(name: str, code: str) -> str:
    return reverse(f'n:{name}', args=[code])


def text(response) -> str:
    return response.content.decode()


@pytest.fixture
def note(funded_user):
    tag = create_tag(funded_user, 'Home', color='#336699')
    return create_note(funded_user, title='Shopping list', content=CONTENT, tags=[tag.pk])


@pytest.fixture
def read_link(note):
    return links.create_link(note, mode='read', label='for Ann')


@pytest.fixture
def edit_link(note):
    return links.create_link(note, mode='edit', label='team')


@pytest.fixture
def visitor():
    return Client()


def edit_post(visitor, link, note_version, content, **extra):
    return visitor.post(
        public('edit', link.code),
        {'content': content, 'base_version': str(note_version), **extra},
        **HTMX,
    )


class TestThePage:
    def test_it_shows_the_note_to_anyone_with_the_link(self, visitor, note, read_link):
        response = visitor.get(public('page', read_link.code))
        body = text(response)
        assert response.status_code == 200
        assert '<h1' in body and 'Shopping list' in body
        assert 'pre class="hl"' in body and 'class="md-table"' in body  # extended profile
        assert 'Home' in body and '#336699' in body  # the tag chip: name and colour
        assert 'Updated' in body

    def test_it_says_nothing_about_the_owner_or_the_link(self, visitor, funded_user, read_link):
        body = text(visitor.get(public('page', read_link.code)))
        assert funded_user.email not in body and funded_user.name not in body
        assert 'for Ann' not in body  # the label is the owner's own reminder

    def test_the_buttons_of_a_read_link(self, visitor, read_link):
        body = text(visitor.get(public('page', read_link.code)))
        for label in ('Copy note', 'Raw', 'Download', 'QR code', 'Duplicate', 'Report this note'):
            assert label in body
        assert f'href="{public("raw", read_link.code)}"' in body
        assert f'href="{public("download", read_link.code)}"' in body
        assert 'data:image/png;base64,' in body  # the QR code, rendered in memory
        assert public('edit', read_link.code) not in body
        assert 'data-checklist-url' not in body  # the boxes stay disabled

    def test_an_editable_link_adds_edit_and_enabled_checklist_boxes(self, visitor, edit_link):
        body = text(visitor.get(public('page', edit_link.code)))
        assert f'href="{public("edit", edit_link.code)}"' in body
        assert f'data-checklist-url="{public("checklist", edit_link.code)}"' in body

    def test_the_headers_make_the_code_a_secret(self, visitor, read_link):
        response = visitor.get(public('page', read_link.code))
        assert response['Cache-Control'] == 'no-store, private'
        assert response['Referrer-Policy'] == 'same-origin'
        assert response['X-Robots-Tag'] == 'noindex, nofollow'
        assert '<meta name="robots" content="noindex">' in text(response)

    def test_it_sets_the_csrf_cookie_for_a_visitor_without_a_session(self, visitor, read_link):
        response = visitor.get(public('page', read_link.code))
        assert 'csrftoken' in response.cookies

    def test_a_trailing_slash_is_accepted_and_the_address_has_none(self, visitor, read_link):
        assert visitor.get(f'/n/{read_link.code}/').status_code == 200
        assert reverse('n:page', args=[read_link.code]) == f'/n/{read_link.code}'
        assert links.link_path(read_link) == f'/n/{read_link.code}'

    def test_it_works_for_a_signed_in_visitor_and_the_owner(self, web, read_link):
        assert web.get(public('page', read_link.code)).status_code == 200

    def test_an_archived_note_keeps_working(self, visitor, note, read_link):
        from ..services import archive

        archive(note)
        assert visitor.get(public('page', read_link.code)).status_code == 200

    def test_an_over_long_note_is_shown_escaped_rather_than_failing(
        self, visitor, funded_user, read_link
    ):
        Note.objects.filter(pk=read_link.note_id).update(content='<b>' + 'x' * 50_001)
        response = visitor.get(public('page', read_link.code))
        assert response.status_code == 200 and '&lt;b&gt;' in text(response)

    def test_it_is_not_gated_by_the_terms_and_is_not_zoned(self, settings):
        assert '/n/' in settings.TIMEZONE_MIDDLEWARE_SKIP_PREFIXES


class TestEveryUnavailableCaseIsTheSame:
    """Unknown, revoked, expired, in the Trash, locked, taken down: one page, never saying why."""

    def make_cases(self, funded_user):
        def link_of(**note_fields):
            note = make_note(funded_user, title='A', content='secret')
            link = links.create_link(note, mode='read')
            Note.objects.filter(pk=note.pk).update(**note_fields)
            return link

        cases = {}
        cases['unknown'] = 'nope' * 5
        revoked = link_of()
        links.revoke_link(ShareLink.objects.select_related('note__user').get(pk=revoked.pk))
        cases['revoked'] = revoked.code
        expired = link_of()
        ShareLink.objects.filter(pk=expired.pk).update(expires_at=timezone.now() - timedelta(1))
        cases['expired'] = expired.code
        cases['trashed'] = link_of(deleted_at=timezone.now()).code
        cases['locked'] = link_of(locked_at=timezone.now(), is_free=False).code
        down = link_of()
        takedown.take_down_note(down.note, by=make_user(is_staff=True), reason='why')
        cases['taken_down'] = down.code
        cases['too_long'] = 'z' * 80
        return cases

    @pytest.mark.parametrize('name', ['page', 'raw', 'download'])
    def test_the_answers_are_byte_for_byte_identical(self, visitor, funded_user, name):
        cases = self.make_cases(funded_user)
        answers = {kind: visitor.get(public(name, code)) for kind, code in cases.items()}
        reference = answers['unknown']
        assert reference.status_code == 404
        for kind, response in answers.items():
            assert response.status_code == 404, kind
            assert response.content == reference.content, kind
            assert response['Cache-Control'] == 'no-store, private'
            assert response['Referrer-Policy'] == 'same-origin'
            assert response['X-Robots-Tag'] == 'noindex, nofollow'
        body = text(reference)
        assert "isn't available" in body and 'secret' not in body

    def test_a_read_link_asked_for_an_edit_page_is_the_same_page(self, visitor, read_link):
        unknown = visitor.get(public('page', 'nope' * 5))
        for method, name in (('get', 'edit'),):
            response = getattr(visitor, method)(public(name, read_link.code))
            assert response.status_code == 404 and response.content == unknown.content

    def test_the_posts_of_a_dead_link_are_unavailable_too(self, visitor, note, read_link):
        for name, data in (
            ('edit', {'content': 'x', 'base_version': '1'}),
            ('checklist', {'index': 0, 'checked': 'true'}),
            ('preview', {'content': 'x'}),
            ('report', {'reason': 'spam'}),
        ):
            first = visitor.post(public(name, 'nope' * 5), data)
            assert first.status_code == 404, name
            htmx = visitor.post(public(name, 'nope' * 5), data, **HTMX)
            assert htmx.status_code == 204 and htmx['HX-Refresh'] == 'true', name
        assert visitor.post(public('duplicate', 'nope' * 5)).status_code == 404

    def test_a_link_that_dies_stops_working_on_the_next_request(self, visitor, read_link):
        assert visitor.get(public('page', read_link.code)).status_code == 200
        links.revoke_link(ShareLink.objects.select_related('note__user').get(pk=read_link.pk))
        assert visitor.get(public('page', read_link.code)).status_code == 404


class TestRawAndDownload:
    def test_raw_is_the_markdown_as_plain_text(self, visitor, read_link):
        response = visitor.get(public('raw', read_link.code))
        assert response.status_code == 200
        assert response['Content-Type'] == 'text/plain; charset=utf-8'
        assert response['X-Content-Type-Options'] == 'nosniff'
        assert response.content.decode() == CONTENT
        assert response['Referrer-Policy'] == 'same-origin'

    def test_a_script_in_the_markdown_is_never_html(self, visitor, note, read_link):
        Note.objects.filter(pk=note.pk).update(content='<script>alert(1)</script>')
        response = visitor.get(public('raw', read_link.code))
        assert response['Content-Type'].startswith('text/plain')
        assert response['X-Content-Type-Options'] == 'nosniff'

    def test_download_is_an_attachment_named_after_the_title(self, visitor, read_link):
        response = visitor.get(public('download', read_link.code))
        assert response['Content-Disposition'] == 'attachment; filename="shopping-list.md"'
        assert response['Content-Type'].startswith('text/markdown')
        assert response.content.decode() == CONTENT

    def test_a_title_with_nothing_to_slugify_is_named_note(self, visitor, note, read_link):
        Note.objects.filter(pk=note.pk).update(title='???')
        response = visitor.get(public('download', read_link.code))
        assert 'filename="note.md"' in response['Content-Disposition']


class TestEditingThroughALink:
    def test_the_editor_shows_the_title_read_only(self, visitor, edit_link):
        response = visitor.get(public('edit', edit_link.code))
        body = text(response)
        assert response.status_code == 200
        assert 'name="title"' not in body and 'readonly' in body
        assert 'Shopping list' in body and "can only be changed by the note's owner" in body
        assert f'hx-post="{public("preview", edit_link.code)}"' in body
        assert response['X-Robots-Tag'] == 'noindex, nofollow' and 'csrftoken' in response.cookies

    def test_a_save_changes_the_content_only_and_ignores_a_posted_title(
        self, visitor, note, edit_link
    ):
        response = visitor.post(
            public('edit', edit_link.code),
            {'title': 'Hijacked', 'content': 'new text', 'base_version': str(note.version)},
            **HTMX,
        )
        assert response.status_code == 200 and response['HX-Redirect'] == public(
            'page', edit_link.code
        )
        fresh = Note.objects.get(pk=note.pk)
        assert (fresh.title, fresh.content, fresh.version) == ('Shopping list', 'new text', 2)
        assert ShareLink.objects.get(pk=edit_link.pk).last_edited_at is not None

    def test_the_flash_is_shown_to_an_anonymous_visitor_on_the_page(self, visitor, note, edit_link):
        edit_post(visitor, edit_link, note.version, 'again')
        assert 'Saved.' in text(visitor.get(public('page', edit_link.code)))

    def test_a_plain_post_redirects(self, visitor, note, edit_link):
        response = visitor.post(
            public('edit', edit_link.code),
            {'content': 'plain', 'base_version': str(note.version)},
        )
        assert response.status_code == 302 and response['Location'] == public(
            'page', edit_link.code
        )

    def test_a_read_link_cannot_edit(self, visitor, note, read_link):
        response = edit_post(visitor, read_link, note.version, 'nope')
        assert response.status_code == 204 and response['HX-Refresh'] == 'true'
        assert Note.objects.get(pk=note.pk).content == CONTENT

    def test_a_stale_version_is_the_conflict_panel_and_nothing_is_written(
        self, visitor, funded_user, note, edit_link
    ):
        edit_post(visitor, edit_link, note.version, 'first editor')
        response = edit_post(visitor, edit_link, note.version, 'my text')
        body = text(response)
        assert response.status_code == 422 and response['HX-Retarget'] == '#editor-form'
        assert 'changed by someone else' in body and 'first editor' in body and 'my text' in body
        assert 'name="base_version" value="2"' in body
        assert Note.objects.get(pk=note.pk).content == 'first editor'

    def test_save_mine_anyway_overwrites_and_keeps_the_text_it_replaced(
        self, visitor, note, edit_link
    ):
        edit_post(visitor, edit_link, note.version, 'first editor')
        response = edit_post(visitor, edit_link, 2, 'mine', overwrite='1')
        assert response.status_code == 200
        assert Note.objects.get(pk=note.pk).content == 'mine'
        assert 'first editor' in set(
            NoteVersion.objects.filter(note=note).values_list('content', flat=True)
        )

    def test_a_missing_version_is_a_refusal(self, visitor, edit_link):
        response = visitor.post(public('edit', edit_link.code), {'content': 'x'}, **HTMX)
        assert response.status_code == 422 and response['HX-Retarget'] == '#editor-msg'

    def test_a_content_over_the_limit_is_a_refusal_and_keeps_what_was_typed(
        self, visitor, note, edit_link
    ):
        response = visitor.post(
            public('edit', edit_link.code),
            {'content': 'x' * 50_001, 'base_version': str(note.version)},
        )
        assert response.status_code == 422
        assert 'Keep the note to 50,000 characters or fewer.' in text(response)

    def test_a_link_revoked_while_editing_is_unavailable_on_save(self, visitor, note, edit_link):
        links.revoke_link(ShareLink.objects.select_related('note__user').get(pk=edit_link.pk))
        response = edit_post(visitor, edit_link, note.version, 'late')
        assert response.status_code == 204 and response['HX-Refresh'] == 'true'
        assert Note.objects.get(pk=note.pk).content == CONTENT


class TestAttribution:
    def versions(self, note):
        return list(NoteVersion.objects.filter(note=note).order_by('number'))

    def test_an_anonymous_editor_is_recorded_as_anonymous_via_the_link(
        self, visitor, note, edit_link
    ):
        edit_post(visitor, edit_link, note.version, 'by anyone')
        last = self.versions(note)[-1]
        assert (last.editor_kind, last.editor_id, last.link_id) == ('anonymous', None, edit_link.pk)

    def test_a_signed_in_editor_is_recorded_with_their_account(self, note, edit_link):
        other = make_user(name='Pat Visitor')
        client = Client()
        client.force_login(other)
        edit_post(client, edit_link, note.version, 'by pat')
        last = self.versions(note)[-1]
        assert (last.editor_kind, last.editor_id, last.link_id) == ('user', other.pk, edit_link.pk)

    def test_the_owner_editing_through_their_own_link_is_the_owner(
        self, web, funded_user, note, edit_link
    ):
        edit_post(web, edit_link, note.version, 'by me')
        last = self.versions(note)[-1]
        assert (last.editor_kind, last.editor_id, last.link_id) == (
            'owner',
            funded_user.pk,
            edit_link.pk,
        )

    def test_the_owners_history_says_who_without_exposing_it_publicly(
        self, web, visitor, note, edit_link
    ):
        edit_post(visitor, edit_link, note.version, 'by someone')
        page = text(web.get(reverse('notes:history', args=[note.pk])))
        assert 'Someone via a shared link' in page and 'You' in page
        assert 'Someone' not in text(visitor.get(public('page', edit_link.code)))

    def test_two_anonymous_editors_on_different_links_never_merge(self, visitor, note, edit_link):
        second = links.create_link(note, mode='edit')
        edit_post(visitor, edit_link, 1, 'from one')
        edit_post(visitor, second, 2, 'from two')
        assert [v.content for v in self.versions(note)][-2:] == ['from one', 'from two']
        assert [v.link_id for v in self.versions(note)][-2:] == [edit_link.pk, second.pk]

    def test_one_editor_on_one_link_coalesces(self, visitor, note, edit_link):
        edit_post(visitor, edit_link, 1, 'one')
        edit_post(visitor, edit_link, 2, 'two')
        assert self.versions(note)[-1].content == 'two' and len(self.versions(note)) == 2

    def test_the_owner_can_restore_the_state_before_the_visitors_edit(
        self, funded_user, note, edit_link, visitor
    ):
        edit_post(visitor, edit_link, note.version, 'vandalised')
        original = self.versions(note)[0]
        restore_version(Note.objects.select_related('user').get(pk=note.pk), original)
        assert Note.objects.get(pk=note.pk).content == CONTENT


class TestTheOwnersNotice:
    def notices(self, user):
        return list(Notification.objects.filter(user=user, key__startswith='note_edited:'))

    def test_an_edit_by_someone_else_notifies_the_owner_once_however_many_edits(
        self, visitor, funded_user, note, edit_link
    ):
        edit_post(visitor, edit_link, 1, 'one')
        edit_post(visitor, edit_link, 2, 'two')
        visitor.post(public('checklist', edit_link.code), {'index': 0, 'checked': 'true'}, **HTMX)
        (notice,) = self.notices(funded_user)
        assert notice.title == 'Your note "Shopping list" was edited through a shared link'
        assert notice.url == reverse('notes:note', args=[note.pk])
        assert notice.key == f'note_edited:{note.pk}' and notice.source == 'notes'

    def test_the_owners_own_edit_through_their_link_makes_no_notice(
        self, web, funded_user, note, edit_link
    ):
        edit_post(web, edit_link, 1, 'mine')
        assert self.notices(funded_user) == []

    def test_a_long_title_is_cut_to_sixty_characters(self, visitor, funded_user, edit_link):
        Note.objects.filter(pk=edit_link.note_id).update(title='T' * 100)
        edit_post(visitor, edit_link, 1, 'x')
        (notice,) = self.notices(funded_user)
        assert notice.title == f'Your note "{"T" * 60}" was edited through a shared link'

    def test_a_save_that_changes_nothing_makes_no_notice(
        self, visitor, funded_user, note, edit_link
    ):
        edit_post(visitor, edit_link, 1, CONTENT)
        assert self.notices(funded_user) == []
        assert ShareLink.objects.get(pk=edit_link.pk).last_edited_at is None

    def test_no_email_is_sent(self, visitor, note, edit_link, mailoutbox):
        edit_post(visitor, edit_link, 1, 'one')
        assert mailoutbox == []


class TestTheChecklist:
    def test_an_editable_link_ticks_a_box_and_answers_the_body(self, visitor, note, edit_link):
        response = visitor.post(
            public('checklist', edit_link.code), {'index': 1, 'checked': 'true'}, **HTMX
        )
        assert response.status_code == 200
        assert f'data-checklist-url="{public("checklist", edit_link.code)}"' in text(response)
        assert '- [ ] eggs\n- [x] milk' in Note.objects.get(pk=note.pk).content
        link = ShareLink.objects.get(pk=edit_link.pk)
        assert link.last_edited_at is not None
        last = NoteVersion.objects.filter(note=note).order_by('-number').first()
        assert last is not None and last.editor_kind == 'anonymous' and last.link_id == link.pk

    def test_a_read_link_cannot_tick(self, visitor, note, read_link):
        response = visitor.post(
            public('checklist', read_link.code), {'index': 1, 'checked': 'true'}, **HTMX
        )
        assert response.status_code == 204 and response['HX-Refresh'] == 'true'
        assert '[x]' not in Note.objects.get(pk=note.pk).content

    def test_a_missing_item_is_a_refusal_into_the_note_slot(self, visitor, edit_link):
        response = visitor.post(
            public('checklist', edit_link.code), {'index': 9, 'checked': 'true'}, **HTMX
        )
        assert response.status_code == 422 and response['HX-Retarget'] == '#note-msg'
        assert 'That checklist item no longer exists.' in text(response)


class TestThePreview:
    def test_an_editable_link_renders_the_markdown(self, visitor, edit_link):
        response = visitor.post(public('preview', edit_link.code), {'content': '# Hi\n\n**bold**'})
        assert response.status_code == 200
        assert '<h1>Hi</h1>' in text(response) and '<strong>bold</strong>' in text(response)

    def test_a_script_is_not_rendered(self, visitor, edit_link):
        response = visitor.post(
            public('preview', edit_link.code), {'content': '<script>x</script>'}
        )
        assert '<script>' not in text(response)

    def test_a_read_link_has_no_preview(self, visitor, read_link):
        assert visitor.post(public('preview', read_link.code), {'content': 'x'}).status_code == 404


class TestRateLimits:
    @pytest.fixture(autouse=True)
    def _limits(self, settings, ratelimit_clock):
        settings.RATELIMIT_ENABLE = True
        settings.RATELIMIT_RULES = {
            **settings.RATELIMIT_RULES,
            'NOTES_VIEW_IP': (3, 60),
            'NOTES_LINK_EDIT_IP': (2, 600),
            'NOTES_LINK_EDIT_LINK': (3, 3600),
            'NOTES_LINK_PREVIEW_IP': (2, 60),
        }

    def test_views_are_limited_per_ip_before_the_lookup_over_it_a_plain_429(
        self, visitor, read_link
    ):
        # Made-up codes spend the same budget as a real one.
        for _ in range(2):
            assert visitor.get(public('page', 'nope' * 5)).status_code == 404
        assert visitor.get(public('page', read_link.code)).status_code == 200
        blocked = visitor.get(public('page', read_link.code))
        assert blocked.status_code == 429 and blocked['Retry-After'] == '20'
        assert blocked['Cache-Control'] == 'no-store, private'
        assert blocked['Referrer-Policy'] == 'same-origin'
        assert visitor.get(public('raw', read_link.code)).status_code == 429
        assert visitor.get(public('download', read_link.code)).status_code == 429
        assert visitor.get(public('edit', read_link.code)).status_code == 429

    def test_saves_are_limited_per_ip(self, visitor, note, edit_link):
        for number in range(2):
            ok = edit_post(visitor, edit_link, number + 1, f'v{number}')
            assert ok.status_code == 200
        response = edit_post(visitor, edit_link, 3, 'v3')
        assert response.status_code == 429 and response['HX-Retarget'] == '#editor-msg'
        assert Note.objects.get(pk=note.pk).content == 'v1'

    def test_ticks_share_the_edit_budget(self, visitor, edit_link):
        for index in range(2):
            assert (
                visitor.post(
                    public('checklist', edit_link.code), {'index': index, 'checked': 'true'}, **HTMX
                ).status_code
                == 200
            )
        response = visitor.post(
            public('checklist', edit_link.code), {'index': 0, 'checked': 'false'}, **HTMX
        )
        assert response.status_code == 429 and response['HX-Retarget'] == '#note-msg'

    def test_the_link_has_its_own_ceiling_across_every_ip(self, note, edit_link):
        for number in range(3):
            client = Client(REMOTE_ADDR=f'198.51.100.{number + 1}')
            assert edit_post(client, edit_link, number + 1, f'v{number}').status_code == 200
        other_ip = Client(REMOTE_ADDR='198.51.100.77')
        assert edit_post(other_ip, edit_link, 4, 'v4').status_code == 429

    def test_a_refused_form_spends_nothing(self, visitor, note, edit_link):
        for _ in range(5):
            bad = visitor.post(public('edit', edit_link.code), {'content': 'x'}, **HTMX)
            assert bad.status_code == 422
        assert edit_post(visitor, edit_link, 1, 'fine').status_code == 200

    def test_the_preview_answers_204_over_its_limit(self, visitor, edit_link):
        assert visitor.post(public('preview', edit_link.code), {'content': 'a'}).status_code == 200
        assert visitor.post(public('preview', edit_link.code), {'content': 'a'}).status_code == 200
        assert visitor.post(public('preview', edit_link.code), {'content': 'a'}).status_code == 204


class TestDuplicate:
    def test_without_a_session_it_goes_to_sign_in_and_back(self, visitor, note, read_link):
        response = visitor.post(public('duplicate', read_link.code))
        assert response.status_code == 302
        assert response['Location'] == f'/account/login/?next=/n/{read_link.code}'
        assert Note.objects.count() == 1

    def test_a_signed_in_visitor_gets_a_copy_with_the_title_and_content_and_no_tags(
        self, note, read_link, other_user
    ):
        visitor = Client()
        visitor.force_login(other_user)
        response = visitor.post(public('duplicate', read_link.code))
        copy = Note.objects.get(user=other_user)
        assert response.status_code == 302 and response['Location'] == reverse(
            'notes:note', args=[copy.pk]
        )
        assert (copy.title, copy.content, copy.is_free) == ('Shopping list', CONTENT, True)
        assert list(copy.tags.all()) == [] and copy.links.count() == 0
        assert copy.versions.count() == 1
        assert response['Cache-Control'] == 'no-store, private'

    def test_the_owner_may_copy_their_own_note_through_a_link(
        self, web, funded_user, note, read_link
    ):
        web.post(public('duplicate', read_link.code))
        assert Note.objects.filter(user=funded_user, title='Shopping list').count() == 2

    def test_a_copy_that_costs_credits_asks_first(self, note, read_link, other_user, config):
        config.free_notes = 0
        config.save()
        add_credits(other_user, 10, description='t', source='test')
        visitor = Client()
        visitor.force_login(other_user)
        page = text(visitor.get(public('page', read_link.code)))
        assert 'data-cost="3"' in page and 'Duplicate for 3 credits' in page
        response = visitor.post(public('duplicate', read_link.code), follow=True)
        assert 'Duplicating this note costs 3 credits.' in text(response)
        assert not Note.objects.filter(user=other_user).exists()
        assert get_balance(other_user) == 10
        visitor.post(public('duplicate', read_link.code), {'confirm_cost': '3'})
        copy = Note.objects.get(user=other_user)
        assert (copy.is_free, copy.charged_credits, get_balance(other_user)) == (False, 3, 7)

    def test_a_changed_cost_asks_again(self, note, read_link, other_user, config):
        config.free_notes = 0
        config.save()
        add_credits(other_user, 10, description='t', source='test')
        visitor = Client()
        visitor.force_login(other_user)
        response = visitor.post(
            public('duplicate', read_link.code), {'confirm_cost': '1'}, follow=True
        )
        assert 'Duplicating this note costs 3 credits.' in text(response)
        assert get_balance(other_user) == 10

    def test_too_few_credits_is_a_message_with_a_top_up_link(
        self, note, read_link, other_user, config
    ):
        config.free_notes = 0
        config.save()
        visitor = Client()
        visitor.force_login(other_user)
        response = visitor.post(
            public('duplicate', read_link.code), {'confirm_cost': '3'}, follow=True
        )
        assert 'You don&#x27;t have enough credits to duplicate this note.' in text(response)
        assert '/billing/credits/' in text(response)
        assert not Note.objects.filter(user=other_user).exists()

    def test_a_taken_down_note_cannot_be_copied(self, visitor, note, read_link, other_user):
        takedown.take_down_note(note, by=make_user(is_staff=True), reason='x')
        client = Client()
        client.force_login(other_user)
        assert client.post(public('duplicate', read_link.code)).status_code == 404
        assert not Note.objects.filter(user=other_user).exists()

    @pytest.mark.terms_gate
    def test_a_visitor_who_has_not_accepted_the_terms_is_sent_to_accept_them(
        self, note, read_link, other_user
    ):
        client = Client()
        client.force_login(other_user)
        # The shared pages themselves are never gated...
        assert client.get(public('page', read_link.code)).status_code == 200
        # ...but making a note in an account still needs the acceptance, like everywhere else.
        response = client.post(public('duplicate', read_link.code))
        assert response.status_code == 302 and response['Location'].startswith('/account/terms/')
        assert not Note.objects.filter(user=other_user).exists()

    def test_duplicates_are_limited_per_user(
        self, note, read_link, other_user, settings, ratelimit_clock
    ):
        settings.RATELIMIT_ENABLE = True
        settings.RATELIMIT_RULES = {**settings.RATELIMIT_RULES, 'NOTES_CREATE_USER': (1, 60)}
        client = Client()
        client.force_login(other_user)
        assert client.post(public('duplicate', read_link.code)).status_code == 302
        assert client.post(public('duplicate', read_link.code)).status_code == 429


def test_a_restored_note_works_for_its_links_again(visitor, note, read_link):
    from ..services import restore

    trash(note)
    assert visitor.get(public('page', read_link.code)).status_code == 404
    restore(note)
    assert visitor.get(public('page', read_link.code)).status_code == 200
