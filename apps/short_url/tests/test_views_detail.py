import struct
from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from ..models import LinkStatus
from .factories import create_link, expired

pytestmark = pytest.mark.django_db


@pytest.fixture
def link(funded_user):
    return create_link(
        funded_user,
        code='Detail1',
        destination='https://example.com/a/page?x=1',
        title='Spring catalogue',
        tags=['news'],
        click_count=7,
        max_clicks=100,
    )


def detail(link):
    return reverse('short_url:detail', args=[link.pk])


def qr(link):
    return reverse('short_url:qr', args=[link.pk])


class TestDetail:
    def test_login_is_required(self, client, link):
        assert client.get(detail(link)).status_code == 302

    def test_it_shows_the_link(self, web, link):
        content = web.get(detail(link)).content.decode()
        assert 'https://app.example.test/s/Detail1' in content
        assert 'x-data="copyText"' in content
        assert 'https://example.com/a/page?x=1' in content
        assert 'Spring catalogue' in content
        assert 'news' in content
        assert '>7<' in content
        assert 'Active' in content
        assert qr(link) in content
        assert '/s/Detail1+' in content
        assert reverse('short_url:edit', args=[link.pk]) in content

    def test_expiry_and_click_limit_are_shown(self, web, funded_user):
        link = create_link(
            funded_user, expires_at=timezone.now() + timedelta(days=2), max_clicks=40
        )
        content = web.get(detail(link)).content.decode()
        assert 'Expires' in content and 'Click limit' in content

    def test_actions_depend_on_the_state(self, web, funded_user):
        active = create_link(funded_user)
        content = web.get(detail(active)).content.decode()
        assert reverse('short_url:disable', args=[active.pk]) in content
        assert reverse('short_url:enable', args=[active.pk]) not in content
        assert reverse('short_url:delete', args=[active.pk]) in content

        disabled = create_link(funded_user, status=LinkStatus.DISABLED)
        content = web.get(detail(disabled)).content.decode()
        assert reverse('short_url:enable', args=[disabled.pk]) in content
        assert reverse('short_url:disable', args=[disabled.pk]) not in content

    def test_enabling_that_costs_asks_first(self, web, funded_user):
        create_link(funded_user)
        create_link(funded_user)
        link = create_link(funded_user, status=LinkStatus.DISABLED)
        content = web.get(detail(link)).content.decode()
        assert 'data-cost="1"' in content

    def test_a_taken_down_link_shows_the_reason_and_no_actions(self, web, funded_user):
        link = create_link(
            funded_user,
            status=LinkStatus.TAKEN_DOWN,
            takedown_reason='Reported as phishing',
            takedown_internal_note='Reporter: alice@example.com',
        )
        content = web.get(detail(link)).content.decode()
        assert 'Taken down' in content
        assert 'Reported as phishing' in content
        assert 'alice@example.com' not in content
        assert reverse('short_url:edit', args=[link.pk]) not in content
        assert reverse('short_url:delete', args=[link.pk]) not in content

    def test_an_expired_link_says_so(self, web, funded_user):
        assert 'Expired' in web.get(detail(expired(funded_user))).content.decode()

    def test_the_title_is_escaped(self, web, funded_user):
        link = create_link(funded_user, title='<script>alert(1)</script>')
        assert '<script>alert(1)</script>' not in web.get(detail(link)).content.decode()

    def test_other_users_and_deleted_links_are_404(self, web, other_user, funded_user):
        assert web.get(detail(create_link(other_user))).status_code == 404
        assert (
            web.get(detail(create_link(funded_user, status=LinkStatus.DELETED))).status_code == 404
        )

    def test_an_unknown_id_is_404(self, web):
        assert (
            web.get(
                reverse('short_url:detail', args=['00000000-0000-0000-0000-000000000000'])
            ).status_code
            == 404
        )


def png_size(data: bytes) -> tuple[int, int]:
    return struct.unpack('>II', data[16:24])


class TestQr:
    def test_it_is_a_png(self, web, link):
        response = web.get(qr(link))
        assert response.status_code == 200
        assert response['Content-Type'] == 'image/png'
        assert response.content.startswith(b'\x89PNG\r\n\x1a\n')
        assert 'Content-Disposition' not in response

    def test_it_encodes_the_short_url(self, web, link):
        from apps.core.services.qr_image import render_png

        assert web.get(qr(link)).content == render_png('https://app.example.test/s/Detail1')

    def test_a_different_link_has_a_different_image(self, web, funded_user, link):
        other = create_link(funded_user, code='Detail2')
        assert web.get(qr(link)).content != web.get(qr(other)).content

    def test_download_names_the_file_after_the_code(self, web, link):
        response = web.get(qr(link), {'download': '1'})
        assert response['Content-Disposition'] == 'attachment; filename="Detail1.png"'

    def test_it_may_be_cached_privately(self, web, link):
        assert 'private' in web.get(qr(link))['Cache-Control']

    def test_login_is_required(self, client, link):
        assert client.get(qr(link)).status_code == 302

    def test_other_users_and_deleted_links_are_404(self, web, other_user, funded_user):
        assert web.get(qr(create_link(other_user))).status_code == 404
        assert web.get(qr(create_link(funded_user, status=LinkStatus.DELETED))).status_code == 404

    def test_the_rate_limit(self, web, link, settings, ratelimit_clock):
        settings.RATELIMIT_ENABLE = True
        settings.RATELIMIT_RULES = {**settings.RATELIMIT_RULES, 'SU_QR_USER': (2, 60)}
        assert [web.get(qr(link)).status_code for _ in range(3)] == [200, 200, 429]
        assert web.get(qr(link))['Retry-After']

    def test_only_get(self, web, link):
        assert web.post(qr(link)).status_code == 405
