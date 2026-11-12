"""Takedown of a note: the service (conditional, final, records, actions reports) and what it does
to the note's owner (read-only, banner with the reason, no share, no copy) and its links."""

from datetime import timedelta

import pytest
from django.core.exceptions import ValidationError
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.billing.services import get_balance
from apps.core.models import Notification

from ..models import Note, NoteReport, ReportStatus
from ..services import (
    Editor,
    archive,
    billing,
    create_note,
    delete_forever,
    duplicate,
    links,
    reports,
    save_note,
    set_tags,
    takedown,
    toggle_checklist,
    trash,
)
from .factories import make_report, make_user

pytestmark = pytest.mark.django_db

TAKEN_DOWN = 'This note was taken down and can no longer be changed.'


@pytest.fixture
def staff():
    return make_user(is_staff=True, email='staff@example.com')


@pytest.fixture
def note(funded_user):
    return create_note(funded_user, title='Bad note', content='- [ ] a\n')


def reload(note) -> Note:
    return Note.objects.select_related('user').get(pk=note.pk)


class TestService:
    def test_it_records_who_when_and_why(self, note, staff):
        assert takedown.take_down_note(note, by=staff, reason='Phishing', internal_note='ticket 4')
        fresh = reload(note)
        assert fresh.is_taken_down and fresh.taken_down_by == staff
        assert (fresh.takedown_reason, fresh.takedown_internal_note) == ('Phishing', 'ticket 4')
        assert fresh.taken_down_at is not None
        assert (note.takedown_reason, note.is_taken_down) == ('Phishing', True)  # the instance too

    def test_it_is_final_and_a_second_call_records_nothing(self, note, staff):
        takedown.take_down_note(note, by=staff, reason='first')
        other = make_user(is_staff=True)
        assert takedown.take_down_note(note, by=other, reason='second') is False
        fresh = reload(note)
        assert (fresh.takedown_reason, fresh.taken_down_by) == ('first', staff)

    def test_pending_reports_are_actioned_and_reviewed_ones_left_alone(self, note, staff):
        pending = make_report(note)
        dismissed = make_report(note, status=ReportStatus.DISMISSED, reporter_ip='203.0.113.1')
        other_note = create_note(note.user, title='Other')
        elsewhere = make_report(other_note)
        takedown.take_down_note(note, by=staff, reason='x')
        pending.refresh_from_db()
        assert (pending.status, pending.reviewed_by) == (ReportStatus.ACTIONED, staff)
        assert pending.reviewed_at is not None
        dismissed.refresh_from_db()
        elsewhere.refresh_from_db()
        assert dismissed.status == ReportStatus.DISMISSED
        assert elsewhere.status == ReportStatus.PENDING

    def test_no_email_is_sent_to_the_owner(
        self, note, staff, mailoutbox, django_capture_on_commit_callbacks
    ):
        with django_capture_on_commit_callbacks(execute=True):
            takedown.take_down_note(note, by=staff, reason='x')
        assert mailoutbox == []

    def test_a_note_in_the_trash_can_be_taken_down_too(self, note, staff):
        trash(note)
        assert takedown.take_down_note(note, by=staff, reason='x')


class TestWhatItDoesToTheNote:
    @pytest.fixture(autouse=True)
    def _down(self, note, staff):
        self.link = links.create_link(note, mode='edit')
        takedown.take_down_note(note, by=staff, reason='Not allowed here', internal_note='secret')
        self.note = reload(note)

    def test_it_is_read_only(self):
        assert self.note.is_read_only
        owner = Editor.for_user(self.note, self.note.user)
        for call in (
            lambda: save_note(self.note, editor=owner, content='x', base_version=1),
            lambda: toggle_checklist(self.note, 0, True, editor=owner),
            lambda: set_tags(self.note, []),
            lambda: archive(self.note),
        ):
            with pytest.raises(ValidationError) as exc:
                call()
            assert exc.value.messages == [TAKEN_DOWN]

    def test_it_can_still_be_trashed_and_deleted(self):
        trash(self.note)
        delete_forever(self.note)
        assert not Note.objects.filter(pk=self.note.pk).exists()

    def test_it_cannot_be_copied(self, other_user):
        with pytest.raises(ValidationError) as exc:
            duplicate(self.note, other_user, keep_title=True)
        assert exc.value.messages == ['This note was taken down and cannot be copied.']
        stale = Note(
            pk=self.note.pk, user=self.note.user, title='Bad note'
        )  # predates the takedown
        with pytest.raises(ValidationError):
            duplicate(stale, other_user)

    def test_its_links_stop_working_and_none_can_be_made(self):
        assert links.get_available(self.link.code) is None
        with pytest.raises(ValidationError) as exc:
            links.create_link(self.note, mode='read')
        assert exc.value.messages == ['This note was taken down and cannot be shared.']

    def test_the_public_pages_are_the_neutral_one(self):
        visitor = Client()
        assert visitor.get(reverse('n:page', args=[self.link.code])).status_code == 404

    def test_it_keeps_renewing_until_then(self, funded_user):
        paid = create_note(funded_user, title='Paid', content='x')
        Note.objects.filter(pk=paid.pk).update(
            is_free=False, paid_through=timezone.now() - timedelta(hours=1)
        )
        takedown.take_down_note(paid, by=make_user(is_staff=True), reason='x')
        before = get_balance(funded_user)
        billing.renew_due(timezone.now())
        assert get_balance(funded_user) == before - 5
        assert reload(paid).paid_through is not None

    def test_a_report_after_it_is_refused(self):
        with pytest.raises(ValidationError):
            reports.create_report(self.link, reason='spam')
        assert NoteReport.objects.count() == 0

    def test_a_restore_from_the_trash_keeps_it_taken_down(self):
        from ..services import restore

        trash(self.note)
        restore(self.note)
        assert reload(self.note).is_taken_down


