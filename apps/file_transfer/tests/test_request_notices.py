"""The debounced "new files" notice and the invites of a file request (issue #306): the job
`file_transfer.send_request_notices`, `services.request_notices` and `services.request_invites`."""

from datetime import timedelta

import pytest
from django.apps import apps
from django.core.exceptions import ValidationError
from django.utils import timezone

from apps.accounts.models import User
from apps.core import ratelimit
from apps.core.models import Notification

from .. import jobs, services
from ..models import Transfer, TransferFile
from ..services import request_notices, request_upload
from ..services.actions import RESEND_COOLDOWN, TransferHeldError
from ..services.blocklist import BlockedSenderError
from ..services.request_uploader import Uploader
from ..services.requests import uploads_notice_key
from .support import new_user

pytestmark = [pytest.mark.django_db, pytest.mark.unit]

LATER = timedelta(minutes=16)


def _notices(owner):
    return list(Notification.objects.filter(user=owner, source='file_transfer'))


def test_an_upload_starts_the_debounce(make_request, anon_uploader, upload_to_request):
    transfer = make_request()
    file = upload_to_request(transfer, anon_uploader, 'a.txt', 10)
    transfer.refresh_from_db()

    assert transfer.upload_notice_since == file.uploaded_at
    assert transfer.upload_notice_due_at == file.uploaded_at + request_upload.NOTICE_DEBOUNCE
    assert request_upload.NOTICE_DEBOUNCE == timedelta(minutes=15)


def test_every_upload_pushes_the_notice_back_but_keeps_the_batch_start(
    make_request, anon_uploader, upload_to_request
):
    transfer = make_request()
    first = upload_to_request(transfer, anon_uploader, 'a.txt', 10)
    transfer.refresh_from_db()
    due_after_first = transfer.upload_notice_due_at

    second = upload_to_request(transfer, anon_uploader, 'b.txt', 10)
    transfer.refresh_from_db()

    assert transfer.upload_notice_since == first.uploaded_at
    assert transfer.upload_notice_due_at == second.uploaded_at + request_upload.NOTICE_DEBOUNCE
    assert transfer.upload_notice_due_at > due_after_first


def test_nothing_is_sent_before_the_debounce_is_over(
    funded_user, make_request, anon_uploader, upload_to_request
):
    transfer = make_request()
    upload_to_request(transfer, anon_uploader, 'a.txt', 10)
    assert request_notices.send_due_notices(now=timezone.now() + timedelta(minutes=10)) == 0
    assert _notices(funded_user) == []


def test_one_notice_per_batch_names_the_files_and_the_uploaders(
    funded_user, make_request, anon_uploader, user_uploader, upload_to_request
):
    transfer = make_request(title='Wedding photos')
    upload_to_request(transfer, anon_uploader, 'a.txt', 10)
    upload_to_request(transfer, anon_uploader, 'b.txt', 10)
    upload_to_request(transfer, user_uploader, 'c.txt', 10)

    assert request_notices.send_due_notices(now=timezone.now() + LATER) == 1

    (notice,) = _notices(funded_user)
    assert notice.title == '3 new files in "Wedding photos"'
    assert notice.body == 'From Jane and John'
    assert notice.url == '/transfer/?kind=request'
    assert notice.key == f'transfer_request_uploads:{transfer.id}'
    transfer.refresh_from_db()
    assert transfer.upload_notice_due_at is None and transfer.upload_notice_since is None

    # Idempotent: another run (or a concurrent one) finds nothing to claim.
    assert request_notices.send_due_notices(now=timezone.now() + LATER) == 0
    assert len(_notices(funded_user)) == 1


def test_the_notice_is_singular_for_one_file(
    funded_user, make_request, anon_uploader, upload_to_request
):
    transfer = make_request(title='Taxes')
    upload_to_request(transfer, anon_uploader, 'a.txt', 10)
    request_notices.send_due_notices(now=timezone.now() + LATER)
    assert _notices(funded_user)[0].title == '1 new file in "Taxes"'


