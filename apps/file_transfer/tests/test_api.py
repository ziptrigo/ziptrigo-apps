"""The file_transfer JWT API (`/api/ft/`, spec section 14), as used by external clients (the
`admin/filetransfer.py` CLI, and anyone else who authenticates with a JWT rather than a session).
Exercises the same services the web send/dashboard flow does, just through
`/api/ft/transfers/...` instead of session-authenticated HTMX views -- see `apps/file_transfer/api/`.
"""

import base64
import hashlib
import json

import pytest

from apps.accounts.models import User
from apps.accounts.tokens import CustomAccessToken
from apps.billing.services import get_balance, spend_credits

from ..models import TransferFile, TransferStatus

pytestmark = [pytest.mark.django_db, pytest.mark.unit]

_CHECKSUM = base64.b64encode(b'\x00' * 32).decode()


def _part_checksum(data: bytes) -> str:
    """What `FakeS3Storage.list_parts` reports for a part recorded with these bytes -- see its
    docstring."""
    return base64.b64encode(hashlib.sha256(data).digest()).decode()


@pytest.fixture
def auth_headers(funded_user) -> dict[str, str]:
    return {'HTTP_AUTHORIZATION': f'Bearer {CustomAccessToken.for_user(funded_user)}'}


def _post(client, url, payload, headers):
    return client.post(url, data=json.dumps(payload), content_type='application/json', **headers)


def _patch(client, url, payload, headers):
    return client.patch(url, data=json.dumps(payload), content_type='application/json', **headers)


# -- Auth --


def test_requires_a_token(client):
    assert client.get('/api/ft/transfers/').status_code == 401


def test_rejects_an_inactive_user(client, funded_user):
    funded_user.status = User.STATUS_INACTIVE
    funded_user.save()
    headers = {'HTTP_AUTHORIZATION': f'Bearer {CustomAccessToken.for_user(funded_user)}'}

    response = client.get('/api/ft/transfers/', **headers)

    assert response.status_code == 401


# -- Full happy path: create -> add file -> part URLs -> complete -> finalize --


def test_full_send_flow(client, funded_user, auth_headers, fake_storage):
    create_resp = _post(client, '/api/ft/transfers/', {}, auth_headers)
    assert create_resp.status_code == 201
    transfer_id = create_resp.json()['id']
    assert create_resp.json()['status'] == 'draft'

    add_resp = _post(
        client,
        f'/api/ft/transfers/{transfer_id}/files/',
        {'name': 'report.pdf', 'size': 10, 'client_last_modified': 1700000000000},
        auth_headers,
    )
    assert add_resp.status_code == 201
    added = add_resp.json()
    file_id = added['file_id']
    assert added['part_count'] == 1
    file = TransferFile.objects.get(id=file_id)
    assert file.client_last_modified == 1700000000000

    parts_resp = _post(
        client,
        f'/api/ft/transfers/{transfer_id}/files/{file_id}/parts/',
        {'parts': [{'part_number': 1, 'checksum_sha256': _CHECKSUM}]},
        auth_headers,
    )
    assert parts_resp.status_code == 200
    assert '1' in parts_resp.json()['urls']

    fake_storage.put_object(file.storage_key, 10)
    complete_resp = _post(
        client,
        f'/api/ft/transfers/{transfer_id}/files/{file_id}/complete/',
        {'parts': [{'part_number': 1, 'etag': 'e1'}]},
        auth_headers,
    )
    assert complete_resp.status_code == 200
    file.refresh_from_db()
    assert file.uploaded is True

    send_resp = _post(
        client,
        f'/api/ft/transfers/{transfer_id}/send',
        {'recipients': ['friend@example.com'], 'message': 'hi', 'expiry_choice': 'none'},
        auth_headers,
    )
    assert send_resp.status_code == 200
    body = send_resp.json()
    assert body['status'] == 'active'
    assert [r['email'] for r in body['recipients']] == ['friend@example.com']
    assert body['display_name'] == 'report.pdf'

    get_resp = client.get(f'/api/ft/transfers/{transfer_id}', **auth_headers)
    assert get_resp.status_code == 200
    assert get_resp.json()['status'] == 'active'


