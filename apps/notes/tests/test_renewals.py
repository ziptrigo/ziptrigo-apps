"""Renewals (issue #260, PR 3): charging, the period, locking and its notice and email, resuming
(oldest first, stop at the first unaffordable), the `credits_added` receiver, the warning once per
window, the daily job and its snapshot rule, trashed notes, and what the pages show."""

from datetime import UTC, datetime, timedelta

import pytest
from django.apps import apps as django_apps
from django.core.exceptions import ValidationError
from django.urls import reverse
from django.utils import timezone

from apps.billing.models import CreditTransaction
from apps.billing.services import add_credits, get_balance, spend_credits
from apps.core.models import Notification
from apps.core.scheduler import get_jobs

from .. import jobs
from ..models import Note, NotesAccount
from ..services import (
    Editor,
    archive,
    billing,
    create_note,
    delete_forever,
    get_account,
    quote_restore,
    restore,
    save_note,
    set_tags,
    toggle_checklist,
    trash,
)
from ..services import emails as emails_module
from .factories import make_note, make_tag, make_user, utc

pytestmark = pytest.mark.django_db

NOW = datetime(2026, 11, 11, 12, 0, tzinfo=UTC)


def paid_note(user, *, due=NOW - timedelta(hours=1), **fields) -> Note:
    """A paid note whose renewal fell due at `due`."""
    return make_note(user, is_free=False, paid_through=due, **fields)


def locked_note(user, *, locked_at=None, **fields) -> Note:
    fields.setdefault('due', NOW - timedelta(days=2))
    return paid_note(user, locked_at=locked_at or NOW - timedelta(days=1), **fields)


def drain(user, amount: int) -> None:
    """Spend the user's whole balance but `amount`."""
    spend = get_balance(user) - amount
    if spend:
        spend_credits(user, spend, source='test', description='Drain')


@pytest.fixture
def poor_user(user, config):
    """`user` with exactly 5 credits (one renewal)."""
    add_credits(user, 5, description='Test top-up', source='test')
    return user


def ledger(user):
    return list(
        CreditTransaction.objects.filter(user=user, source='notes').order_by('created_at', 'pk')
    )


