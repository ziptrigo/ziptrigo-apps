"""Add tasks by email (issue #186): `services.inbound`, from a raw message in the (fake) bucket to a
task. Fixtures are built with the SES-shaped email builder Receipts' tests use
(`apps.receipts.tests.emails`)."""

import base64
import hashlib
import re
import uuid
from datetime import date, time, timedelta
from typing import Any

import pytest
from botocore.exceptions import ClientError
from django.urls import reverse
from django.utils import timezone

from apps.billing.services import add_credits, get_balance
from apps.core.models import Notification
from apps.receipts.tests.emails import (
    HTML_RECEIPT,
    Attachment,
    Ses,
    build_email,
    jpeg,
    logo,
    pdf,
    png,
)

from ..models import (
    Attachment as TaskAttachment,
)
from ..models import (
    InboundEmail,
    InboundEmailStatus,
    InboundMessage,
    InboundMessageStatus,
    InboxCode,
    Task,
    TaskSource,
    TodoAccount,
    TodoSettings,
)
from ..services import get_account, inbound, parsing
from ..services.accounts import inbox_address_for
from ..services.parsing import ParsedTitle, ParseUnavailable
from ..services.storage import (
    FAILED_PREFIX,
    INBOUND_PREFIX,
    attachment_key,
    failed_key_for,
    user_prefix,
)
from .factories import make_user

pytestmark = [pytest.mark.django_db, pytest.mark.unit]


@pytest.fixture
def inbox(funded_user) -> str:
    """The funded user's real `todo+<code>@...` address."""
    return inbox_address_for(funded_user)


@pytest.fixture
def deliver(fake_storage, django_capture_on_commit_callbacks):
    """Put a raw email in the inbound prefix and run its ingest, queued work included."""

    def _deliver(raw: bytes, *, recipients=None) -> InboundMessage:
        key = f'{INBOUND_PREFIX}{uuid.uuid4()}'
        fake_storage.seed(key, raw)
        message = InboundMessage.objects.create(s3_key=key)
        with django_capture_on_commit_callbacks(execute=True):
            inbound.ingest(message.pk, recipients=recipients)
        message.refresh_from_db()
        return message

    return _deliver


def mail(inbox: str, *, subject: str = 'Buy milk', text: str | None = 'Two litres.', **fields):
    return build_email(subject=subject, text=text, ses=Ses(recipient=inbox), **fields)


def one_email(message: InboundMessage) -> InboundEmail:
    [email] = list(message.emails.all())
    return email


def the_task(message: InboundMessage) -> Task:
    task = one_email(message).task
    assert task is not None
    return task


def assert_parked(fake_storage, message: InboundMessage) -> None:
    assert message.status == InboundMessageStatus.FAILED
    assert message.failed_key == failed_key_for(message.s3_key)
    assert message.failed_key.startswith(FAILED_PREFIX)
    assert message.failed_key in fake_storage.objects
    assert message.s3_key not in fake_storage.objects
    assert not fake_storage.list_keys(INBOUND_PREFIX)


class TestAcceptedTask:
    def test_the_subject_is_the_title_and_the_body_the_description(
        self, deliver, inbox, funded_user, fake_storage
    ):
        message = deliver(mail(inbox, subject='Buy milk', text='Two litres, **semi-skimmed**.'))

        assert message.status == InboundMessageStatus.DONE
        assert message.processed_at is not None and message.last_error == ''
        email = one_email(message)
        assert email.status == InboundEmailStatus.ACCEPTED
        assert email.user == funded_user and email.recipient == inbox
        assert email.from_address == 'orders@staples.com'
        assert email.envelope_sender == 'orders@staples.com'
        assert email.subject == 'Buy milk'
        assert email.received_at is not None and email.received_at.year == 2026
        task = the_task(message)
        assert task.user == funded_user
        assert task.title == 'Buy milk'
        assert task.description == 'Two litres, **semi-skimmed**.'
        assert task.source == TaskSource.EMAIL
        assert task.due_date is None
        assert task.number >= 1000
        assert task.unpaid_at is None

    def test_the_inbound_object_is_deleted(self, deliver, inbox, fake_storage):
        message = deliver(mail(inbox))

        assert message.s3_key not in fake_storage.objects
        assert message.s3_key in fake_storage.deleted

    def test_it_counts_toward_the_free_allowance_then_costs_the_price(
        self, deliver, inbox, funded_user
    ):
        for subject in ('one', 'two', 'three'):
            deliver(mail(inbox, subject=subject))

        assert Task.objects.filter(user=funded_user).count() == 3
        assert get_balance(funded_user) == 95  # 2 free, then 5
        assert get_account(funded_user).month_task_count == 3

    def test_the_task_page_says_where_it_came_from(self, deliver, inbox, web):
        task = the_task(deliver(mail(inbox, subject='From mail')))

        page = web.get(f'/todo/{task.number}/').content.decode()

        assert 'Created from an email from orders@staples.com' in page

    def test_a_web_task_says_nothing_of_email(self, web, funded_user):
        from ..services import create_task

        task = create_task(funded_user, title='Typed')

        assert 'Created from an email' not in web.get(f'/todo/{task.number}/').content.decode()

    def test_a_task_made_by_email_whose_row_is_gone_still_renders(self, deliver, inbox, web):
        message = deliver(mail(inbox))
        task = the_task(message)
        InboundEmail.objects.all().delete()

        page = web.get(f'/todo/{task.number}/').content.decode()

        assert 'Created from an email from orders@staples.com' in page  # kept on the task

    def test_the_authentication_of_the_sender_is_stored(self, deliver, inbox):
        task = the_task(deliver(mail(inbox)))

        assert task.email_from == 'orders@staples.com' and task.email_from_authenticated
        assert one_email(InboundMessage.objects.get()).sender_authenticated

    def test_an_unauthenticated_sender_is_stored_as_such_and_the_page_says_claiming(
        self, deliver, inbox, web
    ):
        """An empty allowlist accepts anyone, authenticated or not: the page must not present an
        unproven `From` as the sender."""
        raw = build_email(
            from_addr='boss@work.test',
            subject='Pay up',
            text='x',
            ses=Ses(recipient=inbox, spf=None, dkim=None, dmarc=None),
        )

        message = deliver(raw)

        task = the_task(message)
        assert one_email(message).sender_authenticated is False
        assert task.email_from == 'boss@work.test' and not task.email_from_authenticated
        page = web.get(f'/todo/{task.number}/').content.decode()
        assert 'Created from an email claiming to be from boss@work.test' in page
        assert 'Created from an email from' not in page

    def test_a_forged_from_with_a_pass_for_another_domain_is_not_authenticated(
        self, deliver, inbox
    ):
        raw = build_email(
            from_addr='ceo@victim.test',
            text='x',
            ses=Ses(
                recipient=inbox,
                return_path='me@attacker.test',
                dkim_domain='attacker.test',
                dmarc='FAIL',
            ),
        )

        assert the_task(deliver(raw)).email_from_authenticated is False

    def test_a_listed_and_vouched_sender_is_authenticated(self, deliver, inbox, funded_user):
        TodoAccount.objects.filter(pk=get_account(funded_user).pk).update(
            sender_allowlist=['orders@staples.com']
        )

        assert the_task(deliver(mail(inbox))).email_from_authenticated

    def test_the_task_page_needs_no_lookup_of_the_email(self, deliver, inbox, web, funded_user):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        from ..services import create_task

        emailed = the_task(deliver(mail(inbox)))
        typed = create_task(funded_user, title='Typed')
        web.get(f'/todo/{typed.number}/')  # warm up (caches, content types)

        with CaptureQueriesContext(connection) as typed_queries:
            web.get(f'/todo/{typed.number}/')
        with CaptureQueriesContext(connection) as emailed_queries:
            page = web.get(f'/todo/{emailed.number}/').content.decode()

        assert 'Created from an email from' in page
        assert len(emailed_queries) == len(typed_queries)
        assert not [q for q in emailed_queries if 'todo_inboundemail' in q['sql']]

    def test_two_recipients_two_users(self, deliver, funded_user, inbox):
        other = make_user()
        add_credits(other, 50, source='test')
        second = inbox_address_for(other)

        message = deliver(
            build_email(subject='For both', ses=Ses(recipient=f'{inbox}, {second}'), text='x')
        )

        assert message.emails.count() == 2
        assert Task.objects.filter(user=funded_user, title='For both').count() == 1
        assert Task.objects.filter(user=other, title='For both').count() == 1


