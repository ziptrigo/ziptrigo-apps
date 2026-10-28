"""Add tasks by email (issue #186) outside the ingest itself: the settings page and its endpoints,
the address functions, `todo_ingest_eml`, the jobs, the admin and the prod settings."""

from html import unescape
from io import StringIO

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.urls import reverse

from apps.core.scheduler.registry import get_jobs
from apps.receipts.tests.emails import Ses, build_email, pdf

from ..models import (
    InboundEmail,
    InboundEmailStatus,
    InboundMessage,
    InboundMessageStatus,
    InboxCode,
    Task,
    TodoAccount,
)
from ..services import (
    get_account,
    inbox_address_for,
    regenerate_inbox_code,
    update_inbox_settings,
)
from ..services.storage import INBOUND_PREFIX
from .factories import make_user

pytestmark = [pytest.mark.django_db, pytest.mark.unit]

HTMX = {'HTTP_HX_REQUEST': 'true'}


def text(response) -> str:
    return unescape(response.content.decode())


class TestAddress:
    def test_it_is_todo_plus_the_code_at_the_configured_domain(self, funded_user):
        address = inbox_address_for(funded_user)
        code = InboxCode.objects.get(account__user=funded_user, active=True).code

        assert address == f'todo+{code}@ziptrigo.com'
        assert len(code) == 8

    def test_it_follows_the_setting(self, funded_user, settings):
        settings.TODO_INBOUND_ADDRESS = 'todo-staging@ziptrigo.com'

        assert inbox_address_for(funded_user).startswith('todo-staging+')
        assert inbox_address_for(funded_user).endswith('@ziptrigo.com')

    def test_an_account_without_a_code_gets_one_on_first_use(self, funded_user):
        get_account(funded_user)
        assert not InboxCode.objects.exists()

        inbox_address_for(funded_user)
        inbox_address_for(funded_user)

        assert InboxCode.objects.filter(active=True).count() == 1

    def test_regenerating_retires_the_old_code_for_good(self, funded_user):
        old = inbox_address_for(funded_user)

        new = regenerate_inbox_code(funded_user)

        assert new.active and inbox_address_for(funded_user) != old
        retired = InboxCode.objects.get(code=old.split('+')[1].split('@')[0])
        assert not retired.active and retired.retired_at is not None
        assert InboxCode.objects.filter(account__user=funded_user, active=True).count() == 1

    def test_codes_use_the_alphabet_without_look_alikes(self, funded_user):
        for _ in range(20):
            code = regenerate_inbox_code(funded_user).code
            assert not set(code) & set('il0o1')

    def test_the_allowlist_is_normalized(self, funded_user):
        saved = update_inbox_settings(
            funded_user, sender_allowlist='Me+x@Example.com, *@Acme.test; me@example.com'
        )

        assert saved == ['me@example.com', '*@acme.test']
        assert get_account(funded_user).sender_allowlist == saved

    def test_an_invalid_allowlist_is_a_keyed_error(self, funded_user):
        from django.core.exceptions import ValidationError

        with pytest.raises(ValidationError) as caught:
            update_inbox_settings(funded_user, sender_allowlist='not an address')

        assert 'sender_allowlist' in caught.value.message_dict

    def test_an_empty_allowlist_accepts_anyone(self, funded_user):
        update_inbox_settings(funded_user, sender_allowlist='a@b.com')

        assert update_inbox_settings(funded_user, sender_allowlist='') == []


