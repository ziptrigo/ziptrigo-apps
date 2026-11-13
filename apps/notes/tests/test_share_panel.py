"""The owner's share panel on the note page: opening it, creating, editing and revoking links,
refusals, ownership (404), rate limits, and the share icon on the list and the note page."""

import re
from datetime import timedelta

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from ..models import ShareLink
from ..services import create_note, links, restore_version, trash
from .factories import make_link

pytestmark = pytest.mark.django_db

HTMX = {'HTTP_HX_REQUEST': 'true'}


def url(name, *args):
    return reverse(f'notes:{name}', args=args)


def text(response) -> str:
    return response.content.decode()


@pytest.fixture
def note(funded_user):
    return create_note(funded_user, title='Plan', content='hello')


class TestOpening:
    def test_the_note_page_has_a_share_button_and_an_empty_panel_slot(self, web, note):
        body = text(web.get(url('note', note.pk)))
        assert f'hx-get="{url("share", note.pk)}"' in body and 'Share</a>' in body
        assert '<section id="share-panel"' in body and 'hx-trigger="load"' not in body

    def test_the_panel_is_an_htmx_fragment_listing_the_links(self, web, note):
        link = links.create_link(note, mode='edit', label='team')
        response = web.get(url('share', note.pk), **HTMX)
        body = text(response)
        assert response.status_code == 200 and 'Share this note' in body
        assert f'/n/{link.code}' in body and 'Editable' in body and 'team' in body
        assert 'data:image/png;base64,' in body  # the QR code of an active link
        assert 'Anyone with this link can change the note' in body  # the hint on the radio
        assert 'id="share-indicator"' in body and 'Shared' in body

    def test_opened_directly_it_goes_to_the_note_page_with_the_panel_open(self, web, note):
        response = web.get(url('share', note.pk))
        assert (
            response.status_code == 302
            and response['Location'] == f'{url("note", note.pk)}?share=1'
        )
        page = text(web.get(response['Location']))
        assert 'hx-trigger="load"' in page

    def test_active_links_come_first_and_inactive_ones_say_why(self, web, note):
        old = links.create_link(note, mode='read', label='Aaa-old')
        old.note = note
        links.revoke_link(old)
        make_link(note, label='Bbb-lapsed', expires_at=timezone.now() - timedelta(days=1))
        links.create_link(note, mode='read', label='Ccc-live')
        body = text(web.get(url('share', note.pk), **HTMX))
        order = [body.index(label) for label in ('Ccc-live', 'Aaa-old', 'Bbb-lapsed')]
        assert order[0] < min(order[1:])
        assert 'Revoked' in body and 'Expired' in body
        assert body.count('data-qr-image') == 1  # only the active link has a QR code

    def test_another_users_note_is_a_404(self, web, other_user, config):
        theirs = create_note(other_user, title='Theirs')
        assert web.get(url('share', theirs.pk), **HTMX).status_code == 404
        assert web.post(url('share-create', theirs.pk), {'mode': 'read'}, **HTMX).status_code == 404

    def test_login_is_required(self, note):
        client = Client()
        assert client.get(url('share', note.pk)).status_code == 302
        assert 'HX-Redirect' in client.get(url('share', note.pk), **HTMX)
        assert client.post(url('share-create', note.pk), {'mode': 'read'}).status_code == 302

    def test_a_locked_notes_panel_offers_no_form_only_revoke(self, web, note):
        link = links.create_link(note, mode='edit')
        type(note).objects.filter(pk=note.pk).update(locked_at=timezone.now())
        body = text(web.get(url('share', note.pk), **HTMX))
        assert "can't be shared right now" in body and 'share-form' not in body
        assert 'You can still revoke them.' in body
        assert url('share-revoke', note.pk, link.pk) in body
        assert url('share-edit', note.pk, link.pk) not in body
        assert 'data-qr-image' not in body and 'Copy link' not in body  # it doesn't work now
        assert 'fa-link text-[10px]' not in body  # no "Shared" chip

    def test_a_note_in_the_trash_has_a_panel_to_revoke_in(self, web, note):
        link = links.create_link(note, mode='edit')
        trash(note)
        page = text(web.get(url('note', note.pk)))
        assert f'hx-get="{url("share", note.pk)}"' in page  # the Share button is there
        assert 'fa-link text-[10px]' not in page  # but the links don't work: no "Shared" chip
        body = text(web.get(url('share', note.pk), **HTMX))
        assert 'share-form' not in body and url('share-revoke', note.pk, link.pk) in body

    def test_a_taken_down_notes_panel_says_its_links_are_dead_for_good(self, web, note):
        from ..services import takedown
        from .factories import make_user

        link = links.create_link(note, mode='read')
        takedown.take_down_note(note, by=make_user(is_staff=True), reason='x')
        body = text(web.get(url('share', note.pk), **HTMX))
        assert 'This note was taken down; its links no longer work.' in body
        assert "can't be shared right now" not in body
        assert url('share-revoke', note.pk, link.pk) in body

    def test_a_read_only_note_without_an_active_link_has_no_share_button(self, web, note):
        type(note).objects.filter(pk=note.pk).update(locked_at=timezone.now())
        assert f'hx-get="{url("share", note.pk)}"' not in text(web.get(url('note', note.pk)))


