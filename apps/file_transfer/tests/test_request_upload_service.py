"""Uploading to a file request (issue #306): limits under the lock, who sees which files, the
block list, and what finishing or removing a file does (`services.request_upload`)."""

from datetime import timedelta

import pytest
from django.core.exceptions import ValidationError
from django.utils import timezone

from apps.accounts.tests.factories import UserFactory

from .. import services
from ..models import BlockedSender, BlockedSenderKind, Transfer, TransferFile, TransferStatus
from ..services import request_upload
from ..services.blocklist import BlockedSenderError
from ..services.request_uploader import Uploader, clean_name, identify, own_files, remember_name

pytestmark = [pytest.mark.django_db, pytest.mark.unit]


def test_adding_a_file_records_who_uploaded_it(
    make_request, anon_uploader, user_uploader, fake_storage
):
    transfer = make_request()
    anonymous = request_upload.add_file(
        transfer,
        anon_uploader,
        'a.txt',
        10,
        ip='1.2.3.4',
        client_last_modified=5,
        storage=fake_storage,
    )
    signed_in = request_upload.add_file(
        transfer, user_uploader, 'b.txt', 20, ip='5.6.7.8', storage=fake_storage
    )

    assert anonymous.uploader_user is None
    assert anonymous.uploader_token_hash == anon_uploader.token_hash
    assert anonymous.uploader_name == 'Jane' and anonymous.uploader_email == ''
    assert anonymous.uploader_ip == '1.2.3.4' and anonymous.client_last_modified == 5
    assert anonymous.uploaded is False and anonymous.upload_id

    assert signed_in.uploader_user == user_uploader.user
    assert signed_in.uploader_name == 'John' and signed_in.uploader_email == 'john@example.com'
    assert signed_in.uploader_token_hash == ''


def test_a_finished_upload_is_stamped_and_counted(make_request, anon_uploader, upload_to_request):
    transfer = make_request()
    file = upload_to_request(transfer, anon_uploader, 'a.txt', 100)
    transfer.refresh_from_db()

    assert file.uploaded and file.uploaded_at is not None
    assert transfer.size_bytes == 100
    upload_to_request(transfer, anon_uploader, 'b.txt', 50)
    transfer.refresh_from_db()
    assert transfer.size_bytes == 150


def test_size_bytes_counts_only_uploaded_files(make_request, anon_uploader, fake_storage):
    transfer = make_request()
    request_upload.add_file(transfer, anon_uploader, 'pending.bin', 999, storage=fake_storage)
    transfer.refresh_from_db()
    assert transfer.size_bytes == 0


@pytest.mark.parametrize(
    ('size', 'message'),
    [(0, 'File is empty.'), (6 * 1024**3, 'File is larger than the 5.0 GB limit per file.')],
)
def test_the_per_file_limit_is_the_sites(make_request, anon_uploader, fake_storage, size, message):
    with pytest.raises(ValidationError, match=message):
        request_upload.add_file(make_request(), anon_uploader, 'x', size, storage=fake_storage)


def test_max_files_counts_every_uploaders_files_finished_or_not(
    make_request, anon_uploader, user_uploader, fake_storage
):
    transfer = make_request(max_files=2)
    request_upload.add_file(transfer, anon_uploader, 'a', 10, storage=fake_storage)
    request_upload.add_file(transfer, user_uploader, 'b', 10, storage=fake_storage)

    with pytest.raises(ValidationError, match='This request accepts at most 2 files.'):
        request_upload.add_file(transfer, anon_uploader, 'c', 10, storage=fake_storage)


def test_max_files_message_is_singular_for_one(make_request, anon_uploader, fake_storage):
    transfer = make_request(max_files=1)
    request_upload.add_file(transfer, anon_uploader, 'a', 10, storage=fake_storage)
    with pytest.raises(ValidationError, match='at most 1 file\\.'):
        request_upload.add_file(transfer, anon_uploader, 'b', 10, storage=fake_storage)


def test_max_total_counts_every_file_in_progress_too(
    make_request, anon_uploader, user_uploader, fake_storage
):
    transfer = make_request(max_total_size_bytes=2 * 1024**3)
    request_upload.add_file(transfer, anon_uploader, 'a', 1024**3, storage=fake_storage)
    request_upload.add_file(transfer, user_uploader, 'b', 1024**3 - 1, storage=fake_storage)

    with pytest.raises(ValidationError, match='This request accepts at most 2.0 GB in total.'):
        request_upload.add_file(transfer, anon_uploader, 'c', 2, storage=fake_storage)
    request_upload.add_file(transfer, anon_uploader, 'c', 1, storage=fake_storage)


