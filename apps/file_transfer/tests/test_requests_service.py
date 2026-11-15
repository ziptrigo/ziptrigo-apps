"""File requests (issue #306): creating one, changing it, marking it done and reopening it
(`services.requests`)."""

from datetime import timedelta
from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError
from django.utils import timezone

from apps.billing.services import InsufficientCreditsError
from apps.core import ratelimit
from apps.core.models import Notification
from apps.core.ratelimit import limiter

from .. import services
from ..models import (
    RequestInvite,
    Transfer,
    TransferKind,
    TransferStatus,
    UploadAudience,
    ZipStatus,
)
from ..services.actions import TransferHeldError
from ..services.blocklist import BlockedSenderError
from ..services.request_uploader import Uploader
from ..services.requests import RequestOptions, create_request

pytestmark = [pytest.mark.django_db, pytest.mark.unit]


# -- Creating --


def test_create_request_is_an_active_open_request(funded_user, make_request):
    transfer = make_request(message='Please send the photos.')

    assert transfer.kind == TransferKind.REQUEST
    assert transfer.status == TransferStatus.ACTIVE
    assert transfer.title == 'Wedding photos'
    assert transfer.message == 'Please send the photos.'
    assert transfer.upload_audience == UploadAudience.ANYONE
    assert transfer.owner == funded_user
    assert transfer.done_at is None and transfer.is_open
    assert transfer.completed_at is not None and transfer.last_billed_at is not None
    assert transfer.max_downloads is None and transfer.notify_on_download is False
    assert transfer.size_bytes == 0
    assert transfer.display_name == 'Wedding photos'


def test_create_request_costs_no_credits(funded_user, make_request):
    from apps.billing.services import get_balance

    before = get_balance(funded_user)
    make_request()
    assert get_balance(funded_user) == before


def test_a_request_needs_the_minimum_balance(user):
    with pytest.raises(InsufficientCreditsError):
        create_request(user, RequestOptions(title='Photos'))
    assert not Transfer.objects.exists()


def test_a_blocked_owner_cannot_create_one(funded_user):
    from ..models import BlockedSender, BlockedSenderKind

    BlockedSender.objects.create(kind=BlockedSenderKind.EMAIL, value=funded_user.email.lower())
    with pytest.raises(BlockedSenderError):
        create_request(funded_user, RequestOptions(title='Photos'))


def test_a_blocked_ip_cannot_create_one(funded_user):
    from ..models import BlockedSender, BlockedSenderKind

    BlockedSender.objects.create(kind=BlockedSenderKind.IP, value='9.9.9.0/24')
    with pytest.raises(BlockedSenderError):
        create_request(funded_user, RequestOptions(title='Photos'), ip='9.9.9.9')


@pytest.mark.parametrize(
    ('title', 'message'),
    [
        ('', 'Give the request a title.'),
        ('   ', 'Give the request a title.'),
        ('x' * 101, 'Keep the title'),
    ],
)
def test_the_title_is_required_and_at_most_100_characters(funded_user, title, message):
    with pytest.raises(ValidationError) as excinfo:
        create_request(funded_user, RequestOptions(title=title))
    assert message in excinfo.value.message_dict['title'][0]


def test_control_characters_are_removed_from_the_title(make_request):
    assert make_request(title='Wed\x00ding\x07 photos ').title == 'Wedding photos'


def test_the_message_is_limited(funded_user):
    with pytest.raises(ValidationError) as excinfo:
        create_request(funded_user, RequestOptions(title='x', message='m' * 2001))
    assert 'message' in excinfo.value.message_dict


def test_an_unknown_audience_is_refused(funded_user):
    with pytest.raises(ValidationError) as excinfo:
        create_request(funded_user, RequestOptions(title='x', upload_audience='everyone'))
    assert 'upload_audience' in excinfo.value.message_dict


