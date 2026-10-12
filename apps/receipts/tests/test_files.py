import pytest
from django.core.exceptions import ValidationError
from django.utils import timezone

from ..models import ReceiptsAccount, ReceiptStatus
from ..services import files
from ..services.accounts import get_account
from ..services.errors import AccountFrozenError
from . import samples

pytestmark = [pytest.mark.django_db, pytest.mark.unit]


def freeze(user):
    ReceiptsAccount.objects.filter(pk=get_account(user).pk).update(frozen_at=timezone.now())


def test_download_is_an_attachment_with_the_original_name(make_receipt, fake_storage):
    receipt = make_receipt(original_filename='IMG 2041.heic')

    url = files.download_url(receipt)

    assert url.startswith('https://fake-s3.test/')
    assert fake_storage.get_urls == [
        {
            'key': receipt.file_key,
            'filename': 'IMG 2041.heic',
            'disposition': 'attachment',
            'content_type': None,
        }
    ]


def test_download_of_a_nameless_email_body_gets_a_default_name(make_receipt, fake_storage):
    receipt = make_receipt(data=samples.HTML_BODY, original_filename='')

    files.download_url(receipt)

    assert fake_storage.get_urls[0]['filename'] == 'receipt'


@pytest.mark.parametrize(
    ('data', 'content_type'),
    [
        (samples.text_pdf_bytes(), 'application/pdf'),
        (samples.jpeg_bytes(), 'image/jpeg'),
        (samples.png_bytes(), 'image/png'),
        (samples.webp_bytes(), 'image/webp'),
    ],
)
def test_preview_shows_browser_safe_types_inline_with_the_sniffed_type(
    make_receipt, fake_storage, data, content_type
):
    receipt = make_receipt(data=data)

    files.preview_url(receipt)

    [call] = fake_storage.get_urls
    assert call['key'] == receipt.file_key
    assert call['disposition'] == 'inline'
    assert call['content_type'] == content_type


def test_a_heic_original_is_previewed_through_its_jpeg(make_receipt, fake_storage):
    receipt = make_receipt(data=samples.heic_bytes(), preview_key='receipts/users/u/previews/p.jpg')

    files.preview_url(receipt)

    [call] = fake_storage.get_urls
    assert call['key'] == 'receipts/users/u/previews/p.jpg'
    assert call['content_type'] == 'image/jpeg'
    assert call['disposition'] == 'inline'


def test_a_heic_without_a_preview_yet(make_receipt):
    receipt = make_receipt(data=samples.heic_bytes())

    with pytest.raises(ValidationError, match='not ready'):
        files.preview_url(receipt)


def test_an_html_body_is_never_served_inline_from_s3(make_receipt, fake_storage):
    receipt = make_receipt(data=samples.HTML_BODY)

    with pytest.raises(ValidationError, match='no inline preview'):
        files.preview_url(receipt)

    assert fake_storage.get_urls == []


def test_the_html_body_is_read_for_the_csp_locked_view(make_receipt):
    receipt = make_receipt(data=samples.HTML_BODY)

    assert files.get_body_bytes(receipt) == samples.HTML_BODY


@pytest.mark.parametrize('call', [files.download_url, files.preview_url, files.get_body_bytes])
def test_everything_is_refused_while_the_account_is_frozen(make_receipt, funded_user, call):
    receipt = make_receipt(status=ReceiptStatus.PROCESSED)
    freeze(funded_user)

    with pytest.raises(AccountFrozenError):
        call(receipt)


@pytest.mark.parametrize('call', [files.download_url, files.preview_url])
def test_an_upload_in_progress_has_no_file_yet(make_receipt, call):
    receipt = make_receipt(uploaded=False)

    with pytest.raises(ValidationError, match='no file'):
        call(receipt)


def test_a_receipt_whose_file_was_rejected_has_no_file(make_receipt):
    receipt = make_receipt(file_key='', status=ReceiptStatus.FAILED)

    with pytest.raises(ValidationError):
        files.download_url(receipt)
