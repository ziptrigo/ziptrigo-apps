"""The public side of a file request's link (issue #306): `/t/<slug>/` as the upload page and the
download page, the gates, the JSON upload endpoints and "Mark as done"."""

import json
from datetime import timedelta

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from ..models import BlockedSender, BlockedSenderKind, Transfer, TransferFile, TransferStatus
from .support import new_user

pytestmark = [pytest.mark.django_db, pytest.mark.unit]

CHECKSUM = 'A' * 43 + '='


@pytest.fixture(autouse=True)
def _fake_storage(fake_storage):
    """Nothing here may reach S3."""


def _page(client, transfer):
    return client.get(reverse('t:download', args=[transfer.slug]))


def _post_json(client, url, payload=None, **extra):
    return client.post(
        url, data=json.dumps(payload or {}), content_type='application/json', **extra
    )


def _api(name, transfer, file=None):
    args = [transfer.slug] + ([file] if file else [])
    return reverse(f't:request-{name}', args=args)


def _upload(client, transfer, fake_storage, name='photo.jpg', size=100, **body):
    """What the browser does: add the file, ask for part URLs, PUT (simulated), complete."""
    added = _post_json(
        client,
        _api('add-file', transfer),
        {'name': name, 'size': size, 'client_last_modified': 1, **body},
    )
    assert added.status_code == 201, added.content
    file_id = added.json()['file_id']
    parts = _post_json(
        client,
        _api('part-urls', transfer, file_id),
        {'parts': [{'part_number': 1, 'checksum_sha256': CHECKSUM}]},
    )
    assert parts.status_code == 200 and '1' in parts.json()['urls']
    fake_storage.seed_object(TransferFile.objects.get(pk=file_id).storage_key, size)
    done = _post_json(
        client,
        _api('complete-file', transfer, file_id),
        {'parts': [{'PartNumber': 1, 'ETag': 'etag-1'}]},
    )
    assert done.status_code == 200, done.content
    return file_id


# -- The page --


def test_an_open_request_shows_the_upload_page(client, make_request):
    transfer = make_request(message='Photos <b>please</b>', expiry_choice='5')
    response = _page(client, transfer)

    assert response.status_code == 200
    content = response.content.decode()
    assert 'file_transfer/request_upload.html' in [t.name for t in response.templates]
    assert 'Wedding photos' in content
    assert 'is asking for files' in content
    assert 'Photos &lt;b&gt;please&lt;/b&gt;' in content
    assert 'Up to 100 files, 20.0 GB in total, 5.0 GB per file.' in content
    assert 'Available until' in content
    assert 'Your name (optional)' in content
    assert 'Mark as done' in content and 'Report this link' in content


def test_the_page_says_who_is_asking_never_their_email(client, funded_user, make_request):
    funded_user.name = 'Pat Planner'
    funded_user.save()
    content = _page(client, make_request()).content.decode()
    assert 'Pat Planner is asking for files' in content
    assert funded_user.email not in content


def test_an_owner_without_a_name_is_someone(client, funded_user, make_request):
    funded_user.name = ''
    funded_user.save()
    content = _page(client, make_request()).content.decode()
    assert 'Someone is asking for files' in content and funded_user.email not in content


def test_limits_shown_are_the_requests_own(client, make_request):
    transfer = make_request(max_files=3, max_total_size_bytes=2 * 1024**3)
    assert 'Up to 3 files, 2.0 GB in total' in _page(client, transfer).content.decode()


def test_signed_in_uploaders_see_no_name_field(client, make_request):
    transfer = make_request(upload_audience='signed_in')
    client.force_login(new_user())
    content = _page(client, transfer).content.decode()
    assert 'The owner will see your name and email next to your files.' in content
    assert 'Your name (optional)' not in content


def test_signed_in_visitors_of_an_anyone_request_are_identified_too(client, make_request):
    transfer = make_request()
    client.force_login(new_user())
    content = _page(client, transfer).content.decode()
    assert 'The owner will see your name and email' in content


