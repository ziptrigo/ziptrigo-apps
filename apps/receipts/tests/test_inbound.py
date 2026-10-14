"""Email intake end to end: `services.inbound` (poll + ingest) over real raw emails in the fake
bucket, with the fake AI. `ImmediateBackend` runs `process_receipt` inline when the on-commit
callbacks are executed, so a delivered email ends up as processed (or failed / waiting) receipts."""

import base64
import hashlib
import uuid
from datetime import timedelta

import pytest
from botocore.exceptions import ClientError
from django.db import IntegrityError
from django.utils import timezone

from apps.billing.services import add_credits, get_balance

from ..models import (
    InboundEmail,
    InboundEmailStatus,
    InboundMessage,
    InboundMessageStatus,
    InboxCode,
    Receipt,
    ReceiptsAccount,
    ReceiptSource,
    ReceiptsSettings,
    ReceiptStatus,
)
from ..services import inbound, mime
from ..services.accounts import get_account, inbox_address_for
from ..services.lifecycle import purge_receipt
from ..services.storage import (
    FAILED_PREFIX,
    INBOUND_PREFIX,
    email_key,
    failed_key_for,
    file_key,
    user_prefix,
)
from . import samples
from .emails import (
    FIXED_ADDRESS,
    FIXED_CODE,
    FIXTURES,
    HTML_RECEIPT,
    Attachment,
    Ses,
    build_email,
    jpeg,
    logo,
    pdf,
    png,
)
from .factories import make_user
from .fakes import sample_extraction
from .test_mime import nested_forwards

pytestmark = [pytest.mark.django_db, pytest.mark.unit]


@pytest.fixture
def inbox(account) -> str:
    """The funded user's real `receipts+<code>@...` address."""
    return inbox_address_for(account.user)


@pytest.fixture
def fixed_inbox(account) -> str:
    """The funded user's address, with the fixed code the literal `.eml` fixtures use."""
    InboxCode.objects.filter(account=account, active=True).update(code=FIXED_CODE)
    return FIXED_ADDRESS


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


def assert_parked(fake_storage, message: InboundMessage) -> None:
    """A failed message's object is kept, but not under the prefix the poller lists."""
    assert message.status == InboundMessageStatus.FAILED
    assert message.failed_key == failed_key_for(message.s3_key)
    assert message.failed_key.startswith(FAILED_PREFIX)
    assert message.failed_key in fake_storage.objects
    assert message.s3_key not in fake_storage.objects
    assert not fake_storage.list_keys(INBOUND_PREFIX)


def one_email(message: InboundMessage) -> InboundEmail:
    [email] = list(message.emails.all())
    return email


def contains_attachment(raw: bytes, data: bytes) -> bool:
    """Whether the base64 of the start of `data` appears in `raw` (base64 is wrapped, so the
    line breaks are removed first)."""
    return base64.b64encode(data)[:60] in raw.replace(b'\n', b'')


class TestAcceptedAttachments:
    def test_a_pdf_attachment_becomes_a_receipt(self, deliver, inbox, fake_storage, funded_user):
        raw = build_email(attachments=[pdf()], ses=Ses(recipient=inbox))

        message = deliver(raw)

        assert message.status == InboundMessageStatus.DONE
        assert message.processed_at is not None and message.last_error == ''
        email = one_email(message)
        assert email.status == InboundEmailStatus.ACCEPTED
        assert email.user == funded_user and email.recipient == inbox
        assert email.from_address == 'orders@staples.com'
        assert email.envelope_sender == 'orders@staples.com'
        assert email.subject == 'Your Staples receipt'
        assert email.received_at is not None and email.received_at.year == 2026
        [receipt] = list(email.receipts.all())
        assert receipt.source == ReceiptSource.EMAIL
        assert receipt.original_filename == 'invoice.pdf'
        assert receipt.content_type == 'application/pdf'
        assert receipt.file_key == file_key(funded_user.pk, receipt.pk)
        assert receipt.size_bytes == len(samples.text_pdf_bytes())
        assert receipt.sha256 == hashlib.sha256(samples.text_pdf_bytes()).hexdigest()
        assert receipt.uploaded and receipt.user == funded_user
        assert fake_storage.objects[receipt.file_key] == samples.text_pdf_bytes()
        # Processing was queued on commit and, with the fake AI, finished.
        assert receipt.status == ReceiptStatus.PROCESSED
        assert get_balance(funded_user) == 99

    def test_the_inbound_object_is_deleted(self, deliver, inbox, fake_storage):
        message = deliver(build_email(attachments=[pdf()], ses=Ses(recipient=inbox)))

        assert message.s3_key not in fake_storage.objects
        assert message.s3_key in fake_storage.deleted

    def test_a_jpeg_photo(self, deliver, inbox):
        message = deliver(
            build_email(text='', attachments=[jpeg('IMG_0042.JPG')], ses=Ses(recipient=inbox))
        )

        [receipt] = list(one_email(message).receipts.all())
        assert receipt.content_type == 'image/jpeg'
        assert receipt.original_filename == 'IMG_0042.JPG'

    def test_an_html_body_is_the_receipt_when_there_is_no_attachment(
        self, deliver, inbox, fake_storage
    ):
        raw = build_email(text=None, html=HTML_RECEIPT, ses=Ses(recipient=inbox))

        message = deliver(raw)

        [receipt] = list(one_email(message).receipts.all())
        assert receipt.content_type == 'text/html'
        assert receipt.original_filename == ''
        assert receipt.source == ReceiptSource.EMAIL
        assert fake_storage.objects[receipt.file_key].decode().strip() == HTML_RECEIPT
        assert fake_storage.content_types[receipt.file_key] == 'text/html'

    def test_a_text_body(self, deliver, inbox, fake_storage):
        message = deliver(build_email(ses=Ses(recipient=inbox)))

        [receipt] = list(one_email(message).receipts.all())
        assert receipt.content_type == 'text/plain'
        assert b'Total 42.10 USD' in fake_storage.objects[receipt.file_key]

    def test_each_attachment_is_its_own_receipt(self, deliver, inbox, funded_user):
        raw = build_email(
            text='Three receipts attached',
            attachments=[pdf(), jpeg(), png()],
            ses=Ses(recipient=inbox),
        )

        message = deliver(raw)

        receipts = list(one_email(message).receipts.order_by('original_filename'))
        assert [r.original_filename for r in receipts] == ['invoice.pdf', 'photo.jpg', 'scan.png']
        assert len({r.pk for r in receipts}) == 3
        assert len({r.file_key for r in receipts}) == 3
        assert get_balance(funded_user) == 97  # one charge per file

    def test_the_literal_fixtures(self, deliver, fixed_inbox):
        pdf_message = deliver((FIXTURES / 'pdf_attachment.eml').read_bytes())
        html_message = deliver((FIXTURES / 'html_body.eml').read_bytes())

        # The text body next to the PDF is not a receipt of its own.
        [from_pdf] = list(one_email(pdf_message).receipts.all())
        assert from_pdf.content_type == 'application/pdf'
        [from_html] = list(one_email(html_message).receipts.all())
        assert from_html.content_type == 'text/html'

    def test_an_unnamed_attachment_gets_a_name_from_its_type(self, deliver, inbox):
        raw = build_email(
            attachments=[Attachment('', samples.jpeg_bytes(), 'image', 'jpeg')],
            ses=Ses(recipient=inbox),
        )

        message = deliver(raw)

        [receipt] = list(one_email(message).receipts.all())
        assert receipt.original_filename == 'attachment.jpg'

    def test_a_misleading_filename_is_cleaned(self, deliver, inbox):
        raw = build_email(
            attachments=[Attachment('../../etc/passwd.pdf', samples.text_pdf_bytes())],
            ses=Ses(recipient=inbox),
        )

        message = deliver(raw)

        [receipt] = list(one_email(message).receipts.all())
        assert receipt.original_filename == 'passwd.pdf'
        assert '..' not in receipt.file_key and 'passwd' not in receipt.file_key


class TestInlineImagesAndForwards:
    def test_a_tiny_inline_logo_is_skipped_and_the_attachment_is_the_receipt(
        self, deliver, inbox, fake_storage
    ):
        raw = build_email(
            text=None,
            html=HTML_RECEIPT,
            attachments=[logo((48, 48)), pdf()],
            ses=Ses(recipient=inbox),
        )

        message = deliver(raw)

        email = one_email(message)
        [receipt] = list(email.receipts.all())
        assert receipt.original_filename == 'invoice.pdf'
        eml = fake_storage.objects[email.raw_key]
        assert b'X-Receipts-Skipped: inline image' in eml
        assert f'X-Receipts-Receipt-Id: {receipt.pk}'.encode() in eml

    def test_a_logo_only_email_falls_back_to_the_html_body(self, deliver, inbox):
        raw = build_email(
            text=None, html=HTML_RECEIPT, attachments=[logo((48, 48))], ses=Ses(recipient=inbox)
        )

        message = deliver(raw)

        [receipt] = list(one_email(message).receipts.all())
        assert receipt.content_type == 'text/html'

    def test_forwarded_as_an_attachment(self, deliver, fixed_inbox):
        message = deliver((FIXTURES / 'forward_as_attachment.eml').read_bytes())

        [receipt] = list(one_email(message).receipts.all())
        assert receipt.original_filename == 'inner-invoice.pdf'
        assert receipt.content_type == 'application/pdf'

    def test_a_forwarded_email_whose_receipt_is_its_body(self, deliver, inbox, fake_storage):
        inner = build_email(
            text='Staples order R-77. Total: 12.00 USD. Thank you for shopping with us.'
        )
        raw = build_email(
            text='FYI, please file this one for me, thanks!!',
            nested=inner,
            ses=Ses(recipient=inbox),
        )

        message = deliver(raw)

        [receipt] = list(one_email(message).receipts.all())
        assert b'Staples order R-77' in fake_storage.objects[receipt.file_key]


