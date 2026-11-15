"""The JWT API's file request endpoints (`/api/ft/requests`, issue #306): the owner's side, over
the real URLs."""

import json
from datetime import timedelta

import pytest
from django.utils import timezone

from apps.accounts.models import User
from apps.accounts.tokens import CustomAccessToken
from apps.billing.services import add_credits, spend_credits

from .. import services
from ..models import BlockedSender, BlockedSenderKind, Transfer, TransferStatus
from .support import new_user

pytestmark = [
    pytest.mark.django_db,
    pytest.mark.unit,
    pytest.mark.usefixtures('ratelimit_clock'),
]

URL = '/api/ft/requests'


@pytest.fixture(autouse=True)
def _fake_storage(fake_storage):
    return fake_storage


@pytest.fixture
def auth_headers(funded_user) -> dict[str, str]:
    return {'HTTP_AUTHORIZATION': f'Bearer {CustomAccessToken.for_user(funded_user)}'}


def _post(client, url, payload, headers):
    return client.post(url, data=json.dumps(payload), content_type='application/json', **headers)


def _patch(client, url, payload, headers):
    return client.patch(url, data=json.dumps(payload), content_type='application/json', **headers)


def _create(client, headers, **payload):
    return _post(client, URL, {'title': 'Wedding photos', **payload}, headers)


# -- Auth and scope --


def test_requires_a_token(client):
    assert client.get(URL).status_code == 401
    assert client.post(URL, data='{}', content_type='application/json').status_code == 401


def test_rejects_an_inactive_user(client, funded_user, auth_headers):
    funded_user.status = User.STATUS_INACTIVE
    funded_user.save()
    assert client.get(URL, **auth_headers).status_code == 401


# -- Create --


def test_create_a_request(client, funded_user, auth_headers):
    response = _create(
        client,
        auth_headers,
        message='Please',
        upload_audience='signed_in',
        max_files=5,
        max_total_size_bytes=1024,
        expiry_choice='5',
        password='pw',
    )
    assert response.status_code == 201, response.content
    body = response.json()
    assert body['title'] == 'Wedding photos' and body['message'] == 'Please'
    assert body['upload_audience'] == 'signed_in' and body['status'] == 'active'
    assert body['is_open'] is True and body['done_at'] is None and body['done_by'] == ''
    assert (body['max_files'], body['max_total_size_bytes']) == (5, 1024)
    assert body['effective_max_files'] == 5 and body['has_password'] is True
    assert body['expires_at'] is not None and body['files'] == [] and body['invites'] == []
    assert body['url'].startswith('http') and '/t/' in body['url']
    assert 'password' not in body and 'password_hash' not in body
    assert Transfer.objects.get().owner == funded_user


def test_create_with_invitees_emails_them(
    client, auth_headers, django_capture_on_commit_callbacks, sent_emails
):
    with django_capture_on_commit_callbacks(execute=True):
        response = _create(
            client, auth_headers, upload_audience='selected', emails=['a@x.org', 'b@x.org']
        )
    assert response.status_code == 201
    assert sorted(i['email'] for i in response.json()['invites']) == ['a@x.org', 'b@x.org']
    assert sorted(e['to'] for e in sent_emails) == ['a@x.org', 'b@x.org']


def test_create_defaults_use_the_site_maximum(client, auth_headers):
    body = _create(client, auth_headers).json()
    assert body['max_files'] is None and body['effective_max_files'] == 100
    assert body['effective_max_total_size_bytes'] == 20 * 1024**3


@pytest.mark.parametrize(
    ('payload', 'text'),
    [
        ({'title': ''}, 'title: Give the request a title.'),
        ({'upload_audience': 'selected'}, 'emails: Add at least one email address.'),
        ({'max_files': 0}, 'max_files: Max files must be between 1 and 100.'),
        (
            {'max_total_size_bytes': 10**12},
            'max_total_size_bytes: Max total size must be at most 20.0 GB.',
        ),
        ({'upload_audience': 'nobody'}, 'upload_audience: Choose who can upload.'),
        ({'expiry_choice': 'bogus'}, 'Invalid expiry choice.'),
        ({'emails': ['x'], 'upload_audience': 'selected'}, 'is not a valid email address'),
    ],
)
def test_create_refusals_are_400(client, auth_headers, payload, text):
    response = _create(client, auth_headers, **payload)
    assert response.status_code == 400
    assert text in response.json()['detail']
    assert not Transfer.objects.exists()


