"""The optional title of a send and of a request (issue #318): the services, the send and request
forms, the pages, the emails (a title is never in a subject) and the JWT API."""

import json
import re

import pytest
from django.core.exceptions import ValidationError
from django.urls import reverse

from apps.accounts.tokens import CustomAccessToken

from ..models import Transfer, TransferStatus
from ..services import limits
from ..services.anonymous import AnonymousSendOptions, start_confirmation
from ..services.requests import RequestOptions, create_request
from ..services.send import SendOptions, finalize_send

pytestmark = [pytest.mark.django_db, pytest.mark.unit]


def _title_input(html: str) -> str:
    match = re.search(r'<input[^>]*id="id_title"[^>]*>', html)
    assert match
    return match.group(0)


def _send(draft, **options) -> Transfer:
    return finalize_send(draft, SendOptions(recipients=['a@example.com'], **options))


# -- limits.clean_title --


@pytest.mark.parametrize(
    ('raw', 'expected'),
    [
        ('', ''),
        (None, ''),
        ('   ', ''),
        ('\x00\x07', ''),
        ('  Q3\x00 report  ', 'Q3 report'),
        ('x' * 100, 'x' * 100),
    ],
)
def test_clean_title_is_lenient(raw, expected):
    assert limits.clean_title(raw) == expected


def test_clean_title_refuses_over_100_characters():
    with pytest.raises(ValidationError) as excinfo:
        limits.clean_title('x' * 101)
    assert excinfo.value.message_dict['title'] == ['Keep the title to 100 characters or fewer.']


# -- The model --


def test_display_name_prefers_the_title(draft_transfer, uploaded_file):
    assert draft_transfer.display_name == 'report.pdf'
    draft_transfer.title = 'Q3 report'
    assert draft_transfer.display_name == 'Q3 report'
    assert draft_transfer.file_label == 'report.pdf'


def test_display_name_falls_back_without_a_title_or_files(draft_transfer):
    assert draft_transfer.display_name == 'Untitled transfer'
    draft_transfer.title = 'Hello'
    assert draft_transfer.display_name == 'Hello'


# -- Sending --


def test_finalize_send_stores_a_cleaned_title(draft_transfer, uploaded_file):
    _send(draft_transfer, title='  Q3\x00 report ')
    draft_transfer.refresh_from_db()
    assert draft_transfer.title == 'Q3 report'
    assert draft_transfer.display_name == 'Q3 report'


def test_finalize_send_without_a_title_keeps_the_file_name(draft_transfer, uploaded_file):
    _send(draft_transfer)
    draft_transfer.refresh_from_db()
    assert draft_transfer.title == ''
    assert draft_transfer.display_name == 'report.pdf'


def test_finalize_send_refuses_a_title_over_100_characters(draft_transfer, uploaded_file):
    with pytest.raises(ValidationError):
        _send(draft_transfer, title='x' * 101)
    draft_transfer.refresh_from_db()
    assert draft_transfer.status == TransferStatus.DRAFT


def test_the_send_form_posts_a_title(client, draft_transfer, uploaded_file):
    client.force_login(draft_transfer.owner)
    response = client.post(
        reverse('file_transfer:send-submit', args=[draft_transfer.id]),
        data={'recipients': '', 'title': 'Holiday photos', 'expiry_choice': 'none'},
        HTTP_HX_REQUEST='true',
    )
    assert response.status_code == 200
    draft_transfer.refresh_from_db()
    assert draft_transfer.title == 'Holiday photos'

    sent = client.get(reverse('file_transfer:sent', args=[draft_transfer.id])).content.decode()
    assert 'id="sent-title"' in sent and 'Holiday photos' in sent
    dashboard = client.get(reverse('file_transfer:dashboard')).content.decode()
    assert 'Holiday photos' in dashboard


def test_the_send_form_refuses_a_long_title(client, draft_transfer, uploaded_file):
    client.force_login(draft_transfer.owner)
    response = client.post(
        reverse('file_transfer:send-submit', args=[draft_transfer.id]),
        data={'recipients': '', 'title': 'x' * 101, 'expiry_choice': 'none'},
        HTTP_HX_REQUEST='true',
    )
    assert response.status_code == 422
    draft_transfer.refresh_from_db()
    assert draft_transfer.status == TransferStatus.DRAFT