class TestRenewDue:
    def test_a_due_note_is_charged_and_moves_on_one_period(self, funded_user):
        due = NOW - timedelta(days=1)
        note = paid_note(funded_user, due=due, title='Rent')
        result = billing.renew_due(NOW)
        assert (result.renewed, result.locked_count) == (1, 0)
        note.refresh_from_db()
        assert note.paid_through == due + timedelta(days=365)
        assert note.locked_at is None
        assert get_balance(funded_user) == 95
        [line] = ledger(funded_user)
        assert (line.amount, line.description) == (-5, 'Notes renewal: "Rent"')

    def test_the_ledger_title_is_cut_to_60_characters(self, funded_user):
        paid_note(funded_user, title='x' * 100)
        billing.renew_due(NOW)
        assert ledger(funded_user)[0].description == f'Notes renewal: "{"x" * 60}"'

    def test_the_period_is_the_settings_renewal_days(self, funded_user, config):
        config.renewal_days = 30
        config.save()
        note = paid_note(funded_user, due=NOW - timedelta(hours=1))
        billing.renew_due(NOW)
        note.refresh_from_db()
        assert note.paid_through == NOW - timedelta(hours=1) + timedelta(days=30)

    def test_a_note_long_overdue_gets_one_period_per_run(self, funded_user):
        due = NOW - timedelta(days=800)
        note = paid_note(funded_user, due=due)
        billing.renew_due(NOW)
        note.refresh_from_db()
        assert note.paid_through == due + timedelta(days=365)  # still before NOW
        billing.renew_due(NOW)
        note.refresh_from_db()
        assert note.paid_through == due + timedelta(days=730)
        assert get_balance(funded_user) == 90

    def test_a_price_of_zero_renews_without_a_ledger_line(self, funded_user, config):
        config.renewal_price = 0
        config.save()
        note = paid_note(funded_user, due=NOW - timedelta(days=1))
        before = get_balance(funded_user)
        billing.renew_due(NOW)
        note.refresh_from_db()
        assert note.paid_through == NOW - timedelta(days=1) + timedelta(days=365)
        assert get_balance(funded_user) == before and ledger(funded_user) == []

    def test_a_price_of_zero_never_locks(self, user, config):
        config.renewal_price = 0
        config.save()
        note = paid_note(user)  # a user with no credits at all
        billing.renew_due(NOW)
        note.refresh_from_db()
        assert note.locked_at is None

    def test_only_due_paid_unlocked_notes_outside_the_trash_are_touched(self, funded_user):
        not_yet = paid_note(funded_user, due=NOW + timedelta(seconds=1))
        free = make_note(funded_user, is_free=True)
        trashed = paid_note(funded_user, deleted_at=NOW - timedelta(days=1))
        already = locked_note(funded_user)
        before = {n.pk: (n.paid_through, n.locked_at) for n in (not_yet, free, trashed, already)}
        result = billing.renew_due(NOW)
        assert (result.renewed, result.locked_count) == (0, 0)
        for note in (not_yet, free, trashed, already):
            note.refresh_from_db()
            assert (note.paid_through, note.locked_at) == before[note.pk]
        assert get_balance(funded_user) == 100

    def test_a_note_due_exactly_now_renews(self, funded_user):
        paid_note(funded_user, due=NOW)
        assert billing.renew_due(NOW).renewed == 1

    def test_archived_notes_renew_like_active_ones(self, funded_user):
        note = paid_note(funded_user, archived_at=NOW - timedelta(days=9))
        assert billing.renew_due(NOW).renewed == 1
        note.refresh_from_db()
        assert note.archived_at is not None and note.locked_at is None

    def test_oldest_due_first_and_a_short_balance_locks_the_rest(self, poor_user):
        newer = paid_note(poor_user, due=NOW - timedelta(days=1), title='newer')
        older = paid_note(poor_user, due=NOW - timedelta(days=9), title='older')
        result = billing.renew_due(NOW)
        assert result.renewed == 1 and result.locked == {poor_user.pk: [newer.pk]}
        older.refresh_from_db()
        newer.refresh_from_db()
        assert older.locked_at is None and newer.locked_at == NOW

    def test_one_failing_note_is_logged_and_skipped(self, funded_user, monkeypatch, caplog):
        bad = paid_note(funded_user, due=NOW - timedelta(days=9), title='bad')
        good = paid_note(funded_user, due=NOW - timedelta(days=1), title='good')
        real = billing._renew_one

        def flaky(note_id, *args, **kwargs):
            if note_id == bad.pk:
                raise RuntimeError('boom')
            return real(note_id, *args, **kwargs)

        monkeypatch.setattr(billing, '_renew_one', flaky)
        result = billing.renew_due(NOW)
        assert result.renewed == 1
        good.refresh_from_db()
        bad.refresh_from_db()
        assert good.paid_through is not None and bad.paid_through is not None
        assert good.paid_through > NOW and bad.paid_through < NOW
        assert 'Could not renew note' in caplog.text

    def test_a_note_trashed_after_the_job_looked_is_left_alone(self, funded_user, monkeypatch):
        note = paid_note(funded_user)
        real_lock = billing.lock_account

        def trash_first(user_id):
            Note.objects.filter(pk=note.pk).update(deleted_at=NOW)
            real_lock(user_id)

        monkeypatch.setattr(billing, 'lock_account', trash_first)
        result = billing.renew_due(NOW)
        assert (result.renewed, result.locked_count) == (0, 0)
        assert get_balance(funded_user) == 100

    def test_two_runs_do_not_charge_the_same_period_twice(self, funded_user):
        paid_note(funded_user, due=NOW - timedelta(hours=1))
        billing.renew_due(NOW)
        billing.renew_due(NOW)
        assert get_balance(funded_user) == 95