class TestGmailAutoForward:
    def test_the_envelope_recipient_decides_not_the_to_header(
        self, deliver, fixed_inbox, funded_user
    ):
        message = deliver((FIXTURES / 'gmail_auto_forward.eml').read_bytes())

        email = one_email(message)
        assert email.status == InboundEmailStatus.ACCEPTED
        assert email.user == funded_user
        assert email.recipient == FIXED_ADDRESS
        assert email.from_address == 'orders@staples.com'
        assert email.envelope_sender == 'orders+caf_=joao=gmail.com@staples.com'
        assert [r.original_filename for r in email.receipts.all()] == ['staples-receipt.pdf']

    def test_an_allowlist_matches_the_normalized_envelope_sender(
        self, deliver, inbox, account, fake_storage
    ):
        """`From` is not listed, but the envelope sender, `orders+caf_=...@staples.com`,
        normalizes to a listed address, and SPF passed for it."""
        ReceiptsAccount.objects.filter(pk=account.pk).update(
            sender_allowlist=['orders@staples.com']
        )
        raw = build_email(
            from_addr='Staples Orders <noreply@mailer.example>',
            ses=Ses(recipient=inbox, return_path='orders+caf_=joao=gmail.com@staples.com'),
            attachments=[pdf()],
        )

        message = deliver(raw)

        assert one_email(message).status == InboundEmailStatus.ACCEPTED

    def test_a_forwarded_receipt_is_matched_by_from_and_an_aligned_dkim(
        self, deliver, fixed_inbox, account
    ):
        """Gmail's forward breaks SPF; the vendor's own DKIM signature survives."""
        ReceiptsAccount.objects.filter(pk=account.pk).update(
            sender_allowlist=['orders@staples.com']
        )

        message = deliver((FIXTURES / 'gmail_auto_forward.eml').read_bytes())

        assert one_email(message).status == InboundEmailStatus.ACCEPTED


class TestForwardingConfirmation:
    def test_is_stored_as_a_free_forwarding_confirmation(
        self, deliver, fixed_inbox, funded_user, fake_storage
    ):
        balance = get_balance(funded_user)

        message = deliver((FIXTURES / 'gmail_forwarding_confirmation.eml').read_bytes())

        email = one_email(message)
        assert email.status == InboundEmailStatus.FORWARDING_CONFIRMATION
        assert email.forwarding_confirmation_code == '987654321'
        assert email.forwarding_confirmation_url == (
            'https://mail-settings.google.com/mail/vf-%5BANGjdJ8Xk%5D-AbCdEf123'
        )
        assert email.receipts.count() == 0 and email.raw_key == '' and email.size_bytes == 0
        assert get_balance(funded_user) == balance
        assert not [k for k in fake_storage.objects if k.startswith(user_prefix(funded_user.pk))]
        account = get_account(funded_user)
        assert account.stored_bytes == 0 and account.intake_count == 0

    def test_skips_the_allowlist(self, deliver, fixed_inbox, account):
        ReceiptsAccount.objects.filter(pk=account.pk).update(sender_allowlist=['me@example.com'])

        message = deliver((FIXTURES / 'gmail_forwarding_confirmation.eml').read_bytes())

        assert one_email(message).status == InboundEmailStatus.FORWARDING_CONFIRMATION

    def test_needs_the_sender_to_be_authenticated(self, deliver, inbox, account):
        """Anyone can write `From: forwarding-noreply@google.com`; without SPF/DKIM it is an
        ordinary email (so the allowlist applies) and no link is ever extracted from it."""
        ReceiptsAccount.objects.filter(pk=account.pk).update(sender_allowlist=['me@example.com'])
        raw = build_email(
            from_addr='forwarding-noreply@google.com',
            text='Confirmation code: 123456\nhttps://mail-settings.google.com/mail/vf-x',
            ses=Ses(recipient=inbox, spf='FAIL', dkim='FAIL'),
        )

        message = deliver(raw)

        email = one_email(message)
        assert email.status == InboundEmailStatus.REJECTED_SENDER
        assert email.forwarding_confirmation_url == ''

    def test_authentication_for_another_domain_does_not_make_it_google(
        self, deliver, inbox, account, fake_storage
    ):
        """SPF and DKIM pass -- for the attacker's own domain. Posing as Google's forwarding
        sender must not skip the allowlist or plant a code on the settings page."""
        ReceiptsAccount.objects.filter(pk=account.pk).update(sender_allowlist=['me@example.com'])
        raw = build_email(
            from_addr='forwarding-noreply@google.com',
            text='Confirmation code: 123456\nhttps://mail-settings.google.com/mail/vf-x',
            ses=Ses(
                recipient=inbox,
                return_path='attacker@evil.test',
                spf='PASS',
                dkim='PASS',
                dkim_domain='evil.test',
            ),
        )

        email = one_email(deliver(raw))

        assert email.status == InboundEmailStatus.REJECTED_SENDER
        assert email.forwarding_confirmation_code == email.forwarding_confirmation_url == ''

    def test_injected_verdict_headers_do_not_make_it_google(self, deliver, inbox, account):
        ReceiptsAccount.objects.filter(pk=account.pk).update(sender_allowlist=['me@example.com'])
        raw = build_email(
            from_addr='forwarding-noreply@google.com',
            text='Confirmation code: 123456\nhttps://mail-settings.google.com/mail/vf-x',
            ses=Ses(recipient=inbox, return_path='x@evil.test', spf='FAIL', dkim='FAIL'),
            headers=[('X-SES-Spf-Verdict', 'PASS'), ('X-SES-Dkim-Verdict', 'PASS')],
        )

        email = one_email(deliver(raw))

        assert email.status == InboundEmailStatus.REJECTED_SENDER
        assert email.forwarding_confirmation_url == ''

    @pytest.mark.parametrize(
        'crafted',
        [
            '"x<forwarding-noreply@google.com>"@evil.test',
            '=?utf-8?q?x=3Cforwarding-noreply=40google=2Ecom=3E?=@evil.test',
        ],
    )
    def test_a_crafted_from_naming_google_inside_a_quoted_part_is_not_google(
        self, deliver, inbox, account, crafted
    ):
        ReceiptsAccount.objects.filter(pk=account.pk).update(sender_allowlist=['me@example.com'])
        raw = build_email(
            from_addr=crafted,
            text='Confirmation code: 123456\nhttps://mail-settings.google.com/mail/vf-x',
            ses=Ses(recipient=inbox, return_path='bounce@evil.test'),
        )

        email = one_email(deliver(raw))

        assert email.status == InboundEmailStatus.REJECTED_SENDER
        assert email.forwarding_confirmation_url == ''

    def test_a_dmarc_pass_for_another_domain_does_not_make_it_google(self, deliver, inbox, account):
        ReceiptsAccount.objects.filter(pk=account.pk).update(sender_allowlist=['me@example.com'])
        raw = build_email(
            from_addr='forwarding-noreply@google.com',
            text='Confirmation code: 123456',
            ses=Ses(
                recipient=inbox,
                return_path='x@evil.test',
                spf='FAIL',
                dkim='FAIL',
                dmarc='PASS',
                dmarc_domain='evil.test',
            ),
        )

        assert one_email(deliver(raw)).status == InboundEmailStatus.REJECTED_SENDER

    def test_google_without_a_signing_domain_is_not_google(self, deliver, inbox, account):
        ReceiptsAccount.objects.filter(pk=account.pk).update(sender_allowlist=['me@example.com'])
        raw = build_email(
            from_addr='forwarding-noreply@google.com',
            text='Confirmation code: 123456',
            ses=Ses(recipient=inbox, return_path='x@evil.test', dkim_domain=''),
        )

        assert one_email(deliver(raw)).status == InboundEmailStatus.REJECTED_SENDER

    def test_a_signature_by_a_google_subdomain_is_aligned(self, deliver, inbox, account):
        ReceiptsAccount.objects.filter(pk=account.pk).update(sender_allowlist=['me@example.com'])
        raw = build_email(
            from_addr='forwarding-noreply@google.com',
            text='Confirmation code: 123456',
            ses=Ses(recipient=inbox, return_path='x@bounce.test', dkim_domain='mail.google.com'),
        )

        assert one_email(deliver(raw)).status == InboundEmailStatus.FORWARDING_CONFIRMATION

    def test_a_malformed_link_in_the_confirmation_does_not_fail_the_message(
        self, deliver, inbox, fake_storage
    ):
        """`urlsplit('https://[x')` raises `ValueError`; that used to fail the message for good
        (and, through it, let anyone fill the poller's listing with failures)."""
        raw = build_email(
            from_addr='forwarding-noreply@google.com',
            text='Confirmation code: 123456\nhttps://[x\n',
            ses=Ses(recipient=inbox),
        )

        message = deliver(raw)

        assert message.status == InboundMessageStatus.DONE
        email = one_email(message)
        assert email.status == InboundEmailStatus.FORWARDING_CONFIRMATION
        assert (email.forwarding_confirmation_code, email.forwarding_confirmation_url) == (
            '123456',
            '',
        )

    def test_spam_is_still_rejected_first(self, deliver, inbox):
        raw = build_email(
            from_addr='forwarding-noreply@google.com', ses=Ses(recipient=inbox, spam='FAIL')
        )

        assert one_email(deliver(raw)).status == InboundEmailStatus.REJECTED_SPAM


class TestVerdictRejections:
    @pytest.mark.parametrize(
        ('ses', 'status'),
        [
            ({'spam': 'FAIL'}, InboundEmailStatus.REJECTED_SPAM),
            ({'virus': 'FAIL'}, InboundEmailStatus.REJECTED_VIRUS),
        ],
    )
    def test_spam_and_virus_are_dropped_keeping_metadata_only(
        self, deliver, inbox, fake_storage, funded_user, ses, status
    ):
        raw = build_email(attachments=[pdf()], ses=Ses(recipient=inbox, **ses))
        balance = get_balance(funded_user)

        message = deliver(raw)

        email = one_email(message)
        assert email.status == status
        assert email.from_address == 'orders@staples.com'
        assert email.subject == 'Your Staples receipt'
        assert email.receipts.count() == 0 and email.raw_key == ''
        assert not [k for k in fake_storage.objects if k.startswith(user_prefix(funded_user.pk))]
        assert get_balance(funded_user) == balance
        account = get_account(funded_user)
        assert account.stored_bytes == 0 and account.intake_count == 0
        # Dropping is final: the inbound object goes too.
        assert message.status == InboundMessageStatus.DONE
        assert message.s3_key not in fake_storage.objects

    def test_gray_is_accepted(self, deliver, inbox):
        raw = build_email(attachments=[pdf()], ses=Ses(recipient=inbox, spam='GRAY'))

        assert one_email(deliver(raw)).status == InboundEmailStatus.ACCEPTED