class TestRecipients:
    def test_an_unknown_code_is_dropped_without_a_row(self, deliver, fake_storage, caplog):
        message = deliver(mail('todo+zzzzzzzz@ziptrigo.com'))

        assert message.status == InboundMessageStatus.DONE
        assert not message.emails.exists() and not Task.objects.exists()
        assert 'unknown inbox code' in caplog.text

    def test_a_retired_code_is_dropped(self, deliver, funded_user, inbox):
        from ..services import regenerate_inbox_code

        regenerate_inbox_code(funded_user)

        message = deliver(mail(inbox))

        assert not message.emails.exists() and not Task.objects.exists()

    def test_the_old_code_stays_retired_and_the_new_one_works(self, deliver, funded_user, inbox):
        from ..services import regenerate_inbox_code

        new = regenerate_inbox_code(funded_user)

        assert (
            InboxCode.objects.filter(code=inbox.split('+')[1].split('@')[0]).first().active is False
        )
        message = deliver(mail(f'todo+{new.code}@ziptrigo.com'))
        assert the_task(message).user == funded_user

    def test_an_inactive_user_gets_nothing(self, deliver, inbox, funded_user):
        funded_user.status = funded_user.STATUS_INACTIVE
        funded_user.save()

        message = deliver(mail(inbox))

        assert not message.emails.exists() and not Task.objects.exists()

    def test_a_message_for_nobody_we_know_is_done(self, deliver):
        message = deliver(build_email())  # addressed to a receipts address

        assert message.status == InboundMessageStatus.DONE and not message.emails.exists()

    def test_explicit_recipients_replace_the_headers(self, deliver, inbox):
        message = deliver(mail('todo+zzzzzzzz@ziptrigo.com'), recipients=[inbox])

        assert the_task(message).title == 'Buy milk'

    def test_a_receipts_address_is_not_a_todo_address(self, deliver, funded_user, inbox):
        # The same code under the other product's address is not ours.
        code = InboxCode.objects.get(account__user=funded_user).code
        assert code in inbox
        message = deliver(mail(f'receipts+{code}@ziptrigo.com'))

        assert not message.emails.exists()


class TestSubject:
    @pytest.mark.parametrize(
        ('subject', 'title'),
        [
            ('Fwd: Buy milk', 'Buy milk'),
            ('FW: Buy milk', 'Buy milk'),
            ('Fw:Buy milk', 'Buy milk'),
            ('Re: Buy milk', 'Buy milk'),
            ('RE: Fwd: re: Buy milk', 'Buy milk'),
            ('  re :   Buy   milk  ', 'Buy milk'),
            ('Reminder: buy milk', 'Reminder: buy milk'),
            ('Forward the report', 'Forward the report'),
            ('Re-organise the shelf', 'Re-organise the shelf'),
            ('Buy milk Re: later', 'Buy milk Re: later'),
        ],
    )
    def test_prefixes_are_removed(self, deliver, inbox, subject, title):
        assert the_task(deliver(mail(inbox, subject=subject))).title == title

    @pytest.mark.parametrize('subject', ['', 'Fwd:', 'Re: Fw: ', '   '])
    def test_no_subject(self, deliver, inbox, subject):
        task = the_task(deliver(mail(inbox, subject=subject, text='the body')))

        assert task.title == '(no subject)'
        assert task.description == 'the body'

    def test_a_long_subject_is_cut_to_the_title_limit(self, deliver, inbox):
        # The recorded subject is cut at 500 by the parser already.
        task = the_task(deliver(mail(inbox, subject='x' * 900)))

        assert task.title == 'x' * 500

    def test_a_non_ascii_subject(self, deliver, inbox):
        task = the_task(deliver(mail(inbox, subject='Acheter du café ☕')))

        assert task.title == 'Acheter du café ☕'


class TestBody:
    def test_plain_text_is_preferred_over_html(self, deliver, inbox):
        raw = mail(inbox, text='The plain version.', html='<p>The <b>html</b> version.</p>')

        assert the_task(deliver(raw)).description == 'The plain version.'

    def test_an_html_only_body_becomes_text(self, deliver, inbox):
        html = (
            '<html><head><style>p{color:red}</style></head><body><h1>Order</h1>'
            '<p>See <a href="https://example.com/o/1">the order</a> now.</p>'
            '<ul><li>One</li><li>Two</li></ul><script>alert(1)</script></body></html>'
        )

        task = the_task(deliver(mail(inbox, text=None, html=html)))

        assert (
            task.description == 'Order\nSee the order (https://example.com/o/1) now.\n- One\n- Two'
        )

    def test_a_link_whose_text_is_the_address_is_not_repeated(self, deliver, inbox):
        html = '<p><a href="https://example.com/x">https://example.com/x</a></p>'

        assert the_task(deliver(mail(inbox, text=None, html=html))).description == (
            'https://example.com/x'
        )

    def test_unsafe_links_show_their_text_only(self, deliver, inbox):
        html = '<p><a href="javascript:alert(1)">click</a> <a href="/relative">there</a></p>'

        assert the_task(deliver(mail(inbox, text=None, html=html))).description == 'click there'

    def test_no_body(self, deliver, inbox):
        task = the_task(deliver(mail(inbox, subject='Just a title', text='')))

        assert task.description == ''

    def test_quoted_history_is_kept(self, deliver, inbox):
        text = 'Please do this.\n\n> On Monday Ann wrote:\n> the original request'

        assert the_task(deliver(mail(inbox, text=text))).description == text

    def test_a_long_body_is_cut_with_a_final_mark(self, deliver, inbox):
        from ..services import DESCRIPTION_MAX

        task = the_task(deliver(mail(inbox, text='a' * (DESCRIPTION_MAX + 500))))

        assert len(task.description) == DESCRIPTION_MAX
        assert task.description.endswith('\n…')
        assert task.description.splitlines()[-1] == '…'

    def test_a_body_at_the_limit_is_not_marked(self, deliver, inbox):
        from ..services import DESCRIPTION_MAX

        task = the_task(deliver(mail(inbox, text='a' * DESCRIPTION_MAX)))

        assert task.description == 'a' * DESCRIPTION_MAX

    def test_a_huge_plain_body_is_sliced_before_any_copy(self, deliver, inbox, monkeypatch):
        seen: list[int] = []
        real = inbound._clean_body

        def spy(text: str) -> str:
            seen.append(len(text))
            return real(text)

        monkeypatch.setattr(inbound, '_clean_body', spy)

        task = the_task(deliver(mail(inbox, text='word ' * 2_000_000)))  # 10 MB of text

        assert seen and max(seen) <= inbound.MAX_TEXT_CHARS
        assert inbound.MAX_TEXT_CHARS < 10 * inbound.DESCRIPTION_MAX
        assert len(task.description) == inbound.DESCRIPTION_MAX
        assert task.description.endswith('\n…')

    def test_a_huge_html_body_is_sliced_before_it_is_converted(self, deliver, inbox, monkeypatch):
        seen: list[int] = []
        real = inbound.html_to_text

        def spy(html: str, **kwargs) -> str:
            seen.append(len(html))
            return real(html, **kwargs)

        monkeypatch.setattr(inbound, 'html_to_text', spy)

        task = the_task(deliver(mail(inbox, text=None, html='<p>word</p>' * 1_000_000)))

        assert seen == [inbound.MAX_HTML_CHARS]
        assert 0 < len(task.description) <= inbound.DESCRIPTION_MAX

    def test_html_links_cannot_inject_html_or_unsafe_hrefs_into_the_page(self, deliver, inbox, web):
        html = (
            '<p><a href="javascript:alert(1)">js</a> <a href="data:text/html,<b>x</b>">data</a> '
            '<a href="https://ok.example/a">fine</a> <a href="https://e.example/x)[y](javascript:z)">'
            'label</a> <img src="x" onerror="alert(1)"><script>alert(2)</script></p>'
        )

        task = the_task(deliver(mail(inbox, text=None, html=html)))
        page = web.get(f'/todo/{task.number}/').content.decode()

        hrefs = re.findall(r'href="([^"]*)"', page)
        assert 'href="https://ok.example/a"' in page
        assert not [h for h in hrefs if h.lower().startswith(('javascript:', 'data:', 'vbscript:'))]
        assert 'data:text' not in task.description
        assert '<script>alert' not in page and 'onerror' not in page and '<iframe' not in page

    def test_control_characters_are_dropped(self, deliver, inbox):
        task = the_task(deliver(mail(inbox, text='a\x00b\x07c\td\ne')))

        assert task.description == 'abc\td\ne'

    def test_an_attachment_text_part_is_not_the_body(self, deliver, inbox):
        raw = mail(
            inbox,
            text='the real body',
            attachments=[Attachment('notes.txt', b'attached notes', 'text', 'plain')],
        )

        task = the_task(deliver(raw))

        assert task.description == 'the real body'
        assert TaskAttachment.objects.get(task=task).filename == 'notes.txt'

    def test_the_description_is_rendered_as_markdown_not_html(self, deliver, inbox, web):
        task = the_task(deliver(mail(inbox, text='<script>alert(1)</script> **bold**')))

        page = web.get(f'/todo/{task.number}/').content.decode()

        assert '<script>alert(1)</script>' not in page
        assert '<strong>bold</strong>' in page