class TestLocking:
    def test_an_unpayable_renewal_locks_the_note_and_charges_nothing(self, user, config):
        note = paid_note(user, title='Rent')
        result = billing.renew_due(NOW)
        assert result.locked == {user.pk: [note.pk]}
        note.refresh_from_db()
        assert note.locked_at == NOW
        assert note.paid_through == NOW - timedelta(hours=1)  # unchanged: the lock keeps the date
        assert get_balance(user) == 0 and ledger(user) == []

    def test_the_owner_gets_a_notice_per_locked_note(self, user, config):
        note = paid_note(user, title='Rent')
        billing.renew_due(NOW)
        notice = Notification.objects.get(user=user)
        assert notice.source == 'notes'
        assert notice.key == f'note_locked:{note.pk}'
        assert notice.title == 'Your note "Rent" is locked'
        assert notice.body == (
            'Its yearly renewal of 5 credits could not be paid. Top up to unlock it.'
        )
        assert notice.url == reverse('notes:note', args=[note.pk])

    def test_the_notice_title_cuts_the_note_title_to_60_characters(self, user, config):
        paid_note(user, title='y' * 150)
        billing.renew_due(NOW)
        assert Notification.objects.get(user=user).title == f'Your note "{"y" * 60}" is locked'

    def test_a_failing_notice_does_not_undo_the_lock(self, user, config, monkeypatch, caplog):
        note = paid_note(user)

        def boom(*args, **kwargs):
            raise RuntimeError('notice failed')

        monkeypatch.setattr('apps.notes.services.notices.notifications.notify', boom)
        billing.renew_due(NOW)
        note.refresh_from_db()
        assert note.locked_at == NOW
        assert 'Could not notify user' in caplog.text

    def test_one_email_per_user_per_run_lists_the_notes_locked(
        self, user, config, sent_emails, django_capture_on_commit_callbacks
    ):
        other = make_user()
        a = paid_note(user, title='Alpha', due=NOW - timedelta(days=3))
        b = paid_note(user, title='Beta', due=NOW - timedelta(days=2))
        c = paid_note(other, title='Gamma')
        with django_capture_on_commit_callbacks(execute=True):
            result = billing.renew_due(NOW)
        assert result.locked == {user.pk: [a.pk, b.pk], other.pk: [c.pk]}
        assert len(sent_emails) == 2
        mine = next(m for m in sent_emails if m['to'] == user.email)
        assert mine['subject'] == 'Some of your notes are locked'
        for title in ('Alpha', 'Beta'):
            assert title in mine['text_body'] and title in mine['html_body']
        assert 'Gamma' not in mine['text_body']
        assert '/billing/' in mine['text_body'] and '/notes/' in mine['text_body']
        assert 'Nothing was deleted' in mine['text_body']

    def test_the_email_uses_the_public_date_wording_in_utc(
        self, user, config, sent_emails, django_capture_on_commit_callbacks
    ):
        paid_note(user, due=datetime(2026, 11, 10, 23, 30, tzinfo=UTC))
        with django_capture_on_commit_callbacks(execute=True):
            billing.renew_due(NOW)
        [mail] = sent_emails
        assert 'renewal due Nov 10, 2026' in mail['text_body']
        assert 'renewal due Nov 10, 2026' in mail['html_body']

    def test_the_email_escapes_the_title_and_the_subject_has_none(
        self, user, config, sent_emails, django_capture_on_commit_callbacks
    ):
        paid_note(user, title='<script>alert(1)</script>')
        with django_capture_on_commit_callbacks(execute=True):
            billing.renew_due(NOW)
        [mail] = sent_emails
        assert '<script>' not in mail['html_body']
        assert '&lt;script&gt;' in mail['html_body']
        assert 'script' not in mail['subject']

    def test_no_email_to_an_unconfirmed_address(
        self, user, config, sent_emails, django_capture_on_commit_callbacks
    ):
        user.email_confirmed = False
        user.save()
        note = paid_note(user)
        with django_capture_on_commit_callbacks(execute=True):
            billing.renew_due(NOW)
        note.refresh_from_db()
        assert note.locked_at == NOW and sent_emails == []
        assert Notification.objects.filter(user=user).count() == 1  # the notice still goes

    def test_no_email_when_the_owner_topped_up_before_it_was_sent(
        self, user, config, sent_emails, django_capture_on_commit_callbacks
    ):
        note = paid_note(user)
        with django_capture_on_commit_callbacks(execute=False) as callbacks:
            billing.renew_due(NOW)
        Note.objects.filter(pk=note.pk).update(locked_at=None)
        for callback in callbacks:
            callback()
        assert sent_emails == []

    def test_an_email_naming_many_notes_lists_twenty_and_counts_the_rest(
        self, user, config, sent_emails
    ):
        notes = [locked_note(user, title=f'N{i:02d}') for i in range(23)]
        emails_module.send_notes_locked_email.call(str(user.pk), [str(n.pk) for n in notes])
        [mail] = sent_emails
        assert mail['text_body'].count('- N') == 20
        assert '...and 3 more.' in mail['text_body']

    def test_nothing_is_emailed_when_nothing_was_locked(
        self, funded_user, sent_emails, django_capture_on_commit_callbacks
    ):
        paid_note(funded_user)
        with django_capture_on_commit_callbacks(execute=True):
            billing.renew_due(NOW)
        assert sent_emails == []