class TestRecipients:
    def test_two_recipients_two_users(self, deliver, account, funded_user, fake_storage):
        other = make_user()
        add_credits(other, 50, source='test')
        other_account = get_account(other)
        first, second = inbox_address_for(funded_user), inbox_address_for(other)
        raw = build_email(attachments=[pdf()], ses=Ses(recipient=f'{first}, {second}'))

        message = deliver(raw)

        emails = {e.user: e for e in message.emails.all()}
        assert set(emails) == {funded_user, other}
        for user, email in emails.items():
            [receipt] = list(email.receipts.all())
            assert receipt.user == user
            assert receipt.file_key.startswith(user_prefix(user.pk))
            assert email.raw_key == email_key(user.pk, email.pk)
            assert email.status == InboundEmailStatus.ACCEPTED
        assert get_balance(funded_user) == 99 and get_balance(other) == 49
        assert other_account.pk != account.pk
        # Each user's eml names that user's receipt.
        for user, email in emails.items():
            [receipt] = list(email.receipts.all())
            assert str(receipt.pk).encode() in fake_storage.objects[email.raw_key]

    def test_a_known_and_an_unknown_code(self, deliver, inbox):
        raw = build_email(
            attachments=[pdf()], ses=Ses(recipient=f'receipts+zzzz2222@ziptrigo.com, {inbox}')
        )

        message = deliver(raw)

        assert [e.recipient for e in message.emails.all()] == [inbox]
        assert message.status == InboundMessageStatus.DONE

    def test_an_unknown_code_is_dropped_without_a_row(self, deliver, fake_storage, caplog):
        raw = build_email(attachments=[pdf()], ses=Ses(recipient='receipts+zzzz2222@ziptrigo.com'))

        with caplog.at_level('INFO', logger='apps.receipts.services.inbound'):
            message = deliver(raw)

        assert message.status == InboundMessageStatus.DONE
        assert InboundEmail.objects.count() == 0 and Receipt.objects.count() == 0
        assert 'unknown inbox code zzzz2222' in caplog.text
        assert message.s3_key not in fake_storage.objects

    def test_a_retired_code_is_dropped(self, deliver, account, caplog):
        from ..services.actions import regenerate_inbox_code

        old_address = inbox_address_for(account.user)
        regenerate_inbox_code(account.user)
        raw = build_email(attachments=[pdf()], ses=Ses(recipient=old_address))

        with caplog.at_level('INFO', logger='apps.receipts.services.inbound'):
            message = deliver(raw)

        assert message.status == InboundMessageStatus.DONE
        assert InboundEmail.objects.count() == 0 and Receipt.objects.count() == 0
        assert 'retired inbox code' in caplog.text

    def test_an_inactive_user_gets_nothing(self, deliver, inbox, funded_user):
        funded_user.status = funded_user.STATUS_INACTIVE
        funded_user.save(update_fields=['status'])

        message = deliver(build_email(attachments=[pdf()], ses=Ses(recipient=inbox)))

        assert InboundEmail.objects.count() == 0 and message.status == InboundMessageStatus.DONE

    def test_a_message_for_nobody_we_know_is_done(self, deliver, fake_storage):
        message = deliver(b'this is not an email at all')

        assert message.status == InboundMessageStatus.DONE
        assert message.s3_key not in fake_storage.objects

    def test_explicit_recipients_replace_the_headers(self, deliver, inbox):
        raw = build_email(attachments=[pdf()], ses=Ses(recipient='receipts+zzzz2222@ziptrigo.com'))

        message = deliver(raw, recipients=[inbox])

        assert one_email(message).recipient == inbox

    def test_a_recipient_is_not_ingested_twice(self, deliver, inbox, fake_storage):
        message = deliver(build_email(attachments=[pdf()], ses=Ses(recipient=inbox)))
        InboundMessage.objects.filter(pk=message.pk).update(
            status=InboundMessageStatus.PENDING, next_attempt_at=None
        )
        fake_storage.seed(
            message.s3_key, build_email(attachments=[pdf()], ses=Ses(recipient=inbox))
        )

        inbound.ingest(message.pk)

        assert Receipt.objects.count() == 1 and InboundEmail.objects.count() == 1


