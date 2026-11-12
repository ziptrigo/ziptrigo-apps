"""The admin's Notes tooling for sharing and abuse: share links (never a full code), the report
list, the Take down and Dismiss actions behind `notes.takedown_note` and a confirmation page, and
the History line each leaves."""

import pytest
from django.contrib.admin.helpers import ACTION_CHECKBOX_NAME
from django.contrib.admin.models import LogEntry
from django.contrib.auth.models import Permission
from django.urls import reverse

from apps.accounts.models import User

from ..models import Note, NoteReport, ReportStatus
from ..services import create_note, links, takedown
from .factories import make_report, make_user

pytestmark = pytest.mark.django_db

NOTES = reverse('admin:notes_note_changelist')
REPORTS = reverse('admin:notes_notereport_changelist')
LINKS = reverse('admin:notes_sharelink_changelist')


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
def note(user, config):
    return create_note(user, title='Findable bad note', content='x')


@pytest.fixture
def link(note):
    return links.create_link(note, mode='edit', label='team')


@pytest.fixture
def reported(note, link):
    return make_report(note, link, details='looks like phishing', reporter_email='r@example.com')


class TestShareLinkAdmin:
    def test_the_code_is_never_shown_in_full(self, client, superuser, link):
        for page in (
            client.get(LINKS),
            client.get(reverse('admin:notes_sharelink_change', args=[link.pk])),
            client.get(reverse('admin:notes_note_change', args=[link.note_id])),
            client.get(REPORTS),
        ):
            assert page.status_code == 200
            assert link.code not in page.content.decode()
        assert f'{link.code[:6]}...' in client.get(LINKS).content.decode()

    @pytest.mark.parametrize(
        'url_name, query',
        [
            ('admin:notes_sharelink_changelist', 'code__startswith={start}'),
            ('admin:notes_sharelink_changelist', 'code__exact={code}'),
            ('admin:notes_sharelink_changelist', 'code__contains={start}'),
            ('admin:notes_notereport_changelist', 'link__code__startswith={start}'),
            ('admin:notes_notereport_changelist', 'link__code={code}'),
            ('admin:notes_note_changelist', 'links__code__startswith={start}'),
            ('admin:notes_note_changelist', 'links__code={code}'),
            ('admin:notes_note_changelist', 'versions__link__code__startswith={start}'),
        ],
    )
    def test_no_lookup_reaches_a_code(self, client, superuser, reported, link, url_name, query):
        """`?code__startswith=` would narrow a full code down one character at a time (Django
        accepts any lookup on a local field), so a lookup with a `code` part is refused outright:
        even a superuser gets the admin's "bad lookup" answer, never a filtered list."""
        url = reverse(url_name) + '?' + query.format(start=link.code[:1], code=link.code)
        response = client.get(url)
        assert response.status_code in (400, 302)
        if response.status_code == 302:  # the changelist's "?e=1" redirect for a bad lookup
            assert 'e=1' in response['Location']

    def test_a_lookup_without_code_still_works(self, client, superuser, link):
        assert client.get(LINKS + '?mode__exact=edit').status_code == 200

    def test_the_list_shows_note_owner_mode_and_state(self, client, superuser, link, user):
        body = client.get(LINKS).content.decode()
        assert 'Findable bad note' in body and user.email in body and 'active' in body
        links.revoke_link(type(link).objects.select_related('note__user').get(pk=link.pk))
        assert 'revoked' in client.get(LINKS).content.decode()

    def test_the_code_cannot_be_searched_for(self, client, superuser, link):
        assert 'Findable bad note' in client.get(LINKS).content.decode()
        body = client.get(LINKS, {'q': link.code}).content.decode()
        assert 'Findable bad note' not in body

    def test_it_is_read_only(self, client, superuser, link):
        assert client.get(reverse('admin:notes_sharelink_add')).status_code == 403
        assert (
            client.get(reverse('admin:notes_sharelink_delete', args=[link.pk])).status_code == 403
        )
        assert (
            client.post(reverse('admin:notes_sharelink_change', args=[link.pk]), {}).status_code
            == 403
        )


