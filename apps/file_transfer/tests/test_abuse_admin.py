"""Admin tooling for issue #59: the abuse-report list (dismiss / take down) and the bulk
takedown / "block sender" actions on both `TransferAdmin` and `AbuseReportAdmin`, plus basic
permission checks (existing admin conventions: superusers always in, plain staff need the model
permission)."""

from typing import cast

import pytest
from django.contrib.auth.models import Permission
from django.urls import reverse

from apps.accounts.models import User
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


def _staff_with_perms(*codenames: str) -> User:
    """A staff user (not a superuser) with exactly the given `file_transfer` permission
    codenames -- for the "view-only staff can't run an abuse-tooling action" tests below."""
    staff = cast(User, UserFactory(is_staff=True))
    for codename in codenames:
        # `user_permissions` is a `PermissionsMixin` field `ty` sees only as the declared
        # `ManyToManyField` descriptor, not the manager Django's metaclass actually produces --
        # same category of gap as `.objects`/`.DoesNotExist`, see the class-level comment in
        # `apps/qr_code/models/qrcode.py`.
        staff.user_permissions.add(  # ty: ignore[unresolved-attribute]
            Permission.objects.get(content_type__app_label='file_transfer', codename=codename)
        )
    return staff


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


def test_block_sender_form_defaults_block_ip_off(client, admin_user):
    """Issue #59 code review: a shared/CGNAT or office IP can put other, unrelated senders behind
    the same address -- `block_ip` now defaults off rather than on."""
    from ..admin import BlockSenderForm

    form = BlockSenderForm()
    assert form.fields['block_ip'].initial is False


def test_block_sender_action_also_takes_down_active_transfers_when_opted_in(
    client, admin_user, draft_transfer, uploaded_file
):
    from ..models import Transfer

    _active(draft_transfer)
    other = Transfer.objects.create(owner=draft_transfer.owner, status=TransferStatus.ACTIVE)
    client.force_login(admin_user)

    response = client.post(
        reverse('custom_admin:file_transfer_transfer_changelist'),
        data={
            'action': 'block_sender_action',
            '_selected_action': [str(draft_transfer.id)],
            'apply': 'yes',
            'block_email': 'on',
            'reason': 'spam',
            'take_down_active_transfers': 'on',
        },
        follow=True,
    )

    assert response.status_code == 200
    draft_transfer.refresh_from_db()
    other.refresh_from_db()
    assert draft_transfer.status == TransferStatus.TAKEN_DOWN
    assert other.status == TransferStatus.TAKEN_DOWN


def test_block_sender_action_leaves_active_transfers_alone_by_default(
    client, admin_user, draft_transfer, uploaded_file
):
    _active(draft_transfer)
    client.force_login(admin_user)

    client.post(
        reverse('custom_admin:file_transfer_transfer_changelist'),
        data={
            'action': 'block_sender_action',
            '_selected_action': [str(draft_transfer.id)],
            'apply': 'yes',
            'block_email': 'on',
            'reason': 'spam',
        },
        follow=True,
    )

    draft_transfer.refresh_from_db()
    assert draft_transfer.status == TransferStatus.ACTIVE


def test_block_sender_action_deactivates_account_when_opted_in(
    client, admin_user, draft_transfer, uploaded_file
):
    from apps.accounts.models import User

    _active(draft_transfer)
    client.force_login(admin_user)

    client.post(
        reverse('custom_admin:file_transfer_transfer_changelist'),
        data={
            'action': 'block_sender_action',
            '_selected_action': [str(draft_transfer.id)],
            'apply': 'yes',
            'block_email': 'on',
            'reason': 'spam',
            'deactivate_account': 'on',
        },
        follow=True,
    )

    draft_transfer.owner.refresh_from_db()
    assert draft_transfer.owner.status == User.STATUS_INACTIVE


def test_block_sender_action_does_not_deactivate_account_by_default(
    client, admin_user, draft_transfer, uploaded_file
):
    from apps.accounts.models import User

    _active(draft_transfer)
    client.force_login(admin_user)

    client.post(
        reverse('custom_admin:file_transfer_transfer_changelist'),
        data={
            'action': 'block_sender_action',
            '_selected_action': [str(draft_transfer.id)],
            'apply': 'yes',
            'block_email': 'on',
            'reason': 'spam',
        },
        follow=True,
    )

    draft_transfer.owner.refresh_from_db()
    assert draft_transfer.owner.status == User.STATUS_ACTIVE


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


# -- Admin *action* permissions (issue #59 code review): a plain admin action is independent of
# `has_change_permission`, so a staff user who can merely *view* the transfer/report list must
# not be able to take a transfer down, release a hold, dismiss a report, or block a sender. --


