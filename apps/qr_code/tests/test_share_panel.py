"""The owner's share dialog: opening it, creating, editing and revoking links, refusals, ownership
(404), rate limits, and the dashboard's Share item and "Shared" icon."""

from datetime import timedelta

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.qr_code.models import QRShareLink
from apps.qr_code.services import share

from .share_support import make_link, make_qrcode, make_user

pytestmark = [pytest.mark.django_db, pytest.mark.unit]

HTMX = {'HTTP_HX_REQUEST': 'true'}


def url(name, *args):
    return reverse(f'qr_code:{name}', args=args)


def text(response) -> str:
    return response.content.decode()


def future_date() -> str:
    return (timezone.now() + timedelta(days=5)).date().isoformat()


@pytest.fixture(autouse=True)
def _media_root(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path


@pytest.fixture
def web(client, user):
    client.force_login(user)
    return client


@pytest.fixture
def qrcode(user):
    return make_qrcode(user)


class TestDashboard:
    def test_the_row_menu_has_share_between_duplicate_and_download(self, web, qrcode):
        body = text(web.get(url('dashboard')))
        assert f'hx-get="{url("share", qrcode.pk)}"' in body and 'data-qr-share' in body
        assert body.index('fa-clone') < body.index('fa-share-nodes') < body.index('fa-download')
        assert '<div id="qr-share-modal"></div>' in body
        assert 'core/js/fragment_modal.js' in body

    def test_a_code_with_a_working_link_shows_the_shared_icon(self, web, user, qrcode):
        plain = make_qrcode(user, name='Plain')
        share.create_link(qrcode)
        body = text(web.get(url('dashboard')))
        assert body.count('title="Shared"') == 1
        shared = body.index(f'id="qr-shared-{qrcode.pk}"')
        assert 'title="Shared"' in body[shared : shared + 400]
        empty = body.index(f'id="qr-shared-{plain.pk}"')
        assert 'title="Shared"' not in body[empty : empty + 120]

    def test_a_revoked_or_expired_link_shows_no_icon(self, web, qrcode):
        revoked = share.create_link(qrcode)
        revoked.qrcode = qrcode
        share.revoke_link(revoked)
        make_link(qrcode, expires_at=timezone.now() - timedelta(days=1))
        assert 'title="Shared"' not in text(web.get(url('dashboard')))

    def test_the_icon_costs_no_extra_query_per_row(self, web, user, django_assert_max_num_queries):
        for i in range(5):
            share.create_link(make_qrcode(user, name=f'Code {i}'))
        with django_assert_max_num_queries(8):
            assert web.get(url('dashboard')).status_code == 200

    def test_share_in_the_address_opens_the_dialog_on_load(self, web, qrcode):
        body = text(web.get(f'{url("dashboard")}?share={qrcode.pk}'))
        assert f'hx-get="{url("share", qrcode.pk)}" hx-trigger="load"' in body

    @pytest.mark.parametrize('value', ['', 'nope', '00000000-0000-0000-0000-000000000000'])
    def test_a_bad_or_unknown_id_opens_nothing(self, web, value):
        assert 'hx-trigger="load"' not in text(web.get(f'{url("dashboard")}?share={value}'))

    def test_another_users_code_or_a_deleted_one_opens_nothing(self, web, qrcode):
        theirs = make_qrcode(make_user(), name='Theirs')
        assert 'hx-trigger="load"' not in text(web.get(f'{url("dashboard")}?share={theirs.pk}'))
        qrcode.soft_delete()
        assert 'hx-trigger="load"' not in text(web.get(f'{url("dashboard")}?share={qrcode.pk}'))

    def test_the_editor_in_edit_mode_has_a_share_button(self, web, qrcode):
        body = text(web.get(url('edit', qrcode.pk)))
        assert f'hx-get="{url("share", qrcode.pk)}"' in body and 'id="qr-share-modal"' in body
        assert 'qr-share-modal' not in text(web.get(url('create')))


class TestOpening:
    def test_the_dialog_is_an_htmx_fragment_listing_the_links(self, web, qrcode):
        link = share.create_link(qrcode, label='printer')
        response = web.get(url('share', qrcode.pk), **HTMX)
        body = text(response)
        assert response.status_code == 200 and 'x-data="fragmentModal"' in body
        assert 'Share "Shop door"' in body
        assert f'/q/{link.code}' in body and 'printer' in body and 'Copy link' in body
        assert 'Revoke' in body and 'Edit' in body and '1 of 10 active links used' in body
        assert f'id="qr-shared-{qrcode.pk}"' in body  # the dashboard icon, out of band

    def test_an_empty_dialog_says_there_are_no_links(self, web, qrcode):
        body = text(web.get(url('share', qrcode.pk), **HTMX))
        assert 'This QR code has no links yet.' in body and 'Create link' in body
        assert 'title="Shared"' not in body

    def test_opened_directly_it_goes_to_the_dashboard_with_the_dialog_open(self, web, qrcode):
        response = web.get(url('share', qrcode.pk))
        assert response.status_code == 302
        assert response['Location'] == f'{url("dashboard")}?share={qrcode.pk}'

    def test_active_links_come_first_and_inactive_ones_say_why(self, web, qrcode):
        old = share.create_link(qrcode, label='Aaa-old')
        old.qrcode = qrcode
        share.revoke_link(old)
        make_link(qrcode, label='Bbb-lapsed', expires_at=timezone.now() - timedelta(days=1))
        share.create_link(qrcode, label='Ccc-live')
        body = text(web.get(url('share', qrcode.pk), **HTMX))
        order = [body.index(label) for label in ('Ccc-live', 'Aaa-old', 'Bbb-lapsed')]
        assert order[0] < min(order[1:])
        assert 'Revoked' in body and 'Expired' in body
        assert body.count('Copy link</span>') == 1  # only the active link can be copied

    def test_a_hostile_name_and_label_are_escaped(self, web, user):
        qr = make_qrcode(user, name='<script>alert(1)</script>')
        share.create_link(qr, label='<img src=x onerror=1>')
        body = text(web.get(url('share', qr.pk), **HTMX))
        assert '<script>alert(1)</script>' not in body and '<img src=x' not in body
        assert '&lt;script&gt;' in body

    def test_another_users_code_is_a_404_everywhere(self, web):
        theirs = make_qrcode(make_user(), name='Theirs')
        link = make_link(theirs)
        assert web.get(url('share', theirs.pk), **HTMX).status_code == 404
        assert web.post(url('share-create', theirs.pk), {}, **HTMX).status_code == 404
        assert web.post(url('share-edit', theirs.pk, link.pk), {}, **HTMX).status_code == 404
        assert web.post(url('share-revoke', theirs.pk, link.pk)).status_code == 404

    def test_a_deleted_code_is_a_404(self, web, qrcode):
        qrcode.soft_delete()
        assert web.get(url('share', qrcode.pk), **HTMX).status_code == 404

    def test_a_link_of_another_code_is_a_404_under_my_code(self, web, user, qrcode):
        other = make_qrcode(user, name='Other')
        link = share.create_link(other)
        assert (
            web.post(url('share-edit', qrcode.pk, link.pk), {'label': 'x'}, **HTMX).status_code
            == 404
        )
        assert web.post(url('share-revoke', qrcode.pk, link.pk)).status_code == 404

    def test_login_is_required(self, qrcode):
        client = Client()
        assert client.get(url('share', qrcode.pk)).status_code == 302
        assert 'HX-Redirect' in client.get(url('share', qrcode.pk), **HTMX)
        assert client.post(url('share-create', qrcode.pk), {}).status_code == 302

    def test_only_get_opens_and_only_post_changes(self, web, qrcode):
        assert web.post(url('share', qrcode.pk)).status_code == 405
        assert web.get(url('share-create', qrcode.pk)).status_code == 405


class TestCreate:
    def test_a_link_is_created_and_the_dialog_answered(self, web, qrcode):
        response = web.post(
            url('share-create', qrcode.pk),
            {'label': 'for Ann', 'expires_on': future_date()},
            **HTMX,
        )
        link = QRShareLink.objects.get(qrcode=qrcode)
        body = text(response)
        assert response.status_code == 200 and link.label == 'for Ann' and link.expires_at
        assert f'/q/{link.code}' in body and '1 of 10 active links used' in body
        assert 'title="Shared"' in body  # the icon, out of band

    def test_without_htmx_it_flashes_and_goes_back_with_the_dialog_open(self, web, qrcode):
        response = web.post(url('share-create', qrcode.pk), {'label': ''})
        assert response.status_code == 302
        assert response['Location'] == f'{url("dashboard")}?share={qrcode.pk}'
        assert 'Link created.' in text(web.get(response['Location']))
        assert QRShareLink.objects.filter(qrcode=qrcode).count() == 1

    def test_a_label_and_no_expiry_are_fine(self, web, qrcode):
        web.post(url('share-create', qrcode.pk), {'expires_on': ''}, **HTMX)
        assert QRShareLink.objects.get(qrcode=qrcode).expires_at is None

    @pytest.mark.parametrize(
        ('data', 'line'),
        [
            ({'expires_on': '2000-01-01'}, 'Expires on: Choose a date that has not passed.'),
            ({'expires_on': 'tomorrow'}, 'Expires on: Enter a date.'),
            ({'label': 'x' * 101}, 'Label: Keep the label to 100 characters or fewer.'),
        ],
    )
    def test_a_refusal_is_a_422_into_the_message_slot(self, web, qrcode, data, line):
        response = web.post(url('share-create', qrcode.pk), data, **HTMX)
        assert response.status_code == 422 and response['HX-Retarget'] == '#qr-share-msg'
        assert line in text(response) and 'data-field-error=' in text(response)
        assert not QRShareLink.objects.exists()

    def test_the_cap_is_a_plain_non_field_line(self, web, qrcode):
        for _ in range(10):
            share.create_link(qrcode)
        response = web.post(url('share-create', qrcode.pk), {}, **HTMX)
        assert response.status_code == 422
        assert 'A QR code can have at most 10 active links.' in text(response)
        assert QRShareLink.objects.count() == 10

    def test_without_htmx_a_refusal_is_a_flash(self, web, qrcode):
        response = web.post(url('share-create', qrcode.pk), {'expires_on': '2000-01-01'})
        assert response.status_code == 302
        assert 'Choose a date that has not passed.' in text(web.get(response['Location']))


class TestEdit:
    @pytest.fixture
    def link(self, qrcode):
        link = share.create_link(qrcode, label='old', expires_on=None)
        link.qrcode = qrcode
        return link

    def test_the_label_and_expiry_change(self, web, qrcode, link):
        day = future_date()
        response = web.post(
            url('share-edit', qrcode.pk, link.pk), {'label': 'new', 'expires_on': day}, **HTMX
        )
        link.refresh_from_db()
        assert response.status_code == 200 and link.label == 'new'
        assert link.expires_at and link.expires_at.date().isoformat() >= day

    def test_a_posted_empty_expiry_removes_it_and_a_missing_one_keeps_it(self, web, qrcode, link):
        share.update_link(link, expires_on=timezone.now().date() + timedelta(days=9))
        web.post(url('share-edit', qrcode.pk, link.pk), {'label': 'only label'}, **HTMX)
        link.refresh_from_db()
        assert link.label == 'only label' and link.expires_at is not None
        web.post(url('share-edit', qrcode.pk, link.pk), {'expires_on': ''}, **HTMX)
        link.refresh_from_db()
        assert link.expires_at is None and link.label == 'only label'

    def test_a_revoked_link_cannot_be_edited(self, web, qrcode, link):
        share.revoke_link(link)
        response = web.post(url('share-edit', qrcode.pk, link.pk), {'label': 'x'}, **HTMX)
        assert response.status_code == 422
        assert 'This link was revoked and can no longer be changed.' in text(response)

    def test_a_bad_date_is_a_422(self, web, qrcode, link):
        response = web.post(
            url('share-edit', qrcode.pk, link.pk), {'expires_on': '1999-01-01'}, **HTMX
        )
        assert response.status_code == 422 and response['HX-Retarget'] == '#qr-share-msg'


class TestRevoke:
    def test_a_plain_post_revokes_flashes_and_goes_back_with_the_dialog_open(self, web, qrcode):
        link = share.create_link(qrcode)
        response = web.post(url('share-revoke', qrcode.pk, link.pk))
        link.refresh_from_db()
        assert link.revoked_at is not None
        assert response['Location'] == f'{url("dashboard")}?share={qrcode.pk}'
        assert 'Link revoked.' in text(web.get(response['Location']))
        assert share.get_available(link.code) is None

    def test_revoking_twice_is_fine(self, web, qrcode):
        link = share.create_link(qrcode)
        first = web.post(url('share-revoke', qrcode.pk, link.pk))
        second = web.post(url('share-revoke', qrcode.pk, link.pk))
        assert first.status_code == second.status_code == 302

    def test_the_dialog_revoke_button_uses_the_confirmation_modal(self, web, qrcode):
        link = share.create_link(qrcode)
        body = text(web.get(url('share', qrcode.pk), **HTMX))
        assert f'data-url="{url("share-revoke", qrcode.pk, link.pk)}"' in body
        assert 'x-data="confirmModal"' in body and 'confirm-modal-title' in body


class TestRateLimits:
    @pytest.fixture(autouse=True)
    def _limits(self, settings, ratelimit_clock):
        settings.RATELIMIT_ENABLE = True
        settings.RATELIMIT_RULES = {**settings.RATELIMIT_RULES, 'QR_SHARE_USER': (2, 60)}

    def test_changes_spend_the_budget_and_a_refused_form_spends_nothing(self, web, qrcode):
        for _ in range(5):
            assert (
                web.post(
                    url('share-create', qrcode.pk), {'expires_on': '2000-01-01'}, **HTMX
                ).status_code
                == 422
            )
        assert web.post(url('share-create', qrcode.pk), {}, **HTMX).status_code == 200
        assert web.post(url('share-create', qrcode.pk), {}, **HTMX).status_code == 200
        over = web.post(url('share-create', qrcode.pk), {}, **HTMX)
        assert over.status_code == 429 and over['HX-Retarget'] == '#qr-share-msg'
        assert 'Retry-After' in over and QRShareLink.objects.count() == 2

    def test_edit_and_revoke_spend_it_too(self, web, qrcode):
        link = share.create_link(qrcode)
        assert (
            web.post(url('share-edit', qrcode.pk, link.pk), {'label': 'a'}, **HTMX).status_code
            == 200
        )
        assert (
            web.post(url('share-edit', qrcode.pk, link.pk), {'label': 'b'}, **HTMX).status_code
            == 200
        )
        assert web.post(url('share-revoke', qrcode.pk, link.pk), **HTMX).status_code == 429

    def test_opening_the_dialog_is_free(self, web, qrcode):
        for _ in range(5):
            assert web.get(url('share', qrcode.pk), **HTMX).status_code == 200