class TestTakeDownFromTheNoteList:
    def test_the_first_step_only_asks(self, client, superuser, note):
        response = act(client, NOTES, 'take_down_action', [note])
        body = response.content.decode()
        assert (
            response.status_code == 200
            and 'Take down note(s)' in body
            and 'Findable bad note' in body
        )
        assert 'name="reason"' in body and 'name="internal_note"' in body
        assert 'notify' not in body.lower().replace('not emailed', '')
        assert not Note.objects.get(pk=note.pk).is_taken_down

    def test_confirming_takes_it_down_with_the_record_and_a_history_line(
        self, client, superuser, note
    ):
        response = act(
            client,
            NOTES,
            'take_down_action',
            [note],
            apply='yes',
            reason='Phishing page',
            internal_note='ticket 12',
            follow=True,
        )
        fresh = Note.objects.get(pk=note.pk)
        assert fresh.is_taken_down and fresh.taken_down_by == superuser
        assert (fresh.takedown_reason, fresh.takedown_internal_note) == (
            'Phishing page',
            'ticket 12',
        )
        assert 'Took down 1 note(s).' in response.content.decode()
        entry = LogEntry.objects.get(object_id=str(note.pk))
        assert entry.get_change_message() == 'Taken down: Phishing page'

    def test_a_reason_is_required(self, client, superuser, note):
        response = act(client, NOTES, 'take_down_action', [note], apply='yes', reason='')
        assert response.status_code == 200
        assert not Note.objects.get(pk=note.pk).is_taken_down

    def test_an_already_taken_down_note_is_skipped_and_said_so(
        self, client, superuser, note, user, config
    ):
        done = create_note(user, title='Already')
        takedown.take_down_note(done, by=superuser, reason='first')
        response = act(
            client,
            NOTES,
            'take_down_action',
            [note, done],
            apply='yes',
            reason='again',
            follow=True,
        )
        assert 'Took down 1 note(s). 1 had already been taken down.' in response.content.decode()
        assert Note.objects.get(pk=done.pk).takedown_reason == 'first'

    def test_pending_reports_are_actioned_and_the_owner_is_not_emailed(
        self, client, superuser, note, reported, mailoutbox, django_capture_on_commit_callbacks
    ):
        with django_capture_on_commit_callbacks(execute=True):
            act(client, NOTES, 'take_down_action', [note], apply='yes', reason='x')
        reported.refresh_from_db()
        assert reported.status == ReportStatus.ACTIONED and reported.reviewed_by == superuser
        assert mailoutbox == []

    def test_the_list_shows_and_filters_taken_down_notes(
        self, client, superuser, note, user, config
    ):
        other = create_note(user, title='Fine note')
        takedown.take_down_note(note, by=superuser, reason='x')
        body = client.get(NOTES, {'taken_down': 'yes'}).content.decode()
        assert 'Findable bad note' in body and 'Fine note' not in body
        assert other.pk