class TestAttachments:
    def test_a_pdf_becomes_an_attachment_of_the_task(
        self, deliver, inbox, fake_storage, funded_user
    ):
        message = deliver(mail(inbox, attachments=[pdf('contract.pdf')]))

        task = the_task(message)
        [attachment] = list(TaskAttachment.objects.filter(task=task))
        data = fake_storage.objects[attachment_key(funded_user.pk, attachment.pk)]
        assert attachment.filename == 'contract.pdf'
        assert attachment.uploaded and attachment.user == funded_user
        assert attachment.size_bytes == len(data)
        assert attachment.sha256 == base64.b64encode(hashlib.sha256(data).digest()).decode()
        assert attachment.inline_type == 'application/pdf'
        assert fake_storage.content_types[attachment_key(funded_user.pk, attachment.pk)] == (
            'application/pdf'
        )
        assert get_account(funded_user).stored_bytes == len(data)
        assert one_email(message).note == ''

    def test_any_type_is_accepted(self, deliver, inbox, fake_storage):
        raw = mail(
            inbox,
            attachments=[
                Attachment('data.csv', b'a,b\n1,2\n', 'text', 'csv'),
                Attachment('archive.zip', b'PK\x03\x04' + b'0' * 40, 'application', 'zip'),
                jpeg('photo.jpg'),
            ],
        )

        task = the_task(deliver(raw))

        by_name = {a.filename: a for a in TaskAttachment.objects.filter(task=task)}
        assert set(by_name) == {'data.csv', 'archive.zip', 'photo.jpg'}
        assert by_name['data.csv'].inline_type == ''  # only an inline-safe type is ever shown
        assert by_name['archive.zip'].inline_type == ''
        assert by_name['photo.jpg'].inline_type == 'image/jpeg'
        assert not by_name['data.csv'].is_inline

    def test_stored_bytes_are_exactly_the_files(self, deliver, inbox, funded_user):
        deliver(mail(inbox, attachments=[pdf(), png()]))

        total = sum(a.size_bytes for a in TaskAttachment.objects.all())
        assert get_account(funded_user).stored_bytes == total > 0

    def test_a_nameless_or_hostile_name_is_cleaned(self, deliver, inbox):
        raw = mail(
            inbox,
            attachments=[
                Attachment('../../etc/passwd', b'x' * 10, 'application', 'octet-stream'),
                Attachment('a' * 400 + '.pdf', b'y' * 10, 'application', 'octet-stream'),
            ],
        )

        names = {a.filename for a in TaskAttachment.objects.filter(task=the_task(deliver(raw)))}

        assert '.._.._etc_passwd' in names
        assert all(len(name) <= 255 and '/' not in name for name in names)

    def test_a_tiny_inline_logo_is_not_an_attachment(self, deliver, inbox):
        raw = mail(inbox, text=None, html=HTML_RECEIPT, attachments=[logo((48, 48)), pdf()])

        task = the_task(deliver(raw))

        assert [a.filename for a in TaskAttachment.objects.filter(task=task)] == ['invoice.pdf']

    def test_a_large_inline_image_is_an_attachment(self, deliver, inbox):
        raw = mail(inbox, text=None, html=HTML_RECEIPT, attachments=[logo((600, 400))])

        task = the_task(deliver(raw))

        assert TaskAttachment.objects.filter(task=task, filename='logo.png').exists()

    def test_files_over_the_size_limit_are_skipped_with_a_note(self, deliver, inbox, fake_storage):
        config = TodoSettings.load()
        config.max_attachment_bytes = 1000
        config.save()
        raw = mail(
            inbox,
            attachments=[
                Attachment('small.bin', b'a' * 500, 'application', 'octet-stream'),
                Attachment('big.bin', b'b' * 5000, 'application', 'octet-stream'),
                Attachment('big2.bin', b'c' * 5000, 'application', 'octet-stream'),
            ],
        )

        message = deliver(raw)

        assert [a.filename for a in TaskAttachment.objects.all()] == ['small.bin']
        email = one_email(message)
        assert email.status == InboundEmailStatus.ACCEPTED
        assert email.note == '2 of 3 files skipped: too large'

    def test_one_skipped_file(self, deliver, inbox):
        config = TodoSettings.load()
        config.max_attachment_bytes = 1000
        config.save()
        raw = mail(
            inbox,
            attachments=[
                Attachment('a.bin', b'a' * 500, 'application', 'octet-stream'),
                Attachment('b.bin', b'a' * 500, 'application', 'octet-stream'),
                Attachment('big.bin', b'b' * 5000, 'application', 'octet-stream'),
            ],
        )

        assert one_email(deliver(raw)).note == '1 of 3 files skipped: too large'

    def test_only_up_to_the_per_task_cap_are_kept(self, deliver, inbox):
        config = TodoSettings.load()
        config.max_attachments_per_task = 2
        config.save()
        files = [
            Attachment(f'f{i}.bin', bytes([i]) * 20, 'application', 'octet-stream')
            for i in range(4)
        ]

        message = deliver(mail(inbox, attachments=files))

        assert TaskAttachment.objects.count() == 2
        assert 'files skipped' in one_email(message).note
        assert one_email(message).note.startswith('2 of 4 files skipped')

    def test_too_large_and_over_the_cap_are_both_counted(self, deliver, inbox):
        config = TodoSettings.load()
        config.max_attachments_per_task = 1
        config.max_attachment_bytes = 1000
        config.save()
        files = [
            Attachment('a.bin', b'a' * 20, 'application', 'octet-stream'),
            Attachment('b.bin', b'b' * 20, 'application', 'octet-stream'),
            Attachment('big.bin', b'c' * 5000, 'application', 'octet-stream'),
        ]

        note = one_email(deliver(mail(inbox, attachments=files))).note

        assert note == '2 of 3 files skipped: 1 too large, 1 over the limit of 1 per task'

    def test_a_frozen_storage_account_gets_the_task_but_no_files(
        self, deliver, inbox, fake_storage, funded_user
    ):
        TodoAccount.objects.filter(pk=get_account(funded_user).pk).update(frozen_at=timezone.now())
        before = dict(fake_storage.objects)

        message = deliver(mail(inbox, subject='Frozen', attachments=[pdf()]))

        email = one_email(message)
        assert email.status == InboundEmailStatus.ACCEPTED
        assert email.task is not None and email.task.title == 'Frozen'
        assert email.note == 'files skipped: storage is paused'
        assert not TaskAttachment.objects.exists()
        assert get_account(funded_user).stored_bytes == 0
        assert set(fake_storage.objects) == set(before) - {message.s3_key}

    def test_every_file_too_large_and_nothing_else_is_rejected(self, deliver, inbox):
        config = TodoSettings.load()
        config.max_attachment_bytes = 100
        config.save()
        raw = build_email(
            subject='',
            text='',
            attachments=[Attachment('big.bin', b'a' * 500, 'application', 'octet-stream')],
            ses=Ses(recipient=inbox),
        )

        email = one_email(deliver(raw))

        assert email.status == InboundEmailStatus.REJECTED_TOO_LARGE
        assert email.task is None and not Task.objects.exists()

    def test_a_message_forwarded_as_an_attachment_is_searched_too(self, deliver, inbox):
        raw = build_email(
            subject='Fwd: look',
            text='See attached.',
            nested=build_email(attachments=[pdf('inner.pdf')]),
            ses=Ses(recipient=inbox),
        )

        task = the_task(deliver(raw))

        assert 'inner.pdf' in {a.filename for a in TaskAttachment.objects.filter(task=task)}


class TestNoContent:
    def test_no_subject_no_body_no_files(self, deliver, inbox):
        message = deliver(mail(inbox, subject='', text=''))

        email = one_email(message)
        assert email.status == InboundEmailStatus.NO_CONTENT
        assert email.task is None and not Task.objects.exists()
        assert message.status == InboundMessageStatus.DONE

    def test_a_forward_prefix_alone_is_no_subject(self, deliver, inbox):
        assert one_email(deliver(mail(inbox, subject='Fwd:', text=''))).status == 'no_content'

    def test_only_a_file_is_content(self, deliver, inbox):
        message = deliver(mail(inbox, subject='', text='', attachments=[pdf()]))

        task = the_task(message)
        assert task.title == '(no subject)'
        assert TaskAttachment.objects.filter(task=task).count() == 1

    def test_only_a_subject_is_content(self, deliver, inbox):
        assert the_task(deliver(mail(inbox, subject='Just this', text=''))).title == 'Just this'


class TestVerdictRejections:
    @pytest.mark.parametrize(
        ('field', 'status'),
        [('spam', 'rejected_spam'), ('virus', 'rejected_virus')],
    )
    def test_spam_and_virus_are_dropped_keeping_metadata_only(
        self, deliver, inbox, fake_storage, field, status
    ):
        ses = Ses(
            recipient=inbox,
            spam='FAIL' if field == 'spam' else 'PASS',
            virus='FAIL' if field == 'virus' else 'PASS',
        )
        raw = build_email(subject='Win!', ses=ses)

        email = one_email(deliver(raw))

        assert email.status == status
        assert email.subject == 'Win!' and email.from_address == 'orders@staples.com'
        assert email.task is None and not Task.objects.exists()
        assert not [k for k in fake_storage.objects if k.startswith('todo/users/')]

    def test_gray_is_accepted(self, deliver, inbox):
        raw = build_email(ses=Ses(recipient=inbox, spam='GRAY', virus='PROCESSING_FAILED'))

        assert one_email(deliver(raw)).status == InboundEmailStatus.ACCEPTED


class TestDecisionOrder:
    """Spam, virus, the allowlist, the content, then the credits."""

    @pytest.fixture(autouse=True)
    def _restricted(self, funded_user):
        TodoAccount.objects.filter(pk=get_account(funded_user).pk).update(
            sender_allowlist=['someone@else.test']
        )

    def test_spam_is_decided_before_the_allowlist(self, deliver, inbox):
        raw = build_email(ses=Ses(recipient=inbox, spam='FAIL'))

        assert one_email(deliver(raw)).status == 'rejected_spam'

    def test_virus_is_decided_before_the_allowlist(self, deliver, inbox):
        raw = build_email(ses=Ses(recipient=inbox, virus='FAIL'))

        assert one_email(deliver(raw)).status == 'rejected_virus'

    def test_spam_wins_over_virus(self, deliver, inbox):
        raw = build_email(ses=Ses(recipient=inbox, spam='FAIL', virus='FAIL'))

        assert one_email(deliver(raw)).status == 'rejected_spam'

    def test_the_allowlist_is_decided_before_the_credits(self, deliver, broke):
        TodoAccount.objects.filter(pk=get_account(broke).pk).update(
            sender_allowlist=['someone@else.test']
        )

        email = one_email(deliver(mail(inbox_address_for(broke))))

        assert email.status == 'rejected_sender'
        assert not Notification.objects.filter(user=broke).exists()

    def test_a_refused_sender_with_nothing_in_the_mail_is_still_a_refused_sender(
        self, deliver, inbox
    ):
        assert one_email(deliver(mail(inbox, subject='', text=''))).status == 'rejected_sender'


