"""Unit tests for `apps.core.services.storage`: that `S3Storage` sends boto3 the right
parameters, checked against botocore's `Stubber` (which also validates every request against the
real S3 API model) rather than real S3/Floci. Presigning needs no network, so the presigned-URL
tests use a real client built by `build_client` with dummy credentials and inspect the URL.
"""

import io
from urllib.parse import parse_qs, urlsplit

import boto3
import pytest
from botocore.exceptions import ClientError
from botocore.response import StreamingBody
from botocore.stub import Stubber

from ..services.storage import (
    ObjectTooLargeError,
    S3Storage,
    UnsupportedSignatureVersionError,
    build_client,
)

pytestmark = [pytest.mark.unit]

BUCKET = 'test-bucket'


@pytest.fixture
def stubbed():
    """An `S3Storage` over a real (offline) client with a `Stubber` attached."""
    client = boto3.client(
        's3',
        region_name='eu-west-1',
        aws_access_key_id='AKIATESTKEY',
        aws_secret_access_key='test-secret',
    )
    with Stubber(client) as stubber:
        yield S3Storage(bucket=BUCKET, client=client), stubber
        stubber.assert_no_pending_responses()


@pytest.fixture
def presigner() -> S3Storage:
    client = build_client(
        region='eu-west-1',
        endpoint_url=None,
        access_key_id='AKIATESTKEY',
        secret_access_key='test-secret',
        # SigV4 is what actually signs `Content-Length` (see `build_client`'s docstring).
        signature_version='s3v4',
    )
    return S3Storage(bucket=BUCKET, client=client)


def _query(url: str) -> dict[str, list[str]]:
    return parse_qs(urlsplit(url).query)


def _signed_headers(url: str) -> set[str]:
    return set(_query(url)['X-Amz-SignedHeaders'][0].split(';'))


class _ReadSpy(io.BytesIO):
    reads = 0

    def read(self, size=-1):
        self.reads += 1
        return super().read(size)


def _body(data: bytes) -> StreamingBody:
    return StreamingBody(io.BytesIO(data), len(data))


# -- build_client -------------------------------------------------------------------------------


def test_build_client_passes_settings_through():
    client = build_client(
        region='eu-west-1',
        endpoint_url='http://localhost:4566',
        access_key_id='AKIATESTKEY',
        secret_access_key='test-secret',
    )
    assert client.meta.region_name == 'eu-west-1'
    assert client.meta.endpoint_url == 'http://localhost:4566'


def test_build_client_treats_empty_values_as_unset():
    client = build_client(
        region='eu-west-1', endpoint_url='', access_key_id='', secret_access_key=''
    )
    assert client.meta.endpoint_url == 'https://s3.eu-west-1.amazonaws.com'


def test_build_client_signature_version_opt_in_gives_sigv4_presigned_urls():
    client = build_client('us-east-1', None, 'AKIATESTKEY', 'test-secret', signature_version='s3v4')
    url = client.generate_presigned_url('get_object', Params={'Bucket': BUCKET, 'Key': 'k'})
    assert 'X-Amz-Algorithm=AWS4-HMAC-SHA256' in url


# -- put_object / get_object_bytes / copy_object ------------------------------------------------


def test_put_object_without_content_type(stubbed):
    storage, stubber = stubbed
    stubber.add_response('put_object', {}, {'Bucket': BUCKET, 'Key': 'a/b', 'Body': b'hello'})
    storage.put_object('a/b', b'hello')


def test_put_object_with_content_type(stubbed):
    storage, stubber = stubbed
    stubber.add_response(
        'put_object',
        {},
        {'Bucket': BUCKET, 'Key': 'a/b.pdf', 'Body': b'%PDF', 'ContentType': 'application/pdf'},
    )
    storage.put_object('a/b.pdf', b'%PDF', content_type='application/pdf')


def test_get_object_bytes_returns_the_whole_body(stubbed):
    storage, stubber = stubbed
    stubber.add_response(
        'get_object', {'Body': _body(b'contents')}, {'Bucket': BUCKET, 'Key': 'a/b'}
    )
    assert storage.get_object_bytes('a/b') == b'contents'


def test_get_object_bytes_within_max_bytes(stubbed):
    storage, stubber = stubbed
    stubber.add_response(
        'get_object',
        {'Body': _body(b'12345'), 'ContentLength': 5},
        {'Bucket': BUCKET, 'Key': 'a/b'},
    )
    assert storage.get_object_bytes('a/b', max_bytes=5) == b'12345'


