"""Fixes from the review of the file requests PR (#306): the order of the upload budgets, pending
uploads, the lock around S3 round trips, a deactivated owner, neutral errors, the sign-in
redirect, the owner's own uploads in the notice and the race-safe resend."""

import json
from datetime import timedelta

import pytest
from botocore.exceptions import ClientError
from django.core.exceptions import ValidationError
from django.db.models import QuerySet
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.core.models import Notification

from .. import services
from ..models import Transfer, TransferFile
from ..services import request_notices, request_upload
from ..services.request_uploader import Uploader
from ..services.requests import ABANDONED_EMPTY_UPLOAD_AGE, ABANDONED_UPLOAD_AGE
from .support import new_user

pytestmark = [pytest.mark.django_db, pytest.mark.unit]

PARTS = [{'PartNumber': 1, 'ETag': 'etag-1'}]


@pytest.fixture
def lock_log(monkeypatch):
    """Every `select_for_update` and every S3 completion, in the order they happen."""
    log: list[str] = []
    real = QuerySet.select_for_update

    def spy(self, *args, **kwargs):
        log.append('lock')
        return real(self, *args, **kwargs)

    monkeypatch.setattr(QuerySet, 'select_for_update', spy)
    return log


def _hook_complete(fake_storage, hook):
    """Run `hook()` just before the fake's `complete_multipart_upload` (S3 working while the
    world moves on)."""
    real = fake_storage.complete_multipart_upload

    def wrapped(key, upload_id, parts):
        hook()
        return real(key, upload_id, parts)

    fake_storage.complete_multipart_upload = wrapped


def _pending(transfer, uploader, fake_storage, size=10, name='a.bin'):
    file = request_upload.add_file(transfer, uploader, name, size, storage=fake_storage)
    fake_storage.seed_object(file.storage_key, size)
    return file


# -- Warning 1 and 2: the budgets, and pending files --


def _post_json(client, url, payload=None):
    return client.post(url, data=json.dumps(payload or {}), content_type='application/json')


def _add_url(transfer):
    return reverse('t:request-add-file', args=[transfer.slug])


def test_a_visitor_without_the_password_spends_no_per_request_budget(make_request, settings):
    from apps.core.ratelimit import limiter

    settings.RATELIMIT_ENABLE = True
    transfer = make_request(password='sekret')
    response = _post_json(Client(), _add_url(transfer), {'name': 'a', 'size': 1})
    assert response.status_code == 403
    assert limiter.peek(transfer.slug, 'FT_REQUEST_UPLOAD_TRANSFER').remaining == 3000


def test_someone_not_invited_spends_neither_the_request_nor_their_own_budget(
    make_request, settings
):
    from apps.core.ratelimit import limiter

    settings.RATELIMIT_ENABLE = True
    transfer = make_request(upload_audience='selected', emails=['ann@example.com'])
    stranger = new_user(email='stranger@example.com', email_confirmed=True)
    client = Client()
    client.force_login(stranger)
    response = _post_json(client, _add_url(transfer), {'name': 'a', 'size': 1})
    assert response.status_code == 403
    assert limiter.peek(transfer.slug, 'FT_REQUEST_UPLOAD_TRANSFER').remaining == 3000
    assert limiter.peek(str(stranger.pk), 'FT_UPLOAD_USER').remaining == 120


def test_a_refused_visitor_still_spends_the_per_ip_budget(make_request, settings):
    settings.RATELIMIT_ENABLE = True
    settings.RATELIMIT_RULES = {**settings.RATELIMIT_RULES, 'FT_REQUEST_UPLOAD_IP': (1, 60)}
    transfer = make_request(password='sekret')
    client = Client()
    assert _post_json(client, _add_url(transfer), {'name': 'a'}).status_code == 403
    assert _post_json(client, _add_url(transfer), {'name': 'a'}).status_code == 429


def test_the_default_per_request_budget_fits_a_large_batch():
    from django.conf import settings

    limit, window = settings.RATELIMIT_RULES['FT_REQUEST_UPLOAD_TRANSFER']
    assert limit >= 3000 and window == 3600


