"""The owner's side of file requests (issue #306): the create page, the "created" page, the
dashboard's Sent | Requests tabs and a request row's actions."""

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.accounts.tests.factories import UserFactory
from apps.billing.services import add_credits
from apps.core.models import Notification

from ..models import (
    BlockedSender,
    BlockedSenderKind,
    RequestInvite,
    Transfer,
    TransferKind,
    TransferStatus,
)
from ..services.request_uploader import Uploader

pytestmark = [pytest.mark.django_db, pytest.mark.unit]

NEW = 'file_transfer:request-new'


@pytest.fixture(autouse=True)
def _fake_storage(fake_storage):
    return fake_storage


@pytest.fixture
def owner_client(client, funded_user):
    client.force_login(funded_user)
    return client


def _create(client, **fields):
    data = {'title': 'Wedding photos', 'upload_audience': 'anyone', 'expiry_choice': 'none'}
    data.update(fields)
    return client.post(reverse(NEW), data, HTTP_HX_REQUEST='true')


# -- The create page --


def test_the_create_page_is_login_only(client):
    assert client.get(reverse(NEW)).status_code == 302


def test_the_create_page_renders_the_form(owner_client):
    response = owner_client.get(reverse(NEW))
    content = response.content.decode()
    assert response.status_code == 200
    for text in (
        'Request files',
        'Title',
        'Message (optional)',
        'Anyone with the link',
        'Anyone signed in to ZipTrigo',
        'Only these people',
        'Max files',
        'Max total size (GB)',
        'Expiry',
        'Password (optional)',
        'Create the request',
    ):
        assert text in content
    assert 'Up to 100.' in content


def test_a_blocked_owner_gets_the_403_page(owner_client, funded_user):
    BlockedSender.objects.create(kind=BlockedSenderKind.EMAIL, value=funded_user.email.lower())
    assert owner_client.get(reverse(NEW)).status_code == 403
    response = _create(owner_client)
    assert response.status_code == 200 and response['HX-Redirect'] == reverse(NEW)
    assert not Transfer.objects.exists()


def test_a_blocked_ip_gets_the_403_page(owner_client):
    BlockedSender.objects.create(kind=BlockedSenderKind.IP, value='6.6.6.6')
    assert owner_client.get(reverse(NEW), HTTP_X_REAL_IP='6.6.6.6').status_code == 403


def test_creating_a_request_redirects_to_the_created_page(owner_client, funded_user):
    response = _create(owner_client, message='Hello', max_files='5', max_total_gb='1.5')

    transfer = Transfer.objects.get(kind=TransferKind.REQUEST)
    assert response['HX-Redirect'] == reverse('file_transfer:request-created', args=[transfer.id])
    assert transfer.owner == funded_user and transfer.title == 'Wedding photos'
    assert transfer.message == 'Hello' and transfer.max_files == 5
    assert transfer.max_total_size_bytes == 3 * 1024**3 // 2


def test_a_plain_post_without_htmx_redirects_too(owner_client):
    response = owner_client.post(
        reverse(NEW), {'title': 'x', 'upload_audience': 'anyone', 'expiry_choice': 'none'}
    )
    assert response.status_code == 302 and '/created/' in response['Location']


def test_the_created_page_shows_the_link_copy_and_qr(owner_client, make_request):
    transfer = make_request(upload_audience='selected', emails=['a@x.org', 'b@x.org'])
    response = owner_client.get(reverse('file_transfer:request-created', args=[transfer.id]))
    content = response.content.decode()
    assert response.status_code == 200
    assert transfer.absolute_download_url in content
    assert 'Copy link' in content and 'QR code' in content and 'data:image/png;base64,' in content
    assert 'We emailed 2 people the link.' in content
    assert reverse('file_transfer:dashboard') in content


def test_the_created_page_is_the_owners_only(client, make_request):
    transfer = make_request()
    client.force_login(UserFactory())
    assert (
        client.get(reverse('file_transfer:request-created', args=[transfer.id])).status_code == 404
    )