def test_only_these_people_needs_an_address(funded_user):
    with pytest.raises(ValidationError) as excinfo:
        create_request(
            funded_user, RequestOptions(title='x', upload_audience='selected', emails=[])
        )
    assert excinfo.value.message_dict['emails'] == ['Add at least one email address.']


def test_invitees_are_deduplicated_and_stored(make_request):
    transfer = make_request(
        upload_audience='selected', emails=['Ann@Example.COM', 'ann@example.com', 'bob@x.org']
    )
    assert sorted(i.email for i in transfer.invites.all()) == ['Ann@example.com', 'bob@x.org']


def test_invitees_are_capped(funded_user, ft_settings):
    ft_settings.logged_in_max_recipients = 2
    ft_settings.save()
    with pytest.raises(ValidationError, match='at most 2 invitees'):
        create_request(
            funded_user,
            RequestOptions(
                title='x', upload_audience='selected', emails=['a@x.org', 'b@x.org', 'c@x.org']
            ),
        )


def test_an_invalid_invitee_address_is_refused(funded_user):
    with pytest.raises(ValidationError, match='not a valid email'):
        create_request(
            funded_user,
            RequestOptions(title='x', upload_audience='selected', emails=['nonsense']),
        )


def test_invitees_are_ignored_unless_the_audience_is_selected(make_request):
    assert make_request(upload_audience='signed_in', emails=['a@x.org']).invites.count() == 0


def test_invites_are_emailed_once_created(
    funded_user, make_request, django_capture_on_commit_callbacks, sent_emails
):
    with django_capture_on_commit_callbacks(execute=True):
        transfer = make_request(
            upload_audience='selected', emails=['ann@example.com'], message='Hello & <b>welcome</b>'
        )

    assert len(sent_emails) == 1
    mail = sent_emails[0]
    assert mail['to'] == 'ann@example.com'
    # A fixed subject: nothing the owner typed is in it.
    assert mail['subject'] == "You've been asked to upload files"
    assert transfer.absolute_download_url in mail['text_body']
    assert 'Wedding photos' in mail['text_body']
    assert 'Sign in or create a ZipTrigo account with this address to upload.' in mail['text_body']
    html = mail['html_body']
    assert 'Hello &amp; &lt;b&gt;welcome&lt;/b&gt;' in html and '<b>welcome</b>' not in html


@pytest.mark.parametrize(
    ('options', 'field'),
    [
        ({'max_files': 0}, 'max_files'),
        ({'max_files': 101}, 'max_files'),
        ({'max_total_size_bytes': 0}, 'max_total_size_bytes'),
        ({'max_total_size_bytes': 21 * 1024**3}, 'max_total_size_bytes'),
    ],
)
def test_limits_are_checked_against_the_site_maximum(funded_user, options, field):
    with pytest.raises(ValidationError) as excinfo:
        create_request(funded_user, RequestOptions(title='x', **options))
    assert field in excinfo.value.message_dict


def test_limits_at_the_site_maximum_are_accepted(make_request):
    transfer = make_request(max_files=100, max_total_size_bytes=20 * 1024**3)
    assert (transfer.max_files, transfer.max_total_size_bytes) == (100, 20 * 1024**3)
    assert transfer.effective_max_files == 100


def test_blank_limits_use_the_site_maximum(make_request, ft_settings):
    transfer = make_request()
    assert transfer.max_files is None and transfer.max_total_size_bytes is None
    assert transfer.effective_max_files == ft_settings.logged_in_max_files
    assert transfer.effective_max_total_size_bytes == ft_settings.logged_in_max_total_size_bytes


def test_expiry_and_password(make_request):
    transfer = make_request(expiry_choice='5', password='sekret')
    assert transfer.expires_at is not None
    assert transfer.expires_at > timezone.now() + timedelta(days=4)
    assert transfer.password_hash and transfer.password_hash != 'sekret'


