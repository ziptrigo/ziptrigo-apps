import pytest
from django.urls import reverse
from django.utils import timezone

from apps.accounts.tests.factories import UserFactory
from apps.billing.services import get_balance, spend_credits

from ..models import TransferRecipient, TransferStatus

pytestmark = [pytest.mark.django_db, pytest.mark.unit]

#: Every dashboard action form also has a plain `action`/`method="post"`, so it still works with
#: JS disabled -- htmx just intercepts the same submit and adds this header. Most of these tests
#: exercise the htmx path (matching what the templates' `hx-post` actually sends); a couple of
#: dedicated tests below cover the non-htmx fallback.
HX_HEADERS = {'HTTP_HX_REQUEST': 'true'}


def _active(transfer, **overrides):
    transfer.status = TransferStatus.ACTIVE
    for key, value in overrides.items():
        setattr(transfer, key, value)
    transfer.save()
    return transfer


def test_dashboard_requires_login(client):
    assert client.get(reverse('file_transfer:dashboard')).status_code == 302


def test_dashboard_lists_only_own_transfers(client, draft_transfer):
    _active(draft_transfer)
    other = UserFactory()
    client.force_login(other)

    response = client.get(reverse('file_transfer:dashboard'))

    assert response.status_code == 200
    assert draft_transfer.display_name not in response.content.decode()


def test_transfer_list_partial_filters_active_vs_ended(client, draft_transfer):
    _active(draft_transfer)
    client.force_login(draft_transfer.owner)

    active_response = client.get(reverse('file_transfer:transfer-list'), {'filter': 'active'})
    ended_response = client.get(reverse('file_transfer:transfer-list'), {'filter': 'ended'})

    assert (
        str(draft_transfer.id) in active_response.content.decode()
        or 'transfer-row' in active_response.content.decode()
    )
    assert 'No ended transfers.' in ended_response.content.decode()


def test_disable_and_reenable_round_trip(client, draft_transfer):
    _active(draft_transfer)
    client.force_login(draft_transfer.owner)

    disable_response = client.post(
        reverse('file_transfer:disable', args=[draft_transfer.id]), **HX_HEADERS
    )
    assert disable_response.status_code == 200
    draft_transfer.refresh_from_db()
    assert draft_transfer.status == TransferStatus.DISABLED

    reenable_response = client.post(
        reverse('file_transfer:reenable', args=[draft_transfer.id]), **HX_HEADERS
    )
    assert reenable_response.status_code == 200
    draft_transfer.refresh_from_db()
    assert draft_transfer.status == TransferStatus.ACTIVE


def test_disable_other_users_transfer_is_404(client, draft_transfer):
    _active(draft_transfer)
    other = UserFactory()
    client.force_login(other)

    response = client.post(reverse('file_transfer:disable', args=[draft_transfer.id]), **HX_HEADERS)

    assert response.status_code == 404


def test_reenable_without_credits_returns_422(client, draft_transfer):
    draft_transfer.status = TransferStatus.SUSPENDED
    draft_transfer.suspended_at = timezone.now()
    draft_transfer.save()
    spend_credits(draft_transfer.owner, get_balance(draft_transfer.owner), source='test')
    client.force_login(draft_transfer.owner)

    response = client.post(
        reverse('file_transfer:reenable', args=[draft_transfer.id]), **HX_HEADERS
    )

    assert response.status_code == 422
    draft_transfer.refresh_from_db()
    assert draft_transfer.status == TransferStatus.SUSPENDED


def test_delete_now(client, draft_transfer, uploaded_file, fake_storage):
    _active(draft_transfer)
    client.force_login(draft_transfer.owner)

    response = client.post(reverse('file_transfer:delete', args=[draft_transfer.id]), **HX_HEADERS)

    assert response.status_code == 200
    draft_transfer.refresh_from_db()
    assert draft_transfer.status == TransferStatus.DELETED
    assert uploaded_file.storage_key not in fake_storage.objects


def test_update_settings_success(client, draft_transfer):
    _active(draft_transfer)
    client.force_login(draft_transfer.owner)

    response = client.post(
        reverse('file_transfer:update-settings', args=[draft_transfer.id]),
        data={'expiry_choice': '5', 'max_downloads': '3', 'password': ''},
        **HX_HEADERS,
    )

    assert response.status_code == 200
    draft_transfer.refresh_from_db()
    assert draft_transfer.max_downloads == 3
    assert draft_transfer.expires_at is not None