def test_create_a_date_alone_expires_at_the_end_of_that_day(client, auth_headers):
    day = (timezone.now() + timedelta(days=10)).date().isoformat()
    body = _create(client, auth_headers, expiry_choice='custom', expiry_date=day).json()
    assert body['expires_at'] is not None and body['expires_at'].startswith(day)


def test_create_needs_credits_402(client, user):
    headers = {'HTTP_AUTHORIZATION': f'Bearer {CustomAccessToken.for_user(user)}'}
    response = _create(client, headers)
    assert response.status_code == 402
    assert 'at least 1 credit' in response.json()['detail']


def test_create_by_a_blocked_owner_is_403(client, funded_user, auth_headers):
    BlockedSender.objects.create(kind=BlockedSenderKind.EMAIL, value=funded_user.email.lower())
    assert _create(client, auth_headers).status_code == 403


def test_create_by_a_blocked_ip_is_403(client, auth_headers):
    BlockedSender.objects.create(kind=BlockedSenderKind.IP, value='8.8.8.8')
    assert _create(client, {**auth_headers, 'HTTP_X_REAL_IP': '8.8.8.8'}).status_code == 403


def test_create_is_rate_limited_429(client, auth_headers, settings):
    settings.RATELIMIT_ENABLE = True
    settings.RATELIMIT_RULES = {**settings.RATELIMIT_RULES, 'FT_REQUEST_CREATE_USER': (1, 3600)}
    assert _create(client, auth_headers).status_code == 201
    response = _create(client, auth_headers)
    assert response.status_code == 429 and 'Retry-After' in response


def test_create_over_the_email_budget_is_429_and_creates_nothing(client, auth_headers, settings):
    settings.RATELIMIT_ENABLE = True
    settings.RATELIMIT_RULES = {**settings.RATELIMIT_RULES, 'FT_RECIPIENT_EMAIL_USER': (1, 3600)}
    response = _create(
        client, auth_headers, upload_audience='selected', emails=['a@x.org', 'b@x.org']
    )
    assert response.status_code == 429
    assert not Transfer.objects.exists()


def test_a_refused_create_spends_no_budget(client, funded_user, auth_headers, settings):
    from apps.core.ratelimit import limiter

    settings.RATELIMIT_ENABLE = True
    _create(client, auth_headers, title='')
    assert limiter.peek(str(funded_user.pk), 'FT_REQUEST_CREATE_USER').remaining == 30


# -- List and get --


def test_list_and_get(client, auth_headers, make_request, upload_to_request, anon_uploader):
    transfer = make_request(title='One')
    upload_to_request(transfer, anon_uploader, 'a.jpg', 100, ip='4.4.4.4')

    listed = client.get(URL, **auth_headers).json()
    assert listed['count'] == 1 and listed['results'][0]['title'] == 'One'

    body = client.get(f'{URL}/{transfer.id}', **auth_headers).json()
    assert body['file_count'] == 1 and body['uploader_count'] == 1 and body['size_bytes'] == 100
    (file,) = body['files']
    assert file['name'] == 'a.jpg' and file['size'] == 100 and file['uploaded'] is True
    assert file['uploader_name'] == 'Jane' and file['uploader_email'] == ''
    assert file['uploaded_at'] is not None
    # The IP is for staff only.
    assert 'uploader_ip' not in file and '4.4.4.4' not in json.dumps(body)


def test_get_lists_signed_in_uploader_name_and_email(
    client, auth_headers, make_request, upload_to_request, user_uploader
):
    transfer = make_request()
    upload_to_request(transfer, user_uploader, 'a.jpg', 100)
    (file,) = client.get(f'{URL}/{transfer.id}', **auth_headers).json()['files']
    assert (file['uploader_name'], file['uploader_email']) == ('John', 'john@example.com')


