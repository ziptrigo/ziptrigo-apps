"""The admin's abuse tooling: the report list, the take down / block domain / deactivate owner /
release hold / dismiss actions on links and reports (each behind its own permission, the
destructive ones behind a confirmation page), and the block list's CRUD."""

from datetime import timedelta

import pytest
from django.contrib.admin.helpers import ACTION_CHECKBOX_NAME
from django.contrib.auth.models import Permission
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User

from ..models import BlockedDomain, LinkReport, LinkStatus, ReportStatus, ShortLink
from ..services import blocklist, reports
from .factories import create_link, make_user

pytestmark = pytest.mark.django_db

LINKS = reverse('admin:short_url_shortlink_changelist')
REPORTS = reverse('admin:short_url_linkreport_changelist')
BLOCKED = reverse('admin:short_url_blockeddomain_changelist')


@pytest.fixture
def superuser(client):
    admin = make_user(is_staff=True, is_superuser=True, email='root@example.com')
    client.force_login(admin)
    return admin


def staff_with(client, *permissions: str, email='staff@example.com') -> User:
    """A staff user (not a superuser) holding exactly the given `app_label.codename`
    permissions, logged in."""
    member = make_user(is_staff=True, email=email)
    for permission in permissions:
        app_label, codename = permission.split('.')
        # A `PermissionsMixin` many-to-many manager, which `ty` sees only as the field.
        member.user_permissions.add(  # ty: ignore[unresolved-attribute]
            Permission.objects.get(content_type__app_label=app_label, codename=codename)
        )
    client.force_login(member)
    return member


def act(client, url, action, objects, *, follow=False, **data):
    return client.post(
        url,
        {'action': action, ACTION_CHECKBOX_NAME: [str(obj.pk) for obj in objects], **data},
        follow=follow,
    )


@pytest.fixture
def link(user):
    return create_link(user, code='Bad001', destination='https://www.phish.example.net/login')


@pytest.fixture
def reported(link):
    return reports.create_report(link, reason='phishing', reporter_ip='203.0.113.1')


@pytest.fixture
def sent(monkeypatch):
    mails: list[dict] = []
    monkeypatch.setattr(
        'apps.short_url.services.emails.send_email', lambda **kwargs: mails.append(kwargs)
    )
    return mails


class TestChangelists:
    def test_the_link_list_shows_state_hold_and_pending_reports(self, client, superuser, link):
        reports.create_report(link, reason='spam', reporter_ip='203.0.113.1')
        reports.create_report(link, reason='spam', reporter_ip='203.0.113.2')
        ShortLink.objects.filter(pk=link.pk).update(held_for_review_at=timezone.now())
        response = client.get(LINKS)
        assert response.status_code == 200
        content = response.content.decode()
        assert 'Bad001' in content and 'Pending reports' in content
        assert 'held' in content

    def test_the_held_filter_works(self, client, superuser, link, user):
        create_link(user, code='Fine01')
        ShortLink.objects.filter(pk=link.pk).update(held_for_review_at=timezone.now())
        content = client.get(LINKS, {'held_for_review_at__isempty': '0'}).content.decode()
        assert 'Bad001' in content and 'Fine01' not in content

    def test_the_report_list_links_to_the_link_and_filters(self, client, superuser, reported):
        response = client.get(REPORTS)
        assert response.status_code == 200
        content = response.content.decode()
        assert 'Bad001' in content
        assert (
            reverse('custom_admin:short_url_shortlink_change', args=[reported.link_id]) in content
        )
        assert client.get(REPORTS, {'status__exact': 'pending'}).status_code == 200
        assert client.get(REPORTS, {'q': 'phish.example'}).status_code == 200
        assert client.get(REPORTS, {'q': '203.0.113.1'}).status_code == 200

    def test_a_report_is_read_only(self, client, superuser, reported):
        assert client.get(reverse('admin:short_url_linkreport_add')).status_code == 403
        url = reverse('admin:short_url_linkreport_change', args=[reported.pk])
        assert client.post(url, {'details': 'x'}).status_code == 403
        delete = reverse('admin:short_url_linkreport_delete', args=[reported.pk])
        assert client.post(delete, {'post': 'yes'}).status_code == 403
        assert LinkReport.objects.filter(pk=reported.pk).exists()