class TestALockedNote:
    @pytest.fixture
    def note(self, user, config):
        note = create_note(user, title='Kept', content='- [ ] one\n')
        Note.objects.filter(pk=note.pk).update(is_free=False, paid_through=NOW, locked_at=NOW)
        note.refresh_from_db()
        return note

    def test_it_is_read_only(self, user, note):
        assert note.is_locked and note.is_read_only
        editor = Editor.for_user(note, user)
        with pytest.raises(ValidationError, match='locked until its renewal is paid'):
            save_note(note, editor=editor, content='new', base_version=note.version)
        with pytest.raises(ValidationError, match='locked'):
            toggle_checklist(note, 0, True, editor=editor)
        with pytest.raises(ValidationError, match='locked'):
            archive(note)
        with pytest.raises(ValidationError, match='locked'):
            set_tags(note, [make_tag(user).pk])

    def test_the_owner_can_still_trash_and_delete_it(self, user, note):
        trash(note)
        delete_forever(note)
        assert not Note.objects.filter(pk=note.pk).exists()

    def test_the_page_shows_the_banner_and_no_edit_controls(self, web, funded_user, note):
        Note.objects.filter(pk=note.pk).update(user=funded_user)
        page = web.get(reverse('notes:note', args=[note.pk])).content.decode()
        assert 'This note is locked until its renewal is paid.' in page
        assert reverse('billing:credits-history') in page
        assert reverse('notes:edit', args=[note.pk]) not in page
        assert 'Renewal unpaid since' in page

    def test_the_edit_page_and_save_are_refused(self, web, funded_user, note):
        Note.objects.filter(pk=note.pk).update(user=funded_user)
        response = web.post(
            reverse('notes:edit', args=[note.pk]),
            {'title': 'x', 'content': 'y', 'base_version': note.version},
        )
        assert response.status_code == 422
        note.refresh_from_db()
        assert note.title == 'Kept'

    def test_the_list_row_shows_the_lock(self, web, funded_user, note):
        Note.objects.filter(pk=note.pk).update(user=funded_user)
        assert 'Locked until its renewal is paid' in web.get(reverse('notes:list')).content.decode()


