"""The anonymous manage link (spec section 6): `/t/<slug>/manage/<token>/`."""

import pytest

from ..models import Transfer, TransferStatus

pytestmark = [pytest.mark.django_db, pytest.mark.unit]


def _transfer(**kwargs) -> Transfer:
    defaults = dict(owner=None, sender_email='sender@example.com', status=TransferStatus.ACTIVE)
    defaults.update(kwargs)
    return Transfer.objects.create(**defaults)


def test_manage_page_shows_status_and_count(client):
    transfer = _transfer()
    response = client.get(f'/t/{transfer.slug}/manage/{transfer.manage_token}/')
    assert response.status_code == 200
    assert b'Active' in response.content


def test_manage_page_404s_for_wrong_token(client):
    transfer = _transfer()
    response = client.get(f'/t/{transfer.slug}/manage/not-the-real-token/')
    assert response.status_code == 404


def test_manage_page_404s_for_unknown_slug(client):
    transfer = _transfer()
    response = client.get(f'/t/does-not-exist/manage/{transfer.manage_token}/')
    assert response.status_code == 404


def test_manage_page_404s_once_transfer_has_ended(client):
    transfer = _transfer(status=TransferStatus.EXPIRED)
    response = client.get(f'/t/{transfer.slug}/manage/{transfer.manage_token}/')
    assert response.status_code == 404


def test_manage_page_404s_for_an_owned_transfer(client, user):
    """The manage link is anonymous-only; an owned transfer has its own dashboard."""
    transfer = _transfer(owner=user, sender_email='')
    response = client.get(f'/t/{transfer.slug}/manage/{transfer.manage_token}/')
    assert response.status_code == 404


def test_manage_disable_stops_the_link_from_working(client):
    transfer = _transfer()
    response = client.post(f'/t/{transfer.slug}/manage/{transfer.manage_token}/disable/')
    # Post/redirect/get: success redirects back to the manage page rather than re-rendering it,
    # so a reload never re-submits the disable.
    assert response.status_code == 302
    assert response['Location'] == f'/t/{transfer.slug}/manage/{transfer.manage_token}/'

    transfer.refresh_from_db()
    assert transfer.status == TransferStatus.DISABLED

    download_response = client.get(f'/t/{transfer.slug}/')
    assert download_response.status_code == 404


def test_manage_disable_is_rejected_for_a_non_active_transfer(client):
    transfer = _transfer(status=TransferStatus.DISABLED)
    response = client.post(
        f'/t/{transfer.slug}/manage/{transfer.manage_token}/disable/', follow=True
    )
    assert response.status_code == 200
    assert b'Only an active transfer can be disabled' in response.content


def test_manage_page_404s_for_a_non_ascii_token(client):
    """`hmac.compare_digest` raises `TypeError` for a non-ASCII `str` argument -- this must not
    500 the page, only fail to match (see `_matching_transfer`'s docstring)."""
    transfer = _transfer()
    response = client.get(f'/t/{transfer.slug}/manage/n%C3%B6t-the-token/')
    assert response.status_code == 404
