import pytest
from django.urls import reverse

from ..models import LinkStatus, RetiredCode, ShortLink, ShortUrlSettings
from .factories import create_link, make_user

pytestmark = pytest.mark.django_db


@pytest.fixture
def superuser(client):
    admin = make_user(is_staff=True, is_superuser=True, email='root@example.com')
    client.force_login(admin)
    return admin


@pytest.fixture
def staff(client):
    member = make_user(is_staff=True, email='staff@example.com')
    client.force_login(member)
    return member


class TestShortLinkAdmin:
    def test_the_list_loads_and_shows_the_links(self, client, superuser, user):
        create_link(user, code='Adm001', destination='https://example.com/' + 'x' * 80)
        create_link(user, code='Adm002', status=LinkStatus.DISABLED)
        response = client.get(reverse('admin:short_url_shortlink_changelist'))
        assert response.status_code == 200
        content = response.content.decode()
        assert 'Adm001' in content and 'Adm002' in content
        assert '...' in content  # the long destination is cut

    def test_search_and_filters(self, client, superuser, user):
        create_link(user, code='Find01')
        create_link(user, code='Other1')
        url = reverse('admin:short_url_shortlink_changelist')
        content = client.get(url, {'q': 'find01'}).content.decode()
        assert 'Find01' in content and 'Other1' not in content
        assert client.get(url, {'q': user.email}).status_code == 200
        assert client.get(url, {'status__exact': 'disabled'}).status_code == 200

    def test_the_detail_page_loads_and_shows_the_public_link(self, client, superuser, user):
        link = create_link(user, code='Det001')
        response = client.get(reverse('admin:short_url_shortlink_change', args=[link.pk]))
        assert response.status_code == 200
        assert 'https://app.example.test/s/Det001' in response.content.decode()

    def test_now_says_blocked_for_a_blocked_destination(self, client, superuser, user):
        from ..models import BlockedDomain

        create_link(user, code='Blk001', destination='https://evil.example/page')
        create_link(user, code='Ok0001', destination='https://example.com/page')
        BlockedDomain.objects.create(value='evil.example')

        response = client.get(reverse('admin:short_url_shortlink_changelist'))

        rows = response.content.decode().split('<tr')
        rows = {code: next(row for row in rows if code in row) for code in ('Blk001', 'Ok0001')}
        assert '<td class="field-state">blocked</td>' in rows['Blk001']
        assert '<td class="field-state">active</td>' in rows['Ok0001']

    def test_it_is_read_only(self, client, superuser, user):
        link = create_link(user)
        assert client.get(reverse('admin:short_url_shortlink_add')).status_code == 403
        response = client.post(
            reverse('admin:short_url_shortlink_change', args=[link.pk]), {'destination': 'x'}
        )
        assert response.status_code == 403
        delete = reverse('admin:short_url_shortlink_delete', args=[link.pk])
        assert client.post(delete, {'post': 'yes'}).status_code == 403
        assert ShortLink.objects.filter(pk=link.pk).exists()