def test_view_only_staff_cannot_take_down_a_transfer(client, draft_transfer, uploaded_file):
    _active(draft_transfer)
    staff = _staff_with_perms('view_transfer')
    client.force_login(staff)

    client.post(
        reverse('custom_admin:file_transfer_transfer_changelist'),
        data={
            'action': 'take_down_action',
            '_selected_action': [str(draft_transfer.id)],
            'apply': 'yes',
            'reason': 'confirmed abuse',
        },
        follow=True,
    )

    draft_transfer.refresh_from_db()
    assert draft_transfer.status == TransferStatus.ACTIVE


def test_view_only_staff_cannot_release_hold(client, draft_transfer, uploaded_file):
    _active(draft_transfer, held_for_review_at=None)
    from django.utils import timezone

    draft_transfer.held_for_review_at = timezone.now()
    draft_transfer.save()
    staff = _staff_with_perms('view_transfer')
    client.force_login(staff)

    client.post(
        reverse('custom_admin:file_transfer_transfer_changelist'),
        data={'action': 'release_hold_action', '_selected_action': [str(draft_transfer.id)]},
        follow=True,
    )

    draft_transfer.refresh_from_db()
    assert draft_transfer.held_for_review_at is not None


def test_view_only_staff_cannot_block_sender(client, draft_transfer, uploaded_file):
    _active(draft_transfer)
    staff = _staff_with_perms('view_transfer')
    client.force_login(staff)

    client.post(
        reverse('custom_admin:file_transfer_transfer_changelist'),
        data={
            'action': 'block_sender_action',
            '_selected_action': [str(draft_transfer.id)],
            'apply': 'yes',
            'block_email': 'on',
            'reason': 'spam',
        },
        follow=True,
    )

    assert not BlockedSender.objects.filter(
        kind=BlockedSenderKind.EMAIL, value=draft_transfer.owner.email.lower()
    ).exists()


def test_view_only_staff_cannot_dismiss_a_report(client, draft_transfer, uploaded_file):
    from ..services.reports import create_report

    _active(draft_transfer)
    report = create_report(draft_transfer, reason=AbuseReportReason.OTHER)
    staff = _staff_with_perms('view_abusereport')
    client.force_login(staff)

    client.post(
        reverse('custom_admin:file_transfer_abusereport_changelist'),
        data={'action': 'dismiss_action', '_selected_action': [str(report.id)]},
        follow=True,
    )

    report.refresh_from_db()
    assert report.status == AbuseReportStatus.PENDING


def test_staff_with_takedown_permission_can_take_down_a_transfer(
    client, draft_transfer, uploaded_file
):
    _active(draft_transfer)
    staff = _staff_with_perms('view_transfer', 'takedown_transfer')
    client.force_login(staff)

    client.post(
        reverse('custom_admin:file_transfer_transfer_changelist'),
        data={
            'action': 'take_down_action',
            '_selected_action': [str(draft_transfer.id)],
            'apply': 'yes',
            'reason': 'confirmed abuse',
        },
        follow=True,
    )

    draft_transfer.refresh_from_db()
    assert draft_transfer.status == TransferStatus.TAKEN_DOWN


def test_staff_with_add_blockedsender_permission_can_block_sender(
    client, draft_transfer, uploaded_file
):
    _active(draft_transfer)
    staff = _staff_with_perms('view_transfer', 'add_blockedsender')
    client.force_login(staff)

    client.post(
        reverse('custom_admin:file_transfer_transfer_changelist'),
        data={
            'action': 'block_sender_action',
            '_selected_action': [str(draft_transfer.id)],
            'apply': 'yes',
            'block_email': 'on',
            'reason': 'spam',
        },
        follow=True,
    )

    assert BlockedSender.objects.filter(
        kind=BlockedSenderKind.EMAIL, value=draft_transfer.owner.email.lower()
    ).exists()


def test_block_sender_take_down_checkbox_requires_takedown_permission(
    client, draft_transfer, uploaded_file
):
    """The "also take down this sender's active transfers" checkbox needs its own permission
    check independent of `add_blockedsender` -- otherwise a staff member who can only manage the
    block list could use it to take transfers down too."""
    _active(draft_transfer)
    staff = _staff_with_perms('view_transfer', 'add_blockedsender')
    client.force_login(staff)

    response = client.post(
        reverse('custom_admin:file_transfer_transfer_changelist'),
        data={
            'action': 'block_sender_action',
            '_selected_action': [str(draft_transfer.id)],
            'apply': 'yes',
            'block_email': 'on',
            'reason': 'spam',
            'take_down_active_transfers': 'on',
        },
    )

    assert response.status_code == 200  # redisplays the form with an error, doesn't apply
    draft_transfer.refresh_from_db()
    assert draft_transfer.status == TransferStatus.ACTIVE
    # The block itself wasn't applied either -- the whole submission is refused together.
    assert not BlockedSender.objects.filter(
        kind=BlockedSenderKind.EMAIL, value=draft_transfer.owner.email.lower()
    ).exists()