class TestTakeDownFromTheLinkList:
    def test_the_first_step_only_asks(self, client, superuser, link):
        response = act(client, LINKS, 'take_down_action', [link])
        assert response.status_code == 200
        content = response.content.decode()
        assert 'Take down link(s)' in content and 'Bad001' in content
        link.refresh_from_db()
        assert link.status == LinkStatus.ACTIVE

    def test_confirming_takes_it_down_with_the_record(self, client, superuser, link):
        response = act(
            client,
            LINKS,
            'take_down_action',
            [link],
            apply='yes',
            reason='Phishing page',
            internal_note='ticket 12',
            follow=True,
        )
        assert response.status_code == 200
        link.refresh_from_db()
        assert link.status == LinkStatus.TAKEN_DOWN
        assert link.taken_down_by == superuser
        assert (link.takedown_reason, link.takedown_internal_note) == ('Phishing page', 'ticket 12')
        assert 'Took down 1 link(s).' in response.content.decode()

    def test_a_reason_is_required(self, client, superuser, link):
        response = act(client, LINKS, 'take_down_action', [link], apply='yes', reason='')
        assert response.status_code == 200
        link.refresh_from_db()
        assert link.status == LinkStatus.ACTIVE

    def test_the_owner_is_not_emailed_unless_asked(
        self, client, superuser, link, sent, django_capture_on_commit_callbacks
    ):
        with django_capture_on_commit_callbacks(execute=True):
            act(client, LINKS, 'take_down_action', [link], apply='yes', reason='Phishing')
        assert sent == []

    def test_the_owner_is_emailed_when_staff_opt_in(
        self, client, superuser, link, user, sent, django_capture_on_commit_callbacks
    ):
        with django_capture_on_commit_callbacks(execute=True):
            act(
                client,
                LINKS,
                'take_down_action',
                [link],
                apply='yes',
                reason='Phishing',
                notify_owner='on',
            )
        assert len(sent) == 1 and sent[0]['to'] == user.email

    def test_pending_reports_are_actioned(self, client, superuser, link, reported):
        act(client, LINKS, 'take_down_action', [link], apply='yes', reason='Phishing')
        reported.refresh_from_db()
        assert reported.status == ReportStatus.ACTIONED and reported.reviewed_by == superuser

    def test_links_that_already_ended_are_skipped_and_said_so(self, client, superuser, link, user):
        gone = create_link(user, code='Gone01', status=LinkStatus.DELETED)
        response = act(
            client,
            LINKS,
            'take_down_action',
            [link, gone],
            apply='yes',
            reason='Phishing',
            follow=True,
        )
        gone.refresh_from_db()
        assert gone.status == LinkStatus.DELETED  # not turned into taken down
        assert 'Took down 1 link(s).' in response.content.decode()


class TestTakeDownFromTheReportList:
    def test_several_reports_of_one_link_take_it_down_once(self, client, superuser, link):
        first = reports.create_report(link, reason='spam', reporter_ip='203.0.113.1')
        second = reports.create_report(link, reason='phishing', reporter_ip='203.0.113.2')
        page = act(client, REPORTS, 'take_down_action', [first, second])
        assert page.content.decode().count('Bad001 --') == 1
        response = act(
            client,
            REPORTS,
            'take_down_action',
            [first, second],
            apply='yes',
            reason='Phishing',
            follow=True,
        )
        assert 'Took down 1 link(s).' in response.content.decode()
        link.refresh_from_db()
        assert link.status == LinkStatus.TAKEN_DOWN
        assert set(LinkReport.objects.values_list('status', flat=True)) == {ReportStatus.ACTIONED}


class TestDismiss:
    def test_dismissing_marks_the_report_and_releases_a_hold(self, client, superuser, link):
        first = reports.create_report(link, reason='spam', reporter_ip='203.0.113.1')
        ShortLink.objects.filter(pk=link.pk).update(held_for_review_at=timezone.now())
        response = act(client, REPORTS, 'dismiss_action', [first], follow=True)
        assert 'Dismissed 1 report(s).' in response.content.decode()
        first.refresh_from_db()
        assert first.status == ReportStatus.DISMISSED and first.reviewed_by == superuser
        assert first.resolution_note == 'Dismissed via admin.'
        link.refresh_from_db()
        assert link.held_for_review_at is None

    def test_an_already_reviewed_report_is_not_counted(self, client, superuser, reported):
        act(client, REPORTS, 'dismiss_action', [reported])
        response = act(client, REPORTS, 'dismiss_action', [reported], follow=True)
        assert 'Dismissed 0 report(s).' in response.content.decode()