def test_get_transfer_includes_a_draft_and_its_files(
    client, draft_transfer, uploaded_file, auth_headers
):
    """Unlike the list endpoint, `GET /transfers/{id}` on a known id must return a draft too --
    `admin/filetransfer.py send --draft-id` resumes an interrupted send by fetching it."""
    response = client.get(f'/api/ft/transfers/{draft_transfer.id}', **auth_headers)

    assert response.status_code == 200
    body = response.json()
    assert body['status'] == 'draft'
    assert body['files'] == [
        {
            'id': str(uploaded_file.id),
            'name': uploaded_file.name,
            'size': uploaded_file.size,
            'uploaded': True,
            'client_last_modified': None,
        }
    ]


def test_finalize_validation_error_returns_400(client, draft_transfer, uploaded_file, auth_headers):
    response = _post(
        client,
        f'/api/ft/transfers/{draft_transfer.id}/send',
        {'recipients': [], 'expiry_choice': 'none'},
        auth_headers,
    )
    assert response.status_code == 400
    assert response.json()['detail']


def test_finalize_insufficient_credits_returns_402(
    client, draft_transfer, uploaded_file, auth_headers
):
    spend_credits(draft_transfer.owner, get_balance(draft_transfer.owner), source='test')

    response = _post(
        client,
        f'/api/ft/transfers/{draft_transfer.id}/send',
        {'recipients': ['a@example.com'], 'expiry_choice': 'none'},
        auth_headers,
    )

    assert response.status_code == 402
    draft_transfer.refresh_from_db()
    assert draft_transfer.status == TransferStatus.DRAFT


def test_add_file_rejects_invalid_size(client, draft_transfer, auth_headers, fake_storage):
    response = _post(
        client,
        f'/api/ft/transfers/{draft_transfer.id}/files/',
        {'name': 'a.bin', 'size': -1},
        auth_headers,
    )
    assert response.status_code == 400


def test_resume_reports_uploaded_parts(client, draft_transfer, auth_headers, fake_storage):
    added = _post(
        client,
        f'/api/ft/transfers/{draft_transfer.id}/files/',
        {'name': 'a.bin', 'size': 200 * 1024 * 1024},
        auth_headers,
    ).json()
    file = TransferFile.objects.get(id=added['file_id'])
    fake_storage.upload_part(file.storage_key, file.upload_id, 1, b'\0' * 10)

    response = _post(
        client, f'/api/ft/transfers/{draft_transfer.id}/files/{file.id}/resume/', {}, auth_headers
    )

    assert response.status_code == 200
    body = response.json()
    assert body['restarted'] is False
    assert body['uploaded_parts'] == [
        {
            'part_number': 1,
            'etag': f'etag-{file.upload_id}-1',
            'size': 10,
            'checksum_sha256': _part_checksum(b'\0' * 10),
        }
    ]


def test_resume_then_complete_round_trips_the_checksum(
    client, draft_transfer, auth_headers, fake_storage
):
    """End-to-end through `/api/ft/` (issue #55 phase 3 review's critical fix): a part already
    landed in S3 is reported by `.../resume/` with its checksum, and completing the upload with
    that checksum carried straight through (exactly what `admin/filetransfer.py`'s `_upload_file`
    now does) succeeds."""
    added = _post(
        client,
        f'/api/ft/transfers/{draft_transfer.id}/files/',
        {'name': 'a.bin', 'size': 10},
        auth_headers,
    ).json()
    file = TransferFile.objects.get(id=added['file_id'])
    fake_storage.upload_part(file.storage_key, file.upload_id, 1, b'\0' * 10)

    resumed = _post(
        client, f'/api/ft/transfers/{draft_transfer.id}/files/{file.id}/resume/', {}, auth_headers
    ).json()
    part = resumed['uploaded_parts'][0]
    assert part['checksum_sha256']

    complete_resp = _post(
        client,
        f'/api/ft/transfers/{draft_transfer.id}/files/{file.id}/complete/',
        {
            'parts': [
                {
                    'part_number': part['part_number'],
                    'etag': part['etag'],
                    'checksum_sha256': part['checksum_sha256'],
                }
            ]
        },
        auth_headers,
    )

    assert complete_resp.status_code == 200
    file.refresh_from_db()
    assert file.uploaded is True