class TestRowMenuShare:
    """The list row's menu has Share, which opens the note page with its share panel open, shown
    when the note page shows its Share button."""

    def share_href(self, note):
        return f'href="{url("note", note.pk)}?share=1#share-panel"'

    def test_the_row_menu_has_share(self, web, note):
        body = text(web.get(url('list')))
        assert self.share_href(note) in body
        assert body.index('fa-pen') < body.index('fa-share-nodes') < body.index('fa-clone')

    def test_it_opens_the_note_page_with_the_panel_loading(self, web, note):
        page = text(web.get(f'{url("note", note.pk)}?share=1'))
        assert f'hx-get="{url("share", note.pk)}" hx-trigger="load"' in page

    def test_archived_and_search_rows_have_it_too(self, web, note):
        from ..services import archive

        other = create_note(note.user, title='Plan B', content='hello')
        archive(other)
        assert self.share_href(other) in text(web.get(url('archived')))
        assert self.share_href(note) in text(web.get(url('search') + '?q=plan'))

    def test_a_read_only_note_has_it_only_while_a_link_is_active(self, web, note):
        type(note).objects.filter(pk=note.pk).update(locked_at=timezone.now())
        assert self.share_href(note) not in text(web.get(url('list')))
        links_note = type(note).objects.get(pk=note.pk)
        make_link(links_note)
        assert self.share_href(note) in text(web.get(url('list')))

    def test_the_trash_has_no_menu(self, web, note):
        make_link(note)
        trash(note)
        assert self.share_href(note) not in text(web.get(url('trash')))


class TestCreating:
    def test_an_htmx_create_answers_the_panel(self, web, note):
        response = web.post(
            url('share-create', note.pk), {'mode': 'edit', 'label': ' For  Ann '}, **HTMX
        )
        link = ShareLink.objects.get(note=note)
        assert response.status_code == 200 and f'/n/{link.code}' in text(response)
        assert (link.mode, link.label) == ('edit', 'For Ann')

    def test_the_mode_defaults_to_read_only(self, web, note):
        web.post(url('share-create', note.pk), {}, **HTMX)
        assert ShareLink.objects.get(note=note).mode == 'read'

    def test_an_expiry_date_is_the_end_of_that_day(self, web, note):
        day = (timezone.now() + timedelta(days=4)).date()
        web.post(
            url('share-create', note.pk), {'mode': 'read', 'expires_on': day.isoformat()}, **HTMX
        )
        link = ShareLink.objects.get(note=note)
        assert link.expires_at is not None and link.expires_at.date() >= day

    def test_the_panel_shows_the_expiry_date_in_the_users_format(self, web, note):
        web.post(url('share-create', note.pk), {'mode': 'read', 'expires_on': '2099-03-04'}, **HTMX)
        body = text(web.get(url('share', note.pk), **HTMX))
        assert 'Expires 03/04/2099' in re.sub(r'\s+', ' ', body)

    def test_refusals_are_a_422_into_the_share_slot(self, web, note):
        past = web.post(
            url('share-create', note.pk), {'mode': 'read', 'expires_on': '2020-01-01'}, **HTMX
        )
        assert past.status_code == 422 and past['HX-Retarget'] == '#share-msg'
        assert 'Choose a date that has not passed.' in text(past)
        bad = web.post(url('share-create', note.pk), {'mode': 'read', 'expires_on': 'x'}, **HTMX)
        assert bad.status_code == 422 and 'Enter a date.' in text(bad)
        long = web.post(url('share-create', note.pk), {'mode': 'read', 'label': 'x' * 101}, **HTMX)
        assert long.status_code == 422
        assert ShareLink.objects.filter(note=note).count() == 0

    def test_the_cap_is_a_refusal(self, web, note, config):
        config.max_links_per_note = 1
        config.save()
        web.post(url('share-create', note.pk), {'mode': 'read'}, **HTMX)
        response = web.post(url('share-create', note.pk), {'mode': 'read'}, **HTMX)
        assert response.status_code == 422
        assert 'A note can have at most 1 active links.' in text(response)

    def test_without_htmx_it_flashes_and_goes_back_with_the_panel_open(self, web, note):
        response = web.post(url('share-create', note.pk), {'mode': 'read'}, follow=True)
        assert response.redirect_chain[-1][0] == f'{url("note", note.pk)}?share=1'
        assert 'Link created.' in text(response)

    def test_a_note_that_is_read_only_cannot_get_a_link(self, web, note):
        type(note).objects.filter(pk=note.pk).update(locked_at=timezone.now())
        response = web.post(url('share-create', note.pk), {'mode': 'read'}, **HTMX)
        assert response.status_code == 422
        assert 'This note is locked until its renewal is paid.' in text(response)