class TestAllowlist:
    @pytest.fixture(autouse=True)
    def _allowlist(self, funded_user):
        TodoAccount.objects.filter(pk=get_account(funded_user).pk).update(
            sender_allowlist=['orders@staples.com', '*@acme.test']
        )

    def status(self, deliver, inbox, *, from_addr='orders@staples.com', **ses) -> str:
        raw = build_email(from_addr=from_addr, text='x', ses=Ses(recipient=inbox, **ses))
        return one_email(deliver(raw)).status

    def test_a_listed_sender_with_spf_passes(self, deliver, inbox):
        assert self.status(deliver, inbox, spf='PASS', dkim='FAIL') == 'accepted'

    def test_a_listed_sender_with_only_an_aligned_dkim_passes(self, deliver, inbox):
        assert self.status(deliver, inbox, spf='FAIL', dkim='PASS') == 'accepted'

    def test_a_listed_sender_that_ses_did_not_authenticate_is_rejected(self, deliver, inbox):
        assert self.status(deliver, inbox, spf='FAIL', dkim='FAIL') == 'rejected_sender'

    def test_the_rejection_says_why(self, deliver, inbox):
        raw = build_email(text='x', ses=Ses(recipient=inbox, spf='FAIL', dkim='FAIL'))

        assert 'could not be verified' in one_email(deliver(raw)).note

    def test_an_unlisted_sender_is_rejected_even_when_authenticated(self, deliver, inbox):
        assert self.status(deliver, inbox, from_addr='boss@other.test') == 'rejected_sender'

    def test_a_dkim_pass_for_an_unrelated_domain_does_not_authenticate_a_listed_from(
        self, deliver, inbox
    ):
        status = self.status(
            deliver, inbox, spf='FAIL', dkim='PASS', dkim_domain='attacker.test', dmarc=None
        )

        assert status == 'rejected_sender'

    def test_spf_for_an_unrelated_envelope_domain_does_not_authenticate_a_listed_from(
        self, deliver, inbox
    ):
        status = self.status(
            deliver, inbox, return_path='bounce@attacker.test', dkim='FAIL', dmarc='FAIL'
        )

        assert status == 'rejected_sender'

    def test_headers_the_sender_wrote_authenticate_nothing(self, deliver, inbox):
        raw = build_email(
            text='x',
            headers=[('X-SES-Spf-Verdict', 'PASS'), ('Received-SPF', 'pass')],
            ses=Ses(recipient=inbox, spf=None, dkim=None, dmarc=None),
        )

        assert one_email(deliver(raw)).status == 'rejected_sender'

    def test_a_domain_wildcard(self, deliver, inbox):
        assert self.status(deliver, inbox, from_addr='anyone@acme.test') == 'accepted'

    def test_a_wildcard_does_not_cover_subdomains(self, deliver, inbox):
        assert self.status(deliver, inbox, from_addr='anyone@mail.acme.test') == 'rejected_sender'

    def test_plus_tags_and_case_are_normalized(self, deliver, inbox):
        assert self.status(deliver, inbox, from_addr='Orders+x@Staples.com') == 'accepted'

    def test_a_rejected_sender_creates_nothing(self, deliver, inbox, fake_storage):
        raw = build_email(
            from_addr='boss@other.test',
            text='x',
            attachments=[pdf()],
            ses=Ses(recipient=inbox),
        )

        email = one_email(deliver(raw))

        assert email.status == 'rejected_sender' and email.task is None
        assert not Task.objects.exists() and not TaskAttachment.objects.exists()

    def test_an_empty_allowlist_accepts_anyone_without_authentication(
        self, deliver, inbox, funded_user
    ):
        TodoAccount.objects.filter(pk=get_account(funded_user).pk).update(sender_allowlist=[])

        status = self.status(
            deliver, inbox, from_addr='stranger@anywhere.test', spf=None, dkim=None, dmarc=None
        )

        assert status == 'accepted'


@pytest.fixture
def broke(db):
    """A user with no credits and no free tasks left, so a task can't be paid."""
    config = TodoSettings.load()
    config.free_tasks_per_month = 0
    config.task_price = 5
    config.save()
    return make_user(email='broke@example.com')


class TestCredits:
    def test_a_task_that_cannot_be_paid_is_rejected_with_a_notification(
        self, deliver, broke, fake_storage
    ):
        message = deliver(mail(inbox_address_for(broke), subject='Pay me', attachments=[pdf()]))

        email = one_email(message)
        assert email.status == InboundEmailStatus.REJECTED_CREDITS
        assert email.task is None
        assert message.status == InboundMessageStatus.DONE
        assert not Task.objects.exists() and not TaskAttachment.objects.exists()
        assert not [k for k in fake_storage.objects if k.startswith(user_prefix(broke.pk))]
        assert get_account(broke).stored_bytes == 0
        [note] = list(Notification.objects.filter(user=broke))
        assert note.title == 'Emailed tasks were not created: not enough credits'
        assert note.url == reverse('billing:credits-history')
        assert note.source == 'todo' and note.key == inbound.CREDITS_NOTICE_KEY

    def test_the_balance_is_untouched(self, deliver, broke):
        add_credits(broke, 3, source='test')

        deliver(mail(inbox_address_for(broke)))

        assert get_balance(broke) == 3

    def test_enough_credits_makes_the_task(self, deliver, broke):
        add_credits(broke, 5, source='test')

        message = deliver(mail(inbox_address_for(broke)))

        assert one_email(message).status == InboundEmailStatus.ACCEPTED
        assert get_balance(broke) == 0
        assert not Notification.objects.filter(user=broke).exists()

    def test_creation_blocked_by_an_unpaid_task_is_rejected_too(self, deliver, funded_user):
        TodoAccount.objects.filter(pk=get_account(funded_user).pk).update(
            creation_blocked_at=timezone.now()
        )

        message = deliver(mail(inbox_address_for(funded_user)))

        assert one_email(message).status == InboundEmailStatus.REJECTED_CREDITS
        assert Notification.objects.filter(user=funded_user).count() == 1
        assert not Task.objects.exists()

    def test_a_race_that_spends_the_credits_first_is_rejected_by_create_task(
        self, deliver, broke, monkeypatch
    ):
        """The cheap look says yes, `create_task` (under the lock) says no: the same outcome and
        nothing left behind."""
        add_credits(broke, 5, source='test')
        from ..services import billing

        monkeypatch.setattr(billing, 'cannot_afford', lambda *a, **k: False)

        real = billing.spend_credits

        def poor(*args, **kwargs):
            from apps.billing.services import InsufficientCreditsError

            raise InsufficientCreditsError('no')

        monkeypatch.setattr(billing, 'spend_credits', poor)
        try:
            message = deliver(mail(inbox_address_for(broke), attachments=[pdf()]))
        finally:
            monkeypatch.setattr(billing, 'spend_credits', real)

        assert one_email(message).status == InboundEmailStatus.REJECTED_CREDITS
        assert not Task.objects.exists() and not TaskAttachment.objects.exists()
        assert get_account(broke).stored_bytes == 0
        assert get_account(broke).month_task_count == 0
        assert Notification.objects.filter(user=broke).count() == 1

    def test_one_notification_per_email_not_per_retry(self, deliver, broke):
        message = deliver(mail(inbox_address_for(broke)))
        InboundMessage.objects.filter(pk=message.pk).update(
            status=InboundMessageStatus.PENDING, next_attempt_at=None
        )

        inbound.ingest(message.pk)

        assert Notification.objects.filter(user=broke).count() == 1

    def test_many_rejected_emails_leave_one_notification(self, deliver, broke):
        for i in range(4):
            deliver(mail(inbox_address_for(broke), subject=f'attempt {i}'))

        assert InboundEmail.objects.filter(user=broke).count() == 4
        [note] = list(Notification.objects.filter(user=broke))
        assert note.key == inbound.CREDITS_NOTICE_KEY

    def test_the_notification_is_fresh_each_time(self, deliver, broke):
        deliver(mail(inbox_address_for(broke)))
        first = Notification.objects.get(user=broke)
        Notification.objects.filter(pk=first.pk).update(read_at=timezone.now())

        deliver(mail(inbox_address_for(broke)))

        second = Notification.objects.get(user=broke)
        assert second.pk != first.pk and second.read_at is None

    def test_nothing_the_sender_wrote_reaches_the_notification(self, deliver, broke):
        raw = mail(
            inbox_address_for(broke),
            subject='SENDER-SUBJECT <b>x</b>',
            text='SENDER-BODY',
            from_addr='SENDER-NAME@sender.test',
        )

        deliver(raw)

        note = Notification.objects.get(user=broke)
        for part in (note.title, note.body, note.url, note.key):
            assert 'SENDER' not in part and '<' not in part

    def test_another_users_notification_is_left_alone(self, funded_user, broke, deliver):
        Notification.objects.create(
            user=funded_user, source='todo', title='mine', key=inbound.CREDITS_NOTICE_KEY
        )

        deliver(mail(inbox_address_for(broke)))

        assert Notification.objects.filter(user=funded_user).count() == 1
        assert Notification.objects.filter(user=broke).count() == 1

    def test_the_rows_ceiling_bounds_the_notifications_too(self, deliver, broke):
        TodoSettings.objects.update_or_create(pk=1, defaults={'max_inbound_rows_per_day': 2})
        TodoSettings.load().__class__.objects.filter(pk=1).update(
            free_tasks_per_month=0, task_price=5
        )

        for _ in range(5):
            deliver(mail(inbox_address_for(broke)))

        assert InboundEmail.objects.filter(user=broke).count() == 2
        assert Notification.objects.filter(user=broke).count() == 1

    def test_a_failing_notification_does_not_lose_the_row(self, deliver, broke, monkeypatch):
        def broken(*args, **kwargs):
            raise RuntimeError('notifications are down')

        monkeypatch.setattr(inbound.notifications, 'notify', broken)

        message = deliver(mail(inbox_address_for(broke)))

        assert message.status == InboundMessageStatus.DONE
        assert one_email(message).status == InboundEmailStatus.REJECTED_CREDITS

    def test_the_open_task_cap_is_a_rejection_not_a_failure(self, deliver, inbox):
        config = TodoSettings.load()
        config.max_open_tasks_per_user = 1
        config.save()
        deliver(mail(inbox, subject='first'))

        message = deliver(mail(inbox, subject='second'))

        assert message.status == InboundMessageStatus.DONE
        email = one_email(message)
        assert email.status == InboundEmailStatus.REJECTED_TASK_LIMIT
        assert email.note == 'Too many open tasks.'
        assert Task.objects.count() == 1

    def test_the_cap_is_checked_before_the_ai_call_and_the_uploads(
        self, deliver, inbox, funded_user, fake_title_parser, fake_storage
    ):
        config = TodoSettings.load()
        config.max_open_tasks_per_user = 1
        config.save()
        deliver(mail(inbox, subject='first'))
        fake_title_parser.replies['pay rent tomorrow'] = ParsedTitle(
            has_date=True,
            clean_title='pay rent',
            due_date=date(2026, 10, 29),
            matched_text='tomorrow',
            confidence=0.9,
        )
        before = len(fake_storage.objects)

        message = deliver(mail(inbox, subject='pay rent tomorrow', attachments=[pdf()]))

        assert one_email(message).status == InboundEmailStatus.REJECTED_TASK_LIMIT
        assert fake_title_parser.calls == 0
        assert len(fake_storage.objects) == before
        assert not TaskAttachment.objects.exists()

    def test_a_race_past_the_cap_is_still_a_task_limit_rejection(self, deliver, inbox, monkeypatch):
        """The cheap look says there is room, `create_task` (under the lock) says there is not."""
        config = TodoSettings.load()
        config.max_open_tasks_per_user = 1
        config.save()
        deliver(mail(inbox, subject='first'))
        monkeypatch.setattr(inbound, '_at_task_cap', lambda *a, **k: False)

        message = deliver(mail(inbox, subject='second', attachments=[pdf()]))

        assert one_email(message).status == InboundEmailStatus.REJECTED_TASK_LIMIT
        assert Task.objects.count() == 1 and not TaskAttachment.objects.exists()

    def test_the_settings_page_says_too_many_open_tasks(self, deliver, inbox, web):
        config = TodoSettings.load()
        config.max_open_tasks_per_user = 1
        config.save()
        deliver(mail(inbox, subject='first'))
        deliver(mail(inbox, subject='second'))

        page = web.get('/todo/settings/').content.decode()

        assert 'too many open tasks' in page and 'too large' not in page