def test_resume_restarts_an_expired_upload(client, draft_transfer, auth_headers, fake_storage):
    added = _post(
        client,
        f'/api/ft/transfers/{draft_transfer.id}/files/',
        {'name': 'a.bin', 'size': 10},
        auth_headers,
    ).json()
    file = TransferFile.objects.get(id=added['file_id'])
    old_upload_id = file.upload_id
    fake_storage.abort_multipart_upload(file.storage_key, old_upload_id)

    response = _post(
        client, f'/api/ft/transfers/{draft_transfer.id}/files/{file.id}/resume/', {}, auth_headers
    )

    assert response.status_code == 200
    assert response.json()['restarted'] is True
    file.refresh_from_db()
    assert file.upload_id != old_upload_id


def test_remove_file(client, draft_transfer, auth_headers, fake_storage):
    added = _post(
        client,
        f'/api/ft/transfers/{draft_transfer.id}/files/',
        {'name': 'a.bin', 'size': 10},
        auth_headers,
    ).json()

    response = client.delete(
        f'/api/ft/transfers/{draft_transfer.id}/files/{added["file_id"]}', **auth_headers
    )

    assert response.status_code == 204
    assert not TransferFile.objects.filter(id=added['file_id']).exists()


# -- Ownership: another user's transfer is a 404, never a 403 (doesn't confirm it exists) --


def test_other_users_transfer_is_404(client, draft_transfer, auth_headers, regular_user):
    other_headers = {'HTTP_AUTHORIZATION': f'Bearer {CustomAccessToken.for_user(regular_user)}'}

    response = client.get(f'/api/ft/transfers/{draft_transfer.id}', **other_headers)

    assert response.status_code == 404


def test_add_file_to_other_users_draft_is_404(client, draft_transfer, auth_headers, regular_user):
    other_headers = {'HTTP_AUTHORIZATION': f'Bearer {CustomAccessToken.for_user(regular_user)}'}

    response = _post(
        client,
        f'/api/ft/transfers/{draft_transfer.id}/files/',
        {'name': 'a.bin', 'size': 10},
        other_headers,
    )

    assert response.status_code == 404


# -- List: filter + pagination --


def test_list_filters_active_vs_ended(client, draft_transfer, uploaded_file, auth_headers):
    from ..services.send import SendOptions, finalize_send

    finalize_send(draft_transfer, SendOptions(recipients=['a@example.com']))

    active_resp = client.get('/api/ft/transfers/?filter=active', **auth_headers)
    assert active_resp.status_code == 200
    assert active_resp.json()['count'] == 1
    assert active_resp.json()['results'][0]['id'] == str(draft_transfer.id)

    ended_resp = client.get('/api/ft/transfers/?filter=ended', **auth_headers)
    assert ended_resp.json()['count'] == 0


def test_list_excludes_drafts_by_default(client, draft_transfer, auth_headers):
    response = client.get('/api/ft/transfers/', **auth_headers)
    assert response.json()['count'] == 0


def test_list_unknown_filter_returns_400(client, draft_transfer, auth_headers):
    response = client.get('/api/ft/transfers/?filter=bogus', **auth_headers)
    assert response.status_code == 400