class TestSettingsAdmin:
    def test_the_changelist_goes_to_the_one_row(self, client, superuser):
        response = client.get(reverse('admin:short_url_shorturlsettings_changelist'))
        assert response.status_code == 302
        assert response['Location'] == reverse('admin:short_url_shorturlsettings_change', args=[1])

    def test_a_superuser_can_change_the_prices(self, client, superuser):
        row = ShortUrlSettings.load()
        url = reverse('admin:short_url_shorturlsettings_change', args=[row.pk])
        assert client.get(url).status_code == 200
        response = client.post(
            url,
            {
                'custom_alias_price': 4,
                'password_price': 5,
                'analytics_price': 6,
                'extra_link_price': 7,
                'free_active_links': 10,
                'max_active_links': 20,
                'click_event_retention_days': 90,
                'auto_hold_report_threshold': 0,
            },
        )
        assert response.status_code == 302
        row.refresh_from_db()
        assert (row.custom_alias_price, row.extra_link_price, row.free_active_links) == (4, 7, 10)

    def test_the_free_allowance_cannot_exceed_the_cap(self, client, superuser):
        row = ShortUrlSettings.load()
        response = client.post(
            reverse('admin:short_url_shorturlsettings_change', args=[row.pk]),
            {
                'custom_alias_price': 1,
                'password_price': 1,
                'analytics_price': 1,
                'extra_link_price': 1,
                'free_active_links': 50,
                'max_active_links': 20,
                'click_event_retention_days': 90,
                'auto_hold_report_threshold': 0,
            },
        )
        assert response.status_code == 200  # the form again, with the error
        assert 'cannot be higher than the cap' in response.content.decode()

    def test_staff_who_are_not_superusers_cannot_see_it(self, client, staff):
        url = reverse('admin:short_url_shorturlsettings_change', args=[1])
        ShortUrlSettings.load()
        assert client.get(url).status_code == 403

    def test_it_cannot_be_deleted(self, client, superuser):
        row = ShortUrlSettings.load()
        url = reverse('admin:short_url_shorturlsettings_delete', args=[row.pk])
        assert client.post(url, {'post': 'yes'}).status_code == 403
        assert ShortUrlSettings.objects.exists()


class TestDeletingAUserWithLinks:
    def test_a_superuser_can_delete_a_user_who_has_links(self, client, superuser, user):
        create_link(user, code='Del001')
        url = reverse('admin:accounts_user_delete', args=[user.pk])
        page = client.get(url)
        assert page.status_code == 200
        assert 'Cannot delete' not in page.content.decode()
        response = client.post(url, {'post': 'yes'})
        assert response.status_code == 302
        assert not ShortLink.objects.filter(code_key='del001').exists()
        assert RetiredCode.objects.filter(code_key='del001').exists()

    def test_staff_need_the_short_link_delete_permission(self, client, staff, user):
        from django.contrib.auth.models import Permission

        create_link(user, code='Del002')
        staff.user_permissions.add(Permission.objects.get(codename='delete_user'))
        url = reverse('admin:accounts_user_delete', args=[user.pk])
        assert client.post(url, {'post': 'yes'}).status_code == 403
        assert ShortLink.objects.filter(code_key='del002').exists()

        staff.user_permissions.add(Permission.objects.get(codename='delete_shortlink'))
        assert client.post(url, {'post': 'yes'}).status_code == 302
        assert not ShortLink.objects.filter(code_key='del002').exists()


class TestDailyLinkStatAdmin:
    @pytest.fixture
    def stat(self, user):
        from datetime import date

        from ..models import DailyLinkStat

        link = create_link(user, code='Roll001')
        return DailyLinkStat.objects.create(
            link=link, date=date(2026, 10, 19), clicks=5, uniques=3, bot_clicks=1
        )

    def test_the_list_loads_and_shows_the_rollups(self, client, superuser, stat):
        response = client.get(reverse('admin:short_url_dailylinkstat_changelist'))

        assert response.status_code == 200
        assert 'Roll001' in response.content.decode()

    def test_search_by_code_and_by_owner(self, client, superuser, stat, user):
        url = reverse('admin:short_url_dailylinkstat_changelist')

        assert 'Roll001' in client.get(url, {'q': 'roll001'}).content.decode()
        assert 'Roll001' in client.get(url, {'q': user.email}).content.decode()
        assert 'Roll001' not in client.get(url, {'q': 'nothing-like-it'}).content.decode()

    def test_it_is_read_only(self, client, superuser, stat):
        from ..models import DailyLinkStat

        assert client.get(reverse('admin:short_url_dailylinkstat_add')).status_code == 403
        change = reverse('admin:short_url_dailylinkstat_change', args=[stat.pk])
        assert client.post(change, {'clicks': 99}).status_code == 403
        delete = reverse('admin:short_url_dailylinkstat_delete', args=[stat.pk])
        assert client.post(delete, {'post': 'yes'}).status_code == 403
        assert DailyLinkStat.objects.get(pk=stat.pk).clicks == 5