class TestDateReading:
    def dated(self, fake_title_parser, title='pay rent tomorrow', **fields):
        defaults: dict[str, Any] = dict(
            has_date=True,
            clean_title='pay rent',
            due_date=date(2026, 10, 29),
            matched_text='tomorrow',
            confidence=0.9,
        )
        fake_title_parser.replies[title] = ParsedTitle(**{**defaults, **fields})

    def test_the_ai_date_is_applied_and_the_clean_title_used(
        self, deliver, inbox, fake_title_parser
    ):
        self.dated(fake_title_parser, due_time=time(9, 30))

        task = the_task(deliver(mail(inbox, subject='pay rent tomorrow')))

        assert task.title == 'pay rent'
        assert task.due_date == date(2026, 10, 29)
        assert task.due_time == time(9, 30)
        assert one_email(InboundMessage.objects.get()).subject == 'pay rent tomorrow'

    def test_the_cleaned_subject_is_what_is_read(self, deliver, inbox, fake_title_parser):
        self.dated(fake_title_parser, title='pay rent tomorrow')

        task = the_task(deliver(mail(inbox, subject='Fwd: pay rent tomorrow')))

        assert task.title == 'pay rent' and task.due_date == date(2026, 10, 29)

    def test_a_repeat_rule_is_applied(self, deliver, inbox, fake_title_parser):
        self.dated(
            fake_title_parser,
            title='water plants every week',
            clean_title='water plants',
            due_date=date(2026, 10, 30),
            rrule='FREQ=WEEKLY',
        )

        task = the_task(deliver(mail(inbox, subject='water plants every week')))

        assert task.title == 'water plants'
        assert task.recurrence is not None and task.recurrence.rrule == 'FREQ=WEEKLY'

    def test_a_subject_with_no_date_makes_no_ai_call(self, deliver, inbox, fake_title_parser):
        task = the_task(deliver(mail(inbox, subject='Buy milk')))

        assert task.due_date is None
        assert fake_title_parser.calls == 0

    def test_a_dated_looking_subject_the_ai_finds_nothing_in(
        self, deliver, inbox, fake_title_parser
    ):
        task = the_task(deliver(mail(inbox, subject='Call 555 1234')))

        assert fake_title_parser.calls == 1
        assert task.title == 'Call 555 1234' and task.due_date is None

    def test_an_unavailable_ai_leaves_the_task_undated(self, deliver, inbox, fake_title_parser):
        fake_title_parser.replies['pay rent tomorrow'] = ParseUnavailable('timeout')

        message = deliver(mail(inbox, subject='pay rent tomorrow'))

        task = the_task(message)
        assert task.title == 'pay rent tomorrow' and task.due_date is None
        assert message.status == InboundMessageStatus.DONE

    def test_an_unconfigured_ai_leaves_the_task_undated(
        self, deliver, inbox, monkeypatch, settings
    ):
        monkeypatch.setattr(parsing, '_client', None)
        settings.OPENAI_API_KEY = ''

        task = the_task(deliver(mail(inbox, subject='pay rent tomorrow')))

        assert task.due_date is None

    def test_an_unexpected_ai_failure_leaves_the_task_undated(
        self, deliver, inbox, fake_title_parser
    ):
        fake_title_parser.replies['pay rent tomorrow'] = RuntimeError('bug')

        task = the_task(deliver(mail(inbox, subject='pay rent tomorrow')))

        assert task.due_date is None

    def test_the_ai_rate_limits_are_spent_and_respected(
        self, deliver, inbox, fake_title_parser, settings
    ):
        settings.RATELIMIT_ENABLE = True
        settings.RATELIMIT_RULES = {**settings.RATELIMIT_RULES, 'TODO_PARSE_USER': (1, 3600)}
        self.dated(fake_title_parser, title='pay rent tomorrow')
        self.dated(fake_title_parser, title='pay gas tomorrow', clean_title='pay gas')

        first = the_task(deliver(mail(inbox, subject='pay rent tomorrow')))
        second = the_task(deliver(mail(inbox, subject='pay gas tomorrow')))

        assert first.due_date == date(2026, 10, 29)
        assert second.due_date is None and second.title == 'pay gas tomorrow'
        assert fake_title_parser.calls == 1

    def test_no_ai_call_for_a_user_who_cannot_pay(self, deliver, broke, fake_title_parser):
        self.dated(fake_title_parser, title='pay rent tomorrow')

        deliver(mail(inbox_address_for(broke), subject='pay rent tomorrow'))

        assert fake_title_parser.calls == 0