def test_signed_in_modes_have_no_name_field_for_the_owner_either(client, funded_user, make_request):
    transfer = make_request(upload_audience='signed_in')
    client.force_login(funded_user)
    assert 'Your name (optional)' not in _page(client, transfer).content.decode()


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
    ],
)
def test_every_unavailable_state_is_the_same_neutral_page(client, make_request, change):
    transfer = make_request()
    Transfer.objects.filter(pk=transfer.pk).update(**change)
    response = _page(client, transfer)
    assert response.status_code == 404
    assert 'no longer available' in response.content.decode()
    assert 'Wedding photos' not in response.content.decode()


def test_a_password_protected_request_asks_first(client, make_request):
    transfer = make_request(password='sekret')
    response = _page(client, transfer)
    content = response.content.decode()
    assert response.status_code == 200
    assert 'password protected' in content.lower() and 'Mark as done' not in content

    # Wrong password: the same gate again, with the error.
    wrong = client.post(reverse('t:unlock', args=[transfer.slug]), {'password': 'nope'})
    assert wrong.status_code == 422 and 'Incorrect password.' in wrong.content.decode()
    assert 'Mark as done' not in wrong.content.decode()

    right = client.post(reverse('t:unlock', args=[transfer.slug]), {'password': 'sekret'})
    assert right.status_code == 302
    assert 'Mark as done' in _page(client, transfer).content.decode()


def test_the_owner_is_not_asked_for_the_password(client, funded_user, make_request):
    transfer = make_request(password='sekret')
    client.force_login(funded_user)
    assert 'Mark as done' in _page(client, transfer).content.decode()


def test_signed_in_audience_redirects_a_visitor_to_sign_in(client, make_request):
    transfer = make_request(upload_audience='signed_in')
    response = _page(client, transfer)
    assert response.status_code == 302
    assert response['Location'] == f'{reverse("accounts:login")}?next=/t/{transfer.slug}/'


def test_selected_audience_redirects_a_visitor_to_sign_in(client, make_request):
    transfer = make_request(upload_audience='selected', emails=['a@x.org'])
    assert _page(client, transfer)['Location'].startswith(reverse('accounts:login'))


def test_the_password_comes_before_the_sign_in_redirect(client, make_request):
    transfer = make_request(upload_audience='signed_in', password='sekret')
    assert _page(client, transfer).status_code == 200  # the unlock page, not a redirect
    client.post(reverse('t:unlock', args=[transfer.slug]), {'password': 'sekret'})
    assert _page(client, transfer).status_code == 302


def test_an_account_that_is_not_invited_is_told_without_names(client, make_request):
    transfer = make_request(upload_audience='selected', emails=['secret.invitee@example.com'])
    client.force_login(new_user(email='stranger@example.com', email_confirmed=True))
    response = _page(client, transfer)
    content = response.content.decode()
    assert response.status_code == 403
    assert 'This link is for specific people.' in content
    assert 'Sign in with the address it was sent to.' in content
    assert 'Sign out' in content and reverse('accounts:logout') in content
    assert 'secret.invitee' not in content and 'Wedding photos' not in content


def test_an_invited_but_unconfirmed_account_is_asked_to_confirm(client, make_request):
    transfer = make_request(upload_audience='selected', emails=['ann@example.com'])
    client.force_login(new_user(email='ann@example.com', email_confirmed=False))
    response = _page(client, transfer)
    content = response.content.decode()
    assert response.status_code == 403
    assert 'Confirm your email address to use this link.' in content
    assert reverse('accounts:resend-confirmation') in content
    assert 'Mark as done' not in content


def test_an_invited_confirmed_account_gets_the_upload_page(client, make_request):
    transfer = make_request(upload_audience='selected', emails=['Ann@Example.com'])
    client.force_login(new_user(email='ann@example.com', email_confirmed=True))
    assert 'Mark as done' in _page(client, transfer).content.decode()


def test_the_report_link_works_on_a_request(client, make_request):
    from ..models import AbuseReport

    transfer = make_request()
    response = client.post(
        reverse('t:report', args=[transfer.slug]), {'reason': 'other', 'details': 'bad'}
    )
    assert response.status_code == 200
    assert AbuseReport.objects.filter(transfer=transfer).count() == 1