def test_the_created_page_is_not_for_a_send(owner_client, draft_transfer):
    url = reverse('file_transfer:request-created', args=[draft_transfer.id])
    assert owner_client.get(url).status_code == 404


@pytest.mark.parametrize(
    ('fields', 'text'),
    [
        ({'title': ''}, 'Title'),
        ({'title': 'x' * 101}, 'Keep the title to 100 characters or fewer.'),
        ({'upload_audience': 'selected', 'emails': ''}, 'Add at least one email address.'),
        ({'upload_audience': 'selected', 'emails': 'nonsense'}, 'is not a valid email address'),
        ({'max_files': '0'}, 'Max files'),
        ({'max_files': '101'}, 'Max files must be between 1 and 100.'),
        ({'max_total_gb': '21'}, 'Max total size must be at most 20.0 GB.'),
        ({'expiry_choice': 'custom'}, 'Provide a date for a custom expiry.'),
        ({'message': 'm' * 2001}, 'at most 2000 characters'),
    ],
)
def test_refusals_are_a_422_with_the_errors_partial(owner_client, fields, text):
    response = _create(owner_client, **fields)
    assert response.status_code == 422
    assert response['HX-Retarget'] == '#send-errors'
    assert text in response.content.decode()
    assert not Transfer.objects.exists()


def test_a_field_error_is_also_marked_for_the_field(owner_client):
    response = _create(owner_client, title='')
    assert 'data-field-error="title"' in response.content.decode()


def test_too_few_credits_is_a_422_with_the_message(client, user):
    client.force_login(user)
    response = _create(client)
    assert response.status_code == 422
    assert 'You need at least 1 credit to request files.' in response.content.decode()


def test_only_these_people_invites_them_and_charges_the_email_budget(
    owner_client, django_capture_on_commit_callbacks, sent_emails, settings
):
    settings.RATELIMIT_ENABLE = True
    from apps.core.ratelimit import limiter

    with django_capture_on_commit_callbacks(execute=True):
        _create(owner_client, upload_audience='selected', emails='a@x.org\nb@x.org, a@x.org')

    transfer = Transfer.objects.get()
    assert sorted(i.email for i in transfer.invites.all()) == ['a@x.org', 'b@x.org']
    assert sorted(e['to'] for e in sent_emails) == ['a@x.org', 'b@x.org']
    user_key = str(transfer.owner_id)
    assert limiter.peek(user_key, 'FT_RECIPIENT_EMAIL_USER').remaining == 28
    assert limiter.peek(user_key, 'FT_REQUEST_CREATE_USER').remaining == 29


def test_over_the_email_budget_nothing_is_created(owner_client, settings):
    settings.RATELIMIT_ENABLE = True
    settings.RATELIMIT_RULES = {**settings.RATELIMIT_RULES, 'FT_RECIPIENT_EMAIL_USER': (1, 3600)}
    response = _create(owner_client, upload_audience='selected', emails='a@x.org, b@x.org')
    assert response.status_code == 429 and 'Retry-After' in response
    assert 'try again in' in response.content.decode()
    assert not Transfer.objects.exists()


def test_the_create_rate_limit(owner_client, settings):
    settings.RATELIMIT_ENABLE = True
    settings.RATELIMIT_RULES = {**settings.RATELIMIT_RULES, 'FT_REQUEST_CREATE_USER': (1, 3600)}
    assert _create(owner_client).status_code == 200
    assert _create(owner_client).status_code == 429
    assert Transfer.objects.count() == 1


def test_a_refused_form_spends_no_create_budget(owner_client, settings, funded_user):
    settings.RATELIMIT_ENABLE = True
    from apps.core.ratelimit import limiter

    _create(owner_client, title='')
    assert limiter.peek(str(funded_user.pk), 'FT_REQUEST_CREATE_USER').remaining == 30


# -- The dashboard --


def test_the_dashboard_has_the_request_button_and_the_tabs(owner_client):
    content = owner_client.get(reverse('file_transfer:dashboard')).content.decode()
    assert 'Request files' in content and reverse(NEW) in content
    assert 'Send files' in content
    assert '?kind=send' in content and '?kind=request' in content
    assert 'filter=active&amp;kind=send' in content