def test_pending_files_are_not_in_the_listing(
    client, auth_headers, make_request, anon_uploader, fake_storage
):
    from ..services import request_upload

    transfer = make_request()
    request_upload.add_file(transfer, anon_uploader, 'pending.bin', 10, storage=fake_storage)
    body = client.get(f'{URL}/{transfer.id}', **auth_headers).json()
    assert body['files'] == [] and body['file_count'] == 0


def test_filters_and_paging(client, auth_headers, make_request):
    first = make_request(title='A')
    make_request(title='B')
    Transfer.objects.filter(pk=first.pk).update(status=TransferStatus.DELETED)

    active = client.get(URL, **auth_headers).json()
    ended = client.get(f'{URL}?filter=ended', **auth_headers).json()
    everything = client.get(f'{URL}?filter=all&limit=1&offset=1', **auth_headers).json()
    assert [r['title'] for r in active['results']] == ['B']
    assert [r['title'] for r in ended['results']] == ['A']
    assert everything['count'] == 2 and len(everything['results']) == 1
    assert client.get(f'{URL}?filter=nope', **auth_headers).status_code == 400


def test_sends_are_not_listed_and_requests_are_not_transfers(
    client, auth_headers, funded_user, make_request
):
    transfer = make_request()
    send = Transfer.objects.create(
        owner=funded_user, status=TransferStatus.ACTIVE, completed_at=timezone.now()
    )
    assert [r['id'] for r in client.get(f'{URL}?filter=all', **auth_headers).json()['results']] == [
        str(transfer.id)
    ]
    transfers = client.get('/api/ft/transfers/?filter=all', **auth_headers).json()
    assert [t['id'] for t in transfers['results']] == [str(send.id)]
    assert client.get(f'/api/ft/transfers/{transfer.id}', **auth_headers).status_code == 404
    assert client.get(f'{URL}/{send.id}', **auth_headers).status_code == 404


def test_another_users_request_is_a_404_everywhere(client, auth_headers, make_request):
    stranger = new_user()
    add_credits(stranger, 10, description='t', source='t')
    other = make_request(owner=stranger, upload_audience='selected', emails=['a@x.org'])
    invite = other.invites.get()
    base = f'{URL}/{other.id}'
    import uuid

    some = uuid.uuid4()
    assert client.get(base, **auth_headers).status_code == 404
    assert _patch(client, base, {'title': 'x'}, auth_headers).status_code == 404
    assert client.delete(base, **auth_headers).status_code == 404
    for action in ('done', 'reopen', 'disable', 'reenable'):
        assert _post(client, f'{base}/{action}', {}, auth_headers).status_code == 404, action
    assert _post(client, f'{base}/invites/{invite.id}/resend', {}, auth_headers).status_code == 404
    assert client.get(f'{base}/files/{some}/url', **auth_headers).status_code == 404
    assert client.delete(f'{base}/files/{some}', **auth_headers).status_code == 404
    assert client.get(f'{URL}/{some}', **auth_headers).status_code == 404


# -- Update --


def test_patch_changes_only_what_is_sent(client, auth_headers, make_request):
    transfer = make_request(message='keep')
    response = _patch(
        client, f'{URL}/{transfer.id}', {'title': 'Renamed', 'max_files': 3}, auth_headers
    )
    assert response.status_code == 200
    body = response.json()
    assert body['title'] == 'Renamed' and body['message'] == 'keep' and body['max_files'] == 3


def test_patch_null_clears_the_limits(client, auth_headers, make_request):
    transfer = make_request(max_files=3, max_total_size_bytes=1000)
    body = _patch(
        client,
        f'{URL}/{transfer.id}',
        {'max_files': None, 'max_total_size_bytes': None},
        auth_headers,
    ).json()
    assert body['max_files'] is None and body['max_total_size_bytes'] is None