def test_the_send_page_has_an_optional_title_field(client, funded_user):
    client.force_login(funded_user)
    html = client.get(reverse('file_transfer:send')).content.decode()
    assert 'Title (optional)' in html
    field = _title_input(html)
    assert 'required' not in field and 'maxlength="100"' in field


def test_the_download_page_shows_the_title(client, draft_transfer, uploaded_file):
    _send(draft_transfer, title='Holiday <b>photos</b>')
    html = client.get(reverse('t:download', args=[draft_transfer.slug])).content.decode()
    assert '<h1' in html and 'Holiday &lt;b&gt;photos&lt;/b&gt;' in html
    assert '<b>photos</b>' not in html


# -- Emails: the title is in the bodies, never in a subject --


def test_a_title_stays_out_of_every_subject(
    draft_transfer, uploaded_file, sent_emails, django_capture_on_commit_callbacks
):
    with django_capture_on_commit_callbacks(execute=True):
        _send(draft_transfer, title='Secret plans')
    assert len(sent_emails) == 2
    for email in sent_emails:
        assert 'Secret plans' not in email['subject']
        assert 'report.pdf' in email['subject']

    recipient = next(e for e in sent_emails if e['to'] == 'a@example.com')
    assert recipient['text_body'].startswith('Title: Secret plans\n')
    sender = next(e for e in sent_emails if e['to'] != 'a@example.com')
    assert 'Your transfer "Secret plans"' in sender['text_body']


def test_the_later_emails_keep_the_title_out_of_subjects_too(
    draft_transfer, uploaded_file, sent_emails
):
    from ..services import emails

    transfer = _send(draft_transfer, title='Secret plans')
    sent_emails.clear()
    emails.send_expires_soon_notification.call(str(transfer.id))
    emails.send_files_deleted_notification.call(str(transfer.id))
    emails.send_suspended_notification.call(str(transfer.id))
    emails.send_download_notification.call(str(transfer.id), None)
    assert len(sent_emails) == 4
    for email in sent_emails:
        assert 'Secret plans' not in email['subject']
    assert any('"Secret plans"' in e['text_body'] for e in sent_emails)


def test_a_transfer_without_a_title_emails_as_before(
    draft_transfer, uploaded_file, sent_emails, django_capture_on_commit_callbacks
):
    with django_capture_on_commit_callbacks(execute=True):
        _send(draft_transfer)
    recipient = next(e for e in sent_emails if e['to'] == 'a@example.com')
    assert 'Title:' not in recipient['text_body']
    assert recipient['subject'] == 'report.pdf was sent to you via ZipTrigo'


# -- Anonymous sending --


def test_an_anonymous_send_can_have_a_title(anon_enabled, fake_storage, monkeypatch):
    from .test_anonymous_send import _capture_email, _draft, _upload

    _capture_email(monkeypatch)
    transfer = _draft(anon_enabled)
    _upload(transfer, fake_storage)
    result = start_confirmation(
        transfer,
        AnonymousSendOptions(
            sender_email='sender@example.com',
            recipients=['r@example.com'],
            title=' Holiday ',
            expiry_choice='1',
        ),
    )
    assert result.title == 'Holiday'


# -- Requests: the title is optional --


def test_a_request_without_a_title_is_called_untitled(funded_user):
    transfer = create_request(funded_user, RequestOptions())
    assert transfer.title == ''
    assert transfer.display_name == 'Untitled request'


def test_the_request_form_does_not_need_a_title(client, funded_user):
    client.force_login(funded_user)
    response = client.post(
        reverse('file_transfer:request-new'),
        data={'title': '', 'upload_audience': 'anyone', 'expiry_choice': 'none'},
    )
    assert response.status_code == 302, response.content.decode()[:500]
    transfer = Transfer.objects.get(owner=funded_user)
    assert transfer.title == ''

    page = client.get(reverse('file_transfer:request-new')).content.decode()
    assert 'Title (optional)' in page
    assert 'required' not in _title_input(page)