def test_the_body_says_how_many_others_there_were(funded_user, make_request, upload_to_request):
    transfer = make_request()
    uploaders = [
        Uploader(user=None, token_hash=f'{i}' * 64, name=name, email='')
        for i, name in enumerate(['Jane', '', 'Sam', ''])
    ]
    for index, uploader in enumerate(uploaders):
        upload_to_request(transfer, uploader, f'{index}.txt', 10)
    request_notices.send_due_notices(now=timezone.now() + LATER)
    assert _notices(funded_user)[0].body == 'From Jane, Sam and 2 others'


def test_the_body_names_a_signed_in_uploader_by_email_when_they_have_no_name(
    funded_user, make_request, upload_to_request
):

    account = new_user(name='', email='john@example.com')
    transfer = make_request()
    upload_to_request(
        transfer,
        Uploader(user=account, token_hash='', name='', email='john@example.com'),
        'a.txt',
        10,
    )
    request_notices.send_due_notices(now=timezone.now() + LATER)
    assert _notices(funded_user)[0].body == 'From john@example.com'


def test_an_unnamed_anonymous_uploader_is_not_named(funded_user, make_request, upload_to_request):
    transfer = make_request()
    upload_to_request(
        transfer, Uploader(user=None, token_hash='c' * 64, name='', email=''), 'a.txt', 10
    )
    request_notices.send_due_notices(now=timezone.now() + LATER)
    assert _notices(funded_user)[0].body == ''


def test_a_long_request_title_keeps_the_notice_title_within_140(
    funded_user, make_request, anon_uploader, upload_to_request
):
    transfer = make_request(title='W' * 100)
    upload_to_request(transfer, anon_uploader, 'a.txt', 10)
    request_notices.send_due_notices(now=timezone.now() + LATER)
    title = _notices(funded_user)[0].title
    assert len(title) <= 140 and title.startswith('1 new file in "WWW')


def test_a_new_notice_replaces_the_previous_one(
    funded_user, make_request, anon_uploader, upload_to_request
):
    transfer = make_request()
    upload_to_request(transfer, anon_uploader, 'a.txt', 10)
    request_notices.send_due_notices(now=timezone.now() + LATER)
    upload_to_request(transfer, anon_uploader, 'b.txt', 10)
    request_notices.send_due_notices(now=timezone.now() + 2 * LATER)

    (notice,) = _notices(funded_user)
    assert notice.title == '1 new file in "Wedding photos"'  # the second batch, not 2 files


def test_files_removed_meanwhile_are_not_counted(
    funded_user, make_request, anon_uploader, upload_to_request, fake_storage
):
    transfer = make_request()
    keep = upload_to_request(transfer, anon_uploader, 'keep.txt', 10)
    gone = upload_to_request(transfer, anon_uploader, 'gone.txt', 10)
    request_upload.remove_own_file(transfer, gone, storage=fake_storage)

    request_notices.send_due_notices(now=timezone.now() + LATER)

    assert _notices(funded_user)[0].title == '1 new file in "Wedding photos"'
    assert keep.pk


def test_the_owners_own_uploads_send_no_notice(funded_user, make_request, upload_to_request):
    transfer = make_request()
    owner = Uploader(user=funded_user, token_hash='', name='me', email=funded_user.email)
    upload_to_request(transfer, owner, 'a.txt', 10)
    transfer.refresh_from_db()
    assert transfer.upload_notice_due_at is None
    assert request_notices.send_due_notices(now=timezone.now() + LATER) == 0


def test_a_request_marked_done_is_skipped(
    funded_user, make_request, anon_uploader, upload_to_request
):
    transfer = make_request()
    upload_to_request(transfer, anon_uploader, 'a.txt', 10)
    Transfer.objects.filter(pk=transfer.pk).update(done_at=timezone.now())
    assert request_notices.send_due_notices(now=timezone.now() + LATER) == 0
    assert not Notification.objects.filter(key=uploads_notice_key(transfer.pk)).exists()