def test_an_invalid_expiry_is_refused(funded_user):
    with pytest.raises(ValidationError, match='Invalid expiry choice'):
        create_request(funded_user, RequestOptions(title='x', expiry_choice='7'))


def test_gb_to_bytes():
    assert services.requests.gb_to_bytes(None) is None
    assert services.requests.gb_to_bytes(Decimal('1.5')) == 3 * 1024**3 // 2


def test_a_refused_request_spends_no_budget_and_creates_nothing(funded_user, settings):
    settings.RATELIMIT_ENABLE = True
    with pytest.raises(ValidationError):
        create_request(
            funded_user,
            RequestOptions(title=''),
            charge=lambda n: services.requests.charge_request_creation(funded_user, n),
        )
    assert limiter.peek(str(funded_user.pk), 'FT_REQUEST_CREATE_USER').remaining == 30
    assert not Transfer.objects.exists()


def test_charge_is_called_with_the_number_of_invite_emails(funded_user):
    calls = []
    create_request(
        funded_user,
        RequestOptions(title='x', upload_audience='selected', emails=['a@x.org', 'b@x.org']),
        charge=calls.append,
    )
    assert calls == [2]


def test_over_the_budget_nothing_is_created(funded_user):
    def refuse(count):
        raise ratelimit.RateLimitExceeded(retry_after=60, message='try later')

    with pytest.raises(ratelimit.RateLimitExceeded):
        create_request(funded_user, RequestOptions(title='x'), charge=refuse)
    assert not Transfer.objects.exists()


def test_charge_request_creation_spends_one_create_hit_and_one_email_hit_per_invite(
    funded_user, settings
):
    settings.RATELIMIT_ENABLE = True
    services.requests.charge_request_creation(funded_user, 3)
    assert limiter.peek(str(funded_user.pk), 'FT_REQUEST_CREATE_USER').remaining == 29
    assert limiter.peek(str(funded_user.pk), 'FT_RECIPIENT_EMAIL_USER').remaining == 27


def test_the_create_rule_is_enforced(funded_user, settings):
    settings.RATELIMIT_ENABLE = True
    settings.RATELIMIT_RULES = {**settings.RATELIMIT_RULES, 'FT_REQUEST_CREATE_USER': (1, 3600)}
    services.requests.charge_request_creation(funded_user, 0)
    with pytest.raises(ratelimit.RateLimitExceeded):
        services.requests.charge_request_creation(funded_user, 0)


# -- Changing --


def test_update_changes_only_the_keys_given(make_request):
    transfer = make_request(message='keep me')
    services.requests.update_request(transfer, {'title': 'New title'})
    transfer.refresh_from_db()
    assert transfer.title == 'New title' and transfer.message == 'keep me'


def test_update_everything_at_once(make_request):
    transfer = make_request()
    services.requests.update_request(
        transfer,
        {
            'title': 'Renamed',
            'message': 'hello',
            'upload_audience': 'signed_in',
            'max_files': 5,
            'max_total_size_bytes': 1024,
            'expiry_choice': '15',
            'password': 'pw',
        },
    )
    transfer.refresh_from_db()
    assert transfer.upload_audience == 'signed_in'
    assert (transfer.max_files, transfer.max_total_size_bytes) == (5, 1024)
    assert transfer.expires_at is not None and transfer.password_hash
    services.requests.update_request(transfer, {'remove_password': True, 'max_files': None})
    transfer.refresh_from_db()
    assert transfer.password_hash == '' and transfer.max_files is None


def test_update_is_all_or_nothing(make_request):
    transfer = make_request()
    with pytest.raises(ValidationError):
        services.requests.update_request(transfer, {'title': 'Renamed', 'max_files': 0})
    transfer.refresh_from_db()
    assert transfer.title == 'Wedding photos'