class TestSettingsPage:
    def test_it_shows_the_address_the_senders_and_the_recent_emails(
        self, web, funded_user, fake_storage
    ):
        address = inbox_address_for(funded_user)
        message = InboundMessage.objects.create(s3_key=f'{INBOUND_PREFIX}m1')
        task = Task.objects.create(user=funded_user, number=1500, title='Made by mail')
        InboundEmail.objects.create(
            user=funded_user,
            message=message,
            recipient=address,
            from_address='boss@work.test',
            subject='Do this',
            status=InboundEmailStatus.ACCEPTED,
            task=task,
        )
        InboundEmail.objects.create(
            user=funded_user,
            message=message,
            recipient=address + '.x',
            from_address='spam@bad.test',
            subject='Win',
            status=InboundEmailStatus.REJECTED_SENDER,
            note='The sender is not on the accepted list.',
        )

        page = text(web.get(reverse('todo:settings')))

        assert 'Add tasks by email' in page and address in page
        assert 'Task #1500' in page and reverse('todo:task', args=[1500]) in page
        assert 'boss@work.test' in page and '“Do this”' in page
        assert 'sender not allowed' in page
        assert 'The sender is not on the accepted list.' in page
        assert reverse('todo:regenerate') in page and reverse('todo:settings-inbox') in page

    def test_no_emails_yet(self, web):
        assert 'No emails yet.' in text(web.get(reverse('todo:settings')))

    def test_only_the_last_20_are_shown_and_only_the_users_own(self, web, funded_user):
        message = InboundMessage.objects.create(s3_key=f'{INBOUND_PREFIX}m2')
        other = make_user()
        InboundEmail.objects.create(
            user=other, message=message, recipient='todo+o@x', from_address='other@else.test'
        )
        for i in range(25):
            InboundEmail.objects.create(
                user=funded_user, message=message, recipient=f'r{i}', subject=f'subject {i:02}'
            )

        page = text(web.get(reverse('todo:settings')))

        import re

        assert len(re.findall(r'subject \d\d', page)) == 20
        assert 'other@else.test' not in page

    def test_a_subject_is_escaped(self, web, funded_user):
        message = InboundMessage.objects.create(s3_key=f'{INBOUND_PREFIX}m3')
        InboundEmail.objects.create(
            user=funded_user, message=message, recipient='r', subject='<script>alert(1)</script>'
        )

        raw = web.get(reverse('todo:settings')).content.decode()

        assert '<script>alert(1)</script>' not in raw

    def test_the_default_due_time_form_still_saves(self, web):
        response = web.post(reverse('todo:settings'), {'default_due_time': '08:15'}, **HTMX)

        assert response.status_code == 200 and 'Saved.' in text(response)

    def test_anonymous_visitors_are_sent_to_login(self, client):
        for name in ('todo:settings-inbox', 'todo:regenerate'):
            assert client.post(reverse(name)).status_code == 302


class TestUpdateSenders:
    def test_only_these(self, web, funded_user):
        response = web.post(
            reverse('todo:settings-inbox'),
            {'sender_mode': 'only', 'sender_allowlist': 'a@b.com\n*@acme.test'},
            **HTMX,
        )

        assert response.status_code == 200 and 'Saved' in text(response)
        assert get_account(funded_user).sender_allowlist == ['a@b.com', '*@acme.test']
        # The form comes back showing the saved list.
        assert 'a@b.com, *@acme.test' in text(response)

    def test_anyone_clears_the_list(self, web, funded_user):
        update_inbox_settings(funded_user, sender_allowlist='a@b.com')

        web.post(reverse('todo:settings-inbox'), {'sender_mode': 'any'}, **HTMX)

        assert get_account(funded_user).sender_allowlist == []

    def test_a_typo_is_a_422_into_its_own_slot_and_saves_nothing(self, web, funded_user):
        response = web.post(
            reverse('todo:settings-inbox'),
            {'sender_mode': 'only', 'sender_allowlist': 'not an address'},
            **HTMX,
        )

        assert response.status_code == 422
        assert response['HX-Retarget'] == '#inbox-settings-msg'
        assert 'Use email addresses' in text(response)
        assert get_account(funded_user).sender_allowlist == []

    def test_only_these_with_nothing_typed_is_refused(self, web):
        response = web.post(
            reverse('todo:settings-inbox'), {'sender_mode': 'only', 'sender_allowlist': ' '}, **HTMX
        )

        assert response.status_code == 422 and 'Enter at least one address' in text(response)

    def test_a_bad_mode_is_refused(self, web):
        response = web.post(reverse('todo:settings-inbox'), {'sender_mode': 'nobody'}, **HTMX)

        assert response.status_code == 422

    def test_without_htmx_it_redirects_with_a_message(self, web, funded_user):
        response = web.post(
            reverse('todo:settings-inbox'),
            {'sender_mode': 'only', 'sender_allowlist': 'a@b.com'},
            follow=True,
        )

        assert response.redirect_chain[-1][0] == reverse('todo:settings')
        assert 'Saved.' in text(response)

    def test_get_is_not_allowed(self, web):
        assert web.get(reverse('todo:settings-inbox')).status_code == 405
        assert web.get(reverse('todo:regenerate')).status_code == 405

    def test_it_is_rate_limited(self, web, settings):
        settings.RATELIMIT_ENABLE = True
        settings.RATELIMIT_RULES = {**settings.RATELIMIT_RULES, 'TODO_EDIT_USER': (1, 3600)}
        data = {'sender_mode': 'any'}

        assert web.post(reverse('todo:settings-inbox'), data, **HTMX).status_code == 200
        assert web.post(reverse('todo:settings-inbox'), data, **HTMX).status_code == 429

    def test_another_user_is_not_affected(self, web, funded_user, other_user):
        web.post(
            reverse('todo:settings-inbox'),
            {'sender_mode': 'only', 'sender_allowlist': 'a@b.com'},
            **HTMX,
        )

        assert get_account(other_user).sender_allowlist == []