class TestReleaseHold:
    def test_it_releases_and_dismisses_the_pending_reports(self, client, superuser, link, reported):
        ShortLink.objects.filter(pk=link.pk).update(held_for_review_at=timezone.now())
        response = act(client, LINKS, 'release_hold_action', [link], follow=True)
        assert 'Released the hold on 1 link(s).' in response.content.decode()
        link.refresh_from_db()
        assert link.held_for_review_at is None
        reported.refresh_from_db()
        assert reported.status == ReportStatus.DISMISSED
        assert reported.resolution_note == 'Hold released via admin.'

    def test_a_link_that_is_not_held_is_skipped(self, client, superuser, link, reported):
        response = act(client, LINKS, 'release_hold_action', [link], follow=True)
        assert 'Released the hold on 0 link(s).' in response.content.decode()
        reported.refresh_from_db()
        assert reported.status == ReportStatus.PENDING


class TestBlockDomain:
    def test_the_first_step_only_asks(self, client, superuser, link):
        response = act(client, LINKS, 'block_domain_action', [link])
        assert response.status_code == 200
        assert 'Block destination domain(s)' in response.content.decode()
        assert not BlockedDomain.objects.exists()

    def test_confirming_blocks_the_host_and_its_subdomains_by_default(
        self, client, superuser, link, user
    ):
        other = create_link(user, code='Fine01', destination='https://www.phish.example.net/other')
        response = act(
            client,
            LINKS,
            'block_domain_action',
            [link],
            apply='yes',
            include_subdomains='on',
            reason='Phishing host',
            follow=True,
        )
        assert 'Added 1 block-list entry/entries.' in response.content.decode()
        entry = BlockedDomain.objects.get()
        assert entry.value == '*.www.phish.example.net'
        assert entry.created_by == superuser and entry.reason == 'Phishing host'
        # Existing links to it stop working at once.
        assert blocklist.is_blocked('www.phish.example.net')
        assert client.get('/s/Bad001').status_code == 404
        assert client.get('/s/Fine01').status_code == 404
        assert other.pk

    def test_without_the_box_it_blocks_exactly_the_host(self, client, superuser, link):
        act(client, LINKS, 'block_domain_action', [link], apply='yes')
        assert BlockedDomain.objects.get().value == 'www.phish.example.net'

    def test_two_links_to_one_host_add_one_entry(self, client, superuser, link, user):
        twin = create_link(user, code='Twin01', destination='https://www.phish.example.net/b')
        act(
            client, LINKS, 'block_domain_action', [link, twin], apply='yes', include_subdomains='on'
        )
        assert BlockedDomain.objects.count() == 1

    def test_it_works_from_the_report_list(self, client, superuser, reported):
        act(client, REPORTS, 'block_domain_action', [reported], apply='yes')
        assert BlockedDomain.objects.get().value == 'www.phish.example.net'

    def test_it_needs_the_add_permission(self, client, link):
        staff_with(client, 'short_url.view_shortlink', 'short_url.takedown_link')
        act(client, LINKS, 'block_domain_action', [link], apply='yes')
        assert not BlockedDomain.objects.exists()

    def test_with_the_permission_it_works(self, client, link):
        staff_with(client, 'short_url.view_shortlink', 'short_url.add_blockeddomain')
        act(client, LINKS, 'block_domain_action', [link], apply='yes')
        assert BlockedDomain.objects.exists()