def test_limits_cannot_go_below_what_is_uploaded(make_request, upload_to_request, anon_uploader):
    transfer = make_request()
    upload_to_request(transfer, anon_uploader, 'a.txt', 600)
    upload_to_request(transfer, anon_uploader, 'b.txt', 600)

    with pytest.raises(ValidationError) as excinfo:
        services.requests.update_request(transfer, {'max_files': 1})
    assert excinfo.value.message_dict['max_files'] == [
        'Already 2 files uploaded; choose at least 2.'
    ]
    with pytest.raises(ValidationError) as excinfo:
        services.requests.update_request(transfer, {'max_total_size_bytes': 1000})
    assert (
        'Already 1.2 KB uploaded; choose at least 1.2 KB.'
        in excinfo.value.message_dict['max_total_size_bytes'][0]
    )
    services.requests.update_request(transfer, {'max_files': 2, 'max_total_size_bytes': 1200})


def test_switching_to_selected_requires_an_address(make_request):
    transfer = make_request()
    with pytest.raises(ValidationError) as excinfo:
        services.requests.update_request(transfer, {'upload_audience': 'selected'})
    assert 'emails' in excinfo.value.message_dict
    with pytest.raises(ValidationError):
        services.requests.update_request(transfer, {'upload_audience': 'selected', 'emails': []})
    assert transfer.invites.count() == 0


def test_the_invitee_list_is_replaced_and_new_addresses_are_invited(
    make_request, django_capture_on_commit_callbacks, sent_emails
):
    transfer = make_request(upload_audience='selected', emails=['a@x.org', 'b@x.org'])
    charged = []

    with django_capture_on_commit_callbacks(execute=True):
        services.requests.update_request(
            transfer, {'emails': ['b@x.org', 'c@x.org']}, charge=charged.append
        )

    assert sorted(i.email for i in transfer.invites.all()) == ['b@x.org', 'c@x.org']
    assert charged == [1]
    assert [m['to'] for m in sent_emails] == ['c@x.org']


def test_invitees_are_kept_when_the_audience_changes_away_and_back(make_request):
    transfer = make_request(upload_audience='selected', emails=['a@x.org'])
    services.requests.update_request(transfer, {'upload_audience': 'anyone', 'emails': []})
    assert transfer.invites.count() == 1
    services.requests.update_request(transfer, {'upload_audience': 'selected'})
    assert transfer.invites.count() == 1


def test_adding_invitees_over_the_budget_changes_nothing(make_request):
    transfer = make_request(upload_audience='selected', emails=['a@x.org'])

    def refuse(count):
        raise ratelimit.RateLimitExceeded(retry_after=60, message='try later')

    with pytest.raises(ratelimit.RateLimitExceeded):
        services.requests.update_request(
            transfer, {'title': 'Renamed', 'emails': ['a@x.org', 'b@x.org']}, charge=refuse
        )
    transfer.refresh_from_db()
    assert transfer.title == 'Wedding photos' and transfer.invites.count() == 1


def test_a_held_request_cannot_be_changed(make_request):
    transfer = make_request()
    transfer.held_for_review_at = timezone.now()
    transfer.save()
    with pytest.raises(TransferHeldError):
        services.requests.update_request(transfer, {'title': 'x'})


def test_an_ended_request_cannot_be_changed(make_request):
    transfer = make_request()
    transfer.status = TransferStatus.DELETED
    transfer.save()
    with pytest.raises(ValidationError, match='already ended'):
        services.requests.update_request(transfer, {'title': 'x'})


def test_update_refuses_a_send(draft_transfer):
    with pytest.raises(ValidationError, match='not a file request'):
        services.requests.update_request(draft_transfer, {'title': 'x'})


# -- Done and reopen --


def test_an_uploader_cannot_mark_done_with_no_files(make_request):
    transfer = make_request()
    with pytest.raises(ValidationError, match='Upload at least one file first.'):
        services.requests.mark_done(transfer, by_name='Jane')
    transfer.refresh_from_db()
    assert transfer.done_at is None


