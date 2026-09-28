"""Admin tooling for issue #59: the abuse-report list (dismiss / take down) and the bulk
takedown / "block sender" actions on both `TransferAdmin` and `AbuseReportAdmin`, plus basic
permission checks (existing admin conventions: superusers always in, plain staff need the model
permission)."""

import pytest
from django.urls import reverse

from apps.accounts.tests.factories import UserFactory

from ..models import (
    AbuseReportReason,
    AbuseReportStatus,
    BlockedSender,
    BlockedSenderKind,
    TransferStatus,
)

pytestmark = [pytest.mark.django_db, pytest.mark.unit]


def _active(transfer, **overrides):
    transfer.status = TransferStatus.ACTIVE
    for key, value in overrides.items():
        setattr(transfer, key, value)
    transfer.save()
    return transfer


# -- TransferAdmin.take_down_action --


def test_transfer_admin_take_down_action_shows_confirmation(
    client, admin_user, draft_transfer, uploaded_file
):
    _active(draft_transfer)
    client.force_login(admin_user)

    response = client.post(
        reverse('custom_admin:file_transfer_transfer_changelist'),
        data={'action': 'take_down_action', '_selected_action': [str(draft_transfer.id)]},
    )

    assert response.status_code == 200
    assert 'Take down transfer(s)' in response.content.decode()
    draft_transfer.refresh_from_db()
    assert draft_transfer.status == TransferStatus.ACTIVE


def test_transfer_admin_take_down_action_applies(client, admin_user, draft_transfer, uploaded_file):
    _active(draft_transfer)
    client.force_login(admin_user)

    response = client.post(
        reverse('custom_admin:file_transfer_transfer_changelist'),
        data={
            'action': 'take_down_action',
            '_selected_action': [str(draft_transfer.id)],
            'apply': 'yes',
            'reason': 'confirmed abuse',
        },
        follow=True,
    )

    assert response.status_code == 200
    draft_transfer.refresh_from_db()
    assert draft_transfer.status == TransferStatus.TAKEN_DOWN
    assert draft_transfer.takedown_reason == 'confirmed abuse'
    assert draft_transfer.taken_down_by_id == admin_user.id


def test_transfer_admin_take_down_action_requires_reason(
    client, admin_user, draft_transfer, uploaded_file
):
    _active(draft_transfer)
    client.force_login(admin_user)

    response = client.post(
        reverse('custom_admin:file_transfer_transfer_changelist'),
        data={
            'action': 'take_down_action',
            '_selected_action': [str(draft_transfer.id)],
            'apply': 'yes',
            'reason': '',
        },
    )

    assert response.status_code == 200
    draft_transfer.refresh_from_db()
    assert draft_transfer.status == TransferStatus.ACTIVE


# -- TransferAdmin.block_sender_action --


def test_transfer_admin_block_sender_action(client, admin_user, draft_transfer, uploaded_file):
    _active(draft_transfer, sender_ip='203.0.113.9')
    client.force_login(admin_user)

    response = client.post(
        reverse('custom_admin:file_transfer_transfer_changelist'),
        data={
            'action': 'block_sender_action',
            '_selected_action': [str(draft_transfer.id)],
            'apply': 'yes',
            'block_email': 'on',
            'block_ip': 'on',
            'reason': 'repeat abuse',
        },
        follow=True,
    )

    assert response.status_code == 200
    assert BlockedSender.objects.filter(
        kind=BlockedSenderKind.EMAIL, value=draft_transfer.owner.email.lower()
    ).exists()
    assert BlockedSender.objects.filter(kind=BlockedSenderKind.IP, value='203.0.113.9').exists()


# -- AbuseReportAdmin --