def test_the_sent_tab_lists_sends_only_and_the_requests_tab_requests_only(
    owner_client, draft_transfer, make_request
):
    draft_transfer.status = TransferStatus.ACTIVE
    draft_transfer.completed_at = timezone.now()
    draft_transfer.save()
    make_request(title='Wedding photos')

    sent = owner_client.get(reverse('file_transfer:dashboard')).content.decode()
    assert 'Wedding photos' not in sent and 'transfer-row-' in sent

    requests_tab = owner_client.get(
        reverse('file_transfer:dashboard') + '?kind=request'
    ).content.decode()
    assert 'Wedding photos' in requests_tab
    assert f'transfer-row-{draft_transfer.id}' not in requests_tab


def test_the_list_partial_takes_the_kind(owner_client, make_request):
    make_request(title='Wedding photos')
    url = reverse('file_transfer:transfer-list')
    assert 'Wedding photos' in owner_client.get(f'{url}?kind=request').content.decode()
    assert 'Wedding photos' not in owner_client.get(url).content.decode()


def test_other_owners_requests_are_not_listed(owner_client, make_request):
    stranger = UserFactory()
    add_credits(stranger, 10, description='t', source='t')
    make_request(owner=stranger, title='Not mine')
    assert (
        'Not mine'
        not in owner_client.get(
            reverse('file_transfer:dashboard') + '?kind=request'
        ).content.decode()
    )


def test_the_requests_tab_empty_states(owner_client):
    url = reverse('file_transfer:dashboard')
    assert 'No requests yet' in owner_client.get(f'{url}?kind=request').content.decode()
    assert (
        'No ended requests' in owner_client.get(f'{url}?kind=request&filter=ended').content.decode()
    )


def test_an_ended_request_is_listed_under_ended(owner_client, make_request):
    transfer = make_request(title='Old one')
    Transfer.objects.filter(pk=transfer.pk).update(
        status=TransferStatus.DELETED, ended_at=timezone.now()
    )
    url = reverse('file_transfer:dashboard') + '?kind=request'
    assert 'Old one' not in owner_client.get(url).content.decode()
    assert 'Old one' in owner_client.get(f'{url}&filter=ended').content.decode()


def test_a_request_row_shows_its_state_and_numbers(
    owner_client, make_request, upload_to_request, anon_uploader, user_uploader
):
    transfer = make_request(title='Wedding photos', expiry_choice='5')
    upload_to_request(transfer, anon_uploader, 'a.jpg', 2048)
    upload_to_request(transfer, user_uploader, 'b.jpg', 2048)
    content = owner_client.get(
        reverse('file_transfer:dashboard') + '?kind=request'
    ).content.decode()

    assert 'Wedding photos' in content and 'Open' in content
    assert '2 files, 4.0\xa0KB' in content or '2 files, 4.0 KB' in content
    assert '2 uploaders' in content
    assert 'Expires:' in content and 'Anyone with the link' in content
    # The expanded panel: files with uploader, download, remove, download all, settings.
    assert 'Jane' in content and 'John (john@example.com)' in content
    assert 'Download all (.zip)' in content and 'Mark as done' in content


def test_a_done_row_shows_who_and_offers_reopen(
    owner_client, make_request, upload_to_request, anon_uploader
):
    from .. import services

    transfer = make_request()
    upload_to_request(transfer, anon_uploader, 'a.jpg', 10)
    services.requests.mark_done(transfer, by_name='Jane')
    content = owner_client.get(
        reverse('file_transfer:dashboard') + '?kind=request'
    ).content.decode()
    assert 'Done' in content and ' by Jane' in content
    assert 'Reopen' in content and 'Mark as done' not in content


def test_a_row_for_an_anonymous_unnamed_uploader_says_someone(
    owner_client, make_request, upload_to_request
):
    transfer = make_request()
    upload_to_request(
        transfer, Uploader(user=None, token_hash='d' * 64, name='', email=''), 'a', 10
    )
    assert (
        'Someone'
        in owner_client.get(reverse('file_transfer:dashboard') + '?kind=request').content.decode()
    )