def test_the_site_limits_apply_when_the_request_sets_none(
    make_request, anon_uploader, fake_storage, ft_settings
):
    ft_settings.logged_in_max_files = 1
    ft_settings.save()
    transfer = make_request()
    request_upload.add_file(transfer, anon_uploader, 'a', 10, storage=fake_storage)
    with pytest.raises(ValidationError, match='at most 1 file'):
        request_upload.add_file(transfer, anon_uploader, 'b', 10, storage=fake_storage)


def test_a_removed_file_frees_its_place_and_size(
    make_request, anon_uploader, upload_to_request, fake_storage
):
    transfer = make_request(max_files=1)
    file = upload_to_request(transfer, anon_uploader, 'a.txt', 100)
    key = file.storage_key
    assert key in fake_storage.objects

    request_upload.remove_own_file(transfer, file, storage=fake_storage)

    transfer.refresh_from_db()
    assert transfer.size_bytes == 0 and transfer.files.count() == 0
    assert key not in fake_storage.objects
    upload_to_request(transfer, anon_uploader, 'b.txt', 5)


def test_removing_sends_no_notice_and_leaves_a_batch_that_counts_nothing(
    funded_user, make_request, anon_uploader, upload_to_request, fake_storage
):
    from apps.core.models import Notification

    from ..services.request_notices import send_due_notices

    transfer = make_request()
    file = upload_to_request(transfer, anon_uploader, 'a.txt', 100)
    request_upload.remove_own_file(transfer, file, storage=fake_storage)

    assert send_due_notices(now=timezone.now() + timedelta(hours=1)) == 0
    assert not Notification.objects.exists()


@pytest.mark.parametrize(
    'change',
    [
        {'status': TransferStatus.DISABLED},
        {'status': TransferStatus.SUSPENDED},
        {'status': TransferStatus.EXPIRED},
        {'status': TransferStatus.DELETED},
        {'status': TransferStatus.TAKEN_DOWN},
        {'held_for_review_at': timezone.now()},
        {'expires_at': timezone.now() - timedelta(minutes=1)},
        {'done_at': timezone.now()},
    ],
)
def test_nothing_is_accepted_unless_the_request_is_open_and_available(
    make_request, anon_uploader, fake_storage, change
):
    transfer = make_request()
    Transfer.objects.filter(pk=transfer.pk).update(**change)
    transfer.refresh_from_db()

    with pytest.raises(ValidationError, match='This link is not accepting files right now.'):
        request_upload.add_file(transfer, anon_uploader, 'a', 10, storage=fake_storage)


def test_a_file_cannot_be_completed_or_presigned_after_done(
    make_request, anon_uploader, fake_storage, upload_to_request
):
    transfer = make_request()
    upload_to_request(transfer, anon_uploader, 'first.txt', 10)
    pending = request_upload.add_file(transfer, anon_uploader, 'slow.bin', 10, storage=fake_storage)
    services.requests.mark_done(transfer, by_name='Jane', storage=fake_storage)
    transfer.refresh_from_db()

    with pytest.raises(ValidationError, match='not accepting files'):
        request_upload.presign_parts(
            transfer, pending, [{'part_number': 1, 'checksum_sha256': 'A' * 43 + '='}]
        )
    with pytest.raises(ValidationError, match='not accepting files'):
        request_upload.complete_file(
            transfer, pending, [{'PartNumber': 1, 'ETag': 'e'}], anon_uploader, storage=fake_storage
        )
    with pytest.raises(ValidationError, match='not accepting files'):
        request_upload.remove_own_file(transfer, pending, storage=fake_storage)


def test_completing_a_file_that_done_already_deleted_is_refused(
    make_request, anon_uploader, fake_storage
):
    transfer = make_request()
    ghost = request_upload.add_file(transfer, anon_uploader, 'a', 10, storage=fake_storage)
    TransferFile.objects.filter(pk=ghost.pk).delete()
    with pytest.raises(ValidationError, match='no longer being uploaded'):
        request_upload.complete_file(
            transfer, ghost, [{'PartNumber': 1, 'ETag': 'e'}], anon_uploader, storage=fake_storage
        )


def test_the_owner_can_remove_any_file_even_when_done(
    funded_user, make_request, anon_uploader, upload_to_request, fake_storage
):
    transfer = make_request()
    file = upload_to_request(transfer, anon_uploader, 'a.txt', 100)
    services.requests.owner_mark_done(transfer, funded_user)
    transfer.refresh_from_db()

    request_upload.remove_file_as_owner(transfer, file, storage=fake_storage)

    transfer.refresh_from_db()
    assert transfer.size_bytes == 0 and transfer.files.count() == 0


def test_an_uploader_cannot_remove_a_file_once_done(
    funded_user, make_request, anon_uploader, upload_to_request
):
    transfer = make_request()
    file = upload_to_request(transfer, anon_uploader, 'a.txt', 100)
    services.requests.owner_mark_done(transfer, funded_user)
    transfer.refresh_from_db()
    with pytest.raises(ValidationError, match='not accepting files'):
        request_upload.remove_own_file(transfer, file)