class TestResume:
    def test_oldest_locked_first_and_stop_at_the_first_unaffordable(self, user, config):
        oldest = locked_note(user, locked_at=NOW - timedelta(days=9), title='oldest')
        middle = locked_note(user, locked_at=NOW - timedelta(days=5), title='middle')
        newest = locked_note(user, locked_at=NOW - timedelta(days=1), title='newest')
        add_credits(user, 12, description='Top up', source='test')  # two renewals and a bit
        assert billing.resume(str(user.pk)) == 2
        for note in (oldest, middle, newest):
            note.refresh_from_db()
        assert oldest.locked_at is None and middle.locked_at is None
        assert newest.locked_at is not None
        assert get_balance(user) == 2

    def test_an_unlocked_note_is_charged_and_starts_a_period_now(self, user, config, freeze_now):
        moment = freeze_now(2026, 11, 11, 12, 0)
        note = locked_note(user, title='Rent')
        add_credits(user, 7, description='Top up', source='test')
        billing.resume(str(user.pk))
        note.refresh_from_db()
        assert note.locked_at is None
        assert note.paid_through == moment + timedelta(days=365)
        [line] = ledger(user)
        assert (line.amount, line.description) == (-5, 'Notes renewal: "Rent"')

    def test_the_locked_days_are_never_billed(self, user, config, freeze_now):
        freeze_now(2026, 11, 11, 12, 0)
        note = locked_note(user, due=NOW - timedelta(days=700))
        add_credits(user, 50, description='Top up', source='test')
        billing.resume(str(user.pk))
        note.refresh_from_db()
        assert get_balance(user) == 45  # one renewal, not one per locked year

    def test_its_notice_is_deleted_and_other_notes_keep_theirs(self, user, config):
        first = paid_note(user, title='first', due=NOW - timedelta(days=2))
        second = paid_note(user, title='second', due=NOW - timedelta(days=1))
        billing.renew_due(NOW)
        assert Notification.objects.filter(user=user).count() == 2
        Note.objects.filter(pk=first.pk).update(locked_at=NOW - timedelta(days=1))
        add_credits(user, 5, description='Top up', source='test')
        billing.resume(str(user.pk))
        keys = list(Notification.objects.filter(user=user).values_list('key', flat=True))
        assert keys == [f'note_locked:{second.pk}']
        first.refresh_from_db()
        assert first.locked_at is None

    def test_trashed_locked_notes_other_users_and_free_notes_are_skipped(self, user, config):
        other = make_user()
        trashed = locked_note(user, deleted_at=NOW)
        theirs = locked_note(other)
        add_credits(user, 50, description='Top up', source='test')
        add_credits(other, 1, description='Top up', source='test')
        assert billing.resume(str(user.pk)) == 0
        trashed.refresh_from_db()
        theirs.refresh_from_db()
        assert trashed.locked_at is not None and theirs.locked_at is not None

    def test_a_price_of_zero_unlocks_everything(self, user, config):
        config.renewal_price = 0
        config.save()
        notes = [locked_note(user) for _ in range(3)]
        assert billing.resume(str(user.pk)) == 3
        assert all(Note.objects.get(pk=n.pk).locked_at is None for n in notes)
        assert ledger(user) == []

    def test_only_limits_it_to_the_given_notes(self, user, config):
        a = locked_note(user, locked_at=NOW - timedelta(days=3))
        b = locked_note(user, locked_at=NOW - timedelta(days=2))
        add_credits(user, 50, description='Top up', source='test')
        assert billing.resume(str(user.pk), only={b.pk}) == 1
        a.refresh_from_db()
        b.refresh_from_db()
        assert a.locked_at is not None and b.locked_at is None

    def test_an_unknown_or_malformed_user_does_nothing(self, db):
        assert billing.resume('not-a-uuid') == 0
        assert billing.resume('00000000-0000-0000-0000-000000000000') == 0

    def test_a_note_that_is_no_longer_locked_is_skipped(self, user, config, monkeypatch):
        note = locked_note(user)
        add_credits(user, 50, description='Top up', source='test')
        real_lock = billing.lock_account

        def unlock_first(user_id):
            Note.objects.filter(pk=note.pk).update(locked_at=None)
            real_lock(user_id)

        monkeypatch.setattr(billing, 'lock_account', unlock_first)
        assert billing.resume(str(user.pk)) == 0
        assert get_balance(user) == 50


class TestTheCreditsAddedReceiver:
    def test_a_top_up_unlocks_the_users_notes(
        self, user, config, django_capture_on_commit_callbacks
    ):
        note = locked_note(user)
        with django_capture_on_commit_callbacks(execute=True):
            add_credits(user, 10, description='Top up', source='test')
        note.refresh_from_db()
        assert note.locked_at is None
        assert get_balance(user) == 5

    def test_nothing_is_queued_for_a_user_without_a_locked_note(
        self, funded_user, monkeypatch, django_capture_on_commit_callbacks
    ):
        queued = []
        monkeypatch.setattr(billing, 'enqueue_after_commit', lambda *args: queued.append(args))
        paid_note(funded_user, due=NOW + timedelta(days=30))
        locked_note(funded_user, deleted_at=NOW)  # trashed: doesn't count
        with django_capture_on_commit_callbacks(execute=True):
            add_credits(funded_user, 5, description='Top up', source='test')
        assert queued == []

    def test_the_resume_task_is_queued_for_the_user_with_a_locked_note(
        self, user, config, monkeypatch, django_capture_on_commit_callbacks
    ):
        queued = []
        monkeypatch.setattr(billing, 'enqueue_after_commit', lambda *args: queued.append(args))
        locked_note(user)
        with django_capture_on_commit_callbacks(execute=True):
            add_credits(user, 5, description='Top up', source='test')
        assert queued == [(billing.resume_notes, str(user.pk))]

    def test_a_failure_in_the_receiver_never_breaks_the_top_up(
        self, user, config, monkeypatch, caplog, django_capture_on_commit_callbacks
    ):
        def boom(*args):
            raise RuntimeError('no queue')

        monkeypatch.setattr(billing, 'on_credits_added', boom)
        with django_capture_on_commit_callbacks(execute=True):
            add_credits(user, 5, description='Top up', source='test')
        assert get_balance(user) == 5
        assert 'Could not queue the Notes resume' in caplog.text

    def test_the_receiver_is_registered_with_a_stable_uid(self):
        from apps.billing.signals import credits_added

        assert any(
            'notes.resume_on_credits_added' in str(receiver[0])
            for receiver in credits_added.receivers
        )