# -- Row actions --


def _url(name, transfer, *extra):
    return reverse(f'file_transfer:{name}', args=[transfer.id, *extra])


def test_the_owner_marks_a_request_done_with_no_files_and_nobody_is_told(
    owner_client, funded_user, make_request, django_capture_on_commit_callbacks, sent_emails
):
    transfer = make_request()
    with django_capture_on_commit_callbacks(execute=True):
        response = owner_client.post(_url('request-done', transfer), HTTP_HX_REQUEST='true')

    assert response.status_code == 200 and 'Reopen' in response.content.decode()
    transfer.refresh_from_db()
    assert transfer.is_done and transfer.done_by_user == funded_user
    assert not Notification.objects.exists() and sent_emails == []


def test_reopen_from_the_row(owner_client, make_request):
    from .. import services

    transfer = make_request()
    services.requests.owner_mark_done(transfer, transfer.owner)
    response = owner_client.post(_url('request-reopen', transfer), HTTP_HX_REQUEST='true')
    assert response.status_code == 200 and 'Mark as done' in response.content.decode()
    transfer.refresh_from_db()
    assert transfer.is_open


def test_reopening_an_open_request_is_a_422_row(owner_client, make_request):
    response = owner_client.post(_url('request-reopen', make_request()), HTTP_HX_REQUEST='true')
    assert response.status_code == 422 and 'already open' in response.content.decode()


def test_actions_without_htmx_redirect_to_the_requests_tab_with_a_flash(owner_client, make_request):
    transfer = make_request()
    response = owner_client.post(_url('request-done', transfer))
    assert response.status_code == 302
    assert response['Location'] == reverse('file_transfer:dashboard') + '?kind=request'
    assert owner_client.post(_url('request-reopen', make_request(title='B'))).status_code == 302


def test_disable_reenable_and_delete_answer_a_requests_row(
    owner_client, make_request, fake_storage
):
    transfer = make_request(title='Wedding photos')
    disabled = owner_client.post(_url('disable', transfer), HTTP_HX_REQUEST='true')
    assert (
        'Re-enable' in disabled.content.decode() and 'Mark as done' not in disabled.content.decode()
    )
    enabled = owner_client.post(_url('reenable', transfer), HTTP_HX_REQUEST='true')
    assert 'Disable' in enabled.content.decode() and 'Mark as done' in enabled.content.decode()
    deleted = owner_client.post(_url('delete', transfer), HTTP_HX_REQUEST='true')
    assert deleted.status_code == 200
    transfer.refresh_from_db()
    assert transfer.status == TransferStatus.DELETED


def test_reenabling_a_request_needs_credits_like_a_transfer(
    client, user, make_request, funded_user
):
    transfer = make_request()
    Transfer.objects.filter(pk=transfer.pk).update(status=TransferStatus.SUSPENDED)
    from apps.billing.services import spend_credits

    spend_credits(funded_user, 100, description='x', source='t')
    client.force_login(funded_user)
    response = client.post(_url('reenable', transfer), HTTP_HX_REQUEST='true')
    assert response.status_code == 422 and 'one day of storage' in response.content.decode()


def test_settings_update_a_request(owner_client, make_request):
    transfer = make_request(upload_audience='selected', emails=['a@x.org'])
    response = owner_client.post(
        _url('request-settings', transfer),
        {
            'title': 'Renamed',
            'message': 'New message',
            'upload_audience': 'selected',
            'emails': 'a@x.org\nb@x.org',
            'max_files': '7',
            'max_total_gb': '2',
            'expiry_choice': '15',
            'password': 'pw',
        },
        HTTP_HX_REQUEST='true',
    )
    assert response.status_code == 200 and 'Renamed' in response.content.decode()
    transfer.refresh_from_db()
    assert (transfer.title, transfer.message, transfer.max_files) == ('Renamed', 'New message', 7)
    assert transfer.max_total_size_bytes == 2 * 1024**3
    assert transfer.expires_at is not None and transfer.password_hash
    assert sorted(i.email for i in transfer.invites.all()) == ['a@x.org', 'b@x.org']


