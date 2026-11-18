"""Emailing a share link: the order of checks (all before the budget is charged), the budget per
email, the cooldown and its race, the unconfirmed owner, the task that sends nothing for a link
that stopped working, the rendered email, and the owner's panel (forms, refusals, 404s)."""

from datetime import timedelta
from unittest import mock

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.core import ratelimit
from apps.core.datetime_format import format_utc
from apps.qr_code.limits import MAX_RECIPIENTS_PER_LINK, RESEND_COOLDOWN
from apps.qr_code.models import QRShareRecipient
from apps.qr_code.services import share, share_emails
from apps.qr_code.services.text import clean_message

from .share_support import make_link, make_qrcode, make_user

pytestmark = [pytest.mark.django_db, pytest.mark.unit]

HTMX = {'HTTP_HX_REQUEST': 'true'}


def url(name, *args):
    return reverse(f'qr_code:{name}', args=args)


def text(response) -> str:
    return response.content.decode()


@pytest.fixture(autouse=True)
def _media_root(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path


@pytest.fixture
def owner():
    return make_user(name='Alice Owner')


@pytest.fixture
def qrcode(owner):
    return make_qrcode(owner, name='Shop door')


@pytest.fixture
def link(qrcode):
    return share.create_link(qrcode)


@pytest.fixture
def sent(monkeypatch):
    """Every email the task sends, as the keyword arguments of `send_email`."""
    emails: list[dict] = []

    def _record(**kwargs):
        emails.append(kwargs)
        return 1, 0

    monkeypatch.setattr('apps.qr_code.services.share_emails.send_email', _record)
    return emails


@pytest.fixture
def load(link):
    """The link as the views load it: with its QR code and the code's owner."""
    from apps.qr_code.models import QRShareLink

    def _load():
        return QRShareLink.objects.select_related('qrcode', 'qrcode__created_by').get(pk=link.pk)

    return _load


class Charge:
    """A `charge` callback that records its calls."""

    def __init__(self):
        self.calls: list[int] = []

    def __call__(self, count: int) -> None:
        self.calls.append(count)


def refused(fn, *args, **kwargs) -> ValidationError:
    with pytest.raises(ValidationError) as caught:
        fn(*args, **kwargs)
    return caught.value


def send(load, emails, *, callbacks, message='', charge=None):
    with callbacks(execute=True):
        return share_emails.send_link(load(), emails, message=message, charge=charge)


class TestCleaning:
    def test_a_message_keeps_its_lines_and_drops_what_hides_text(self):
        raw = 'Hi\r\nthere\u200b  you\x07\n\n\n\n  Bye\u202e  \t\r\n'
        assert clean_message(raw) == 'Hi\nthere you\n\nBye'
        assert clean_message(None) == ''
        assert clean_message('  \n \n') == ''

    def test_addresses_are_deduplicated_ignoring_case_and_their_domain_lowercased(self):
        cleaned = share_emails.clean_addresses(
            [' Ann@Example.COM ', 'ann@example.com', '', 'bob@x.org', 'BOB@x.org']
        )
        assert cleaned == ['Ann@example.com', 'bob@x.org']

    def test_no_address_and_a_bad_one_are_refused_by_field(self):
        assert refused(share_emails.clean_addresses, ['', ' ']).message_dict == {
            'emails': [share_emails.NO_ADDRESS_MESSAGE]
        }
        error = refused(share_emails.clean_addresses, ['ok@example.com', 'x' * 200 + '@@'])
        (message,) = error.message_dict['emails']
        assert message.endswith('is not a valid email address.') and len(message) < 120

    def test_more_than_the_cap_is_refused_without_validating_the_rest(self):
        many = [f'p{i}@example.com' for i in range(MAX_RECIPIENTS_PER_LINK + 1)]
        assert refused(share_emails.clean_addresses, many + ['garbage']).message_dict == {
            'emails': [share_emails.CAP_MESSAGE]
        }


class TestSending:
    def test_it_creates_the_people_and_emails_each_once_the_transaction_commits(
        self, load, sent, django_capture_on_commit_callbacks
    ):
        charge = Charge()
        before = timezone.now()
        with django_capture_on_commit_callbacks(execute=False) as callbacks:
            people = share_emails.send_link(
                load(),
                ['Ann@Example.com', 'bob@example.com'],
                message='Hello',
                charge=charge,
            )
        assert sent == []  # nothing before the commit
        assert [p.email for p in people] == ['Ann@example.com', 'bob@example.com']
        assert charge.calls == [2]
        assert QRShareRecipient.objects.count() == 2
        assert all(p.last_sent_at >= before for p in QRShareRecipient.objects.all())
        for callback in callbacks:
            callback()
        assert sorted(email['to'] for email in sent) == ['Ann@example.com', 'bob@example.com']

    def test_the_email_has_a_fixed_subject_and_the_owners_message(
        self, load, sent, django_capture_on_commit_callbacks
    ):
        send(load, ['ann@example.com'], callbacks=django_capture_on_commit_callbacks, message='Hi!')
        (email,) = sent
        assert email['subject'] == 'A QR code was shared with you'
        assert 'Alice Owner shared the QR code "Shop door" with you.' in email['text_body']
        assert 'Hi!' in email['text_body'] and 'Hi!' in email['html_body']
        assert '/q/' in email['text_body']

    def test_the_message_reaches_each_person_but_only_a_cleaned_one(
        self, load, sent, django_capture_on_commit_callbacks
    ):
        send(
            load,
            ['ann@example.com'],
            callbacks=django_capture_on_commit_callbacks,
            message='line one\u200b\n\n\n\nline two',
        )
        assert 'line one\n\nline two' in sent[0]['text_body']

    def test_one_charge_with_the_number_of_emails_resends_included(
        self, load, link, sent, django_capture_on_commit_callbacks
    ):
        QRShareRecipient.objects.create(
            link=link, email='old@example.com', last_sent_at=timezone.now() - RESEND_COOLDOWN * 2
        )
        charge = Charge()
        send(
            load,
            ['old@example.com', 'new@example.com'],
            callbacks=django_capture_on_commit_callbacks,
            charge=charge,
        )
        assert charge.calls == [2]
        assert len(sent) == 2 and QRShareRecipient.objects.count() == 2

    def test_the_same_address_in_another_case_is_one_person(
        self, load, link, sent, django_capture_on_commit_callbacks
    ):
        QRShareRecipient.objects.create(
            link=link, email='Ann@example.com', last_sent_at=timezone.now() - RESEND_COOLDOWN * 2
        )
        send(load, ['ann@example.com'], callbacks=django_capture_on_commit_callbacks)
        assert QRShareRecipient.objects.count() == 1 and len(sent) == 1
        assert sent[0]['to'] == 'Ann@example.com'  # the spelling already on the link

    def test_a_link_with_an_expiry_says_so_in_utc(
        self, qrcode, sent, django_capture_on_commit_callbacks
    ):
        link = share.create_link(qrcode, expires_on=(timezone.now() + timedelta(days=3)).date())
        from apps.qr_code.models import QRShareLink

        loaded = QRShareLink.objects.select_related('qrcode', 'qrcode__created_by').get(pk=link.pk)
        with django_capture_on_commit_callbacks(execute=True):
            share_emails.send_link(loaded, ['ann@example.com'])
        assert 'The link expires on' in sent[0]['text_body']
        assert ' UTC.' in sent[0]['text_body'] and ' UTC.' in sent[0]['html_body']


class TestRefusalsSpendNothing:
    """Every refusal comes before `charge`, queues nothing and writes nothing."""

    @pytest.fixture(autouse=True)
    def _nothing_goes_out(self, sent, django_capture_on_commit_callbacks):
        with django_capture_on_commit_callbacks(execute=True) as callbacks:
            yield
        assert callbacks == [] and sent == []  # nothing was queued, so nothing was sent

    def assert_nothing(self, charge, *_):
        assert charge.calls == []
        assert QRShareRecipient.objects.count() == 0

    def test_a_revoked_link(self, load, link, sent, django_capture_on_commit_callbacks):
        share.revoke_link(link)
        charge = Charge()
        error = refused(share_emails.send_link, load(), ['a@example.com'], charge=charge)
        assert error.messages == [share_emails.NOT_WORKING_MESSAGE]
        self.assert_nothing(charge, sent, django_capture_on_commit_callbacks)

    def test_an_expired_link(self, load, link, sent, django_capture_on_commit_callbacks):
        link.expires_at = timezone.now() - timedelta(minutes=1)
        link.save()
        charge = Charge()
        error = refused(share_emails.send_link, load(), ['a@example.com'], charge=charge)
        assert error.messages == [share_emails.NOT_WORKING_MESSAGE]
        self.assert_nothing(charge, sent, django_capture_on_commit_callbacks)

    def test_a_deleted_code(self, load, qrcode, sent, django_capture_on_commit_callbacks):
        qrcode.deleted_at = timezone.now()
        qrcode.save()
        charge = Charge()
        error = refused(share_emails.send_link, load(), ['a@example.com'], charge=charge)
        assert error.messages == [share_emails.NOT_WORKING_MESSAGE]
        self.assert_nothing(charge, sent, django_capture_on_commit_callbacks)

    def test_an_owner_whose_email_is_not_confirmed(
        self, load, owner, sent, django_capture_on_commit_callbacks
    ):
        owner.email_confirmed = False
        owner.save()
        charge = Charge()
        error = refused(share_emails.send_link, load(), ['a@example.com'], charge=charge)
        assert error.messages == ['Confirm your email address to send a QR code by email.']
        self.assert_nothing(charge, sent, django_capture_on_commit_callbacks)

    def test_the_link_comes_before_the_owner_before_the_addresses(self, load, link, owner):
        owner.email_confirmed = False
        owner.save()
        share.revoke_link(link)
        error = refused(share_emails.send_link, load(), ['not an address'])
        assert error.messages == [share_emails.NOT_WORKING_MESSAGE]
        link.revoked_at = None
        link.save()
        error = refused(share_emails.send_link, load(), ['not an address'])
        assert error.messages == [share_emails.UNCONFIRMED_MESSAGE]

    def test_no_address_and_a_bad_one(self, load, sent, django_capture_on_commit_callbacks):
        charge = Charge()
        error = refused(share_emails.send_link, load(), [], charge=charge)
        assert error.message_dict == {'emails': [share_emails.NO_ADDRESS_MESSAGE]}
        error = refused(share_emails.send_link, load(), ['a@example.com', 'nope'], charge=charge)
        assert error.message_dict == {'emails': ['"nope" is not a valid email address.']}
        self.assert_nothing(charge, sent, django_capture_on_commit_callbacks)

    def test_a_message_over_the_limit_after_cleaning(
        self, load, sent, django_capture_on_commit_callbacks
    ):
        charge = Charge()
        error = refused(
            share_emails.send_link, load(), ['a@example.com'], message='x' * 501, charge=charge
        )
        assert error.message_dict == {'message': ['Keep the message to 500 characters or fewer.']}
        assert charge.calls == []

    def test_more_people_than_the_cap_in_all(self, load, link, sent):
        for i in range(MAX_RECIPIENTS_PER_LINK - 1):
            QRShareRecipient.objects.create(
                link=link,
                email=f'p{i}@example.com',
                last_sent_at=timezone.now() - RESEND_COOLDOWN * 2,
            )
        charge = Charge()
        error = refused(
            share_emails.send_link, load(), ['n1@example.com', 'n2@example.com'], charge=charge
        )
        assert error.message_dict == {'emails': [share_emails.CAP_MESSAGE]}
        assert (
            charge.calls == [] and QRShareRecipient.objects.count() == MAX_RECIPIENTS_PER_LINK - 1
        )

    def test_an_address_still_in_its_cooldown_refuses_the_whole_request(self, load, link):
        QRShareRecipient.objects.create(
            link=link, email='ann@example.com', last_sent_at=timezone.now() - timedelta(minutes=3)
        )
        charge = Charge()
        error = refused(
            share_emails.send_link,
            load(),
            ['new@example.com', 'ANN@example.com'],
            charge=charge,
        )
        assert error.message_dict == {
            'emails': ['"ANN@example.com" was emailed recently; you can resend in 7 minutes.']
        }
        assert charge.calls == [] and QRShareRecipient.objects.count() == 1

    def test_a_charge_that_refuses_stops_everything(self, load, sent):
        def over(count):
            raise ratelimit.RateLimitExceeded(retry_after=120, message='slow down')

        with pytest.raises(ratelimit.RateLimitExceeded):
            share_emails.send_link(load(), ['a@example.com'], charge=over)
        assert QRShareRecipient.objects.count() == 0


class TestAtTheLimits:
    def test_characters_cleaning_drops_do_not_count_toward_the_message_limit(
        self, load, sent, django_capture_on_commit_callbacks
    ):
        charge = Charge()
        message = 'x' * 500 + '\u200b' * 50
        send(
            load,
            ['a@example.com'],
            callbacks=django_capture_on_commit_callbacks,
            message=message,
            charge=charge,
        )
        assert charge.calls == [1] and len(sent) == 1

    def test_one_more_person_fits_and_so_does_a_resend_at_the_cap(
        self, load, link, sent, django_capture_on_commit_callbacks
    ):
        for i in range(MAX_RECIPIENTS_PER_LINK - 1):
            QRShareRecipient.objects.create(
                link=link,
                email=f'p{i}@example.com',
                last_sent_at=timezone.now() - RESEND_COOLDOWN * 2,
            )
        charge = Charge()
        people = send(
            load,
            ['n1@example.com', 'p0@example.com'],
            callbacks=django_capture_on_commit_callbacks,
            charge=charge,
        )
        assert len(people) == 2 and charge.calls == [2]
        assert QRShareRecipient.objects.count() == MAX_RECIPIENTS_PER_LINK
        assert len(sent) == 2


class TestBudget:
    @pytest.fixture(autouse=True)
    def _limits(self, settings, ratelimit_clock):
        settings.RATELIMIT_ENABLE = True
        settings.RATELIMIT_RULES = {**settings.RATELIMIT_RULES, 'QR_SHARE_EMAIL_USER': (3, 3600)}

    def test_one_hit_per_email_and_a_call_that_does_not_fit_emails_nobody(self, load, owner):
        import functools

        charge = functools.partial(share_emails.charge_share_emails, owner)
        people = share_emails.send_link(load(), ['a@example.com', 'b@example.com'], charge=charge)
        assert len(people) == 2
        with pytest.raises(ratelimit.RateLimitExceeded) as caught:
            share_emails.send_link(load(), ['c@example.com', 'd@example.com'], charge=charge)
        assert 'try again in' in caught.value.message and caught.value.retry_after > 0
        assert QRShareRecipient.objects.count() == 2  # nobody of the refused call

    def test_a_refused_request_spends_none_of_it(self, load, owner):
        import functools

        charge = functools.partial(share_emails.charge_share_emails, owner)
        for _ in range(10):  # an invalid address, a dead message: never charged
            refused(share_emails.send_link, load(), ['bad'], charge=charge)
            refused(
                share_emails.send_link, load(), ['a@example.com'], message='x' * 600, charge=charge
            )
        assert (
            len(
                share_emails.send_link(
                    load(), ['a@example.com', 'b@example.com', 'c@example.com'], charge=charge
                )
            )
            == 3
        )


class TestResend:
    def person(self, link, minutes_ago=30, email='ann@example.com'):
        return QRShareRecipient.objects.create(
            link=link, email=email, last_sent_at=timezone.now() - timedelta(minutes=minutes_ago)
        )

    def fresh(self, person):
        from apps.qr_code.models import QRShareLink

        loaded = QRShareLink.objects.select_related('qrcode', 'qrcode__created_by').get(
            pk=person.link_id
        )
        person = QRShareRecipient.objects.get(pk=person.pk)
        person.link = loaded
        return person

    def test_it_emails_one_person_again_without_a_message(
        self, link, sent, django_capture_on_commit_callbacks
    ):
        person = self.person(link)
        charge = Charge()
        with django_capture_on_commit_callbacks(execute=True):
            share_emails.resend(self.fresh(person), charge=charge)
        assert charge.calls == [1]
        assert [email['to'] for email in sent] == ['ann@example.com']
        person.refresh_from_db()
        assert person.last_sent_at > timezone.now() - timedelta(minutes=1)

    def test_the_cooldown_refuses_before_charging_and_names_the_wait(self, link):
        person = self.person(link, minutes_ago=4)
        charge = Charge()
        error = refused(share_emails.resend, self.fresh(person), charge=charge)
        assert error.messages == ['This person was emailed recently; you can resend in 6 minutes.']
        assert charge.calls == []

    def test_a_dead_link_and_an_unconfirmed_owner_refuse_before_charging(self, link, owner):
        person = self.person(link)
        charge = Charge()
        owner.email_confirmed = False
        owner.save()
        assert refused(share_emails.resend, self.fresh(person), charge=charge).messages == [
            share_emails.UNCONFIRMED_MESSAGE
        ]
        owner.email_confirmed = True
        owner.save()
        share.revoke_link(link)
        assert refused(share_emails.resend, self.fresh(person), charge=charge).messages == [
            share_emails.NOT_WORKING_MESSAGE
        ]
        assert charge.calls == []

    def test_two_quick_clicks_email_once(self, link, sent, django_capture_on_commit_callbacks):
        """Both requests pass the cooldown check; the conditional write lets one through."""
        person = self.person(link)
        first, second = self.fresh(person), self.fresh(person)
        with django_capture_on_commit_callbacks(execute=True):
            share_emails.resend(first)
            error = refused(share_emails.resend, second)  # `second` still read it as old
        assert 'was emailed recently' in error.messages[0]
        assert len(sent) == 1

    def test_a_person_removed_meanwhile(self, link, sent, django_capture_on_commit_callbacks):
        person = self.person(link)
        stale = self.fresh(person)
        QRShareRecipient.objects.filter(pk=person.pk).delete()
        error = refused(share_emails.resend, stale)
        assert error.messages == [share_emails.NO_LONGER_MESSAGE]

    def test_the_race_in_send_rolls_back_the_whole_request(
        self, load, link, sent, django_capture_on_commit_callbacks
    ):
        self.person(link, minutes_ago=30)
        with mock.patch.object(share_emails, '_claim', return_value=False):
            error = refused(share_emails.send_link, load(), ['new@example.com', 'ann@example.com'])
        assert 'was emailed recently' in error.message_dict['emails'][0]
        assert QRShareRecipient.objects.count() == 1  # the new person was rolled back
        with django_capture_on_commit_callbacks(execute=True):
            pass
        assert sent == []

    def test_an_address_added_at_the_same_moment_is_refused_not_a_500(self, load):
        with mock.patch.object(
            QRShareRecipient.objects, 'create', side_effect=IntegrityError('duplicate')
        ):
            error = refused(share_emails.send_link, load(), ['a@example.com'])
        assert 'was emailed recently' in error.message_dict['emails'][0]


class TestTask:
    def queue(self, link, email='ann@example.com', message=''):
        QRShareRecipient.objects.create(link=link, email=email, last_sent_at=timezone.now())
        share_emails.send_share_email.call(str(link.pk), email, message)

    def test_it_sends(self, link, sent):
        self.queue(link)
        assert len(sent) == 1

    @pytest.mark.parametrize('how', ['revoked', 'expired', 'deleted', 'unconfirmed', 'inactive'])
    def test_it_sends_nothing_when_the_link_no_longer_works(self, link, qrcode, owner, sent, how):
        QRShareRecipient.objects.create(link=link, email='ann@example.com')
        if how == 'revoked':
            share.revoke_link(link)
        elif how == 'expired':
            link.expires_at = timezone.now() - timedelta(minutes=1)
            link.save()
        elif how == 'deleted':
            qrcode.deleted_at = timezone.now()
            qrcode.save()
        elif how == 'unconfirmed':
            owner.email_confirmed = False
            owner.save()
        else:
            owner.is_active = False
            owner.save()
        share_emails.send_share_email.call(str(link.pk), 'ann@example.com', '')
        assert sent == []

    def test_it_sends_nothing_for_a_person_no_longer_on_the_link_or_a_gone_link(self, link, sent):
        share_emails.send_share_email.call(str(link.pk), 'ann@example.com', '')
        QRShareRecipient.objects.create(link=link, email='ann@example.com')
        link_id = str(link.pk)
        link.delete()
        share_emails.send_share_email.call(link_id, 'ann@example.com', '')
        assert sent == []

    def test_a_hostile_name_and_message_are_escaped_in_the_html_and_never_in_the_subject(
        self, qrcode, owner, sent
    ):
        owner.name = '<script>alert(1)</script> & Co'
        owner.save()
        qrcode.name = '"><img src=x onerror=alert(2)>'
        qrcode.save()
        link = share.create_link(qrcode)
        self.queue(link, message='<b>bold</b> & <a href="javascript:x()">click</a>')
        (email,) = sent
        html = email['html_body']
        assert '<script>' not in html and '<img src=x' not in html and '<b>bold</b>' not in html
        assert '&lt;script&gt;' in html and '&lt;b&gt;bold&lt;/b&gt;' in html
        assert 'javascript:x()' in html  # as escaped text, in no href
        assert 'href="javascript' not in html
        assert email['subject'] == 'A QR code was shared with you'
        assert '<script>' not in email['subject'] and 'Co' not in email['subject']

    def test_an_owner_with_no_name_is_someone_never_the_email(self, link, owner, sent):
        owner.name = '   '
        owner.save()
        self.queue(link)
        assert sent[0]['text_body'].startswith('Someone shared the QR code')
        assert owner.email not in sent[0]['text_body'] + sent[0]['html_body']

    def test_the_link_is_absolute_and_the_expiry_is_public_wording(self, qrcode, sent, settings):
        settings.BASE_URL = 'https://app.example.test'
        link = share.create_link(qrcode, expires_on=(timezone.now() + timedelta(days=9)).date())
        self.queue(link)
        body = sent[0]['text_body']
        assert f'https://app.example.test/q/{link.code}' in body
        assert link.expires_at is not None
        assert f'The link expires on {format_utc(link.expires_at)} UTC.' in body


class TestPanel:
    @pytest.fixture
    def web(self, owner):
        client = Client()
        client.force_login(owner)
        return client

    def panel(self, web, qrcode):
        return text(web.get(url('share', qrcode.pk), **HTMX))

    def test_an_active_link_has_a_send_form_a_revoked_one_does_not(self, web, qrcode, link):
        dead = make_link(qrcode, revoked_at=timezone.now())
        body = self.panel(web, qrcode)
        assert f'action="{url("share-email", qrcode.pk, link.pk)}"' in body
        assert f'action="{url("share-email", qrcode.pk, dead.pk)}"' not in body
        assert 'Send by email' in body and 'name="emails"' in body and 'name="message"' in body

    def test_an_unconfirmed_owner_sees_why_there_is_no_form(self, web, owner, qrcode, link):
        owner.email_confirmed = False
        owner.save()
        body = self.panel(web, qrcode)
        assert 'Confirm your email address to send a QR code by email.' in body
        assert 'name="emails"' not in body

    def test_the_people_it_was_sent_to_are_listed_with_a_resend_button(self, web, qrcode, link):
        person = QRShareRecipient.objects.create(
            link=link, email='ann@example.com', last_sent_at=timezone.now()
        )
        body = self.panel(web, qrcode)
        assert 'ann@example.com' in body and 'Emailed to' in body
        assert f'action="{url("share-resend", qrcode.pk, link.pk, person.pk)}"' in body

    def test_the_panel_makes_a_constant_number_of_queries_for_any_number_of_people(
        self, web, qrcode, link, django_assert_max_num_queries
    ):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        def count():
            with CaptureQueriesContext(connection) as ctx:
                self.panel(web, qrcode)
            return len(ctx)

        QRShareRecipient.objects.create(link=link, email='p0@example.com')
        one = count()
        for i in range(1, 15):
            QRShareRecipient.objects.create(link=link, email=f'p{i}@example.com')
        assert count() == one

    def test_sending_answers_the_panel_with_a_notice_and_queues_the_emails(
        self, web, qrcode, link, sent, django_capture_on_commit_callbacks
    ):
        with django_capture_on_commit_callbacks(execute=True):
            response = web.post(
                url('share-email', qrcode.pk, link.pk),
                {'emails': 'ann@example.com, bob@example.com\ncy@example.com', 'message': 'Hi'},
                **HTMX,
            )
        assert response.status_code == 200
        body = text(response)
        assert 'Emailed the link to 3 people.' in body and 'cy@example.com' in body
        assert len(sent) == 3

    def test_a_refusal_is_a_422_into_the_message_slot_with_the_field_error(self, web, qrcode, link):
        response = web.post(
            url('share-email', qrcode.pk, link.pk), {'emails': 'nope', 'message': ''}, **HTMX
        )
        assert response.status_code == 422
        assert response['HX-Retarget'] == '#qr-share-msg'
        body = text(response)
        assert 'Email addresses: &quot;nope&quot; is not a valid email address.' in body
        assert 'data-field-error="emails"' in body
        assert QRShareRecipient.objects.count() == 0

    def test_an_empty_box_and_a_long_message(self, web, qrcode, link):
        response = web.post(url('share-email', qrcode.pk, link.pk), {'emails': ''}, **HTMX)
        assert response.status_code == 422 and 'Enter at least one email address.' in text(response)
        response = web.post(
            url('share-email', qrcode.pk, link.pk),
            {'emails': 'a@example.com', 'message': 'x' * 501},
            **HTMX,
        )
        assert response.status_code == 422
        assert 'Message: Keep the message to 500 characters or fewer.' in text(response)

    def test_without_htmx_it_flashes_and_goes_back_to_the_dialog(
        self, web, qrcode, link, sent, django_capture_on_commit_callbacks
    ):
        with django_capture_on_commit_callbacks(execute=True):
            response = web.post(
                url('share-email', qrcode.pk, link.pk), {'emails': 'ann@example.com'}
            )
        assert response.status_code == 302
        assert response['Location'] == f'{url("dashboard")}?share={qrcode.pk}'
        assert len(sent) == 1

    def test_resending_inside_the_cooldown_is_a_422_and_after_it_a_success(
        self, web, qrcode, link, sent, django_capture_on_commit_callbacks
    ):
        person = QRShareRecipient.objects.create(
            link=link, email='ann@example.com', last_sent_at=timezone.now()
        )
        target = url('share-resend', qrcode.pk, link.pk, person.pk)
        response = web.post(target, **HTMX)
        assert response.status_code == 422 and response['HX-Retarget'] == '#qr-share-msg'
        assert 'This person was emailed recently; you can resend in 10 minutes.' in text(response)
        QRShareRecipient.objects.filter(pk=person.pk).update(
            last_sent_at=timezone.now() - RESEND_COOLDOWN * 2
        )
        with django_capture_on_commit_callbacks(execute=True):
            response = web.post(target, **HTMX)
        assert response.status_code == 200
        assert 'Sent the email to ann@example.com again.' in text(response)
        assert len(sent) == 1

    def test_an_unconfirmed_owner_is_refused(self, web, owner, qrcode, link):
        owner.email_confirmed = False
        owner.save()
        response = web.post(
            url('share-email', qrcode.pk, link.pk), {'emails': 'a@example.com'}, **HTMX
        )
        assert response.status_code == 422
        assert 'Confirm your email address to send a QR code by email.' in text(response)

    def test_a_revoked_link_is_refused(self, web, qrcode, link):
        share.revoke_link(link)
        response = web.post(
            url('share-email', qrcode.pk, link.pk), {'emails': 'a@example.com'}, **HTMX
        )
        assert response.status_code == 422
        assert 'This link no longer works.' in text(response)

    def test_another_users_code_link_and_recipient_are_a_404(self, web, qrcode, link):
        other = make_user()
        theirs = make_qrcode(other)
        their_link = share.create_link(theirs)
        their_person = QRShareRecipient.objects.create(link=their_link, email='x@example.com')
        mine = QRShareRecipient.objects.create(link=link, email='m@example.com')
        post = {'emails': 'a@example.com'}
        assert web.post(url('share-email', theirs.pk, their_link.pk), post).status_code == 404
        assert web.post(url('share-email', qrcode.pk, their_link.pk), post).status_code == 404
        assert (
            web.post(url('share-resend', theirs.pk, their_link.pk, their_person.pk)).status_code
            == 404
        )
        # A person of another link under my own code and link.
        assert web.post(url('share-resend', qrcode.pk, link.pk, their_person.pk)).status_code == 404
        assert web.post(url('share-resend', qrcode.pk, their_link.pk, mine.pk)).status_code == 404
        assert QRShareRecipient.objects.filter(email='x@example.com').count() == 1

    def test_login_is_required_and_only_post_is_allowed(self, qrcode, link, web):
        anonymous = Client()
        assert anonymous.post(url('share-email', qrcode.pk, link.pk), {}).status_code == 302
        assert web.get(url('share-email', qrcode.pk, link.pk)).status_code == 405


class TestPanelRateLimit:
    @pytest.fixture(autouse=True)
    def _limits(self, settings, ratelimit_clock):
        settings.RATELIMIT_ENABLE = True
        settings.RATELIMIT_RULES = {**settings.RATELIMIT_RULES, 'QR_SHARE_EMAIL_USER': (3, 3600)}

    @pytest.fixture
    def web(self, owner):
        client = Client()
        client.force_login(owner)
        return client

    def test_over_the_budget_is_a_429_with_the_wait_and_creates_nobody(self, web, qrcode, link):
        target = url('share-email', qrcode.pk, link.pk)
        ok = web.post(target, {'emails': 'a@example.com, b@example.com'}, **HTMX)
        assert ok.status_code == 200
        response = web.post(target, {'emails': 'c@example.com, d@example.com'}, **HTMX)
        assert response.status_code == 429
        assert response['HX-Retarget'] == '#qr-share-msg' and int(response['Retry-After']) > 0
        assert 'try again in' in text(response)
        assert sorted(QRShareRecipient.objects.values_list('email', flat=True)) == [
            'a@example.com',
            'b@example.com',
        ]

    def test_refusals_spend_nothing_and_resends_spend_one_each(
        self, web, qrcode, link, sent, django_capture_on_commit_callbacks
    ):
        target = url('share-email', qrcode.pk, link.pk)
        for _ in range(6):
            assert web.post(target, {'emails': 'bad'}, **HTMX).status_code == 422
        person = QRShareRecipient.objects.create(
            link=link, email='ann@example.com', last_sent_at=timezone.now() - RESEND_COOLDOWN * 2
        )
        resend = url('share-resend', qrcode.pk, link.pk, person.pk)
        for _ in range(3):
            QRShareRecipient.objects.filter(pk=person.pk).update(
                last_sent_at=timezone.now() - RESEND_COOLDOWN * 2
            )
            with django_capture_on_commit_callbacks(execute=True):
                assert web.post(resend, **HTMX).status_code == 200
        QRShareRecipient.objects.filter(pk=person.pk).update(
            last_sent_at=timezone.now() - RESEND_COOLDOWN * 2
        )
        assert web.post(resend, **HTMX).status_code == 429
        assert len(sent) == 3

    def test_without_htmx_the_429_is_a_page(self, web, qrcode, link):
        target = url('share-email', qrcode.pk, link.pk)
        response = web.post(target, {'emails': 'a@example.com, b@example.com, c@example.com'})
        assert response.status_code == 302
        response = web.post(target, {'emails': 'd@example.com'})
        assert response.status_code == 429 and 'try again in' in text(response)
