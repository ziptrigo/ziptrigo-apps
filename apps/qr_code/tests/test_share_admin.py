"""The admin's share link tooling: never a full code, no lookup reaches one, read-only, the links
inline on the QR code, and deleting a QR code or a user that has links."""

import pytest
from django.contrib.auth.models import Permission
from django.urls import reverse

from apps.accounts.models import User
from apps.qr_code.models import QRCode, QRShareLink, QRShareRecipient
from apps.qr_code.services import share

from .share_support import make_qrcode, make_user

pytestmark = [pytest.mark.django_db, pytest.mark.unit]

QRCODES = reverse('admin:qr_code_qrcode_changelist')
LINKS = reverse('admin:qr_code_qrsharelink_changelist')


@pytest.fixture
def superuser(client):
    admin = make_user(is_staff=True, is_superuser=True, email='root@example.com')
    client.force_login(admin)
    return admin


def staff_with(client, *permissions: str, email='staff@example.com') -> User:
    """A staff user (not a superuser) holding exactly the given `app_label.codename` permissions."""
    member = make_user(is_staff=True, email=email)
    for permission in permissions:
        app_label, codename = permission.split('.')
        member.user_permissions.add(  # ty: ignore[missing-attribute]
            Permission.objects.get(content_type__app_label=app_label, codename=codename)
        )
    client.force_login(member)
    return member


@pytest.fixture
def qrcode(user):
    return make_qrcode(user, name='Findable door')


@pytest.fixture
def link(qrcode):
    return share.create_link(qrcode, label='for the printer')


class TestShareLinkAdmin:
    def test_the_code_is_never_shown_in_full(self, client, superuser, link):
        for page in (
            client.get(LINKS),
            client.get(reverse('admin:qr_code_qrsharelink_change', args=[link.pk])),
            client.get(reverse('admin:qr_code_qrcode_change', args=[link.qrcode_id])),
            client.get(QRCODES),
        ):
            assert page.status_code == 200
            assert link.code not in page.content.decode()
        assert f'{link.code[:6]}...' in client.get(LINKS).content.decode()
        assert (
            f'{link.code[:6]}...'
            in client.get(
                reverse('admin:qr_code_qrcode_change', args=[link.qrcode_id])
            ).content.decode()
        )

    @pytest.mark.parametrize(
        ('url_name', 'query'),
        [
            ('admin:qr_code_qrsharelink_changelist', 'code__startswith={start}'),
            ('admin:qr_code_qrsharelink_changelist', 'code__exact={code}'),
            ('admin:qr_code_qrsharelink_changelist', 'code__contains={start}'),
            (
                'admin:qr_code_qrsharelink_changelist',
                'qrcode__share_links__code__startswith={start}',
            ),
            ('admin:qr_code_qrcode_changelist', 'share_links__code__startswith={start}'),
            ('admin:qr_code_qrcode_changelist', 'share_links__code={code}'),
        ],
    )
    def test_no_lookup_reaches_a_code(self, client, superuser, link, url_name, query):
        """`?code__startswith=` would narrow a full code down one character at a time (Django
        accepts any lookup on a local field), so a lookup with a `code` part is refused: even a
        superuser gets the admin's "bad lookup" answer, never a filtered list."""
        url = reverse(url_name) + '?' + query.format(start=link.code[:1], code=link.code)
        response = client.get(url)
        assert response.status_code in (400, 302)
        if response.status_code == 302:  # the changelist's "?e=1" redirect for a bad lookup
            assert 'e=1' in response['Location']

    def test_a_lookup_without_code_still_works(self, client, superuser, link):
        assert client.get(LINKS + '?created_at__year=2000').status_code == 200
        # `short_code` is a QR code field, not a share link code: its lookups stay allowed.
        assert client.get(QRCODES + '?short_code__startswith=a').status_code == 200

    def test_the_list_shows_the_code_owner_and_state(self, client, superuser, link, user):
        body = client.get(LINKS).content.decode()
        assert 'Findable door' in body and user.email in body and 'active' in body
        share.revoke_link(QRShareLink.objects.select_related('qrcode__created_by').get(pk=link.pk))
        assert 'revoked' in client.get(LINKS).content.decode()

    def test_a_link_of_a_deleted_code_says_so(self, client, superuser, link, qrcode):
        qrcode.soft_delete()
        assert 'active (QR code unavailable)' in client.get(LINKS).content.decode()

    def test_the_code_cannot_be_searched_for(self, client, superuser, link):
        assert 'Findable door' in client.get(LINKS).content.decode()
        assert 'Findable door' not in client.get(LINKS, {'q': link.code}).content.decode()
        assert 'Findable door' in client.get(LINKS, {'q': 'Findable'}).content.decode()

    def test_it_is_read_only(self, client, superuser, link):
        assert client.get(reverse('admin:qr_code_qrsharelink_add')).status_code == 403
        delete = reverse('admin:qr_code_qrsharelink_delete', args=[link.pk])
        assert client.get(delete).status_code == 403
        change = reverse('admin:qr_code_qrsharelink_change', args=[link.pk])
        assert client.post(change, {}).status_code == 403

    def test_the_qr_codes_page_lists_its_links_read_only(self, client, superuser, link):
        body = client.get(
            reverse('admin:qr_code_qrcode_change', args=[link.qrcode_id])
        ).content.decode()
        assert 'for the printer' in body and 'name="share_links-0-label"' not in body