def test_the_owner_can_mark_done_with_no_files(funded_user, make_request):
    transfer = make_request()
    assert services.requests.owner_mark_done(transfer, funded_user) is True
    transfer.refresh_from_db()
    assert transfer.is_done and transfer.done_by_user == funded_user


def test_an_uploader_marks_it_done_and_the_owner_is_told(
    funded_user,
    make_request,
    upload_to_request,
    anon_uploader,
    django_capture_on_commit_callbacks,
    sent_emails,
):
    transfer = make_request()
    upload_to_request(transfer, anon_uploader, 'a.txt', 2048)
    upload_to_request(transfer, anon_uploader, 'b.txt', 2048)

    with django_capture_on_commit_callbacks(execute=True):
        assert services.requests.mark_done(transfer, by_name='Jane') is True

    transfer.refresh_from_db()
    assert transfer.is_done and transfer.done_by_name == 'Jane' and transfer.done_by_user is None
    assert transfer.upload_notice_due_at is None and transfer.upload_notice_since is None

    notice = Notification.objects.get(user=funded_user, key=f'transfer_request_done:{transfer.id}')
    assert notice.title == '"Wedding photos" is done'
    assert notice.body == '2 files, 4.0 KB. Marked done by Jane.'
    assert notice.url == '/transfer/?kind=request'

    assert len(sent_emails) == 1
    assert sent_emails[0]['to'] == funded_user.email
    assert sent_emails[0]['subject'] == 'Your file request is done'
    assert (
        '2 files, 4.0 KB' in sent_emails[0]['text_body'] and 'Jane' in sent_emails[0]['text_body']
    )
    assert 'Wedding photos' not in sent_emails[0]['subject']


def test_done_by_a_signed_in_uploader_names_the_account(
    funded_user, make_request, upload_to_request, user_uploader, django_capture_on_commit_callbacks
):
    transfer = make_request()
    upload_to_request(transfer, user_uploader, 'a.txt', 10)
    with django_capture_on_commit_callbacks(execute=True):
        services.requests.mark_done(transfer, by_user=user_uploader.user)
    notice = Notification.objects.get(user=funded_user, key__startswith='transfer_request_done:')
    assert notice.body.endswith('Marked done by John.')


def test_done_without_a_name_says_someone(
    funded_user, make_request, upload_to_request, anon_uploader, django_capture_on_commit_callbacks
):
    transfer = make_request()
    upload_to_request(transfer, anon_uploader, 'a.txt', 10)
    with django_capture_on_commit_callbacks(execute=True):
        services.requests.mark_done(transfer)
    notice = Notification.objects.get(user=funded_user, key__startswith='transfer_request_done:')
    assert notice.body.endswith('Marked done by someone.')


def test_the_owner_marking_done_sends_nothing(
    funded_user,
    make_request,
    upload_to_request,
    anon_uploader,
    django_capture_on_commit_callbacks,
    sent_emails,
):
    transfer = make_request()
    upload_to_request(transfer, anon_uploader, 'a.txt', 10)
    with django_capture_on_commit_callbacks(execute=True):
        services.requests.owner_mark_done(transfer, funded_user)
    assert not Notification.objects.filter(
        user=funded_user, key__startswith='transfer_request_done'
    ).exists()
    assert sent_emails == []


def test_the_owner_uploading_then_marking_done_sends_nothing(
    funded_user, make_request, upload_to_request, django_capture_on_commit_callbacks, sent_emails
):
    transfer = make_request()
    owner = Uploader(
        user=funded_user, token_hash='', name=funded_user.name, email=funded_user.email
    )
    upload_to_request(transfer, owner, 'a.txt', 10)
    with django_capture_on_commit_callbacks(execute=True):
        services.requests.mark_done(transfer, by_user=funded_user)
    assert not Notification.objects.exists() and sent_emails == []


