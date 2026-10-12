import pytest
from django.conf import settings
from django.urls import reverse

from ..models import FailureReason, Receipt, ReceiptStatus
from ..services import emails
from .factories import create_inbound_email, create_receipt

pytestmark = [pytest.mark.django_db, pytest.mark.unit]


def attention(funded_user, fake_storage, *specs):
    email = create_inbound_email(funded_user, subject='Fwd: October <b>invoices</b>')
    for fields in specs:
        create_receipt(funded_user, fake_storage, inbound_email=email, source='email', **fields)
    return email


def test_the_subject_counts_and_never_contains_sender_text(funded_user, fake_storage, sent_emails):
    email = attention(
        funded_user,
        fake_storage,
        {'status': ReceiptStatus.FAILED, 'failure_reason': FailureReason.NOT_A_RECEIPT},
        {'status': ReceiptStatus.NEEDS_REVIEW, 'original_filename': 'invoice-oct.pdf'},
        {'status': ReceiptStatus.PROCESSED},
    )

    emails.send_attention_email.call(str(email.pk))

    [mail] = sent_emails
    assert mail['subject'] == '2 receipts need your attention'
    assert 'October' not in mail['subject'] and 'invoice' not in mail['subject']
    assert mail['to'] == funded_user.email


def test_the_body_lists_each_problem_with_a_link(funded_user, fake_storage, sent_emails):
    email = attention(
        funded_user,
        fake_storage,
        {
            'status': ReceiptStatus.FAILED,
            'failure_reason': FailureReason.MULTIPLE_RECEIPTS,
            'original_filename': 'scan.pdf',
        },
        {'status': ReceiptStatus.NEEDS_REVIEW, 'original_filename': 'invoice-oct.pdf'},
    )

    emails.send_attention_email.call(str(email.pk))

    [mail] = sent_emails
    text = mail['text_body']
    assert 'From the email "Fwd: October <b>invoices</b>"' in text
    assert "invoice-oct.pdf couldn't read the vendor, amount, date" in text
    assert 'scan.pdf contains more than one receipt; upload each separately' in text
    base = settings.BASE_URL
    assert f'Manage receipts: {base}{reverse("receipts:dashboard")}' in text
    # Only a receipt that needs review gets a "Review" link, built from the URLconf.
    needs_review = Receipt.objects.get(inbound_email=email, status=ReceiptStatus.NEEDS_REVIEW)
    assert f'Review: {base}{reverse("receipts:detail", args=[needs_review.pk])}' in text
    assert text.count('Review: ') == 1


def test_the_html_body_escapes_everything_the_sender_controls(
    funded_user, fake_storage, sent_emails
):
    email = attention(
        funded_user,
        fake_storage,
        {
            'status': ReceiptStatus.NEEDS_REVIEW,
            'original_filename': '<script>alert(1)</script>.pdf',
        },
    )

    emails.send_attention_email.call(str(email.pk))

    html = sent_emails[0]['html_body']
    assert '<script>' not in html
    assert '&lt;script&gt;' in html
    assert '&lt;b&gt;invoices&lt;/b&gt;' in html
    assert '<b>' not in html


def test_one_receipt_is_singular(funded_user, fake_storage, sent_emails):
    email = attention(funded_user, fake_storage, {'status': ReceiptStatus.NEEDS_REVIEW})

    emails.send_attention_email.call(str(email.pk))

    assert sent_emails[0]['subject'] == '1 receipt needs your attention'


def test_nothing_is_sent_to_an_unconfirmed_address(funded_user, fake_storage, sent_emails):
    funded_user.email_confirmed = False
    funded_user.save()
    email = attention(funded_user, fake_storage, {'status': ReceiptStatus.NEEDS_REVIEW})

    emails.send_attention_email.call(str(email.pk))

    assert sent_emails == []


def test_nothing_is_sent_when_nothing_needs_attention_any_more(
    funded_user, fake_storage, sent_emails
):
    email = attention(funded_user, fake_storage, {'status': ReceiptStatus.PROCESSED})

    emails.send_attention_email.call(str(email.pk))

    assert sent_emails == []


def test_trashed_receipts_are_not_mentioned(funded_user, fake_storage, sent_emails):
    from django.utils import timezone

    email = attention(
        funded_user,
        fake_storage,
        {'status': ReceiptStatus.NEEDS_REVIEW, 'deleted_at': timezone.now()},
    )

    emails.send_attention_email.call(str(email.pk))

    assert sent_emails == []


@pytest.mark.parametrize('email_id', ['00000000-0000-0000-0000-000000000000', 'garbage'])
def test_an_unknown_email_id_is_ignored(sent_emails, email_id):
    emails.send_attention_email.call(email_id)

    assert sent_emails == []


def test_the_emails_go_to_the_owner_only(funded_user, fake_storage, sent_emails):
    email = attention(funded_user, fake_storage, {'status': ReceiptStatus.NEEDS_REVIEW})

    emails.send_attention_email.call(str(email.pk))

    assert [mail['to'] for mail in sent_emails] == [funded_user.email]
    assert email.from_address not in str(sent_emails)


def test_storage_paused_email(funded_user, sent_emails):
    emails.send_storage_paused_email.call(str(funded_user.pk))

    [mail] = sent_emails
    assert mail['to'] == funded_user.email
    assert mail['subject'] == 'Your receipts storage is paused'
    assert 'credits' in mail['text_body']
    assert '/billing/credits/' in mail['text_body']
    assert 'href=' in mail['html_body']


def test_storage_paused_is_not_sent_to_an_unconfirmed_address(funded_user, sent_emails):
    funded_user.email_confirmed = False
    funded_user.save()

    emails.send_storage_paused_email.call(str(funded_user.pk))

    assert sent_emails == []


def test_storage_paused_for_a_deleted_user(sent_emails):
    emails.send_storage_paused_email.call('00000000-0000-0000-0000-000000000000')
    emails.send_storage_paused_email.call('nope')

    assert sent_emails == []


def test_both_emails_are_tasks_on_the_receipts_queue():
    assert emails.send_attention_email.queue_name == 'receipts'
    assert emails.send_storage_paused_email.queue_name == 'receipts'