class TestRevokingOnAReadOnlyNote:
    """Revoking is always the owner's: it only reduces what is exposed."""

    @pytest.fixture
    def link(self, note):
        return links.create_link(note, mode='edit', label='leaked')

    @pytest.mark.parametrize('state', ['locked', 'trashed', 'taken_down'])
    def test_revoke_works(self, web, note, link, state):
        from ..services import takedown
        from .factories import make_user

        if state == 'locked':
            type(note).objects.filter(pk=note.pk).update(locked_at=timezone.now())
        elif state == 'trashed':
            trash(note)
        else:
            takedown.take_down_note(note, by=make_user(is_staff=True), reason='x')
        response = web.post(url('share-revoke', note.pk, link.pk), **HTMX)
        assert response.status_code == 200
        link.refresh_from_db()
        assert link.revoked_at is not None
        plain = web.post(url('share-revoke', note.pk, link.pk), follow=True)
        assert plain.status_code == 200 and 'Link revoked.' in text(plain)

    def test_create_and_edit_still_refuse(self, web, note, link):
        type(note).objects.filter(pk=note.pk).update(locked_at=timezone.now())
        assert web.post(url('share-create', note.pk), {'mode': 'read'}, **HTMX).status_code == 422
        edit = web.post(url('share-edit', note.pk, link.pk), {'label': 'x'}, **HTMX)
        assert edit.status_code == 422

    def test_restoring_from_the_trash_says_how_many_links_work_again(self, web, note, link):
        links.create_link(note, mode='read')
        trash(note)
        response = web.post(url('restore', note.pk), follow=True)
        assert 'Note restored. 2 share links work again.' in text(response)

    def test_one_link_is_singular_and_none_says_nothing(self, web, note, link):
        trash(note)
        assert 'Note restored. 1 share link works again.' in text(
            web.post(url('restore', note.pk), follow=True)
        )
        trash(note)
        links.revoke_link(link)
        restored = text(web.post(url('restore', note.pk), follow=True))
        assert 'Note restored.' in restored and 'work again' not in restored

    def test_an_expired_or_revoked_link_is_not_counted(self, web, note, link):
        make_link(note, expires_at=timezone.now() - timedelta(days=1))
        trash(note)
        links.revoke_link(link)
        assert 'work again' not in text(web.post(url('restore', note.pk), follow=True))


class TestEditingAndRevoking:
    @pytest.fixture
    def link(self, note):
        return links.create_link(note, mode='read', label='old')

    def test_edit_changes_mode_label_and_expiry(self, web, note, link):
        response = web.post(
            url('share-edit', note.pk, link.pk),
            {'mode': 'edit', 'label': 'new', 'expires_on': '2099-01-01'},
            **HTMX,
        )
        link.refresh_from_db()
        assert response.status_code == 200
        assert (link.mode, link.label) == ('edit', 'new') and link.expires_at is not None

    def test_a_posted_empty_date_removes_the_expiry_but_a_missing_one_leaves_it(
        self, web, note, link
    ):
        web.post(url('share-edit', note.pk, link.pk), {'expires_on': '2099-01-01'}, **HTMX)
        web.post(url('share-edit', note.pk, link.pk), {'label': 'x'}, **HTMX)
        link.refresh_from_db()
        assert link.expires_at is not None and link.label == 'x'
        web.post(url('share-edit', note.pk, link.pk), {'expires_on': ''}, **HTMX)
        link.refresh_from_db()
        assert link.expires_at is None

    def test_a_bad_value_is_a_refusal(self, web, note, link):
        response = web.post(url('share-edit', note.pk, link.pk), {'mode': 'nope'}, **HTMX)
        assert response.status_code == 422 and response['HX-Retarget'] == '#share-msg'

    def test_revoking_from_the_modal_flashes_and_reopens_the_panel(self, web, note, link):
        response = web.post(url('share-revoke', note.pk, link.pk), follow=True)
        link.refresh_from_db()
        assert link.revoked_at is not None
        assert response.redirect_chain[-1][0] == f'{url("note", note.pk)}?share=1'
        assert 'Link revoked.' in text(response)

    def test_revoking_over_htmx_answers_the_panel_and_a_revoked_link_has_no_actions(
        self, web, note, link
    ):
        response = web.post(url('share-revoke', note.pk, link.pk), **HTMX)
        body = text(response)
        assert response.status_code == 200 and 'Revoked' in body
        assert url('share-revoke', note.pk, link.pk) not in body

    def test_a_revoked_link_cannot_be_edited(self, web, note, link):
        web.post(url('share-revoke', note.pk, link.pk))
        response = web.post(url('share-edit', note.pk, link.pk), {'label': 'x'}, **HTMX)
        assert response.status_code == 422
        assert 'This link was revoked and can no longer be changed.' in text(response)

    def test_another_notes_or_users_link_is_a_404(self, web, user, note, link, other_user, config):
        other_note = create_note(user, title='Other')
        assert web.post(url('share-revoke', other_note.pk, link.pk)).status_code == 404
        theirs = links.create_link(create_note(other_user, title='T'), mode='read')
        assert web.post(url('share-revoke', note.pk, theirs.pk)).status_code == 404
        assert web.post(url('share-edit', note.pk, theirs.pk), {}, **HTMX).status_code == 404

    def test_the_revoke_button_asks_first(self, web, note, link):
        body = text(web.get(url('share', note.pk), **HTMX))
        assert 'data-title="Revoke this link?"' in body and 'data-label="Revoke"' in body