class TestWarnUpcoming:
    def due_in(self, days: float):
        return NOW + timedelta(days=days)

    def test_a_balance_short_of_the_coming_renewals_is_warned_once(
        self, poor_user, sent_emails, django_capture_on_commit_callbacks
    ):
        paid_note(poor_user, due=self.due_in(2), title='a')
        paid_note(poor_user, due=self.due_in(6), title='b')
        with django_capture_on_commit_callbacks(execute=True):
            assert billing.warn_upcoming(NOW) == 1
        notice = Notification.objects.get(user=poor_user)
        assert notice.key == 'notes_renewal_warning'
        assert notice.title == 'Notes renewal: not enough credits'
        assert (
            notice.body == '2 notes renew in the next 7 days for 10 credits and your balance is 5.'
        )
        assert notice.url == reverse('billing:credits-history')
        [mail] = sent_emails
        assert mail['subject'] == 'Your notes renew soon'
        assert mail['to'] == poor_user.email
        assert '2 of your notes renew in the next 7 days' in mail['text_body']
        assert '(the first on Nov 13, 2026)' in mail['text_body']
        assert 'balance is 5 credits' in mail['text_body']
        assert NotesAccount.objects.get(pk=poor_user.pk).renewal_warned_at == NOW

    def test_one_note_reads_in_the_singular(self, poor_user):
        drain(poor_user, 2)
        paid_note(poor_user, due=self.due_in(1))
        billing.warn_upcoming(NOW)
        assert Notification.objects.get().body == (
            '1 note renews in the next 7 days for 5 credits and your balance is 2.'
        )

    def test_not_again_within_the_window_but_again_after_it_replacing_the_notice(
        self, poor_user, sent_emails, django_capture_on_commit_callbacks
    ):
        paid_note(poor_user, due=self.due_in(3))
        paid_note(poor_user, due=self.due_in(4))
        with django_capture_on_commit_callbacks(execute=True):
            billing.warn_upcoming(NOW)
            assert billing.warn_upcoming(NOW + timedelta(days=3)) == 0
            assert billing.warn_upcoming(NOW + timedelta(days=7)) == 0  # exactly the window
            assert billing.warn_upcoming(NOW + timedelta(days=7, seconds=1)) == 1
        assert len(sent_emails) == 2
        assert Notification.objects.filter(user=poor_user, key='notes_renewal_warning').count() == 1

    def test_a_balance_that_covers_them_is_not_warned(self, user, config):
        add_credits(user, 10, description='Top up', source='test')
        paid_note(user, due=self.due_in(2))
        paid_note(user, due=self.due_in(3))
        assert billing.warn_upcoming(NOW) == 0  # 10 credits for 10 credits: enough
        assert not Notification.objects.exists()
        assert NotesAccount.objects.get(pk=user.pk).renewal_warned_at is None

    def test_only_paid_unlocked_notes_in_the_window_count(self, poor_user):
        paid_note(poor_user, due=self.due_in(8))  # outside the window
        make_note(poor_user, is_free=True)
        paid_note(poor_user, due=self.due_in(2), deleted_at=NOW)  # trashed
        locked_note(poor_user)  # already locked
        paid_note(poor_user, due=self.due_in(2))  # one note: 5 credits, the balance is 5
        assert billing.warn_upcoming(NOW) == 0

    def test_a_price_of_zero_never_warns(self, user, config):
        config.renewal_price = 0
        config.save()
        paid_note(user, due=self.due_in(1))
        assert billing.warn_upcoming(NOW) == 0

    def test_the_window_is_the_settings_value(self, poor_user, config):
        config.renewal_warning_days = 2
        config.save()
        paid_note(poor_user, due=self.due_in(1))
        paid_note(poor_user, due=self.due_in(1))
        paid_note(poor_user, due=self.due_in(3))
        billing.warn_upcoming(NOW)
        assert Notification.objects.get().body.startswith('2 notes renew in the next 2 days')

    def test_each_user_is_warned_and_one_failure_does_not_stop_the_rest(
        self, poor_user, monkeypatch, caplog
    ):
        other = make_user()
        add_credits(other, 1, description='Top up', source='test')
        for owner in (poor_user, other):
            paid_note(owner, due=self.due_in(1))
            paid_note(owner, due=self.due_in(1))
        real = billing._warn_user

        def flaky(user_id, *args, **kwargs):
            if user_id == other.pk:
                raise RuntimeError('boom')
            return real(user_id, *args, **kwargs)

        monkeypatch.setattr(billing, '_warn_user', flaky)
        assert billing.warn_upcoming(NOW) == 1
        assert Notification.objects.filter(user=poor_user).exists()
        assert 'Could not warn user' in caplog.text

    def test_no_account_row_yet_is_fine(self, user, config):
        paid_note(user, due=self.due_in(1))
        NotesAccount.objects.filter(pk=user.pk).delete()
        assert billing.warn_upcoming(NOW) == 1
        assert get_account(user).renewal_warned_at == NOW