class TestRowsCeiling:
    def set_ceiling(self, rows: int) -> None:
        TodoSettings.objects.update_or_create(pk=1, defaults={'max_inbound_rows_per_day': rows})

    def spam(self, inbox: str) -> bytes:
        return build_email(ses=Ses(recipient=inbox, spam='FAIL'))

    def test_junk_beyond_the_ceiling_is_logged_not_listed(
        self, deliver, inbox, funded_user, caplog
    ):
        self.set_ceiling(3)

        messages = [deliver(self.spam(inbox)) for _ in range(6)]

        assert InboundEmail.objects.filter(user=funded_user).count() == 3
        assert all(m.status == InboundMessageStatus.DONE for m in messages)
        assert 'not recorded' in caplog.text

    def test_accepted_mail_is_never_dropped_by_the_ceiling(self, deliver, inbox, funded_user):
        self.set_ceiling(2)
        for _ in range(3):
            deliver(self.spam(inbox))

        message = deliver(mail(inbox))

        assert one_email(message).status == InboundEmailStatus.ACCEPTED
        assert Task.objects.count() == 1

    def test_the_other_unaccepted_kinds_are_covered_too(self, deliver, inbox):
        self.set_ceiling(1)
        deliver(self.spam(inbox))

        empty = deliver(mail(inbox, subject='', text=''))

        assert not empty.emails.exists()

    def test_only_the_last_24_hours_count(self, deliver, inbox, funded_user):
        self.set_ceiling(1)
        deliver(self.spam(inbox))
        InboundEmail.objects.filter(user=funded_user).update(
            created_at=timezone.now() - timedelta(hours=25)
        )

        assert deliver(self.spam(inbox)).emails.count() == 1

    def test_the_ceiling_is_per_user(self, deliver, inbox):
        self.set_ceiling(1)
        other = make_user()
        add_credits(other, 10, source='test')
        deliver(self.spam(inbox))

        assert deliver(self.spam(inbox_address_for(other))).emails.count() == 1

    def test_a_dropped_row_does_not_make_the_message_fail_or_retry(self, deliver, inbox):
        self.set_ceiling(1)
        deliver(self.spam(inbox))

        message = deliver(self.spam(inbox))

        assert message.status == InboundMessageStatus.DONE and message.attempts == 1


class TestRetries:
    def seed(self, fake_storage, raw: bytes) -> InboundMessage:
        key = f'{INBOUND_PREFIX}{uuid.uuid4()}'
        fake_storage.seed(key, raw)
        return InboundMessage.objects.create(s3_key=key)

    def test_a_failure_schedules_a_retry_with_backoff(self, fake_storage, inbox, monkeypatch):
        message = self.seed(fake_storage, mail(inbox))
        monkeypatch.setattr(
            fake_storage,
            'get_object_bytes',
            lambda *a, **k: (_ for _ in ()).throw(
                ClientError({'Error': {'Code': 'InternalError', 'Message': 'x'}}, 'GetObject')
            ),
        )

        inbound.ingest(message.pk)

        message.refresh_from_db()
        assert message.status == InboundMessageStatus.PENDING
        assert message.attempts == 1
        assert 'ClientError' in message.last_error
        delay = message.next_attempt_at - timezone.now()
        assert timedelta(seconds=30) < delay <= inbound.RETRY_BACKOFF[0]
        assert message.queued_at is None

    def test_it_is_not_retried_before_its_time_and_succeeds_after(
        self, fake_storage, inbox, django_capture_on_commit_callbacks, monkeypatch
    ):
        message = self.seed(fake_storage, mail(inbox))
        real = fake_storage.get_object_bytes
        calls = []

        def flaky(key, **kwargs):
            calls.append(key)
            if len(calls) == 1:
                raise ClientError({'Error': {'Code': 'InternalError', 'Message': 'x'}}, 'GetObject')
            return real(key, **kwargs)

        monkeypatch.setattr(fake_storage, 'get_object_bytes', flaky)
        inbound.ingest(message.pk)

        inbound.ingest(message.pk)  # too early: the claim is refused
        assert len(calls) == 1
        InboundMessage.objects.filter(pk=message.pk).update(
            next_attempt_at=timezone.now() - timedelta(seconds=1)
        )
        with django_capture_on_commit_callbacks(execute=True):
            inbound.ingest(message.pk)

        message.refresh_from_db()
        assert message.status == InboundMessageStatus.DONE and message.attempts == 2
        assert Task.objects.count() == 1

    def test_after_three_attempts_it_fails_and_parks_the_object(
        self, fake_storage, inbox, monkeypatch
    ):
        message = self.seed(fake_storage, mail(inbox))
        monkeypatch.setattr(
            fake_storage,
            'get_object_bytes',
            lambda *a, **k: (_ for _ in ()).throw(RuntimeError('boom')),
        )

        for _ in range(inbound.MAX_ATTEMPTS):
            InboundMessage.objects.filter(pk=message.pk).update(next_attempt_at=None)
            inbound.ingest(message.pk)

        message.refresh_from_db()
        assert message.attempts == 3 and message.next_attempt_at is None
        assert 'RuntimeError: boom' in message.last_error
        monkeypatch.undo()
        assert_parked(fake_storage, message)

    def test_a_failed_message_is_never_claimed_again(self, fake_storage):
        message = self.seed(fake_storage, b'x')
        InboundMessage.objects.filter(pk=message.pk).update(status=InboundMessageStatus.FAILED)

        inbound.ingest(message.pk)

        message.refresh_from_db()
        assert message.attempts == 0

    def test_a_message_that_keeps_killing_its_worker_is_given_up_on(self, fake_storage):
        message = self.seed(fake_storage, b'x')
        InboundMessage.objects.filter(pk=message.pk).update(attempts=inbound.MAX_ATTEMPTS)

        inbound.ingest(message.pk)

        message.refresh_from_db()
        assert message.status == InboundMessageStatus.FAILED
        assert 'interrupted' in message.last_error
        assert_parked(fake_storage, message)

    def test_a_running_claim_is_not_taken_twice(self, fake_storage, inbox):
        message = self.seed(fake_storage, mail(inbox))
        assert inbound._claim(message.pk) is not None

        assert inbound._claim(message.pk) is None

    def test_an_expired_lease_can_be_claimed_again(self, fake_storage):
        message = self.seed(fake_storage, b'x')
        assert inbound._claim(message.pk) is not None
        InboundMessage.objects.filter(pk=message.pk).update(
            next_attempt_at=timezone.now() - timedelta(seconds=1)
        )

        claimed = inbound._claim(message.pk)

        assert claimed is not None and claimed.attempts == 2

    def test_a_slow_worker_whose_lease_ran_out_cannot_overwrite_the_new_claim(
        self, fake_storage, inbox
    ):
        message = self.seed(fake_storage, mail(inbox))
        slow = inbound._claim(message.pk)
        InboundMessage.objects.filter(pk=message.pk).update(
            next_attempt_at=timezone.now() - timedelta(seconds=1)
        )
        fast = inbound._claim(message.pk)
        assert slow is not None and fast is not None

        assert inbound._mark_done(slow) is False
        assert inbound._mark_done(fast) is True

    def test_an_oversized_object_fails_at_once(self, fake_storage, monkeypatch):
        monkeypatch.setattr(inbound, 'MAX_RAW_BYTES', 10)
        message = self.seed(fake_storage, b'x' * 100)

        inbound.ingest(message.pk)

        message.refresh_from_db()
        assert message.status == InboundMessageStatus.FAILED and message.attempts == 1
        assert 'over 10 bytes' in message.last_error
        assert_parked(fake_storage, message)

    def test_a_missing_object_fails_at_once(self, fake_storage):
        message = InboundMessage.objects.create(s3_key=f'{INBOUND_PREFIX}gone')

        inbound.ingest(message.pk)

        message.refresh_from_db()
        assert message.status == InboundMessageStatus.FAILED
        assert 'missing' in message.last_error

    def test_an_unparseable_message_fails_at_once(self, fake_storage, monkeypatch):
        message = self.seed(fake_storage, b'x')
        monkeypatch.setattr(
            inbound.mime, 'parse_message', lambda raw: (_ for _ in ()).throw(ValueError('bad'))
        )

        inbound.ingest(message.pk)

        message.refresh_from_db()
        assert message.status == InboundMessageStatus.FAILED
        assert 'could not be parsed' in message.last_error

    def test_too_many_parts_fails_at_once(self, fake_storage, inbox):
        from apps.core.services.mail import mime as core_mime

        parts = b''.join(b'---\r\n\r\nx\r\n' for _ in range(core_mime.MAX_PARTS + 100))
        raw = (
            b'Content-Type: multipart/mixed; boundary=-\r\nSubject: x\r\n\r\n'
            + parts
            + b'-----\r\n'
        )
        message = self.seed(fake_storage, raw)

        inbound.ingest(message.pk)

        message.refresh_from_db()
        assert message.status == InboundMessageStatus.FAILED and message.attempts == 1

    def test_a_missing_object_after_the_work_was_done_is_done(self, deliver, inbox):
        message = deliver(mail(inbox))
        InboundMessage.objects.filter(pk=message.pk).update(
            status=InboundMessageStatus.PENDING, next_attempt_at=None
        )

        inbound.ingest(message.pk)  # the object is gone, the email row exists

        message.refresh_from_db()
        assert message.status == InboundMessageStatus.DONE
        assert Task.objects.count() == 1