class TestAllowlist:
    @pytest.fixture(autouse=True)
    def _allowlist(self, account):
        ReceiptsAccount.objects.filter(pk=account.pk).update(
            sender_allowlist=['orders@staples.com', '*@acme.test']
        )

    def status(self, deliver, inbox, *, from_addr='orders@staples.com', **ses) -> str:
        raw = build_email(from_addr=from_addr, attachments=[pdf()], ses=Ses(recipient=inbox, **ses))
        email = one_email(deliver(raw))
        return email.status

    def test_a_listed_sender_with_spf_passes(self, deliver, inbox):
        assert self.status(deliver, inbox, spf='PASS', dkim='FAIL') == 'accepted'

    def test_a_listed_sender_with_only_an_aligned_dkim_passes(self, deliver, inbox):
        assert self.status(deliver, inbox, spf='FAIL', dkim='PASS') == 'accepted'

    def test_spf_for_an_unrelated_envelope_domain_does_not_authenticate_a_listed_from(
        self, deliver, inbox
    ):
        """The bypass: pass SPF for your own domain, write the allowlisted address in `From`."""
        status = self.status(
            deliver,
            inbox,
            from_addr='orders@staples.com',
            return_path='attacker@evil.test',
            spf='PASS',
            dkim='FAIL',
        )

        assert status == 'rejected_sender'

    @pytest.mark.parametrize(
        'crafted',
        [
            '"x<orders@staples.com>"@evil.test',
            '=?utf-8?q?x=3Corders=40staples=2Ecom=3E?=@evil.test',
            '"x<anyone@acme.test>"@evil.test',
        ],
    )
    def test_a_listed_address_inside_a_crafted_from_is_not_the_sender(
        self, deliver, inbox, crafted
    ):
        """The address written in `From` is the one authenticated (its domain must pass DMARC),
        so a listed address tucked into a quoted local part must not count as listed."""
        status = self.status(
            deliver, inbox, from_addr=crafted, return_path='bounce@evil.test', spf='PASS'
        )

        assert status == 'rejected_sender'

    def test_a_dkim_pass_for_an_unrelated_domain_does_not_authenticate_a_listed_from(
        self, deliver, inbox
    ):
        status = self.status(
            deliver,
            inbox,
            from_addr='orders@staples.com',
            return_path='attacker@evil.test',
            spf='FAIL',
            dkim='PASS',
            dkim_domain='evil.test',
        )

        assert status == 'rejected_sender'

    def test_a_from_match_without_a_signing_domain_is_not_authenticated(self, deliver, inbox):
        status = self.status(deliver, inbox, spf='FAIL', dkim='PASS', dkim_domain='')

        assert status == 'rejected_sender'

    def test_dkim_for_a_subdomain_of_the_from_domain_is_aligned(self, deliver, inbox):
        status = self.status(
            deliver, inbox, spf='FAIL', dkim='PASS', dkim_domain='mail.staples.com'
        )

        assert status == 'accepted'

    def test_dkim_does_not_authenticate_a_match_on_the_envelope_sender_alone(self, deliver, inbox):
        """Only `Return-Path` is listed; the proof available (DKIM) is about the From domain."""
        status = self.status(
            deliver,
            inbox,
            from_addr='noreply@mailer.test',
            return_path='orders@staples.com',
            spf='FAIL',
            dkim='PASS',
            dkim_domain='mailer.test',
        )

        assert status == 'rejected_sender'

    def test_the_rejection_says_why(self, deliver, inbox):
        raw = build_email(
            from_addr='orders@staples.com',
            attachments=[pdf()],
            ses=Ses(recipient=inbox, return_path='x@evil.test', dkim='FAIL'),
        )

        email = one_email(deliver(raw))

        assert 'could not be verified' in email.note

    def test_a_listed_sender_without_any_authentication_is_rejected(self, deliver, inbox):
        assert self.status(deliver, inbox, spf='FAIL', dkim='FAIL') == 'rejected_sender'
        assert self.status(deliver, inbox, spf=None, dkim=None) == 'rejected_sender'

    def test_an_unlisted_sender_is_rejected_even_when_authenticated(self, deliver, inbox):
        status = self.status(
            deliver, inbox, from_addr='scammer@evil.test', return_path='scammer@evil.test'
        )

        assert status == 'rejected_sender'

    def test_a_rejected_sender_stores_nothing(self, deliver, inbox, fake_storage, funded_user):
        self.status(deliver, inbox, from_addr='x@evil.test', return_path='x@evil.test')

        assert not [k for k in fake_storage.objects if k.startswith(user_prefix(funded_user.pk))]
        assert get_account(funded_user).intake_count == 0

    def test_the_envelope_sender_alone_can_match(self, deliver, inbox):
        status = self.status(
            deliver, inbox, from_addr='noreply@mailer.test', return_path='orders@staples.com'
        )

        assert status == 'accepted'

    def test_a_domain_wildcard(self, deliver, inbox):
        status = self.status(
            deliver, inbox, from_addr='Anyone <billing@ACME.test>', return_path='b@bounce.test'
        )

        assert status == 'accepted'

    def test_a_wildcard_does_not_cover_subdomains(self, deliver, inbox):
        status = self.status(
            deliver, inbox, from_addr='billing@mail.acme.test', return_path='b@mail.acme.test'
        )

        assert status == 'rejected_sender'

    def test_plus_tags_and_case_are_normalized(self, deliver, inbox):
        status = self.status(deliver, inbox, from_addr='Orders+Invoices@Staples.COM')

        assert status == 'accepted'

    def test_a_dmarc_pass_for_the_from_domain_authenticates_a_listed_from(self, deliver, inbox):
        """Neither SPF nor an aligned DKIM signature of its own, but DMARC passed for the From
        domain."""
        assert self.status(deliver, inbox, spf='FAIL', dkim='FAIL', dmarc='PASS') == 'accepted'

    def test_a_dmarc_pass_for_another_domain_does_not_authenticate_a_listed_from(
        self, deliver, inbox
    ):
        status = self.status(
            deliver, inbox, spf='FAIL', dkim='FAIL', dmarc='PASS', dmarc_domain='evil.test'
        )

        assert status == 'rejected_sender'

    def test_without_a_dmarc_result_an_aligned_dkim_authenticates(self, deliver, inbox):
        assert self.status(deliver, inbox, spf='FAIL', dkim='PASS', dmarc=None) == 'accepted'
        assert self.status(deliver, inbox, spf='FAIL', dkim='FAIL', dmarc=None) == 'rejected_sender'

    def test_spf_for_a_different_envelope_from_does_not_authenticate_the_return_path(
        self, deliver, inbox
    ):
        """`Return-Path` is the listed address, but the SPF result SES wrote is about another one."""
        status = self.status(
            deliver,
            inbox,
            from_addr='noreply@mailer.test',
            return_path='orders@staples.com',
            spf='PASS',
            spf_envelope_from='attacker@evil.test',
            dkim='FAIL',
            dmarc='FAIL',
        )

        assert status == 'rejected_sender'

    @pytest.mark.parametrize(
        'headers',
        [
            [('X-SES-Spf-Verdict', 'PASS'), ('X-SES-Dkim-Verdict', 'PASS')],
            [('Received-SPF', 'pass (spfCheck: ok) envelope-from=orders@staples.com')],
            [
                (
                    'Authentication-Results',
                    'amazonses.com; spf=pass; envelope-from=orders@staples.com; dkim=pass '
                    'header.i=@staples.com; dmarc=pass header.from=staples.com',
                )
            ],
        ],
        ids=['injected-x-ses-verdicts', 'injected-received-spf', 'forged-authentication-results'],
    )
    def test_headers_the_sender_wrote_authenticate_nothing(self, deliver, inbox, headers):
        """The regression this guards: SES never writes `X-SES-Spf/Dkim-Verdict`, so a sender could
        add them to a message that really failed authentication and be let in."""
        raw = build_email(
            from_addr='orders@staples.com',
            attachments=[pdf()],
            ses=Ses(recipient=inbox, spf='FAIL', dkim='FAIL', dmarc='FAIL'),
            headers=headers,
        )

        email = one_email(deliver(raw))

        assert email.status == 'rejected_sender'

    def test_injected_verdicts_do_not_help_without_any_authentication_results(self, deliver, inbox):
        raw = build_email(
            from_addr='orders@staples.com',
            attachments=[pdf()],
            ses=Ses(recipient=inbox, spf=None, dkim=None, dmarc=None),
            headers=[('X-SES-Spf-Verdict', 'PASS'), ('X-SES-Dkim-Verdict', 'PASS')],
        )

        assert one_email(deliver(raw)).status == 'rejected_sender'

    @pytest.mark.parametrize(
        'ses',
        [
            # A MAIL FROM that is a quoted-string holding `;` and fake clauses.
            {
                'return_path': '"x;dkim=pass header.i=@staples.com;dmarc=pass '
                'header.from=staples.com"@attacker.test',
                'dmarc': 'FAIL',
            },
            # An EHLO name that carries them, with no spaces.
            {
                'return_path': 'x@attacker.test',
                'helo': 'x;dkim=pass,header.i=@staples.com;dmarc=pass,header.from=staples.com',
                'dmarc': 'FAIL',
            },
            # An RFC 5321 address-literal EHLO.
            {
                'return_path': 'x@attacker.test',
                'helo': '[x:a;dkim=pass,header.i=@staples.com;dmarc=pass,header.from=staples.com;x]',
                'dmarc': 'FAIL',
            },
            # DKIM only, for a From domain that publishes no DMARC policy.
            {
                'return_path': 'x@attacker.test',
                'helo': 'x;dkim=pass,header.i=@staples.com',
                'dmarc': 'NONE',
            },
        ],
        ids=['quoted-mail-from', 'helo-no-spaces', 'address-literal-helo', 'dkim-only-no-dmarc'],
    )
    def test_values_the_sender_controls_cannot_inject_an_authentication_result(
        self, deliver, inbox, ses
    ):
        """The header SES writes echoes MAIL FROM and EHLO; a `;` in them must not become a
        `dkim=pass` / `dmarc=pass` clause that lets a forged `From` through."""
        status = self.status(
            deliver,
            inbox,
            from_addr='orders@staples.com',
            spf='PASS',
            dkim='PASS',
            dkim_domain='attacker.test',
            **ses,
        )

        assert status == 'rejected_sender'

    def test_an_envelope_from_that_is_not_the_return_path_is_rejected(self, deliver, inbox):
        status = self.status(
            deliver,
            inbox,
            from_addr='noreply@mailer.test',
            return_path='orders@staples.com',
            spf='PASS',
            spf_envelope_from='orders@staples.com.evil.test',
            dkim='FAIL',
            dmarc='FAIL',
        )

        assert status == 'rejected_sender'

    def test_a_helo_that_is_not_the_received_host_is_rejected(self, deliver, inbox):
        status = self.status(deliver, inbox, spf='PASS', dkim='PASS', received_from='other.test')

        assert status == 'rejected_sender'

    @pytest.mark.parametrize('dmarc', ['TEMPERROR', 'PERMERROR'])
    def test_a_dmarc_error_does_not_fall_back_to_dkim(self, deliver, inbox, dmarc):
        status = self.status(deliver, inbox, spf='FAIL', dkim='PASS', dmarc=dmarc)

        assert status == 'rejected_sender'

    def test_a_sibling_domain_dkim_signature_is_not_aligned_without_dmarc(self, deliver, inbox):
        ReceiptsAccount.objects.filter(pk=ReceiptsAccount.objects.get().pk).update(
            sender_allowlist=['billing@victim.github.io']
        )

        status = self.status(
            deliver,
            inbox,
            from_addr='billing@victim.github.io',
            return_path='x@esp.test',
            spf='FAIL',
            dkim='PASS',
            dkim_domain='attacker.github.io',
            dmarc='NONE',
        )

        assert status == 'rejected_sender'

    def test_a_real_shaped_ses_message_from_a_listed_sender_is_accepted(
        self, deliver, fixed_inbox, account
    ):
        ReceiptsAccount.objects.filter(pk=account.pk).update(sender_allowlist=['sender@gmail.com'])

        message = deliver((FIXTURES / 'gmail_manual_forward.eml').read_bytes())

        assert one_email(message).status == InboundEmailStatus.ACCEPTED

    def test_a_real_shaped_ses_message_from_an_unlisted_sender_is_rejected(
        self, deliver, fixed_inbox, account
    ):
        ReceiptsAccount.objects.filter(pk=account.pk).update(
            sender_allowlist=['orders@staples.com']
        )

        message = deliver((FIXTURES / 'gmail_manual_forward.eml').read_bytes())

        assert one_email(message).status == InboundEmailStatus.REJECTED_SENDER

    def test_an_empty_allowlist_accepts_anyone_without_authentication(
        self, deliver, inbox, account
    ):
        ReceiptsAccount.objects.filter(pk=account.pk).update(sender_allowlist=[])

        status = self.status(
            deliver, inbox, from_addr='x@anywhere.test', spf=None, dkim=None, spam=None, virus=None
        )

        assert status == 'accepted'


class TestDailyCap:
    def set_cap(self, cap: int) -> None:
        ReceiptsSettings.objects.update_or_create(
            pk=1, defaults={'max_files_per_user_per_day': cap}
        )

    def test_partial(self, deliver, inbox, funded_user, fake_storage):
        self.set_cap(2)
        raw = build_email(
            attachments=[pdf('a.pdf'), jpeg('b.jpg'), png('c.png')], ses=Ses(recipient=inbox)
        )

        message = deliver(raw)

        email = one_email(message)
        assert email.status == InboundEmailStatus.ACCEPTED
        assert [r.original_filename for r in email.receipts.order_by('original_filename')] == [
            'a.pdf',
            'b.jpg',
        ]
        assert '1 of 3 files skipped' in email.note and 'daily limit' in email.note
        assert get_account(funded_user).intake_count == 2
        eml = fake_storage.objects[email.raw_key]
        assert b'X-Receipts-Skipped: daily limit' in eml

    def test_full(self, deliver, inbox, funded_user, fake_storage):
        self.set_cap(1)
        first = deliver(build_email(attachments=[pdf()], ses=Ses(recipient=inbox)))
        assert one_email(first).status == InboundEmailStatus.ACCEPTED
        stored = get_account(funded_user).stored_bytes
        balance = get_balance(funded_user)

        message = deliver(build_email(attachments=[pdf('again.pdf')], ses=Ses(recipient=inbox)))

        email = one_email(message)
        assert email.status == InboundEmailStatus.REJECTED_CAP
        assert 'Daily limit of 1' in email.note
        assert email.receipts.count() == 0 and email.raw_key == ''
        assert Receipt.objects.count() == 1
        assert get_account(funded_user).stored_bytes == stored
        assert get_balance(funded_user) == balance
        assert message.s3_key not in fake_storage.objects

    def test_it_is_the_same_counter_uploads_use(self, deliver, inbox, funded_user):
        from ..services import uploads

        self.set_cap(2)
        uploads.start_upload(
            funded_user,
            'a.jpg',
            len(samples.jpeg_bytes()),
            'image/jpeg',
            base64.b64encode(hashlib.sha256(samples.jpeg_bytes()).digest()).decode(),
        )

        message = deliver(
            build_email(attachments=[pdf('a.pdf'), jpeg('b.jpg')], ses=Ses(recipient=inbox))
        )

        email = one_email(message)
        assert email.receipts.count() == 1 and '1 of 2 files skipped' in email.note

    def test_deleting_receipts_does_not_free_slots(self, deliver, inbox):
        self.set_cap(1)
        first = one_email(deliver(build_email(attachments=[pdf()], ses=Ses(recipient=inbox))))
        for receipt in first.receipts.all():
            purge_receipt(receipt)

        message = deliver(build_email(attachments=[pdf()], ses=Ses(recipient=inbox)))

        assert one_email(message).status == InboundEmailStatus.REJECTED_CAP

    def test_a_body_receipt_counts_too(self, deliver, inbox):
        self.set_cap(1)
        deliver(build_email(ses=Ses(recipient=inbox)))

        message = deliver(build_email(ses=Ses(recipient=inbox)))

        assert one_email(message).status == InboundEmailStatus.REJECTED_CAP

    def test_each_user_has_their_own_cap(self, deliver, inbox):
        self.set_cap(1)
        other = make_user()
        add_credits(other, 10, source='test')
        deliver(build_email(attachments=[pdf()], ses=Ses(recipient=inbox)))

        message = deliver(
            build_email(attachments=[pdf()], ses=Ses(recipient=inbox_address_for(other)))
        )

        assert one_email(message).status == InboundEmailStatus.ACCEPTED


