"""Follow-ups to the phase 3 review (PR #167): an inactive owner can't create, edit or enable
links; enabling a link to a blocked destination is refused before any charge; the dashboard and
the link page say "Not working" for blocked links; the deactivate action skips staff and the
moderator; the hold check stops early; a rate-limited report keeps the form; and a block-list
entry with an invalid value raises instead of saving nothing."""

import pytest
from django.contrib.admin.helpers import ACTION_CHECKBOX_NAME
from django.core.exceptions import ValidationError
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from apps.accounts.models import User

from ..models import BlockedDomain, LinkStatus, ShortLink, ShortUrlSettings
from ..services import (
    BlockedDomainError,
    LinkInput,
    OwnerInactiveError,
    blocklist,
    create_link,
    display_state,
    enable_analytics,
    hold,
    reports,
    set_enabled,
    update_link,
)
from .conftest import balance
from .factories import create_link as make_link
from .factories import make_user

pytestmark = pytest.mark.django_db

HX = {'HTTP_HX_REQUEST': 'true'}
MESSAGE = OwnerInactiveError.MESSAGE


def deactivate(user) -> None:
    user.deactivate(reason='abuse')


class TestInactiveOwner:
    def test_create_is_refused(self, funded_user):
        deactivate(funded_user)
        with pytest.raises(OwnerInactiveError):
            create_link(funded_user, LinkInput(destination='https://example.com/x'))
        assert not ShortLink.objects.exists()

    def test_edit_is_refused(self, funded_user):
        link = make_link(funded_user, destination='https://example.com/x')
        deactivate(funded_user)
        with pytest.raises(OwnerInactiveError):
            update_link(link, title='New')
        link.refresh_from_db()
        assert link.title == ''

    def test_enabling_is_refused_and_charges_nothing(self, funded_user):
        make_link(funded_user)
        make_link(funded_user)
        extra = make_link(funded_user, status=LinkStatus.DISABLED)
        deactivate(funded_user)
        before = balance(funded_user)
        with pytest.raises(OwnerInactiveError):
            set_enabled(extra, True)
        extra.refresh_from_db()
        assert extra.status == LinkStatus.DISABLED and balance(funded_user) == before

    def test_disabling_stays_open(self, funded_user):
        link = make_link(funded_user)
        deactivate(funded_user)
        assert set_enabled(link, False)

    def test_paid_analytics_is_refused(self, funded_user):
        link = make_link(funded_user)
        deactivate(funded_user)
        before = balance(funded_user)
        with pytest.raises(OwnerInactiveError):
            enable_analytics(link)
        assert balance(funded_user) == before

    def test_the_status_is_read_inside_the_call_not_from_the_instance(self, funded_user):
        """A stale `link.owner` (loaded while still active) doesn't get past the check."""
        link = make_link(funded_user, status=LinkStatus.DISABLED)
        stale = ShortLink.objects.select_related('owner').get(pk=link.pk)
        User.objects.filter(pk=funded_user.pk).update(status=User.STATUS_INACTIVE)
        assert stale.owner.status == User.STATUS_ACTIVE
        with pytest.raises(OwnerInactiveError):
            update_link(stale, title='x')
        with pytest.raises(OwnerInactiveError):
            set_enabled(stale, True)

    def test_a_live_session_cannot_create_a_link(self, web, funded_user):
        deactivate(funded_user)  # the session stays valid: only `status` changed
        page = web.get(reverse('short_url:create'))
        assert page.status_code == 302 and page['Location'] == reverse('short_url:dashboard')
        response = web.post(reverse('short_url:create'), {'destination': 'https://x.example'}, **HX)
        assert response.status_code == 422 and MESSAGE in response.content.decode()
        assert not ShortLink.objects.exists()

    def test_a_live_session_cannot_edit_a_link(self, web, funded_user):
        link = make_link(funded_user, destination='https://example.com/x')
        deactivate(funded_user)
        response = web.post(
            reverse('short_url:edit', args=[link.pk]),
            {'destination': 'https://example.com/x', 'title': 'Changed'},
            **HX,
        )
        assert response.status_code == 422 and MESSAGE in response.content.decode()
        link.refresh_from_db()
        assert link.title == ''

    def test_a_live_session_cannot_enable_a_link(self, web, funded_user):
        link = make_link(funded_user, status=LinkStatus.DISABLED)
        deactivate(funded_user)
        response = web.post(reverse('short_url:enable', args=[link.pk]), follow=True)
        assert MESSAGE in response.content.decode()
        link.refresh_from_db()
        assert link.status == LinkStatus.DISABLED