def test_done_deletes_the_notice_about_new_files(
    funded_user, make_request, upload_to_request, anon_uploader, django_capture_on_commit_callbacks
):
    transfer = make_request()
    upload_to_request(transfer, anon_uploader, 'a.txt', 10)
    transfer.refresh_from_db()
    assert transfer.upload_notice_due_at is not None
    from ..services.request_notices import send_due_notices

    send_due_notices(now=timezone.now() + timedelta(hours=1))
    assert Notification.objects.filter(key=f'transfer_request_uploads:{transfer.id}').exists()

    with django_capture_on_commit_callbacks(execute=True):
        services.requests.mark_done(transfer, by_name='Jane')
    assert not Notification.objects.filter(key=f'transfer_request_uploads:{transfer.id}').exists()
    assert Notification.objects.filter(key=f'transfer_request_done:{transfer.id}').exists()


def test_done_twice_is_a_no_op(
    make_request, upload_to_request, anon_uploader, django_capture_on_commit_callbacks, sent_emails
):
    transfer = make_request()
    upload_to_request(transfer, anon_uploader, 'a.txt', 10)
    with django_capture_on_commit_callbacks(execute=True):
        assert services.requests.mark_done(transfer, by_name='Jane') is True
        assert services.requests.mark_done(transfer, by_name='Someone else') is False
    transfer.refresh_from_db()
    assert transfer.done_by_name == 'Jane'
    assert len(sent_emails) == 1


def test_done_deletes_unfinished_uploads_of_every_uploader(
    make_request,
    upload_to_request,
    anon_uploader,
    user_uploader,
    fake_storage,
    django_capture_on_commit_callbacks,
):
    from ..services import request_upload

    transfer = make_request()
    upload_to_request(transfer, anon_uploader, 'done.txt', 10)
    pending = request_upload.add_file(
        transfer, user_uploader, 'slow.bin', 500, storage=fake_storage
    )
    assert pending.upload_id in fake_storage.active_uploads

    with django_capture_on_commit_callbacks(execute=True):
        services.requests.mark_done(transfer, by_name='Jane', storage=fake_storage)

    assert [f.name for f in transfer.files.all()] == ['done.txt']
    assert pending.upload_id in fake_storage.aborted_uploads
    transfer.refresh_from_db()
    assert transfer.size_bytes == 10


def test_done_is_refused_when_the_link_is_not_active(
    make_request, upload_to_request, anon_uploader
):
    transfer = make_request()
    upload_to_request(transfer, anon_uploader, 'a.txt', 10)
    Transfer.objects.filter(pk=transfer.pk).update(status=TransferStatus.DISABLED)
    transfer.refresh_from_db()
    with pytest.raises(ValidationError, match='not accepting files'):
        services.requests.mark_done(transfer, by_name='Jane')


def test_the_owner_cannot_mark_a_disabled_request_done(funded_user, make_request):
    transfer = make_request()
    Transfer.objects.filter(pk=transfer.pk).update(status=TransferStatus.DISABLED)
    transfer.refresh_from_db()
    with pytest.raises(ValidationError, match='Only an active request'):
        services.requests.owner_mark_done(transfer, funded_user)


def test_an_inactive_owner_is_not_told(
    funded_user,
    make_request,
    upload_to_request,
    anon_uploader,
    django_capture_on_commit_callbacks,
    sent_emails,
):
    from apps.accounts.models import User

    transfer = make_request()
    upload_to_request(transfer, anon_uploader, 'a.txt', 10)
    funded_user.status = User.STATUS_INACTIVE
    funded_user.save()
    transfer.refresh_from_db()
    with django_capture_on_commit_callbacks(execute=True):
        services.requests.mark_done(transfer, by_name='Jane')
    assert not Notification.objects.exists() and sent_emails == []