class TestSizeAndEmptiness:
    def test_too_large_with_nothing_else_is_rejected(self, deliver, inbox, fake_storage):
        ReceiptsSettings.objects.update_or_create(pk=1, defaults={'max_file_bytes': 100})
        raw = build_email(attachments=[pdf()], ses=Ses(recipient=inbox))

        message = deliver(raw)

        email = one_email(message)
        assert email.status == InboundEmailStatus.REJECTED_TOO_LARGE
        assert email.raw_key == '' and email.receipts.count() == 0
        assert Receipt.objects.count() == 0

    def test_a_large_attachment_is_not_swapped_for_the_body(self, deliver, inbox):
        ReceiptsSettings.objects.update_or_create(pk=1, defaults={'max_file_bytes': 100})
        raw = build_email(
            text='Thanks for your order, your invoice is attached below. Total 42.10 USD.',
            attachments=[pdf()],
            ses=Ses(recipient=inbox),
        )

        assert one_email(deliver(raw)).status == InboundEmailStatus.REJECTED_TOO_LARGE

    def test_a_large_file_next_to_a_good_one_is_skipped_with_a_note(
        self, deliver, inbox, fake_storage
    ):
        big = Attachment('big.png', samples.png_bytes((900, 900)), 'image', 'png')
        ReceiptsSettings.objects.update_or_create(
            pk=1, defaults={'max_file_bytes': len(samples.text_pdf_bytes()) + 10}
        )
        raw = build_email(attachments=[pdf(), big], ses=Ses(recipient=inbox))

        message = deliver(raw)

        email = one_email(message)
        assert email.status == InboundEmailStatus.ACCEPTED
        assert [r.original_filename for r in email.receipts.all()] == ['invoice.pdf']
        assert '1 file(s) over the size limit skipped' in email.note
        assert b'X-Receipts-Skipped: too large' in fake_storage.objects[email.raw_key]

    def test_a_too_large_body_is_rejected(self, deliver, inbox):
        ReceiptsSettings.objects.update_or_create(pk=1, defaults={'max_file_bytes': 10})

        assert (
            one_email(deliver(build_email(ses=Ses(recipient=inbox)))).status == 'rejected_too_large'
        )

    def test_nothing_usable_is_no_receipt(self, deliver, inbox, fake_storage, funded_user):
        raw = build_email(
            text='ok',
            attachments=[Attachment('notes.zip', b'PK\x03\x04 zipdata', 'application', 'zip')],
            ses=Ses(recipient=inbox),
        )

        message = deliver(raw)

        email = one_email(message)
        assert email.status == InboundEmailStatus.NO_RECEIPT
        assert email.receipts.count() == 0 and email.raw_key == ''
        assert get_account(funded_user).intake_count == 0

    def test_an_unsupported_attachment_with_a_real_body_falls_back_to_the_body(
        self, deliver, inbox
    ):
        raw = build_email(
            attachments=[Attachment('terms.zip', b'PK\x03\x04 zipdata', 'application', 'zip')],
            ses=Ses(recipient=inbox),
        )

        [receipt] = list(one_email(deliver(raw)).receipts.all())
        assert receipt.content_type == 'text/plain'


class TestStoredCopyAndAccounting:
    def test_the_stripped_eml(self, deliver, inbox, fake_storage, funded_user):
        raw = build_email(
            text='Order attached',
            attachments=[pdf(), jpeg()],
            subject='Order R-55102',
            ses=Ses(recipient=inbox),
        )

        message = deliver(raw)

        email = one_email(message)
        eml = fake_storage.objects[email.raw_key]
        assert email.raw_key == email_key(funded_user.pk, email.pk)
        assert email.size_bytes == len(eml) and len(eml) < len(raw) / 2
        assert fake_storage.content_types[email.raw_key] == 'message/rfc822'
        # No attachment payload survived; headers and the body text did.
        assert not contains_attachment(eml, samples.text_pdf_bytes())
        assert not contains_attachment(eml, samples.jpeg_bytes())
        msg = mime.parse_message(eml)
        assert mime.subject(msg) == 'Order R-55102'
        assert msg.get('X-SES-Spam-Verdict') == 'PASS'
        assert b'Order attached' in eml
        by_name = {p.get_filename(): p for p in mime.iter_leaves(msg) if mime.is_attachment_like(p)}
        receipts = {r.original_filename: r for r in email.receipts.all()}
        for name, part in by_name.items():
            assert part['X-Receipts-Receipt-Id'] == str(receipts[name].pk)
            assert mime.part_bytes(part) == b''

    def test_a_body_receipt_is_not_stored_twice(self, deliver, inbox, fake_storage):
        raw = build_email(text=None, html=HTML_RECEIPT, ses=Ses(recipient=inbox))

        message = deliver(raw)

        email = one_email(message)
        [receipt] = list(email.receipts.all())
        eml = fake_storage.objects[email.raw_key]
        assert b'Thanks for your order' not in eml
        assert f'X-Receipts-Receipt-Id: {receipt.pk}'.encode() in eml

    def test_stored_bytes_are_exactly_the_files_plus_the_eml(
        self, deliver, inbox, funded_user, fake_storage
    ):
        raw = build_email(attachments=[pdf(), jpeg()], ses=Ses(recipient=inbox))

        message = deliver(raw)

        email = one_email(message)
        files = sum(r.size_bytes for r in email.receipts.all())
        assert get_account(funded_user).stored_bytes == files + email.size_bytes
        on_disk = sum(
            len(v)
            for k, v in fake_storage.objects.items()
            if k.startswith(user_prefix(funded_user.pk))
        )
        assert on_disk == files + email.size_bytes

    def test_purging_every_receipt_returns_the_account_to_zero(
        self, deliver, inbox, funded_user, fake_storage
    ):
        message = deliver(build_email(attachments=[pdf(), jpeg()], ses=Ses(recipient=inbox)))

        for receipt in list(one_email(message).receipts.all()):
            purge_receipt(receipt)

        assert get_account(funded_user).stored_bytes == 0
        assert not [k for k in fake_storage.objects if k.startswith(user_prefix(funded_user.pk))]

    def test_the_first_bytes_restart_the_billing_clock(self, deliver, inbox, funded_user):
        ReceiptsAccount.objects.filter(pk=funded_user.pk).update(
            last_billed_at=timezone.now() - timedelta(days=30)
        )

        deliver(build_email(attachments=[pdf()], ses=Ses(recipient=inbox)))

        assert timezone.now() - get_account(funded_user).last_billed_at < timedelta(minutes=1)


class TestFrozenAccountAndAttention:
    def test_a_frozen_account_still_receives_its_receipts(self, deliver, inbox, funded_user):
        ReceiptsAccount.objects.filter(pk=funded_user.pk).update(frozen_at=timezone.now())

        message = deliver(build_email(attachments=[pdf()], ses=Ses(recipient=inbox)))

        [receipt] = list(one_email(message).receipts.all())
        assert receipt.status == ReceiptStatus.WAITING_FOR_CREDITS
        assert receipt.file_key and get_balance(funded_user) == 100
        assert get_account(funded_user).stored_bytes > 0

    def test_an_account_without_credits_waits_for_credits(self, deliver, regular_user):
        address = inbox_address_for(regular_user)

        message = deliver(build_email(attachments=[pdf()], ses=Ses(recipient=address)))

        [receipt] = list(one_email(message).receipts.all())
        assert receipt.status == ReceiptStatus.WAITING_FOR_CREDITS

    def test_the_owner_is_told_once_when_a_receipt_needs_attention(
        self, deliver, inbox, fake_ai, sent_emails, funded_user
    ):
        fake_ai.queue = [sample_extraction(), sample_extraction(is_receipt=False)]
        raw = build_email(
            text='Two files', attachments=[pdf('a.pdf'), jpeg('b.jpg')], ses=Ses(recipient=inbox)
        )

        message = deliver(raw)

        email = one_email(message)
        statuses = sorted(r.status for r in email.receipts.all())
        assert statuses == [ReceiptStatus.FAILED, ReceiptStatus.PROCESSED]
        [mail] = sent_emails
        assert mail['to'] == funded_user.email
        assert mail['subject'] == '1 receipt needs your attention'
        email.refresh_from_db()
        assert email.attention_notified_at is not None

    def test_no_email_when_everything_processed(self, deliver, inbox, sent_emails):
        deliver(build_email(attachments=[pdf(), jpeg()], ses=Ses(recipient=inbox)))

        assert sent_emails == []

    def test_a_rejected_email_sends_nothing(self, deliver, inbox, sent_emails):
        deliver(build_email(attachments=[pdf()], ses=Ses(recipient=inbox, spam='FAIL')))

        assert sent_emails == []