class TestEnablingABlockedDestination:
    def test_it_is_refused_before_any_charge(self, funded_user):
        make_link(funded_user)
        make_link(funded_user)
        link = make_link(
            funded_user, status=LinkStatus.DISABLED, destination='https://shop.evil.com/x'
        )
        BlockedDomain.objects.create(value='*.evil.com')
        before = balance(funded_user)
        with pytest.raises(BlockedDomainError):
            set_enabled(link, True)
        link.refresh_from_db()
        assert link.status == LinkStatus.DISABLED
        assert balance(funded_user) == before

    def test_the_web_action_shows_the_neutral_refusal(self, web, funded_user):
        link = make_link(funded_user, status=LinkStatus.DISABLED, destination='https://evil.com/x')
        BlockedDomain.objects.create(value='evil.com')
        response = web.post(reverse('short_url:enable', args=[link.pk]), follow=True)
        assert 'That address cannot be used.' in response.content.decode()
        link.refresh_from_db()
        assert link.status == LinkStatus.DISABLED

    def test_a_disabled_link_shows_not_working_on_its_page(self, web, funded_user):
        link = make_link(funded_user, status=LinkStatus.DISABLED, destination='https://evil.com/x')
        assert 'Not working' not in web.get(f'/links/{link.pk}/').content.decode()
        BlockedDomain.objects.create(value='evil.com')
        assert 'Not working' in web.get(f'/links/{link.pk}/').content.decode()

    def test_display_state(self, funded_user):
        link = make_link(funded_user, status=LinkStatus.DISABLED)
        assert display_state(link) == 'disabled'
        assert display_state(link, destination_blocked=True) == 'blocked'


class TestDashboardFlagsBlockedLinks:
    def test_blocked_links_show_not_working(self, web, funded_user):
        make_link(funded_user, code='Good01', destination='https://good.example/x')
        make_link(funded_user, code='Bad001', destination='https://www.evil.com/x')
        BlockedDomain.objects.create(value='*.evil.com')
        content = web.get(reverse('short_url:dashboard')).content.decode()
        assert content.count('Not working') == 2  # the wide and the narrow pill of one row
        assert 'Active' in content

    def test_the_cost_is_one_query_whatever_the_number_of_rows(self, web, funded_user):
        BlockedDomain.objects.create(value='*.evil.com')

        def blocklist_queries() -> int:
            with CaptureQueriesContext(connection) as queries:
                web.get(reverse('short_url:dashboard'))
            return sum('short_url_blockeddomain' in q['sql'] for q in queries.captured_queries)

        make_link(funded_user, destination='https://one.evil.com/x')
        assert blocklist_queries() == 1
        for n in range(4):
            make_link(funded_user, destination=f'https://host{n}.example/x')
        assert blocklist_queries() == 1

    def test_blocked_hosts_matches_like_is_blocked(self):
        BlockedDomain.objects.create(value='*.evil.com')
        BlockedDomain.objects.create(value='bad.example')
        hosts = ['evil.com', 'a.evil.com', 'bad.example', 'sub.bad.example', 'fine.com', '']
        assert blocklist.blocked_hosts(hosts) == {
            host for host in hosts if blocklist.is_blocked(host)
        }
        assert blocklist.blocked_hosts([]) == set()