def test_the_owners_row_lists_pending_uploads_with_a_remove_button(
    client, funded_user, make_request, anon_uploader, fake_storage
):
    transfer = make_request()
    pending = request_upload.add_file(
        transfer, anon_uploader, 'stuck.bin', 10, storage=fake_storage
    )
    client.force_login(funded_user)

    response = client.get(reverse('file_transfer:dashboard') + '?kind=request')
    content = response.content.decode()

    assert 'stuck.bin' in content and 'Upload in progress' in content
    remove_url = reverse('file_transfer:request-remove-file', args=[transfer.id, pending.id])
    assert remove_url in content
    # No download link for a file that has not finished.
    assert reverse('t:download-file', args=[transfer.slug, pending.id]) not in content

    removed = client.post(remove_url)
    assert removed.status_code == 302 or removed.status_code == 200
    assert not TransferFile.objects.filter(pk=pending.pk).exists()
    assert pending.upload_id in fake_storage.aborted_uploads


def test_an_upload_with_no_part_yet_is_abandoned_after_two_hours(
    make_request, anon_uploader, fake_storage
):
    assert ABANDONED_EMPTY_UPLOAD_AGE == timedelta(hours=2)
    transfer = make_request()
    empty = request_upload.add_file(transfer, anon_uploader, 'empty.bin', 10, storage=fake_storage)
    fresh = request_upload.add_file(transfer, anon_uploader, 'fresh.bin', 10, storage=fake_storage)
    TransferFile.objects.filter(pk=empty.pk).update(
        created_at=timezone.now() - ABANDONED_EMPTY_UPLOAD_AGE - timedelta(minutes=1)
    )

    assert services.requests.cleanup_abandoned_files(storage=fake_storage) == 1

    assert list(transfer.files.values_list('name', flat=True)) == ['fresh.bin']
    assert empty.upload_id in fake_storage.aborted_uploads
    assert fresh.upload_id in fake_storage.active_uploads


def test_an_upload_that_has_parts_is_kept_until_a_day_old(
    make_request, anon_uploader, fake_storage
):
    transfer = make_request()
    started = request_upload.add_file(transfer, anon_uploader, 'big.bin', 10, storage=fake_storage)
    fake_storage.upload_part(started.storage_key, started.upload_id, 1, b'x' * 10)
    TransferFile.objects.filter(pk=started.pk).update(
        created_at=timezone.now() - timedelta(hours=5)
    )
    assert services.requests.cleanup_abandoned_files(storage=fake_storage) == 0
    assert transfer.files.count() == 1

    TransferFile.objects.filter(pk=started.pk).update(
        created_at=timezone.now() - ABANDONED_UPLOAD_AGE - timedelta(minutes=1)
    )
    assert services.requests.cleanup_abandoned_files(storage=fake_storage) == 1
    assert transfer.files.count() == 0


def test_an_upload_s3_no_longer_knows_counts_as_having_no_parts(
    make_request, anon_uploader, fake_storage
):
    transfer = make_request()
    file = request_upload.add_file(transfer, anon_uploader, 'gone.bin', 10, storage=fake_storage)
    fake_storage.active_uploads.discard(file.upload_id)  # S3 aborted it (NoSuchUpload)
    TransferFile.objects.filter(pk=file.pk).update(created_at=timezone.now() - timedelta(hours=3))
    assert services.requests.cleanup_abandoned_files(storage=fake_storage) == 1


def test_an_upload_whose_parts_cannot_be_listed_is_left_for_the_next_run(
    make_request, anon_uploader, fake_storage, monkeypatch
):
    transfer = make_request()
    file = request_upload.add_file(transfer, anon_uploader, 'x.bin', 10, storage=fake_storage)
    TransferFile.objects.filter(pk=file.pk).update(created_at=timezone.now() - timedelta(hours=3))

    def broken(key, upload_id):
        raise ClientError({'Error': {'Code': 'InternalError', 'Message': 'boom'}}, 'ListParts')

    monkeypatch.setattr(fake_storage, 'list_parts', broken)
    assert services.requests.cleanup_abandoned_files(storage=fake_storage) == 0
    assert transfer.files.count() == 1