class TestRetries:
    def seed(self, fake_storage, raw: bytes) -> InboundMessage:
        key = f'{INBOUND_PREFIX}{uuid.uuid4()}'
        fake_storage.seed(key, raw)
        return InboundMessage.objects.create(s3_key=key)

    def test_a_failure_schedules_a_retry_with_backoff(self, fake_storage, inbox, monkeypatch):
        message = self.seed(
            fake_storage, build_email(attachments=[pdf()], ses=Ses(recipient=inbox))
        )
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
        message = self.seed(
            fake_storage, build_email(attachments=[pdf()], ses=Ses(recipient=inbox))
        )
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
        assert Receipt.objects.count() == 1

    def test_after_three_attempts_it_fails_and_keeps_the_object(
        self, fake_storage, inbox, monkeypatch
    ):
        message = self.seed(
            fake_storage, build_email(attachments=[pdf()], ses=Ses(recipient=inbox))
        )
        monkeypatch.setattr(
            fake_storage,
            'get_object_bytes',
            lambda *a, **k: (_ for _ in ()).throw(RuntimeError('boom')),
        )

        for _ in range(inbound.MAX_ATTEMPTS):
            InboundMessage.objects.filter(pk=message.pk).update(next_attempt_at=None)
            inbound.ingest(message.pk)

        message.refresh_from_db()
        assert message.status == InboundMessageStatus.FAILED
        assert message.attempts == 3
        assert 'RuntimeError: boom' in message.last_error
        assert message.next_attempt_at is None
        assert_parked(fake_storage, message)  # kept for inspection, out of the polled prefix

    def test_a_failed_message_is_never_claimed_again(self, fake_storage):
        message = self.seed(fake_storage, b'x')
        InboundMessage.objects.filter(pk=message.pk).update(status=InboundMessageStatus.FAILED)

        inbound.ingest(message.pk)

        message.refresh_from_db()
        assert message.attempts == 0

    def test_a_message_that_keeps_killing_its_worker_is_given_up_on(self, fake_storage):
        """Each claim bumps `attempts` and a dead worker never records the failure."""
        message = self.seed(fake_storage, b'x')
        InboundMessage.objects.filter(pk=message.pk).update(attempts=inbound.MAX_ATTEMPTS)

        inbound.ingest(message.pk)

        message.refresh_from_db()
        assert message.status == InboundMessageStatus.FAILED
        assert 'interrupted' in message.last_error
        assert_parked(fake_storage, message)

    def test_a_running_claim_is_not_taken_twice(self, fake_storage, inbox):
        message = self.seed(
            fake_storage, build_email(attachments=[pdf()], ses=Ses(recipient=inbox))
        )
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

    def test_a_missing_object_after_the_work_was_done_is_done(self, deliver, inbox, fake_storage):
        message = deliver(build_email(attachments=[pdf()], ses=Ses(recipient=inbox)))
        InboundMessage.objects.filter(pk=message.pk).update(
            status=InboundMessageStatus.PENDING, next_attempt_at=None
        )

        inbound.ingest(message.pk)  # the object is gone, the email row exists

        message.refresh_from_db()
        assert message.status == InboundMessageStatus.DONE
        assert Receipt.objects.count() == 1

    def test_a_failure_for_one_recipient_does_not_redo_the_other(
        self, fake_storage, funded_user, django_capture_on_commit_callbacks, monkeypatch
    ):
        other = make_user()
        add_credits(other, 50, source='test')
        first, second = inbox_address_for(funded_user), inbox_address_for(other)
        message = self.seed(
            fake_storage,
            build_email(attachments=[pdf()], ses=Ses(recipient=f'{first}, {second}')),
        )
        real_put = fake_storage.put_object

        def failing_put(key, body, **kwargs):
            if key.startswith(user_prefix(other.pk)) and key.endswith('.eml'):
                raise ClientError({'Error': {'Code': 'InternalError', 'Message': 'x'}}, 'PutObject')
            real_put(key, body, **kwargs)

        monkeypatch.setattr(fake_storage, 'put_object', failing_put)
        with django_capture_on_commit_callbacks(execute=True):
            inbound.ingest(message.pk)

        message.refresh_from_db()
        assert message.status == InboundMessageStatus.PENDING and message.attempts == 1
        # The first user is complete; the second left nothing behind (no rows, no objects, no cap).
        assert InboundEmail.objects.filter(user=funded_user).count() == 1
        assert not InboundEmail.objects.filter(user=other).exists()
        assert not Receipt.objects.filter(user=other).exists()
        assert not [k for k in fake_storage.objects if k.startswith(user_prefix(other.pk))]
        assert get_account(other).intake_count == 0 and get_account(other).stored_bytes == 0

        monkeypatch.setattr(fake_storage, 'put_object', real_put)
        InboundMessage.objects.filter(pk=message.pk).update(next_attempt_at=None)
        with django_capture_on_commit_callbacks(execute=True):
            inbound.ingest(message.pk)

        message.refresh_from_db()
        assert message.status == InboundMessageStatus.DONE
        assert Receipt.objects.filter(user=funded_user).count() == 1  # not duplicated
        assert Receipt.objects.filter(user=other).count() == 1
        assert get_account(funded_user).intake_count == 1

    def test_the_admin_reset_puts_a_failed_message_back_and_queues_it(
        self, fake_storage, inbox, django_capture_on_commit_callbacks, monkeypatch
    ):
        queued = []

        class Recorder:
            def enqueue(self, message_id):
                queued.append(message_id)

        monkeypatch.setattr(inbound, 'ingest_message', Recorder())
        message = self.seed(fake_storage, b'x')
        InboundMessage.objects.filter(pk=message.pk).update(
            status=InboundMessageStatus.FAILED, attempts=3, last_error='boom'
        )

        with django_capture_on_commit_callbacks(execute=True):
            assert inbound.reset_message_for_retry(message)

        message.refresh_from_db()
        assert message.status == InboundMessageStatus.PENDING
        assert (message.attempts, message.last_error, message.next_attempt_at) == (0, '', None)
        assert queued == [message.pk]
        # Only a failed one is reset.
        with django_capture_on_commit_callbacks(execute=True):
            assert not inbound.reset_message_for_retry(message)
        assert queued == [message.pk]


class TestFailedMessagesAreParked:
    def seed(self, fake_storage, raw: bytes) -> InboundMessage:
        key = f'{INBOUND_PREFIX}{uuid.uuid4()}'
        fake_storage.seed(key, raw)
        return InboundMessage.objects.create(s3_key=key)

    def test_a_retry_moves_the_object_back_and_the_ingest_then_works(
        self, fake_storage, inbox, django_capture_on_commit_callbacks, monkeypatch
    ):
        raw = build_email(attachments=[pdf()], ses=Ses(recipient=inbox))
        message = self.seed(fake_storage, raw)
        limit = inbound.MAX_RAW_BYTES
        monkeypatch.setattr(inbound, 'MAX_RAW_BYTES', 10)  # too large -> fails at once
        inbound.ingest(message.pk)
        message.refresh_from_db()
        assert_parked(fake_storage, message)
        parked_key = message.failed_key
        monkeypatch.setattr(inbound, 'MAX_RAW_BYTES', limit)

        with django_capture_on_commit_callbacks(execute=True):
            assert inbound.reset_message_for_retry(message)

        message.refresh_from_db()
        assert message.status == InboundMessageStatus.DONE  # the queued ingest ran inline
        assert message.failed_key == ''
        assert parked_key not in fake_storage.objects
        assert Receipt.objects.count() == 1

    def test_a_retry_puts_the_object_back_under_the_polled_prefix(self, fake_storage, monkeypatch):
        queued_ids = []

        class Recorder:
            def enqueue(self, message_id):
                queued_ids.append(message_id)

        monkeypatch.setattr(inbound, 'ingest_message', Recorder())
        message = self.seed(fake_storage, b'raw')
        parked = failed_key_for(message.s3_key)
        fake_storage.objects[parked] = fake_storage.objects.pop(message.s3_key)
        InboundMessage.objects.filter(pk=message.pk).update(status='failed', failed_key=parked)
        message.refresh_from_db()

        assert inbound.reset_message_for_retry(message)

        message.refresh_from_db()
        assert message.status == 'pending' and message.failed_key == ''
        assert fake_storage.objects == {message.s3_key: b'raw'}

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
        assert message.s3_key in fake_storage.objects

        monkeypatch.setattr(fake_storage, 'copy_object', real_copy)
        inbound.poll()

        message.refresh_from_db()
        assert_parked(fake_storage, message)

    def test_the_parked_key_mirrors_the_inbound_name(self):
        assert failed_key_for(f'{INBOUND_PREFIX}abc123') == f'{FAILED_PREFIX}abc123'
        assert failed_key_for('somewhere/else') == f'{FAILED_PREFIX}somewhere_else'

    def test_a_missing_object_has_nothing_to_park(self, fake_storage):
        message = InboundMessage.objects.create(s3_key=f'{INBOUND_PREFIX}gone')

        inbound.ingest(message.pk)

        message.refresh_from_db()
        assert message.status == 'failed' and message.failed_key == ''
        assert not fake_storage.objects