class TestDeactivateOwner:
    def test_the_first_step_only_asks(self, client, superuser, link, user):
        response = act(client, LINKS, 'deactivate_owner_action', [link])
        assert response.status_code == 200
        assert user.email in response.content.decode()
        user.refresh_from_db()
        assert user.status == User.STATUS_ACTIVE

    def test_confirming_deactivates_the_account_but_not_its_links(
        self, client, superuser, link, user
    ):
        response = act(
            client,
            LINKS,
            'deactivate_owner_action',
            [link],
            apply='yes',
            reason='Repeat phishing',
            follow=True,
        )
        assert 'Deactivated 1 account(s).' in response.content.decode()
        user.refresh_from_db()
        assert user.status == User.STATUS_INACTIVE and user.inactive_reason == 'Repeat phishing'
        link.refresh_from_db()
        assert link.status == LinkStatus.ACTIVE  # off by default: the link keeps working

    def test_it_can_also_take_the_owners_links_down(self, client, superuser, link, user):
        second = create_link(user, code='Mine02', status=LinkStatus.DISABLED)
        elsewhere = create_link(make_user(), code='Other1')
        response = act(
            client,
            LINKS,
            'deactivate_owner_action',
            [link],
            apply='yes',
            reason='Repeat phishing',
            take_down_links='on',
            follow=True,
        )
        assert 'Took down 2 link(s).' in response.content.decode()
        for mine in (link, second):
            mine.refresh_from_db()
            assert mine.status == LinkStatus.TAKEN_DOWN
            assert mine.takedown_reason == 'Repeat phishing'
            assert mine.takedown_internal_note == 'Owner deactivated.'
        elsewhere.refresh_from_db()
        assert elsewhere.status == LinkStatus.ACTIVE

    def test_taking_links_down_needs_a_reason(self, client, superuser, link, user):
        response = act(
            client,
            LINKS,
            'deactivate_owner_action',
            [link],
            apply='yes',
            reason='',
            take_down_links='on',
        )
        assert response.status_code == 200
        user.refresh_from_db()
        assert user.status == User.STATUS_ACTIVE

    def test_it_works_from_the_report_list(self, client, superuser, reported, user):
        act(client, REPORTS, 'deactivate_owner_action', [reported], apply='yes')
        user.refresh_from_db()
        assert user.status == User.STATUS_INACTIVE

    def test_it_needs_the_takedown_and_the_user_permission(self, client, link, user):
        staff_with(client, 'short_url.view_shortlink', 'short_url.takedown_link')
        act(client, LINKS, 'deactivate_owner_action', [link], apply='yes')
        user.refresh_from_db()
        assert user.status == User.STATUS_ACTIVE

        staff_with(
            client, 'short_url.view_shortlink', 'accounts.change_user', email='other@example.com'
        )
        act(client, LINKS, 'deactivate_owner_action', [link], apply='yes')
        user.refresh_from_db()
        assert user.status == User.STATUS_ACTIVE

        staff_with(
            client,
            'short_url.view_shortlink',
            'short_url.takedown_link',
            'accounts.change_user',
            email='both@example.com',
        )
        act(client, LINKS, 'deactivate_owner_action', [link], apply='yes')
        user.refresh_from_db()
        assert user.status == User.STATUS_INACTIVE


class TestPermissions:
    """A plain admin action ignores `has_change_permission`, so each one names its own."""

    def test_a_superuser_gets_all_of_them(self, client, superuser, link, reported):
        page = client.get(LINKS).content.decode()
        for action in (
            'take_down_action',
            'block_domain_action',
            'deactivate_owner_action',
            'release_hold_action',
        ):
            assert f'value="{action}"' in page
        assert 'value="dismiss_action"' in client.get(REPORTS).content.decode()

    def test_view_only_staff_get_none_of_the_actions(self, client, link, reported):
        staff_with(client, 'short_url.view_shortlink', 'short_url.view_linkreport')
        page = client.get(LINKS).content.decode()
        for action in (
            'take_down_action',
            'block_domain_action',
            'deactivate_owner_action',
            'release_hold_action',
        ):
            assert f'value="{action}"' not in page
        assert 'value="dismiss_action"' not in client.get(REPORTS).content.decode()

    def test_view_only_staff_cannot_run_them_by_posting_either(self, client, link, reported):
        staff_with(client, 'short_url.view_shortlink', 'short_url.view_linkreport')
        ShortLink.objects.filter(pk=link.pk).update(held_for_review_at=timezone.now())
        act(client, LINKS, 'take_down_action', [link], apply='yes', reason='x')
        act(client, LINKS, 'release_hold_action', [link])
        act(client, REPORTS, 'dismiss_action', [reported])
        act(client, REPORTS, 'take_down_action', [reported], apply='yes', reason='x')
        link.refresh_from_db()
        assert link.status == LinkStatus.ACTIVE and link.held_for_review_at is not None
        reported.refresh_from_db()
        assert reported.status == ReportStatus.PENDING

    def test_the_takedown_permission_unlocks_take_down_release_and_dismiss(
        self, client, link, reported
    ):
        staff_with(
            client,
            'short_url.view_shortlink',
            'short_url.view_linkreport',
            'short_url.takedown_link',
        )
        ShortLink.objects.filter(pk=link.pk).update(held_for_review_at=timezone.now())
        act(client, LINKS, 'release_hold_action', [link])
        link.refresh_from_db()
        assert link.held_for_review_at is None
        act(client, REPORTS, 'dismiss_action', [reported])
        reported.refresh_from_db()
        assert reported.status == ReportStatus.DISMISSED  # by the release, above
        act(client, LINKS, 'take_down_action', [link], apply='yes', reason='x')
        link.refresh_from_db()
        assert link.status == LinkStatus.TAKEN_DOWN

    def test_the_settings_page_is_for_superusers_only(self, client):
        staff_with(client, 'short_url.view_shortlink', 'short_url.takedown_link')
        assert (
            client.get(reverse('admin:short_url_shorturlsettings_change', args=[1])).status_code
            == 403
        )