# -- Warning 3: removing acts on a fresh, locked row --


def test_an_uploader_cannot_remove_after_done_even_with_a_stale_request(
    make_request, anon_uploader, upload_to_request, fake_storage
):
    transfer = make_request()
    mine = upload_to_request(transfer, anon_uploader, 'mine.txt', 10)
    stale = Transfer.objects.get(pk=transfer.pk)  # read while the request is still open
    Transfer.objects.filter(pk=transfer.pk).update(done_at=timezone.now())

    with pytest.raises(ValidationError, match='not accepting files'):
        request_upload.remove_own_file(stale, mine, storage=fake_storage)
    assert TransferFile.objects.filter(pk=mine.pk).exists()
    assert mine.storage_key in fake_storage.objects


def test_removing_a_stale_file_that_has_since_finished_deletes_the_object_not_the_upload(
    make_request, anon_uploader, fake_storage
):
    transfer = make_request()
    pending = _pending(transfer, anon_uploader, fake_storage)
    stale = TransferFile.objects.get(pk=pending.pk)  # still says: unfinished, with an upload id
    request_upload.complete_file(transfer, pending, PARTS, anon_uploader, storage=fake_storage)
    assert stale.storage_key in fake_storage.objects

    request_upload.remove_own_file(transfer, stale, storage=fake_storage)

    assert stale.storage_key not in fake_storage.objects
    assert stale.upload_id not in fake_storage.aborted_uploads
    assert not TransferFile.objects.filter(pk=stale.pk).exists()
    transfer.refresh_from_db()
    assert transfer.size_bytes == 0


def test_removing_a_file_that_is_already_gone_is_a_no_op(
    make_request, anon_uploader, upload_to_request, fake_storage
):
    transfer = make_request()
    file = upload_to_request(transfer, anon_uploader, 'a.txt', 10)
    request_upload.remove_own_file(transfer, file, storage=fake_storage)
    request_upload.remove_own_file(transfer, file, storage=fake_storage)


def test_a_failing_s3_delete_still_removes_the_row(
    make_request, anon_uploader, upload_to_request, fake_storage, monkeypatch
):
    transfer = make_request()
    file = upload_to_request(transfer, anon_uploader, 'a.txt', 10)

    def boom(key):
        raise ClientError({'Error': {'Code': 'InternalError', 'Message': 'x'}}, 'DeleteObject')

    monkeypatch.setattr(fake_storage, 'delete_object', boom)
    request_upload.remove_own_file(transfer, file, storage=fake_storage)
    assert not TransferFile.objects.filter(pk=file.pk).exists()
    transfer.refresh_from_db()
    assert transfer.size_bytes == 0


# -- Warning 4: S3 outside the lock --


def test_completing_does_its_s3_work_before_taking_the_lock(
    make_request, anon_uploader, fake_storage, lock_log
):
    transfer = make_request()
    file = _pending(transfer, anon_uploader, fake_storage)
    lock_log.clear()
    _hook_complete(fake_storage, lambda: lock_log.append('s3 complete'))
    real_head = fake_storage.head_object

    def head(key):
        lock_log.append('s3 head')
        return real_head(key)

    fake_storage.head_object = head

    request_upload.complete_file(transfer, file, PARTS, anon_uploader, storage=fake_storage)

    assert lock_log == ['s3 complete', 's3 head', 'lock']


def test_removing_does_its_s3_work_after_the_lock_is_released(
    make_request, anon_uploader, fake_storage, lock_log
):
    transfer = make_request()
    pending = _pending(transfer, anon_uploader, fake_storage)
    lock_log.clear()
    real_abort = fake_storage.abort_multipart_upload

    def abort(key, upload_id):
        lock_log.append('s3 abort')
        return real_abort(key, upload_id)

    fake_storage.abort_multipart_upload = abort

    request_upload.remove_own_file(transfer, pending, storage=fake_storage)

    assert lock_log == ['lock', 's3 abort']