def test_create_transfer_reuses_an_existing_empty_draft(client, funded_user, auth_headers):
    """Mirrors the web send page (`services.get_or_create_draft`): calling this more than once
    without adding a file must not litter the account with empty draft rows (issue #55 phase 3
    review)."""
    first = _post(client, '/api/ft/transfers/', {}, auth_headers)
    second = _post(client, '/api/ft/transfers/', {}, auth_headers)

    assert first.json()['id'] == second.json()['id']


def test_list_pagination(client, funded_user, auth_headers, fake_storage):
    from ..services.send import SendOptions, finalize_send
    from ..services.uploads import add_file, complete_file_upload, create_draft

    for i in range(3):
        transfer = create_draft(funded_user)
        file = add_file(transfer, f'f{i}.bin', 10, storage=fake_storage)
        fake_storage.put_object(file.storage_key, 10)
        complete_file_upload(file, [{'PartNumber': 1, 'ETag': 'e'}], storage=fake_storage)
        finalize_send(transfer, SendOptions(recipients=['a@example.com']))

    page1 = client.get('/api/ft/transfers/?limit=2&offset=0', **auth_headers).json()
    page2 = client.get('/api/ft/transfers/?limit=2&offset=2', **auth_headers).json()

    assert page1['count'] == 3
    assert len(page1['results']) == 2
    assert len(page2['results']) == 1


# -- Update actions --


def test_update_disable_and_reenable(client, draft_transfer, uploaded_file, auth_headers):
    from ..services.send import SendOptions, finalize_send

    finalize_send(draft_transfer, SendOptions(recipients=['a@example.com']))

    disable_resp = _patch(
        client, f'/api/ft/transfers/{draft_transfer.id}', {'disabled': True}, auth_headers
    )
    assert disable_resp.status_code == 200
    assert disable_resp.json()['status'] == 'disabled'

    reenable_resp = _patch(
        client, f'/api/ft/transfers/{draft_transfer.id}', {'disabled': False}, auth_headers
    )
    assert reenable_resp.status_code == 200
    assert reenable_resp.json()['status'] == 'active'


def test_update_expiry_max_downloads_password_and_notify(
    client, draft_transfer, uploaded_file, auth_headers
):
    from ..services.send import SendOptions, finalize_send

    finalize_send(draft_transfer, SendOptions(recipients=['a@example.com']))

    response = _patch(
        client,
        f'/api/ft/transfers/{draft_transfer.id}',
        {
            'expiry_choice': '5',
            'max_downloads': 3,
            'password': 'sekret',
            'notify_on_download': False,
        },
        auth_headers,
    )

    assert response.status_code == 200
    body = response.json()
    assert body['max_downloads'] == 3
    assert body['has_password'] is True
    assert body['notify_on_download'] is False
    assert body['expires_at'] is not None


def test_update_remove_password(client, draft_transfer, uploaded_file, auth_headers):
    from ..services import actions
    from ..services.send import SendOptions, finalize_send

    finalize_send(draft_transfer, SendOptions(recipients=['a@example.com']))
    actions.set_password(draft_transfer, 'sekret')

    response = _patch(
        client,
        f'/api/ft/transfers/{draft_transfer.id}',
        {'remove_password': True},
        auth_headers,
    )

    assert response.status_code == 200
    assert response.json()['has_password'] is False


def test_update_validation_error_returns_400(client, draft_transfer, uploaded_file, auth_headers):
    from ..services.send import SendOptions, finalize_send

    finalize_send(draft_transfer, SendOptions(recipients=['a@example.com']))

    response = _patch(
        client, f'/api/ft/transfers/{draft_transfer.id}', {'max_downloads': 0}, auth_headers
    )

    assert response.status_code == 400


def test_update_is_atomic_a_later_field_failing_rolls_back_an_earlier_one(
    client, draft_transfer, uploaded_file, auth_headers
):
    """`expiry_choice` (applied first) would otherwise succeed and commit even though the same
    request's `max_downloads` (applied after) is invalid -- a partial update from one PATCH call
    is worse than rejecting it outright."""
    from ..services.send import SendOptions, finalize_send

    finalize_send(draft_transfer, SendOptions(recipients=['a@example.com']))
    original_expires_at = draft_transfer.expires_at

    response = _patch(
        client,
        f'/api/ft/transfers/{draft_transfer.id}',
        {'expiry_choice': '5', 'max_downloads': 0},
        auth_headers,
    )

    assert response.status_code == 400
    draft_transfer.refresh_from_db()
    assert draft_transfer.expires_at == original_expires_at