# -- Uploading --


def test_an_anonymous_uploader_uploads_and_sees_only_their_own_files(
    client, make_request, fake_storage
):
    transfer = make_request()
    other = Client()
    mine = _upload(client, transfer, fake_storage, 'mine.jpg', uploader_name='Jane')
    _upload(other, transfer, fake_storage, 'theirs.jpg', uploader_name='Sam')

    own = _page(client, transfer).context['existing_files']
    assert [f['name'] for f in own] == ['mine.jpg'] and own[0]['id'] == mine and own[0]['uploaded']
    assert [f['name'] for f in _page(other, transfer).context['existing_files']] == ['theirs.jpg']
    assert 'theirs.jpg' not in _page(client, transfer).content.decode()

    stored = {f.name: f for f in transfer.files.all()}
    assert (
        stored['mine.jpg'].uploader_name == 'Jane' and stored['theirs.jpg'].uploader_name == 'Sam'
    )
    transfer.refresh_from_db()
    assert transfer.size_bytes == 200


def test_the_typed_name_is_remembered_in_the_session(client, make_request, fake_storage):
    transfer = make_request()
    _post_json(
        client, _api('add-file', transfer), {'name': 'a', 'size': 1, 'uploader_name': 'Jane'}
    )
    assert 'value="Jane"' in _page(client, make_request(title='Another')).content.decode()


def test_a_signed_in_uploader_is_stored_with_name_and_email(client, make_request, fake_storage):
    transfer = make_request(upload_audience='signed_in')
    account = new_user(name='John Doe', email='john@example.com')
    client.force_login(account)
    _upload(client, transfer, fake_storage, 'a.txt')
    file = transfer.files.get()
    assert file.uploader_user == account
    assert (file.uploader_name, file.uploader_email) == ('John Doe', 'john@example.com')
    assert file.uploader_token_hash == ''


def test_uploader_ip_is_recorded_from_the_trusted_header(client, make_request, fake_storage):
    transfer = make_request()
    added = _post_json(
        client, _api('add-file', transfer), {'name': 'a', 'size': 1}, HTTP_X_REAL_IP='7.7.7.7'
    )
    assert added.status_code == 201
    assert transfer.files.get().uploader_ip == '7.7.7.7'


def test_another_uploaders_file_is_a_404_on_every_endpoint(client, make_request, fake_storage):
    transfer = make_request()
    theirs = _upload(Client(), transfer, fake_storage, 'theirs.jpg')
    _post_json(
        client, _api('add-file', transfer), {'name': 'mine', 'size': 1}
    )  # a token of its own

    for name, payload in (
        ('part-urls', {'parts': [{'part_number': 1, 'checksum_sha256': CHECKSUM}]}),
        ('complete-file', {'parts': [{'PartNumber': 1, 'ETag': 'e'}]}),
        ('remove-file', {}),
        ('resume-file', {}),
    ):
        response = _post_json(client, _api(name, transfer, theirs), payload)
        assert response.status_code == 404, name
    assert transfer.files.filter(pk=theirs).exists()


def test_an_uploader_removes_their_own_file(client, make_request, fake_storage):
    transfer = make_request()
    file_id = _upload(client, transfer, fake_storage)
    key = TransferFile.objects.get(pk=file_id).storage_key

    assert _post_json(client, _api('remove-file', transfer, file_id)).status_code == 200

    assert not transfer.files.exists() and key not in fake_storage.objects
    transfer.refresh_from_db()
    assert transfer.size_bytes == 0


def test_resuming_an_interrupted_upload(client, make_request, fake_storage):
    transfer = make_request()
    added = _post_json(client, _api('add-file', transfer), {'name': 'big.bin', 'size': 100}).json()
    response = _post_json(client, _api('resume-file', transfer, added['file_id']))
    assert response.status_code == 200
    assert response.json()['uploaded_parts'] == [] and response.json()['restarted'] is False