def test_a_file_that_finishes_in_s3_after_the_request_is_done_is_deleted(
    make_request, anon_uploader, upload_to_request, fake_storage
):
    transfer = make_request()
    upload_to_request(transfer, anon_uploader, 'first.txt', 10)
    late = _pending(transfer, anon_uploader, fake_storage, name='late.bin')
    _hook_complete(
        fake_storage,
        lambda: services.requests.mark_done(
            Transfer.objects.get(pk=transfer.pk), by_name='Jane', storage=fake_storage
        ),
    )

    with pytest.raises(ValidationError, match='not accepting files'):
        request_upload.complete_file(transfer, late, PARTS, anon_uploader, storage=fake_storage)

    assert late.storage_key not in fake_storage.objects
    assert not TransferFile.objects.filter(pk=late.pk).exists()
    transfer.refresh_from_db()
    assert transfer.size_bytes == 10 and transfer.is_done


def test_a_file_closed_over_by_a_disable_is_deleted_too(make_request, anon_uploader, fake_storage):
    transfer = make_request()
    file = _pending(transfer, anon_uploader, fake_storage)
    _hook_complete(
        fake_storage,
        lambda: Transfer.objects.filter(pk=transfer.pk).update(status='disabled'),
    )
    with pytest.raises(ValidationError, match='not accepting files'):
        request_upload.complete_file(transfer, file, PARTS, anon_uploader, storage=fake_storage)
    assert file.storage_key not in fake_storage.objects
    assert not TransferFile.objects.filter(pk=file.pk).exists()


def test_a_file_the_owner_removed_while_s3_worked_is_deleted_from_s3(
    make_request, anon_uploader, fake_storage
):
    transfer = make_request()
    file = _pending(transfer, anon_uploader, fake_storage)
    _hook_complete(fake_storage, lambda: TransferFile.objects.filter(pk=file.pk).delete())

    with pytest.raises(ValidationError, match='no longer being uploaded'):
        request_upload.complete_file(transfer, file, PARTS, anon_uploader, storage=fake_storage)
    assert file.storage_key not in fake_storage.objects


def test_a_size_mismatch_keeps_the_cleared_upload_and_resume_starts_over(
    make_request, anon_uploader, fake_storage
):
    transfer = make_request()
    file = request_upload.add_file(transfer, anon_uploader, 'a.bin', 10, storage=fake_storage)
    fake_storage.seed_object(file.storage_key, 7)  # not the declared 10 bytes

    with pytest.raises(ValidationError, match='does not match the declared size'):
        request_upload.complete_file(transfer, file, PARTS, anon_uploader, storage=fake_storage)

    file.refresh_from_db()
    assert file.upload_id == '' and not file.uploaded
    assert file.storage_key not in fake_storage.objects  # the odd object was deleted

    resumed, parts, restarted = request_upload.resume_file(transfer, file, storage=fake_storage)
    assert restarted and parts == []
    assert resumed.upload_id and resumed.upload_id in fake_storage.active_uploads
    file.refresh_from_db()
    assert file.upload_id == resumed.upload_id


def test_a_failed_completion_shows_a_neutral_message_and_logs_the_detail(
    make_request, anon_uploader, fake_storage, caplog
):
    transfer = make_request()
    file = _pending(transfer, anon_uploader, fake_storage)

    def boom(key, upload_id, parts):
        raise ClientError(
            {'Error': {'Code': 'InvalidPart', 'Message': 'secret-bucket-name part 1'}},
            'CompleteMultipartUpload',
        )

    fake_storage.complete_multipart_upload = boom
    with caplog.at_level('WARNING'), pytest.raises(ValidationError) as excinfo:
        request_upload.complete_file(transfer, file, PARTS, anon_uploader, storage=fake_storage)

    assert excinfo.value.messages == ['Could not complete the upload. Please try again.']
    assert 'secret-bucket-name' not in str(excinfo.value)
    assert 'secret-bucket-name' in caplog.text


# -- Warning 5: charging before the lock --