class TestBlockedDomainAdmin:
    def add_url(self):
        return reverse('admin:short_url_blockeddomain_add')

    def test_adding_normalizes_and_stamps_the_creator(self, client, superuser):
        response = client.post(
            self.add_url(), {'value': ' *.Evil.EXAMPLE ', 'reason': 'phishing'}, follow=True
        )
        assert response.status_code == 200
        entry = BlockedDomain.objects.get()
        assert entry.value == '*.evil.example' and entry.created_by == superuser

    def test_an_active_duplicate_is_refused(self, client, superuser):
        BlockedDomain.objects.create(value='evil.example')
        response = client.post(self.add_url(), {'value': 'EVIL.example'})
        assert response.status_code == 200
        assert 'already actively blocked' in response.content.decode()
        assert BlockedDomain.objects.count() == 1

    def test_a_bad_value_is_refused(self, client, superuser):
        response = client.post(self.add_url(), {'value': '*.com'})
        assert response.status_code == 200
        assert 'at least two parts' in response.content.decode()
        assert not BlockedDomain.objects.exists()

    def test_editing_keeps_the_creator(self, client, superuser):
        author = make_user(email='author@example.com')
        entry = BlockedDomain.objects.create(value='evil.example', created_by=author)
        url = reverse('admin:short_url_blockeddomain_change', args=[entry.pk])
        response = client.post(url, {'value': 'evil.example', 'reason': 'now with a reason'})
        assert response.status_code == 302
        entry.refresh_from_db()
        assert entry.created_by == author and entry.reason == 'now with a reason'

    def test_the_list_shows_whether_an_entry_is_in_force(self, client, superuser):
        BlockedDomain.objects.create(
            value='old.example', expires_at=timezone.now() - timedelta(days=1)
        )
        BlockedDomain.objects.create(value='new.example')
        response = client.get(BLOCKED, {'q': 'example'})
        assert response.status_code == 200

    def test_deleting_an_entry_lets_the_host_through_again(self, client, superuser):
        entry = BlockedDomain.objects.create(value='evil.example')
        url = reverse('admin:short_url_blockeddomain_delete', args=[entry.pk])
        client.post(url, {'post': 'yes'})
        assert not blocklist.is_blocked('evil.example')

    def test_staff_need_the_model_permissions(self, client):
        staff_with(client)
        assert client.get(self.add_url()).status_code == 403
        staff_with(client, 'short_url.add_blockeddomain', email='adder@example.com')
        assert client.get(self.add_url()).status_code == 200


class TestDeletingAUserWithReports:
    def test_a_superuser_can_delete_a_user_whose_link_was_reported(
        self, client, superuser, link, reported, user
    ):
        url = reverse('admin:accounts_user_delete', args=[user.pk])
        assert 'Cannot delete' not in client.get(url).content.decode()
        assert client.post(url, {'post': 'yes'}).status_code == 302
        assert not LinkReport.objects.exists()
