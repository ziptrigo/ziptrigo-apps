"""The admin's share link tooling: never a full code, no lookup reaches one, read-only, the links
inline on the QR code, deleting a QR code or a user that has links; and the abuse tooling: the
report list, the Take down and Dismiss actions behind `qr_code.takedown_qrcode` and a confirmation
page, and the History line each leaves."""

import pytest
from django.contrib.admin.helpers import ACTION_CHECKBOX_NAME
from django.contrib.admin.models import LogEntry
from django.contrib.auth.models import Permission
from django.urls import reverse

from apps.accounts.models import User
from apps.qr_code.models import QRCode, QRShareLink, QRShareRecipient, QRShareReport, ReportStatus
from apps.qr_code.services import share, takedown

from .share_support import make_qrcode, make_report, make_user

pytestmark = [pytest.mark.django_db, pytest.mark.unit]

QRCODES = reverse('admin:qr_code_qrcode_changelist')
LINKS = reverse('admin:qr_code_qrsharelink_changelist')
REPORTS = reverse('admin:qr_code_qrsharereport_changelist')


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


def act(client, url, action, objects, *, follow=False, **data):
    return client.post(
        url,
        {'action': action, ACTION_CHECKBOX_NAME: [str(obj.pk) for obj in objects], **data},
        follow=follow,
    )


@pytest.fixture
def qrcode(user):
    return make_qrcode(user, name='Findable door')


@pytest.fixture
def link(qrcode):
    return share.create_link(qrcode, label='for the printer')


