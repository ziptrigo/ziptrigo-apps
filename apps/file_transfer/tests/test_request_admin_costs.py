"""Staff and the Costs page for file requests (issue #306): the transfer admin shows a request's
kind, title, audience and done state, who uploaded each file and from which IP; a takedown ends a
request; the Costs page says what requests cost."""

import pytest
from django.urls import reverse

from apps.core.costs import CostSection

from .. import services
from ..models import Transfer, TransferStatus
from ..services.costs import cost_section

pytestmark = [pytest.mark.django_db, pytest.mark.unit]


@pytest.fixture(autouse=True)
def _fake_storage(fake_storage):
    return fake_storage


def _changelist(client):
    return reverse('custom_admin:file_transfer_transfer_changelist')


def test_the_changelist_shows_kind_title_audience_and_done(client, admin_user, make_request):
    transfer = make_request(title='Wedding photos', upload_audience='signed_in')
    services.requests.owner_mark_done(transfer, transfer.owner)
    client.force_login(admin_user)

    response = client.get(_changelist(client))

    assert 'Wedding photos' in response.content.decode()
    columns = list(response.context['cl'].list_display)
    for column in ('kind', 'title', 'upload_audience', 'done_at'):
        assert column in columns


def test_the_changelist_filters_by_kind(client, admin_user, draft_transfer, make_request):
    make_request(title='Wedding photos')
    draft_transfer.status = TransferStatus.ACTIVE
    draft_transfer.save()
    client.force_login(admin_user)

    requests_only = client.get(_changelist(client) + '?kind=request').content.decode()
    sends_only = client.get(_changelist(client) + '?kind=send').content.decode()

    assert 'Wedding photos' in requests_only and str(draft_transfer.id) not in requests_only
    assert 'Wedding photos' not in sends_only and str(draft_transfer.id) in sends_only


def test_the_changelist_searches_by_title(client, admin_user, make_request):
    make_request(title='Wedding photos')
    make_request(title='Tax documents')
    client.force_login(admin_user)
    content = client.get(_changelist(client) + '?q=Tax').content.decode()
    assert 'Tax documents' in content and 'Wedding photos' not in content


def test_the_detail_page_shows_the_uploaders_email_and_ip_to_staff(
    client, admin_user, make_request, upload_to_request, user_uploader, anon_uploader
):
    transfer = make_request(upload_audience='selected', emails=['invitee@example.com'])
    upload_to_request(transfer, user_uploader, 'a.jpg', 10, ip='203.0.113.7')
    upload_to_request(transfer, anon_uploader, 'b.jpg', 10, ip='198.51.100.9')
    client.force_login(admin_user)

    response = client.get(reverse('custom_admin:file_transfer_transfer_change', args=[transfer.id]))
    content = response.content.decode()

    assert response.status_code == 200 and 'name="_save"' not in content
    for text in (
        'Wedding photos',
        'selected',
        'john@example.com',
        '203.0.113.7',
        '198.51.100.9',
        'Jane',
        'invitee@example.com',
    ):
        assert text in content


def test_a_taken_down_request_refuses_uploads(
    client, admin_user, make_request, anon_uploader, fake_storage
):
    from django.core.exceptions import ValidationError

    from ..services import request_upload

    transfer = make_request()
    assert services.take_down_transfer(transfer, by=admin_user, reason='No')
    transfer.refresh_from_db()
    assert transfer.status == TransferStatus.TAKEN_DOWN
    with pytest.raises(ValidationError, match='not accepting files'):
        request_upload.add_file(transfer, anon_uploader, 'a', 10, storage=fake_storage)


def test_taking_down_a_request_deletes_its_files(
    admin_user, make_request, upload_to_request, anon_uploader, fake_storage
):
    transfer = make_request()
    file = upload_to_request(transfer, anon_uploader, 'a.jpg', 10)
    services.take_down_transfer(transfer, by=admin_user, reason='No')
    assert file.storage_key not in fake_storage.objects


def test_the_admin_take_down_action_applies_to_a_request(client, admin_user, make_request):
    transfer = make_request()
    client.force_login(admin_user)
    url = _changelist(client)
    data = {'action': 'take_down_action', '_selected_action': [str(transfer.id)]}
    response = client.post(url, data)
    assert response.status_code == 200  # the confirmation page
    response = client.post(
        url, {**data, 'apply': 'yes', 'reason': 'Not allowed', 'notify_sender': ''}
    )
    assert response.status_code == 302
    transfer.refresh_from_db()
    assert (
        transfer.status == TransferStatus.TAKEN_DOWN and transfer.takedown_reason == 'Not allowed'
    )


def test_the_costs_page_section_mentions_requests(funded_user, make_request):
    section = cost_section(funded_user)
    assert isinstance(section, CostSection)
    titles = [line.action for line in section.lines]
    assert 'Request files from others' in titles
    line = next(line for line in section.lines if line.action == 'Request files from others')
    assert 'Free to create' in line.note and "billed to you per day like a transfer's" in line.note


def test_the_costs_usage_counts_transfers_and_requests_together(
    funded_user, make_request, upload_to_request, anon_uploader
):
    transfer = make_request()
    upload_to_request(transfer, anon_uploader, 'a.jpg', 2 * 1024**3)
    Transfer.objects.create(
        owner=funded_user,
        status=TransferStatus.ACTIVE,
        size_bytes=1024**3,
        completed_at=transfer.completed_at,
    )
    (usage,) = [u for u in cost_section(funded_user).usage if 'active' in u]
    assert usage.startswith(
        'You have 2 active transfers and requests (3.0\xa0GB)'
    ) or usage.startswith('You have 2 active transfers and requests (3.0 GB)')


def test_costs_usage_for_a_single_request_and_none(funded_user, make_request):
    assert 'no active transfers or requests' in cost_section(funded_user).usage[0]
    make_request()
    assert cost_section(funded_user).usage[0].startswith('You have 1 active transfer or request')


def test_the_costs_page_renders_the_line(client, funded_user, make_request):
    make_request()
    client.force_login(funded_user)
    content = client.get(reverse('billing:costs')).content.decode()
    assert 'Request files from others' in content