class TestTheOwnersView:
    @pytest.fixture(autouse=True)
    def _down(self, web, note, staff):
        self.link = links.create_link(note, mode='read')
        takedown.take_down_note(
            note, by=staff, reason='Copyright\ncomplaint', internal_note='STAFF ONLY'
        )
        self.note = reload(note)
        self.web = web

    def page(self) -> str:
        return self.web.get(reverse('notes:note', args=[self.note.pk])).content.decode()

    def test_the_banner_shows_the_reason_and_never_the_internal_note(self):
        body = self.page()
        assert 'This note was taken down.' in body and 'Copyright<br>complaint' in body
        assert 'STAFF ONLY' not in body and 'staff@example.com' not in body
        assert 'can no longer be changed, shared or copied' in body

    def test_there_is_no_edit_share_duplicate_or_archive(self):
        body = self.page()
        for gone in (
            reverse('notes:edit', args=[self.note.pk]),
            reverse('notes:share', args=[self.note.pk]),
            reverse('notes:duplicate', args=[self.note.pk]),
            reverse('notes:archive', args=[self.note.pk]),
            'Edit tags',
            'data-checklist-url',
        ):
            assert gone not in body
        for kept in (
            reverse('notes:download', args=[self.note.pk]),
            reverse('notes:trash-note', args=[self.note.pk]),
            reverse('notes:history', args=[self.note.pk]),
        ):
            assert kept in body

    def test_the_actions_are_refused_with_the_reason_in_a_message(self):
        edit = self.web.get(reverse('notes:edit', args=[self.note.pk]), follow=True)
        assert TAKEN_DOWN in edit.content.decode()
        dup = self.web.post(reverse('notes:duplicate', args=[self.note.pk]), follow=True)
        assert 'This note was taken down and cannot be copied.' in dup.content.decode()
        assert Note.objects.filter(user=self.note.user).count() == 1
        share = self.web.post(
            reverse('notes:share-create', args=[self.note.pk]),
            {'mode': 'read'},
            **{'HTTP_HX_REQUEST': 'true'},
        )
        assert share.status_code == 422 and 'taken down' in share.content.decode()
        archived = self.web.post(reverse('notes:archive', args=[self.note.pk]), follow=True)
        assert TAKEN_DOWN in archived.content.decode()

    def test_the_history_offers_no_restore(self):
        body = self.web.get(reverse('notes:history', args=[self.note.pk])).content.decode()
        assert 'Restore' not in body

    def test_the_list_row_is_marked(self):
        body = self.web.get(reverse('notes:list')).content.decode()
        assert 'Taken down' in body

    def test_the_checklist_endpoint_is_refused(self):
        response = self.web.post(
            reverse('notes:checklist', args=[self.note.pk]),
            {'index': 0, 'checked': 'true'},
            **{'HTTP_HX_REQUEST': 'true'},
        )
        assert response.status_code == 422 and TAKEN_DOWN in response.content.decode()

    def test_it_can_still_be_downloaded_trashed_and_deleted(self):
        assert self.web.get(reverse('notes:download', args=[self.note.pk])).status_code == 200
        self.web.post(reverse('notes:trash-note', args=[self.note.pk]))
        assert Note.objects.get(pk=self.note.pk).is_trashed
        self.web.post(reverse('notes:delete', args=[self.note.pk]))
        assert not Note.objects.filter(pk=self.note.pk).exists()

    def test_the_owner_gets_no_notification(self):
        assert Notification.objects.filter(user=self.note.user).count() == 0