class TestClaimToken:
    """The final writes only apply while the worker still holds the claim it made."""

    def claimed(self, fake_storage) -> InboundMessage:
        key = f'{INBOUND_PREFIX}{uuid.uuid4()}'
        fake_storage.seed(key, b'raw')
        message = InboundMessage.objects.create(s3_key=key)
        claimed = inbound._claim(message.pk)
        assert claimed is not None
        return claimed

    def take_over(self, message: InboundMessage) -> None:
        """Another worker claims it after the lease ran out."""
        InboundMessage.objects.filter(pk=message.pk).update(
            next_attempt_at=timezone.now() - timedelta(seconds=1)
        )
        assert inbound._claim(message.pk) is not None

    def test_a_stale_worker_cannot_mark_it_done(self, fake_storage):
        stale = self.claimed(fake_storage)
        self.take_over(stale)

        assert not inbound._mark_done(stale)

        assert InboundMessage.objects.get(pk=stale.pk).status == 'pending'

    def test_a_stale_worker_cannot_fail_it_or_move_its_object(self, fake_storage):
        stale = self.claimed(fake_storage)
        self.take_over(stale)

        inbound._mark_failed(stale, 'boom')

        current = InboundMessage.objects.get(pk=stale.pk)
        assert current.status == 'pending' and current.last_error == ''
        assert stale.s3_key in fake_storage.objects and not current.failed_key

    def test_a_stale_failure_does_not_shorten_the_new_workers_lease(self, fake_storage):
        stale = self.claimed(fake_storage)
        self.take_over(stale)
        lease = InboundMessage.objects.get(pk=stale.pk).next_attempt_at

        inbound._record_failure(stale, RuntimeError('late'))

        current = InboundMessage.objects.get(pk=stale.pk)
        assert current.next_attempt_at == lease and current.last_error == ''

    def test_a_stale_worker_does_not_delete_the_object(self, fake_storage, inbox, monkeypatch):
        key = f'{INBOUND_PREFIX}{uuid.uuid4()}'
        fake_storage.seed(key, build_email(attachments=[pdf()], ses=Ses(recipient=inbox)))
        message = InboundMessage.objects.create(s3_key=key)
        real_mark_done = inbound._mark_done

        def lose_the_claim(message):
            InboundMessage.objects.filter(pk=message.pk).update(attempts=message.attempts + 1)
            return real_mark_done(message)

        monkeypatch.setattr(inbound, '_mark_done', lose_the_claim)

        inbound.ingest(message.pk)

        assert key in fake_storage.objects  # the worker that holds the claim now will finish it


class TestHostileMessages:
    def test_deeply_nested_forwards_are_ingested_not_failed(self, deliver, inbox):
        """About 20 `message/rfc822` levels used to raise `KeyError` in `analyze` -- a crash a
        free account could repeat to fail messages for good."""
        for levels in (20, 25, 40):
            raw = nested_forwards(levels, build_email(attachments=[pdf('deep.pdf')]))
            raw = raw.replace(FIXED_ADDRESS.encode(), inbox.encode())

            message = deliver(raw)

            assert message.status == InboundMessageStatus.DONE, message.last_error
            assert message.attempts == 1

    def test_deep_nesting_with_a_text_body_at_the_bottom(self, deliver, inbox):
        raw = nested_forwards(25).replace(FIXED_ADDRESS.encode(), inbox.encode())

        message = deliver(raw)

        assert message.status == InboundMessageStatus.DONE, message.last_error
        assert one_email(message).status in (
            InboundEmailStatus.ACCEPTED,
            InboundEmailStatus.NO_RECEIPT,
        )

    def test_a_part_count_bomb_fails_fast_and_for_good(self, fake_storage, inbox, monkeypatch):
        parts = b''.join(b'--b\r\n\r\nx\r\n' for _ in range(mime.MAX_BOUNDARY_LINES + 100))
        raw = (
            (
                f'Received: by inbound-smtp.us-east-1.amazonaws.com with SMTP id a for {inbox};\r\n'
                'Content-Type: multipart/mixed; boundary=b\r\n\r\n'
            ).encode()
            + parts
            + b'--b--\r\n'
        )
        key = f'{INBOUND_PREFIX}{uuid.uuid4()}'
        fake_storage.seed(key, raw)
        message = InboundMessage.objects.create(s3_key=key)
        monkeypatch.setattr(
            mime, 'parse_message', lambda _raw: pytest.fail('a bomb must not reach the parser')
        )

        inbound.ingest(message.pk)

        message.refresh_from_db()
        assert message.attempts == 1  # no retries
        assert 'MIME parts' in message.last_error
        assert_parked(fake_storage, message)
        assert InboundEmail.objects.count() == 0

    def test_a_huge_html_body_next_to_a_pdf_does_not_inflate_the_stored_eml(
        self, deliver, inbox, fake_storage, funded_user
    ):
        """5 MB of HTML plus a tiny PDF: the PDF is the receipt; the HTML used to be kept whole
        in the `.eml`, and billed."""
        big = '<html><body><p>' + 'x' * (5 * 1024 * 1024) + '</p></body></html>'
        raw = build_email(html=big, attachments=[pdf()], ses=Ses(recipient=inbox))

        message = deliver(raw)

        email = one_email(message)
        assert email.status == InboundEmailStatus.ACCEPTED
        eml = fake_storage.objects[email.raw_key]
        assert len(eml) < 20 * 1024 and email.size_bytes == len(eml)
        assert b'X-Receipts-Skipped: too large' in eml
        files = sum(r.size_bytes for r in email.receipts.all())
        assert get_account(funded_user).stored_bytes == files + len(eml)

    def test_a_stripped_eml_over_the_ceiling_is_headers_only(
        self, deliver, inbox, fake_storage, monkeypatch
    ):
        monkeypatch.setattr(mime, 'MAX_EML_BYTES', 1100)
        raw = build_email(attachments=[pdf()], ses=Ses(recipient=inbox))

        email = one_email(deliver(raw))

        eml = fake_storage.objects[email.raw_key]
        assert b'X-Receipts-Stripped: headers only' in eml and b'Thanks for your order' not in eml
        assert email.receipts.count() == 1  # the receipt itself is unaffected

    def test_a_message_parsed_once_serves_every_recipient(
        self, deliver, funded_user, inbox, fake_storage, monkeypatch
    ):
        other = make_user()
        add_credits(other, 50, source='test')
        calls = []
        real_parse = mime.parse_message
        monkeypatch.setattr(mime, 'parse_message', lambda raw: calls.append(1) or real_parse(raw))
        raw = build_email(
            attachments=[pdf()],
            ses=Ses(recipient=f'{inbox}, {inbox_address_for(other)}'),
        )

        message = deliver(raw)

        assert message.emails.count() == 2
        assert len(calls) == 1
        # And each recipient's copy is marked with its own receipt id.
        for email in message.emails.all():
            [receipt] = list(email.receipts.all())
            assert (
                f'X-Receipts-Receipt-Id: {receipt.pk}'.encode()
                in fake_storage.objects[email.raw_key]
            )


class TestDeterministicIds:
    def test_ids_depend_only_on_message_recipient_and_part(self):
        a = inbound.receipt_id_for(7, 'receipts+abcd2345@ziptrigo.com', 2)

        assert a == inbound.receipt_id_for(7, 'receipts+abcd2345@ziptrigo.com', 2)
        assert a != inbound.receipt_id_for(8, 'receipts+abcd2345@ziptrigo.com', 2)
        assert a != inbound.receipt_id_for(7, 'receipts+abcd2345@ziptrigo.com', 3)
        assert a != inbound.receipt_id_for(7, 'receipts+zzzz2222@ziptrigo.com', 2)
        assert inbound.email_id_for(7, 'x@y.com') != inbound.receipt_id_for(7, 'x@y.com', 0)

    def test_the_rows_use_them(self, deliver, inbox):
        message = deliver(build_email(attachments=[pdf(), jpeg()], ses=Ses(recipient=inbox)))

        email = one_email(message)
        assert email.pk == inbound.email_id_for(message.pk, inbox)
        possible = {inbound.receipt_id_for(message.pk, inbox, leaf) for leaf in range(20)}
        receipts = list(email.receipts.all())
        assert len(receipts) == 2 and {r.pk for r in receipts} <= possible

    def test_a_hard_crash_leaves_no_orphans_after_the_retry(
        self, fake_storage, funded_user, inbox, django_capture_on_commit_callbacks, monkeypatch
    ):
        """Simulate a kill after the uploads: nothing ran the cleanup (deletes are disabled and
        the transaction fails). The next try writes the very same keys, so the bucket ends up
        with exactly the stored objects and nothing left over."""
        key = f'{INBOUND_PREFIX}{uuid.uuid4()}'
        fake_storage.seed(key, build_email(attachments=[pdf(), jpeg()], ses=Ses(recipient=inbox)))
        message = InboundMessage.objects.create(s3_key=key)
        real_delete = fake_storage.delete_object
        real_add = inbound.add_stored_bytes

        def kill(*args, **kwargs):
            raise RuntimeError('kill')

        monkeypatch.setattr(fake_storage, 'delete_object', lambda k: None)
        monkeypatch.setattr(inbound, 'add_stored_bytes', kill)
        inbound.ingest(message.pk)
        orphans = {k for k in fake_storage.objects if k.startswith(user_prefix(funded_user.pk))}
        assert len(orphans) == 3  # two files and the .eml, written, with no rows

        monkeypatch.setattr(inbound, 'add_stored_bytes', real_add)
        monkeypatch.setattr(fake_storage, 'delete_object', real_delete)
        InboundMessage.objects.filter(pk=message.pk).update(next_attempt_at=None)
        with django_capture_on_commit_callbacks(execute=True):
            inbound.ingest(message.pk)

        message.refresh_from_db()
        assert message.status == InboundMessageStatus.DONE
        stored = {k for k in fake_storage.objects if k.startswith(user_prefix(funded_user.pk))}
        assert stored == orphans  # the same keys, overwritten
        email = one_email(message)
        assert stored == {email.raw_key, *(r.file_key for r in email.receipts.all())}
        assert get_account(funded_user).stored_bytes == sum(
            len(fake_storage.objects[k]) for k in stored
        )

    def test_a_concurrent_duplicate_does_not_delete_the_winners_objects(
        self, fake_storage, funded_user, inbox, django_capture_on_commit_callbacks, monkeypatch
    ):
        """Two workers ingesting the same message write the same keys. The one that loses the
        unique-constraint race must leave the winner's objects alone."""
        key = f'{INBOUND_PREFIX}{uuid.uuid4()}'
        fake_storage.seed(key, build_email(attachments=[pdf()], ses=Ses(recipient=inbox)))
        message = InboundMessage.objects.create(s3_key=key)
        context = inbound._Context(
            message=message,
            raw=fake_storage.objects[key],
            parsed=mime.parse_message(fake_storage.objects[key]),
        )
        [recipient] = mime.scan_recipients(context.parsed).matches
        real_store_round = inbound._store_round

        def other_worker_finishes_first(*args, **kwargs):
            # The winner completes this recipient (rows and objects) just before we commit.
            monkeypatch.setattr(inbound, '_store_round', real_store_round)
            with django_capture_on_commit_callbacks(execute=False):
                inbound._ingest_for_recipient(context, recipient)
            monkeypatch.setattr(inbound, '_store_round', other_worker_finishes_first)
            return real_store_round(*args, **kwargs)

        monkeypatch.setattr(inbound, '_store_round', other_worker_finishes_first)
        with pytest.raises(IntegrityError):  # the loser hits the unique constraint
            inbound._ingest_for_recipient(context, recipient)

        [email] = list(InboundEmail.objects.all())
        assert email.raw_key in fake_storage.objects
        [receipt] = list(email.receipts.all())
        assert receipt.file_key in fake_storage.objects