class TestAtomicity:
    def seed(self, fake_storage, raw: bytes) -> InboundMessage:
        key = f'{INBOUND_PREFIX}{uuid.uuid4()}'
        fake_storage.seed(key, raw)
        return InboundMessage.objects.create(s3_key=key)

    def test_a_failure_for_one_recipient_does_not_redo_the_other(
        self, fake_storage, funded_user, inbox, django_capture_on_commit_callbacks, monkeypatch
    ):
        other = make_user()
        add_credits(other, 50, source='test')
        second = inbox_address_for(other)
        message = self.seed(
            fake_storage,
            build_email(
                subject='Both', attachments=[pdf()], ses=Ses(recipient=f'{inbox}, {second}')
            ),
        )
        real_put = fake_storage.put_object

        def failing_put(key, body, **kwargs):
            if key.startswith(user_prefix(other.pk)):
                raise ClientError({'Error': {'Code': 'InternalError', 'Message': 'x'}}, 'PutObject')
            real_put(key, body, **kwargs)

        monkeypatch.setattr(fake_storage, 'put_object', failing_put)
        with django_capture_on_commit_callbacks(execute=True):
            inbound.ingest(message.pk)

        message.refresh_from_db()
        assert message.status == InboundMessageStatus.PENDING and message.attempts == 1
        # The first user is complete; the second left nothing behind.
        assert InboundEmail.objects.filter(user=funded_user).count() == 1
        assert not InboundEmail.objects.filter(user=other).exists()
        assert not Task.objects.filter(user=other).exists()
        assert not [k for k in fake_storage.objects if k.startswith(user_prefix(other.pk))]
        assert get_account(other).stored_bytes == 0 and get_account(other).month_task_count == 0

        monkeypatch.setattr(fake_storage, 'put_object', real_put)
        InboundMessage.objects.filter(pk=message.pk).update(next_attempt_at=None)
        with django_capture_on_commit_callbacks(execute=True):
            inbound.ingest(message.pk)

        message.refresh_from_db()
        assert message.status == InboundMessageStatus.DONE
        assert Task.objects.filter(user=funded_user).count() == 1  # not duplicated
        assert Task.objects.filter(user=other).count() == 1
        assert TaskAttachment.objects.filter(user=funded_user).count() == 1

    def test_a_failing_second_file_deletes_the_first_again(
        self, fake_storage, inbox, funded_user, monkeypatch
    ):
        message = self.seed(fake_storage, mail(inbox, attachments=[pdf('a.pdf'), jpeg('b.jpg')]))
        real_put = fake_storage.put_object
        puts = []

        def failing_put(key, body, **kwargs):
            puts.append(key)
            if len(puts) == 2:
                raise ClientError({'Error': {'Code': 'InternalError', 'Message': 'x'}}, 'PutObject')
            real_put(key, body, **kwargs)

        monkeypatch.setattr(fake_storage, 'put_object', failing_put)

        inbound.ingest(message.pk)

        assert not [k for k in fake_storage.objects if k.startswith(user_prefix(funded_user.pk))]
        assert not Task.objects.exists() and not TaskAttachment.objects.exists()

    def test_a_failing_task_creation_leaves_no_objects_rows_or_charge(
        self, fake_storage, inbox, funded_user, monkeypatch
    ):
        message = self.seed(fake_storage, mail(inbox, attachments=[pdf()]))

        def boom(*args, **kwargs):
            raise RuntimeError('task creation is down')

        monkeypatch.setattr(inbound.tasks, 'create_task', boom)

        inbound.ingest(message.pk)

        message.refresh_from_db()
        assert message.status == InboundMessageStatus.PENDING
        assert not InboundEmail.objects.exists()
        assert not [k for k in fake_storage.objects if k.startswith(user_prefix(funded_user.pk))]
        assert get_account(funded_user).stored_bytes == 0

    def test_the_keys_are_the_same_on_every_try(self, fake_storage, inbox, funded_user):
        """A try killed after uploading leaves objects the next try overwrites, not orphans."""
        first = inbound.attachment_id_for(1, inbox, 3)
        assert first == inbound.attachment_id_for(1, inbox, 3)
        assert first != inbound.attachment_id_for(1, inbox, 4)
        assert first != inbound.attachment_id_for(2, inbox, 3)
        assert inbound.email_id_for(1, inbox) == inbound.email_id_for(1, inbox)
        assert inbound.email_id_for(1, inbox) != inbound.email_id_for(2, inbox)

    def test_a_recipient_is_not_ingested_twice(self, deliver, inbox, fake_storage):
        message = deliver(mail(inbox, attachments=[pdf()]))
        InboundMessage.objects.filter(pk=message.pk).update(
            status=InboundMessageStatus.PENDING, next_attempt_at=None
        )
        fake_storage.seed(message.s3_key, mail(inbox, attachments=[pdf()]))

        inbound.ingest(message.pk)

        assert Task.objects.count() == 1 and TaskAttachment.objects.count() == 1

    def test_objects_written_by_a_killed_try_are_overwritten_not_orphaned(
        self, fake_storage, inbox, funded_user
    ):
        message = self.seed(fake_storage, mail(inbox, attachments=[pdf()]))
        email_id = inbound.email_id_for(message.pk, inbox)
        # What a try that died after uploading, before its transaction, left behind.
        [leaf] = [
            a.index
            for a in inbound.mime.analyze(
                inbound.mime.parse_message(fake_storage.objects[message.s3_key]),
                allowed_types=None,
                max_file_bytes=10**9,
                min_inline_image_px=0,
                sniff=inbound._sniff,
            ).attachments
        ]
        orphan = attachment_key(funded_user.pk, inbound.attachment_id_for(message.pk, inbox, leaf))
        fake_storage.seed(orphan, b'stale')

        inbound.ingest(message.pk)

        assert email_id == one_email(message).pk
        [attachment] = list(TaskAttachment.objects.all())
        assert attachment_key(funded_user.pk, attachment.pk) == orphan
        assert fake_storage.objects[orphan] != b'stale'
        user_objects = [
            k for k in fake_storage.objects if k.startswith(user_prefix(funded_user.pk))
        ]
        assert user_objects == [orphan]


class TestFailedMessagesAreParked:
    def seed(self, fake_storage, raw: bytes) -> InboundMessage:
        key = f'{INBOUND_PREFIX}{uuid.uuid4()}'
        fake_storage.seed(key, raw)
        return InboundMessage.objects.create(s3_key=key)

    def test_a_retry_moves_the_object_back_and_the_ingest_then_works(
        self, fake_storage, inbox, django_capture_on_commit_callbacks, monkeypatch
    ):
        message = self.seed(fake_storage, mail(inbox))
        limit = inbound.MAX_RAW_BYTES
        monkeypatch.setattr(inbound, 'MAX_RAW_BYTES', 10)
        inbound.ingest(message.pk)
        message.refresh_from_db()
        assert_parked(fake_storage, message)
        parked_key = message.failed_key
        monkeypatch.setattr(inbound, 'MAX_RAW_BYTES', limit)

        with django_capture_on_commit_callbacks(execute=True):
            assert inbound.reset_message_for_retry(message)

        message.refresh_from_db()
        assert message.status == InboundMessageStatus.DONE
        assert message.failed_key == '' and parked_key not in fake_storage.objects
        assert Task.objects.count() == 1

    def test_only_a_failed_message_is_reset(self, fake_storage, monkeypatch):
        queued = []

        class Recorder:
            def enqueue(self, message_id):
                queued.append(message_id)

        monkeypatch.setattr(inbound, 'ingest_message', Recorder())
        message = self.seed(fake_storage, b'raw')

        assert not inbound.reset_message_for_retry(message)
        assert queued == []

    def test_a_retry_whose_move_back_fails_changes_nothing(self, fake_storage, monkeypatch):
        message = self.seed(fake_storage, b'raw')
        parked = failed_key_for(message.s3_key)
        fake_storage.objects[parked] = fake_storage.objects.pop(message.s3_key)
        InboundMessage.objects.filter(pk=message.pk).update(status='failed', failed_key=parked)
        message.refresh_from_db()

        def broken(*args, **kwargs):
            raise ClientError({'Error': {'Code': 'AccessDenied', 'Message': 'x'}}, 'CopyObject')

        monkeypatch.setattr(fake_storage, 'copy_object', broken)

        assert not inbound.reset_message_for_retry(message)

        message.refresh_from_db()
        assert message.status == 'failed' and message.failed_key == parked

    def test_a_failure_whose_move_fails_is_finished_by_the_next_poll(
        self, fake_storage, monkeypatch
    ):
        monkeypatch.setattr(inbound, 'MAX_RAW_BYTES', 10)
        message = self.seed(fake_storage, b'x' * 100)
        real_copy = fake_storage.copy_object

        def broken(*args, **kwargs):
            raise ClientError({'Error': {'Code': 'InternalError', 'Message': 'x'}}, 'CopyObject')

        monkeypatch.setattr(fake_storage, 'copy_object', broken)
        inbound.ingest(message.pk)
        message.refresh_from_db()
        assert message.status == 'failed' and message.failed_key == ''

        monkeypatch.setattr(fake_storage, 'copy_object', real_copy)
        inbound.poll()

        message.refresh_from_db()
        assert_parked(fake_storage, message)

    def test_the_parked_key_mirrors_the_inbound_name(self):
        assert failed_key_for(f'{INBOUND_PREFIX}abc123') == f'{FAILED_PREFIX}abc123'
        assert failed_key_for('somewhere/else') == f'{FAILED_PREFIX}somewhere_else'
        assert FAILED_PREFIX == 'todo/failed/' and INBOUND_PREFIX == 'todo/inbound/'


@pytest.fixture
def queued(monkeypatch):
    """Replaces the `ingest_message` task with a recorder, so the poll can be tested alone."""
    calls: list[int] = []

    class Recorder:
        def enqueue(self, message_id):
            calls.append(message_id)

    monkeypatch.setattr(inbound, 'ingest_message', Recorder())
    return calls