def test_limits_are_enforced_by_the_endpoint_with_messages_naming_the_request(
    client, make_request, fake_storage
):
    transfer = make_request(max_files=1)
    _upload(client, transfer, fake_storage)
    response = _post_json(client, _api('add-file', transfer), {'name': 'b', 'size': 1})
    assert response.status_code == 422
    assert response.json()['error'] == 'This request accepts at most 1 file.'


def test_bad_bodies_are_422(client, make_request):
    transfer = make_request()
    assert (
        _post_json(client, _api('add-file', transfer), {'name': '', 'size': 1}).status_code == 422
    )
    assert (
        _post_json(client, _api('add-file', transfer), {'name': 'a', 'size': 'x'}).status_code
        == 422
    )
    assert (
        client.post(
            _api('add-file', transfer), 'not json', content_type='application/json'
        ).status_code
        == 422
    )
    assert client.get(_api('add-file', transfer)).status_code == 405


def test_uploads_are_refused_for_an_unavailable_or_unknown_link(client, make_request):
    transfer = make_request()
    Transfer.objects.filter(pk=transfer.pk).update(status=TransferStatus.DISABLED)
    assert (
        _post_json(client, _api('add-file', transfer), {'name': 'a', 'size': 1}).status_code == 404
    )
    unknown = reverse('t:request-add-file', args=['nope'])
    assert _post_json(client, unknown, {'name': 'a', 'size': 1}).status_code == 404


def test_uploads_to_a_send_link_are_refused(client, draft_transfer):
    draft_transfer.status = TransferStatus.ACTIVE
    draft_transfer.save()
    url = reverse('t:request-add-file', args=[draft_transfer.slug])
    assert _post_json(client, url, {'name': 'a', 'size': 1}).status_code == 404


@pytest.mark.parametrize(
    ('audience', 'extra'),
    [('signed_in', {}), ('selected', {'emails': ['a@x.org']})],
)
def test_upload_endpoints_enforce_the_audience(client, make_request, audience, extra):
    transfer = make_request(upload_audience=audience, **extra)
    response = _post_json(client, _api('add-file', transfer), {'name': 'a', 'size': 1})
    assert response.status_code == 403 and response.json()['access'] == 'needs_sign_in'

    client.force_login(new_user(email='stranger@example.com', email_confirmed=True))
    expected = 403 if audience == 'selected' else 201
    assert (
        _post_json(client, _api('add-file', transfer), {'name': 'a', 'size': 1}).status_code
        == expected
    )


def test_upload_endpoints_enforce_the_password(client, make_request):
    transfer = make_request(password='sekret')
    response = _post_json(client, _api('add-file', transfer), {'name': 'a', 'size': 1})
    assert response.status_code == 403 and response.json()['access'] == 'needs_password'


def test_a_blocked_ip_is_refused_with_the_neutral_message(client, make_request):
    BlockedSender.objects.create(kind=BlockedSenderKind.IP, value='6.6.6.6')
    response = _post_json(
        client, _api('add-file', make_request()), {'name': 'a', 'size': 1}, HTTP_X_REAL_IP='6.6.6.6'
    )
    assert response.status_code == 403
    assert response.json()['error'] == "Sorry, you can't send transfers at this time."


def test_a_blocked_account_email_is_refused(client, make_request):
    BlockedSender.objects.create(kind=BlockedSenderKind.EMAIL, value='bad@example.com')
    transfer = make_request(upload_audience='signed_in')
    client.force_login(new_user(email='bad@example.com'))
    assert (
        _post_json(client, _api('add-file', transfer), {'name': 'a', 'size': 1}).status_code == 403
    )


def test_the_ip_rate_limit_applies_to_every_upload_call(client, make_request, settings):
    settings.RATELIMIT_ENABLE = True
    settings.RATELIMIT_RULES = {**settings.RATELIMIT_RULES, 'FT_REQUEST_UPLOAD_IP': (2, 60)}
    transfer = make_request()
    for _ in range(2):
        assert (
            _post_json(client, _api('add-file', transfer), {'name': 'a', 'size': 1}).status_code
            == 201
        )
    response = _post_json(client, _api('add-file', transfer), {'name': 'a', 'size': 1})
    assert response.status_code == 429 and 'Retry-After' in response