def test_new_invitees_are_charged_before_the_request_is_locked(make_request, lock_log):
    transfer = make_request(upload_audience='selected', emails=['a@x.org'])
    lock_log.clear()

    services.requests.update_request(
        transfer,
        {'emails': ['a@x.org', 'b@x.org']},
        charge=lambda count: lock_log.append(f'charge {count}'),
    )

    assert lock_log == ['charge 1', 'lock']


def test_invitees_a_concurrent_edit_removed_are_charged_for_under_the_lock(make_request):
    transfer = make_request(upload_audience='selected', emails=['a@x.org'])
    charged: list[int] = []

    def charge(count):
        charged.append(count)
        if len(charged) == 1:  # someone removes a@x.org after the first plan
            transfer.invites.all().delete()

    services.requests.update_request(transfer, {'emails': ['a@x.org', 'b@x.org']}, charge=charge)

    assert charged == [1, 1]
    assert sorted(i.email for i in transfer.invites.all()) == ['a@x.org', 'b@x.org']


def test_a_refused_field_spends_no_budget_before_the_lock(make_request):
    transfer = make_request(upload_audience='selected', emails=['a@x.org'])
    charged: list[int] = []
    with pytest.raises(ValidationError):
        services.requests.update_request(
            transfer,
            {'emails': ['a@x.org', 'b@x.org'], 'max_files': 0},
            charge=charged.append,
        )
    assert charged == []


# -- Suggestions --


def test_a_deactivated_owner_takes_no_uploads(
    funded_user, make_request, anon_uploader, fake_storage
):
    transfer = make_request()
    funded_user.status = User.STATUS_INACTIVE
    funded_user.save()
    with pytest.raises(ValidationError, match='not accepting files'):
        request_upload.add_file(
            Transfer.objects.get(pk=transfer.pk), anon_uploader, 'a', 10, storage=fake_storage
        )

    funded_user.status = User.STATUS_ACTIVE
    funded_user.is_active = False
    funded_user.save()
    with pytest.raises(ValidationError, match='not accepting files'):
        request_upload.add_file(
            Transfer.objects.get(pk=transfer.pk), anon_uploader, 'a', 10, storage=fake_storage
        )


def test_the_endpoint_refuses_uploads_to_a_deactivated_owners_request(funded_user, make_request):
    transfer = make_request()
    funded_user.status = User.STATUS_INACTIVE
    funded_user.save()
    response = _post_json(Client(), _add_url(transfer), {'name': 'a', 'size': 1})
    assert response.status_code == 422
    assert response.json()['error'] == 'This link is not accepting files right now.'


def test_sign_in_redirects_from_post_only_urls_go_back_to_the_page(make_request):
    transfer = make_request(upload_audience='signed_in')
    page = reverse('t:download', args=[transfer.slug])
    done = Client().post(reverse('t:request-done', args=[transfer.slug]))
    assert done.status_code == 302
    assert done['Location'] == f'{reverse("accounts:login")}?next={page}'


def test_the_notice_does_not_count_the_owners_own_files(
    funded_user, make_request, anon_uploader, upload_to_request
):
    transfer = make_request()
    owner = Uploader(user=funded_user, token_hash='', name='me', email=funded_user.email)
    upload_to_request(transfer, anon_uploader, 'guest.txt', 10)
    upload_to_request(transfer, owner, 'mine1.txt', 10)
    upload_to_request(transfer, owner, 'mine2.txt', 10)

    request_notices.send_due_notices(now=timezone.now() + timedelta(hours=1))

    notice = Notification.objects.get(user=funded_user)
    assert notice.title == '1 new file in "Wedding photos"'


def test_two_quick_resends_email_the_invitee_once(make_request, sent_emails):
    transfer = make_request(upload_audience='selected', emails=['ann@example.com'])
    invite = transfer.invites.get()
    invite.last_sent_at = None
    invite.save()
    other_click = transfer.invites.get()

    # The first click is between the cooldown check and its write when the second one lands.
    def second_click_wins(count):
        services.request_invites.resend_invite(other_click)

    with pytest.raises(ValidationError, match='you can resend in'):
        services.request_invites.resend_invite(invite, charge=second_click_wins)

    assert [mail['to'] for mail in sent_emails] == ['ann@example.com']