def test_get_object_bytes_over_max_bytes_raises_before_reading(stubbed):
    storage, stubber = stubbed
    raw = _ReadSpy(b'123456')
    stubber.add_response(
        'get_object',
        {'Body': StreamingBody(raw, 6), 'ContentLength': 6},
        {'Bucket': BUCKET, 'Key': 'a/b'},
    )
    with pytest.raises(ObjectTooLargeError):
        storage.get_object_bytes('a/b', max_bytes=5)
    assert raw.reads == 0


def test_get_object_bytes_max_bytes_caps_the_read_without_content_length(stubbed):
    storage, stubber = stubbed
    stubber.add_response('get_object', {'Body': _body(b'123456')}, {'Bucket': BUCKET, 'Key': 'a/b'})
    with pytest.raises(ObjectTooLargeError):
        storage.get_object_bytes('a/b', max_bytes=5)


def test_get_object_bytes_missing_key_raises(stubbed):
    storage, stubber = stubbed
    stubber.add_client_error('get_object', service_error_code='NoSuchKey', http_status_code=404)
    with pytest.raises(ClientError) as excinfo:
        storage.get_object_bytes('missing')
    assert excinfo.value.response['Error']['Code'] == 'NoSuchKey'


def test_get_object_stream_reads_in_chunks(stubbed):
    storage, stubber = stubbed
    stubber.add_response('get_object', {'Body': _body(b'abcdef')}, {'Bucket': BUCKET, 'Key': 'a/b'})
    stream = storage.get_object_stream('a/b')
    assert stream.read(4) == b'abcd'
    assert stream.read(4) == b'ef'


def test_copy_object_copies_within_the_bucket(stubbed):
    storage, stubber = stubbed
    stubber.add_response(
        'copy_object',
        {},
        {
            'Bucket': BUCKET,
            'Key': 'processed/x',
            'CopySource': {'Bucket': BUCKET, 'Key': 'inbound/x'},
        },
    )
    storage.copy_object('inbound/x', 'processed/x')


# -- list_keys ----------------------------------------------------------------------------------


def _list_response(keys: list[str], *, truncated: bool = False, token: str | None = None) -> dict:
    response: dict = {
        'IsTruncated': truncated,
        'Contents': [{'Key': key} for key in keys],
        'Name': BUCKET,
    }
    if token:
        response['NextContinuationToken'] = token
    return response


def test_list_keys_returns_keys_under_prefix(stubbed):
    storage, stubber = stubbed
    stubber.add_response(
        'list_objects_v2',
        _list_response(['inbound/a', 'inbound/b']),
        {'Bucket': BUCKET, 'Prefix': 'inbound/'},
    )
    assert storage.list_keys('inbound/') == ['inbound/a', 'inbound/b']


def test_list_keys_empty_prefix_has_no_contents(stubbed):
    storage, stubber = stubbed
    stubber.add_response('list_objects_v2', {'IsTruncated': False, 'Name': BUCKET}, None)
    assert storage.list_keys('nothing/') == []


def test_list_keys_follows_pagination(stubbed):
    storage, stubber = stubbed
    stubber.add_response(
        'list_objects_v2', _list_response(['p/1', 'p/2'], truncated=True, token='t1'), None
    )
    stubber.add_response('list_objects_v2', _list_response(['p/3']), None)
    assert storage.list_keys('p/') == ['p/1', 'p/2', 'p/3']


def test_list_keys_stops_at_max_keys(stubbed):
    storage, stubber = stubbed
    stubber.add_response(
        'list_objects_v2', _list_response(['p/1', 'p/2', 'p/3'], truncated=True, token='t1'), None
    )
    assert storage.list_keys('p/', max_keys=2) == ['p/1', 'p/2']


def test_list_keys_with_no_limit_returns_everything(stubbed):
    storage, stubber = stubbed
    stubber.add_response(
        'list_objects_v2', _list_response(['p/1', 'p/2'], truncated=True, token='t1'), None
    )
    stubber.add_response('list_objects_v2', _list_response(['p/3']), None)
    assert storage.list_keys('p/', max_keys=None) == ['p/1', 'p/2', 'p/3']


def test_list_keys_rejects_a_non_positive_max_keys(stubbed):
    storage, _ = stubbed
    with pytest.raises(ValueError):
        storage.list_keys('p/', max_keys=0)


# -- delete_prefix / head_object ---------------------------------------------------------------