class TestPoll:
    def put(self, fake_storage, name: str, data: bytes = b'raw') -> str:
        key = f'{INBOUND_PREFIX}{name}'
        fake_storage.seed(key, data)
        return key

    def test_new_keys_get_a_pending_row_and_one_task(
        self, fake_storage, queued, django_capture_on_commit_callbacks
    ):
        a, b = self.put(fake_storage, 'a'), self.put(fake_storage, 'b')

        with django_capture_on_commit_callbacks(execute=True):
            inbound.poll()

        rows = InboundMessage.objects.order_by('s3_key')
        assert [(m.s3_key, m.status, m.attempts) for m in rows] == [
            (a, 'pending', 0),
            (b, 'pending', 0),
        ]
        assert sorted(queued) == sorted(m.pk for m in rows)
        assert a in fake_storage.objects and not fake_storage.deleted

    def test_it_is_idempotent_for_keys_already_known(
        self, fake_storage, queued, django_capture_on_commit_callbacks
    ):
        self.put(fake_storage, 'a')
        with django_capture_on_commit_callbacks(execute=True):
            inbound.poll()
            inbound.poll()
            inbound.poll()

        assert InboundMessage.objects.count() == 1 and len(queued) == 1

    def test_it_ignores_other_prefixes_and_the_folder_placeholder(
        self, fake_storage, queued, django_capture_on_commit_callbacks
    ):
        fake_storage.seed(INBOUND_PREFIX, b'')
        fake_storage.seed('receipts/inbound/x', b'z')
        fake_storage.seed('todo/users/x/attachments/y', b'z')

        with django_capture_on_commit_callbacks(execute=True):
            inbound.poll()

        assert InboundMessage.objects.count() == 0 and queued == []

    def test_it_lists_at_most_a_batch(
        self, fake_storage, queued, django_capture_on_commit_callbacks
    ):
        for i in range(inbound.POLL_BATCH + 20):
            self.put(fake_storage, f'k{i:04}')

        with django_capture_on_commit_callbacks(execute=True):
            inbound.poll()

        assert InboundMessage.objects.count() == inbound.POLL_BATCH

    def test_failed_messages_do_not_hide_new_mail(
        self, fake_storage, queued, django_capture_on_commit_callbacks, monkeypatch
    ):
        monkeypatch.setattr(inbound, 'MAX_RAW_BYTES', 10)
        for i in range(inbound.POLL_BATCH + 20):
            key = self.put(fake_storage, f'a{i:04}', b'x' * 100)
            inbound.ingest(InboundMessage.objects.create(s3_key=key).pk)

        assert InboundMessage.objects.filter(status='failed').count() == inbound.POLL_BATCH + 20
        assert not fake_storage.list_keys(INBOUND_PREFIX)
        new_key = self.put(fake_storage, 'zzzz-new')

        with django_capture_on_commit_callbacks(execute=True):
            inbound.poll()

        assert InboundMessage.objects.filter(s3_key=new_key, status='pending').exists()

    def test_an_interrupted_move_of_failed_messages_is_finished_by_the_poll(
        self, fake_storage, queued, django_capture_on_commit_callbacks
    ):
        InboundMessage.objects.bulk_create(
            InboundMessage(s3_key=self.put(fake_storage, f'a{i:04}'), status='failed')
            for i in range(inbound.POLL_BATCH + 50)
        )
        new_key = self.put(fake_storage, 'zzzz-new')

        for _ in range(3):
            with django_capture_on_commit_callbacks(execute=True):
                inbound.poll()

        assert InboundMessage.objects.filter(s3_key=new_key, status='pending').exists()
        assert fake_storage.list_keys(INBOUND_PREFIX) == [new_key]
        parked = InboundMessage.objects.filter(status='failed', failed_key__startswith='todo/')
        assert parked.count() == inbound.POLL_BATCH + 50

    def test_a_done_message_whose_object_survived_is_cleaned_up(self, fake_storage, queued):
        key = self.put(fake_storage, 'done-one')
        InboundMessage.objects.create(s3_key=key, status=InboundMessageStatus.DONE)

        inbound.poll()

        assert key not in fake_storage.objects and queued == []

    def test_an_unqueued_pending_message_is_requeued_after_ten_minutes(self, fake_storage, queued):
        stale = InboundMessage.objects.create(s3_key=self.put(fake_storage, 'stale'))
        fresh = InboundMessage.objects.create(s3_key=self.put(fake_storage, 'fresh'))
        InboundMessage.objects.filter(pk=stale.pk).update(
            created_at=timezone.now() - timedelta(minutes=11)
        )

        inbound.poll()

        assert queued == [stale.pk] and fresh.pk not in queued

    def test_it_does_not_queue_the_same_message_every_minute(self, fake_storage, queued):
        stale = InboundMessage.objects.create(s3_key=self.put(fake_storage, 'stale'))
        InboundMessage.objects.filter(pk=stale.pk).update(
            created_at=timezone.now() - timedelta(minutes=11)
        )

        inbound.poll()
        inbound.poll()
        assert queued == [stale.pk]

        InboundMessage.objects.filter(pk=stale.pk).update(
            queued_at=timezone.now() - inbound.REQUEUE_THROTTLE - timedelta(seconds=1)
        )
        inbound.poll()
        assert queued == [stale.pk, stale.pk]

    def test_a_retry_that_is_due_is_requeued_and_one_in_the_future_is_not(
        self, fake_storage, queued
    ):
        due = InboundMessage.objects.create(
            s3_key=self.put(fake_storage, 'due'),
            next_attempt_at=timezone.now() - timedelta(seconds=5),
        )
        leased = InboundMessage.objects.create(
            s3_key=self.put(fake_storage, 'leased'),
            next_attempt_at=timezone.now() + timedelta(minutes=5),
        )
        InboundMessage.objects.filter(pk__in=[due.pk, leased.pk]).update(
            created_at=timezone.now() - timedelta(hours=1)
        )

        inbound.poll()

        assert queued == [due.pk]

    def test_end_to_end_the_poll_leads_to_a_task(
        self, fake_storage, inbox, django_capture_on_commit_callbacks
    ):
        key = self.put(fake_storage, 'e2e', mail(inbox, subject='Polled', attachments=[pdf()]))

        with django_capture_on_commit_callbacks(execute=True):
            inbound.poll()

        message = InboundMessage.objects.get(s3_key=key)
        assert message.status == InboundMessageStatus.DONE
        assert Task.objects.filter(source=TaskSource.EMAIL, title='Polled').count() == 1
        assert key not in fake_storage.objects
        with django_capture_on_commit_callbacks(execute=True):
            inbound.poll()  # nothing left to do
        assert InboundMessage.objects.count() == 1

    def test_a_storage_error_while_listing_propagates_to_the_scheduler(
        self, fake_storage, monkeypatch
    ):
        def broken(*args, **kwargs):
            raise ClientError({'Error': {'Code': 'AccessDenied', 'Message': 'x'}}, 'ListObjectsV2')

        monkeypatch.setattr(fake_storage, 'list_keys', broken)

        with pytest.raises(ClientError):
            inbound.poll()


class TestPurgeDoneMessages:
    def done(self, fake_storage, name: str, *, days: int, with_email=None, **fields):
        key = f'{INBOUND_PREFIX}{name}'
        fake_storage.seed(key, b'raw')
        message = InboundMessage.objects.create(
            s3_key=key,
            status=InboundMessageStatus.DONE,
            processed_at=timezone.now() - timedelta(days=days),
            **fields,
        )
        return message

    def test_old_done_messages_without_an_email_go_with_their_objects(self, fake_storage):
        old = self.done(fake_storage, 'old', days=31)
        recent = self.done(fake_storage, 'recent', days=29)

        assert inbound.purge_done_messages() == 1

        assert not InboundMessage.objects.filter(pk=old.pk).exists()
        assert old.s3_key not in fake_storage.objects
        assert InboundMessage.objects.filter(pk=recent.pk).exists()
        assert recent.s3_key in fake_storage.objects

    def test_a_message_with_an_email_stays(self, fake_storage, funded_user):
        old = self.done(fake_storage, 'old', days=90)
        InboundEmail.objects.create(user=funded_user, message=old, recipient='todo+x@ziptrigo.com')

        assert inbound.purge_done_messages() == 0
        assert InboundMessage.objects.filter(pk=old.pk).exists()

    def test_pending_and_failed_messages_stay(self, fake_storage):
        for status in (InboundMessageStatus.PENDING, InboundMessageStatus.FAILED):
            key = f'{INBOUND_PREFIX}{status}'
            fake_storage.seed(key, b'raw')
            InboundMessage.objects.create(
                s3_key=key, status=status, processed_at=timezone.now() - timedelta(days=90)
            )

        assert inbound.purge_done_messages() == 0

    def test_a_row_without_a_processing_time_uses_its_creation_time(self, fake_storage):
        message = self.done(fake_storage, 'noproc', days=0)
        InboundMessage.objects.filter(pk=message.pk).update(
            processed_at=None, created_at=timezone.now() - timedelta(days=40)
        )

        assert inbound.purge_done_messages() == 1

    def test_a_storage_error_keeps_the_rows(self, fake_storage):
        message = self.done(fake_storage, 'stuck', days=40)
        fake_storage.failing_deletes.add(message.s3_key)

        with pytest.raises(RuntimeError):
            inbound.purge_done_messages()

        assert InboundMessage.objects.filter(pk=message.pk).exists()

    def test_it_goes_in_batches(self, fake_storage, monkeypatch):
        monkeypatch.setattr(inbound, 'PURGE_BATCH', 2)
        for i in range(5):
            self.done(fake_storage, f'm{i}', days=40)

        assert inbound.purge_done_messages() == 5