class TestTheShareIcon:
    def test_the_list_row_and_the_note_page_show_it_only_while_a_link_is_active(self, web, note):
        assert 'data-shared' not in text(web.get(url('list')))
        link = links.create_link(note, mode='read')
        assert 'data-shared' in text(web.get(url('list')))
        assert 'Shared' in text(web.get(url('note', note.pk)))
        link.note = note
        links.revoke_link(link)
        assert 'data-shared' not in text(web.get(url('list')))
        assert 'fa-link text-[10px]' not in text(web.get(url('note', note.pk)))

    def test_an_expired_link_shows_no_icon(self, web, note):
        make_link(note, expires_at=timezone.now() - timedelta(minutes=1))
        assert 'data-shared' not in text(web.get(url('list')))

    def test_no_icon_while_the_note_is_read_only_and_its_links_dont_work(self, web, note):
        links.create_link(note, mode='read')
        type(note).objects.filter(pk=note.pk).update(locked_at=timezone.now())
        assert 'data-shared' not in text(web.get(url('list')))
        assert 'fa-link text-[10px]' not in text(web.get(url('note', note.pk)))

    def test_the_list_does_not_query_per_row(self, web, funded_user, django_assert_max_num_queries):
        for number in range(6):
            links.create_link(create_note(funded_user, title=f'N{number}'), mode='read')
        with django_assert_max_num_queries(25):
            assert web.get(url('list')).status_code == 200


class TestRateLimits:
    @pytest.fixture(autouse=True)
    def _limits(self, settings, ratelimit_clock):
        settings.RATELIMIT_ENABLE = True
        settings.RATELIMIT_RULES = {**settings.RATELIMIT_RULES, 'NOTES_EDIT_USER': (2, 60)}

    def test_changes_spend_the_edit_budget_and_a_refused_form_spends_nothing(self, web, note):
        for _ in range(5):
            bad = web.post(url('share-create', note.pk), {'mode': 'nope'}, **HTMX)
            assert bad.status_code == 422
        assert web.post(url('share-create', note.pk), {'mode': 'read'}, **HTMX).status_code == 200
        assert web.post(url('share-create', note.pk), {'mode': 'read'}, **HTMX).status_code == 200
        over = web.post(url('share-create', note.pk), {'mode': 'read'}, **HTMX)
        assert over.status_code == 429 and over['HX-Retarget'] == '#share-msg'

    def test_reading_the_panel_is_free(self, web, note):
        for _ in range(5):
            assert web.get(url('share', note.pk), **HTMX).status_code == 200


def test_history_marks_versions_made_through_a_link(web, funded_user, note):
    link = links.create_link(note, mode='edit')
    anon = Client()
    anon.post(
        reverse('n:edit', args=[link.code]), {'content': 'by anyone', 'base_version': '1'}, **HTMX
    )
    body = text(web.get(url('history', note.pk)))
    assert 'Someone via a shared link' in body
    version = note.versions.order_by('-number').first()
    page = text(web.get(url('history-version', note.pk, version.pk)))
    assert 'Someone via a shared link' in page and 'by anyone' in page
    restore_version(
        type(note).objects.select_related('user').get(pk=note.pk), note.versions.get(number=1)
    )