def test_delete_prefix_deletes_every_listed_key(stubbed):
    storage, stubber = stubbed
    stubber.add_response('list_objects_v2', _list_response(['p/1', 'p/2']), None)
    stubber.add_response(
        'delete_objects',
        {},
        {'Bucket': BUCKET, 'Delete': {'Objects': [{'Key': 'p/1'}, {'Key': 'p/2'}]}},
    )
    assert storage.delete_prefix('p/') == 2


def test_delete_objects_sends_every_key_in_one_request(stubbed):
    storage, stubber = stubbed
    stubber.add_response(
        'delete_objects',
        {},
        {
            'Bucket': BUCKET,
            'Delete': {'Objects': [{'Key': 'a'}, {'Key': 'b'}], 'Quiet': True},
        },
    )
    assert storage.delete_objects(['a', 'b']) == 2


def test_delete_objects_batches_at_a_thousand_keys(stubbed):
    storage, stubber = stubbed
    keys = [f'k/{n}' for n in range(1001)]
    for batch in (keys[:1000], keys[1000:]):
        stubber.add_response(
            'delete_objects',
            {},
            {
                'Bucket': BUCKET,
                'Delete': {'Objects': [{'Key': key} for key in batch], 'Quiet': True},
            },
        )
    assert storage.delete_objects(keys) == 1001


def test_delete_objects_with_no_keys_makes_no_request(stubbed):
    storage, _ = stubbed
    assert storage.delete_objects([]) == 0


def test_delete_objects_raises_when_s3_reports_a_failed_key(stubbed):
    storage, stubber = stubbed
    stubber.add_response(
        'delete_objects',
        {'Errors': [{'Key': 'b', 'Code': 'AccessDenied', 'Message': 'no'}]},
        None,
    )
    with pytest.raises(RuntimeError, match='b: AccessDenied'):
        storage.delete_objects(['a', 'b'])


def test_head_object_reports_size_and_checksum(stubbed):
    storage, stubber = stubbed
    stubber.add_response(
        'head_object',
        {'ContentLength': 7, 'ChecksumSHA256': 'abc='},
        {'Bucket': BUCKET, 'Key': 'a/b', 'ChecksumMode': 'ENABLED'},
    )
    info = storage.head_object('a/b')
    assert (info.size, info.checksum_sha256) == (7, 'abc=')


def test_object_exists_is_false_on_client_error(stubbed):
    storage, stubber = stubbed
    stubber.add_client_error('head_object', service_error_code='404', http_status_code=404)
    assert storage.object_exists('missing') is False


# -- multipart ----------------------------------------------------------------------------------


def test_create_multipart_upload_defaults_to_sha256(stubbed):
    storage, stubber = stubbed
    stubber.add_response(
        'create_multipart_upload',
        {'UploadId': 'u1'},
        {'Bucket': BUCKET, 'Key': 'k', 'ChecksumAlgorithm': 'SHA256'},
    )
    assert storage.create_multipart_upload('k') == 'u1'


def test_create_multipart_upload_can_opt_out_of_checksums(stubbed):
    storage, stubber = stubbed
    stubber.add_response(
        'create_multipart_upload', {'UploadId': 'u2'}, {'Bucket': BUCKET, 'Key': 'k'}
    )
    assert storage.create_multipart_upload('k', checksum_algorithm=None) == 'u2'


def test_upload_part_returns_the_etag(stubbed):
    storage, stubber = stubbed
    stubber.add_response(
        'upload_part',
        {'ETag': '"etag-1"'},
        {'Bucket': BUCKET, 'Key': 'k', 'UploadId': 'u1', 'PartNumber': 1, 'Body': b'data'},
    )
    assert storage.upload_part('k', 'u1', 1, b'data') == '"etag-1"'


def test_complete_multipart_upload(stubbed):
    storage, stubber = stubbed
    parts = [{'PartNumber': 1, 'ETag': '"e1"', 'ChecksumSHA256': 'abc='}]
    stubber.add_response(
        'complete_multipart_upload',
        {},
        {
            'Bucket': BUCKET,
            'Key': 'k',
            'UploadId': 'u1',
            'MultipartUpload': {'Parts': parts},
        },
    )
    storage.complete_multipart_upload('k', 'u1', parts)


