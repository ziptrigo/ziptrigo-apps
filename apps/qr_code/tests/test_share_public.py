"""The shared QR code pages `/q/<code>` and `/q/<code>/image`: what they show, that every dead link
is the same neutral page, the headers, the image (and a tracked code's), the rate limit."""

from datetime import timedelta

import pytest
from django.conf import settings as django_settings
from django.template.loader import render_to_string
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.qr_code.models import QRCodeType
from apps.qr_code.services import QRCodeGenerator, share, takedown

from .share_support import make_link, make_qrcode, make_user

pytestmark = [pytest.mark.django_db, pytest.mark.unit]


def text(response) -> str:
    return response.content.decode()


def page(code: str) -> str:
    return reverse('q:page', args=[code])


def image(code: str) -> str:
    return reverse('q:image', args=[code])


@pytest.fixture
def qrcode(user):
    return make_qrcode(user)


@pytest.fixture
def link(qrcode):
    return share.create_link(qrcode, label='secret reminder')


@pytest.fixture
def visitor():
    return Client()


def assert_private(response):
    assert response['Cache-Control'] == 'no-store, private'
    assert response['Referrer-Policy'] == 'strict-origin'
    assert response['X-Robots-Tag'] == 'noindex, nofollow'


class TestPage:
    def test_it_shows_the_code_without_a_login(self, visitor, qrcode, link):
        response = visitor.get(page(link.code))
        body = text(response)
        assert response.status_code == 200
        assert_private(response)
        assert '<meta name="robots" content="noindex">' in body
        assert 'Shop door' in body and f'src="{image(link.code)}"' in body
        assert f'{image(link.code)}?download=1' in body and 'Download' in body
        assert 'This QR code leads to' in body and 'https://example.com/shop' in body
        assert 'Copy' in body

    def test_a_url_code_shows_its_destination_as_text_not_a_link(self, visitor, qrcode, link):
        body = text(visitor.get(page(link.code)))
        assert 'href="https://example.com/shop"' not in body
        assert 'Shown as text, not a link' in body

    def test_a_tracked_code_shows_where_it_leads_not_the_short_link(self, visitor, user):
        qr = make_qrcode(
            user,
            content='https://app.example.com/go/abc12345',
            original_url='https://example.com/long/destination',
            use_url_shortening=True,
            short_code='abc12345',
        )
        code = share.create_link(qr).code
        body = text(visitor.get(page(code)))
        assert 'https://example.com/long/destination' in body
        assert '/go/abc12345' not in body

    def test_a_text_code_shows_its_text(self, visitor, user):
        qr = make_qrcode(user, name='Wifi', qr_type=QRCodeType.TEXT, content='WIFI:S:home;P:x;;')
        body = text(visitor.get(page(share.create_link(qr).code)))
        assert 'This QR code contains' in body and 'WIFI:S:home;P:x;;' in body
        assert 'Shown as text, not a link' not in body

    def test_it_shows_nothing_about_the_owner_the_label_or_the_scans(self, visitor, user, link):
        from apps.qr_code.models import QRCode

        QRCode.objects.filter(pk=link.qrcode_id).update(scan_count=4242)
        body = text(visitor.get(page(link.code)))
        for secret in (user.email, user.name, 'secret reminder', '4242', str(link.qrcode_id)):
            assert secret not in body

    def test_a_hostile_name_and_content_are_escaped(self, visitor, user):
        qr = make_qrcode(
            user,
            name='<script>alert(1)</script>',
            qr_type=QRCodeType.TEXT,
            content='<img src=x onerror=alert(2)>',
        )
        body = text(visitor.get(page(share.create_link(qr).code)))
        assert '<script>alert(1)</script>' not in body and '<img src=x onerror' not in body
        assert '&lt;script&gt;' in body and '&lt;img src=x' in body

    def test_an_expiry_is_shown(self, visitor, qrcode):
        link = share.create_link(qrcode, expires_on=(timezone.now() + timedelta(days=3)).date())
        body = text(visitor.get(page(link.code)))
        assert 'Expires' in body and '<time datetime=' in body

    def test_no_expiry_says_nothing(self, visitor, link):
        assert 'Expires' not in text(visitor.get(page(link.code)))

    def test_it_is_the_code_as_it_is_now(self, visitor, qrcode, link):
        qrcode.name = 'Renamed door'
        qrcode.save()
        assert 'Renamed door' in text(visitor.get(page(link.code)))

    def test_a_trailing_slash_is_accepted_and_only_get_works(self, visitor, link):
        assert visitor.get(page(link.code) + '/').status_code == 200
        assert visitor.post(page(link.code)).status_code == 405
        assert visitor.post(image(link.code)).status_code == 405

    def test_the_signed_in_owner_sees_the_same_page(self, client, user, link):
        client.force_login(user)
        assert client.get(page(link.code)).status_code == 200