class TestRecipients:
    @pytest.fixture
    def person(self, link):
        return QRShareRecipient.objects.create(link=link, email='ann@example.com')

    def test_the_link_page_lists_the_people_it_was_emailed_to_read_only(
        self, client, superuser, link, person
    ):
        body = client.get(
            reverse('admin:qr_code_qrsharelink_change', args=[link.pk])
        ).content.decode()
        assert 'ann@example.com' in body
        assert 'name="recipients-0-email"' not in body and 'recipients-TOTAL_FORMS' in body
        assert body.count(link.code) == 0  # still no full code

    def test_a_post_cannot_add_or_change_a_person(self, client, superuser, link, person):
        change = reverse('admin:qr_code_qrsharelink_change', args=[link.pk])
        assert client.post(change, {'recipients-TOTAL_FORMS': '1'}).status_code == 403
        assert QRShareRecipient.objects.count() == 1

    def test_deleting_a_qr_code_or_a_user_takes_the_people_too(
        self, client, superuser, user, link, person
    ):
        delete = reverse('admin:qr_code_qrcode_delete', args=[link.qrcode_id])
        assert 'cannot be deleted' not in client.get(delete).content.decode()
        assert client.post(delete, {'post': 'yes'}).status_code == 302
        assert not QRShareRecipient.objects.exists()
        other = make_qrcode(user)
        QRShareRecipient.objects.create(link=share.create_link(other), email='bob@example.com')
        response = client.post(
            reverse('admin:accounts_user_delete', args=[user.pk]), {'post': 'yes'}
        )
        assert response.status_code == 302 and not QRShareRecipient.objects.exists()

    def test_staff_who_may_delete_a_user_are_not_blocked_by_the_people(
        self, client, user, link, person
    ):
        staff_with(
            client,
            'accounts.view_user',
            'accounts.delete_user',
            'qr_code.delete_qrcode',
            'qr_code.delete_qrsharelink',
        )
        delete = reverse('admin:accounts_user_delete', args=[user.pk])
        assert 'cannot be deleted' not in client.get(delete).content.decode()


class TestDeleting:
    def test_a_superuser_can_delete_a_qr_code_that_has_links(self, client, superuser, link):
        delete = reverse('admin:qr_code_qrcode_delete', args=[link.qrcode_id])
        page = client.get(delete)
        assert page.status_code == 200
        assert 'cannot be deleted' not in page.content.decode()
        response = client.post(delete, {'post': 'yes'})
        assert response.status_code == 302
        assert not QRCode.objects.exists() and not QRShareLink.objects.exists()

    def test_staff_with_delete_permissions_can_too(self, client, link):
        staff_with(
            client,
            'qr_code.view_qrcode',
            'qr_code.delete_qrcode',
            'qr_code.delete_qrsharelink',
            'qr_code.view_qrsharelink',
        )
        delete = reverse('admin:qr_code_qrcode_delete', args=[link.qrcode_id])
        assert 'cannot be deleted' not in client.get(delete).content.decode()

    def test_staff_without_the_link_permission_are_still_refused(self, client, link):
        staff_with(client, 'qr_code.view_qrcode', 'qr_code.delete_qrcode')
        delete = reverse('admin:qr_code_qrcode_delete', args=[link.qrcode_id])
        assert 'QR share link' in client.get(delete).content.decode()
        assert client.post(delete, {'post': 'yes'}).status_code in (200, 403)
        assert QRCode.objects.filter(pk=link.qrcode_id).exists()

    def test_a_superuser_can_delete_a_user_who_has_shared_codes(
        self, client, superuser, user, link
    ):
        response = client.post(
            reverse('admin:accounts_user_delete', args=[user.pk]), {'post': 'yes'}
        )
        assert response.status_code == 302
        assert not User.objects.filter(pk=user.pk).exists()
        assert not QRShareLink.objects.exists() and not QRCode.objects.exists()

    def test_staff_who_may_delete_users_and_links_are_not_blocked_by_the_read_only_admin(
        self, client, user, link
    ):
        staff_with(
            client,
            'accounts.view_user',
            'accounts.delete_user',
            'qr_code.delete_qrcode',
            'qr_code.delete_qrsharelink',
        )
        delete = reverse('admin:accounts_user_delete', args=[user.pk])
        assert 'cannot be deleted' not in client.get(delete).content.decode()