def test_patch_password_and_expiry(client, auth_headers, make_request):
    transfer = make_request(password='pw')
    body = _patch(client, f'{URL}/{transfer.id}', {'remove_password': True}, auth_headers).json()
    assert body['has_password'] is False
    body = _patch(
        client, f'{URL}/{transfer.id}', {'expiry_choice': '5', 'password': 'new'}, auth_headers
    ).json()
    assert body['has_password'] is True and body['expires_at'] is not None


def test_patch_replaces_the_invitees(
    client, auth_headers, make_request, django_capture_on_commit_callbacks, sent_emails
):
    transfer = make_request(upload_audience='selected', emails=['a@x.org'])
    with django_capture_on_commit_callbacks(execute=True):
        body = _patch(client, f'{URL}/{transfer.id}', {'emails': ['b@x.org']}, auth_headers).json()
    assert [i['email'] for i in body['invites']] == ['b@x.org']
    assert [e['to'] for e in sent_emails] == ['b@x.org']


def test_patch_is_atomic(client, auth_headers, make_request):
    transfer = make_request()
    response = _patch(
        client, f'{URL}/{transfer.id}', {'title': 'Renamed', 'max_files': 0}, auth_headers
    )
    assert response.status_code == 400 and 'max_files' in response.json()['detail']
    transfer.refresh_from_db()
    assert transfer.title == 'Wedding photos'


def test_patch_limits_below_what_is_uploaded_are_400(
    client, auth_headers, make_request, upload_to_request, anon_uploader
):
    transfer = make_request()
    upload_to_request(transfer, anon_uploader, 'a', 10)
    upload_to_request(transfer, anon_uploader, 'b', 10)
    response = _patch(client, f'{URL}/{transfer.id}', {'max_files': 1}, auth_headers)
    assert response.status_code == 400
    assert 'Already 2 files uploaded; choose at least 2.' in response.json()['detail']


@pytest.mark.parametrize('key', ['title', 'message', 'upload_audience', 'emails', 'expiry_choice'])
def test_patch_null_is_refused_for_fields_that_cannot_be_cleared(
    client, auth_headers, make_request, key
):
    response = _patch(client, f'{URL}/{make_request().id}', {key: None}, auth_headers)
    assert response.status_code == 400 and f'{key} cannot be null' in response.json()['detail']


def test_patch_expiry_date_needs_a_choice(client, auth_headers, make_request):
    response = _patch(
        client, f'{URL}/{make_request().id}', {'expiry_date': '2099-01-01'}, auth_headers
    )
    assert response.status_code == 400 and 'requires expiry_choice' in response.json()['detail']


def test_patch_an_empty_body_changes_nothing(client, auth_headers, make_request):
    transfer = make_request()
    assert _patch(client, f'{URL}/{transfer.id}', {}, auth_headers).status_code == 200


def test_patch_a_held_request_is_409(client, auth_headers, make_request):
    transfer = make_request()
    Transfer.objects.filter(pk=transfer.pk).update(held_for_review_at=timezone.now())
    response = _patch(client, f'{URL}/{transfer.id}', {'title': 'x'}, auth_headers)
    assert response.status_code == 409


def test_patch_an_ended_request_is_400(client, auth_headers, make_request):
    transfer = make_request()
    Transfer.objects.filter(pk=transfer.pk).update(status=TransferStatus.DELETED)
    assert _patch(client, f'{URL}/{transfer.id}', {'title': 'x'}, auth_headers).status_code == 400


# -- Actions --


def test_done_and_reopen(client, auth_headers, make_request, sent_emails):
    transfer = make_request()
    done = _post(client, f'{URL}/{transfer.id}/done', {}, auth_headers)
    assert done.status_code == 200
    body = done.json()
    assert body['is_open'] is False and body['done_at'] is not None
    assert body['done_by'] and sent_emails == []

    reopened = _post(client, f'{URL}/{transfer.id}/reopen', {}, auth_headers)
    assert reopened.json()['is_open'] is True and reopened.json()['done_at'] is None
    assert _post(client, f'{URL}/{transfer.id}/reopen', {}, auth_headers).status_code == 400