def test_the_per_request_rate_limit_counts_across_ips(make_request, settings):
    settings.RATELIMIT_ENABLE = True
    settings.RATELIMIT_RULES = {**settings.RATELIMIT_RULES, 'FT_REQUEST_UPLOAD_TRANSFER': (2, 3600)}
    transfer = make_request()
    codes = [
        _post_json(
            Client(),
            _api('add-file', transfer),
            {'name': 'a', 'size': 1},
            HTTP_X_REAL_IP=f'9.9.9.{n}',
        ).status_code
        for n in range(3)
    ]
    assert codes == [201, 201, 429]


def test_a_made_up_slug_creates_no_per_request_counter(client, settings):
    from apps.core.ratelimit import limiter

    settings.RATELIMIT_ENABLE = True
    _post_json(client, reverse('t:request-add-file', args=['nope']), {'name': 'a', 'size': 1})
    assert limiter.peek('nope', 'FT_REQUEST_UPLOAD_TRANSFER').remaining == 600


def test_a_signed_in_uploader_also_spends_the_shared_upload_budget(make_request, settings):
    from apps.core.ratelimit import limiter

    settings.RATELIMIT_ENABLE = True
    transfer = make_request(upload_audience='signed_in')
    account = new_user()
    client = Client()
    client.force_login(account)
    _post_json(client, _api('add-file', transfer), {'name': 'a', 'size': 1})
    assert limiter.peek(str(account.pk), 'FT_UPLOAD_USER').remaining == 119


# -- Mark as done --


def test_an_uploader_marks_done_and_the_page_switches_to_the_download_view(
    client, funded_user, make_request, fake_storage, django_capture_on_commit_callbacks, sent_emails
):
    transfer = make_request()
    _upload(client, transfer, fake_storage, 'photo.jpg')

    with django_capture_on_commit_callbacks(execute=True):
        response = client.post(_api('done', transfer), {'name': 'Jane'})

    assert response.status_code == 302 and response['Location'] == reverse(
        't:download', args=[transfer.slug]
    )
    transfer.refresh_from_db()
    assert transfer.is_done and transfer.done_by_name == 'Jane'
    assert [e['to'] for e in sent_emails] == [funded_user.email]

    page = client.get(response['Location'], follow=True)
    content = page.content.decode()
    assert 'Thanks, ' in content and 'has been told.' in content
    # The download view: the file list (names and sizes only, no uploader) and the zip not shown
    # for a single file.
    assert 'photo.jpg' in content and 'Mark as done' not in content
    assert 'Jane' not in content.split('has been told')[-1]


def test_done_through_htmx_redirects_the_whole_page(client, make_request, fake_storage):
    transfer = make_request()
    _upload(client, transfer, fake_storage)
    response = client.post(_api('done', transfer), {'name': ''}, HTTP_HX_REQUEST='true')
    assert response.status_code == 200
    assert response['HX-Redirect'] == reverse('t:download', args=[transfer.slug])


def test_done_with_no_files_is_refused_with_a_message(client, make_request):
    transfer = make_request()
    response = client.post(_api('done', transfer), HTTP_HX_REQUEST='true')
    assert response.status_code == 422
    assert response['HX-Retarget'] == '#request-msg'
    assert 'Upload at least one file first.' in response.content.decode()


def test_anyone_with_access_may_mark_done_even_without_uploading(
    client, make_request, fake_storage
):
    transfer = make_request()
    _upload(Client(), transfer, fake_storage)
    assert client.post(_api('done', transfer)).status_code == 302
    transfer.refresh_from_db()
    assert transfer.is_done


def test_done_by_a_signed_in_uploader_records_the_account(client, make_request, fake_storage):
    transfer = make_request(upload_audience='signed_in')
    account = new_user(name='Sam')
    client.force_login(account)
    _upload(client, transfer, fake_storage)
    client.post(_api('done', transfer))
    transfer.refresh_from_db()
    assert transfer.done_by_user == account and transfer.done_by_name == ''


def test_done_again_is_answered_as_success(client, make_request, fake_storage):
    transfer = make_request()
    _upload(client, transfer, fake_storage)
    client.post(_api('done', transfer))
    assert client.post(_api('done', transfer)).status_code == 302