class TestRegenerate:
    def test_it_issues_a_new_address_and_redirects_with_a_message(self, web, funded_user):
        old = inbox_address_for(funded_user)

        response = web.post(reverse('todo:regenerate'), follow=True)

        assert response.redirect_chain[-1][0] == reverse('todo:settings')
        assert 'Your new address is ready' in text(response)
        assert inbox_address_for(funded_user) != old
        assert inbox_address_for(funded_user) in text(response)

    def test_with_htmx_it_redirects_by_header(self, web):
        response = web.post(reverse('todo:regenerate'), **HTMX)

        assert response['HX-Redirect'] == reverse('todo:settings')

    def test_the_sixth_in_an_hour_is_refused(self, web, funded_user, settings):
        settings.RATELIMIT_ENABLE = True
        old = inbox_address_for(funded_user)

        codes = [web.post(reverse('todo:regenerate')).status_code for _ in range(6)]

        assert codes == [302] * 5 + [429]
        assert inbox_address_for(funded_user) != old
        assert InboxCode.objects.filter(account__user=funded_user).count() == 6

    def test_the_old_address_stops_delivering(self, web, funded_user, fake_storage):
        from ..services import inbound

        old = inbox_address_for(funded_user)
        web.post(reverse('todo:regenerate'))
        key = f'{INBOUND_PREFIX}old'
        fake_storage.seed(key, build_email(subject='Late', ses=Ses(recipient=old)))
        message = InboundMessage.objects.create(s3_key=key)

        inbound.ingest(message.pk)

        assert not Task.objects.exists()


class TestTaskPage:
    def test_a_task_of_another_user_is_a_404_even_when_made_by_email(self, web, other_user):
        task = Task.objects.create(user=other_user, number=1700, title='Theirs', source='email')

        assert web.get(reverse('todo:task', args=[task.number])).status_code == 404


class TestIngestCommand:
    def run(self, *args: str) -> str:
        out = StringIO()
        call_command('todo_ingest_eml', *args, stdout=out)
        return out.getvalue()

    def test_to_replaces_the_recipient_in_the_file(
        self, tmp_path, funded_user, fake_storage, django_capture_on_commit_callbacks
    ):
        path = tmp_path / 'task.eml'
        path.write_bytes(
            build_email(
                subject='From a file',
                attachments=[pdf()],
                ses=Ses(recipient='todo+zzzz2222@ziptrigo.com'),
            )
        )
        address = inbox_address_for(funded_user)

        with django_capture_on_commit_callbacks(execute=True):
            output = self.run(str(path), '--to', address)

        task = Task.objects.get()
        assert f'{address}: accepted' in output and f'task #{task.number}' in output
        message = InboundMessage.objects.get()
        assert message.s3_key.startswith(f'{INBOUND_PREFIX}local-')
        assert message.status == InboundMessageStatus.DONE
        assert message.s3_key not in fake_storage.objects
        assert task.title == 'From a file' and task.attachments.count() == 1

    def test_without_to_the_headers_decide(self, tmp_path, funded_user):
        path = tmp_path / 'task.eml'
        path.write_bytes(
            build_email(subject='By header', ses=Ses(recipient=inbox_address_for(funded_user)))
        )

        output = self.run(str(path))

        assert 'accepted' in output and Task.objects.get().title == 'By header'

    def test_no_matching_recipient_is_said(self, tmp_path, funded_user):
        path = tmp_path / 'task.eml'
        path.write_bytes(build_email(ses=Ses(recipient='todo+zzzz2222@ziptrigo.com')))

        assert 'No recipient matched' in self.run(str(path))

    def test_a_missing_file(self, tmp_path):
        with pytest.raises(CommandError, match='not a file'):
            self.run(str(tmp_path / 'nope.eml'))

    def test_a_message_that_cannot_be_ingested_exits_with_an_error(
        self, tmp_path, fake_storage, monkeypatch
    ):
        from ..services import inbound

        path = tmp_path / 'bad.eml'
        path.write_bytes(b'x')
        monkeypatch.setattr(
            inbound.mime, 'parse_message', lambda raw: (_ for _ in ()).throw(ValueError('bad'))
        )

        with pytest.raises(CommandError, match='failed after 1 attempt'):
            self.run(str(path))

    def test_a_file_over_the_ses_limit_is_refused(self, tmp_path, monkeypatch):
        from ..services import inbound

        path = tmp_path / 'big.eml'
        path.write_bytes(b'x' * 20)
        monkeypatch.setattr(inbound, 'MAX_RAW_BYTES', 10)

        with pytest.raises(CommandError, match='40 MB'):
            self.run(str(path))


