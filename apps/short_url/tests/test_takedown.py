"""Takedown: the conditional write, what it records, the optional owner email, and what the owner
sees (the reason, never the internal note nor who reported it)."""

import pytest
from django.core.exceptions import ValidationError

from apps.accounts.models import User

from ..models import LinkReport, LinkStatus, ReportStatus, ShortLink
from ..services import (
    delete_link,
    is_available,
    is_code_available,
    reports,
    set_enabled,
    take_down_link,
    update_link,
)
from ..services.emails import send_link_taken_down_email
from .factories import create_link, make_user

pytestmark = pytest.mark.django_db


@pytest.fixture
def staff(db):
    return make_user(is_staff=True, email='staff@example.com')


@pytest.fixture
def link(user):
    return create_link(user, code='Bad001', destination='https://phish.example.net/login')


@pytest.fixture
def sent(monkeypatch):
    """Every email the task would send, recorded instead."""
    mails: list[dict] = []
    monkeypatch.setattr(
        'apps.short_url.services.emails.send_email', lambda **kwargs: mails.append(kwargs)
    )
    return mails


class TestTakeDownLink:
    def test_an_active_link_is_taken_down_and_the_record_kept(self, link, staff):
        assert take_down_link(link, by=staff, reason='Phishing', internal_note='ticket 12')
        link.refresh_from_db()
        assert link.status == LinkStatus.TAKEN_DOWN
        assert link.taken_down_by == staff and link.taken_down_at is not None
        assert (link.takedown_reason, link.takedown_internal_note) == ('Phishing', 'ticket 12')
        assert not is_available(link)

    def test_the_instance_passed_in_is_updated(self, link, staff):
        take_down_link(link, by=staff, reason='Phishing')
        assert link.status == LinkStatus.TAKEN_DOWN and link.takedown_reason == 'Phishing'

    def test_a_disabled_link_can_be_taken_down(self, link, staff):
        ShortLink.objects.filter(pk=link.pk).update(status=LinkStatus.DISABLED)
        assert take_down_link(link, by=staff, reason='Phishing')
        link.refresh_from_db()
        assert link.status == LinkStatus.TAKEN_DOWN

    def test_it_clears_a_hold(self, link, staff):
        from django.utils import timezone

        ShortLink.objects.filter(pk=link.pk).update(held_for_review_at=timezone.now())
        link.refresh_from_db()
        take_down_link(link, by=staff, reason='Phishing')
        link.refresh_from_db()
        assert link.held_for_review_at is None

    def test_pending_reports_become_actioned_in_the_same_step(self, link, staff):
        pending = reports.create_report(link, reason='phishing', reporter_ip='203.0.113.1')
        dismissed = reports.create_report(link, reason='spam', reporter_ip='203.0.113.2')
        reports.dismiss_report(dismissed, by=staff, note='no')
        take_down_link(link, by=staff, reason='Phishing')
        pending.refresh_from_db()
        dismissed.refresh_from_db()
        assert pending.status == ReportStatus.ACTIONED
        assert pending.reviewed_by == staff and pending.reviewed_at is not None
        assert pending.resolution_note == 'Link taken down.'
        assert dismissed.status == ReportStatus.DISMISSED  # a decision already made stays

    @pytest.mark.parametrize('status', [LinkStatus.DELETED, LinkStatus.TAKEN_DOWN])
    def test_a_link_that_already_ended_is_left_alone(self, link, staff, status):
        ShortLink.objects.filter(pk=link.pk).update(status=status, takedown_reason='first')
        assert take_down_link(link, by=staff, reason='second', internal_note='n') is False
        link.refresh_from_db()
        assert link.status == status
        assert link.takedown_reason == 'first' and link.takedown_internal_note == ''
        assert link.taken_down_by is None

    def test_it_is_a_conditional_write_on_a_stale_read(self, link, staff):
        stale = ShortLink.objects.get(pk=link.pk)  # still thinks it is active
        delete_link(link)  # the owner deletes it meanwhile
        report = LinkReport.objects.create(link=link, reason='spam')
        assert take_down_link(stale, by=staff, reason='Phishing') is False
        assert stale.status == LinkStatus.DELETED  # refreshed to what won
        assert LinkReport.objects.get(pk=report.pk).status == ReportStatus.PENDING

    def test_a_losing_call_sends_no_email(
        self, link, staff, sent, django_capture_on_commit_callbacks
    ):
        ShortLink.objects.filter(pk=link.pk).update(status=LinkStatus.DELETED)
        with django_capture_on_commit_callbacks(execute=True):
            take_down_link(link, by=staff, reason='x', notify=True)
        assert sent == []

    def test_the_code_stays_reserved(self, link, staff):
        take_down_link(link, by=staff, reason='Phishing')
        assert not is_code_available('Bad001') and not is_code_available('bad001')

    def test_it_is_terminal_for_the_owner(self, link, staff, funded_user):
        mine = create_link(funded_user, code='Mine01')
        take_down_link(mine, by=staff, reason='Phishing')
        with pytest.raises(ValidationError, match='can no longer be changed'):
            update_link(mine, title='New')
        with pytest.raises(ValidationError, match='can no longer be changed'):
            set_enabled(mine, True)
        with pytest.raises(ValidationError, match='can no longer be changed'):
            set_enabled(mine, False)
        with pytest.raises(ValidationError, match='can no longer be changed'):
            delete_link(mine)