def test_update_settings_without_password_keeps_it_unchanged(draft_transfer, client):
    """A settings save that only touches expiry/max-downloads must not silently strip an existing
    password -- the combined form always posts a `password` field (blank, since the hash can't be
    pre-filled), so blank has to mean "unchanged" rather than "remove" (see
    `apps.file_transfer.forms.dashboard_actions.PasswordActionForm`)."""
    from ..services.password import hash_password

    _active(draft_transfer, password_hash=hash_password('sekret'))
    client.force_login(draft_transfer.owner)

    response = client.post(
        reverse('file_transfer:update-settings', args=[draft_transfer.id]),
        data={'expiry_choice': '5', 'max_downloads': '3', 'password': ''},
        **HX_HEADERS,
    )

    assert response.status_code == 200
    draft_transfer.refresh_from_db()
    assert draft_transfer.password_hash


def test_update_settings_remove_password_checkbox_clears_it(draft_transfer, client):
    from ..services.password import hash_password

    _active(draft_transfer, password_hash=hash_password('sekret'))
    client.force_login(draft_transfer.owner)

    response = client.post(
        reverse('file_transfer:update-settings', args=[draft_transfer.id]),
        data={
            'expiry_choice': 'none',
            'max_downloads': '',
            'password': '',
            'remove_password': 'true',
        },
        **HX_HEADERS,
    )

    assert response.status_code == 200
    draft_transfer.refresh_from_db()
    assert draft_transfer.password_hash == ''


def test_update_settings_new_password_replaces_old_one(draft_transfer, client):
    from ..services.password import hash_password, verify_password

    _active(draft_transfer, password_hash=hash_password('old-one'))
    client.force_login(draft_transfer.owner)

    response = client.post(
        reverse('file_transfer:update-settings', args=[draft_transfer.id]),
        data={'expiry_choice': 'none', 'max_downloads': '', 'password': 'new-one'},
        **HX_HEADERS,
    )

    assert response.status_code == 200
    draft_transfer.refresh_from_db()
    assert verify_password('new-one', draft_transfer.password_hash)


def test_update_settings_validation_error(client, draft_transfer):
    _active(draft_transfer)
    client.force_login(draft_transfer.owner)

    response = client.post(
        reverse('file_transfer:update-settings', args=[draft_transfer.id]),
        data={'expiry_choice': 'custom', 'expiry_date': '', 'max_downloads': ''},
        **HX_HEADERS,
    )

    assert response.status_code == 422


def test_update_settings_without_htmx_redirects_to_dashboard(client, draft_transfer):
    """The plain-form fallback (`CLAUDE.md`'s HTMX convention: redirect without htmx) for a
    JS-disabled visitor, or a validation error with nothing to swap a partial into."""
    _active(draft_transfer)
    client.force_login(draft_transfer.owner)

    response = client.post(
        reverse('file_transfer:update-settings', args=[draft_transfer.id]),
        data={'expiry_choice': 'custom', 'expiry_date': '', 'max_downloads': ''},
    )

    assert response.status_code == 302
    assert response['Location'] == reverse('file_transfer:dashboard')


def test_add_recipients_and_resend(client, draft_transfer):
    _active(draft_transfer)
    client.force_login(draft_transfer.owner)

    add_response = client.post(
        reverse('file_transfer:add-recipients', args=[draft_transfer.id]),
        data={'recipients': 'friend@example.com'},
        **HX_HEADERS,
    )
    assert add_response.status_code == 200
    recipient = TransferRecipient.objects.get(transfer=draft_transfer, email='friend@example.com')

    resend_response = client.post(
        reverse('file_transfer:resend-recipient', args=[draft_transfer.id, recipient.id]),
        **HX_HEADERS,
    )
    assert resend_response.status_code == 200
    recipient.refresh_from_db()
    assert recipient.last_sent_at is not None


def test_download_log_is_bounded_and_does_not_n_plus_one(
    client, draft_transfer, uploaded_file, django_assert_max_num_queries
):
    """The dashboard list's `download_events` prefetch (`views.dashboard._transfers_for`) selects
    each event's `file` up front and caps the log at `_DOWNLOAD_LOG_LIMIT` -- otherwise the row
    partial's `event.file.name` would fire one query per shown event, and a heavily-downloaded
    transfer would drag its entire history into memory just to render a handful of rows."""
    from ..models import DownloadEvent

    _active(draft_transfer)
    client.force_login(draft_transfer.owner)

    for _ in range(25):
        DownloadEvent.objects.create(transfer=draft_transfer, file=uploaded_file, ip='1.2.3.4')

    with django_assert_max_num_queries(10):
        response = client.get(reverse('file_transfer:dashboard'))

    assert response.status_code == 200
    transfer = response.context['transfers'][0]
    assert len(transfer.recent_download_events) == 20