class TestJobs:
    def test_they_are_registered(self):
        from django.apps import apps

        # Re-run the registration: other tests clear the registry.
        apps.get_app_config('todo')._register_jobs()
        jobs = {spec.name: spec for spec in get_jobs()}

        poll = jobs['todo.poll_inbound']
        assert poll.interval.total_seconds() == 60 and poll.lease.total_seconds() == 120
        purge = jobs['todo.purge_inbound_messages']
        assert purge.interval.total_seconds() == 24 * 3600

    def test_the_poll_job_runs_the_poll(self, fake_storage, funded_user):
        from .. import jobs

        key = f'{INBOUND_PREFIX}job'
        fake_storage.seed(key, build_email(ses=Ses(recipient=inbox_address_for(funded_user))))

        jobs.poll_inbound()

        assert InboundMessage.objects.filter(s3_key=key).exists()

    def test_the_purge_job_runs_the_purge(self, fake_storage):
        from datetime import timedelta

        from django.utils import timezone

        from .. import jobs

        fake_storage.seed(f'{INBOUND_PREFIX}old', b'x')
        InboundMessage.objects.create(
            s3_key=f'{INBOUND_PREFIX}old',
            status=InboundMessageStatus.DONE,
            processed_at=timezone.now() - timedelta(days=40),
        )

        jobs.purge_inbound_messages()

        assert not InboundMessage.objects.exists()


class TestAdmin:
    @pytest.fixture
    def staff(self, client, db):
        user = make_user(is_staff=True, is_superuser=True)
        client.force_login(user)
        return client

    def test_the_pages_open(self, staff, funded_user):
        inbox_address_for(funded_user)
        message = InboundMessage.objects.create(s3_key=f'{INBOUND_PREFIX}a', status='failed')
        InboundEmail.objects.create(user=funded_user, message=message, recipient='r')
        account = TodoAccount.objects.get(pk=funded_user.pk)

        for url in (
            '/admin/todo/inboundmessage/',
            f'/admin/todo/inboundmessage/{message.pk}/change/',
            '/admin/todo/inboundemail/',
            f'/admin/todo/todoaccount/{account.pk}/change/',
        ):
            assert staff.get(url).status_code == 200, url
        assert inbox_address_for(funded_user).split('+')[1].split('@')[0] in text(
            staff.get(f'/admin/todo/todoaccount/{account.pk}/change/')
        )

    def test_retry_resets_a_failed_message(self, staff, fake_storage):
        message = InboundMessage.objects.create(
            s3_key=f'{INBOUND_PREFIX}retry', status='failed', attempts=3
        )
        fake_storage.seed(message.s3_key, b'x')

        staff.post(
            '/admin/todo/inboundmessage/',
            {'action': 'retry_action', '_selected_action': [message.pk]},
        )

        message.refresh_from_db()
        assert message.status != 'failed' or message.attempts == 0

    def test_staff_without_the_change_permission_cannot_retry(self, client, db):
        from django.contrib.auth.models import Permission

        staff = make_user(is_staff=True)
        staff.user_permissions.add(  # ty: ignore[unresolved-attribute]
            Permission.objects.get(codename='view_inboundmessage', content_type__app_label='todo')
        )
        message = InboundMessage.objects.create(s3_key=f'{INBOUND_PREFIX}x', status='failed')
        client.force_login(staff)

        client.post(
            '/admin/todo/inboundmessage/',
            {'action': 'retry_action', '_selected_action': [message.pk]},
        )

        message.refresh_from_db()
        assert message.status == 'failed'

    def test_staff_with_the_change_permission_can_retry(self, client, db, fake_storage):
        from django.contrib.auth.models import Permission

        staff = make_user(is_staff=True)
        staff.user_permissions.add(  # ty: ignore[unresolved-attribute]
            Permission.objects.get(codename='view_inboundmessage', content_type__app_label='todo'),
            Permission.objects.get(
                codename='change_inboundmessage', content_type__app_label='todo'
            ),
        )
        message = InboundMessage.objects.create(s3_key=f'{INBOUND_PREFIX}y', status='failed')
        fake_storage.seed(message.s3_key, b'x')
        client.force_login(staff)

        client.post(
            '/admin/todo/inboundmessage/',
            {'action': 'retry_action', '_selected_action': [message.pk]},
        )

        message.refresh_from_db()
        assert message.status != 'failed'

    def test_the_settings_page_has_the_ceiling(self, staff):
        response = staff.get('/admin/todo/todosettings/', follow=True)

        assert response.status_code == 200 and 'max_inbound_rows_per_day' in text(response)


def test_a_new_account_defaults(funded_user):
    account = get_account(funded_user)

    assert account.sender_allowlist == []


def test_the_default_inbound_address_setting():
    from django.conf import settings

    assert settings.TODO_INBOUND_ADDRESS.startswith('todo')
    assert 'TODO_REGENERATE_USER' in settings.RATELIMIT_RULES
    assert settings.RATELIMIT_RULES['TODO_REGENERATE_USER'] == (5, 3600)
