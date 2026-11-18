"""Takedown of a QR code: the service (conditional, final, records, actions reports), what it does
to the code's links and sharing, what the owner sees, and that a tracked code's `/go/` redirect is
untouched."""

from datetime import timedelta

import pytest
from django.core.exceptions import ValidationError
from django.template.loader import render_to_string
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.core.models import Notification
from apps.qr_code.models import QRCode, QRShareLink, QRShareRecipient, ReportStatus
from apps.qr_code.services import QRCodeTakenDownError, share, share_emails, takedown

from .share_support import make_link, make_qrcode, make_report, make_user

pytestmark = [pytest.mark.django_db, pytest.mark.unit]

HTMX = {'HTTP_HX_REQUEST': 'true'}
TAKEN_DOWN = 'This QR code was taken down and cannot be shared.'


def url(name, *args):
    return reverse(f'qr_code:{name}', args=args)


def text(response) -> str:
    return response.content.decode()


@pytest.fixture(autouse=True)
def _media_root(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path


@pytest.fixture
def staff():
    return make_user(is_staff=True, email='staff@example.com')


@pytest.fixture
def qrcode(user):
    return make_qrcode(user, name='Bad door')


def reload(qrcode) -> QRCode:
    return QRCode.objects.select_related('created_by').get(pk=qrcode.pk)


class TestService:
    def test_it_records_who_when_and_why(self, qrcode, staff):
        assert takedown.take_down_qrcode(
            qrcode, by=staff, reason='Phishing', internal_note='ticket 4'
        )
        fresh = reload(qrcode)
        assert fresh.is_taken_down and fresh.taken_down_by == staff
        assert (fresh.takedown_reason, fresh.takedown_internal_note) == ('Phishing', 'ticket 4')
        assert fresh.taken_down_at is not None
        assert (qrcode.takedown_reason, qrcode.is_taken_down) == ('Phishing', True)  # the instance

    def test_a_new_code_is_not_taken_down(self, qrcode):
        assert not qrcode.is_taken_down
        assert (qrcode.takedown_reason, qrcode.takedown_internal_note) == ('', '')

    def test_it_is_final_and_a_second_call_records_nothing(self, qrcode, staff):
        takedown.take_down_qrcode(qrcode, by=staff, reason='first')
        other = make_user(is_staff=True)
        assert takedown.take_down_qrcode(qrcode, by=other, reason='second') is False
        fresh = reload(qrcode)
        assert (fresh.takedown_reason, fresh.taken_down_by) == ('first', staff)

    def test_a_stale_instance_does_not_overwrite_and_is_refreshed(self, qrcode, staff):
        stale = reload(qrcode)
        takedown.take_down_qrcode(qrcode, by=staff, reason='first')
        assert takedown.take_down_qrcode(stale, by=staff, reason='second') is False
        assert stale.takedown_reason == 'first'

    def test_pending_reports_are_actioned_and_reviewed_ones_left_alone(self, qrcode, staff, user):
        pending = make_report(qrcode)
        dismissed = make_report(qrcode, status=ReportStatus.DISMISSED, reporter_ip='203.0.113.1')
        elsewhere = make_report(make_qrcode(user, name='Other'))
        takedown.take_down_qrcode(qrcode, by=staff, reason='x')
        pending.refresh_from_db()
        assert (pending.status, pending.reviewed_by) == (ReportStatus.ACTIONED, staff)
        assert pending.reviewed_at is not None
        dismissed.refresh_from_db()
        elsewhere.refresh_from_db()
        assert dismissed.status == ReportStatus.DISMISSED
        assert elsewhere.status == ReportStatus.PENDING

    def test_no_email_or_notification_is_sent_to_the_owner(
        self, qrcode, staff, mailoutbox, django_capture_on_commit_callbacks
    ):
        with django_capture_on_commit_callbacks(execute=True):
            takedown.take_down_qrcode(qrcode, by=staff, reason='x')
        assert mailoutbox == []
        assert Notification.objects.filter(user=qrcode.created_by).count() == 0

    def test_a_deleted_code_can_be_taken_down_too(self, qrcode, staff):
        qrcode.soft_delete()
        assert takedown.take_down_qrcode(qrcode, by=staff, reason='x')


class TestWhatItDoesToSharing:
    @pytest.fixture(autouse=True)
    def _down(self, qrcode, staff):
        self.link = share.create_link(qrcode, label='printer')
        takedown.take_down_qrcode(qrcode, by=staff, reason='Not allowed here', internal_note='x')
        self.qrcode = reload(qrcode)

    def test_its_links_stop_working(self):
        assert share.get_available(self.link.code) is None
        link = QRShareLink.objects.select_related('qrcode__created_by').get(pk=self.link.pk)
        assert link.is_active() and not share.is_available(link)

    def test_no_link_can_be_made_or_changed(self):
        with pytest.raises(QRCodeTakenDownError) as exc:
            share.create_link(self.qrcode)
        assert exc.value.messages == [TAKEN_DOWN]
        link = QRShareLink.objects.get(pk=self.link.pk)
        link.qrcode = self.qrcode
        for call in (
            lambda: share.update_link(link, label='x'),
            lambda: share.check_create(self.qrcode),
            lambda: share.check_update(link, label='x'),
        ):
            with pytest.raises(ValidationError) as caught:
                call()
            assert caught.value.messages == [TAKEN_DOWN]

    def test_a_stale_instance_is_refused_under_the_lock(self, qrcode):
        stale = QRCode.objects.select_related('created_by').get(pk=qrcode.pk)
        stale.taken_down_at = None  # what a page rendered before the takedown held
        with pytest.raises(ValidationError):
            share.create_link(stale)
        assert QRShareLink.objects.filter(qrcode=qrcode).count() == 1

    def test_revoking_stays_allowed(self):
        link = QRShareLink.objects.select_related('qrcode__created_by').get(pk=self.link.pk)
        assert share.revoke_link(link).revoked_at is not None

    def test_a_link_cannot_be_emailed(self):
        link = QRShareLink.objects.select_related('qrcode__created_by').get(pk=self.link.pk)
        charged = []
        with pytest.raises(QRCodeTakenDownError) as exc:
            share_emails.send_link(link, ['a@example.com'], charge=charged.append)
        assert exc.value.messages == [TAKEN_DOWN] and charged == []
        assert QRShareRecipient.objects.count() == 0

    def test_a_resend_is_refused_too(self):
        link = QRShareLink.objects.select_related('qrcode__created_by').get(pk=self.link.pk)
        person = QRShareRecipient.objects.create(link=link, email='a@example.com')
        person.link = link
        with pytest.raises(QRCodeTakenDownError):
            share_emails.resend(person)

    def test_the_email_task_sends_nothing(self, monkeypatch):
        sent: list = []
        monkeypatch.setattr(share_emails, 'send_email', lambda **kwargs: sent.append(kwargs))
        QRShareRecipient.objects.create(link=self.link, email='a@example.com')
        share_emails.send_share_email.call(str(self.link.pk), 'a@example.com')
        assert sent == []

    def test_the_public_pages_are_the_neutral_one(self):
        visitor = Client()
        expected = render_to_string('qr_code/unavailable.html')
        for route in ('q:page', 'q:image'):
            response = visitor.get(reverse(route, args=[self.link.code]))
            assert response.status_code == 404 and text(response) == expected
        assert 'taken down' not in expected.lower()

    def test_the_dashboards_icon_goes(self, web):
        body = text(web.get(url('dashboard')))
        assert 'title="Shared"' not in body


@pytest.fixture
def web(client, user):
    client.force_login(user)
    return client


class TestTheOwnersView:
    @pytest.fixture(autouse=True)
    def _down(self, web, qrcode, staff):
        self.link = share.create_link(qrcode, label='printer')
        takedown.take_down_qrcode(
            qrcode, by=staff, reason='Copyright\ncomplaint', internal_note='STAFF ONLY'
        )
        self.qrcode = reload(qrcode)
        self.web = web

    def test_the_dashboard_row_shows_the_reason_and_never_the_internal_note(self):
        body = text(self.web.get(url('dashboard')))
        assert 'data-taken-down' in body and 'Taken down.' in body
        assert 'Copyright<br>complaint' in body
        assert 'STAFF ONLY' not in body and 'staff@example.com' not in body

    def test_the_dialog_says_why_and_offers_only_revoke(self):
        body = text(self.web.get(url('share', self.qrcode.pk), **HTMX))
        assert 'id="qr-share-taken-down"' in body and 'This QR code was taken down.' in body
        assert 'Copyright<br>complaint' in body
        assert 'STAFF ONLY' not in body and 'staff@example.com' not in body
        assert 'Not working' in body
        for gone in (
            url('share-create', self.qrcode.pk),
            url('share-edit', self.qrcode.pk, self.link.pk),
            url('share-email', self.qrcode.pk, self.link.pk),
            'Copy link',
            'Send by email',
        ):
            assert gone not in body
        assert url('share-revoke', self.qrcode.pk, self.link.pk) in body
        assert 'title="Shared"' not in body

    def test_the_actions_are_refused_with_the_message(self):
        create = self.web.post(url('share-create', self.qrcode.pk), {}, **HTMX)
        assert create.status_code == 422 and TAKEN_DOWN in text(create)
        edit = self.web.post(
            url('share-edit', self.qrcode.pk, self.link.pk), {'label': 'x'}, **HTMX
        )
        assert edit.status_code == 422 and TAKEN_DOWN in text(edit)
        email = self.web.post(
            url('share-email', self.qrcode.pk, self.link.pk), {'emails': 'a@example.com'}, **HTMX
        )
        assert email.status_code == 422 and TAKEN_DOWN in text(email)
        assert QRShareLink.objects.filter(qrcode=self.qrcode).count() == 1
        assert QRShareRecipient.objects.count() == 0

    def test_a_plain_refused_action_flashes_the_message(self):
        response = self.web.post(url('share-create', self.qrcode.pk), {}, follow=True)
        assert TAKEN_DOWN in text(response)

    def test_revoking_works_and_is_not_rate_limited(self):
        response = self.web.post(url('share-revoke', self.qrcode.pk, self.link.pk), **HTMX)
        assert response.status_code == 200
        assert QRShareLink.objects.get(pk=self.link.pk).revoked_at is not None

    def test_the_owner_can_still_view_edit_download_and_delete_it(self):
        assert self.web.get(url('edit', self.qrcode.pk)).status_code == 200
        assert self.web.get(url('image', self.qrcode.pk)).status_code == 200
        assert self.web.get(url('image', self.qrcode.pk) + '?download=1').status_code == 200
        renamed = self.web.post(url('edit-submit', self.qrcode.pk), {'name': 'Renamed'}, **HTMX)
        assert renamed.status_code == 200
        assert self.web.post(url('delete', self.qrcode.pk)).status_code == 204
        assert QRCode.objects.get(pk=self.qrcode.pk).deleted_at is not None

    def test_a_rename_keeps_it_taken_down(self):
        self.web.post(url('edit-submit', self.qrcode.pk), {'name': 'Renamed'}, **HTMX)
        assert reload(self.qrcode).is_taken_down

    def test_a_duplicate_is_a_new_untouched_code(self):
        page = self.web.get(url('duplicate', self.qrcode.pk))
        assert page.status_code == 200 and 'taken down' not in text(page).lower()

    def test_another_user_still_gets_a_404(self):
        theirs = make_qrcode(make_user(), name='Theirs')
        takedown.take_down_qrcode(theirs, by=make_user(is_staff=True), reason='x')
        assert self.web.get(url('share', theirs.pk), **HTMX).status_code == 404

    def test_a_code_that_is_not_taken_down_shows_none_of_this(self, user):
        fine = make_qrcode(user, name='Fine door')
        body = text(self.web.get(url('share', fine.pk), **HTMX))
        assert 'taken down' not in body.lower() and url('share-create', fine.pk) in body


class TestTheRedirectIsUntouched:
    """A takedown does not stop a tracked code's `/go/<code>` redirect: it is printed on physical
    codes, and killing a redirect is a different decision."""

    def test_a_tracked_code_still_redirects_and_counts(self, user, staff):
        qr = make_qrcode(
            user,
            content='https://app.example.com/go/abc12345',
            original_url='https://example.com/long',
            use_url_shortening=True,
            short_code='abc12345',
        )
        link = share.create_link(qr)
        takedown.take_down_qrcode(qr, by=staff, reason='x')
        response = Client().get('/go/abc12345')
        assert response.status_code == 302 and response['Location'] == 'https://example.com/long'
        qr.refresh_from_db()
        assert qr.scan_count == 1 and qr.is_taken_down
        assert Client().get(reverse('q:page', args=[link.code])).status_code == 404


class TestWhenSomethingElseIsDeadToo:
    def test_a_revoked_expired_and_taken_down_link_are_all_the_same_page(self, user, staff):
        qr = make_qrcode(user)
        revoked = share.create_link(qr)
        revoked.qrcode = qr
        share.revoke_link(revoked)
        expired = make_link(qr, expires_at=timezone.now() - timedelta(seconds=1))
        down = make_qrcode(user, name='Down')
        on_down = share.create_link(down)
        takedown.take_down_qrcode(down, by=staff, reason='x')
        bodies = {
            text(Client().get(reverse('q:page', args=[link.code])))
            for link in (revoked, expired, on_down)
        }
        assert bodies == {render_to_string('qr_code/unavailable.html')}