def test_an_unconfirmed_owner_gets_the_notice_but_no_email(
    funded_user,
    make_request,
    upload_to_request,
    anon_uploader,
    django_capture_on_commit_callbacks,
    sent_emails,
):
    transfer = make_request()
    upload_to_request(transfer, anon_uploader, 'a.txt', 10)
    funded_user.email_confirmed = False
    funded_user.save()
    transfer.refresh_from_db()
    with django_capture_on_commit_callbacks(execute=True):
        services.requests.mark_done(transfer, by_name='Jane')
    assert Notification.objects.filter(user=funded_user).count() == 1
    assert sent_emails == []


def test_reopen_puts_the_link_back_in_upload_mode(
    funded_user, make_request, upload_to_request, anon_uploader
):
    transfer = make_request()
    upload_to_request(transfer, anon_uploader, 'a.txt', 10)
    services.requests.mark_done(transfer, by_name='Jane')

    services.requests.reopen(transfer)

    transfer.refresh_from_db()
    assert transfer.is_open and transfer.done_by_user is None and transfer.done_by_name == ''
    assert transfer.files.count() == 1
    # Uploaders can add files again.
    upload_to_request(transfer, anon_uploader, 'b.txt', 10)


def test_reopening_an_open_request_is_an_error(make_request):
    with pytest.raises(ValidationError, match='already open'):
        services.requests.reopen(make_request())


def test_a_held_request_cannot_be_reopened(funded_user, make_request):
    transfer = make_request()
    services.requests.owner_mark_done(transfer, funded_user)
    Transfer.objects.filter(pk=transfer.pk).update(held_for_review_at=timezone.now())
    transfer.refresh_from_db()
    with pytest.raises(TransferHeldError):
        services.requests.reopen(transfer)


# -- Zip and cleanup --


def test_the_zip_is_forgotten_when_the_files_change(
    make_request, upload_to_request, anon_uploader, fake_storage
):
    transfer = make_request()
    upload_to_request(transfer, anon_uploader, 'a.txt', 10)
    Transfer.objects.filter(pk=transfer.pk).update(
        zip_status=ZipStatus.READY, zip_key=f'transfers/{transfer.id}/all.zip'
    )
    fake_storage.objects[f'transfers/{transfer.id}/all.zip'] = b'zip'

    upload_to_request(transfer, anon_uploader, 'b.txt', 10)

    transfer.refresh_from_db()
    assert transfer.zip_status == ZipStatus.NONE and transfer.zip_key == ''


def test_cleanup_deletes_old_unfinished_uploads(make_request, user_uploader, fake_storage):
    from ..models import TransferFile
    from ..services import request_upload

    transfer = make_request()
    old = request_upload.add_file(transfer, user_uploader, 'old.bin', 100, storage=fake_storage)
    new = request_upload.add_file(transfer, user_uploader, 'new.bin', 100, storage=fake_storage)
    TransferFile.objects.filter(pk=old.pk).update(created_at=timezone.now() - timedelta(hours=25))

    assert services.requests.cleanup_abandoned_files(storage=fake_storage) == 1

    assert list(transfer.files.values_list('name', flat=True)) == ['new.bin']
    assert old.upload_id in fake_storage.aborted_uploads
    assert new.upload_id in fake_storage.active_uploads


def test_cleanup_never_touches_finished_files_or_sends(
    make_request, upload_to_request, anon_uploader, draft_transfer, fake_storage
):
    from ..models import TransferFile

    transfer = make_request()
    upload_to_request(transfer, anon_uploader, 'a.txt', 10)
    TransferFile.objects.update(created_at=timezone.now() - timedelta(days=3))
    assert services.requests.cleanup_abandoned_files(storage=fake_storage) == 0
    assert transfer.files.count() == 1


def test_a_user_can_be_the_owner_of_many_requests(funded_user, make_request):
    first, second = make_request(title='A'), make_request(title='B')
    assert first.slug != second.slug
    assert RequestInvite.objects.count() == 0