def test_done_respects_the_audience_and_the_password(client, make_request, fake_storage):
    transfer = make_request(upload_audience='signed_in')
    assert client.post(_api('done', transfer)).status_code == 302  # to sign in
    htmx = client.post(_api('done', transfer), HTTP_HX_REQUEST='true')
    assert htmx['HX-Redirect'].startswith(reverse('accounts:login'))

    locked = make_request(title='Locked', password='sekret')
    response = client.post(_api('done', locked), HTTP_HX_REQUEST='true')
    assert response.status_code == 204 and response['HX-Refresh'] == 'true'


def test_done_is_post_only_and_neutral_for_unknown_links(client, make_request):
    transfer = make_request()
    assert client.get(_api('done', transfer)).status_code == 405
    assert client.post(reverse('t:request-done', args=['nope'])).status_code == 404


def test_the_owner_marking_done_from_the_link_gets_a_plain_message(
    client, funded_user, make_request
):
    transfer = make_request()
    client.force_login(funded_user)
    response = client.post(_api('done', transfer), follow=True)
    assert 'Marked as done.' in response.content.decode()


def test_nothing_can_be_uploaded_after_done(client, make_request, fake_storage):
    transfer = make_request()
    _upload(client, transfer, fake_storage)
    client.post(_api('done', transfer))
    response = _post_json(client, _api('add-file', transfer), {'name': 'late', 'size': 1})
    assert response.status_code == 422
    assert response.json()['error'] == 'This link is not accepting files right now.'


# -- Downloading after done --


def _done_request(make_request, fake_storage, **options):
    transfer = make_request(**options)
    uploader = Client()
    if options.get('password'):
        uploader.post(reverse('t:unlock', args=[transfer.slug]), {'password': options['password']})
    if options.get('upload_audience', 'anyone') != 'anyone':
        uploader.force_login(new_user(email='up@example.com', email_confirmed=True))
    _upload(uploader, transfer, fake_storage, 'photo.jpg', uploader_name='Jane')
    uploader.post(_api('done', transfer))
    transfer.refresh_from_db()
    return transfer, transfer.files.get()


def test_downloading_after_done_for_anyone_with_the_link(client, make_request, fake_storage):
    transfer, file = _done_request(make_request, fake_storage)
    page = _page(client, transfer)
    assert page.status_code == 200 and 'photo.jpg' in page.content.decode()

    response = client.get(reverse('t:download-file', args=[transfer.slug, file.id]))
    assert response.status_code == 302 and file.storage_key in response['Location']
    from ..models import DownloadEvent

    assert DownloadEvent.objects.filter(transfer=transfer, file=file).exists()


def test_uploader_names_are_shown_only_to_the_owner(
    client, funded_user, make_request, fake_storage
):
    transfer, _ = _done_request(make_request, fake_storage)
    assert 'Jane' not in _page(client, transfer).content.decode()
    client.force_login(funded_user)
    assert 'Jane' in _page(client, transfer).content.decode()


def test_a_download_before_done_goes_back_to_the_page_except_for_the_owner(
    client, funded_user, make_request, fake_storage
):
    transfer = make_request()
    file_id = _upload(Client(), transfer, fake_storage)
    url = reverse('t:download-file', args=[transfer.slug, file_id])

    response = client.get(url)
    assert response.status_code == 302 and response['Location'] == reverse(
        't:download', args=[transfer.slug]
    )

    client.force_login(funded_user)
    assert client.get(url).status_code == 302
    assert 'fake-s3' in client.get(url)['Location']


def test_download_after_done_follows_the_audience(client, make_request, fake_storage):
    transfer, file = _done_request(make_request, fake_storage, upload_audience='signed_in')
    url = reverse('t:download-file', args=[transfer.slug, file.id])

    assert _page(client, transfer)['Location'].startswith(reverse('accounts:login'))
    assert client.get(url)['Location'] == reverse('t:download', args=[transfer.slug])

    client.force_login(new_user())
    assert 'fake-s3' in client.get(url)['Location']