def test_an_inactive_owner_is_not_told(funded_user, make_request, anon_uploader, upload_to_request):
    transfer = make_request()
    upload_to_request(transfer, anon_uploader, 'a.txt', 10)
    funded_user.status = User.STATUS_INACTIVE
    funded_user.save()
    assert request_notices.send_due_notices(now=timezone.now() + LATER) == 0
    transfer.refresh_from_db()
    assert transfer.upload_notice_since is None  # the batch is closed even so


def test_no_email_is_sent_for_new_files(
    make_request, anon_uploader, upload_to_request, sent_emails
):
    transfer = make_request()
    upload_to_request(transfer, anon_uploader, 'a.txt', 10)
    request_notices.send_due_notices(now=timezone.now() + LATER)
    assert sent_emails == []


def test_a_failing_request_does_not_stop_the_others(
    funded_user, make_request, anon_uploader, upload_to_request, monkeypatch, caplog
):
    first, second = make_request(title='First'), make_request(title='Second')
    upload_to_request(first, anon_uploader, 'a.txt', 10)
    upload_to_request(second, anon_uploader, 'b.txt', 10)

    real = request_notices._send_one
    calls = []

    def flaky(pk, now):
        calls.append(pk)
        if len(calls) == 1:
            raise RuntimeError('boom')
        return real(pk, now)

    monkeypatch.setattr(request_notices, '_send_one', flaky)
    assert request_notices.send_due_notices(now=timezone.now() + LATER) == 1
    assert len(calls) == 2 and 'Could not send the new-files notice' in caplog.text


def test_the_job_is_registered_with_a_description():
    from apps.core.scheduler import registry

    # Other tests clear the registry: register this app's jobs again, as `ready()` does.
    apps.get_app_config('file_transfer')._register_jobs()
    spec = registry.get_job('file_transfer.send_request_notices')
    assert spec is not None
    assert spec.func is jobs.send_request_notices
    assert spec.interval == timedelta(minutes=1) and spec.lease == timedelta(minutes=5)
    assert spec.description


def test_the_job_runs_the_notices(funded_user, make_request, anon_uploader, upload_to_request):
    transfer = make_request()
    upload_to_request(transfer, anon_uploader, 'a.txt', 10)
    Transfer.objects.filter(pk=transfer.pk).update(
        upload_notice_due_at=timezone.now() - timedelta(minutes=1)
    )
    jobs.send_request_notices()
    assert len(_notices(funded_user)) == 1


def test_the_job_purges_uploader_ips_like_download_ips(
    make_request, anon_uploader, upload_to_request
):
    transfer = make_request()
    old = upload_to_request(transfer, anon_uploader, 'old.txt', 10, ip='1.1.1.1')
    new = upload_to_request(transfer, anon_uploader, 'new.txt', 10, ip='2.2.2.2')
    TransferFile.objects.filter(pk=old.pk).update(created_at=timezone.now() - timedelta(days=91))

    jobs.purge_download_ips()

    assert TransferFile.objects.get(pk=old.pk).uploader_ip is None
    assert TransferFile.objects.get(pk=new.pk).uploader_ip == '2.2.2.2'


def test_cleanup_drafts_also_clears_abandoned_request_uploads(
    make_request, anon_uploader, fake_storage
):
    transfer = make_request()
    file = request_upload.add_file(transfer, anon_uploader, 'a.bin', 10, storage=fake_storage)
    TransferFile.objects.filter(pk=file.pk).update(created_at=timezone.now() - timedelta(hours=30))
    jobs.cleanup_drafts()
    assert transfer.files.count() == 0


# -- Invites --


def test_resend_emails_the_invitee_again(make_request, sent_emails):
    transfer = make_request(upload_audience='selected', emails=['ann@example.com'])
    invite = transfer.invites.get()
    invite.last_sent_at = timezone.now() - RESEND_COOLDOWN - timedelta(seconds=1)
    invite.save()

    services.request_invites.resend_invite(invite)

    assert [mail['to'] for mail in sent_emails] == ['ann@example.com']
    invite.refresh_from_db()
    assert invite.last_sent_at > timezone.now() - timedelta(seconds=5)