class TestTheDailyJob:
    def test_it_is_registered_daily_with_a_description(self):
        django_apps.get_app_config('notes')._register_jobs()
        spec = {job.name: job for job in get_jobs()}['notes.renew']
        assert spec.interval == timedelta(hours=24) and spec.lease == timedelta(hours=1)
        assert spec.description.strip()
        assert spec.func is jobs.renew

    def test_it_renews_unlocks_and_warns(self, user, config, freeze_now, sent_emails):
        freeze_now(2026, 11, 11, 12, 0)
        add_credits(user, 12, description='Top up', source='test')
        earlier = locked_note(user, title='earlier')
        due = paid_note(user, title='due')
        soon = paid_note(user, title='soon', due=NOW + timedelta(days=2))
        soon2 = paid_note(user, title='soon2', due=NOW + timedelta(days=3))
        jobs.renew()
        for note in (earlier, due, soon, soon2):
            note.refresh_from_db()
        # 12 credits: `due` renewed first (7 left), `earlier` resumed (2 left).
        assert due.paid_through is not None
        assert due.paid_through > NOW and earlier.locked_at is None
        assert get_balance(user) == 2
        # Two more renewals within the week need 10 credits: warned.
        assert Notification.objects.get(user=user).key == 'notes_renewal_warning'

    def test_a_note_this_run_locked_is_not_resumed_in_the_same_run(
        self, user, config, freeze_now, monkeypatch
    ):
        freeze_now(2026, 11, 11, 12, 0)
        before = locked_note(user, title='before')
        this_run = paid_note(user, title='this run')
        calls = []
        monkeypatch.setattr(
            billing, 'resume', lambda user_id, only=None: calls.append((user_id, only)) or 0
        )
        jobs.renew()
        this_run.refresh_from_db()
        assert this_run.locked_at is not None
        assert calls == [(str(user.pk), {before.pk})]

    def test_a_user_without_earlier_locks_gets_no_resume(
        self, funded_user, freeze_now, monkeypatch
    ):
        freeze_now(2026, 11, 11, 12, 0)
        paid_note(funded_user)
        calls = []
        monkeypatch.setattr(billing, 'resume', lambda *args, **kwargs: calls.append(args))
        jobs.renew()
        assert calls == []

    def test_trashed_locked_notes_are_not_resumed(self, user, config, freeze_now):
        freeze_now(2026, 11, 11, 12, 0)
        add_credits(user, 50, description='Top up', source='test')
        note = locked_note(user, deleted_at=NOW - timedelta(days=1))
        jobs.renew()
        note.refresh_from_db()
        assert note.locked_at is not None and get_balance(user) == 50

    def test_a_failing_resume_is_logged_and_the_warning_step_still_runs(
        self, poor_user, config, freeze_now, monkeypatch, caplog
    ):
        freeze_now(2026, 11, 11, 12, 0)
        locked_note(poor_user)
        paid_note(poor_user, due=NOW + timedelta(days=1))
        paid_note(poor_user, due=NOW + timedelta(days=1))

        def boom(*args, **kwargs):
            raise RuntimeError('boom')

        monkeypatch.setattr(billing, 'resume', boom)
        jobs.renew()
        assert 'Could not resume the notes of user' in caplog.text
        assert Notification.objects.filter(user=poor_user, key='notes_renewal_warning').exists()

    def test_running_it_twice_is_harmless(self, funded_user, freeze_now):
        freeze_now(2026, 11, 11, 12, 0)
        paid_note(funded_user)
        jobs.renew()
        jobs.renew()
        assert get_balance(funded_user) == 95