def test_download_after_done_follows_the_invite_list(client, make_request, fake_storage):
    transfer, file = _done_request(
        make_request,
        fake_storage,
        upload_audience='selected',
        emails=['up@example.com', 'dl@example.com'],
    )
    url = reverse('t:download-file', args=[transfer.slug, file.id])

    stranger = Client()
    stranger.force_login(new_user(email='stranger@example.com', email_confirmed=True))
    assert _page(stranger, transfer).status_code == 403
    assert stranger.get(url)['Location'] == reverse('t:download', args=[transfer.slug])

    invited = Client()
    invited.force_login(new_user(email='dl@example.com', email_confirmed=True))
    assert 'photo.jpg' in _page(invited, transfer).content.decode()
    assert 'fake-s3' in invited.get(url)['Location']


def test_download_after_done_follows_the_password(client, make_request, fake_storage):
    transfer, file = _done_request(make_request, fake_storage, password='sekret')
    url = reverse('t:download-file', args=[transfer.slug, file.id])

    assert 'password protected' in _page(client, transfer).content.decode().lower()
    assert 'photo.jpg' not in _page(client, transfer).content.decode()
    assert client.get(url)['Location'] == reverse('t:download', args=[transfer.slug])

    client.post(reverse('t:unlock', args=[transfer.slug]), {'password': 'sekret'})
    assert 'photo.jpg' in _page(client, transfer).content.decode()
    assert 'fake-s3' in client.get(url)['Location']


def test_zip_after_done_for_a_request(client, make_request, fake_storage):
    transfer = make_request()
    for name in ('a.txt', 'b.txt'):
        _upload(Client(), transfer, fake_storage, name)
    Client().post(_api('done', transfer))

    status = client.get(reverse('t:zip-status', args=[transfer.slug]))
    assert status.status_code == 200
    transfer.refresh_from_db()
    assert transfer.zip_status != 'none'


def test_zip_is_refused_before_done_except_for_the_owner(
    client, funded_user, make_request, fake_storage
):
    transfer = make_request()
    _upload(Client(), transfer, fake_storage)
    assert client.get(reverse('t:zip-status', args=[transfer.slug])).status_code == 302
    client.force_login(funded_user)
    assert client.get(reverse('t:zip-status', args=[transfer.slug])).status_code == 200


def test_the_zip_area_id_is_validated(client, funded_user, make_request, fake_storage):
    transfer = make_request()
    _upload(Client(), transfer, fake_storage)
    client.force_login(funded_user)
    url = reverse('t:zip-status', args=[transfer.slug])
    ok = client.get(f'{url}?area=zip-area-{transfer.id}').content.decode()
    assert f'id="zip-area-{transfer.id}"' in ok
    bad = client.get(f'{url}?area="><script>').content.decode()
    assert '<script>' not in bad and 'id="zip-area"' in bad


def test_a_password_and_unavailable_states_use_the_same_unlock_view_for_requests(
    client, make_request
):
    transfer = make_request(password='sekret')
    Transfer.objects.filter(pk=transfer.pk).update(status=TransferStatus.DISABLED)
    response = client.post(reverse('t:unlock', args=[transfer.slug]), {'password': 'sekret'})
    assert response.status_code == 404


def test_unlock_rate_limit_still_applies_to_requests(client, make_request, settings):
    settings.RATELIMIT_ENABLE = True
    settings.RATELIMIT_RULES = {**settings.RATELIMIT_RULES, 'FT_UNLOCK_IP': (1, 600)}
    transfer = make_request(password='sekret')
    url = reverse('t:unlock', args=[transfer.slug])
    assert client.post(url, {'password': 'x'}).status_code == 422
    assert client.post(url, {'password': 'x'}).status_code == 429


def test_a_send_transfers_page_is_unchanged(client, draft_transfer, uploaded_file):
    draft_transfer.status = TransferStatus.ACTIVE
    draft_transfer.save()
    response = _page(client, draft_transfer)
    assert 'file_transfer/download.html' in [t.name for t in response.templates]
    assert uploaded_file.name in response.content.decode()