class TestStoreStructure:
    def test_objects_are_uploaded_before_any_transaction_or_lock_starts(
        self, deliver, inbox, fake_storage, funded_user, monkeypatch
    ):
        """The account row must not be locked while megabytes go to S3."""
        from django.db import connection

        seen = []
        real_put = fake_storage.put_object

        def put(key, body, **kwargs):
            if key.startswith(user_prefix(funded_user.pk)):
                seen.append((len(connection.savepoint_ids), get_account(funded_user).intake_count))
            real_put(key, body, **kwargs)

        monkeypatch.setattr(fake_storage, 'put_object', put)

        message = deliver(build_email(attachments=[pdf(), jpeg()], ses=Ses(recipient=inbox)))

        assert message.status == InboundMessageStatus.DONE
        assert len(seen) == 3  # two files and the .eml
        assert all(depth == 0 and count == 0 for depth, count in seen)
        assert get_account(funded_user).intake_count == 2

    def test_a_cap_that_shrinks_meanwhile_is_planned_again(
        self, deliver, inbox, funded_user, fake_storage, monkeypatch
    ):
        ReceiptsSettings.objects.update_or_create(pk=1, defaults={'max_files_per_user_per_day': 1})
        real = inbound.intake_slots_remaining
        answers = iter([5])  # the first look says there is room for everything

        monkeypatch.setattr(
            inbound, 'intake_slots_remaining', lambda *a: next(answers, None) or real(*a)
        )

        message = deliver(
            build_email(attachments=[pdf('a.pdf'), jpeg('b.jpg')], ses=Ses(recipient=inbox))
        )

        email = one_email(message)
        assert email.status == InboundEmailStatus.ACCEPTED
        assert [r.original_filename for r in email.receipts.all()] == ['a.pdf']
        assert '1 of 2 files skipped' in email.note
        assert get_account(funded_user).intake_count == 1
        stored = {k for k in fake_storage.objects if k.startswith(user_prefix(funded_user.pk))}
        assert stored == {email.raw_key, *(r.file_key for r in email.receipts.all())}

    def test_a_cap_that_keeps_changing_falls_back_to_the_ordinary_retry(
        self, fake_storage, inbox, funded_user, monkeypatch
    ):
        ReceiptsSettings.objects.update_or_create(pk=1, defaults={'max_files_per_user_per_day': 1})
        monkeypatch.setattr(inbound, 'intake_slots_remaining', lambda *a: 5)
        key = f'{INBOUND_PREFIX}{uuid.uuid4()}'
        fake_storage.seed(key, build_email(attachments=[pdf(), jpeg()], ses=Ses(recipient=inbox)))
        message = InboundMessage.objects.create(s3_key=key)

        inbound.ingest(message.pk)

        message.refresh_from_db()
        assert (
            message.status == InboundMessageStatus.PENDING
            and 'cap kept changing' in message.last_error
        )
        assert not [k for k in fake_storage.objects if k.startswith(user_prefix(funded_user.pk))]
        assert get_account(funded_user).intake_count == 0


class TestInboundRowsCeiling:
    def set_ceiling(self, rows: int) -> None:
        ReceiptsSettings.objects.update_or_create(pk=1, defaults={'max_inbound_rows_per_day': rows})

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

        message = deliver(build_email(attachments=[pdf()], ses=Ses(recipient=inbox)))

        assert one_email(message).status == InboundEmailStatus.ACCEPTED
        assert Receipt.objects.count() == 1

    def test_the_other_unaccepted_kinds_are_covered_too(self, deliver, fixed_inbox, account):
        self.set_ceiling(1)
        deliver(self.spam(fixed_inbox))

        confirmation = deliver((FIXTURES / 'gmail_forwarding_confirmation.eml').read_bytes())
        no_receipt = deliver(build_email(text='ok', ses=Ses(recipient=fixed_inbox)))

        assert not confirmation.emails.exists() and not no_receipt.emails.exists()

    def test_only_the_last_24_hours_count(self, deliver, inbox, funded_user):
        self.set_ceiling(1)
        deliver(self.spam(inbox))
        InboundEmail.objects.filter(user=funded_user).update(
            created_at=timezone.now() - timedelta(hours=25)
        )

        message = deliver(self.spam(inbox))

        assert message.emails.count() == 1

    def test_the_ceiling_is_per_user(self, deliver, inbox):
        self.set_ceiling(1)
        other = make_user()
        add_credits(other, 10, source='test')
        deliver(self.spam(inbox))

        message = deliver(self.spam(inbox_address_for(other)))

        assert message.emails.count() == 1

    def test_a_dropped_row_does_not_make_the_message_fail_or_retry_forever(self, deliver, inbox):
        self.set_ceiling(1)
        deliver(self.spam(inbox))

        message = deliver(self.spam(inbox))

        assert message.status == InboundMessageStatus.DONE and message.attempts == 1


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
        # Nothing was read or parsed, and nothing deleted.
        assert a in fake_storage.objects and not fake_storage.deleted

    def test_it_is_idempotent_for_keys_already_known(
        self, fake_storage, queued, django_capture_on_commit_callbacks
    ):
        self.put(fake_storage, 'a')
        with django_capture_on_commit_callbacks(execute=True):
            inbound.poll()
            inbound.poll()
            inbound.poll()

        assert InboundMessage.objects.count() == 1
        assert len(queued) == 1

    def test_it_ignores_other_prefixes_and_the_folder_placeholder(
        self, fake_storage, queued, django_capture_on_commit_callbacks
    ):
        fake_storage.seed(INBOUND_PREFIX, b'')
        fake_storage.seed('receipts/users/x/files/y', b'z')
        fake_storage.seed('transfers/x', b'z')

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
        """The starvation bug: failed objects used to stay in the listing, so with enough of them
        the poller never reached newer keys. Now a failure moves its object out of the prefix."""
        monkeypatch.setattr(inbound, 'MAX_RAW_BYTES', 10)
        for i in range(inbound.POLL_BATCH + 20):
            key = self.put(fake_storage, f'a{i:04}', b'x' * 100)  # over the size guard: fails
            message = InboundMessage.objects.create(s3_key=key)
            inbound.ingest(message.pk)

        # Whatever happened to them, none of the failures is left in the polled prefix.
        failed = InboundMessage.objects.filter(status='failed')
        assert failed.count() == inbound.POLL_BATCH + 20
        assert not fake_storage.list_keys(INBOUND_PREFIX)
        new_key = self.put(fake_storage, 'zzzz-new')

        with django_capture_on_commit_callbacks(execute=True):
            inbound.poll()

        assert InboundMessage.objects.filter(s3_key=new_key, status='pending').exists()

    def test_an_interrupted_move_of_failed_messages_is_finished_by_the_poll(
        self, fake_storage, queued, django_capture_on_commit_callbacks
    ):
        """If a failure was recorded but the object never left (the copy or delete failed), the
        poller finishes the move, a batch a minute; new mail is still registered meanwhile."""
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
        parked = InboundMessage.objects.filter(status='failed', failed_key__startswith='receipts/')
        assert parked.count() == inbound.POLL_BATCH + 50

    def test_a_done_message_whose_object_survived_is_cleaned_up(self, fake_storage, queued):
        key = self.put(fake_storage, 'done-one')
        InboundMessage.objects.create(s3_key=key, status=InboundMessageStatus.DONE)

        inbound.poll()

        assert key not in fake_storage.objects
        assert queued == []

    def test_a_failed_message_is_not_ingested_again_just_moved(self, fake_storage, queued):
        key = self.put(fake_storage, 'failed-one')
        message = InboundMessage.objects.create(s3_key=key, status=InboundMessageStatus.FAILED)

        inbound.poll()

        message.refresh_from_db()
        assert queued == []
        assert message.failed_key == failed_key_for(key)
        assert key not in fake_storage.objects and message.failed_key in fake_storage.objects

    def test_an_unqueued_pending_message_is_requeued_after_ten_minutes(self, fake_storage, queued):
        stale = InboundMessage.objects.create(s3_key=self.put(fake_storage, 'stale'))
        fresh = InboundMessage.objects.create(s3_key=self.put(fake_storage, 'fresh'))
        InboundMessage.objects.filter(pk=stale.pk).update(
            created_at=timezone.now() - timedelta(minutes=11)
        )

        inbound.poll()

        assert queued == [stale.pk]
        assert fresh.pk not in queued

    def test_it_does_not_queue_the_same_message_every_minute(self, fake_storage, queued):
        stale = InboundMessage.objects.create(s3_key=self.put(fake_storage, 'stale'))
        InboundMessage.objects.filter(pk=stale.pk).update(
            created_at=timezone.now() - timedelta(minutes=11)
        )

        inbound.poll()
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

    def test_end_to_end_the_poll_leads_to_receipts(
        self, fake_storage, inbox, django_capture_on_commit_callbacks
    ):
        key = self.put(
            fake_storage, 'e2e', build_email(attachments=[pdf()], ses=Ses(recipient=inbox))
        )

        with django_capture_on_commit_callbacks(execute=True):
            inbound.poll()

        message = InboundMessage.objects.get(s3_key=key)
        assert message.status == InboundMessageStatus.DONE
        assert Receipt.objects.filter(source=ReceiptSource.EMAIL).count() == 1
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