def test_done_twice_is_not_an_error(client, auth_headers, make_request):
    transfer = make_request()
    for _ in range(2):
        assert _post(client, f'{URL}/{transfer.id}/done', {}, auth_headers).status_code == 200


def test_disable_and_reenable(client, auth_headers, make_request):
    transfer = make_request()
    assert (
        _post(client, f'{URL}/{transfer.id}/disable', {}, auth_headers).json()['status']
        == 'disabled'
    )
    assert _post(client, f'{URL}/{transfer.id}/disable', {}, auth_headers).status_code == 400
    assert (
        _post(client, f'{URL}/{transfer.id}/reenable', {}, auth_headers).json()['status']
        == 'active'
    )


def test_reenable_needs_credits_402(client, funded_user, auth_headers, make_request):
    transfer = make_request()
    Transfer.objects.filter(pk=transfer.pk).update(status=TransferStatus.SUSPENDED)
    spend_credits(funded_user, 100, description='x', source='t')
    assert _post(client, f'{URL}/{transfer.id}/reenable', {}, auth_headers).status_code == 402


def test_reenable_by_a_blocked_owner_is_403(client, funded_user, auth_headers, make_request):
    transfer = make_request()
    Transfer.objects.filter(pk=transfer.pk).update(status=TransferStatus.DISABLED)
    BlockedSender.objects.create(kind=BlockedSenderKind.EMAIL, value=funded_user.email.lower())
    assert _post(client, f'{URL}/{transfer.id}/reenable', {}, auth_headers).status_code == 403


def test_a_held_request_refuses_reopen_with_409(client, auth_headers, make_request):
    transfer = make_request()
    services.requests.owner_mark_done(transfer, transfer.owner)
    Transfer.objects.filter(pk=transfer.pk).update(held_for_review_at=timezone.now())
    assert _post(client, f'{URL}/{transfer.id}/reopen', {}, auth_headers).status_code == 409


def test_delete_removes_the_files_now(
    client, auth_headers, make_request, upload_to_request, anon_uploader, fake_storage
):
    transfer = make_request()
    file = upload_to_request(transfer, anon_uploader, 'a', 10)
    assert client.delete(f'{URL}/{transfer.id}', **auth_headers).status_code == 204
    transfer.refresh_from_db()
    assert (
        transfer.status == TransferStatus.DELETED and file.storage_key not in fake_storage.objects
    )
    assert client.delete(f'{URL}/{transfer.id}', **auth_headers).status_code == 400


def test_delete_a_held_request_is_409(client, auth_headers, make_request):
    transfer = make_request()
    Transfer.objects.filter(pk=transfer.pk).update(held_for_review_at=timezone.now())
    assert client.delete(f'{URL}/{transfer.id}', **auth_headers).status_code == 409


def test_resend_an_invite(client, auth_headers, make_request, sent_emails):
    transfer = make_request(upload_audience='selected', emails=['ann@example.com'])
    invite = transfer.invites.get()
    url = f'{URL}/{transfer.id}/invites/{invite.id}/resend'

    # Just emailed at creation: inside the cooldown.
    response = _post(client, url, {}, auth_headers)
    assert response.status_code == 400 and 'emailed recently' in response.json()['detail']

    invite.last_sent_at = timezone.now() - timedelta(minutes=11)
    invite.save()
    assert _post(client, url, {}, auth_headers).status_code == 200
    assert [e['to'] for e in sent_emails] == ['ann@example.com']


def test_resend_is_429_over_the_email_budget(client, auth_headers, make_request, settings):
    settings.RATELIMIT_ENABLE = True
    settings.RATELIMIT_RULES = {**settings.RATELIMIT_RULES, 'FT_RECIPIENT_EMAIL_USER': (0, 3600)}
    transfer = make_request(upload_audience='selected', emails=['ann@example.com'])
    invite = transfer.invites.get()
    invite.last_sent_at = None
    invite.save()
    url = f'{URL}/{transfer.id}/invites/{invite.id}/resend'
    assert _post(client, url, {}, auth_headers).status_code == 429


# -- Files --