def test_list_parts_includes_checksums(stubbed):
    storage, stubber = stubbed
    stubber.add_response(
        'list_parts',
        {
            'IsTruncated': False,
            'Parts': [
                {'PartNumber': 1, 'ETag': '"e1"', 'Size': 5, 'ChecksumSHA256': 'abc='},
                {'PartNumber': 2, 'ETag': '"e2"', 'Size': 3},
            ],
        },
        {'Bucket': BUCKET, 'Key': 'k', 'UploadId': 'u1'},
    )
    assert storage.list_parts('k', 'u1') == [
        {'PartNumber': 1, 'ETag': '"e1"', 'Size': 5, 'ChecksumSHA256': 'abc='},
        {'PartNumber': 2, 'ETag': '"e2"', 'Size': 3, 'ChecksumSHA256': ''},
    ]


def test_abort_multipart_upload_ignores_an_already_gone_upload(stubbed):
    storage, stubber = stubbed
    stubber.add_client_error(
        'abort_multipart_upload', service_error_code='NoSuchUpload', http_status_code=404
    )
    storage.abort_multipart_upload('k', 'gone')


# -- presigned_put_url --------------------------------------------------------------------------


def test_presigned_put_url_signs_content_length_and_checksum(presigner):
    url = presigner.presigned_put_url(
        'inbound/x.pdf', content_length=1024, checksum_sha256='abcd=='
    )

    assert urlsplit(url).path.endswith('/inbound/x.pdf')
    signed = _signed_headers(url)
    assert 'content-length' in signed
    assert 'x-amz-checksum-sha256' in signed


def test_presigned_put_url_signs_content_type_when_given(presigner):
    url = presigner.presigned_put_url(
        'inbound/x.pdf', content_length=10, content_type='application/pdf'
    )
    assert 'content-type' in _signed_headers(url)


def test_presigned_put_url_omits_optional_params_when_not_given(presigner):
    url = presigner.presigned_put_url('inbound/x.pdf', content_length=10)
    signed = _signed_headers(url)
    assert 'content-length' in signed
    assert 'x-amz-checksum-sha256' not in signed
    assert 'content-type' not in signed


def test_presigned_put_url_refuses_a_non_sigv4_client():
    client = build_client(
        region='us-east-1',
        endpoint_url=None,
        access_key_id='AKIATESTKEY',
        secret_access_key='test-secret',
    )
    storage = S3Storage(bucket=BUCKET, client=client)

    with pytest.raises(UnsupportedSignatureVersionError):
        storage.presigned_put_url('k', content_length=10)


def test_presigned_put_url_works_on_an_explicit_sigv4_client(presigner):
    assert presigner.client.meta.config.signature_version == 's3v4'
    url = presigner.presigned_put_url('k', content_length=10)
    assert 'X-Amz-Algorithm=AWS4-HMAC-SHA256' in url


def test_presigned_put_url_uses_the_expiry(presigner):
    url = presigner.presigned_put_url('k', content_length=1, expires_in=120)
    assert _query(url)['X-Amz-Expires'] == ['120']


def test_presigned_put_url_passes_expected_params_to_boto():
    from unittest.mock import MagicMock

    client = MagicMock()
    client.generate_presigned_url.return_value = (
        'https://example.test/signed?X-Amz-SignedHeaders='
        'content-length%3Bcontent-type%3Bhost%3Bx-amz-checksum-sha256'
    )
    storage = S3Storage(bucket=BUCKET, client=client)

    storage.presigned_put_url(
        'k', content_length=5, checksum_sha256='abc=', content_type='image/png', expires_in=60
    )

    client.generate_presigned_url.assert_called_once_with(
        'put_object',
        Params={
            'Bucket': BUCKET,
            'Key': 'k',
            'ContentLength': 5,
            'ChecksumSHA256': 'abc=',
            'ContentType': 'image/png',
        },
        ExpiresIn=60,
    )


# -- presigned_get_url / presign_part_url -------------------------------------------------------


def _mock_storage():
    from unittest.mock import MagicMock

    client = MagicMock()
    client.generate_presigned_url.return_value = 'https://example.test/signed'
    return S3Storage(bucket=BUCKET, client=client), client


def _params(client) -> dict:
    return client.generate_presigned_url.call_args.kwargs['Params']


def test_presigned_get_url_defaults_to_attachment_without_content_type():
    storage, client = _mock_storage()
    storage.presigned_get_url('k', 'report.pdf')

    params = _params(client)
    assert params['ResponseContentDisposition'] == (
        'attachment; filename="report.pdf"; filename*=UTF-8\'\'report.pdf'
    )
    assert 'ResponseContentType' not in params
    assert client.generate_presigned_url.call_args.kwargs['ExpiresIn'] == 15 * 60