def test_abuse_report_admin_dismiss_action(client, admin_user, draft_transfer, uploaded_file):
    from ..services.reports import create_report

    _active(draft_transfer)
    report = create_report(draft_transfer, reason=AbuseReportReason.OTHER)
    client.force_login(admin_user)

    response = client.post(
        reverse('custom_admin:file_transfer_abusereport_changelist'),
        data={'action': 'dismiss_action', '_selected_action': [str(report.id)]},
        follow=True,
    )

    assert response.status_code == 200
    report.refresh_from_db()
    assert report.status == AbuseReportStatus.DISMISSED
    assert report.reviewed_by_id == admin_user.id


def test_abuse_report_admin_take_down_action_applies(
    client, admin_user, draft_transfer, uploaded_file
):
    from ..services.reports import create_report

    _active(draft_transfer)
    report = create_report(draft_transfer, reason=AbuseReportReason.MALWARE)
    client.force_login(admin_user)

    response = client.post(
        reverse('custom_admin:file_transfer_abusereport_changelist'),
        data={
            'action': 'take_down_action',
            '_selected_action': [str(report.id)],
            'apply': 'yes',
            'reason': 'confirmed malware',
        },
        follow=True,
    )

    assert response.status_code == 200
    draft_transfer.refresh_from_db()
    assert draft_transfer.status == TransferStatus.TAKEN_DOWN
    report.refresh_from_db()
    assert report.status == AbuseReportStatus.ACTIONED


def test_abuse_report_admin_take_down_action_dedupes_transfers(
    client, admin_user, draft_transfer, uploaded_file
):
    """Several reports against the same transfer, all selected, only take it down once."""
    from ..services.reports import create_report

    _active(draft_transfer)
    report1 = create_report(
        draft_transfer, reason=AbuseReportReason.OTHER, reporter_ip='203.0.113.1'
    )
    report2 = create_report(
        draft_transfer, reason=AbuseReportReason.OTHER, reporter_ip='203.0.113.2'
    )
    client.force_login(admin_user)

    response = client.post(
        reverse('custom_admin:file_transfer_abusereport_changelist'),
        data={
            'action': 'take_down_action',
            '_selected_action': [str(report1.id), str(report2.id)],
            'apply': 'yes',
            'reason': 'confirmed',
        },
        follow=True,
    )

    assert response.status_code == 200
    draft_transfer.refresh_from_db()
    assert draft_transfer.status == TransferStatus.TAKEN_DOWN


def test_abuse_report_admin_list_filters_by_status(
    client, admin_user, draft_transfer, uploaded_file
):
    from ..services.reports import create_report

    _active(draft_transfer)
    create_report(draft_transfer, reason=AbuseReportReason.OTHER)
    client.force_login(admin_user)

    response = client.get(
        reverse('custom_admin:file_transfer_abusereport_changelist') + '?status__exact=pending'
    )

    assert response.status_code == 200


def test_abuse_report_admin_add_is_disabled(client, admin_user):
    client.force_login(admin_user)
    response = client.get(reverse('custom_admin:file_transfer_abusereport_add'))
    assert response.status_code == 403


# -- BlockedSenderAdmin --


def test_blocked_sender_admin_create_sets_created_by(client, admin_user):
    client.force_login(admin_user)

    response = client.post(
        reverse('custom_admin:file_transfer_blockedsender_add'),
        data={'kind': BlockedSenderKind.EMAIL, 'value': 'spammer@example.com', 'reason': 'spam'},
        follow=True,
    )

    assert response.status_code == 200
    entry = BlockedSender.objects.get(value='spammer@example.com')
    assert entry.created_by_id == admin_user.id


# -- Permission checks (existing admin conventions: superuser always in, staff needs the perm) --


def test_abuse_report_admin_forbidden_for_staff_without_permission(client):
    staff = UserFactory(is_staff=True)
    client.force_login(staff)

    response = client.get(reverse('custom_admin:file_transfer_abusereport_changelist'))

    assert response.status_code in (302, 403)


def test_blocked_sender_admin_forbidden_for_staff_without_permission(client):
    staff = UserFactory(is_staff=True)
    client.force_login(staff)

    response = client.get(reverse('custom_admin:file_transfer_blockedsender_changelist'))

    assert response.status_code in (302, 403)