class TestOwnerEmail:
    def test_no_email_by_default(self, link, staff, sent, django_capture_on_commit_callbacks):
        with django_capture_on_commit_callbacks(execute=True):
            take_down_link(link, by=staff, reason='Phishing')
        assert sent == []

    def test_notify_queues_the_email_once_committed(
        self, link, user, staff, sent, django_capture_on_commit_callbacks
    ):
        with django_capture_on_commit_callbacks(execute=False) as callbacks:
            take_down_link(link, by=staff, reason='Phishing', notify=True)
        assert sent == []  # nothing before the transaction commits
        assert len(callbacks) == 1
        callbacks[0]()
        assert len(sent) == 1
        assert sent[0]['to'] == user.email

    def test_the_email_says_what_happened_and_why(self, link, user, staff, sent):
        take_down_link(link, by=staff, reason='Phishing page', internal_note='SECRET NOTE')
        send_link_taken_down_email.call(str(link.pk))
        mail = sent[0]
        assert mail['subject'] == 'One of your short links was removed'
        assert 'Bad001' not in mail['subject']  # nothing owner-controlled in a subject
        assert 'https://app.example.test/s/Bad001' in mail['text_body']
        assert 'Reason: Phishing page' in mail['text_body']
        assert f'/links/{link.pk}/' in mail['text_body']
        assert 'SECRET NOTE' not in mail['text_body'] + mail['html_body']

    def test_it_never_names_who_reported_it(self, link, staff, sent):
        reports.create_report(
            link,
            reason='phishing',
            reporter_email='reporter@elsewhere.org',
            reporter_ip='203.0.113.9',
        )
        take_down_link(link, by=staff, reason='Phishing page')
        send_link_taken_down_email.call(str(link.pk))
        body = sent[0]['text_body'] + sent[0]['html_body']
        assert 'reporter@elsewhere.org' not in body and '203.0.113.9' not in body

    def test_the_html_body_is_autoescaped(self, user, staff, sent):
        link = create_link(user, code='Esc001')
        take_down_link(link, by=staff, reason='<script>alert(1)</script> & more')
        send_link_taken_down_email.call(str(link.pk))
        html = sent[0]['html_body']
        assert '<script>' not in html
        assert '&lt;script&gt;' in html and '&amp; more' in html

    def test_an_unconfirmed_address_gets_nothing(self, link, user, staff, sent):
        User.objects.filter(pk=user.pk).update(email_confirmed=False)
        take_down_link(link, by=staff, reason='x')
        send_link_taken_down_email.call(str(link.pk))
        assert sent == []

    def test_a_deactivated_owner_gets_nothing(self, link, user, staff, sent):
        take_down_link(link, by=staff, reason='x')
        user.deactivate(reason='abuse')
        send_link_taken_down_email.call(str(link.pk))
        assert sent == []

    def test_a_link_that_is_not_taken_down_sends_nothing(self, link, sent):
        send_link_taken_down_email.call(str(link.pk))
        send_link_taken_down_email.call('not-a-uuid')
        send_link_taken_down_email.call('00000000-0000-0000-0000-000000000000')
        assert sent == []

    def test_no_reason_is_left_out_cleanly(self, link, staff, sent):
        take_down_link(link, by=staff, reason='')
        send_link_taken_down_email.call(str(link.pk))
        assert 'Reason:' not in sent[0]['text_body']


class TestWhatTheOwnerSees:
    @pytest.fixture
    def taken(self, funded_user, staff):
        mine = create_link(funded_user, code='Mine01', title='My page')
        reports.create_report(
            mine, reason='phishing', reporter_email='who@elsewhere.org', reporter_ip='203.0.113.7'
        )
        take_down_link(mine, by=staff, reason='Phishing page', internal_note='SECRET NOTE')
        return mine

    def test_the_link_page_shows_the_reason_only(self, web, taken):
        content = web.get(f'/links/{taken.pk}/').content.decode()
        assert 'This link was taken down: Phishing page' in content
        assert 'SECRET NOTE' not in content
        assert 'who@elsewhere.org' not in content and '203.0.113.7' not in content
        assert 'Taken down' in content  # the pill

    def test_the_dashboard_row_shows_the_reason_only(self, web, taken):
        content = web.get('/links/').content.decode()
        assert 'Taken down: Phishing page' in content
        assert 'SECRET NOTE' not in content
        assert 'who@elsewhere.org' not in content and '203.0.113.7' not in content

    def test_the_taken_down_tab_lists_it(self, web, taken):
        assert 'Mine01' in web.get('/links/', {'status': 'taken_down'}).content.decode()

    def test_the_row_menu_offers_no_change(self, web, taken):
        page = web.get(f'/links/{taken.pk}/').content.decode()
        assert f'/links/{taken.pk}/edit/' not in page
        assert f'/links/{taken.pk}/delete/' not in page
        assert f'/links/{taken.pk}/enable/' not in page
        row = web.get('/links/').content.decode()
        assert f'/links/{taken.pk}/edit/' not in row
        assert f'/links/{taken.pk}/delete/' not in row

    def test_the_edit_page_goes_back_with_a_message(self, web, taken):
        response = web.get(f'/links/{taken.pk}/edit/', follow=True)
        assert response.redirect_chain[-1][0] == f'/links/{taken.pk}/'
        assert 'can no longer be changed' in response.content.decode()

    def test_the_public_pages_say_nothing(self, client, taken):
        unknown = client.get('/s/neverissued')
        assert client.get('/s/Mine01').content == unknown.content
        assert client.get('/s/Mine01+').status_code == 404
        assert client.post('/s/Mine01/report', {'reason': 'spam'}).status_code == 404