def test_blank_limits_in_the_settings_clear_them(owner_client, make_request):
    transfer = make_request(max_files=5, max_total_size_bytes=1024)
    owner_client.post(
        _url('request-settings', transfer),
        {
            'title': 'x',
            'upload_audience': 'anyone',
            'expiry_choice': 'none',
            'max_files': '',
            'max_total_gb': '',
        },
        HTTP_HX_REQUEST='true',
    )
    transfer.refresh_from_db()
    assert transfer.max_files is None and transfer.max_total_size_bytes is None


def test_a_blank_password_keeps_it_and_remove_password_clears_it(owner_client, make_request):
    transfer = make_request(password='sekret')
    base = {'title': 'x', 'upload_audience': 'anyone', 'expiry_choice': 'none'}
    owner_client.post(
        _url('request-settings', transfer), {**base, 'password': ''}, HTTP_HX_REQUEST='true'
    )
    transfer.refresh_from_db()
    assert transfer.password_hash
    owner_client.post(
        _url('request-settings', transfer),
        {**base, 'remove_password': 'true'},
        HTTP_HX_REQUEST='true',
    )
    transfer.refresh_from_db()
    assert transfer.password_hash == ''


def test_settings_refuse_limits_below_what_is_uploaded(
    owner_client, make_request, upload_to_request, anon_uploader
):
    transfer = make_request()
    upload_to_request(transfer, anon_uploader, 'a', 10)
    upload_to_request(transfer, anon_uploader, 'b', 10)
    response = owner_client.post(
        _url('request-settings', transfer),
        {'title': 'x', 'upload_audience': 'anyone', 'expiry_choice': 'none', 'max_files': '1'},
        HTTP_HX_REQUEST='true',
    )
    assert response.status_code == 422
    assert 'Already 2 files uploaded; choose at least 2.' in response.content.decode()


def test_settings_validation_errors_are_a_422_row(owner_client, make_request):
    transfer = make_request()
    response = owner_client.post(
        _url('request-settings', transfer),
        {'title': '', 'upload_audience': 'anyone', 'expiry_choice': 'none'},
        HTTP_HX_REQUEST='true',
    )
    assert response.status_code == 422


def test_switching_to_selected_needs_an_address_in_the_settings(owner_client, make_request):
    transfer = make_request()
    response = owner_client.post(
        _url('request-settings', transfer),
        {'title': 'x', 'upload_audience': 'selected', 'emails': '', 'expiry_choice': 'none'},
        HTTP_HX_REQUEST='true',
    )
    assert (
        response.status_code == 422
        and 'Add at least one email address.' in response.content.decode()
    )


def test_the_owner_removes_a_file_whenever(
    owner_client, make_request, upload_to_request, anon_uploader, fake_storage
):
    transfer = make_request()
    file = upload_to_request(transfer, anon_uploader, 'a.jpg', 10)
    response = owner_client.post(
        _url('request-remove-file', transfer, file.id), HTTP_HX_REQUEST='true'
    )
    assert response.status_code == 200 and 'File removed.' in response.content.decode()
    assert not transfer.files.exists() and file.storage_key not in fake_storage.objects


def test_the_owner_cannot_touch_another_owners_file(
    owner_client, make_request, upload_to_request, anon_uploader
):
    stranger = UserFactory()
    add_credits(stranger, 10, description='t', source='t')
    other = make_request(owner=stranger)
    file = upload_to_request(other, anon_uploader, 'a.jpg', 10)
    assert owner_client.post(_url('request-remove-file', other, file.id)).status_code == 404
    mine = make_request()
    assert owner_client.post(_url('request-remove-file', mine, file.id)).status_code == 404


def test_resend_an_invite_from_the_row(owner_client, make_request, sent_emails):
    transfer = make_request(upload_audience='selected', emails=['ann@example.com'])
    invite = transfer.invites.get()
    invite.last_sent_at = None
    invite.save()
    response = owner_client.post(
        _url('request-resend-invite', transfer, invite.id), HTTP_HX_REQUEST='true'
    )
    assert response.status_code == 200
    assert 'Sent the email to ann@example.com again.' in response.content.decode()
    assert [e['to'] for e in sent_emails] == ['ann@example.com']