# -- The block list --


def test_a_blocked_ip_cannot_upload(make_request, anon_uploader, fake_storage):
    BlockedSender.objects.create(kind=BlockedSenderKind.IP, value='1.2.3.0/24')
    with pytest.raises(BlockedSenderError):
        request_upload.add_file(
            make_request(), anon_uploader, 'a', 10, ip='1.2.3.4', storage=fake_storage
        )


def test_a_blocked_account_email_cannot_upload(make_request, user_uploader, fake_storage):
    BlockedSender.objects.create(kind=BlockedSenderKind.EMAIL, value='*@example.com')
    with pytest.raises(BlockedSenderError):
        request_upload.add_file(make_request(), user_uploader, 'a', 10, storage=fake_storage)


def test_a_blocked_uploader_cannot_complete_either(make_request, anon_uploader, fake_storage):
    transfer = make_request()
    file = request_upload.add_file(
        transfer, anon_uploader, 'a', 10, ip='1.2.3.4', storage=fake_storage
    )
    BlockedSender.objects.create(kind=BlockedSenderKind.IP, value='1.2.3.4')
    with pytest.raises(BlockedSenderError):
        request_upload.complete_file(
            transfer,
            file,
            [{'PartNumber': 1, 'ETag': 'e'}],
            anon_uploader,
            ip='1.2.3.4',
            storage=fake_storage,
        )


# -- Whose files are whose --


def test_an_uploader_only_sees_their_own_files(
    make_request, anon_uploader, user_uploader, upload_to_request
):
    transfer = make_request()
    mine = upload_to_request(transfer, anon_uploader, 'mine.txt', 10)
    theirs = upload_to_request(transfer, user_uploader, 'theirs.txt', 10)
    other_browser = Uploader(user=None, token_hash='b' * 64, name='', email='')

    assert list(own_files(transfer, anon_uploader)) == [mine]
    assert list(own_files(transfer, user_uploader)) == [theirs]
    assert list(own_files(transfer, other_browser)) == []
    assert request_upload.own_file(transfer, anon_uploader, theirs.id) is None
    assert request_upload.own_file(transfer, anon_uploader, mine.id) == mine
    assert request_upload.own_file(transfer, anon_uploader, 'not-a-uuid') is None


def test_files_of_other_requests_are_never_own(make_request, anon_uploader, upload_to_request):
    one, two = make_request(title='One'), make_request(title='Two')
    upload_to_request(one, anon_uploader, 'a.txt', 10)
    assert list(own_files(two, anon_uploader)) == []


def test_a_signed_in_visitor_still_sees_what_their_browser_uploaded_anonymously(
    make_request, upload_to_request, anon_uploader
):
    transfer = make_request()
    earlier = upload_to_request(transfer, anon_uploader, 'earlier.txt', 10)
    account = UserFactory(email='x@example.com')
    signed_in = Uploader(
        user=account, token_hash=anon_uploader.token_hash, name='X', email='x@example.com'
    )
    assert list(own_files(transfer, signed_in)) == [earlier]


def test_nothing_matches_an_uploader_with_no_identity(
    make_request, upload_to_request, anon_uploader
):
    transfer = make_request()
    upload_to_request(transfer, anon_uploader, 'a.txt', 10)
    nobody = Uploader(user=None, token_hash='', name='', email='')
    assert list(own_files(transfer, nobody)) == []


# -- The session identity --


class FakeSession(dict):
    pass


def test_an_anonymous_uploader_gets_one_stable_browser_token():
    from django.contrib.auth.models import AnonymousUser

    session = FakeSession()
    assert identify(session, AnonymousUser()).token_hash == ''  # reading creates nothing
    first = identify(session, AnonymousUser(), create=True)
    again = identify(session, AnonymousUser(), create=True)
    assert first.token_hash and first.token_hash == again.token_hash
    assert 'file_transfer_request_uploader_token' in session
    # Only a digest is ever stored on a file; the raw token stays in the session.
    assert session['file_transfer_request_uploader_token'] != first.token_hash


def test_a_signed_in_uploader_is_their_account():
    account = UserFactory(name='Jane Doe', email='jane@example.com')
    uploader = identify(FakeSession(), account)
    assert uploader.is_signed_in and uploader.user == account
    assert (uploader.name, uploader.email) == ('Jane Doe', 'jane@example.com')


def test_the_typed_name_is_remembered_and_cleaned():
    session = FakeSession()
    assert remember_name(session, '  Jane\x00   Doe\n') == 'Jane Doe'
    assert remember_name(session, 'x' * 300) == 'x' * 100
    assert remember_name(session, '') == ''
    assert 'file_transfer_request_uploader_name' not in session
    assert clean_name(None) == ''