def test_update_expiry_date_without_expiry_choice_returns_400(
    client, draft_transfer, uploaded_file, auth_headers
):
    from ..services.send import SendOptions, finalize_send

    finalize_send(draft_transfer, SendOptions(recipients=['a@example.com']))

    response = _patch(
        client,
        f'/api/ft/transfers/{draft_transfer.id}',
        {'expiry_date': '2026-12-31T00:00:00Z'},
        auth_headers,
    )

    assert response.status_code == 400


def test_update_omitted_fields_are_left_unchanged(
    client, draft_transfer, uploaded_file, auth_headers
):
    from ..services.send import SendOptions, finalize_send

    finalize_send(draft_transfer, SendOptions(recipients=['a@example.com'], max_downloads=5))

    response = _patch(
        client,
        f'/api/ft/transfers/{draft_transfer.id}',
        {'notify_on_download': False},
        auth_headers,
    )

    assert response.status_code == 200
    assert response.json()['max_downloads'] == 5


# -- Recipients --


def test_add_recipients_and_resend(client, draft_transfer, uploaded_file, auth_headers):
    from ..services.send import SendOptions, finalize_send

    finalize_send(draft_transfer, SendOptions(recipients=['a@example.com']))

    add_resp = _post(
        client,
        f'/api/ft/transfers/{draft_transfer.id}/recipients',
        {'recipients': ['b@example.com']},
        auth_headers,
    )
    assert add_resp.status_code == 200
    emails = [r['email'] for r in add_resp.json()['recipients']]
    assert sorted(emails) == ['a@example.com', 'b@example.com']

    from ..models import TransferRecipient

    recipient = TransferRecipient.objects.get(transfer=draft_transfer, email='b@example.com')
    resend_resp = _post(
        client,
        f'/api/ft/transfers/{draft_transfer.id}/recipients/{recipient.id}/resend',
        {},
        auth_headers,
    )
    assert resend_resp.status_code == 200


# -- Delete --


def test_delete_draft_aborts_it(client, draft_transfer, uploaded_file, auth_headers):
    response = client.delete(f'/api/ft/transfers/{draft_transfer.id}', **auth_headers)

    assert response.status_code == 204
    draft_transfer.refresh_from_db()
    assert draft_transfer.status == TransferStatus.DELETED


def test_delete_active_transfer(client, draft_transfer, uploaded_file, auth_headers):
    from ..services.send import SendOptions, finalize_send

    finalize_send(draft_transfer, SendOptions(recipients=['a@example.com']))

    response = client.delete(f'/api/ft/transfers/{draft_transfer.id}', **auth_headers)

    assert response.status_code == 204
    draft_transfer.refresh_from_db()
    assert draft_transfer.status == TransferStatus.DELETED


# -- Rate limiting (issue #53): same rule as the web upload endpoints, per user --


def test_add_file_429_after_rate_limit(
    client, draft_transfer, auth_headers, fake_storage, settings
):
    settings.RATELIMIT_ENABLE = True
    settings.RATELIMIT_RULES = {**settings.RATELIMIT_RULES, 'FT_UPLOAD_USER': (1, 60)}

    _post(
        client,
        f'/api/ft/transfers/{draft_transfer.id}/files/',
        {'name': 'a.bin', 'size': 10},
        auth_headers,
    )
    response = _post(
        client,
        f'/api/ft/transfers/{draft_transfer.id}/files/',
        {'name': 'b.bin', 'size': 10},
        auth_headers,
    )

    assert response.status_code == 429
    assert 'Retry-After' in response