class TestTheReportList:
    def test_it_lists_with_a_link_to_the_note_and_filters(self, client, superuser, note, reported):
        body = client.get(REPORTS).content.decode()
        assert (
            'Findable bad note' in body
            and reverse('admin:notes_note_change', args=[note.pk]) in body
        )
        assert 'r@example.com' in body and '203.0.113.9' in body
        for flt in ({'status__exact': 'pending'}, {'reason__exact': 'spam'}):
            assert 'Findable bad note' in client.get(REPORTS, flt).content.decode()
        assert (
            'Findable bad note'
            not in client.get(REPORTS, {'status__exact': 'dismissed'}).content.decode()
        )
        assert (
            client.get(reverse('admin:notes_notereport_change', args=[reported.pk])).status_code
            == 200
        )

    def test_several_reports_of_one_note_take_it_down_once(self, client, superuser, note, link):
        first = make_report(note, link, reporter_ip='203.0.113.1')
        second = make_report(note, link, reporter_ip='203.0.113.2', reason='phishing')
        page = act(client, REPORTS, 'take_down_action', [first, second])
        assert page.content.decode().count('<strong>Findable bad note</strong>') == 1
        response = act(
            client,
            REPORTS,
            'take_down_action',
            [first, second],
            apply='yes',
            reason='Phishing',
            follow=True,
        )
        assert 'Took down 1 note(s).' in response.content.decode()
        assert Note.objects.get(pk=note.pk).is_taken_down
        assert set(NoteReport.objects.values_list('status', flat=True)) == {ReportStatus.ACTIONED}

    def test_dismiss_marks_the_report_and_logs_it(self, client, superuser, reported):
        response = act(client, REPORTS, 'dismiss_action', [reported], follow=True)
        assert 'Dismissed 1 report(s).' in response.content.decode()
        reported.refresh_from_db()
        assert reported.status == ReportStatus.DISMISSED and reported.reviewed_by == superuser
        assert reported.resolution_note == 'Dismissed via admin.'
        assert not Note.objects.get(pk=reported.note_id).is_taken_down
        assert LogEntry.objects.filter(object_id=str(reported.pk)).exists()

    def test_an_already_reviewed_report_is_not_counted(self, client, superuser, reported):
        act(client, REPORTS, 'dismiss_action', [reported])
        response = act(client, REPORTS, 'dismiss_action', [reported], follow=True)
        assert 'Dismissed 0 report(s).' in response.content.decode()

    def test_it_is_read_only(self, client, superuser, reported):
        assert client.get(reverse('admin:notes_notereport_add')).status_code == 403
        assert (
            client.get(reverse('admin:notes_notereport_delete', args=[reported.pk])).status_code
            == 403
        )


class TestPermissions:
    """A plain admin action ignores `has_change_permission`, so each one names its own."""

    def test_a_superuser_gets_the_actions(self, client, superuser, note, reported):
        assert 'value="take_down_action"' in client.get(NOTES).content.decode()
        page = client.get(REPORTS).content.decode()
        assert 'value="take_down_action"' in page and 'value="dismiss_action"' in page

    def test_view_only_staff_get_none_of_them(self, client, note, reported):
        staff_with(client, 'notes.view_note', 'notes.view_notereport')
        assert 'value="take_down_action"' not in client.get(NOTES).content.decode()
        page = client.get(REPORTS).content.decode()
        assert 'value="take_down_action"' not in page and 'value="dismiss_action"' not in page

    def test_view_only_staff_cannot_run_them_by_posting_either(self, client, note, reported):
        staff_with(client, 'notes.view_note', 'notes.view_notereport')
        act(client, NOTES, 'take_down_action', [note], apply='yes', reason='x')
        act(client, REPORTS, 'take_down_action', [reported], apply='yes', reason='x')
        act(client, REPORTS, 'dismiss_action', [reported])
        assert not Note.objects.get(pk=note.pk).is_taken_down
        reported.refresh_from_db()
        assert reported.status == ReportStatus.PENDING

    def test_the_takedown_permission_unlocks_take_down_and_dismiss(self, client, note, reported):
        member = staff_with(
            client, 'notes.view_note', 'notes.view_notereport', 'notes.takedown_note'
        )
        act(client, REPORTS, 'dismiss_action', [reported])
        reported.refresh_from_db()
        assert reported.status == ReportStatus.DISMISSED and reported.reviewed_by == member
        act(client, NOTES, 'take_down_action', [note], apply='yes', reason='x')
        assert Note.objects.get(pk=note.pk).is_taken_down

    def test_the_permission_exists_on_the_note_model(self):
        assert Permission.objects.filter(
            content_type__app_label='notes', codename='takedown_note'
        ).exists()
