"""Unit tests for `apps.file_transfer.services.storage` that don't need the full send/download
flow: the S3 key shape, the `Content-Disposition` header it builds, and that `S3Storage` passes
the right parameters to boto3 (checked against a stub client rather than real S3/Floci).
"""

from unittest.mock import MagicMock

import pytest

from ..services.storage import S3Storage, storage_key, transfer_prefix

pytestmark = [pytest.mark.unit]


def test_storage_key_has_no_filename_segment():
    """Deliberately not `.../<filename>` -- see the module docstring for why (a `/`, `..` or
    otherwise unsafe name in the key itself)."""
    key = storage_key('11111111-1111-1111-1111-111111111111', 'file-id')
    assert key == 'transfers/11111111-1111-1111-1111-111111111111/file-id'


def test_transfer_prefix_covers_its_files():
    assert transfer_prefix('t1') == 'transfers/t1/'
    assert storage_key('t1', 'f1').startswith(transfer_prefix('t1'))


def _storage() -> tuple[S3Storage, MagicMock]:
    client = MagicMock()
    client.generate_presigned_url.return_value = 'https://example.test/signed'
    return S3Storage(client=client), client


def test_presigned_get_url_ascii_filename():
    storage, client = _storage()
    storage.presigned_get_url('transfers/t/f', 'report.pdf')

    params = client.generate_presigned_url.call_args.kwargs['Params']
    disposition = params['ResponseContentDisposition']
    assert disposition == 'attachment; filename="report.pdf"; filename*=UTF-8\'\'report.pdf'


def test_presigned_get_url_non_ascii_filename_has_both_forms():
    storage, client = _storage()
    storage.presigned_get_url('transfers/t/f', 'résumé.pdf')

    disposition = client.generate_presigned_url.call_args.kwargs['Params'][
        'ResponseContentDisposition'
    ]
    # ASCII fallback for clients that don't understand filename*, encoded UTF-8 for those that do.
    assert 'filename="r_sum_.pdf"' in disposition
    assert "filename*=UTF-8''r%C3%A9sum%C3%A9.pdf" in disposition


def test_presigned_get_url_strips_quotes_and_backslashes_from_ascii_fallback():
    storage, client = _storage()
    storage.presigned_get_url('transfers/t/f', 'evil".pdf; x=1\\')

    disposition = client.generate_presigned_url.call_args.kwargs['Params'][
        'ResponseContentDisposition'
    ]
    assert '"' not in disposition.split('filename="', 1)[1].split('"', 1)[0].replace("'", '')
    assert '\\' not in disposition


def test_presign_part_url_signs_content_length_and_checksum():
    storage, client = _storage()
    storage.presign_part_url(
        'transfers/t/f', 'upload-1', 2, content_length=1024, checksum_sha256='abcd=='
    )

    params = client.generate_presigned_url.call_args.kwargs['Params']
    assert params['ContentLength'] == 1024
    assert params['ChecksumSHA256'] == 'abcd=='
    assert params['PartNumber'] == 2


def test_presign_part_url_omits_optional_params_when_not_given():
    storage, client = _storage()
    storage.presign_part_url('transfers/t/f', 'upload-1', 1)

    params = client.generate_presigned_url.call_args.kwargs['Params']
    assert 'ContentLength' not in params
    assert 'ChecksumSHA256' not in params