def test_resend_within_the_cooldown_is_a_422_row(owner_client, make_request):
    transfer = make_request(upload_audience='selected', emails=['ann@example.com'])
    invite = transfer.invites.get()
    response = owner_client.post(
        _url('request-resend-invite', transfer, invite.id), HTTP_HX_REQUEST='true'
    )
    assert response.status_code == 422 and 'emailed recently' in response.content.decode()


def test_resend_is_limited_by_the_shared_email_budget(owner_client, make_request, settings):
    settings.RATELIMIT_ENABLE = True
    settings.RATELIMIT_RULES = {**settings.RATELIMIT_RULES, 'FT_RECIPIENT_EMAIL_USER': (0, 3600)}
    transfer = make_request(upload_audience='selected', emails=['ann@example.com'])
    invite = transfer.invites.get()
    invite.last_sent_at = None
    invite.save()
    response = owner_client.post(
        _url('request-resend-invite', transfer, invite.id), HTTP_HX_REQUEST='true'
    )
    assert response.status_code == 429 and 'Retry-After' in response


def test_actions_on_another_users_request_are_a_404(client, make_request):
    transfer = make_request()
    client.force_login(UserFactory())
    for name in ('request-done', 'request-reopen', 'request-settings', 'disable', 'delete'):
        assert client.post(_url(name, transfer), {}).status_code == 404, name


def test_request_actions_need_login(make_request):
    transfer = make_request()
    for name in ('request-done', 'request-reopen', 'request-settings'):
        assert Client().post(_url(name, transfer)).status_code == 302


def test_transfer_only_actions_do_not_apply_to_a_request(owner_client, make_request):
    transfer = make_request()
    assert (
        owner_client.post(_url('update-settings', transfer), {'expiry_choice': 'none'}).status_code
        == 404
    )
    assert (
        owner_client.post(_url('add-recipients', transfer), {'recipients': 'a@x.org'}).status_code
        == 404
    )


def test_a_held_request_shows_the_hold_and_offers_no_manage(owner_client, make_request):
    transfer = make_request()
    Transfer.objects.filter(pk=transfer.pk).update(held_for_review_at=timezone.now())
    content = owner_client.get(
        reverse('file_transfer:dashboard') + '?kind=request'
    ).content.decode()
    assert 'on hold pending review' in content and 'Mark as done' not in content


def test_a_taken_down_request_shows_the_reason(owner_client, make_request):
    transfer = make_request()
    Transfer.objects.filter(pk=transfer.pk).update(
        status=TransferStatus.TAKEN_DOWN, takedown_reason='Not allowed', ended_at=timezone.now()
    )
    content = owner_client.get(
        reverse('file_transfer:dashboard') + '?kind=request&filter=ended'
    ).content.decode()
    assert 'This request was taken down: Not allowed' in content


def test_invited_people_are_listed_with_resend(owner_client, make_request):
    transfer = make_request(upload_audience='selected', emails=['ann@example.com'])
    content = owner_client.get(
        reverse('file_transfer:dashboard') + '?kind=request'
    ).content.decode()
    assert 'Invited people' in content and 'ann@example.com' in content and 'Resend' in content
    assert RequestInvite.objects.filter(transfer=transfer).count() == 1


def test_the_owner_downloads_a_file_through_the_link_while_the_request_is_open(
    owner_client, make_request, upload_to_request, anon_uploader
):
    transfer = make_request()
    file = upload_to_request(transfer, anon_uploader, 'a.jpg', 10)
    response = owner_client.get(reverse('t:download-file', args=[transfer.slug, file.id]))
    assert response.status_code == 302 and 'fake-s3' in response['Location']


def test_the_gb_input_filter_rounds_up():
    from ..templatetags.ft_ui import gb_input

    assert gb_input(None) == '' and gb_input(0) == ''
    assert gb_input(5 * 1024**3) == '5'
    assert gb_input(3 * 1024**3 // 2) == '1.5'
    assert gb_input(1) == '0.01'  # never below what is stored
