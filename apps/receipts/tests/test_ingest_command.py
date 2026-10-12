"""`./manage.py receipts_ingest_eml`: the real ingest path on a local file, without SES."""

from io import StringIO

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from ..models import InboundMessage, InboundMessageStatus, InboxCode, Receipt
from ..services.accounts import inbox_address_for
from ..services.storage import INBOUND_PREFIX
from .emails import FIXED_CODE, FIXTURES, Ses, build_email, pdf

pytestmark = [pytest.mark.django_db, pytest.mark.unit]


def run(*args: str) -> str:
    out = StringIO()
    call_command('receipts_ingest_eml', *args, stdout=out)
    return out.getvalue()


def test_to_replaces_the_recipient_in_the_file(
    tmp_path, account, fake_storage, django_capture_on_commit_callbacks
):
    # The file is addressed to nobody we know: `--to` decides who it is for.
    path = tmp_path / 'receipt.eml'
    path.write_bytes(
        build_email(attachments=[pdf()], ses=Ses(recipient='receipts+zzzz2222@ziptrigo.com'))
    )
    address = inbox_address_for(account.user)

    with django_capture_on_commit_callbacks(execute=True):
        output = run(str(path), '--to', address)

    assert f'{address}: accepted' in output and '1 receipt(s)' in output
    message = InboundMessage.objects.get()
    assert message.s3_key.startswith(f'{INBOUND_PREFIX}local-')
    assert message.status == InboundMessageStatus.DONE
    assert message.s3_key not in fake_storage.objects  # the real path deletes it
    [receipt] = Receipt.objects.all()
    assert receipt.original_filename == 'invoice.pdf' and receipt.user == account.user


def test_without_to_the_headers_decide(tmp_path, account, django_capture_on_commit_callbacks):
    InboxCode.objects.filter(account=account, active=True).update(code=FIXED_CODE)

    with django_capture_on_commit_callbacks(execute=True):
        output = run(str(FIXTURES / 'pdf_attachment.eml'))

    assert 'accepted' in output and Receipt.objects.count() == 1


def test_the_verdict_headers_in_the_file_are_honoured(tmp_path, account):
    path = tmp_path / 'spam.eml'
    path.write_bytes(build_email(attachments=[pdf()], ses=Ses(recipient='x@y.com', spam='FAIL')))

    output = run(str(path), '--to', inbox_address_for(account.user))

    assert 'rejected_spam' in output and Receipt.objects.count() == 0


def test_an_address_nobody_owns_is_reported(tmp_path, db):
    path = tmp_path / 'x.eml'
    path.write_bytes(build_email(attachments=[pdf()]))

    output = run(str(path), '--to', 'receipts+zzzz2222@ziptrigo.com')

    assert 'No recipient matched an active inbox code' in output


def test_a_failed_ingest_is_an_error_and_keeps_the_object(
    tmp_path, account, fake_storage, monkeypatch
):
    path = tmp_path / 'x.eml'
    path.write_bytes(build_email(attachments=[pdf()]))
    monkeypatch.setattr(
        fake_storage,
        'get_object_bytes',
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError('boom')),
    )

    with pytest.raises(CommandError, match='pending after 1 attempt'):
        run(str(path), '--to', inbox_address_for(account.user))

    assert InboundMessage.objects.get().s3_key in fake_storage.objects


def test_a_missing_file_is_an_error(tmp_path):
    with pytest.raises(CommandError, match='is not a file'):
        run(str(tmp_path / 'nope.eml'))