def test_presigned_get_url_inline_with_content_type():
    storage, client = _mock_storage()
    storage.presigned_get_url(
        'k', 'invoice.pdf', disposition='inline', content_type='application/pdf'
    )

    params = _params(client)
    assert params['ResponseContentDisposition'].startswith('inline; filename="invoice.pdf"')
    assert params['ResponseContentType'] == 'application/pdf'


@pytest.mark.parametrize(
    'content_type',
    ['text/html', 'image/svg+xml', 'application/xhtml+xml', 'application/octet-stream'],
)
def test_presigned_get_url_inline_rejects_content_types_that_could_run_script(content_type):
    storage, client = _mock_storage()
    with pytest.raises(ValueError):
        storage.presigned_get_url('k', 'a', disposition='inline', content_type=content_type)
    client.generate_presigned_url.assert_not_called()


@pytest.mark.parametrize(
    'content_type',
    [
        'application/pdf',
        'image/jpeg',
        'image/png',
        'image/webp',
        'image/gif',
        'text/plain',
        'Application/PDF',
        'text/plain; charset=utf-8',
    ],
)
def test_presigned_get_url_inline_allows_safe_content_types(content_type):
    storage, client = _mock_storage()
    storage.presigned_get_url('k', 'a', disposition='inline', content_type=content_type)
    assert _params(client)['ResponseContentType'] == content_type


def test_presigned_get_url_attachment_allows_any_content_type():
    storage, client = _mock_storage()
    storage.presigned_get_url('k', 'a.html', content_type='text/html')
    assert _params(client)['ResponseContentType'] == 'text/html'


def test_presigned_get_url_rejects_an_unknown_disposition():
    storage, _ = _mock_storage()
    with pytest.raises(ValueError):
        storage.presigned_get_url('k', 'a.pdf', disposition='download')


def test_presigned_get_url_non_ascii_filename_has_both_forms():
    storage, client = _mock_storage()
    storage.presigned_get_url('k', 'résumé.pdf')

    disposition = _params(client)['ResponseContentDisposition']
    # ASCII fallback for clients that don't understand filename*, encoded UTF-8 for those that do.
    assert 'filename="r_sum_.pdf"' in disposition
    assert "filename*=UTF-8''r%C3%A9sum%C3%A9.pdf" in disposition


def test_presigned_get_url_strips_quotes_and_backslashes_from_ascii_fallback():
    storage, client = _mock_storage()
    storage.presigned_get_url('k', 'evil".pdf; x=1\\')

    disposition = _params(client)['ResponseContentDisposition']
    assert '"' not in disposition.split('filename="', 1)[1].split('"', 1)[0].replace("'", '')
    assert '\\' not in disposition


def test_presign_part_url_signs_content_length_and_checksum():
    storage, client = _mock_storage()
    client.generate_presigned_url.return_value = 'https://example.test/signed?X-Amz-SignedHeaders=content-length%3Bhost%3Bx-amz-checksum-sha256'
    storage.presign_part_url('k', 'upload-1', 2, content_length=1024, checksum_sha256='abcd==')

    params = _params(client)
    assert params['ContentLength'] == 1024
    assert params['ChecksumSHA256'] == 'abcd=='
    assert params['PartNumber'] == 2
    assert params['Bucket'] == BUCKET


def test_presign_part_url_omits_optional_params_when_not_given():
    storage, client = _mock_storage()
    storage.presign_part_url('k', 'upload-1', 1)

    params = _params(client)
    assert 'ContentLength' not in params
    assert 'ChecksumSHA256' not in params


def test_presign_part_url_signs_both_headers_on_a_sigv4_client(presigner):
    url = presigner.presign_part_url(
        'k', 'upload-1', 1, content_length=1024, checksum_sha256='abcd=='
    )
    assert {'content-length', 'x-amz-checksum-sha256'} <= _signed_headers(url)


def test_presign_part_url_refuses_a_non_sigv4_client():
    # ziptrigo-apps#100: a default client in us-east-1 presigns with SigV2, which signs neither.
    client = build_client('us-east-1', None, 'AKIATESTKEY', 'test-secret')
    storage = S3Storage(bucket=BUCKET, client=client)

    with pytest.raises(UnsupportedSignatureVersionError):
        storage.presign_part_url('k', 'upload-1', 1, content_length=1024)


def test_presign_part_url_without_size_or_checksum_allows_any_client():
    client = build_client('us-east-1', None, 'AKIATESTKEY', 'test-secret')
    storage = S3Storage(bucket=BUCKET, client=client)

    assert storage.presign_part_url('k', 'upload-1', 1).startswith('https://')