def test_the_pages_of_an_untitled_request(client, make_request, funded_user):
    transfer = make_request(title='')
    # The public upload page names the owner's request neutrally.
    html = client.get(reverse('t:download', args=[transfer.slug])).content.decode()
    assert 'Untitled request' not in html
    assert '<h1' in html and 'Upload files' in html

    client.force_login(funded_user)
    dashboard = client.get(reverse('file_transfer:dashboard') + '?kind=request').content.decode()
    assert 'Untitled request' in dashboard
    assert 'name="title" maxlength="100" value=""' in dashboard
    assert 'required' not in dashboard.split('name="title" maxlength="100"')[1].split('>')[0]


def test_the_request_settings_form_can_clear_the_title(client, make_request, funded_user):
    transfer = make_request(title='Photos')
    client.force_login(funded_user)
    response = client.post(
        reverse('file_transfer:request-settings', args=[transfer.id]),
        data={
            'title': '',
            'message': '',
            'upload_audience': 'anyone',
            'expiry_choice': 'none',
        },
        HTTP_HX_REQUEST='true',
    )
    assert response.status_code == 200, response.content.decode()[:500]
    transfer.refresh_from_db()
    assert transfer.title == ''


def test_the_invite_emails_of_an_untitled_request(make_request, sent_emails):
    from ..services import emails

    transfer = make_request(title='', upload_audience='selected', emails=['x@example.org'])
    sent_emails.clear()
    emails.send_request_invite.call(str(transfer.id), 'x@example.org')
    assert sent_emails and 'Untitled' not in sent_emails[0]['text_body']
    assert 'Untitled' not in sent_emails[0]['html_body']
    assert sent_emails[0]['subject'] == "You've been asked to upload files"


def test_the_invite_email_of_a_titled_request_escapes_the_title(make_request, sent_emails):
    from ..services import emails

    transfer = make_request(
        title='<i>Photos</i>', upload_audience='selected', emails=['x@example.org']
    )
    sent_emails.clear()
    emails.send_request_invite.call(str(transfer.id), 'x@example.org')
    assert '&lt;i&gt;Photos&lt;/i&gt;' in sent_emails[0]['html_body']
    assert '<i>Photos</i>' not in sent_emails[0]['html_body']
    assert '<i>Photos</i>' not in sent_emails[0]['subject']


# -- API --


@pytest.fixture
def auth_headers(funded_user) -> dict[str, str]:
    return {'HTTP_AUTHORIZATION': f'Bearer {CustomAccessToken.for_user(funded_user)}'}


def _post(client, url, payload, headers):
    return client.post(url, data=json.dumps(payload), content_type='application/json', **headers)


def test_the_api_sends_with_a_title(client, draft_transfer, uploaded_file, auth_headers):
    response = _post(
        client,
        f'/api/ft/transfers/{draft_transfer.id}/send',
        {'recipients': [], 'title': 'Q3 report'},
        auth_headers,
    )
    assert response.status_code == 200, response.content
    body = response.json()
    assert body['title'] == 'Q3 report' and body['display_name'] == 'Q3 report'

    listed = client.get('/api/ft/transfers/', **auth_headers).json()['results']
    assert listed[0]['title'] == 'Q3 report'


def test_the_api_sends_without_a_title(client, draft_transfer, uploaded_file, auth_headers):
    response = _post(
        client, f'/api/ft/transfers/{draft_transfer.id}/send', {'recipients': []}, auth_headers
    )
    assert response.status_code == 200
    assert response.json()['title'] == '' and response.json()['display_name'] == 'report.pdf'


def test_the_api_refuses_a_long_send_title(client, draft_transfer, uploaded_file, auth_headers):
    response = _post(
        client,
        f'/api/ft/transfers/{draft_transfer.id}/send',
        {'recipients': [], 'title': 'x' * 101},
        auth_headers,
    )
    assert response.status_code == 400
    assert 'Keep the title' in response.json()['detail']
    draft_transfer.refresh_from_db()
    assert draft_transfer.status == TransferStatus.DRAFT


def test_the_api_creates_a_request_without_a_title(client, auth_headers):
    response = _post(client, '/api/ft/requests', {}, auth_headers)
    assert response.status_code == 201, response.content
    assert response.json()['title'] == ''


def test_the_api_clears_a_request_title(client, make_request, auth_headers):
    transfer = make_request(title='Photos')
    response = client.patch(
        f'/api/ft/requests/{transfer.id}',
        data=json.dumps({'title': ''}),
        content_type='application/json',
        **auth_headers,
    )
    assert response.status_code == 200, response.content
    assert response.json()['title'] == ''