class TestDeactivateSkipsStaff:
    @pytest.fixture
    def superuser(self, client):
        admin = make_user(is_staff=True, is_superuser=True, email='root@example.com')
        client.force_login(admin)
        return admin

    def act(self, client, links, **data):
        return client.post(
            reverse('admin:short_url_shortlink_changelist'),
            {
                'action': 'deactivate_owner_action',
                ACTION_CHECKBOX_NAME: [str(link.pk) for link in links],
                **data,
            },
            follow=data.get('apply') == 'yes',
        )

    def test_staff_and_self_are_listed_as_not_deactivated(self, client, superuser, user):
        colleague = make_user(is_staff=True, email='mod@example.com')
        links = [make_link(user), make_link(colleague), make_link(superuser)]
        content = self.act(client, links).content.decode()
        assert 'Will not be deactivated' in content
        assert 'mod@example.com (it is a staff account)' in content
        assert 'root@example.com (it is your own account)' in content

    def test_they_are_left_alone_and_the_message_says_so(self, client, superuser, user):
        colleague = make_user(is_staff=True, email='mod@example.com')
        links = [make_link(user), make_link(colleague), make_link(superuser)]
        content = self.act(client, links, apply='yes', reason='x').content.decode()
        assert 'Deactivated 1 account(s).' in content
        assert '2 not deactivated (staff or your own account)' in content
        for person in (colleague, superuser):
            person.refresh_from_db()
            assert person.status == User.STATUS_ACTIVE
        user.refresh_from_db()
        assert user.status == User.STATUS_INACTIVE

    def test_their_links_are_not_taken_down_either(self, client, superuser, user):
        colleague = make_user(is_staff=True, email='mod@example.com')
        theirs = make_link(colleague)
        self.act(client, [make_link(user), theirs], apply='yes', reason='x', take_down_links='on')
        theirs.refresh_from_db()
        assert theirs.status == LinkStatus.ACTIVE

    def test_the_count_leaves_out_accounts_that_were_already_inactive(
        self, client, superuser, user
    ):
        gone = make_user(email='gone@example.com')
        gone.deactivate(reason='earlier')
        content = self.act(
            client, [make_link(user), make_link(gone)], apply='yes', reason='x'
        ).content.decode()
        assert 'Deactivated 1 account(s).' in content
        assert '1 already inactive' in content

    def test_the_page_explains_what_inactive_means(self, client, superuser, user):
        content = self.act(client, [make_link(user)]).content.decode()
        assert "can't create, edit or enable links" in content


class TestHoldStopsEarly:
    def test_it_reads_no_more_reports_than_it_needs(self, user, monkeypatch):
        link = make_link(user)
        for n in range(8):
            reports.create_report(link, reason='spam', reporter_ip=f'203.0.113.{n + 1}')
        row = ShortUrlSettings.load()
        row.auto_hold_report_threshold = 3
        row.save()
        seen: list[str] = []
        real = hold.normalize_ip_for_key

        def spy(ip):
            seen.append(ip)
            return real(ip)

        monkeypatch.setattr(hold, 'normalize_ip_for_key', spy)
        assert hold.maybe_hold_for_reports(link)
        assert len(seen) == 3


class TestReportRateLimitKeepsTheForm:
    def test_the_message_has_its_own_element_inside_the_box(self, client, user):
        make_link(user, code='Promo1')
        page = client.get('/s/Promo1+').content.decode()
        assert page.index('id="report-box"') < page.index('id="report-msg"')

    def test_a_429_is_retargeted_so_the_form_and_text_stay(
        self, client, user, settings, ratelimit_clock
    ):
        make_link(user, code='Promo1')
        settings.RATELIMIT_ENABLE = True
        settings.RATELIMIT_RULES = {**settings.RATELIMIT_RULES, 'SU_REPORT_IP': (1, 3600)}
        data = {'reason': 'spam', 'details': 'my long description'}
        ip = {'HTTP_X_REAL_IP': '203.0.113.9'}
        assert client.post('/s/Promo1/report', data, **ip, **HX).status_code == 200
        blocked = client.post('/s/Promo1/report', data, **ip, **HX)
        assert blocked.status_code == 429
        assert blocked['HX-Retarget'] == '#report-msg'
        assert blocked['HX-Reswap'] == 'innerHTML'
        assert 'Too many requests' in blocked.content.decode()
        assert '<form' not in blocked.content.decode()  # only the message goes into #report-msg


class TestBlockedDomainSaveValidates:
    @pytest.mark.parametrize('value', ['', '*.com', 'a b.example', 'user@example.com', '*.1.2.3.4'])
    def test_an_invalid_value_raises_instead_of_saving_a_dead_entry(self, value):
        with pytest.raises(ValidationError):
            BlockedDomain.objects.create(value=value)
        assert not BlockedDomain.objects.exists()

    def test_a_valid_value_is_still_normalized(self):
        assert BlockedDomain.objects.create(value=' EVIL.com ').value == 'evil.com'
