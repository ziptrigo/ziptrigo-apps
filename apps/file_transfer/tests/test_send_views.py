import pytest
from django.urls import reverse

from apps.billing.services import get_balance, spend_credits

from ..models import Transfer, TransferStatus

pytestmark = [pytest.mark.django_db, pytest.mark.unit]


def test_send_page_requires_login(client):
    response = client.get(reverse('file_transfer:send'))
    assert response.status_code == 302


def test_send_page_creates_a_draft(client, funded_user):
    client.force_login(funded_user)
    response = client.get(reverse('file_transfer:send'))
    assert response.status_code == 200
    assert Transfer.objects.filter(owner=funded_user, status=TransferStatus.DRAFT).exists()


def test_send_page_reuses_existing_empty_draft_across_visits(client, funded_user):
    """Regression test: `send_page` used to call `create_draft` unconditionally, so reloading it
    (or just leaving the tab open and coming back) left a new "Untitled transfer" row on the
    dashboard every time (`_transfers_for` filters those out too, but only after `cleanup_drafts`
    finally deletes the stale ones up to 24h later)."""
    client.force_login(funded_user)

    client.get(reverse('file_transfer:send'))
    client.get(reverse('file_transfer:send'))

    assert Transfer.objects.filter(owner=funded_user, status=TransferStatus.DRAFT).count() == 1


def test_send_page_leaves_a_draft_with_files_alone_and_starts_a_new_one(
    client, draft_transfer, uploaded_file
):
    """A draft that already has files (the sender started uploading, then came back to the send
    page) isn't reused -- the page's own file list is only tracked client-side, so silently
    reusing it would attach new uploads to files the page has no record of."""
    client.force_login(draft_transfer.owner)

    client.get(reverse('file_transfer:send'))

    assert (
        Transfer.objects.filter(owner=draft_transfer.owner, status=TransferStatus.DRAFT).count()
        == 2
    )


def test_send_page_resume_param_reuses_a_draft_with_files(client, draft_transfer, uploaded_file):
    """`?resume=<draft id>` (spec section 2: resumable uploads) is how the send page's upload JS
    picks the same draft back up after a reload -- unlike a plain visit, it must find a draft that
    already has files rather than starting a new one (see the "leaves a draft with files alone"
    test above for the plain-visit behavior this deliberately differs from)."""
    client.force_login(draft_transfer.owner)

    response = client.get(reverse('file_transfer:send') + f'?resume={draft_transfer.id}')

    assert response.status_code == 200
    assert (
        Transfer.objects.filter(owner=draft_transfer.owner, status=TransferStatus.DRAFT).count()
        == 1
    )
    assert response.context['draft_id'] == str(draft_transfer.id)
    existing = response.context['existing_files']
    assert len(existing) == 1
    assert existing[0]['id'] == str(uploaded_file.id)
    assert existing[0]['uploaded'] is True


def test_send_page_resume_param_ignores_other_users_draft(client, draft_transfer, uploaded_file):
    from apps.accounts.tests.factories import UserFactory

    other = UserFactory()
    client.force_login(other)

    response = client.get(reverse('file_transfer:send') + f'?resume={draft_transfer.id}')

    assert response.status_code == 200
    # Falls back to `get_or_create_draft` for `other` rather than 404ing or leaking the transfer.
    assert response.context['draft_id'] != str(draft_transfer.id)


def test_send_page_resume_param_ignores_malformed_value(client, funded_user):
    client.force_login(funded_user)

    response = client.get(reverse('file_transfer:send') + '?resume=not-a-uuid')

    assert response.status_code == 200


def test_send_submit_validation_error_returns_422(client, draft_transfer, uploaded_file):
    client.force_login(draft_transfer.owner)
    url = reverse('file_transfer:send-submit', args=[draft_transfer.id])

    response = client.post(
        url,
        data={'recipients': '', 'expiry_choice': 'none'},
        HTTP_HX_REQUEST='true',
    )

    assert response.status_code == 422


def test_send_submit_success_redirects_to_sent_page(client, draft_transfer, uploaded_file):
    client.force_login(draft_transfer.owner)
    url = reverse('file_transfer:send-submit', args=[draft_transfer.id])

    response = client.post(
        url,
        data={'recipients': 'friend@example.com', 'message': 'hi', 'expiry_choice': 'none'},
        HTTP_HX_REQUEST='true',
    )

    assert response.status_code == 200
    assert response['HX-Redirect'] == reverse('file_transfer:sent', args=[draft_transfer.id])
    draft_transfer.refresh_from_db()
    assert draft_transfer.status == TransferStatus.ACTIVE


def test_send_submit_insufficient_credits(client, draft_transfer, uploaded_file):
    spend_credits(draft_transfer.owner, get_balance(draft_transfer.owner), source='test')
    client.force_login(draft_transfer.owner)
    url = reverse('file_transfer:send-submit', args=[draft_transfer.id])

    response = client.post(
        url,
        data={'recipients': 'friend@example.com', 'expiry_choice': 'none'},
        HTTP_HX_REQUEST='true',
    )

    assert response.status_code == 422
    draft_transfer.refresh_from_db()
    assert draft_transfer.status == TransferStatus.DRAFT


def test_send_submit_other_users_draft_is_404(client, draft_transfer, uploaded_file):
    from apps.accounts.tests.factories import UserFactory

    other = UserFactory()
    client.force_login(other)
    url = reverse('file_transfer:send-submit', args=[draft_transfer.id])

    response = client.post(url, data={'recipients': 'a@example.com', 'expiry_choice': 'none'})

    assert response.status_code == 404


def test_sent_page_requires_active_ownership(client, draft_transfer, uploaded_file):
    client.force_login(draft_transfer.owner)
    url = reverse('file_transfer:sent', args=[draft_transfer.id])

    # Still a draft: not sent yet.
    assert client.get(url).status_code == 404

    from ..services.send import SendOptions, finalize_send

    finalize_send(draft_transfer, SendOptions(recipients=['a@example.com']))

    response = client.get(url)
    assert response.status_code == 200
    assert draft_transfer.slug in response.content.decode()