class TestImage:
    def test_it_is_the_owners_png(self, visitor, qrcode, link):
        response = visitor.get(image(link.code))
        assert response.status_code == 200 and response['Content-Type'] == 'image/png'
        assert response['X-Content-Type-Options'] == 'nosniff'
        assert 'Content-Disposition' not in response
        assert_private(response)
        assert response.content == QRCodeGenerator.render_png(qrcode)
        assert response.content.startswith(b'\x89PNG')

    def test_download_is_an_attachment_named_after_the_code(self, visitor, qrcode, link):
        response = visitor.get(image(link.code) + '?download=1')
        assert response['Content-Disposition'] == 'attachment; filename="shop-door.png"'
        assert response.content == QRCodeGenerator.render_png(qrcode)

    def test_a_name_with_nothing_to_slug_falls_back(self, visitor, user):
        qr = make_qrcode(user, name='!!!')
        response = visitor.get(image(share.create_link(qr).code) + '?download=1')
        assert 'filename="qr-code.png"' in response['Content-Disposition']

    def test_a_tracked_codes_image_still_encodes_the_short_link(self, visitor, user, client):
        link_url = 'https://app.example.com/go/abc12345'
        qr = make_qrcode(
            user,
            content=link_url,
            original_url='https://example.com/x',
            use_url_shortening=True,
            short_code='abc12345',
        )
        code = share.create_link(qr).code
        shared = visitor.get(image(code)).content
        client.force_login(user)
        owners = client.get(reverse('qr_code:image', args=[qr.pk])).content
        assert shared == owners == QRCodeGenerator.render_png(qr)

    def test_a_scan_of_a_shared_copy_counts_as_any_scan(self, visitor, user, client):
        qr = make_qrcode(
            user,
            content='https://app.example.com/go/abc12345',
            original_url='https://example.com/x',
            use_url_shortening=True,
            short_code='abc12345',
        )
        share.create_link(qr)
        response = client.get('/go/abc12345')
        qr.refresh_from_db()
        assert response.status_code == 302 and qr.scan_count == 1

    def test_it_is_always_png_even_for_other_formats(self, visitor, user):
        qr = make_qrcode(user, qr_format='svg')
        assert visitor.get(image(share.create_link(qr).code)).content.startswith(b'\x89PNG')


class TestUnavailable:
    """Every link that doesn't work is the same neutral 404, byte for byte."""

    @pytest.fixture
    def dead_codes(self, user, qrcode):
        revoked = share.create_link(qrcode)
        revoked.qrcode = qrcode
        share.revoke_link(revoked)
        expired = make_link(qrcode, expires_at=timezone.now() - timedelta(seconds=1))
        gone = make_qrcode(user, name='Gone')
        on_deleted = share.create_link(gone)
        gone.soft_delete()
        down = make_qrcode(user, name='Down')
        on_down = share.create_link(down)
        takedown.take_down_qrcode(down, by=make_user(is_staff=True), reason='x')
        return {
            'revoked': revoked.code,
            'expired': expired.code,
            'deleted code': on_deleted.code,
            'taken down code': on_down.code,
            'unknown': 'nope-nope-nope',
            'too long': 'x' * 200,
        }

    @pytest.mark.parametrize('route', [page, image])
    def test_every_case_is_identical(self, visitor, dead_codes, route):
        expected = render_to_string('qr_code/unavailable.html')
        for reason, code in dead_codes.items():
            response = visitor.get(route(code))
            assert response.status_code == 404, reason
            assert text(response) == expected, reason
            assert_private(response)

    def test_it_is_the_page_a_scan_of_an_unknown_code_gets(self, visitor, dead_codes):
        scan = visitor.get('/go/unknown1')
        assert text(visitor.get(page(dead_codes['unknown']))) == text(scan)

    def test_a_dead_link_makes_at_most_one_query_and_loads_no_session(
        self, client, user, dead_codes, django_assert_max_num_queries
    ):
        client.force_login(user)
        for code in dead_codes.values():
            with django_assert_max_num_queries(1):
                client.get(page(code))

    def test_the_page_says_nothing_about_why(self, visitor, dead_codes):
        body = text(visitor.get(page(dead_codes['revoked']))).lower()
        for word in ('revoked', 'expired', 'deleted', 'taken down'):
            assert word not in body


class TestPlumbing:
    def test_the_prefix_skips_the_timezone_middleware(self):
        assert '/q/' in django_settings.TIMEZONE_MIDDLEWARE_SKIP_PREFIXES

    def test_a_link_works_for_a_visitor_whatever_the_owners_zone(self, visitor, user, link):
        user.timezone = 'Pacific/Auckland'
        user.save()
        assert visitor.get(page(link.code)).status_code == 200

    @pytest.mark.terms_gate
    def test_a_signed_in_visitor_who_has_not_accepted_the_terms_is_not_sent_away(
        self, client, link
    ):
        visitor = make_user()
        client.force_login(visitor)
        assert client.get(page(link.code)).status_code == 200
        assert client.get(image(link.code)).status_code == 200


class TestRateLimit:
    @pytest.fixture(autouse=True)
    def _limits(self, settings, ratelimit_clock):
        settings.RATELIMIT_ENABLE = True
        settings.RATELIMIT_RULES = {**settings.RATELIMIT_RULES, 'QR_SHARE_VIEW_IP': (3, 60)}

    def test_made_up_codes_spend_the_budget_too(self, visitor, link):
        for i in range(3):
            assert visitor.get(page(f'made-up-{i}')).status_code == 404
        over = visitor.get(page(link.code))
        assert over.status_code == 429 and 'Retry-After' in over
        assert_private(over)

    def test_the_image_has_its_own_looser_budget(self, visitor, link, settings):
        settings.RATELIMIT_RULES = {**settings.RATELIMIT_RULES, 'QR_SHARE_IMAGE_IP': (4, 60)}
        # Page views spend only the page's budget (3), images and downloads only the image's (4).
        for _ in range(2):
            assert visitor.get(page(link.code)).status_code == 200
            assert visitor.get(image(link.code)).status_code == 200
        assert visitor.get(page(link.code)).status_code == 200
        assert visitor.get(image(link.code) + '?download=1').status_code == 200
        assert visitor.get(image(link.code)).status_code == 200
        for route in (page, image):
            response = visitor.get(route(link.code))
            assert response.status_code == 429 and 'Retry-After' in response
            assert_private(response)

    def test_another_address_has_its_own_budget(self, visitor, link):
        for _ in range(3):
            visitor.get(page(link.code))
        other = Client(REMOTE_ADDR='203.0.113.77')
        assert other.get(page(link.code)).status_code == 200