class TestTheTrash:
    def test_a_trashed_note_is_not_charged_or_locked_on_its_anniversary(self, user, config):
        note = paid_note(user, deleted_at=NOW - timedelta(days=3))
        result = billing.renew_due(NOW)
        assert (result.renewed, result.locked_count) == (0, 0)
        note.refresh_from_db()
        assert note.locked_at is None and ledger(user) == []

    def test_restoring_charges_the_renewal_it_skipped(self, funded_user):
        note = paid_note(funded_user, deleted_at=NOW - timedelta(days=3))
        assert quote_restore(note) == 5
        restore(note, expected_cost=5)
        note.refresh_from_db()
        assert note.deleted_at is None and note.paid_through > timezone.now()
        assert get_balance(funded_user) == 95
        assert ledger(funded_user)[0].description.startswith('Notes renewal: ')

    def test_restoring_a_locked_note_unlocks_it_and_clears_its_notice(self, funded_user):
        note = paid_note(funded_user, due=NOW - timedelta(days=30))
        drain(funded_user, 0)
        billing.renew_due(NOW)
        assert Notification.objects.filter(key=f'note_locked:{note.pk}').exists()
        trash(note)
        add_credits(funded_user, 5, description='Top up', source='test')
        restore(note)
        note.refresh_from_db()
        assert note.locked_at is None and note.deleted_at is None
        assert not Notification.objects.filter(key=f'note_locked:{note.pk}').exists()


class TestThePages:
    def test_a_free_note_says_free(self, web, funded_user):
        note = create_note(funded_user, title='Free one')
        page = web.get(reverse('notes:note', args=[note.pk])).content.decode()
        assert 'data-renewal' in page and 'Free' in page
        assert 'Renews' not in page

    def test_a_paid_note_says_when_it_renews(self, web, funded_user):
        note = paid_note(funded_user, due=utc(2027, 3, 4, 12), title='Paid')
        page = web.get(reverse('notes:note', args=[note.pk])).content.decode()
        assert 'Renews 03/04/2027' in ' '.join(page.split())

    def test_a_locked_note_says_since_when(self, web, funded_user):
        note = locked_note(funded_user, due=utc(2026, 10, 1, 12))
        page = ' '.join(web.get(reverse('notes:note', args=[note.pk])).content.decode().split())
        assert 'Renewal unpaid since 10/01/2026' in page

    def test_a_trashed_paid_note_whose_renewal_fell_due_says_so_and_shows_the_restore_cost(
        self, web, funded_user
    ):
        note = paid_note(
            funded_user, due=timezone.now() - timedelta(days=2), deleted_at=timezone.now()
        )
        page = ' '.join(web.get(reverse('notes:note', args=[note.pk])).content.decode().split())
        assert 'Renewal was due' in page and 'Renews' not in page
        assert 'Restore for 5 credits' in page

    def test_the_trash_page_shows_what_a_restore_costs(self, web, funded_user):
        paid_note(funded_user, due=timezone.now() - timedelta(days=2), deleted_at=timezone.now())
        page = web.get(reverse('notes:trash')).content.decode()
        assert 'Restore for 5 credits' in page