def test_file_url_returns_a_presigned_url_and_logs_the_download(
    client, auth_headers, make_request, upload_to_request, anon_uploader
):
    from ..models import DownloadEvent

    transfer = make_request()
    file = upload_to_request(transfer, anon_uploader, 'photo.jpg', 10)
    response = client.get(f'{URL}/{transfer.id}/files/{file.id}/url', **auth_headers)

    assert response.status_code == 200
    body = response.json()
    assert body['filename'] == 'photo.jpg' and body['expires_in'] > 0
    assert 'fake-s3' in body['url'] and file.storage_key in body['url']
    assert DownloadEvent.objects.filter(transfer=transfer, file=file).count() == 1


def test_file_url_is_available_while_open_and_after_done(
    client, auth_headers, make_request, upload_to_request, anon_uploader
):
    transfer = make_request()
    file = upload_to_request(transfer, anon_uploader, 'a', 10)
    url = f'{URL}/{transfer.id}/files/{file.id}/url'
    assert client.get(url, **auth_headers).status_code == 200
    services.requests.owner_mark_done(transfer, transfer.owner)
    assert client.get(url, **auth_headers).status_code == 200


def test_file_url_refuses_a_pending_file_a_held_request_and_an_ended_one(
    client, auth_headers, make_request, upload_to_request, anon_uploader, fake_storage
):
    from ..services import request_upload

    transfer = make_request()
    pending = request_upload.add_file(transfer, anon_uploader, 'p', 10, storage=fake_storage)
    assert (
        client.get(f'{URL}/{transfer.id}/files/{pending.id}/url', **auth_headers).status_code == 404
    )

    file = upload_to_request(transfer, anon_uploader, 'a', 10)
    url = f'{URL}/{transfer.id}/files/{file.id}/url'
    Transfer.objects.filter(pk=transfer.pk).update(held_for_review_at=timezone.now())
    assert client.get(url, **auth_headers).status_code == 409
    Transfer.objects.filter(pk=transfer.pk).update(
        held_for_review_at=None, status=TransferStatus.DELETED
    )
    assert client.get(url, **auth_headers).status_code == 400


def test_remove_a_file(
    client, auth_headers, make_request, upload_to_request, anon_uploader, fake_storage
):
    transfer = make_request()
    file = upload_to_request(transfer, anon_uploader, 'a', 10)
    assert client.delete(f'{URL}/{transfer.id}/files/{file.id}', **auth_headers).status_code == 204
    transfer.refresh_from_db()
    assert transfer.size_bytes == 0 and file.storage_key not in fake_storage.objects
    assert client.delete(f'{URL}/{transfer.id}/files/{file.id}', **auth_headers).status_code == 404


def test_a_file_of_another_request_is_a_404(
    client, auth_headers, make_request, upload_to_request, anon_uploader
):
    one, two = make_request(title='One'), make_request(title='Two')
    file = upload_to_request(one, anon_uploader, 'a', 10)
    assert client.get(f'{URL}/{two.id}/files/{file.id}/url', **auth_headers).status_code == 404
    assert client.delete(f'{URL}/{two.id}/files/{file.id}', **auth_headers).status_code == 404


def test_uploading_is_not_part_of_the_api(client, auth_headers, make_request):
    transfer = make_request()
    for path in (f'{URL}/{transfer.id}/files', f'{URL}/{transfer.id}/files/'):
        response = _post(client, path, {'name': 'a', 'size': 1}, auth_headers)
        assert response.status_code in (404, 405)


def test_the_openapi_document_lists_the_request_endpoints_and_prefixed_schemas(client):
    spec = client.get('/api/openapi.json').json()
    paths = [p for p in spec['paths'] if p.startswith('/api/ft/requests')]
    assert len(paths) == 9
    names = [n for n in spec['components']['schemas'] if n.startswith('FileRequest')]
    assert sorted(names) == [
        'FileRequestCreateSchema',
        'FileRequestFileSchema',
        'FileRequestFileUrlSchema',
        'FileRequestInviteSchema',
        'FileRequestListSchema',
        'FileRequestSchema',
        'FileRequestUpdateSchema',
    ]