class TestShareLinkAdmin:
    def test_the_code_is_never_shown_in_full(self, client, superuser, link):
        report = make_report(link.qrcode, link)
        for page in (
            client.get(LINKS),
            client.get(reverse('admin:qr_code_qrsharelink_change', args=[link.pk])),
            client.get(reverse('admin:qr_code_qrcode_change', args=[link.qrcode_id])),
            client.get(QRCODES),
            client.get(REPORTS),
            client.get(reverse('admin:qr_code_qrsharereport_change', args=[report.pk])),
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
            ('admin:qr_code_qrsharereport_changelist', 'link__code__startswith={start}'),
            ('admin:qr_code_qrsharereport_changelist', 'link__code={code}'),
            ('admin:qr_code_qrsharereport_changelist', 'qrcode__share_links__code={code}'),
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


@pytest.fixture
def reported(qrcode, link):
    return make_report(qrcode, link, details='looks like phishing', reporter_email='r@example.com')


class TestTakeDownOnTheQRCodeList:
    def test_the_action_asks_for_a_reason_first_and_changes_nothing(
        self, client, superuser, qrcode
    ):
        response = act(client, QRCODES, 'take_down_action', [qrcode])
        body = response.content.decode()
        assert response.status_code == 200 and 'Take down QR code(s)' in body
        assert 'Findable door' in body and user_email(qrcode) in body
        assert 'name="reason"' in body and 'name="internal_note"' in body
        assert '/go/' in body and 'notify' not in body.lower().replace('not emailed', '')
        assert not QRCode.objects.get(pk=qrcode.pk).is_taken_down

    def test_confirming_takes_it_down_with_the_record_and_a_history_line(
        self, client, superuser, qrcode
    ):
        response = act(
            client,
            QRCODES,
            'take_down_action',
            [qrcode],
            apply='yes',
            reason='Phishing page',
            internal_note='ticket 12',
            follow=True,
        )
        fresh = QRCode.objects.get(pk=qrcode.pk)
        assert fresh.is_taken_down and fresh.taken_down_by == superuser
        assert (fresh.takedown_reason, fresh.takedown_internal_note) == (
            'Phishing page',
            'ticket 12',
        )
        assert 'Took down 1 QR code(s).' in response.content.decode()
        entry = LogEntry.objects.get(object_id=str(qrcode.pk))
        assert entry.get_change_message() == 'Taken down: Phishing page'

    def test_a_reason_is_required(self, client, superuser, qrcode):
        response = act(client, QRCODES, 'take_down_action', [qrcode], apply='yes', reason='')
        assert response.status_code == 200
        assert not QRCode.objects.get(pk=qrcode.pk).is_taken_down

    def test_an_already_taken_down_code_is_skipped_and_said_so(
        self, client, superuser, qrcode, user
    ):
        done = make_qrcode(user, name='Already')
        takedown.take_down_qrcode(done, by=superuser, reason='first')
        response = act(
            client,
            QRCODES,
            'take_down_action',
            [qrcode, done],
            apply='yes',
            reason='again',
            follow=True,
        )
        assert 'Took down 1 QR code(s). 1 had already been taken down.' in response.content.decode()
        assert QRCode.objects.get(pk=done.pk).takedown_reason == 'first'

    def test_pending_reports_are_actioned_and_the_owner_is_not_emailed(
        self, client, superuser, qrcode, reported, mailoutbox, django_capture_on_commit_callbacks
    ):
        with django_capture_on_commit_callbacks(execute=True):
            act(client, QRCODES, 'take_down_action', [qrcode], apply='yes', reason='x')
        reported.refresh_from_db()
        assert reported.status == ReportStatus.ACTIONED and reported.reviewed_by == superuser
        assert mailoutbox == []

    def test_the_list_shows_and_filters_taken_down_codes(self, client, superuser, qrcode, user):
        make_qrcode(user, name='Fine door')
        takedown.take_down_qrcode(qrcode, by=superuser, reason='x')
        body = client.get(QRCODES, {'taken_down': 'yes'}).content.decode()
        assert 'Findable door' in body and 'Fine door' not in body
        body = client.get(QRCODES, {'taken_down': 'no'}).content.decode()
        assert 'Findable door' not in body and 'Fine door' in body

    def test_the_takedown_columns_cannot_be_edited_in_the_change_form(
        self, client, superuser, qrcode
    ):
        body = client.get(reverse('admin:qr_code_qrcode_change', args=[qrcode.pk])).content.decode()
        for field in (
            'taken_down_at',
            'taken_down_by',
            'takedown_reason',
            'takedown_internal_note',
        ):
            assert f'name="{field}"' not in body

    def test_a_change_form_save_cannot_revert_a_takedown_from_a_stale_instance(
        self, client, superuser, qrcode
    ):
        stale = QRCode.objects.get(pk=qrcode.pk)  # read before the takedown
        takedown.take_down_qrcode(qrcode, by=superuser, reason='Landed meanwhile')
        stale.name = 'Renamed in the admin'

        class FakeForm:
            changed_data = ['name']

        from apps.core.admin_site import custom_admin_site

        model_admin = custom_admin_site._registry[QRCode]
        model_admin.save_model(None, stale, FakeForm(), True)
        fresh = QRCode.objects.get(pk=qrcode.pk)
        assert fresh.name == 'Renamed in the admin'
        assert fresh.is_taken_down and fresh.takedown_reason == 'Landed meanwhile'


def user_email(qrcode) -> str:
    return qrcode.created_by.email


class TestTheReportList:
    def test_it_lists_with_a_link_to_the_code_and_filters(
        self, client, superuser, qrcode, reported
    ):
        body = client.get(REPORTS).content.decode()
        assert (
            'Findable door' in body
            and reverse('admin:qr_code_qrcode_change', args=[qrcode.pk]) in body
        )
        assert 'r@example.com' in body and '203.0.113.9' in body
        for flt in ({'status__exact': 'pending'}, {'reason__exact': 'spam'}):
            assert 'Findable door' in client.get(REPORTS, flt).content.decode()
        dismissed = client.get(REPORTS, {'status__exact': 'dismissed'}).content.decode()
        assert 'Findable door' not in dismissed
        assert (
            client.get(
                reverse('admin:qr_code_qrsharereport_change', args=[reported.pk])
            ).status_code
            == 200
        )

    def test_several_reports_of_one_code_take_it_down_once(self, client, superuser, qrcode, link):
        first = make_report(qrcode, link, reporter_ip='203.0.113.1')
        second = make_report(qrcode, link, reporter_ip='203.0.113.2', reason='phishing')
        page = act(client, REPORTS, 'take_down_action', [first, second])
        assert page.content.decode().count('<strong>Findable door</strong>') == 1
        response = act(
            client,
            REPORTS,
            'take_down_action',
            [first, second],
            apply='yes',
            reason='Phishing',
            follow=True,
        )
        assert 'Took down 1 QR code(s).' in response.content.decode()
        assert QRCode.objects.get(pk=qrcode.pk).is_taken_down
        assert set(QRShareReport.objects.values_list('status', flat=True)) == {
            ReportStatus.ACTIONED
        }
        assert LogEntry.objects.filter(object_id=str(qrcode.pk)).count() == 1
        for report in (first, second):
            entry = LogEntry.objects.get(object_id=str(report.pk))
            assert entry.get_change_message() == 'Actioned: QR code taken down'

    def test_dismiss_marks_the_report_and_logs_it(self, client, superuser, reported):
        response = act(client, REPORTS, 'dismiss_action', [reported], follow=True)
        assert 'Dismissed 1 report(s).' in response.content.decode()
        reported.refresh_from_db()
        assert reported.status == ReportStatus.DISMISSED and reported.reviewed_by == superuser
        assert reported.resolution_note == 'Dismissed via admin.'
        assert not QRCode.objects.get(pk=reported.qrcode_id).is_taken_down
        assert LogEntry.objects.filter(object_id=str(reported.pk)).exists()

    def test_an_already_reviewed_report_is_not_counted(self, client, superuser, reported):
        act(client, REPORTS, 'dismiss_action', [reported])
        response = act(client, REPORTS, 'dismiss_action', [reported], follow=True)
        assert 'Dismissed 0 report(s).' in response.content.decode()

    def test_it_is_read_only(self, client, superuser, reported):
        assert client.get(reverse('admin:qr_code_qrsharereport_add')).status_code == 403
        delete = reverse('admin:qr_code_qrsharereport_delete', args=[reported.pk])
        assert client.get(delete).status_code == 403
        change = reverse('admin:qr_code_qrsharereport_change', args=[reported.pk])
        assert client.post(change, {}).status_code == 403

    def test_deleting_a_code_or_a_user_takes_its_reports(self, client, superuser, user, reported):
        delete = reverse('admin:qr_code_qrcode_delete', args=[reported.qrcode_id])
        assert 'cannot be deleted' not in client.get(delete).content.decode()
        assert client.post(delete, {'post': 'yes'}).status_code == 302
        assert not QRShareReport.objects.exists()
        other = make_qrcode(user)
        make_report(other)
        response = client.post(
            reverse('admin:accounts_user_delete', args=[user.pk]), {'post': 'yes'}
        )
        assert response.status_code == 302 and not QRShareReport.objects.exists()

    def test_staff_who_may_delete_codes_and_links_are_not_blocked_by_the_reports(
        self, client, reported
    ):
        staff_with(
            client,
            'qr_code.view_qrcode',
            'qr_code.delete_qrcode',
            'qr_code.delete_qrsharelink',
            'qr_code.delete_qrsharereport',
        )
        delete = reverse('admin:qr_code_qrcode_delete', args=[reported.qrcode_id])
        assert 'cannot be deleted' not in client.get(delete).content.decode()

    def test_staff_without_the_report_permission_are_still_refused(self, client, reported):
        staff_with(
            client, 'qr_code.view_qrcode', 'qr_code.delete_qrcode', 'qr_code.delete_qrsharelink'
        )
        delete = reverse('admin:qr_code_qrcode_delete', args=[reported.qrcode_id])
        assert 'QR share report' in client.get(delete).content.decode()
        assert QRCode.objects.filter(pk=reported.qrcode_id).exists()

    def test_staff_who_may_delete_users_are_not_blocked_by_the_reports(
        self, client, user, reported
    ):
        staff_with(
            client,
            'accounts.view_user',
            'accounts.delete_user',
            'qr_code.delete_qrcode',
            'qr_code.delete_qrsharelink',
            'qr_code.delete_qrsharereport',
        )
        delete = reverse('admin:accounts_user_delete', args=[user.pk])
        assert 'cannot be deleted' not in client.get(delete).content.decode()


class TestPermissions:
    """A plain admin action ignores `has_change_permission`, so each one names its own."""

    def test_a_superuser_gets_the_actions(self, client, superuser, qrcode, reported):
        assert 'value="take_down_action"' in client.get(QRCODES).content.decode()
        page = client.get(REPORTS).content.decode()
        assert 'value="take_down_action"' in page and 'value="dismiss_action"' in page

    def test_view_only_staff_get_none_of_them(self, client, qrcode, reported):
        staff_with(client, 'qr_code.view_qrcode', 'qr_code.view_qrsharereport')
        assert 'value="take_down_action"' not in client.get(QRCODES).content.decode()
        page = client.get(REPORTS).content.decode()
        assert 'value="take_down_action"' not in page and 'value="dismiss_action"' not in page

    def test_view_only_staff_cannot_run_them_by_posting_either(self, client, qrcode, reported):
        staff_with(client, 'qr_code.view_qrcode', 'qr_code.view_qrsharereport')
        act(client, QRCODES, 'take_down_action', [qrcode], apply='yes', reason='x')
        act(client, REPORTS, 'take_down_action', [reported], apply='yes', reason='x')
        act(client, REPORTS, 'dismiss_action', [reported])
        assert not QRCode.objects.get(pk=qrcode.pk).is_taken_down
        reported.refresh_from_db()
        assert reported.status == ReportStatus.PENDING

    def test_the_change_permission_alone_does_not_unlock_them(self, client, qrcode, reported):
        staff_with(
            client,
            'qr_code.view_qrcode',
            'qr_code.change_qrcode',
            'qr_code.view_qrsharereport',
        )
        act(client, QRCODES, 'take_down_action', [qrcode], apply='yes', reason='x')
        assert not QRCode.objects.get(pk=qrcode.pk).is_taken_down

    def test_the_takedown_permission_unlocks_take_down_and_dismiss(self, client, qrcode, reported):
        member = staff_with(
            client, 'qr_code.view_qrcode', 'qr_code.view_qrsharereport', 'qr_code.takedown_qrcode'
        )
        act(client, REPORTS, 'dismiss_action', [reported])
        reported.refresh_from_db()
        assert reported.status == ReportStatus.DISMISSED and reported.reviewed_by == member
        act(client, QRCODES, 'take_down_action', [qrcode], apply='yes', reason='x')
        assert QRCode.objects.get(pk=qrcode.pk).is_taken_down

    def test_the_permission_exists_on_the_qr_code_model(self):
        assert Permission.objects.filter(
            content_type__app_label='qr_code', codename='takedown_qrcode'
        ).exists()