def test_resend_respects_the_cooldown(make_request, sent_emails):
    transfer = make_request(upload_audience='selected', emails=['ann@example.com'])
    invite = transfer.invites.get()
    invite.last_sent_at = timezone.now() - timedelta(minutes=3)
    invite.save()

    with pytest.raises(ValidationError, match='you can resend in 7 minutes'):
        services.request_invites.resend_invite(invite)
    assert sent_emails == []


def test_resend_is_charged_one_hit_after_every_other_check(make_request):
    transfer = make_request(upload_audience='selected', emails=['ann@example.com'])
    invite = transfer.invites.get()
    invite.last_sent_at = timezone.now() - timedelta(minutes=3)
    invite.save()
    charged = []

    with pytest.raises(ValidationError):
        services.request_invites.resend_invite(invite, charge=charged.append)
    assert charged == []

    invite.last_sent_at = None
    invite.save()
    services.request_invites.resend_invite(invite, charge=charged.append)
    assert charged == [1]


def test_resend_over_the_budget_emails_nobody(make_request, sent_emails):
    transfer = make_request(upload_audience='selected', emails=['ann@example.com'])
    invite = transfer.invites.get()
    invite.last_sent_at = None
    invite.save()

    def refuse(count):
        raise ratelimit.RateLimitExceeded(retry_after=60, message='try later')

    with pytest.raises(ratelimit.RateLimitExceeded):
        services.request_invites.resend_invite(invite, charge=refuse)
    assert sent_emails == []


def test_resend_is_refused_for_a_held_or_ended_request_or_blocked_owner(make_request):
    from ..models import BlockedSender, BlockedSenderKind, TransferStatus

    transfer = make_request(upload_audience='selected', emails=['ann@example.com'])
    invite = transfer.invites.get()
    invite.last_sent_at = None
    invite.save()

    Transfer.objects.filter(pk=transfer.pk).update(held_for_review_at=timezone.now())
    invite = transfer.invites.select_related('transfer').get()
    with pytest.raises(TransferHeldError):
        services.request_invites.resend_invite(invite)

    Transfer.objects.filter(pk=transfer.pk).update(held_for_review_at=None)
    BlockedSender.objects.create(kind=BlockedSenderKind.EMAIL, value=transfer.owner.email.lower())
    invite = transfer.invites.select_related('transfer__owner').get()
    with pytest.raises(BlockedSenderError):
        services.request_invites.resend_invite(invite)

    BlockedSender.objects.all().delete()
    Transfer.objects.filter(pk=transfer.pk).update(status=TransferStatus.DELETED)
    invite = transfer.invites.select_related('transfer').get()
    with pytest.raises(ValidationError, match='already ended'):
        services.request_invites.resend_invite(invite)


def test_an_invite_email_for_a_removed_invitee_is_not_sent(make_request, sent_emails):
    from ..services.emails import send_request_invite

    transfer = make_request(upload_audience='selected', emails=['ann@example.com', 'b@x.org'])
    transfer.invites.filter(email='ann@example.com').delete()
    send_request_invite.call(str(transfer.id), 'ann@example.com')
    assert sent_emails == []
    send_request_invite.call(str(transfer.id), 'b@x.org')
    assert len(sent_emails) == 1


def test_invite_emails_are_not_sent_for_an_ended_request(make_request, sent_emails):
    from ..models import TransferStatus
    from ..services.emails import send_request_invite

    transfer = make_request(upload_audience='selected', emails=['ann@example.com'])
    Transfer.objects.filter(pk=transfer.pk).update(status=TransferStatus.DELETED)
    send_request_invite.call(str(transfer.id), 'ann@example.com')
    assert sent_emails == []


def test_the_invite_names_the_owner_by_account_name_never_email(
    funded_user, make_request, sent_emails
):
    from ..services.emails import send_request_invite

    funded_user.name = ''
    funded_user.save()
    transfer = make_request(upload_audience='selected', emails=['ann@example.com'])
    send_request_invite.call(str(transfer.id), 'ann@example.com')
    assert 'Someone is asking you for files' in sent_emails[0]['text_body']
    assert funded_user.email not in sent_emails[0]['text_body']
